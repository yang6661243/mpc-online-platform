from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Sequence

from sqlalchemy.orm import Session

from microgrid_online.models import StrategyComparison


@dataclass(frozen=True)
class StrategyMetrics:
    peak_kw: float
    purchase_cost_yuan: float
    export_revenue_yuan: float
    degradation_cost_yuan: float
    demand_charge_yuan: float
    total_cost_yuan: float
    soc_min: float | None
    soc_max: float | None
    reverse_flow_count: int


@dataclass(frozen=True)
class StrategyComparisonResult:
    peak_reduction_kw: float
    peak_reduction_pct: float
    cost_saving_yuan: float
    cost_saving_pct: float


def _require_same_length(**series: Sequence[float]) -> int:
    lengths = {name: len(values) for name, values in series.items()}
    unique = set(lengths.values())
    if len(unique) != 1:
        raise ValueError(f"series lengths must match: {lengths}")
    return unique.pop()


def compute_actual_strategy_metrics(
    *,
    grid_power_kw: Sequence[float],
    battery_power_kw: Sequence[float],
    soc: Sequence[float],
    buy_price: Sequence[float],
    sell_price: Sequence[float],
    c_deg: float,
    demand_rate: float,
    billing_days: float,
    dt_hours: float = 0.25,
) -> StrategyMetrics:
    _require_same_length(
        grid_power_kw=grid_power_kw,
        battery_power_kw=battery_power_kw,
        buy_price=buy_price,
        sell_price=sell_price,
    )
    if not grid_power_kw:
        raise ValueError("at least one grid_power_kw value is required")
    if dt_hours <= 0:
        raise ValueError("dt_hours must be positive")
    if billing_days <= 0:
        raise ValueError("billing_days must be positive")

    import_power = [max(0.0, float(v)) for v in grid_power_kw]
    export_power = [max(0.0, -float(v)) for v in grid_power_kw]
    purchase_cost = sum(float(buy_price[i]) * import_power[i] * dt_hours for i in range(len(import_power)))
    export_revenue = sum(float(sell_price[i]) * export_power[i] * dt_hours for i in range(len(export_power)))
    degradation_cost = sum(c_deg * abs(float(v)) * dt_hours for v in battery_power_kw)
    peak_kw = max(import_power)
    demand_charge = demand_rate * peak_kw * len(grid_power_kw) * dt_hours / 24.0 / billing_days
    total_cost = purchase_cost - export_revenue + degradation_cost + demand_charge

    return StrategyMetrics(
        peak_kw=peak_kw,
        purchase_cost_yuan=purchase_cost,
        export_revenue_yuan=export_revenue,
        degradation_cost_yuan=degradation_cost,
        demand_charge_yuan=demand_charge,
        total_cost_yuan=total_cost,
        soc_min=min(soc) if soc else None,
        soc_max=max(soc) if soc else None,
        reverse_flow_count=sum(1 for v in grid_power_kw if float(v) < 0),
    )


def compare_strategy_metrics(
    actual: StrategyMetrics,
    mpc: StrategyMetrics,
) -> StrategyComparisonResult:
    peak_reduction = actual.peak_kw - mpc.peak_kw
    peak_reduction_pct = peak_reduction / actual.peak_kw * 100.0 if actual.peak_kw > 0 else 0.0
    cost_saving = actual.total_cost_yuan - mpc.total_cost_yuan
    cost_saving_pct = cost_saving / actual.total_cost_yuan * 100.0 if actual.total_cost_yuan > 0 else 0.0
    return StrategyComparisonResult(
        peak_reduction_kw=peak_reduction,
        peak_reduction_pct=peak_reduction_pct,
        cost_saving_yuan=cost_saving,
        cost_saving_pct=cost_saving_pct,
    )


def save_strategy_comparison(
    session: Session,
    *,
    run_id: str,
    plant_id: str,
    actual: StrategyMetrics,
    mpc: StrategyMetrics,
) -> StrategyComparison:
    comparison = compare_strategy_metrics(actual, mpc)
    row = StrategyComparison(
        run_id=run_id,
        plant_id=plant_id,
        actual_peak_kw=actual.peak_kw,
        mpc_peak_kw=mpc.peak_kw,
        peak_reduction_kw=comparison.peak_reduction_kw,
        peak_reduction_pct=comparison.peak_reduction_pct,
        actual_cost_yuan=actual.total_cost_yuan,
        mpc_cost_yuan=mpc.total_cost_yuan,
        cost_saving_yuan=comparison.cost_saving_yuan,
        cost_saving_pct=comparison.cost_saving_pct,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row
