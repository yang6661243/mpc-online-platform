from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from api.database.orm import RawTelemetry, Telemetry15Min
from api.utils.time import parse_timestamp


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _age_minutes(reference_time: datetime, sample_time: datetime) -> float:
    return (reference_time - sample_time).total_seconds() / 60.0


def _latest_raw(session: Session, plant_id: str) -> RawTelemetry | None:
    return session.scalar(
        select(RawTelemetry)
        .where(RawTelemetry.plant_id == plant_id)
        .order_by(RawTelemetry.time.desc())
        .limit(1)
    )


def _latest_telemetry(session: Session, plant_id: str) -> Telemetry15Min | None:
    return session.scalar(
        select(Telemetry15Min)
        .where(Telemetry15Min.plant_id == plant_id)
        .order_by(Telemetry15Min.end_time.desc())
        .limit(1)
    )


def _raw_payload(row: RawTelemetry | None, reference_time: datetime) -> dict | None:
    if row is None:
        return None
    return {
        "time": row.time.isoformat(),
        "age_minutes": _age_minutes(reference_time, row.time),
        "grid_power_kw": row.grid_power_kw,
        "battery_power_kw": row.battery_power_kw,
        "soc": row.soc,
        "buy_price": row.buy_price,
    }


def _telemetry_payload(row: Telemetry15Min | None, reference_time: datetime) -> dict | None:
    if row is None:
        return None
    return {
        "start_time": row.start_time.isoformat(),
        "end_time": row.end_time.isoformat(),
        "age_minutes": _age_minutes(reference_time, row.end_time),
        "grid_power_kw_avg": row.grid_power_kw_avg,
        "grid_power_kw_max": row.grid_power_kw_max,
        "battery_power_kw_avg": row.battery_power_kw_avg,
        "load_minus_pv_kw_avg": row.load_minus_pv_kw_avg,
        "soc_start": row.soc_start,
        "soc_end": row.soc_end,
        "grid_sample_count": row.grid_sample_count,
        "battery_sample_count": row.battery_sample_count,
        "quality_flag": row.quality_flag,
    }


def build_data_health_payload(
    session: Session,
    *,
    plant_id: str,
    reference_time: str | datetime | None = None,
    max_raw_delay_minutes: int = 30,
    max_telemetry_delay_minutes: int = 30,
) -> dict:
    if max_raw_delay_minutes <= 0:
        raise ValueError("max_raw_delay_minutes must be positive")
    if max_telemetry_delay_minutes <= 0:
        raise ValueError("max_telemetry_delay_minutes must be positive")

    reference = _utc_now_naive() if reference_time is None else parse_timestamp(reference_time)
    raw = _latest_raw(session, plant_id)
    telemetry = _latest_telemetry(session, plant_id)

    issues: list[str] = []
    if raw is None:
        issues.append("missing_raw")
    else:
        age = _age_minutes(reference, raw.time)
        if raw.grid_power_kw is None:
            issues.append("missing_grid_raw")
        elif age > max_raw_delay_minutes:
            issues.append("stale_grid_raw")
        if raw.battery_power_kw is None or raw.soc is None:
            issues.append("missing_battery_raw")
        elif age > max_raw_delay_minutes:
            issues.append("stale_battery_raw")

    if telemetry is None:
        issues.append("missing_telemetry")
    else:
        if _age_minutes(reference, telemetry.end_time) > max_telemetry_delay_minutes:
            issues.append("stale_telemetry")
        if telemetry.quality_flag != "ok":
            issues.append(f"latest_telemetry_quality_{telemetry.quality_flag}")
        if telemetry.grid_power_kw_avg is None:
            issues.append("latest_telemetry_missing_grid_power")
        if telemetry.battery_power_kw_avg is None:
            issues.append("latest_telemetry_missing_battery_power")
        if telemetry.soc_end is None:
            issues.append("latest_telemetry_missing_soc")

    return {
        "plant_id": plant_id,
        "reference_time": reference.isoformat(),
        "ready_for_mpc": len(issues) == 0,
        "issues": issues,
        "thresholds": {
            "max_raw_delay_minutes": max_raw_delay_minutes,
            "max_telemetry_delay_minutes": max_telemetry_delay_minutes,
        },
        "latest_raw": _raw_payload(raw, reference),
        "latest_telemetry": _telemetry_payload(telemetry, reference),
    }
