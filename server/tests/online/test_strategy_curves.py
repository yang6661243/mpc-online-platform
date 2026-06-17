from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from server.api.main import create_app
from server.api.services.comparison import StrategyMetrics
from server.api.database import create_sqlite_memory_session
from server.api.database.orm import StrategyCurvePoint, Telemetry15Min
from server.api.services.mpc.orchestrator import MpcRunnerResult
from server.api.utils.time import parse_timestamp


def _add_ready_telemetry(session):
    session.add_all(
        [
            Telemetry15Min(
                plant_id="aodelai",
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
                plant_id="aodelai",
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


def test_run_saves_strategy_curve_points_and_dashboard_returns_series(tmp_path: Path):
    session = create_sqlite_memory_session()
    _add_ready_telemetry(session)

    def fake_runner(_run_input):
        return MpcRunnerResult(
            metrics=StrategyMetrics(
                peak_kw=390.0,
                purchase_cost_yuan=130.0,
                export_revenue_yuan=0.0,
                degradation_cost_yuan=1.0,
                demand_charge_yuan=8.0,
                total_cost_yuan=139.0,
                soc_min=0.40,
                soc_max=0.75,
                reverse_flow_count=0,
            ),
            curve=[
                {"grid_power_kw": 380.0, "battery_power_kw": 40.0, "soc": 0.58},
                {"grid_power_kw": 390.0, "battery_power_kw": 30.0, "soc": 0.57},
            ],
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
            "request_id": "req_curve",
            "plant_id": "aodelai",
            "start_time": "2026-06-12T10:00:00+08:00",
            "end_time": "2026-06-12T10:30:00+08:00",
        },
    )

    assert response.status_code == 200
    points = session.scalars(
        select(StrategyCurvePoint).order_by(StrategyCurvePoint.time)
    ).all()
    assert len(points) == 2
    assert points[0].actual_grid_power_kw == 400.0
    assert points[0].actual_battery_power_kw == 20.0
    assert points[0].actual_soc == 0.59
    assert points[0].mpc_grid_power_kw == 380.0
    assert points[0].mpc_battery_power_kw == 40.0
    assert points[0].mpc_soc == 0.58

    dashboard = client.get("/api/v1/plants/aodelai/dashboard").json()
    assert dashboard["series"][0]["time"] == "2026-06-12T02:15:00"
    assert dashboard["series"][0]["quality_flag"] == "ok"
    assert dashboard["series"][0]["actual_grid_power_kw"] == 400.0
    assert dashboard["series"][0]["mpc_grid_power_kw"] == 380.0
    assert dashboard["series"][1]["time"] == "2026-06-12T02:30:00"
    assert dashboard["series"][1]["actual_battery_power_kw"] == -10.0
    assert dashboard["series"][1]["mpc_soc"] == 0.57
