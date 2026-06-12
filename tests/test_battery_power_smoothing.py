from models.benchmark.solver import BenchmarkConfig, solve_benchmark


def test_battery_ramp_limit_caps_first_step_and_adjacent_changes():
    cfg = BenchmarkConfig(
        dt_hours=0.25,
        battery_capacity_kwh=100.0,
        battery_soc_init=0.9,
        battery_soc_min=0.1,
        battery_soc_max=0.9,
        battery_charge_eff=1.0,
        battery_discharge_eff=1.0,
        battery_charge_max_kw=0.0,
        battery_discharge_max_kw=100.0,
        battery_ramp_limit_kw=25.0,
        battery_initial_power_kw=0.0,
        grid_import_max_kw=500.0,
        transformer_capacity_kw=500.0,
        grid_export_max_kw=0.0,
        anti_backflow=True,
        c_deg=0.0,
        use_milp=True,
        terminal_constraint=False,
    )

    result = solve_benchmark(
        pv_kw=[0.0, 0.0, 0.0],
        wind_kw=[0.0, 0.0, 0.0],
        load_kw=[100.0, 100.0, 100.0],
        buy_price=[1.0, 1.0, 1.0],
        sell_price=[0.0, 0.0, 0.0],
        config=cfg,
    )

    powers = result.battery_power
    assert powers[0] <= 25.0 + 1e-6
    assert all(abs(powers[i] - powers[i - 1]) <= 25.0 + 1e-6 for i in range(1, len(powers)))


def test_battery_smooth_penalty_discourages_unnecessary_power_changes():
    cfg = BenchmarkConfig(
        dt_hours=0.25,
        battery_capacity_kwh=100.0,
        battery_soc_init=0.9,
        battery_soc_min=0.1,
        battery_soc_max=0.9,
        battery_charge_eff=1.0,
        battery_discharge_eff=1.0,
        battery_charge_max_kw=0.0,
        battery_discharge_max_kw=100.0,
        battery_initial_power_kw=0.0,
        battery_smooth_penalty=1.0,
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

    assert abs(result.battery_power[0]) <= 1e-6


def test_soft_ramp_limit_stays_feasible_when_load_drops_under_anti_backflow():
    cfg = BenchmarkConfig(
        dt_hours=0.25,
        battery_capacity_kwh=100.0,
        battery_soc_init=0.9,
        battery_soc_min=0.1,
        battery_soc_max=0.9,
        battery_charge_eff=1.0,
        battery_discharge_eff=1.0,
        battery_charge_max_kw=0.0,
        battery_discharge_max_kw=100.0,
        battery_ramp_limit_kw=10.0,
        battery_initial_power_kw=100.0,
        battery_smooth_penalty=0.01,
        battery_ramp_slack_penalty=100.0,
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
        load_kw=[0.0],
        buy_price=[1.0],
        sell_price=[0.0],
        config=cfg,
    )

    assert result.solver_status.lower() in {"ok", "warning"}
    assert abs(result.battery_power[0]) <= 1e-6
