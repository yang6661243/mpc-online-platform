"""月初扫描经济最优峰值。"""
from __future__ import annotations
import numpy as np
from models.benchmark.solver import BenchmarkConfig, solve_benchmark


def find_optimal_peak(
    pv_kw: list[float],
    wind_kw: list[float],
    load_kw: list[float],
    buy_price: list[float],
    sell_price: list[float],
    base_config: BenchmarkConfig,
    remaining_days: int,
    candidates: list[int] | None = None,
) -> float:
    """扫描候选 target_peak 值，返回可控成本最低的峰值 (kW).

    Args:
        remaining_days: 本月剩余天数，自动裁剪 horizon (min 7 days)
        candidates: 候选 target 列表，默认 [4,6,8,10,12,14,16,18,20,24,28]
        base_config: 基础配置（电池/电网/成本参数）

    Returns:
        最优峰值 (kW)，失败时返回 16
    """
    if candidates is None:
        candidates = [4, 6, 8, 10, 12, 14, 16, 18, 20, 24, 28]

    horizon_days = min(30, max(7, remaining_days))
    T = horizon_days * 96
    max_T = min(len(pv_kw), len(wind_kw), len(load_kw), len(buy_price), len(sell_price))
    T = min(T, max_T)

    pv = pv_kw[:T]
    wind = wind_kw[:T]
    load = load_kw[:T]
    buy = buy_price[:T]
    sell = sell_price[:T]

    best_target = 16.0
    best_cost = float('inf')

    # 跑一次不带任何护栏的 MILP，取经济最优峰值
    cfg = BenchmarkConfig(
        battery_capacity_kwh=base_config.battery_capacity_kwh,
        battery_charge_max_kw=base_config.battery_charge_max_kw,
        battery_discharge_max_kw=base_config.battery_discharge_max_kw,
        battery_soc_init=base_config.battery_soc_init,
        battery_soc_min=base_config.battery_soc_min,
        battery_soc_max=base_config.battery_soc_max,
        battery_charge_eff=base_config.battery_charge_eff,
        battery_discharge_eff=base_config.battery_discharge_eff,
        grid_import_max_kw=base_config.grid_import_max_kw,
        grid_export_max_kw=base_config.grid_export_max_kw,
        transformer_capacity_kw=base_config.transformer_capacity_kw,
        anti_backflow=base_config.anti_backflow,
        c_deg=base_config.c_deg,
        c_pv=base_config.c_pv,
        c_wind=base_config.c_wind,
        battery_unit_cost=base_config.battery_unit_cost,
        battery_annual_decay_rate=base_config.battery_annual_decay_rate,
        r_demand=base_config.r_demand,
        r_capacity=base_config.r_capacity,
        use_milp=True,
        terminal_constraint=False,
        target_peak_kw=0,
        peak_slack_penalty=0.0,
        optimization_billing_days=horizon_days,
    )

    try:
        r = solve_benchmark(pv, wind, load, buy, sell, config=cfg, verbose=False)
    except Exception:
        return 16.0

    if r.solver_status not in ('ok', 'warning'):
        return 16.0

    peak = max(r.grid_import) if r.grid_import else 0
    return float(peak)
