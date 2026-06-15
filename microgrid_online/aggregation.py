from __future__ import annotations

from datetime import timedelta
from statistics import mean
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from microgrid_online.models import RawBattery, RawGridMeter, Telemetry15Min
from microgrid_online.time_utils import parse_timestamp


class _TimedValue(Protocol):
    time: object


def _window_quality(grid_count: int, battery_count: int, expected_count: int) -> str:
    missing = []
    if grid_count == 0:
        missing.append("grid")
    if battery_count == 0:
        missing.append("battery")
    if missing:
        return "missing_" + "_".join(missing)

    partial = []
    if grid_count < expected_count:
        partial.append("grid")
    if battery_count < expected_count:
        partial.append("battery")
    if partial:
        return "partial_" + "_".join(partial)

    return "ok"


def _resample_points(start, end, step: timedelta) -> list:
    points = []
    cursor = start
    while cursor < end:
        points.append(cursor)
        cursor += step
    return points


def _resample_series(
    rows: list[_TimedValue],
    *,
    points: list,
    value_name: str,
    max_staleness: timedelta,
) -> list[float | None]:
    values: list[float | None] = []
    index = 0
    latest = None
    for point in points:
        while index < len(rows) and rows[index].time <= point:
            latest = rows[index]
            index += 1

        if latest is None or point - latest.time > max_staleness:
            values.append(None)
            continue

        value = getattr(latest, value_name)
        values.append(float(value) if value is not None else None)
    return values


def _mean_available(values: list[float | None]) -> float | None:
    available = [value for value in values if value is not None]
    return mean(available) if available else None


def _battery_power_average_from_series(
    battery_power_values: list[float | None],
    soc_values: list[float | None],
    *,
    battery_power_mode: str,
    soc_deadband: float = 0.001,
) -> float | None:
    available_power = [value for value in battery_power_values if value is not None]
    if not available_power:
        return None

    if battery_power_mode == "signed_meter":
        return mean(available_power)

    if battery_power_mode != "soc_delta":
        raise ValueError("battery_power_mode must be signed_meter or soc_delta")

    available_soc = [value for value in soc_values if value is not None]
    if len(available_soc) < 2:
        return None

    soc_delta = available_soc[-1] - available_soc[0]
    if abs(soc_delta) < soc_deadband:
        return 0.0

    magnitude = mean(abs(value) for value in available_power)
    # System convention: positive battery power means discharge, negative means charge.
    return -magnitude if soc_delta > 0 else magnitude


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
    battery_power_mode: str = "signed_meter",
    resample_minutes: int = 1,
    max_staleness_minutes: int = 10,
) -> list[Telemetry15Min]:
    start = parse_timestamp(start_time)
    end = parse_timestamp(end_time)
    if end <= start:
        raise ValueError("end_time must be after start_time")
    if window_minutes <= 0:
        raise ValueError("window_minutes must be positive")
    if resample_minutes <= 0:
        raise ValueError("resample_minutes must be positive")
    if max_staleness_minutes <= 0:
        raise ValueError("max_staleness_minutes must be positive")

    rows: list[Telemetry15Min] = []
    step = timedelta(minutes=window_minutes)
    resample_step = timedelta(minutes=resample_minutes)
    max_staleness = timedelta(minutes=max_staleness_minutes)
    window_start = start
    while window_start < end:
        window_end = min(window_start + step, end)
        lookback_start = window_start - max_staleness
        grid_rows = session.scalars(
            select(RawGridMeter)
            .where(
                RawGridMeter.plant_id == plant_id,
                RawGridMeter.time >= lookback_start,
                RawGridMeter.time < window_end,
            )
            .order_by(RawGridMeter.time)
        ).all()
        battery_rows = session.scalars(
            select(RawBattery)
            .where(
                RawBattery.plant_id == plant_id,
                RawBattery.time >= lookback_start,
                RawBattery.time < window_end,
            )
            .order_by(RawBattery.time)
        ).all()

        points = _resample_points(window_start, window_end, resample_step)
        grid_values = _resample_series(
            grid_rows,
            points=points,
            value_name="grid_power_kw",
            max_staleness=max_staleness,
        )
        battery_values = _resample_series(
            battery_rows,
            points=points,
            value_name="battery_power_kw",
            max_staleness=max_staleness,
        )
        soc_values = _resample_series(
            battery_rows,
            points=points,
            value_name="soc",
            max_staleness=max_staleness,
        )

        available_grid_values = [value for value in grid_values if value is not None]
        available_soc_values = [value for value in soc_values if value is not None]
        grid_avg = _mean_available(grid_values)
        grid_max = max(available_grid_values) if available_grid_values else None
        battery_avg = _battery_power_average_from_series(
            battery_values,
            soc_values,
            battery_power_mode=battery_power_mode,
        )
        load_minus_pv = grid_avg + battery_avg if grid_avg is not None and battery_avg is not None else None
        raw_grid_count = sum(1 for row in grid_rows if window_start <= row.time < window_end)
        raw_battery_count = sum(1 for row in battery_rows if window_start <= row.time < window_end)
        values = {
            "grid_power_kw_avg": grid_avg,
            "grid_power_kw_max": grid_max,
            "battery_power_kw_avg": battery_avg,
            "load_minus_pv_kw_avg": load_minus_pv,
            "soc_start": available_soc_values[0] if available_soc_values else None,
            "soc_end": available_soc_values[-1] if available_soc_values else None,
            "grid_sample_count": raw_grid_count,
            "battery_sample_count": raw_battery_count,
            "quality_flag": _window_quality(
                len(available_grid_values),
                len([value for value in battery_values if value is not None]),
                len(points),
            ),
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
