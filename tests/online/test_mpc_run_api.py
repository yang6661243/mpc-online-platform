from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient
from sqlalchemy import select

from microgrid_online.api import create_app
from microgrid_online.comparison import StrategyMetrics
from microgrid_online.database import create_sqlite_memory_session
from microgrid_online.models import MpcRun, StrategyComparison, Telemetry15Min
from microgrid_online.time_utils import parse_timestamp


def _add_ready_telemetry(session, plant_id="aodelai"):
    session.add_all(
        [
            Telemetry15Min(
                plant_id=plant_id,
                start_time=parse_timestamp("2026-06-12T10:00:00+08:00"),
                end_time=parse_timestamp("2026-06-12T10:15:00+08:00"),
                grid_power_kw_avg=400.0,
                grid_power_kw_max=405.0,
                battery_power_kw_avg=20.0,
                load_minus_pv_kw_avg=420.0,
                soc_start=0.60,
                soc_end=0.59,
                grid_sample_count=3,
                battery_sample_count=3,
                quality_flag="ok",
            ),
            Telemetry15Min(
                plant_id=plant_id,
                start_time=parse_timestamp("2026-06-12T10:15:00+08:00"),
                end_time=parse_timestamp("2026-06-12T10:30:00+08:00"),
                grid_power_kw_avg=430.0,
                grid_power_kw_max=435.0,
                battery_power_kw_avg=-10.0,
                load_minus_pv_kw_avg=420.0,
                soc_start=0.59,
                soc_end=0.60,
                grid_sample_count=3,
                battery_sample_count=3,
                quality_flag="ok",
            ),
        ]
    )
    session.commit()


def test_run_endpoint_exports_scenario_and_saves_comparison(tmp_path: Path):
    session = create_sqlite_memory_session()
    _add_ready_telemetry(session)
    runner_calls = []

    def fake_runner(run_input):
        runner_calls.append(run_input)
        assert run_input.scenario.output_path.exists()
        assert run_input.scenario.steps == 2
        assert run_input.actual_metrics.peak_kw == 430.0
        assert run_input.target_peak_kw == 260.0
        return StrategyMetrics(
            peak_kw=380.0,
            purchase_cost_yuan=120.0,
            export_revenue_yuan=0.0,
            degradation_cost_yuan=2.0,
            demand_charge_yuan=7.0,
            total_cost_yuan=129.0,
            soc_min=0.35,
            soc_max=0.70,
            reverse_flow_count=0,
        )

    client = TestClient(
        create_app(
            session_factory=lambda: session,
            mpc_runner=fake_runner,
            run_output_dir=tmp_path,
        )
    )

    response = client.post(
        "/api/v1/mpc/run",
        json={
            "request_id": "req_run_1",
            "plant_id": "aodelai",
            "start_time": "2026-06-12T10:00:00+08:00",
            "end_time": "2026-06-12T10:30:00+08:00",
            "profile": "customer_demo_base",
            "buy_price": 0.8,
            "sell_price": 0.3,
            "c_deg": 0.05,
            "demand_rate": 30.0,
            "billing_days": 30,
            "target_peak_kw": 260.0,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["status"] == "succeeded"
    assert body["run_id"] == "mpc_req_run_1"
    assert body["target_peak_kw"] == 260.0
    assert Path(body["scenario_path"]).exists()
    assert len(runner_calls) == 1

    run = session.scalar(select(MpcRun).where(MpcRun.run_id == "mpc_req_run_1"))
    assert run is not None
    assert run.status == "succeeded"
    assert run.profile == "customer_demo_base"

    comparison = session.scalar(
        select(StrategyComparison).where(StrategyComparison.run_id == "mpc_req_run_1")
    )
    assert comparison is not None
    assert comparison.actual_peak_kw == 430.0
    assert comparison.mpc_peak_kw == 380.0
    assert comparison.peak_reduction_kw == 50.0
    assert comparison.cost_saving_yuan > 0


def test_run_result_endpoint_returns_status_and_comparison(tmp_path: Path):
    session = create_sqlite_memory_session()
    _add_ready_telemetry(session)

    def fake_runner(_run_input):
        return StrategyMetrics(
            peak_kw=390.0,
            purchase_cost_yuan=130.0,
            export_revenue_yuan=0.0,
            degradation_cost_yuan=1.0,
            demand_charge_yuan=8.0,
            total_cost_yuan=139.0,
            soc_min=0.40,
            soc_max=0.75,
            reverse_flow_count=0,
        )

    client = TestClient(
        create_app(
            session_factory=lambda: session,
            mpc_runner=fake_runner,
            run_output_dir=tmp_path,
        )
    )
    client.post(
        "/api/v1/mpc/run",
        json={
            "request_id": "req_run_2",
            "plant_id": "aodelai",
            "start_time": "2026-06-12T10:00:00+08:00",
            "end_time": "2026-06-12T10:30:00+08:00",
        },
    )

    response = client.get("/api/v1/mpc/runs/mpc_req_run_2")

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == "mpc_req_run_2"
    assert body["plant_id"] == "aodelai"
    assert body["status"] == "succeeded"
    assert body["comparison"]["mpc_peak_kw"] == 390.0
    assert body["comparison"]["actual_peak_kw"] == 430.0


def test_run_endpoint_uses_default_cli_runner_when_no_runner_is_injected(tmp_path: Path):
    session = create_sqlite_memory_session()
    _add_ready_telemetry(session)
    commands = []

    def fake_command(command, _cwd):
        commands.append(command)
        config_path = Path(command[command.index("--config") + 1])
        import yaml

        cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        output_path = Path(cfg["output"])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
            pd.DataFrame(
                {
                    "时间": ["2026-06-12 10:00:00", "2026-06-12 10:15:00"],
                    "SOC": [0.6, 0.59],
                    "电池功率(kW)": [40.0, 10.0],
                    "电网功率(kW)": [380.0, 410.0],
                    "购电价(元/kWh)": [0.8, 0.8],
                    "售电价(元/kWh)": [0.3, 0.3],
                }
            ).to_excel(writer, sheet_name="15min_trajectory", index=False)
            pd.DataFrame(
                {
                    "指标": ["购电费(元)", "售电收益(元)", "储能衰减(元)", "需量费(元)", "可控成本(元)", "峰值需量(kW)"],
                    "MPC 数值": [150.0, 0.0, 1.0, 10.0, 161.0, 410.0],
                }
            ).to_excel(writer, sheet_name="cost_summary", index=False)

    client = TestClient(
        create_app(
            session_factory=lambda: session,
            run_output_dir=tmp_path,
            mpc_command_runner=fake_command,
            mpc_runner_project_root=tmp_path,
        )
    )

    response = client.post(
        "/api/v1/mpc/run",
        json={
            "request_id": "req_default_runner",
            "plant_id": "aodelai",
            "start_time": "2026-06-12T10:00:00+08:00",
            "end_time": "2026-06-12T10:30:00+08:00",
        },
    )

    assert response.status_code == 200
    assert response.json()["comparison"]["mpc_peak_kw"] == 410.0
    assert len(commands) == 1
