from __future__ import annotations

from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from microgrid_online.models import StrategyComparison, StrategyCurvePoint, Telemetry15Min


def comparison_payload(comparison: StrategyComparison | None) -> dict | None:
    if comparison is None:
        return None
    return {
        "run_id": comparison.run_id,
        "actual_peak_kw": comparison.actual_peak_kw,
        "mpc_peak_kw": comparison.mpc_peak_kw,
        "peak_reduction_kw": comparison.peak_reduction_kw,
        "peak_reduction_pct": comparison.peak_reduction_pct,
        "actual_cost_yuan": comparison.actual_cost_yuan,
        "mpc_cost_yuan": comparison.mpc_cost_yuan,
        "cost_saving_yuan": comparison.cost_saving_yuan,
        "cost_saving_pct": comparison.cost_saving_pct,
    }


def _latest_telemetry(session: Session, plant_id: str) -> Telemetry15Min:
    latest = session.scalar(
        select(Telemetry15Min)
        .where(Telemetry15Min.plant_id == plant_id)
        .order_by(Telemetry15Min.end_time.desc())
        .limit(1)
    )
    if latest is None:
        raise HTTPException(status_code=404, detail="no telemetry found")
    return latest


def _telemetry_window(
    session: Session,
    *,
    plant_id: str,
    latest: Telemetry15Min,
    window_hours: int,
) -> list[Telemetry15Min]:
    start_at = latest.end_time - timedelta(hours=window_hours)
    return list(
        session.scalars(
            select(Telemetry15Min)
            .where(
                Telemetry15Min.plant_id == plant_id,
                Telemetry15Min.end_time > start_at,
                Telemetry15Min.end_time <= latest.end_time,
            )
            .order_by(Telemetry15Min.end_time)
        )
    )


def _latest_comparison(session: Session, plant_id: str) -> StrategyComparison | None:
    return session.scalar(
        select(StrategyComparison)
        .where(StrategyComparison.plant_id == plant_id)
        .order_by(StrategyComparison.created_at.desc())
        .limit(1)
    )


def _curve_points_by_time(
    session: Session,
    comparison: StrategyComparison | None,
) -> dict:
    if comparison is None:
        return {}
    points = list(
        session.scalars(
            select(StrategyCurvePoint)
            .where(StrategyCurvePoint.run_id == comparison.run_id)
            .order_by(StrategyCurvePoint.time)
        )
    )
    return {point.time: point for point in points}


def _comparison_by_run_id(session: Session, *, plant_id: str, run_id: str) -> StrategyComparison:
    comparison = session.scalar(
        select(StrategyComparison)
        .where(
            StrategyComparison.plant_id == plant_id,
            StrategyComparison.run_id == run_id,
        )
        .order_by(StrategyComparison.created_at.desc())
        .limit(1)
    )
    if comparison is None:
        raise HTTPException(status_code=404, detail="strategy comparison not found")
    return comparison


def _curve_points_for_run(session: Session, *, run_id: str) -> list[StrategyCurvePoint]:
    points = list(
        session.scalars(
            select(StrategyCurvePoint)
            .where(StrategyCurvePoint.run_id == run_id)
            .order_by(StrategyCurvePoint.time)
        )
    )
    if not points:
        raise HTTPException(status_code=404, detail="strategy curve points not found")
    return points


def _series_payload(rows: list[Telemetry15Min], curve_by_time: dict) -> list[dict]:
    series = []
    for row in rows:
        point = curve_by_time.get(row.start_time)
        series.append(
            {
                "time": row.end_time.isoformat(),
                "actual_grid_power_kw": row.grid_power_kw_avg,
                "actual_battery_power_kw": row.battery_power_kw_avg,
                "actual_soc": row.soc_end,
                "load_minus_pv_kw": row.load_minus_pv_kw_avg,
                "mpc_grid_power_kw": None if point is None else point.mpc_grid_power_kw,
                "mpc_battery_power_kw": None if point is None else point.mpc_battery_power_kw,
                "mpc_soc": None if point is None else point.mpc_soc,
                "buy_price": None if point is None else point.buy_price,
                "sell_price": None if point is None else point.sell_price,
                "quality_flag": row.quality_flag,
            }
        )
    return series


def _run_series_payload(points: list[StrategyCurvePoint]) -> list[dict]:
    series = []
    for point in points:
        series.append(
            {
                "time": (point.time + timedelta(minutes=15)).isoformat(),
                "actual_grid_power_kw": point.actual_grid_power_kw,
                "actual_battery_power_kw": point.actual_battery_power_kw,
                "actual_soc": point.actual_soc,
                "load_minus_pv_kw": point.load_minus_pv_kw,
                "mpc_grid_power_kw": point.mpc_grid_power_kw,
                "mpc_battery_power_kw": point.mpc_battery_power_kw,
                "mpc_soc": point.mpc_soc,
                "buy_price": point.buy_price,
                "sell_price": point.sell_price,
                "quality_flag": "ok",
            }
        )
    return series


def _dashboard_payload_for_run(session: Session, *, plant_id: str, run_id: str) -> dict:
    comparison = _comparison_by_run_id(session, plant_id=plant_id, run_id=run_id)
    points = _curve_points_for_run(session, run_id=run_id)
    latest = points[-1]
    return {
        "plant_id": plant_id,
        "current": {
            "time": (latest.time + timedelta(minutes=15)).isoformat(),
            "grid_power_kw": latest.actual_grid_power_kw,
            "battery_power_kw": latest.actual_battery_power_kw,
            "load_minus_pv_kw": latest.load_minus_pv_kw,
            "soc": latest.actual_soc,
            "quality_flag": "ok",
        },
        "comparison": comparison_payload(comparison),
        "series": _run_series_payload(points),
    }


def build_dashboard_payload(
    session: Session,
    *,
    plant_id: str,
    window_hours: int = 24,
    run_id: str | None = None,
) -> dict:
    if run_id:
        return _dashboard_payload_for_run(session, plant_id=plant_id, run_id=run_id)

    if window_hours < 1 or window_hours > 168:
        raise HTTPException(status_code=422, detail="window_hours must be between 1 and 168")

    latest = _latest_telemetry(session, plant_id)
    comparison = _latest_comparison(session, plant_id)
    rows = _telemetry_window(session, plant_id=plant_id, latest=latest, window_hours=window_hours)
    curve_by_time = _curve_points_by_time(session, comparison)

    return {
        "plant_id": plant_id,
        "current": {
            "time": latest.end_time.isoformat(),
            "grid_power_kw": latest.grid_power_kw_avg,
            "battery_power_kw": latest.battery_power_kw_avg,
            "load_minus_pv_kw": latest.load_minus_pv_kw_avg,
            "soc": latest.soc_end,
            "quality_flag": latest.quality_flag,
        },
        "comparison": comparison_payload(comparison),
        "series": _series_payload(rows, curve_by_time),
    }
