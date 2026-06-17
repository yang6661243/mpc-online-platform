"""MPC online scheduler: two-layer battery dispatch control.

forecast_mode: 'file' | 'lightgbm' | 'naive'
"""
from __future__ import annotations
import asyncio, csv, os, sys, yaml
import datetime as dt

import numpy as np
import httpx
from openpyxl import load_workbook

_PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

from mpc.solvers.benchmark.solver import BenchmarkConfig, solve_benchmark


def _seasonal_naive_t96(history: np.ndarray, horizon_steps: int) -> list[float]:
    """Predict using yesterday's same time: forecast[t] = history[-96+t]."""
    n = len(history)
    forecast = []
    for i in range(horizon_steps):
        idx = n - 96 + i
        if 0 <= idx < n:
            forecast.append(float(history[idx]))
        elif idx < 0:
            forecast.append(float(history[0]))
        else:
            forecast.append(float(history[-1]))
    return forecast


def _read_xlsx_col(fname: str, keywords: list[str]) -> list[float]:
    """Read first numeric column from xlsx whose header contains any keyword."""
    wb = load_workbook(fname, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    headers = [str(c.value) if c.value else '' for c in next(ws.iter_rows(min_row=1, max_row=1))]
    target_col = None
    for kw in keywords:
        for idx, h in enumerate(headers):
            if kw in h:
                target_col = idx
                break
        if target_col is not None:
            break
    if target_col is None:
        raise ValueError(f"None of {keywords} found in headers: {headers}")
    values = []
    for row in ws.iter_rows(min_row=2, min_col=target_col + 1, max_col=target_col + 1):
        cell = row[0]
        if cell.value is not None:
            try:
                values.append(float(cell.value))
            except (ValueError, TypeError):
                pass
    wb.close()
    return values


def _read_csv_col(fname: str, keywords: list[str]) -> list[float]:
    """Read column from CSV where header contains any keyword."""
    with open(fname, encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        headers = list(reader.fieldnames or [])
        target = None
        for kw in keywords:
            for h in headers:
                if kw in h:
                    target = h
                    break
            if target:
                break
        if target is None:
            raise ValueError(f"None of {keywords} found in headers: {headers}")
        values = []
        for row in reader:
            try:
                values.append(float(row[target]))
            except (ValueError, TypeError):
                pass
    return values


class MpcScheduler:
    """Two-layer MPC scheduler for battery dispatch.

    Energy layer (5 min): solve 48h MILP, post first-step action.
    Power layer (1 sec): compensate real-time deviations, battery-first.

    forecast_mode:
        'file'      — perfect forecast (use actual future data from file)
        'lightgbm'  — LightGBM recursive multi-step forecast
        'naive'     — seasonal naive t-96 (same time yesterday)
    """

    DT_HOURS = 0.25
    HORIZON_HOURS = 48
    HORIZON_STEPS = int(HORIZON_HOURS / DT_HOURS)  # 192
    MIN_HISTORY = 96  # bootstrap with perfect forecast until 1 day of history

    def __init__(
        self,
        api_url: str = "http://localhost:8000",
        instance_id: str = "",
        config_path: str = "mpc/solvers/benchmark/config.yaml",
        forecast_mode: str = "file",
        forecaster = None,
    ):
        self.api_url = api_url.rstrip("/")
        self.instance_id = instance_id
        self.config_path = config_path
        self.forecast_mode = forecast_mode
        self._forecaster = forecaster  # LoadForecaster instance or None

        with open(config_path, 'r', encoding='utf-8') as f:
            yc = yaml.safe_load(f)
        self._yc = yc
        self._bt = yc.get('battery', {})
        self._gd = yc.get('grid', {})
        self._cs = yc.get('cost', {})
        self._dv = yc.get('device', {})
        self._sc = yc.get('scenario', {})
        self._s  = yc.get('solver', {})

        pv_wind_file = os.path.join(_PROJ, self._sc['pv_wind_file'])
        load_file    = os.path.join(_PROJ, self._sc['load_file'])
        price_file   = os.path.join(_PROJ, self._sc['price_file'])

        pv_raw  = _read_xlsx_col(pv_wind_file, ['辐照', 'irradiance', 'ghi', 'solar'])
        wind_raw = _read_xlsx_col(pv_wind_file, ['风速', 'wind'])
        load_raw = _read_xlsx_col(load_file,      ['负荷', 'load', '有功', 'demand'])
        buy_raw  = _read_csv_col(price_file,       ['购电价', 'buy_price'])
        sell_raw = _read_csv_col(price_file,       ['售电价', 'sell_price'])

        T_max = min(len(pv_raw), len(wind_raw), len(load_raw), len(buy_raw), len(sell_raw))

        pv_cap = self._dv.get('pv_capacity_kw', 80.0)
        pv_eff = self._dv.get('pv_efficiency', 0.95)
        self._pv_full = [min(1.0, p / 1000.0) * pv_cap * pv_eff for p in pv_raw[:T_max]]

        wind_cap = self._dv.get('wind_capacity_kw', 50.0)
        wind_eff = self._dv.get('wind_efficiency', 0.92)
        self._wind_full = []
        for w_kmh in wind_raw[:T_max]:
            ws = w_kmh / 3.6
            self._wind_full.append(min(1.0, ws / 12.0) * wind_cap * wind_eff)

        load_base = self._dv.get('load_base_kw', 80.0)
        self._load_base = load_base
        self._load_raw = [max(0.0, min(1.0, l)) for l in load_raw[:T_max]]  # ratio [0,1] for forecaster
        self._load_full = [l * load_base for l in self._load_raw]  # kW

        self._buy_full  = buy_raw[:T_max]
        self._sell_full = sell_raw[:T_max]
        self._T_max = T_max

        self._client: httpx.AsyncClient | None = None
        self._battery_device_id: str | None = None
        self._tick = 0
        self._grid_limit = float('inf')
        self._battery_target = 0.0

        # Peak guard config
        mpc_cfg_path = os.path.join(os.path.dirname(config_path), '..', 'mpc', 'config.yaml')
        if not os.path.exists(mpc_cfg_path):
            mpc_cfg_path = os.path.join(_PROJ, 'mpc', 'solvers', 'mpc', 'config.yaml')
        mpc_yc = {}
        if os.path.exists(mpc_cfg_path):
            with open(mpc_cfg_path, encoding='utf-8') as f:
                mpc_yc = yaml.safe_load(f) or {}
        self._peak_guard = mpc_yc.get('peak_guard', {})
        self._target_peak_kw = float(self._peak_guard.get('target_peak_kw', 0.0))
        self._peak_guard_enabled = bool(self._peak_guard.get('enabled', False))
        self._peak_deadband_kw = float(self._peak_guard.get('deadband_kw', 0.5))

        # ── 动态最优峰值: 扫描一次 ──
        self._optimal_peak = 16.0  # 默认，run()时扫描
        self._demand_modeling = mpc_yc.get('demand_modeling', {})
        self._optimization_billing_days = self._cs.get(
            'optimization_billing_days',
            self._demand_modeling.get('optimization_billing_days', 30)
        )
        self._peak_so_far = 0.0

        # Forecast statistics
        self._fc_stats: list[dict] = []

    async def _get_snapshot(self) -> dict:
        resp = await self._client.get(
            f"{self.api_url}/api/simulation/instances/{self.instance_id}/snapshot"
        )
        resp.raise_for_status()
        return resp.json() or {}

    async def _get_battery_device_id(self) -> str:
        resp = await self._client.get(
            f"{self.api_url}/api/simulation/instances/{self.instance_id}/devices"
        )
        resp.raise_for_status()
        devices = resp.json()
        # Handle both list and dict formats
        items = devices if isinstance(devices, list) else list(devices.values())
        for info in items:
            dt = info.get('device_type', '') if isinstance(info, dict) else ''
            if 'battery' in str(dt).lower():
                return info.get('device_id', '') if isinstance(info, dict) else ''
        raise RuntimeError("No battery device found")

    async def _set_battery_power(self, power_kw: float):
        await self._client.post(
            f"{self.api_url}/api/control/instances/{self.instance_id}/battery/{self._battery_device_id}",
            json={"power_kw": round(power_kw, 2)},
        )

    def _get_current_soc(self, snapshot: dict) -> float:
        # Search devices list for battery SOC
        devices = snapshot.get('devices', [])
        if isinstance(devices, list):
            for d in devices:
                if isinstance(d, dict) and 'battery' in str(d.get('device_type', '')).lower():
                    return float(d.get('soc', self._bt.get('soc_init', 0.5)))
        return self._bt.get('soc_init', 0.5)

    def _get_forecast(self):
        """Get forecast vectors for the next HORIZON_STEPS.

        - 'file': perfect forecast (actual future data)
        - 'lightgbm': LightGBM recursive multi-step (with perfect bootstrap first day)
        - 'naive': same time yesterday (t-96)
        """
        t = self._tick
        T = min(self.HORIZON_STEPS, self._T_max - t)

        if self.forecast_mode == 'file' or t < self.MIN_HISTORY:
            load_fc = self._load_full[t:t+T]
        elif self.forecast_mode == 'lightgbm' and self._forecaster is not None:
            history = np.array(self._load_raw[:t], dtype=np.float64)
            start_time = dt.datetime(2025, 4, 20, 12, 0) + dt.timedelta(minutes=15 * t)
            try:
                fc_ratio = self._forecaster.predict(history, start_time, T)
                load_fc = [max(0.0, r * self._load_base) for r in fc_ratio]
            except Exception:
                load_fc = self._load_full[t:t+T]  # fallback to perfect
        elif self.forecast_mode == 'naive':
            history = np.array(self._load_full[:t], dtype=np.float64) if t > 0 else np.zeros(96)
            load_fc = _seasonal_naive_t96(history, T)
        else:
            load_fc = self._load_full[t:t+T]

        return (
            self._pv_full[t:t+T], self._wind_full[t:t+T], load_fc,
            self._buy_full[t:t+T], self._sell_full[t:t+T],
        )

    def _build_config(self, soc_init: float, peak_so_far: float = 0.0) -> BenchmarkConfig:
        return BenchmarkConfig(
            battery_capacity_kwh=self._bt.get('capacity_kwh', 200.0),
            battery_charge_max_kw=self._bt.get('charge_max_kw', 100.0),
            battery_discharge_max_kw=self._bt.get('discharge_max_kw', 100.0),
            battery_soc_init=soc_init,
            battery_soc_min=self._bt.get('soc_min', 0.1),
            battery_soc_max=self._bt.get('soc_max', 0.95),
            battery_charge_eff=self._bt.get('charge_eff', 0.95),
            battery_discharge_eff=self._bt.get('discharge_eff', 0.95),
            grid_import_max_kw=self._gd.get('import_max_kw', 100.0),
            grid_export_max_kw=self._gd.get('export_max_kw', 100.0),
            transformer_capacity_kw=self._gd.get('transformer_capacity_kw', 200.0),
            anti_backflow=self._gd.get('anti_backflow', False),
            c_deg=self._cs.get('c_deg', 0.05),
            c_pv=self._cs.get('c_pv', 0.0),
            c_wind=self._cs.get('c_wind', 0.0),
            battery_unit_cost=self._cs.get('battery_unit_cost', 0.0),
            battery_annual_decay_rate=self._cs.get('battery_annual_decay_rate', 0.0),
            r_demand=self._cs.get('demand_rate', 0.0),
            r_capacity=self._cs.get('capacity_rate', 0.0),
            use_milp=self._s.get('use_milp', True),
            peak_so_far_kw=peak_so_far,
            optimal_peak_kw=self._optimal_peak,
            peak_slack_penalty=5000.0 if self._peak_guard_enabled or self._optimal_peak > 0 else 0.0,
            optimization_billing_days=self._optimization_billing_days,
        )

    async def _energy_step(self):
        snapshot = await self._get_snapshot()
        soc = self._get_current_soc(snapshot)
        pv, wind, load, buy, sell = self._get_forecast()

        # Track actual peak_so_far from the simulation
        actual_grid_in = max(0.0, float(snapshot.get('total_grid_in_kw',
                              float(snapshot.get('total_load_kw', 0))
                              - float(snapshot.get('total_pv_kw', 0))
                              - float(snapshot.get('total_wind_kw', 0))
                              - float(snapshot.get('total_battery_kw', 0)))))
        if not hasattr(self, '_peak_so_far'):
            self._peak_so_far = 0.0
        self._peak_so_far = max(self._peak_so_far, actual_grid_in)

        cfg = self._build_config(soc, self._peak_so_far)
        result = solve_benchmark(pv, wind, load, buy, sell, config=cfg, verbose=False)

        self._battery_target = result.battery_power[0] if result.battery_power else 0.0
        self._grid_limit = max(0.0, result.peak_demand_kw)
        await self._set_battery_power(self._battery_target)

        print(f"[MPC t={self._tick}] soc={soc:.3f} bat={self._battery_target:.1f}kW "
              f"grid_limit={self._grid_limit:.1f}kW peak_sofar={self._peak_so_far:.1f}kW solve={result.solve_time_s:.1f}s")
        self._tick += 1

    async def _power_loop(self):
        for _ in range(300):
            try:
                snapshot = await self._get_snapshot()
            except Exception:
                await asyncio.sleep(1)
                continue

            actual_load   = float(snapshot.get('total_load_kw', 0))
            actual_pv     = float(snapshot.get('total_pv_kw', 0))
            actual_wind   = float(snapshot.get('total_wind_kw', 0))
            actual_battery = float(snapshot.get('total_battery_kw', 0))

            t = self._tick
            if t < self._T_max:
                fc_load = self._load_full[t]
                fc_pv   = self._pv_full[t]
                fc_wind = self._wind_full[t]
            else:
                fc_load = actual_load
                fc_pv   = actual_pv
                fc_wind = actual_wind

            deviation = (actual_load - fc_load) - (actual_pv - fc_pv) - (actual_wind - fc_wind)

            if abs(deviation) < 0.5:
                await asyncio.sleep(1)
                continue

            chg_max = self._bt.get('charge_max_kw', 100.0)
            dis_max = self._bt.get('discharge_max_kw', 100.0)

            if deviation > 0:
                bat_extra = min(deviation, dis_max - max(0, self._battery_target))
                new_battery = self._battery_target + bat_extra
            else:
                bat_extra = max(deviation, -(chg_max - max(0, -self._battery_target)))
                new_battery = self._battery_target + bat_extra

            # ── 第二层: 峰值守门 ──
            effective_guard = max(self._peak_so_far, self._optimal_peak)
            if self._peak_guard_enabled and effective_guard > 0:
                projected_grid = max(0.0, actual_load - actual_pv - actual_wind - new_battery)
                if projected_grid > effective_guard + self._peak_deadband_kw:
                    need = projected_grid - effective_guard
                    # SOC 保护: 不能为守峰把电池打穿
                    soc = self._get_current_soc(snapshot)
                    hard_min = self._bt.get('soc_min', 0.1)
                    capacity = self._bt.get('capacity_kwh', 200)
                    eff = self._bt.get('discharge_eff', 0.95)
                    soc_limited = max(0.0, (soc - hard_min) * capacity * eff / (1.0 / 3600.0))
                    extra = min(need, dis_max - max(0.0, new_battery), soc_limited)
                    new_battery += extra

            if abs(new_battery - actual_battery) > 0.5:
                try:
                    await self._set_battery_power(new_battery)
                except Exception:
                    pass

            await asyncio.sleep(1)

    async def run(self):
        self._client = httpx.AsyncClient(timeout=10.0)
        try:
            self._battery_device_id = await self._get_battery_device_id()
            print(f"MPC: battery device = {self._battery_device_id}")
            print(f"MPC: forecast_mode = {self.forecast_mode}")

            # 月初扫描最优峰值
            try:
                from .peak_optimizer import find_optimal_peak
                remaining_days = max(7, int((self._T_max - self._tick) / 96))
                base_cfg = self._build_config(self._bt.get('soc_init', 0.5))
                self._optimal_peak = find_optimal_peak(
                    self._pv_full, self._wind_full, self._load_full,
                    self._buy_full, self._sell_full, base_cfg,
                    remaining_days)
                print(f"MPC: optimal_peak = {self._optimal_peak:.0f} kW (scan over {remaining_days} days)")
            except Exception as e:
                print(f"MPC: peak scan failed ({e}), using default 16kW")

            while self._tick < self._T_max - self.HORIZON_STEPS:
                await self._energy_step()
                await self._power_loop()

            print("MPC: scenario complete.")
        finally:
            await self._client.aclose()


def create_scheduler(api_url: str = "http://localhost:8000",
                     instance_id: str = "",
                     config_path: str = "mpc/solvers/benchmark/config.yaml",
                     mpc_config_path: str = "mpc/solvers/mpc/config.yaml",
                     forecast_mode: str | None = None,
                     forecaster = None) -> MpcScheduler:
    """Factory: create MpcScheduler with forecaster auto-loaded from config.

    If forecast_mode is not specified, reads from mpc/config.yaml.
    If mode is 'lightgbm' and no forecaster given, auto-loads from the path
    specified in mpc/config.yaml (forecast.model_path).
    """
    if forecast_mode is None:
        with open(os.path.join(_PROJ, mpc_config_path), encoding='utf-8') as f:
            mpc_cfg = yaml.safe_load(f)
        forecast_mode = mpc_cfg.get('forecast', {}).get('mode', 'file')

    if forecast_mode == 'lightgbm' and forecaster is None:
        with open(os.path.join(_PROJ, mpc_config_path), encoding='utf-8') as f:
            mpc_cfg = yaml.safe_load(f)
        model_path = mpc_cfg.get('forecast', {}).get('model_path', 'outputs/models/load_forecaster.joblib')
        from mpc.solvers.forecasting import LoadForecaster
        forecaster = LoadForecaster.load(os.path.join(_PROJ, model_path))
        print(f"create_scheduler: loaded forecaster from {model_path}")

    return MpcScheduler(
        api_url=api_url,
        instance_id=instance_id,
        config_path=config_path,
        forecast_mode=forecast_mode,
        forecaster=forecaster,
    )
