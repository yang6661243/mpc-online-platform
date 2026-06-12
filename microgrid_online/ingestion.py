from __future__ import annotations

import math
from collections.abc import Iterable, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from microgrid_online.models import RawBattery, RawGridMeter
from microgrid_online.time_utils import parse_timestamp


def _as_finite_float(value, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be finite")
    return number


def _validate_soc(value) -> float:
    soc = _as_finite_float(value, "soc")
    if not 0.0 <= soc <= 1.0:
        raise ValueError("soc must be between 0 and 1")
    return soc


def ingest_grid_records(
    session: Session,
    *,
    plant_id: str,
    records: Iterable[Mapping],
) -> int:
    count = 0
    for record in records:
        ts = parse_timestamp(record["time"])
        grid_power_kw = _as_finite_float(record["grid_power_kw"], "grid_power_kw")
        existing = session.scalar(
            select(RawGridMeter).where(
                RawGridMeter.plant_id == plant_id,
                RawGridMeter.time == ts,
            )
        )
        if existing:
            existing.grid_power_kw = grid_power_kw
        else:
            session.add(
                RawGridMeter(
                    plant_id=plant_id,
                    time=ts,
                    grid_power_kw=grid_power_kw,
                )
            )
        count += 1
    session.commit()
    return count


def ingest_battery_records(
    session: Session,
    *,
    plant_id: str,
    records: Iterable[Mapping],
) -> int:
    count = 0
    for record in records:
        ts = parse_timestamp(record["time"])
        battery_power_kw = _as_finite_float(record["battery_power_kw"], "battery_power_kw")
        soc = _validate_soc(record["soc"])
        existing = session.scalar(
            select(RawBattery).where(
                RawBattery.plant_id == plant_id,
                RawBattery.time == ts,
            )
        )
        values = {
            "battery_power_kw": battery_power_kw,
            "soc": soc,
            "battery_available": bool(record.get("battery_available", True)),
            "pcs_available": bool(record.get("pcs_available", True)),
        }
        if existing:
            for key, value in values.items():
                setattr(existing, key, value)
        else:
            session.add(
                RawBattery(
                    plant_id=plant_id,
                    time=ts,
                    **values,
                )
            )
        count += 1
    session.commit()
    return count
