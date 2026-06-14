from microgrid_online.dashboard_data import build_dashboard_payload
from microgrid_online.database import create_sqlite_memory_session
from microgrid_online.models import StrategyComparison, StrategyCurvePoint, Telemetry15Min
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


def test_build_dashboard_payload_can_replay_a_specific_run_id():
    session = create_sqlite_memory_session()
    _add_telemetry(session)
    session.add(
        Telemetry15Min(
            plant_id="ecloud_factory",
            start_time=parse_timestamp("2026-06-14T00:00:00+08:00"),
            end_time=parse_timestamp("2026-06-14T00:15:00+08:00"),
            grid_power_kw_avg=999.0,
            grid_power_kw_max=999.0,
            battery_power_kw_avg=0.0,
            load_minus_pv_kw_avg=999.0,
            soc_start=0.60,
            soc_end=0.60,
            grid_sample_count=15,
            battery_sample_count=15,
            quality_flag="ok",
        )
    )
    session.add(
        StrategyComparison(
            run_id="hehong_apr2026_real_mpc",
            plant_id="ecloud_factory",
            actual_peak_kw=662.46,
            mpc_peak_kw=617.50,
            peak_reduction_kw=44.96,
            peak_reduction_pct=6.79,
            actual_cost_yuan=86051.90,
            mpc_cost_yuan=74778.18,
            cost_saving_yuan=11273.73,
            cost_saving_pct=13.10,
        )
    )
    session.add_all(
        [
            StrategyCurvePoint(
                run_id="hehong_apr2026_real_mpc",
                plant_id="ecloud_factory",
                time=parse_timestamp("2026-04-01T00:00:00+08:00"),
                actual_grid_power_kw=109.81,
                actual_battery_power_kw=0.0,
                actual_soc=0.5,
                load_minus_pv_kw=109.81,
                mpc_grid_power_kw=148.56,
                mpc_battery_power_kw=-38.75,
                mpc_soc=0.5,
                buy_price=0.241,
                sell_price=0.0,
            ),
            StrategyCurvePoint(
                run_id="hehong_apr2026_real_mpc",
                plant_id="ecloud_factory",
                time=parse_timestamp("2026-04-01T00:15:00+08:00"),
                actual_grid_power_kw=117.53,
                actual_battery_power_kw=0.0,
                actual_soc=0.5,
                load_minus_pv_kw=117.53,
                mpc_grid_power_kw=148.56,
                mpc_battery_power_kw=-31.03,
                mpc_soc=0.5118,
                buy_price=0.241,
                sell_price=0.0,
            ),
        ]
    )
    session.commit()

    payload = build_dashboard_payload(
        session,
        plant_id="ecloud_factory",
        window_hours=24,
        run_id="hehong_apr2026_real_mpc",
    )

    assert payload["comparison"]["run_id"] == "hehong_apr2026_real_mpc"
    assert payload["current"]["time"] == "2026-03-31T16:30:00"
    assert payload["current"]["grid_power_kw"] == 117.53
    assert payload["current"]["battery_power_kw"] == 0.0
    assert payload["current"]["soc"] == 0.5
    assert [point["mpc_grid_power_kw"] for point in payload["series"]] == [148.56, 148.56]
