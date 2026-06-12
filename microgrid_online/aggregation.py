from __future__ import annotations

from datetime import timedelta
from statistics import mean

from sqlalchemy import select
from sqlalchemy.orm import Session

from microgrid_online.models import RawBattery, RawGridMeter, Telemetry15Min
from microgrid_online.time_utils import parse_timestamp


def _window_quality(grid_count: int, battery_count: int) -> str:
    missing = []
    if grid_count == 0:
        missing.append("grid")
    if battery_count == 0:
        missing.append("battery")
    if missing:
        return "missing_" + "_".join(missing)
    return "ok"


def _upsert_telemetry_window(
    session: Session,
    *,
    plant_id: str,
    start_time,
    end_time,
    values: dict,
) -> Telemetry15Min:
    existing = session.scalar(
        select(Telemetry15Min).where(
            Telemetry15Min.plant_id == plant_id,
            Telemetry15Min.start_time == start_time,
            Telemetry15Min.end_time == end_time,
        )
    )
    if existing:
        for key, value in values.items():
            setattr(existing, key, value)
        return existing

    row = Telemetry15Min(
        plant_id=plant_id,
        start_time=start_time,
        end_time=end_time,
        **values,
    )
    session.add(row)
    return row


def aggregate_telemetry_15min(
    session: Session,
    *,
    plant_id: str,
    start_time,
    end_time,
    window_minutes: int = 15,
) -> list[Telemetry15Min]:
    start = parse_timestamp(start_time)
    end = parse_timestamp(end_time)
    if end <= start:
        raise ValueError("end_time must be after start_time")
    if window_minutes <= 0:
        raise ValueError("window_minutes must be positive")

    rows: list[Telemetry15Min] = []
    step = timedelta(minutes=window_minutes)
    window_start = start
    while window_start < end:
        window_end = min(window_start + step, end)
        grid_rows = session.scalars(
            select(RawGridMeter)
            .where(
                RawGridMeter.plant_id == plant_id,
                RawGridMeter.time >= window_start,
                RawGridMeter.time < window_end,
            )
            .order_by(RawGridMeter.time)
        ).all()
        battery_rows = session.scalars(
            select(RawBattery)
            .where(
                RawBattery.plant_id == plant_id,
                RawBattery.time >= window_start,
                RawBattery.time < window_end,
            )
            .order_by(RawBattery.time)
        ).all()

        grid_values = [row.grid_power_kw for row in grid_rows]
        battery_values = [row.battery_power_kw for row in battery_rows]
        grid_avg = mean(grid_values) if grid_values else None
        grid_max = max(grid_values) if grid_values else None
        battery_avg = mean(battery_values) if battery_values else None
        load_minus_pv = grid_avg + battery_avg if grid_avg is not None and battery_avg is not None else None
        values = {
            "grid_power_kw_avg": grid_avg,
            "grid_power_kw_max": grid_max,
            "battery_power_kw_avg": battery_avg,
            "load_minus_pv_kw_avg": load_minus_pv,
            "soc_start": battery_rows[0].soc if battery_rows else None,
            "soc_end": battery_rows[-1].soc if battery_rows else None,
            "grid_sample_count": len(grid_rows),
            "battery_sample_count": len(battery_rows),
            "quality_flag": _window_quality(len(grid_rows), len(battery_rows)),
        }
        rows.append(
            _upsert_telemetry_window(
                session,
                plant_id=plant_id,
                start_time=window_start,
                end_time=window_end,
                values=values,
            )
        )
        window_start = window_end

    session.commit()
    return rows
