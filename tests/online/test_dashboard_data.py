from microgrid_online.dashboard_data import build_dashboard_payload
from microgrid_online.database import create_sqlite_memory_session
from microgrid_online.models import Telemetry15Min
from microgrid_online.time_utils import parse_timestamp


def _add_telemetry(session):
    session.add_all(
        [
            Telemetry15Min(
                plant_id="ecloud_factory",
                start_time=parse_timestamp("2026-06-13T00:00:00+08:00"),
                end_time=parse_timestamp("2026-06-13T00:15:00+08:00"),
                grid_power_kw_avg=180.0,
                grid_power_kw_max=185.0,
                battery_power_kw_avg=2.0,
                load_minus_pv_kw_avg=182.0,
                soc_start=0.51,
                soc_end=0.50,
                grid_sample_count=15,
                battery_sample_count=15,
                quality_flag="ok",
            ),
            Telemetry15Min(
                plant_id="ecloud_factory",
                start_time=parse_timestamp("2026-06-13T00:15:00+08:00"),
                end_time=parse_timestamp("2026-06-13T00:30:00+08:00"),
                grid_power_kw_avg=210.0,
                grid_power_kw_max=216.0,
                battery_power_kw_avg=0.1,
                load_minus_pv_kw_avg=210.1,
                soc_start=0.50,
                soc_end=0.50,
                grid_sample_count=15,
                battery_sample_count=15,
                quality_flag="ok",
            ),
        ]
    )
    session.commit()


def test_build_dashboard_payload_returns_actual_series_without_mpc_result():
    session = create_sqlite_memory_session()
    _add_telemetry(session)

    payload = build_dashboard_payload(session, plant_id="ecloud_factory", window_hours=24)

    assert payload["plant_id"] == "ecloud_factory"
    assert payload["current"]["time"] == "2026-06-12T16:30:00"
    assert payload["current"]["grid_power_kw"] == 210.0
    assert payload["current"]["battery_power_kw"] == 0.1
    assert payload["current"]["soc"] == 0.5
    assert payload["current"]["quality_flag"] == "ok"
    assert payload["comparison"] is None
    assert payload["series"] == [
        {
            "time": "2026-06-12T16:15:00",
            "actual_grid_power_kw": 180.0,
            "actual_battery_power_kw": 2.0,
            "actual_soc": 0.5,
            "load_minus_pv_kw": 182.0,
            "mpc_grid_power_kw": None,
            "mpc_battery_power_kw": None,
            "mpc_soc": None,
            "buy_price": None,
            "sell_price": None,
            "quality_flag": "ok",
        },
        {
            "time": "2026-06-12T16:30:00",
            "actual_grid_power_kw": 210.0,
            "actual_battery_power_kw": 0.1,
            "actual_soc": 0.5,
            "load_minus_pv_kw": 210.1,
            "mpc_grid_power_kw": None,
            "mpc_battery_power_kw": None,
            "mpc_soc": None,
            "buy_price": None,
            "sell_price": None,
            "quality_flag": "ok",
        },
    ]
