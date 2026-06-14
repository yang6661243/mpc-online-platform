from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys

import pandas as pd
import yaml

from microgrid_online.comparison import StrategyMetrics
from microgrid_online.mpc_run import MpcRunnerResult, OnlineMpcRunInput


CommandRunner = Callable[[Sequence[str], Path], None]


@dataclass(frozen=True)
class MicrogridMpcCliRunnerConfig:
    project_root: str | Path
    battery_capacity_kwh: float = 783.0
    battery_charge_max_kw: float = 375.0
    battery_discharge_max_kw: float = 375.0
    battery_soc_min: float = 0.10
    battery_soc_max: float = 0.90
    battery_charge_eff: float = 0.95
    battery_discharge_eff: float = 0.95
    grid_import_max_kw: float = 5000.0
    grid_export_max_kw: float = 0.0
    transformer_capacity_kw: float = 5000.0
    anti_backflow: bool = True
    capacity_rate: float = 0.0
    horizon_steps: int = 96
    target_peak_ratio: float = 0.90
    target_peak_kw: float | None = None
    peak_slack_penalty: float = 5000.0
    enable_battery_power_smoothing: bool = True
    battery_ramp_limit_kw: float = 150.0
    battery_smooth_penalty: float = 0.01


def _default_command_runner(command: Sequence[str], cwd: Path) -> None:
    subprocess.run(list(command), cwd=cwd, check=True)


def _metric_value(cost_summary: pd.DataFrame, name: str) -> float:
    row = cost_summary.loc[cost_summary["指标"] == name]
    if row.empty:
        raise ValueError(f"missing metric in cost_summary: {name}")
    return float(row.iloc[0]["MPC 数值"])


def parse_microgrid_mpc_output(output_path: str | Path) -> StrategyMetrics:
    path = Path(output_path)
    if not path.exists():
        raise FileNotFoundError(path)

    cost_summary = pd.read_excel(path, sheet_name="cost_summary")
    trajectory = pd.read_excel(path, sheet_name="15min_trajectory")
    grid = trajectory["电网功率(kW)"].astype(float).tolist()
    soc = trajectory["SOC"].astype(float).tolist()

    return StrategyMetrics(
        peak_kw=_metric_value(cost_summary, "峰值需量(kW)"),
        purchase_cost_yuan=_metric_value(cost_summary, "购电费(元)"),
        export_revenue_yuan=_metric_value(cost_summary, "售电收益(元)"),
        degradation_cost_yuan=_metric_value(cost_summary, "储能衰减(元)"),
        demand_charge_yuan=_metric_value(cost_summary, "需量费(元)"),
        total_cost_yuan=_metric_value(cost_summary, "可控成本(元)"),
        soc_min=min(soc) if soc else None,
        soc_max=max(soc) if soc else None,
        reverse_flow_count=sum(1 for value in grid if value < 0),
    )


def parse_microgrid_mpc_curve(output_path: str | Path) -> list[dict]:
    path = Path(output_path)
    if not path.exists():
        raise FileNotFoundError(path)

    trajectory = pd.read_excel(path, sheet_name="15min_trajectory")
    points = []
    for _, row in trajectory.iterrows():
        points.append(
            {
                "grid_power_kw": float(row["电网功率(kW)"]),
                "battery_power_kw": float(row["电池功率(kW)"]),
                "soc": float(row["SOC"]),
                "buy_price": float(row["购电价(元/kWh)"]) if "购电价(元/kWh)" in trajectory.columns else None,
                "sell_price": float(row["售电价(元/kWh)"]) if "售电价(元/kWh)" in trajectory.columns else None,
            }
        )
    return points


class MicrogridMpcCliRunner:
    def __init__(
        self,
        config: MicrogridMpcCliRunnerConfig,
        *,
        command_runner: CommandRunner | None = None,
        python_executable: str | None = None,
    ) -> None:
        self.config = config
        self.command_runner = command_runner or _default_command_runner
        self.python_executable = python_executable or sys.executable

    def __call__(self, run_input: OnlineMpcRunInput) -> MpcRunnerResult:
        root = Path(self.config.project_root)
        run_dir = Path(run_input.scenario.output_path).parent
        output_path = run_dir / "microgrid_mpc_result.xlsx"
        config_path = run_dir / "microgrid_mpc_config.yaml"
        config_path.write_text(
            yaml.safe_dump(
                self._build_config(run_input, output_path),
                sort_keys=False,
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        command = [
            self.python_executable,
            "-m",
            "microgrid.mpc",
            "--config",
            str(config_path),
        ]
        self.command_runner(command, root)
        return MpcRunnerResult(
            metrics=parse_microgrid_mpc_output(output_path),
            curve=parse_microgrid_mpc_curve(output_path),
        )

    def _build_config(self, run_input: OnlineMpcRunInput, output_path: Path) -> dict:
        soc_init = self._initial_soc(run_input)
        soc_min, soc_max = self._effective_soc_bounds(soc_init)
        target_peak_kw = run_input.target_peak_kw or self.config.target_peak_kw
        if target_peak_kw is None:
            target_peak_kw = run_input.actual_metrics.peak_kw * self.config.target_peak_ratio
        if target_peak_kw <= 0:
            raise ValueError("target_peak_kw must be positive")

        return {
            "scenario": {
                "data_file": str(run_input.scenario.output_path),
                "sheets": {
                    "load": "load",
                    "pv_wind": "pv",
                    "price": "price",
                },
            },
            "battery": {
                "capacity_kwh": self.config.battery_capacity_kwh,
                "charge_max_kw": self.config.battery_charge_max_kw,
                "discharge_max_kw": self.config.battery_discharge_max_kw,
                "soc_init": soc_init,
                "soc_min": soc_min,
                "soc_max": soc_max,
                "charge_eff": self.config.battery_charge_eff,
                "discharge_eff": self.config.battery_discharge_eff,
            },
            "device": {
                "load_base_kw": run_input.scenario.load_base_kw,
                "pv_capacity_kw": 0,
                "pv_efficiency": 0,
                "wind_capacity_kw": 0,
                "wind_efficiency": 0,
            },
            "grid": {
                "anti_backflow": self.config.anti_backflow,
                "import_max_kw": self.config.grid_import_max_kw,
                "export_max_kw": self.config.grid_export_max_kw,
                "transformer_capacity_kw": self.config.transformer_capacity_kw,
            },
            "cost": {
                "c_deg": run_input.c_deg,
                "demand_rate": run_input.demand_rate,
                "capacity_rate": self.config.capacity_rate,
                "billing_days": run_input.billing_days,
            },
            "mpc": {
                "days": max(1, int((run_input.scenario.steps + 95) // 96)),
                "horizon_steps": min(self.config.horizon_steps, max(1, run_input.scenario.steps)),
                "forecast_mode": "file",
                "start_step": 0,
                "target_peak_mode": "manual",
                "target_peak_kw": float(target_peak_kw),
                "peak_slack_penalty": self.config.peak_slack_penalty,
                "enable_battery_power_smoothing": self.config.enable_battery_power_smoothing,
                "battery_ramp_limit_kw": self.config.battery_ramp_limit_kw,
                "battery_smooth_penalty": self.config.battery_smooth_penalty,
                "progress_interval_steps": max(1, run_input.scenario.steps),
            },
            "vpp": {
                "enabled": False,
            },
            "output": str(output_path),
        }

    @staticmethod
    def _initial_soc(run_input: OnlineMpcRunInput) -> float:
        if run_input.telemetry_rows:
            first = run_input.telemetry_rows[0]
            if first.soc_start is not None:
                return float(first.soc_start)
            if first.soc_end is not None:
                return float(first.soc_end)
        if run_input.actual_metrics.soc_max is not None:
            return float(run_input.actual_metrics.soc_max)
        return 0.5

    def _effective_soc_bounds(self, soc_init: float) -> tuple[float, float]:
        soc_min = min(float(self.config.battery_soc_min), float(soc_init))
        soc_max = max(float(self.config.battery_soc_max), float(soc_init))
        return max(0.0, soc_min), min(1.0, soc_max)
