from datetime import datetime

import pytest
from sqlalchemy import inspect

from server.api.database import create_sqlite_memory_session
from server.api.services.ingestion import ingest_battery_records, ingest_grid_records
from server.api.database.orm import RawBattery, RawGridMeter


def test_online_database_creates_required_tables():
    session = create_sqlite_memory_session()

    table_names = set(inspect(session.bind).get_table_names())

    assert {
        "raw_grid_meter",
        "raw_battery",
        "telemetry_15min",
        "mpc_runs",
        "mpc_targets",
        "strategy_comparison",
        "control_api_outbox",
        "control_api_ack",
    }.issubset(table_names)


def test_ingest_grid_and_battery_records_persist_rows():
    session = create_sqlite_memory_session()

    grid_count = ingest_grid_records(
        session,
        plant_id="aodelai",
        records=[
            {"time": "2026-06-12T10:00:00+08:00", "grid_power_kw": 410.0},
            {"time": "2026-06-12T10:05:00+08:00", "grid_power_kw": 430.0},
        ],
    )
    battery_count = ingest_battery_records(
        session,
        plant_id="aodelai",
        records=[
            {"time": "2026-06-12T10:00:00+08:00", "battery_power_kw": 20.0, "soc": 0.60},
            {"time": "2026-06-12T10:05:00+08:00", "battery_power_kw": -10.0, "soc": 0.58},
        ],
    )

    assert grid_count == 2
    assert battery_count == 2
    assert session.query(RawGridMeter).count() == 2
    assert session.query(RawBattery).count() == 2

    first_grid = session.query(RawGridMeter).order_by(RawGridMeter.time).first()
    assert first_grid.plant_id == "aodelai"
    assert first_grid.grid_power_kw == 410.0
    assert isinstance(first_grid.time, datetime)


def test_ingestion_rejects_invalid_soc():
    session = create_sqlite_memory_session()

    with pytest.raises(ValueError, match="soc must be between 0 and 1"):
        ingest_battery_records(
            session,
            plant_id="aodelai",
            records=[
                {"time": "2026-06-12T10:00:00+08:00", "battery_power_kw": 20.0, "soc": 60},
            ],
        )


def test_ingestion_is_idempotent_by_plant_and_time():
    session = create_sqlite_memory_session()

    ingest_grid_records(
        session,
        plant_id="aodelai",
        records=[{"time": "2026-06-12T10:00:00+08:00", "grid_power_kw": 410.0}],
    )
    ingest_grid_records(
        session,
        plant_id="aodelai",
        records=[{"time": "2026-06-12T10:00:00+08:00", "grid_power_kw": 411.0}],
    )

    rows = session.query(RawGridMeter).all()
    assert len(rows) == 1
    assert rows[0].grid_power_kw == 411.0
