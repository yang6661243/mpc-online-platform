from server.api.services.comparison import (
    StrategyMetrics,
    compare_strategy_metrics,
    compute_actual_strategy_metrics,
    save_strategy_comparison,
)
from server.api.database import create_sqlite_memory_session
from server.api.database.orm import StrategyComparison


def test_compute_actual_strategy_metrics_from_curves():
    metrics = compute_actual_strategy_metrics(
        grid_power_kw=[100.0, 120.0, -5.0, 80.0],
        battery_power_kw=[10.0, -20.0, 0.0, 5.0],
        soc=[0.55, 0.50, 0.52, 0.51],
        buy_price=[1.0, 1.0, 0.5, 0.5],
        sell_price=[0.2, 0.2, 0.2, 0.2],
        c_deg=0.05,
        demand_rate=30.0,
        billing_days=30,
        dt_hours=0.25,
    )

    assert metrics.peak_kw == 120.0
    assert metrics.reverse_flow_count == 1
    assert metrics.soc_min == 0.50
    assert metrics.soc_max == 0.55
    assert metrics.purchase_cost_yuan == 65.0
    assert metrics.export_revenue_yuan == 0.25
    assert metrics.degradation_cost_yuan == 0.4375
    assert metrics.demand_charge_yuan == 5.0
    assert metrics.total_cost_yuan == 70.1875


def test_compare_strategy_metrics_calculates_savings():
    actual = StrategyMetrics(
        peak_kw=500.0,
        purchase_cost_yuan=1000.0,
        export_revenue_yuan=0.0,
        degradation_cost_yuan=10.0,
        demand_charge_yuan=100.0,
        total_cost_yuan=1110.0,
        soc_min=0.2,
        soc_max=0.8,
        reverse_flow_count=0,
    )
    mpc = StrategyMetrics(
        peak_kw=420.0,
        purchase_cost_yuan=950.0,
        export_revenue_yuan=0.0,
        degradation_cost_yuan=15.0,
        demand_charge_yuan=84.0,
        total_cost_yuan=1049.0,
        soc_min=0.1,
        soc_max=0.9,
        reverse_flow_count=0,
    )

    comparison = compare_strategy_metrics(actual, mpc)

    assert comparison.peak_reduction_kw == 80.0
    assert comparison.peak_reduction_pct == 16.0
    assert comparison.cost_saving_yuan == 61.0
    assert round(comparison.cost_saving_pct, 2) == 5.50


def test_save_strategy_comparison_persists_metrics():
    session = create_sqlite_memory_session()
    actual = StrategyMetrics(
        peak_kw=500.0,
        purchase_cost_yuan=1000.0,
        export_revenue_yuan=0.0,
        degradation_cost_yuan=10.0,
        demand_charge_yuan=100.0,
        total_cost_yuan=1110.0,
        soc_min=0.2,
        soc_max=0.8,
        reverse_flow_count=0,
    )
    mpc = StrategyMetrics(
        peak_kw=420.0,
        purchase_cost_yuan=950.0,
        export_revenue_yuan=0.0,
        degradation_cost_yuan=15.0,
        demand_charge_yuan=84.0,
        total_cost_yuan=1049.0,
        soc_min=0.1,
        soc_max=0.9,
        reverse_flow_count=0,
    )

    row = save_strategy_comparison(
        session,
        run_id="mpc_1",
        plant_id="aodelai",
        actual=actual,
        mpc=mpc,
    )

    assert row.peak_reduction_kw == 80.0
    persisted = session.query(StrategyComparison).one()
    assert persisted.run_id == "mpc_1"
    assert persisted.cost_saving_yuan == 61.0
