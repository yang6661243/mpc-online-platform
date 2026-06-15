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
            "actual_load_kw": None,
            "actual_pv_kw": None,
            "load_minus_pv_kw": 182.0,
            "mpc_grid_power_kw": None,
            "mpc_battery_power_kw": None,
            "mpc_soc": None,
            "mpc_load_kw": None,
            "mpc_pv_kw": None,
            "buy_price": None,
            "sell_price": None,
            "quality_flag": "ok",
        },
        {
            "time": "2026-06-12T16:30:00",
            "actual_grid_power_kw": 210.0,
            "actual_battery_power_kw": 0.1,
            "actual_soc": 0.5,
            "actual_load_kw": None,
            "actual_pv_kw": None,
            "load_minus_pv_kw": 210.1,
            "mpc_grid_power_kw": None,
            "mpc_battery_power_kw": None,
            "mpc_soc": None,
            "mpc_load_kw": None,
            "mpc_pv_kw": None,
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
                actual_load_kw=150.0,
                actual_pv_kw=40.19,
                load_minus_pv_kw=109.81,
                mpc_grid_power_kw=148.56,
                mpc_battery_power_kw=-38.75,
                mpc_soc=0.5,
                mpc_load_kw=150.0,
                mpc_pv_kw=40.19,
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
                actual_load_kw=160.0,
                actual_pv_kw=42.47,
                load_minus_pv_kw=117.53,
                mpc_grid_power_kw=148.56,
                mpc_battery_power_kw=-31.03,
                mpc_soc=0.5118,
                mpc_load_kw=160.0,
                mpc_pv_kw=42.47,
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
    assert payload["series"][0]["actual_load_kw"] == 150.0
    assert payload["series"][0]["actual_pv_kw"] == 40.19
    assert payload["series"][0]["mpc_load_kw"] == 150.0
    assert payload["series"][0]["mpc_pv_kw"] == 40.19
    assert [point["mpc_grid_power_kw"] for point in payload["series"]] == [148.56, 148.56]


def test_build_dashboard_payload_filters_run_id_by_time_range_and_recomputes_comparison():
    session = create_sqlite_memory_session()
    _add_telemetry(session)
    session.add(
        StrategyComparison(
            run_id="hehong_apr2026_real_mpc",
            plant_id="ecloud_factory",
            actual_peak_kw=300.0,
            mpc_peak_kw=250.0,
            peak_reduction_kw=50.0,
            peak_reduction_pct=16.67,
            actual_cost_yuan=1000.0,
            mpc_cost_yuan=800.0,
            cost_saving_yuan=200.0,
            cost_saving_pct=20.0,
        )
    )
    session.add_all(
        [
            StrategyCurvePoint(
                run_id="hehong_apr2026_real_mpc",
                plant_id="ecloud_factory",
                time=parse_timestamp("2026-04-01T00:00:00+08:00"),
                actual_grid_power_kw=100.0,
                actual_battery_power_kw=0.0,
                actual_soc=0.5,
                load_minus_pv_kw=100.0,
                mpc_grid_power_kw=90.0,
                mpc_battery_power_kw=10.0,
                mpc_soc=0.5,
                buy_price=1.0,
                sell_price=0.0,
            ),
            StrategyCurvePoint(
                run_id="hehong_apr2026_real_mpc",
                plant_id="ecloud_factory",
                time=parse_timestamp("2026-04-01T00:15:00+08:00"),
                actual_grid_power_kw=300.0,
                actual_battery_power_kw=0.0,
                actual_soc=0.5,
                load_minus_pv_kw=300.0,
                mpc_grid_power_kw=220.0,
                mpc_battery_power_kw=80.0,
                mpc_soc=0.6,
                buy_price=1.0,
                sell_price=0.0,
            ),
            StrategyCurvePoint(
                run_id="hehong_apr2026_real_mpc",
                plant_id="ecloud_factory",
                time=parse_timestamp("2026-04-01T00:30:00+08:00"),
                actual_grid_power_kw=120.0,
                actual_battery_power_kw=0.0,
                actual_soc=0.5,
                load_minus_pv_kw=120.0,
                mpc_grid_power_kw=110.0,
                mpc_battery_power_kw=10.0,
                mpc_soc=0.7,
                buy_price=1.0,
                sell_price=0.0,
            ),
        ]
    )
    session.commit()

    payload = build_dashboard_payload(
        session,
        plant_id="ecloud_factory",
        run_id="hehong_apr2026_real_mpc",
        start_time="2026-03-31T16:15:00",
        end_time="2026-03-31T16:30:00",
    )

    assert [point["time"] for point in payload["series"]] == ["2026-03-31T16:30:00"]
    assert payload["current"]["grid_power_kw"] == 300.0
    assert payload["comparison"]["actual_peak_kw"] == 300.0
    assert payload["comparison"]["mpc_peak_kw"] == 220.0
    assert payload["comparison"]["peak_reduction_kw"] == 80.0
    assert payload["comparison"]["actual_cost_yuan"] == 300.0 * 0.25 + 300.0 * 39.0 * 0.25 / 24.0 / 30.0
    assert (
        payload["comparison"]["mpc_cost_yuan"]
        == 220.0 * 0.25 + 220.0 * 39.0 * 0.25 / 24.0 / 30.0 + 80.0 * 0.25 * 0.05
    )


def test_build_dashboard_payload_time_range_updates_current_and_comparison_without_run_id():
    session = create_sqlite_memory_session()
    _add_telemetry(session)
    session.add(
        Telemetry15Min(
            plant_id="ecloud_factory",
            start_time=parse_timestamp("2026-06-13T00:30:00+08:00"),
            end_time=parse_timestamp("2026-06-13T00:45:00+08:00"),
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
            run_id="selected_window_mpc",
            plant_id="ecloud_factory",
            actual_peak_kw=999.0,
            mpc_peak_kw=888.0,
            peak_reduction_kw=111.0,
            peak_reduction_pct=11.11,
            actual_cost_yuan=9999.0,
            mpc_cost_yuan=8888.0,
            cost_saving_yuan=1111.0,
            cost_saving_pct=11.11,
        )
    )
    session.add_all(
        [
            StrategyCurvePoint(
                run_id="selected_window_mpc",
                plant_id="ecloud_factory",
                time=parse_timestamp("2026-06-13T00:00:00+08:00"),
                actual_grid_power_kw=180.0,
                actual_battery_power_kw=2.0,
                actual_soc=0.5,
                load_minus_pv_kw=182.0,
                mpc_grid_power_kw=150.0,
                mpc_battery_power_kw=32.0,
                mpc_soc=0.51,
                buy_price=1.0,
                sell_price=0.0,
            ),
            StrategyCurvePoint(
                run_id="selected_window_mpc",
                plant_id="ecloud_factory",
                time=parse_timestamp("2026-06-13T00:15:00+08:00"),
                actual_grid_power_kw=210.0,
                actual_battery_power_kw=0.1,
                actual_soc=0.5,
                load_minus_pv_kw=210.1,
                mpc_grid_power_kw=160.0,
                mpc_battery_power_kw=50.1,
                mpc_soc=0.52,
                buy_price=1.0,
                sell_price=0.0,
            ),
        ]
    )
    session.commit()

    payload = build_dashboard_payload(
        session,
        plant_id="ecloud_factory",
        window_hours=24,
        start_time="2026-06-12T16:00:00",
        end_time="2026-06-12T16:15:00",
    )

    assert [point["time"] for point in payload["series"]] == ["2026-06-12T16:15:00"]
    assert payload["current"]["time"] == "2026-06-12T16:15:00"
    assert payload["current"]["grid_power_kw"] == 180.0
    assert payload["comparison"]["run_id"] == "selected_window_mpc"
    assert payload["comparison"]["actual_peak_kw"] == 180.0
    assert payload["comparison"]["mpc_peak_kw"] == 150.0
    assert payload["comparison"]["peak_reduction_kw"] == 30.0
