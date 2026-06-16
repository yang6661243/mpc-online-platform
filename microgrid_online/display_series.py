from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from microgrid_online.models import RawBattery, RawGridMeter


DisplayQuality = Literal["observed", "interpolated_quadratic", "gap", "derived"]


@dataclass(frozen=True)
class Sample:
    minute: datetime
    value: float


def _floor_minute(value: datetime) -> datetime:
    return value.replace(second=0, microsecond=0)


def _timeline(start: datetime, end: datetime) -> list[datetime]:
    current = _floor_minute(start)
    final = _floor_minute(end)
    minutes: list[datetime] = []
    while current <= final:
        minutes.append(current)
        current += timedelta(minutes=1)
    return minutes


def _bucket_samples(rows: list[tuple[datetime, float]]) -> dict[datetime, Sample]:
    buckets: dict[datetime, tuple[datetime, float]] = {}
    for raw_time, value in rows:
        minute = _floor_minute(raw_time)
        previous = buckets.get(minute)
        if previous is None or raw_time >= previous[0]:
            buckets[minute] = (raw_time, float(value))
    return {minute: Sample(minute=minute, value=value) for minute, (_raw_time, value) in buckets.items()}


def _query_grid_samples(session: Session, plant_id: str, start: datetime, end: datetime) -> dict[datetime, Sample]:
    rows = session.execute(
        select(RawGridMeter.time, RawGridMeter.grid_power_kw)
        .where(
            RawGridMeter.plant_id == plant_id,
            RawGridMeter.time >= start,
            RawGridMeter.time <= end,
        )
        .order_by(RawGridMeter.time)
    ).all()
    return _bucket_samples([(time_value, value) for time_value, value in rows])


def _query_battery_samples(
    session: Session,
    plant_id: str,
    start: datetime,
    end: datetime,
    field_name: str,
) -> dict[datetime, Sample]:
    column = RawBattery.battery_power_kw if field_name == "battery_power_kw" else RawBattery.soc
    rows = session.execute(
        select(RawBattery.time, column)
        .where(
            RawBattery.plant_id == plant_id,
            RawBattery.time >= start,
            RawBattery.time <= end,
        )
        .order_by(RawBattery.time)
    ).all()
    return _bucket_samples([(time_value, value) for time_value, value in rows])


def _quadratic_value(samples: list[Sample], target: datetime) -> float:
    points = [((sample.minute - target).total_seconds() / 60.0, sample.value) for sample in samples]
    result = 0.0
    for index, (x_i, y_i) in enumerate(points):
        basis = 1.0
        for other_index, (x_j, _y_j) in enumerate(points):
            if other_index == index:
                continue
            basis *= (0.0 - x_j) / (x_i - x_j)
        result += y_i * basis
    return result


def _clamp_power(value: float, samples: list[Sample]) -> float:
    observed = [sample.value for sample in samples]
    low = min(observed)
    high = max(observed)
    span = max(high - low, abs(high) * 0.05, 1.0)
    return min(max(value, low - span * 0.10), high + span * 0.10)


def _value_for_minute(
    samples_by_minute: dict[datetime, Sample],
    minute: datetime,
    *,
    max_gap_minutes: int,
    clamp: Literal["power", "soc"],
) -> tuple[float | None, DisplayQuality]:
    observed = samples_by_minute.get(minute)
    if observed is not None:
        return observed.value, "observed"

    samples = sorted(samples_by_minute.values(), key=lambda sample: sample.minute)
    before = [sample for sample in samples if sample.minute < minute]
    after = [sample for sample in samples if sample.minute > minute]
    if not before or not after:
        return None, "gap"

    nearest_before = before[-1]
    nearest_after = after[0]
    gap_minutes = (nearest_after.minute - nearest_before.minute).total_seconds() / 60.0
    if gap_minutes > max_gap_minutes:
        return None, "gap"

    nearby = sorted(samples, key=lambda sample: abs((sample.minute - minute).total_seconds()))[:3]
    if len(nearby) < 3:
        return None, "gap"
    nearby = sorted(nearby, key=lambda sample: sample.minute)
    if len({sample.minute for sample in nearby}) < 3:
        return None, "gap"

    value = _quadratic_value(nearby, minute)
    if clamp == "soc":
        value = min(max(value, 0.0), 1.0)
    else:
        value = _clamp_power(value, nearby)
    return round(value, 6), "interpolated_quadratic"


def build_display_series_payload(
    session: Session,
    *,
    plant_id: str,
    reference_time: datetime | None = None,
    window_hours: int = 2,
    max_gap_minutes: int = 5,
) -> dict:
    if window_hours not in {2, 6, 24}:
        raise ValueError("window_hours must be one of 2, 6, or 24")

    latest_grid = session.scalar(
        select(RawGridMeter.time)
        .where(RawGridMeter.plant_id == plant_id)
        .order_by(RawGridMeter.time.desc())
        .limit(1)
    )
    latest_battery = session.scalar(
        select(RawBattery.time)
        .where(RawBattery.plant_id == plant_id)
        .order_by(RawBattery.time.desc())
        .limit(1)
    )
    if reference_time is None:
        latest_times = [time_value for time_value in [latest_grid, latest_battery] if time_value is not None]
        if not latest_times:
            return {
                "plant_id": plant_id,
                "window_hours": window_hours,
                "step_minutes": 1,
                "display_only": True,
                "series": [],
            }
        reference_time = max(latest_times)

    end = _floor_minute(reference_time)
    start = end - timedelta(hours=window_hours)
    query_start = start - timedelta(minutes=max_gap_minutes)
    query_end = end + timedelta(minutes=max_gap_minutes)

    grid_samples = _query_grid_samples(session, plant_id, query_start, query_end)
    battery_samples = _query_battery_samples(session, plant_id, query_start, query_end, "battery_power_kw")
    soc_samples = _query_battery_samples(session, plant_id, query_start, query_end, "soc")

    series = []
    for minute in _timeline(start, end):
        grid_value, grid_quality = _value_for_minute(
            grid_samples,
            minute,
            max_gap_minutes=max_gap_minutes,
            clamp="power",
        )
        battery_value, battery_quality = _value_for_minute(
            battery_samples,
            minute,
            max_gap_minutes=max_gap_minutes,
            clamp="power",
        )
        soc_value, soc_quality = _value_for_minute(
            soc_samples,
            minute,
            max_gap_minutes=max_gap_minutes,
            clamp="soc",
        )
        if grid_value is not None and battery_value is not None:
            load_minus_pv = round(grid_value + battery_value, 6)
            load_quality: DisplayQuality = "derived"
        else:
            load_minus_pv = None
            load_quality = "gap"
        series.append(
            {
                "time": minute.isoformat(),
                "grid_power_kw": grid_value,
                "battery_power_kw": battery_value,
                "soc": soc_value,
                "load_minus_pv_kw": load_minus_pv,
                "quality": {
                    "grid_power_kw": grid_quality,
                    "battery_power_kw": battery_quality,
                    "soc": soc_quality,
                    "load_minus_pv_kw": load_quality,
                },
                "display_only": True,
            }
        )

    return {
        "plant_id": plant_id,
        "window_hours": window_hours,
        "step_minutes": 1,
        "display_only": True,
        "series": series,
    }
