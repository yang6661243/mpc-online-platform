from microgrid_online.aggregation import aggregate_telemetry_15min
from microgrid_online.database import create_sqlite_memory_session
from microgrid_online.ingestion import ingest_battery_records, ingest_grid_records
from microgrid_online.models import Telemetry15Min


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
