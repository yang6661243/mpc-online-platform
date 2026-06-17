from server.api.services.aggregation import aggregate_telemetry_15min
from server.api.database import create_sqlite_memory_session
from server.api.services.ingestion import ingest_battery_records, ingest_grid_records
from server.api.database.orm import Telemetry15Min


def test_aggregate_15min_derives_load_minus_pv_from_grid_and_battery_power():
    session = create_sqlite_memory_session()
    ingest_grid_records(
        session,
        plant_id="aodelai",
        records=[
            {"time": "2026-06-12T10:00:00+08:00", "grid_power_kw": 400.0},
            {"time": "2026-06-12T10:05:00+08:00", "grid_power_kw": 420.0},
            {"time": "2026-06-12T10:10:00+08:00", "grid_power_kw": 440.0},
        ],
    )
    ingest_battery_records(
        session,
        plant_id="aodelai",
        records=[
            {"time": "2026-06-12T10:00:00+08:00", "battery_power_kw": 20.0, "soc": 0.60},
            {"time": "2026-06-12T10:05:00+08:00", "battery_power_kw": -10.0, "soc": 0.58},
            {"time": "2026-06-12T10:10:00+08:00", "battery_power_kw": 5.0, "soc": 0.57},
        ],
    )

    aggregates = aggregate_telemetry_15min(
        session,
        plant_id="aodelai",
        start_time="2026-06-12T10:00:00+08:00",
        end_time="2026-06-12T10:15:00+08:00",
    )

    assert len(aggregates) == 1
    row = aggregates[0]
    assert row.grid_power_kw_avg == 420.0
    assert row.battery_power_kw_avg == 5.0
    assert row.load_minus_pv_kw_avg == 425.0
    assert row.soc_start == 0.60
    assert row.soc_end == 0.57
    assert row.quality_flag == "ok"

    persisted = session.query(Telemetry15Min).one()
    assert persisted.load_minus_pv_kw_avg == 425.0


def test_aggregate_15min_can_infer_battery_sign_from_soc_delta():
    session = create_sqlite_memory_session()
    ingest_grid_records(
        session,
        plant_id="ecloud_factory",
        records=[
            {"time": "2026-06-12T10:00:00+08:00", "grid_power_kw": 400.0},
            {"time": "2026-06-12T10:05:00+08:00", "grid_power_kw": 420.0},
        ],
    )
    ingest_battery_records(
        session,
        plant_id="ecloud_factory",
        records=[
            {"time": "2026-06-12T10:00:00+08:00", "battery_power_kw": 20.0, "soc": 0.60},
            {"time": "2026-06-12T10:05:00+08:00", "battery_power_kw": 30.0, "soc": 0.62},
        ],
    )

    aggregates = aggregate_telemetry_15min(
        session,
        plant_id="ecloud_factory",
        start_time="2026-06-12T10:00:00+08:00",
        end_time="2026-06-12T10:15:00+08:00",
        battery_power_mode="soc_delta",
    )

    row = aggregates[0]
    assert round(row.grid_power_kw_avg, 6) == round((400.0 * 5 + 420.0 * 10) / 15, 6)
    assert round(row.battery_power_kw_avg, 6) == round(-(20.0 * 5 + 30.0 * 10) / 15, 6)
    assert round(row.load_minus_pv_kw_avg, 6) == round(row.grid_power_kw_avg + row.battery_power_kw_avg, 6)
    assert row.soc_start == 0.60
    assert row.soc_end == 0.62


def test_aggregate_marks_missing_battery_window_as_incomplete():
    session = create_sqlite_memory_session()
    ingest_grid_records(
        session,
        plant_id="aodelai",
        records=[
            {"time": "2026-06-12T10:00:00+08:00", "grid_power_kw": 400.0},
            {"time": "2026-06-12T10:05:00+08:00", "grid_power_kw": 420.0},
        ],
    )

    aggregates = aggregate_telemetry_15min(
        session,
        plant_id="aodelai",
        start_time="2026-06-12T10:00:00+08:00",
        end_time="2026-06-12T10:15:00+08:00",
    )

    assert len(aggregates) == 1
    assert aggregates[0].quality_flag == "missing_battery"
    assert aggregates[0].load_minus_pv_kw_avg is None


def test_aggregate_resamples_uneven_samples_before_averaging():
    session = create_sqlite_memory_session()
    ingest_grid_records(
        session,
        plant_id="hehong_huajin",
        records=[
            {"time": "2026-06-12T10:00:00+08:00", "grid_power_kw": 100.0},
            {"time": "2026-06-12T10:14:00+08:00", "grid_power_kw": 200.0},
        ],
    )
    ingest_battery_records(
        session,
        plant_id="hehong_huajin",
        records=[
            {"time": "2026-06-12T10:00:00+08:00", "battery_power_kw": 10.0, "soc": 0.50},
            {"time": "2026-06-12T10:05:00+08:00", "battery_power_kw": 20.0, "soc": 0.51},
            {"time": "2026-06-12T10:10:00+08:00", "battery_power_kw": 30.0, "soc": 0.52},
        ],
    )

    aggregates = aggregate_telemetry_15min(
        session,
        plant_id="hehong_huajin",
        start_time="2026-06-12T10:00:00+08:00",
        end_time="2026-06-12T10:15:00+08:00",
        max_staleness_minutes=15,
    )

    row = aggregates[0]
    assert round(row.grid_power_kw_avg, 6) == round((100.0 * 14 + 200.0) / 15, 6)
    assert row.battery_power_kw_avg == 20.0
    assert round(row.load_minus_pv_kw_avg, 6) == round(row.grid_power_kw_avg + 20.0, 6)
    assert row.soc_start == 0.50
    assert row.soc_end == 0.52
    assert row.grid_sample_count == 2
    assert row.battery_sample_count == 3
    assert row.quality_flag == "ok"


def test_aggregate_marks_stale_resampled_values_as_partial():
    session = create_sqlite_memory_session()
    ingest_grid_records(
        session,
        plant_id="hehong_huajin",
        records=[
            {"time": "2026-06-12T10:00:00+08:00", "grid_power_kw": 100.0},
        ],
    )
    ingest_battery_records(
        session,
        plant_id="hehong_huajin",
        records=[
            {"time": "2026-06-12T10:00:00+08:00", "battery_power_kw": 10.0, "soc": 0.50},
        ],
    )

    aggregates = aggregate_telemetry_15min(
        session,
        plant_id="hehong_huajin",
        start_time="2026-06-12T10:00:00+08:00",
        end_time="2026-06-12T10:15:00+08:00",
        max_staleness_minutes=3,
    )

    row = aggregates[0]
    assert row.grid_power_kw_avg == 100.0
    assert row.battery_power_kw_avg == 10.0
    assert row.quality_flag == "partial_grid_battery"
