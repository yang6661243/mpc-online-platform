from datetime import datetime
import sqlite3

from server.api.database import create_session_factory
from server.api.database.maintenance import (
    DEFAULT_RETENTION_POLICY,
    MaintenanceOptions,
    backup_sqlite_database,
    maintenance_options_from_env,
    run_database_maintenance,
)
from server.api.database.orm import (
    RawBattery,
    RawGridMeter,
    StrategyComparison,
    StrategyCurvePoint,
    Telemetry15Min,
)


def _session_for_file(tmp_path):
    db_path = tmp_path / "mpc_online.db"
    session_factory = create_session_factory(f"sqlite:///{db_path}")
    return db_path, session_factory()


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def test_backup_sqlite_database_creates_readable_snapshot(tmp_path):
    db_path, session = _session_for_file(tmp_path)
    session.add(RawGridMeter(plant_id="factory", time=_dt("2026-06-14T00:00:00"), grid_power_kw=10.0))
    session.commit()
    session.close()

    backup_path = backup_sqlite_database(db_path, tmp_path / "backups", timestamp=_dt("2026-06-14T01:02:03"))

    assert backup_path.exists()
    assert backup_path.name == "mpc_online-20260614T010203.db"
    backup_conn = sqlite3.connect(backup_path)
    try:
        assert backup_conn.execute("select count(*) from raw_grid_meter").fetchone()[0] == 1
    finally:
        backup_conn.close()


def test_maintenance_dry_run_reports_expired_rows_without_deleting(tmp_path):
    db_path, session = _session_for_file(tmp_path)
    session.add(RawGridMeter(plant_id="factory", time=_dt("2026-05-01T00:00:00"), grid_power_kw=10.0))
    session.add(RawGridMeter(plant_id="factory", time=_dt("2026-06-13T00:00:00"), grid_power_kw=20.0))
    session.add(RawBattery(plant_id="factory", time=_dt("2026-05-01T00:00:00"), battery_power_kw=1.0, soc=0.5))
    session.add(RawBattery(plant_id="factory", time=_dt("2026-06-13T00:00:00"), battery_power_kw=2.0, soc=0.6))
    session.commit()
    session.close()

    result = run_database_maintenance(
        database_url=f"sqlite:///{db_path}",
        options=MaintenanceOptions(
            reference_time=_dt("2026-06-14T00:00:00"),
            backup_dir=tmp_path / "backups",
            execute=False,
        ),
    )

    assert result.executed is False
    assert result.backup_path is None
    assert result.deleted_counts["raw_grid_meter"] == 1
    assert result.deleted_counts["raw_battery"] == 1

    check = create_session_factory(f"sqlite:///{db_path}")()
    try:
        assert check.query(RawGridMeter).count() == 2
        assert check.query(RawBattery).count() == 2
    finally:
        check.close()


def test_maintenance_execute_backs_up_then_deletes_only_expired_rows(tmp_path):
    db_path, session = _session_for_file(tmp_path)
    session.add(RawGridMeter(plant_id="factory", time=_dt("2026-05-01T00:00:00"), grid_power_kw=10.0))
    session.add(RawGridMeter(plant_id="factory", time=_dt("2026-06-13T00:00:00"), grid_power_kw=20.0))
    session.add(RawBattery(plant_id="factory", time=_dt("2026-05-01T00:00:00"), battery_power_kw=1.0, soc=0.5))
    session.add(RawBattery(plant_id="factory", time=_dt("2026-06-13T00:00:00"), battery_power_kw=2.0, soc=0.6))
    session.add(
        Telemetry15Min(
            plant_id="factory",
            start_time=_dt("2025-01-01T00:00:00"),
            end_time=_dt("2025-01-01T00:15:00"),
            quality_flag="ok",
        )
    )
    session.add(
        Telemetry15Min(
            plant_id="factory",
            start_time=_dt("2026-06-13T00:00:00"),
            end_time=_dt("2026-06-13T00:15:00"),
            quality_flag="ok",
        )
    )
    session.add(StrategyComparison(run_id="old", plant_id="factory", created_at=_dt("2025-01-01T00:00:00")))
    session.add(StrategyComparison(run_id="new", plant_id="factory", created_at=_dt("2026-06-13T00:00:00")))
    session.add(StrategyCurvePoint(run_id="old", plant_id="factory", time=_dt("2025-01-01T00:00:00")))
    session.add(StrategyCurvePoint(run_id="new", plant_id="factory", time=_dt("2026-06-13T00:00:00")))
    session.commit()
    session.close()

    result = run_database_maintenance(
        database_url=f"sqlite:///{db_path}",
        options=MaintenanceOptions(
            reference_time=_dt("2026-06-14T00:00:00"),
            backup_dir=tmp_path / "backups",
            execute=True,
            retention=DEFAULT_RETENTION_POLICY,
        ),
    )

    assert result.executed is True
    assert result.backup_path is not None
    assert result.backup_path.exists()
    assert result.deleted_counts["raw_grid_meter"] == 1
    assert result.deleted_counts["raw_battery"] == 1
    assert result.deleted_counts["telemetry_15min"] == 1
    assert result.deleted_counts["strategy_comparison"] == 1
    assert result.deleted_counts["strategy_curve_points"] == 1

    check = create_session_factory(f"sqlite:///{db_path}")()
    try:
        assert [row.grid_power_kw for row in check.query(RawGridMeter).all()] == [20.0]
        assert [row.battery_power_kw for row in check.query(RawBattery).all()] == [2.0]
        assert check.query(Telemetry15Min).one().end_time == _dt("2026-06-13T00:15:00")
        assert check.query(StrategyComparison).one().run_id == "new"
        assert check.query(StrategyCurvePoint).one().run_id == "new"
    finally:
        check.close()


def test_maintenance_options_from_env_uses_configured_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("MPC_DB_BACKUP_DIR", str(tmp_path / "configured_backups"))
    monkeypatch.setenv("MPC_RAW_RETENTION_DAYS", "31")
    monkeypatch.setenv("MPC_TELEMETRY_RETENTION_DAYS", "366")
    monkeypatch.setenv("MPC_RESULT_RETENTION_DAYS", "367")
    monkeypatch.setenv("MPC_CONTROL_API_RETENTION_DAYS", "368")
    monkeypatch.setenv("MPC_BACKUP_RETENTION_DAYS", "32")

    options = maintenance_options_from_env(reference_time=_dt("2026-06-14T00:00:00"), execute=True)

    assert options.reference_time == _dt("2026-06-14T00:00:00")
    assert options.execute is True
    assert options.backup_dir == tmp_path / "configured_backups"
    assert options.retention.raw_days == 31
    assert options.retention.telemetry_days == 366
    assert options.retention.mpc_result_days == 367
    assert options.retention.control_api_days == 368
    assert options.retention.backup_days == 32
