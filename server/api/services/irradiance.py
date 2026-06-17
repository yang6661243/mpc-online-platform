"""Open-Meteo Archive API 辐照度获取与插值。

从 Open-Meteo 拉取逐小时 GHI（短波辐射），
通过二次插值对齐到 15 分钟 telemetry 窗口，存入 telemetry_15min.irradiance_w_m2。
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from api.database.orm import Telemetry15Min

logger = logging.getLogger(__name__)

OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


def fetch_hourly_ghi(
    latitude: float,
    longitude: float,
    start_date: str,
    end_date: str,
) -> list[dict]:
    """从 Open-Meteo Archive API 获取逐小时 GHI。

    Args:
        latitude: 纬度
        longitude: 经度
        start_date: 起始日期 "YYYY-MM-DD"
        end_date: 结束日期 "YYYY-MM-DD"

    Returns:
        [{"time": "2026-06-17T00:00", "ghi": 123.4}, ...]
    """
    import urllib.request
    import json as _json

    params = (
        f"latitude={latitude}"
        f"&longitude={longitude}"
        f"&start_date={start_date}"
        f"&end_date={end_date}"
        f"&hourly=shortwave_radiation"
        f"&timezone=Asia%2FShanghai"
    )
    url = f"{OPEN_METEO_ARCHIVE_URL}?{params}"

    logger.info("Fetching GHI from Open-Meteo: lat=%s lon=%s %s~%s", latitude, longitude, start_date, end_date)
    with urllib.request.urlopen(url, timeout=30) as resp:
        data = _json.loads(resp.read())

    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    values = hourly.get("shortwave_radiation", [])

    return [
        {"time": t, "ghi": float(v) if v is not None else 0.0}
        for t, v in zip(times, values)
    ]


def _quadratic_interpolate(
    hourly_times: list[datetime],
    hourly_values: list[float],
    target: datetime,
) -> float:
    """三点二次拉格朗日插值（纯 Python，无 scipy/numpy 依赖）。"""
    if len(hourly_times) < 2:
        return hourly_values[0] if hourly_values else 0.0

    # 找到离 target 最近的三个点
    deltas = [(abs((t - target).total_seconds()), i) for i, t in enumerate(hourly_times)]
    indices = [i for _, i in sorted(deltas)[:3]]
    indices.sort()

    result = 0.0
    for idx in indices:
        x_i = (hourly_times[idx] - target).total_seconds() / 3600.0
        y_i = hourly_values[idx]
        basis = 1.0
        for other in indices:
            if other == idx:
                continue
            x_j = (hourly_times[other] - target).total_seconds() / 3600.0
            basis *= (0.0 - x_j) / (x_i - x_j)
        result += y_i * basis

    return max(0.0, result)


def interpolate_ghi_to_telemetry(
    hourly_data: list[dict],
    telemetry_times: list[datetime],
) -> dict[datetime, float]:
    """将逐小时 GHI 二次插值到 15 分钟 telemetry 时间点。

    Returns:
        {telemetry_start_time: irradiance_w_m2, ...}
    """
    if not hourly_data or not telemetry_times:
        return {}

    hourly_times = [datetime.fromisoformat(d["time"]) for d in hourly_data]
    hourly_values = [d["ghi"] for d in hourly_data]

    return {
        t: _quadratic_interpolate(hourly_times, hourly_values, t)
        for t in telemetry_times
    }


def fetch_and_store_irradiance(
    session: Session,
    *,
    plant_id: str,
    start_time: datetime,
    end_time: datetime,
    latitude: float,
    longitude: float,
) -> int:
    """拉取 GHI → 二次插值 → 写入 telemetry_15min.irradiance_w_m2。

    经纬度从电站配置传入。奥来德（PV=0）可跳过。

    Returns:
        更新的 telemetry 行数。
    """
    if latitude == 0 and longitude == 0:
        logger.info("Skipping irradiance fetch: no coordinates (plant=%s)", plant_id)
        return 0

    start_date = start_time.strftime("%Y-%m-%d")
    end_date = end_time.strftime("%Y-%m-%d")

    try:
        hourly_data = fetch_hourly_ghi(latitude, longitude, start_date, end_date)
    except Exception as exc:
        logger.warning("Failed to fetch GHI for plant=%s: %s", plant_id, exc)
        return 0

    if not hourly_data:
        logger.warning("Empty GHI data for plant=%s (%s~%s)", plant_id, start_date, end_date)
        return 0

    # 查 telemetry 窗口
    rows = session.scalars(
        select(Telemetry15Min.start_time)
        .where(
            Telemetry15Min.plant_id == plant_id,
            Telemetry15Min.start_time >= start_time,
            Telemetry15Min.end_time <= end_time,
        )
        .order_by(Telemetry15Min.start_time)
    ).all()
    telemetry_times = list(rows)

    irradiance_map = interpolate_ghi_to_telemetry(hourly_data, telemetry_times)

    updated = 0
    for start_t, irradiance in irradiance_map.items():
        session.execute(
            update(Telemetry15Min)
            .where(
                Telemetry15Min.plant_id == plant_id,
                Telemetry15Min.start_time == start_t,
            )
            .values(irradiance_w_m2=irradiance)
        )
        updated += 1

    session.commit()
    logger.info("Stored irradiance for %d telemetry windows (plant=%s)", updated, plant_id)
    return updated
