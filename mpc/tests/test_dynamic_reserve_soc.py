from mpc.microgrid.battery_ops import (
    apply_realtime_load_compensation,
    build_dynamic_reserve_soc,
    build_reserve_energy_request,
    estimate_under_forecast_margin,
)
from mpc.solvers.benchmark.solver import BenchmarkConfig, solve_benchmark


def test_solver_honors_time_varying_soc_min_profile():
    cfg = BenchmarkConfig(
        dt_hours=0.25,
        battery_capacity_kwh=100.0,
        battery_soc_init=0.7,
        battery_soc_min=0.1,
        battery_soc_max=0.9,
        battery_soc_min_profile=[0.6],
        battery_charge_eff=1.0,
        battery_discharge_eff=1.0,
        battery_charge_max_kw=0.0,
        battery_discharge_max_kw=100.0,
        grid_import_max_kw=500.0,
        transformer_capacity_kw=500.0,
        grid_export_max_kw=0.0,
        anti_backflow=True,
        c_deg=0.0,
        use_milp=True,
        terminal_constraint=False,
    )

    result = solve_benchmark(
        pv_kw=[0.0],
        wind_kw=[0.0],
        load_kw=[100.0],
        buy_price=[1.0],
        sell_price=[0.0],
        config=cfg,
    )

    assert result.soc_trajectory[0] >= 0.6
    assert result.battery_power[0] <= 40.0


def test_dynamic_reserve_soc_uses_three_risk_levels():
    profile = build_dynamic_reserve_soc(
        load_fc=[300.0, 450.0, 500.0, 560.0],
        pv_kw=[0.0, 0.0, 0.0, 0.0],
        wind_kw=[0.0, 0.0, 0.0, 0.0],
        effective_target_kw=520.0,
        base_soc=0.10,
        risk_soc=0.25,
        high_soc=0.35,
        risk_margin_kw=120.0,
        high_margin_kw=60.0,
        enabled=True,
    )

    assert profile == [0.10, 0.25, 0.35, 0.35]


def test_dynamic_reserve_soc_does_not_force_charging_above_peak_target():
    profile = build_dynamic_reserve_soc(
        load_fc=[520.0, 530.0, 540.0],
        pv_kw=[0.0, 0.0, 0.0],
        wind_kw=[0.0, 0.0, 0.0],
        effective_target_kw=520.0,
        base_soc=0.10,
        risk_soc=0.25,
        high_soc=0.35,
        risk_margin_kw=120.0,
        high_margin_kw=60.0,
        enabled=True,
        current_soc=0.10,
        charge_max_kw=375.0,
        capacity_kwh=783.0,
        charge_eff=0.95,
        dt_hours=0.25,
        soc_max=0.9,
    )

    assert profile == [0.10, 0.10, 0.10]


def test_dynamic_reserve_soc_stays_at_current_reachable_level_when_peak_headroom_is_absent():
    profile = build_dynamic_reserve_soc(
        load_fc=[600.0, 620.0, 640.0],
        pv_kw=[0.0, 0.0, 0.0],
        wind_kw=[0.0, 0.0, 0.0],
        effective_target_kw=449.0,
        base_soc=0.10,
        risk_soc=0.25,
        high_soc=0.35,
        risk_margin_kw=120.0,
        high_margin_kw=60.0,
        enabled=True,
        current_soc=0.152,
        charge_max_kw=375.0,
        capacity_kwh=783.0,
        charge_eff=0.95,
        dt_hours=0.25,
        soc_max=0.9,
    )

    assert profile == [0.152, 0.152, 0.152]


def test_vpp_window_allows_realtime_discharge_compensation_for_under_forecast():
    bp = apply_realtime_load_compensation(
        battery_power_kw=50.0,
        load_error_kw=80.0,
        discharge_max_kw=100.0,
        charge_max_kw=100.0,
        vpp_window_now=True,
    )

    assert bp == 100.0


def test_realtime_compensation_cannot_discharge_at_soc_floor():
    bp = apply_realtime_load_compensation(
        battery_power_kw=0.0,
        load_error_kw=80.0,
        discharge_max_kw=100.0,
        charge_max_kw=100.0,
        current_soc=0.10,
        soc_min=0.10,
        soc_max=0.90,
        capacity_kwh=100.0,
        discharge_eff=1.0,
        dt_hours=0.25,
    )

    assert bp == 0.0


def test_realtime_compensation_discharge_is_limited_by_available_soc():
    bp = apply_realtime_load_compensation(
        battery_power_kw=0.0,
        load_error_kw=100.0,
        discharge_max_kw=100.0,
        charge_max_kw=100.0,
        current_soc=0.15,
        soc_min=0.10,
        soc_max=0.90,
        capacity_kwh=100.0,
        discharge_eff=1.0,
        dt_hours=0.25,
    )

    assert round(bp, 6) == 20.0


def test_vpp_window_does_not_turn_realtime_compensation_into_charging():
    bp = apply_realtime_load_compensation(
        battery_power_kw=20.0,
        load_error_kw=-80.0,
        discharge_max_kw=100.0,
        charge_max_kw=100.0,
        vpp_window_now=True,
    )

    assert bp == 0.0


def test_under_forecast_margin_uses_warmup_until_enough_history():
    margin = estimate_under_forecast_margin(
        under_error_history_kw=[10.0, 30.0, 120.0],
        quantile=0.9,
        warmup_kw=100.0,
        min_kw=30.0,
        max_kw=150.0,
        min_samples=96,
    )

    assert margin == 102.0


def test_under_forecast_margin_uses_recent_quantile_after_warmup():
    history = [0.0] * 90 + [20.0, 40.0, 60.0, 80.0, 100.0, 120.0]

    margin = estimate_under_forecast_margin(
        under_error_history_kw=history,
        quantile=0.9,
        warmup_kw=100.0,
        min_kw=30.0,
        max_kw=150.0,
        min_samples=96,
    )

    assert round(margin, 1) == 110.0


def test_reserve_energy_request_converts_risk_peak_area_to_checkpoint_soc():
    request = build_reserve_energy_request(
        load_fc=[450.0, 500.0, 540.0, 500.0],
        pv_kw=[0.0, 0.0, 0.0, 0.0],
        wind_kw=[0.0, 0.0, 0.0, 0.0],
        effective_target_kw=520.0,
        risk_margin_kw=50.0,
        soc_min=0.10,
        soc_max=0.90,
        capacity_kwh=100.0,
        discharge_eff=1.0,
        dt_hours=0.25,
        reserve_lead_hours=0.25,
        enabled=True,
    )

    assert request["check_step"] == 1
    assert request["reserve_energy_kwh"] == 32.5
    assert round(request["target_soc"], 3) == 0.425


def test_solver_soft_reserve_checkpoint_can_create_planned_energy_buffer():
    cfg = BenchmarkConfig(
        dt_hours=0.25,
        battery_capacity_kwh=100.0,
        battery_soc_init=0.1,
        battery_soc_min=0.1,
        battery_soc_max=0.9,
        battery_charge_eff=1.0,
        battery_discharge_eff=1.0,
        battery_charge_max_kw=100.0,
        battery_discharge_max_kw=100.0,
        grid_import_max_kw=500.0,
        transformer_capacity_kw=500.0,
        grid_export_max_kw=0.0,
        anti_backflow=True,
        c_deg=0.0,
        use_milp=True,
        terminal_constraint=False,
        reserve_check_step=0,
        reserve_target_soc=0.3,
        reserve_slack_penalty=20.0,
    )

    result = solve_benchmark(
        pv_kw=[0.0],
        wind_kw=[0.0],
        load_kw=[0.0],
        buy_price=[1.0],
        sell_price=[0.0],
        config=cfg,
    )

    assert result.soc_trajectory[0] >= 0.3
