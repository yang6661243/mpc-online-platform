from __future__ import annotations

import math
from collections.abc import Iterable, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from api.database.orm import RawTelemetry
from api.utils.time import parse_timestamp


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


def ingest_telemetry_records(
    session: Session,
    *,
    plant_id: str,
    records: Iterable[Mapping],
) -> int:
    """插入或更新原始电表数据到 raw_telemetry。

    每条 record 至少需要 "time"。可选字段：
    - grid_power_kw（电网功率，kW）
    - battery_power_kw（储能功率，kW）
    - soc（荷电状态，0~1）
    - buy_price（实时电价，元/kWh）

    按 (plant_id, time) 做 upsert：
    - 不存在则插入
    - 已存在则只更新本次传入的字段（其他字段保留原值）
    """
    count = 0
    for record in records:
        ts = parse_timestamp(record["time"])
        existing = session.scalar(
            select(RawTelemetry).where(
                RawTelemetry.plant_id == plant_id,
                RawTelemetry.time == ts,
            )
        )

        updates: dict[str, object] = {}
        if "grid_power_kw" in record and record["grid_power_kw"] is not None:
            updates["grid_power_kw"] = _as_finite_float(record["grid_power_kw"], "grid_power_kw")
        if "battery_power_kw" in record and record["battery_power_kw"] is not None:
            updates["battery_power_kw"] = _as_finite_float(record["battery_power_kw"], "battery_power_kw")
        if "soc" in record and record["soc"] is not None:
            updates["soc"] = _validate_soc(record["soc"])
        if "buy_price" in record and record["buy_price"] is not None:
            updates["buy_price"] = _as_finite_float(record["buy_price"], "buy_price")
        if "source" in record:
            updates["source"] = str(record["source"])

        if not updates:
            continue  # 没有可写字段，跳过

        if existing:
            for key, value in updates.items():
                setattr(existing, key, value)
        else:
            session.add(RawTelemetry(plant_id=plant_id, time=ts, **updates))  # type: ignore[arg-type]
        count += 1

    session.commit()
    return count
