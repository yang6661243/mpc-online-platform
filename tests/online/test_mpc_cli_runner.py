from pathlib import Path

import pandas as pd
import yaml

from microgrid_online.comparison import StrategyMetrics
from microgrid_online.mpc_adapter import MpcScenarioExport
from microgrid_online.mpc_cli_runner import (
    MicrogridMpcCliRunner,
    MicrogridMpcCliRunnerConfig,
    parse_microgrid_mpc_output,
)
from microgrid_online.mpc_run import OnlineMpcRunInput
from microgrid_online.time_utils import parse_timestamp


def _write_mpc_output(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame(
            {
                "时间": ["2026-06-12 10:00:00", "2026-06-12 10:15:00"],
                "光伏出力(kW)": [0.0, 0.0],
                "风电出力(kW)": [0.0, 0.0],
                "负荷功率(kW)": [420.0, 420.0],
                "SOC": [0.60, 0.58],
                "电池功率(kW)": [40.0, -10.0],
                "电网功率(kW)": [380.0, 430.0],
                "购电价(元/kWh)": [0.8, 0.8],
                "售电价(元/kWh)": [0.3, 0.3],
            }
        ).to_excel(writer, sheet_name="15min_trajectory", index=False)
        pd.DataFrame(
            {
                "指标": ["购电费(元)", "售电收益(元)", "储能衰减(元)", "需量费(元)", "可控成本(元)", "峰值需量(kW)"],
                "MPC 数值": [162.0, 0.0, 0.625, 17.5, 180.125, 430.0],
                "MILP 基准": [160.0, 0.0, 0.5, 16.0, 176.5, 400.0],
                "单位/说明": ["", "", "", "", "", ""],
            }
        ).to_excel(writer, sheet_name="cost_summary", index=False)


def test_parse_microgrid_mpc_output_reads_metrics(tmp_path: Path):
    output_path = tmp_path / "mpc_result.xlsx"
    _write_mpc_output(output_path)

    metrics = parse_microgrid_mpc_output(output_path)

    assert metrics == StrategyMetrics(
        peak_kw=430.0,
        purchase_cost_yuan=162.0,
        export_revenue_yuan=0.0,
        degradation_cost_yuan=0.625,
        demand_charge_yuan=17.5,
        total_cost_yuan=180.125,
        soc_min=0.58,
        soc_max=0.60,
        reverse_flow_count=0,
    )


def test_cli_runner_writes_config_invokes_microgrid_mpc_and_parses_output(tmp_path: Path):
    scenario_path = tmp_path / "scenario.xlsx"
    scenario_path.write_bytes(b"placeholder")
    invoked = []

    def fake_command(command, cwd):
        invoked.append((command, cwd))
        config_path = Path(command[command.index("--config") + 1])
        cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert cfg["scenario"]["data_file"] == str(scenario_path)
        assert cfg["scenario"]["sheets"] == {"load": "load", "pv_wind": "pv", "price": "price"}
        assert cfg["mpc"]["forecast_mode"] == "file"
        assert cfg["mpc"]["target_peak_mode"] == "manual"
        assert cfg["mpc"]["target_peak_kw"] == 387.0
        assert cfg["battery"]["soc_init"] == 0.6
        _write_mpc_output(Path(cfg["output"]))

    runner = MicrogridMpcCliRunner(
        MicrogridMpcCliRunnerConfig(
            project_root=tmp_path,
            target_peak_ratio=0.9,
            horizon_steps=8,
        ),
        command_runner=fake_command,
    )
    run_input = OnlineMpcRunInput(
        run_id="mpc_req_1",
        plant_id="aodelai",
        profile="online_cli",
        start_time=parse_timestamp("2026-06-12T10:00:00+08:00"),
        end_time=parse_timestamp("2026-06-12T10:30:00+08:00"),
        scenario=MpcScenarioExport(output_path=scenario_path, load_base_kw=1000.0, steps=2),
        telemetry_rows=[],
        actual_metrics=StrategyMetrics(
            peak_kw=430.0,
            purchase_cost_yuan=170.0,
            export_revenue_yuan=0.0,
            degradation_cost_yuan=1.0,
            demand_charge_yuan=20.0,
            total_cost_yuan=191.0,
            soc_min=0.58,
            soc_max=0.60,
            reverse_flow_count=0,
        ),
        buy_price=0.8,
        sell_price=0.3,
        c_deg=0.05,
        demand_rate=30.0,
        billing_days=30.0,
    )

    result = runner(run_input)

    assert len(invoked) == 1
    command, cwd = invoked[0]
    assert command[:3] == [runner.python_executable, "-m", "microgrid.mpc"]
    assert cwd == tmp_path
    assert result.metrics.peak_kw == 430.0
    assert result.metrics.total_cost_yuan == 180.125
    assert result.curve[0]["grid_power_kw"] == 380.0
    assert result.curve[0]["battery_power_kw"] == 40.0
