import pandas as pd
from openpyxl import load_workbook

from mpc.microgrid.solver import write_excel
from mpc.solvers.benchmark.solver import BenchmarkConfig, BenchmarkResult, solve_benchmark


def test_vpp_settlement_forbids_charging_but_allows_discharge_in_window():
    cfg = BenchmarkConfig(
        dt_hours=0.25,
        battery_capacity_kwh=100.0,
        battery_soc_init=0.5,
        battery_soc_min=0.1,
        battery_soc_max=0.9,
        battery_charge_max_kw=100.0,
        battery_discharge_max_kw=100.0,
        grid_import_max_kw=500.0,
        transformer_capacity_kw=500.0,
        grid_export_max_kw=0.0,
        anti_backflow=True,
        c_deg=0.0,
        use_milp=True,
        terminal_constraint=False,
        vpp_enabled=True,
        vpp_charge_price=0.2,
        vpp_baseline_kw=[0.0, 60.0],
        vpp_window_mask=[False, True],
    )

    result = solve_benchmark(
        pv_kw=[0.0, 0.0],
        wind_kw=[0.0, 0.0],
        load_kw=[50.0, 200.0],
        buy_price=[1.0, 1.0],
        sell_price=[0.0, 0.0],
        config=cfg,
    )

    assert result.battery_power[1] == 100.0
    assert result.grid_import[1] == 100.0
    assert result.vpp_response_kw == [0.0, 40.0]
    assert result.vpp_benefit == 8.0


def test_vpp_variables_are_only_created_for_window_steps():
    cfg = BenchmarkConfig(
        dt_hours=0.25,
        battery_charge_max_kw=0.0,
        battery_discharge_max_kw=0.0,
        grid_import_max_kw=500.0,
        transformer_capacity_kw=500.0,
        grid_export_max_kw=0.0,
        anti_backflow=True,
        c_deg=0.0,
        use_milp=True,
        terminal_constraint=False,
        vpp_enabled=True,
        vpp_charge_price=0.2,
        vpp_window_mask=[False, True, True, False],
    )
    model_sizes = {}

    solve_benchmark(
        pv_kw=[0.0, 0.0, 0.0, 0.0],
        wind_kw=[0.0, 0.0, 0.0, 0.0],
        load_kw=[100.0, 100.0, 100.0, 100.0],
        buy_price=[1.0, 1.0, 1.0, 1.0],
        sell_price=[0.0, 0.0, 0.0, 0.0],
        config=cfg,
        debug_model_callback=lambda m: model_sizes.update(
            vpp_steps=len(list(m.T_vpp)),
            y_above=len(m.y_above),
            vpp_resp=len(m.VPP_resp),
        ),
    )

    assert model_sizes == {
        "vpp_steps": 2,
        "y_above": 2,
        "vpp_resp": 2,
    }


def test_optimal_peak_soft_guard_adds_slack_penalty_to_objective():
    cfg = BenchmarkConfig(
        dt_hours=0.25,
        battery_charge_max_kw=0.0,
        battery_discharge_max_kw=0.0,
        grid_import_max_kw=500.0,
        transformer_capacity_kw=500.0,
        grid_export_max_kw=0.0,
        anti_backflow=True,
        c_deg=0.0,
        use_milp=True,
        terminal_constraint=False,
        optimal_peak_kw=80.0,
        peak_slack_penalty=5000.0,
    )
    has_slack_penalty = {}

    solve_benchmark(
        pv_kw=[0.0],
        wind_kw=[0.0],
        load_kw=[100.0],
        buy_price=[1.0],
        sell_price=[0.0],
        config=cfg,
        debug_model_callback=lambda m: has_slack_penalty.update(
            present="Peak_slack" in str(m.cost.expr)
        ),
    )

    assert has_slack_penalty["present"] is True


def test_solver_excel_includes_vpp_response_and_daily_benefit(tmp_path):
    output = tmp_path / "solver_vpp.xlsx"
    result = BenchmarkResult(
        vpp_benefit=8.0,
        vpp_response_kw=[40.0] + [0.0] * 95,
    )
    traj = [(0.5, 0.0, 100.0)] + [(0.5, 0.0, 0.0)] * 95
    ts_list = pd.date_range("2026-05-06 11:00:00", periods=96, freq="15min")

    write_excel(
        traj,
        ts_list,
        pv_kw=[0.0] * 96,
        load_kw=[100.0] + [0.0] * 95,
        buy_prices=[1.0] * 96,
        cfg={
            "cost": {"demand_rate": 0.0},
            "vpp": {
                "enabled": True,
                "charge_price": 0.2,
                "start_hour": 11,
                "end_hour": 13,
                "baseline_kw": [60.0] + [0.0] * 95,
            },
        },
        result=result,
        output_path=output,
    )

    wb = load_workbook(output, read_only=True, data_only=True)
    ws_traj = wb["15min_trajectory"]
    ws_daily = wb["daily_summary"]
    ws_cost = wb["cost_summary"]

    trajectory_headers = [cell.value for cell in ws_traj[1]]
    daily_headers = [cell.value for cell in ws_daily[1]]
    cost_rows = {
        ws_cost.cell(row=i, column=1).value: ws_cost.cell(row=i, column=2).value
        for i in range(2, ws_cost.max_row + 1)
    }

    assert trajectory_headers[-5:] == [
        "互济窗口",
        "互济基线(kW)",
        "互济结算功率(kW)",
        "互济结算电量(kWh)",
        "互济收益(元)",
    ]
    assert ws_traj.cell(row=2, column=9).value == "是"
    assert ws_traj.cell(row=2, column=10).value == 60.0
    assert ws_traj.cell(row=2, column=11).value == 40.0
    assert ws_traj.cell(row=2, column=12).value == 10.0
    assert ws_traj.cell(row=2, column=13).value == 8.0

    assert daily_headers[-2:] == ["互济结算电量(kWh)", "互济收益(元)"]
    assert ws_daily.max_column == 14
    assert ws_daily.cell(row=2, column=12).value == 17.0
    assert ws_daily.cell(row=2, column=13).value == 10.0
    assert ws_daily.cell(row=2, column=14).value == 8.0
    assert cost_rows["互济总收益(元)"] == 8.0
    assert cost_rows["互济总结算电量(kWh)"] == 10.0
    assert cost_rows["等效互济均价(元/kWh)"] == 0.2
    wb.close()
