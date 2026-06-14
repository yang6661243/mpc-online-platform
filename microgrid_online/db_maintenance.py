from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from microgrid_online.database import create_session_factory, resolve_database_url
from microgrid_online.models import (
    ControlApiAck,
    ControlApiOutbox,
    MpcRun,
    MpcTarget,
    RawBattery,
    RawGridMeter,
    StrategyComparison,
    StrategyCurvePoint,
    Telemetry15Min,
)
from microgrid_online.time_utils import parse_timestamp


@dataclass(frozen=True)
class RetentionPolicy:
    raw_days: int = 30
    telemetry_days: int = 365
    mpc_result_days: int = 365
    control_api_days: int = 365
    backup_days: int = 30


DEFAULT_RETENTION_POLICY = RetentionPolicy()


@dataclass(frozen=True)
class MaintenanceOptions:
    reference_time: datetime | str | None = None
    backup_dir: str | Path = "data/backups"
    execute: bool = False
    retention: RetentionPolicy = DEFAULT_RETENTION_POLICY


@dataclass(frozen=True)
class MaintenanceResult:
    executed: bool
    reference_time: datetime
    backup_path: Path | None
    deleted_counts: dict[str, int] = field(default_factory=dict)
    pruned_backup_count: int = 0


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def sqlite_path_from_url(database_url: str | None = None) -> Path:
    resolved = resolve_database_url(database_url)
    if resolved in {"sqlite://", "sqlite+pysqlite:///:memory:"} or ":memory:" in resolved:
        raise ValueError("database maintenance requires a file-backed SQLite database")
    if not resolved.startswith("sqlite:///"):
        raise ValueError("database maintenance currently supports sqlite:/// URLs only")
    return Path(resolved.removeprefix("sqlite:///"))


def backup_sqlite_database(
    database_path: str | Path,
    backup_dir: str | Path,
    *,
    timestamp: datetime | str | None = None,
) -> Path:
    source = Path(database_path)
    if not source.exists():
        raise FileNotFoundError(source)

    ts = _utc_now_naive() if timestamp is None else parse_timestamp(timestamp)
    destination_dir = Path(backup_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{source.stem}-{ts.strftime('%Y%m%dT%H%M%S')}{source.suffix}"

    source_conn = sqlite3.connect(source)
    try:
        dest_conn = sqlite3.connect(destination)
        try:
            source_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        source_conn.close()
    return destination


def _count(session: Session, model, predicate) -> int:
    return int(session.scalar(select(func.count()).select_from(model).where(predicate)) or 0)


def _delete(session: Session, model, predicate) -> int:
    result = session.execute(delete(model).where(predicate))
    return int(result.rowcount or 0)


def _expired_specs(reference: datetime, retention: RetentionPolicy):
    raw_cutoff = reference - timedelta(days=retention.raw_days)
    telemetry_cutoff = reference - timedelta(days=retention.telemetry_days)
    mpc_cutoff = reference - timedelta(days=retention.mpc_result_days)
    control_cutoff = reference - timedelta(days=retention.control_api_days)
    return {
        "raw_grid_meter": (RawGridMeter, RawGridMeter.time < raw_cutoff),
        "raw_battery": (RawBattery, RawBattery.time < raw_cutoff),
        "telemetry_15min": (Telemetry15Min, Telemetry15Min.end_time < telemetry_cutoff),
        "mpc_runs": (
            MpcRun,
            or_(MpcRun.finished_at < mpc_cutoff, MpcRun.input_end_time < mpc_cutoff),
        ),
        "mpc_targets": (MpcTarget, MpcTarget.valid_until < mpc_cutoff),
        "strategy_comparison": (StrategyComparison, StrategyComparison.created_at < mpc_cutoff),
        "strategy_curve_points": (StrategyCurvePoint, StrategyCurvePoint.time < mpc_cutoff),
        "control_api_outbox": (ControlApiOutbox, ControlApiOutbox.created_at < control_cutoff),
        "control_api_ack": (ControlApiAck, ControlApiAck.received_at < control_cutoff),
    }


def _prune_old_backups(backup_dir: Path, reference: datetime, retention_days: int, *, execute: bool) -> int:
    cutoff = reference - timedelta(days=retention_days)
    count = 0
    if not backup_dir.exists():
        return 0
    for path in backup_dir.glob("*.db"):
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        if mtime >= cutoff:
            continue
        count += 1
        if execute:
            path.unlink()
    return count


def run_database_maintenance(
    *,
    database_url: str | None = None,
    options: MaintenanceOptions | None = None,
) -> MaintenanceResult:
    opts = options or MaintenanceOptions()
    reference = _utc_now_naive() if opts.reference_time is None else parse_timestamp(opts.reference_time)
    session_factory = create_session_factory(database_url)
    session = session_factory()
    try:
        specs = _expired_specs(reference, opts.retention)
        deleted_counts = {
            table_name: _count(session, model, predicate)
            for table_name, (model, predicate) in specs.items()
        }
        backup_path = None
        if opts.execute:
            backup_path = backup_sqlite_database(
                sqlite_path_from_url(database_url),
                opts.backup_dir,
                timestamp=reference,
            )
            deleted_counts = {
                table_name: _delete(session, model, predicate)
                for table_name, (model, predicate) in specs.items()
            }
            session.commit()
        pruned_backup_count = _prune_old_backups(
            Path(opts.backup_dir),
            reference,
            opts.retention.backup_days,
            execute=opts.execute,
        )
        return MaintenanceResult(
            executed=opts.execute,
            reference_time=reference,
            backup_path=backup_path,
            deleted_counts=deleted_counts,
            pruned_backup_count=pruned_backup_count,
        )
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _env_positive_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw in {None, ""}:
        return default
    return _positive_int(raw)


def maintenance_options_from_env(
    *,
    reference_time: datetime | str | None = None,
    execute: bool = False,
) -> MaintenanceOptions:
    return MaintenanceOptions(
        reference_time=reference_time,
        backup_dir=Path(os.getenv("MPC_DB_BACKUP_DIR", "data/backups")),
        execute=execute,
        retention=RetentionPolicy(
            raw_days=_env_positive_int("MPC_RAW_RETENTION_DAYS", DEFAULT_RETENTION_POLICY.raw_days),
            telemetry_days=_env_positive_int(
                "MPC_TELEMETRY_RETENTION_DAYS",
                DEFAULT_RETENTION_POLICY.telemetry_days,
            ),
            mpc_result_days=_env_positive_int(
                "MPC_RESULT_RETENTION_DAYS",
                DEFAULT_RETENTION_POLICY.mpc_result_days,
            ),
            control_api_days=_env_positive_int(
                "MPC_CONTROL_API_RETENTION_DAYS",
                DEFAULT_RETENTION_POLICY.control_api_days,
            ),
            backup_days=_env_positive_int(
                "MPC_BACKUP_RETENTION_DAYS",
                DEFAULT_RETENTION_POLICY.backup_days,
            ),
        ),
    )


def main(argv: list[str] | None = None) -> int:
    env_options = maintenance_options_from_env()
    parser = argparse.ArgumentParser(description="Back up and prune the online MPC SQLite database.")
    parser.add_argument("--database-url", default=None, help="SQLite database URL. Defaults to MPC_DATABASE_URL.")
    parser.add_argument("--backup-dir", default=str(env_options.backup_dir), help="Directory for SQLite backup snapshots.")
    parser.add_argument("--reference-time", default=None, help="ISO timestamp for retention cutoff calculation.")
    parser.add_argument("--raw-days", type=_positive_int, default=env_options.retention.raw_days)
    parser.add_argument("--telemetry-days", type=_positive_int, default=env_options.retention.telemetry_days)
    parser.add_argument("--mpc-result-days", type=_positive_int, default=env_options.retention.mpc_result_days)
    parser.add_argument("--control-api-days", type=_positive_int, default=env_options.retention.control_api_days)
    parser.add_argument("--backup-days", type=_positive_int, default=env_options.retention.backup_days)
    parser.add_argument("--execute", action="store_true", help="Actually create backup and delete expired rows.")
    args = parser.parse_args(argv)

    result = run_database_maintenance(
        database_url=args.database_url,
        options=MaintenanceOptions(
            reference_time=args.reference_time,
            backup_dir=args.backup_dir,
            execute=args.execute,
            retention=RetentionPolicy(
                raw_days=args.raw_days,
                telemetry_days=args.telemetry_days,
                mpc_result_days=args.mpc_result_days,
                control_api_days=args.control_api_days,
                backup_days=args.backup_days,
            ),
        ),
    )
    mode = "EXECUTE" if result.executed else "DRY_RUN"
    print(f"mode={mode}")
    print(f"reference_time={result.reference_time.isoformat()}")
    if result.backup_path is not None:
        print(f"backup_path={result.backup_path}")
    for table_name, count in sorted(result.deleted_counts.items()):
        print(f"expired_rows.{table_name}={count}")
    print(f"expired_backups={result.pruned_backup_count}")
    if not result.executed:
        print("dry_run=true; pass --execute to create a backup and delete expired rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
