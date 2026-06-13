from fastapi.testclient import TestClient

from microgrid_online.aggregation import aggregate_telemetry_15min
from microgrid_online.api import create_app
from microgrid_online.database import create_sqlite_memory_session
from microgrid_online.models import Telemetry15Min
from microgrid_online.time_utils import parse_timestamp


def test_input_data_endpoint_accepts_grid_and_battery_records():
    session = create_sqlite_memory_session()
    client = TestClient(create_app(session_factory=lambda: session))

    grid_response = client.post(
        "/api/v1/mpc/input-data",
        json={
            "request_id": "req_grid_1",
            "plant_id": "aodelai",
            "data_type": "grid_meter",
            "generated_at": "2026-06-12T10:15:00+08:00",
            "records": [
                {"time": "2026-06-12T10:00:00+08:00", "grid_power_kw": 400.0},
            ],
        },
    )
    battery_response = client.post(
        "/api/v1/mpc/input-data",
        json={
            "request_id": "req_battery_1",
            "plant_id": "aodelai",
            "data_type": "battery",
            "generated_at": "2026-06-12T10:15:00+08:00",
            "records": [
                {"time": "2026-06-12T10:00:00+08:00", "battery_power_kw": 20.0, "soc": 0.6},
            ],
        },
    )

    assert grid_response.status_code == 200
    assert grid_response.json()["accepted_count"] == 1
    assert battery_response.status_code == 200
    assert battery_response.json()["accepted_count"] == 1


def test_input_data_endpoint_allows_ecloud_extension_cors_preflight():
    session = create_sqlite_memory_session()
    client = TestClient(create_app(session_factory=lambda: session))

    response = client.options(
        "/api/v1/mpc/input-data",
        headers={
            "Origin": "chrome-extension://becnmfbeidffckhenedfiahikaagpgek",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "chrome-extension://becnmfbeidffckhenedfiahikaagpgek"
    assert "POST" in response.headers["access-control-allow-methods"]


def test_dashboard_endpoint_returns_latest_status_cards():
    session = create_sqlite_memory_session()
    client = TestClient(create_app(session_factory=lambda: session))
    client.post(
        "/api/v1/mpc/input-data",
        json={
            "request_id": "req_grid_1",
            "plant_id": "aodelai",
            "data_type": "grid_meter",
            "generated_at": "2026-06-12T10:15:00+08:00",
            "records": [
                {"time": "2026-06-12T10:00:00+08:00", "grid_power_kw": 400.0},
            ],
        },
    )
    client.post(
        "/api/v1/mpc/input-data",
        json={
            "request_id": "req_battery_1",
            "plant_id": "aodelai",
            "data_type": "battery",
            "generated_at": "2026-06-12T10:15:00+08:00",
            "records": [
                {"time": "2026-06-12T10:00:00+08:00", "battery_power_kw": 20.0, "soc": 0.6},
            ],
        },
    )
    aggregate_telemetry_15min(
        session,
        plant_id="aodelai",
        start_time="2026-06-12T10:00:00+08:00",
        end_time="2026-06-12T10:15:00+08:00",
    )

    response = client.get("/api/v1/plants/aodelai/dashboard")

    assert response.status_code == 200
    body = response.json()
    assert body["plant_id"] == "aodelai"
    assert body["current"]["grid_power_kw"] == 400.0
    assert body["current"]["battery_power_kw"] == 20.0
    assert body["current"]["load_minus_pv_kw"] == 420.0
    assert body["current"]["soc"] == 0.6
    assert body["comparison"] is None


def test_input_data_endpoint_accepts_field_mapping_and_soc_percent():
    session = create_sqlite_memory_session()
    client = TestClient(create_app(session_factory=lambda: session))

    grid_response = client.post(
        "/api/v1/mpc/input-data",
        json={
            "request_id": "req_grid_mapped",
            "plant_id": "aodelai",
            "data_type": "grid_meter",
            "generated_at": "2026-06-12T10:15:00+08:00",
            "field_mapping": {"time": "ts", "grid_power_kw": "p_grid"},
            "power_signs": {"grid_power_kw": -1},
            "records": [
                {"ts": "2026-06-12T10:00:00+08:00", "p_grid": -400.0},
            ],
        },
    )
    battery_response = client.post(
        "/api/v1/mpc/input-data",
        json={
            "request_id": "req_battery_mapped",
            "plant_id": "aodelai",
            "data_type": "battery",
            "generated_at": "2026-06-12T10:15:00+08:00",
            "field_mapping": {
                "time": "ts",
                "battery_power_kw": "p_bess",
                "soc": "soc_pct",
            },
            "soc_unit": "percent",
            "records": [
                {"ts": "2026-06-12T10:00:00+08:00", "p_bess": 20.0, "soc_pct": 60.0},
            ],
        },
    )

    assert grid_response.status_code == 200
    assert battery_response.status_code == 200
    aggregate_telemetry_15min(
        session,
        plant_id="aodelai",
        start_time="2026-06-12T10:00:00+08:00",
        end_time="2026-06-12T10:15:00+08:00",
    )

    body = client.get("/api/v1/plants/aodelai/dashboard").json()
    assert body["current"]["grid_power_kw"] == 400.0
    assert body["current"]["battery_power_kw"] == 20.0
    assert body["current"]["soc"] == 0.6


def test_aggregate_endpoint_builds_dashboard_telemetry_from_raw_records():
    session = create_sqlite_memory_session()
    client = TestClient(create_app(session_factory=lambda: session))
    client.post(
        "/api/v1/mpc/input-data",
        json={
            "request_id": "req_grid_for_aggregate",
            "plant_id": "aodelai",
            "data_type": "grid_meter",
            "generated_at": "2026-06-12T10:15:00+08:00",
            "records": [
                {"time": "2026-06-12T10:00:00+08:00", "grid_power_kw": 400.0},
                {"time": "2026-06-12T10:05:00+08:00", "grid_power_kw": 420.0},
            ],
        },
    )
    client.post(
        "/api/v1/mpc/input-data",
        json={
            "request_id": "req_battery_for_aggregate",
            "plant_id": "aodelai",
            "data_type": "battery",
            "generated_at": "2026-06-12T10:15:00+08:00",
            "records": [
                {"time": "2026-06-12T10:00:00+08:00", "battery_power_kw": 20.0, "soc": 0.60},
                {"time": "2026-06-12T10:05:00+08:00", "battery_power_kw": 30.0, "soc": 0.58},
            ],
        },
    )

    aggregate_response = client.post(
        "/api/v1/plants/aodelai/aggregate",
        json={
            "start_time": "2026-06-12T10:00:00+08:00",
            "end_time": "2026-06-12T10:15:00+08:00",
            "window_minutes": 15,
        },
    )

    assert aggregate_response.status_code == 200
    assert aggregate_response.json() == {
        "success": True,
        "plant_id": "aodelai",
        "window_count": 1,
        "quality_counts": {"ok": 1},
    }

    dashboard = client.get("/api/v1/plants/aodelai/dashboard").json()
    assert dashboard["current"]["grid_power_kw"] == 410.0
    assert dashboard["current"]["battery_power_kw"] == 25.0
    assert dashboard["current"]["load_minus_pv_kw"] == 435.0
    assert dashboard["current"]["soc"] == 0.58


def test_dashboard_endpoint_filters_series_by_window_hours():
    session = create_sqlite_memory_session()
    client = TestClient(create_app(session_factory=lambda: session))
    session.add_all(
        [
            Telemetry15Min(
                plant_id="aodelai",
                start_time=parse_timestamp("2026-06-12T00:00:00+08:00"),
                end_time=parse_timestamp("2026-06-12T00:15:00+08:00"),
                grid_power_kw_avg=100.0,
                grid_power_kw_max=100.0,
                battery_power_kw_avg=1.0,
                load_minus_pv_kw_avg=101.0,
                soc_start=0.5,
                soc_end=0.5,
                grid_sample_count=1,
                battery_sample_count=1,
                quality_flag="ok",
            ),
            Telemetry15Min(
                plant_id="aodelai",
                start_time=parse_timestamp("2026-06-13T00:00:00+08:00"),
                end_time=parse_timestamp("2026-06-13T00:15:00+08:00"),
                grid_power_kw_avg=200.0,
                grid_power_kw_max=200.0,
                battery_power_kw_avg=2.0,
                load_minus_pv_kw_avg=202.0,
                soc_start=0.6,
                soc_end=0.6,
                grid_sample_count=1,
                battery_sample_count=1,
                quality_flag="ok",
            ),
        ]
    )
    session.commit()

    body = client.get("/api/v1/plants/aodelai/dashboard?window_hours=1").json()

    assert [point["time"] for point in body["series"]] == ["2026-06-12T16:15:00"]
    assert body["series"][0]["actual_grid_power_kw"] == 200.0


def test_dashboard_endpoint_rejects_window_hours_outside_supported_range():
    session = create_sqlite_memory_session()
    client = TestClient(create_app(session_factory=lambda: session))

    response = client.get("/api/v1/plants/aodelai/dashboard?window_hours=169")

    assert response.status_code == 422
