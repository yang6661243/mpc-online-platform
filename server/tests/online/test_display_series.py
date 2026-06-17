from server.api.database import create_sqlite_memory_session
from server.api.services.display import build_display_series_payload
from server.api.database.orm import RawBattery, RawGridMeter
from server.api.utils.time import parse_timestamp


def _add_grid(session, times_and_values):
    for time_value, grid_kw in times_and_values:
        session.add(
            RawGridMeter(
                plant_id="hehong_huajin",
                time=parse_timestamp(time_value),
                grid_power_kw=grid_kw,
            )
        )


def _add_battery(session, times_power_soc):
    for time_value, battery_kw, soc in times_power_soc:
        session.add(
            RawBattery(
                plant_id="hehong_huajin",
                time=parse_timestamp(time_value),
                battery_power_kw=battery_kw,
                soc=soc,
            )
        )


def test_display_series_quadratic_interpolates_short_gap_for_display_only():
    session = create_sqlite_memory_session()
    _add_grid(
        session,
        [
            ("2026-06-16T10:00:10+08:00", 100.0),
            ("2026-06-16T10:02:20+08:00", 140.0),
            ("2026-06-16T10:04:30+08:00", 260.0),
        ],
    )
    _add_battery(
        session,
        [
            ("2026-06-16T10:00:05+08:00", 10.0, 0.50),
            ("2026-06-16T10:02:05+08:00", 12.0, 0.52),
            ("2026-06-16T10:04:05+08:00", 14.0, 0.54),
        ],
    )
    session.commit()

    payload = build_display_series_payload(
        session,
        plant_id="hehong_huajin",
        reference_time=parse_timestamp("2026-06-16T10:04:59+08:00"),
        window_hours=2,
        max_gap_minutes=5,
    )

    by_time = {point["time"]: point for point in payload["series"]}
    interpolated = by_time["2026-06-16T02:01:00"]

    assert payload["plant_id"] == "hehong_huajin"
    assert payload["display_only"] is True
    assert interpolated["grid_power_kw"] is not None
    assert interpolated["quality"]["grid_power_kw"] == "interpolated_quadratic"
    assert interpolated["battery_power_kw"] is not None
    assert interpolated["quality"]["battery_power_kw"] == "interpolated_quadratic"
    assert interpolated["soc"] is not None
    assert interpolated["quality"]["soc"] == "interpolated_quadratic"
    assert interpolated["load_minus_pv_kw"] == round(
        interpolated["grid_power_kw"] + interpolated["battery_power_kw"],
        6,
    )
    assert interpolated["quality"]["load_minus_pv_kw"] == "derived"


def test_display_series_keeps_long_gap_null():
    session = create_sqlite_memory_session()
    _add_grid(
        session,
        [
            ("2026-06-16T10:00:00+08:00", 100.0),
            ("2026-06-16T10:10:00+08:00", 200.0),
            ("2026-06-16T10:20:00+08:00", 300.0),
        ],
    )
    session.commit()

    payload = build_display_series_payload(
        session,
        plant_id="hehong_huajin",
        reference_time=parse_timestamp("2026-06-16T10:20:00+08:00"),
        window_hours=2,
        max_gap_minutes=5,
    )

    by_time = {point["time"]: point for point in payload["series"]}
    gap_point = by_time["2026-06-16T02:05:00"]

    assert gap_point["grid_power_kw"] is None
    assert gap_point["quality"]["grid_power_kw"] == "gap"


def test_display_series_clamps_soc_interpolation_to_ratio_bounds():
    session = create_sqlite_memory_session()
    _add_battery(
        session,
        [
            ("2026-06-16T10:00:00+08:00", 0.0, 0.95),
            ("2026-06-16T10:02:00+08:00", 0.0, 1.20),
            ("2026-06-16T10:04:00+08:00", 0.0, 1.10),
        ],
    )
    session.commit()

    payload = build_display_series_payload(
        session,
        plant_id="hehong_huajin",
        reference_time=parse_timestamp("2026-06-16T10:04:00+08:00"),
        window_hours=2,
        max_gap_minutes=5,
    )

    by_time = {point["time"]: point for point in payload["series"]}
    point = by_time["2026-06-16T02:01:00"]

    assert point["soc"] == 1.0
    assert point["quality"]["soc"] == "interpolated_quadratic"


def test_display_series_buckets_raw_samples_by_minute_and_keeps_latest_sample():
    session = create_sqlite_memory_session()
    _add_grid(
        session,
        [
            ("2026-06-16T10:00:05+08:00", 100.0),
            ("2026-06-16T10:00:55+08:00", 123.0),
            ("2026-06-16T10:01:05+08:00", 140.0),
            ("2026-06-16T10:02:05+08:00", 160.0),
        ],
    )
    session.commit()

    payload = build_display_series_payload(
        session,
        plant_id="hehong_huajin",
        reference_time=parse_timestamp("2026-06-16T10:02:30+08:00"),
        window_hours=2,
    )

    by_time = {point["time"]: point for point in payload["series"]}
    point = by_time["2026-06-16T02:00:00"]

    assert point["grid_power_kw"] == 123.0
    assert point["quality"]["grid_power_kw"] == "observed"
