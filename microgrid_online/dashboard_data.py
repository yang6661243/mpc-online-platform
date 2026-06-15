from __future__ import annotations

from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from microgrid_online.comparison import compare_strategy_metrics, compute_actual_strategy_metrics
from microgrid_online.models import StrategyComparison, StrategyCurvePoint, Telemetry15Min
from microgrid_online.time_utils import parse_timestamp


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
    start_time: str | None = None,
    end_time: str | None = None,
) -> list[Telemetry15Min]:
    explicit_start, explicit_end = _parse_time_range(start_time, end_time)
    if explicit_start is None and explicit_end is None:
        start_at = latest.end_time - timedelta(hours=window_hours)
        end_at = latest.end_time
    else:
        start_at = explicit_start
        end_at = explicit_end or latest.end_time

    conditions = [
        Telemetry15Min.plant_id == plant_id,
        Telemetry15Min.end_time <= end_at,
    ]
    if start_at is not None:
        conditions.append(Telemetry15Min.end_time > start_at)
    return list(
        session.scalars(
            select(Telemetry15Min)
            .where(*conditions)
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


def _parse_time_range(start_time: str | None, end_time: str | None):
    start = parse_timestamp(start_time) if start_time else None
    end = parse_timestamp(end_time) if end_time else None
    if start is not None and end is not None and start >= end:
        raise HTTPException(status_code=422, detail="start_time must be before end_time")
    return start, end


def _curve_points_for_run(
    session: Session,
    *,
    run_id: str,
    start_time: str | None = None,
    end_time: str | None = None,
) -> list[StrategyCurvePoint]:
    start, end = _parse_time_range(start_time, end_time)
    conditions = [StrategyCurvePoint.run_id == run_id]
    if start is not None:
        conditions.append(StrategyCurvePoint.time > start - timedelta(minutes=15))
    if end is not None:
        conditions.append(StrategyCurvePoint.time <= end - timedelta(minutes=15))
    points = list(
        session.scalars(
            select(StrategyCurvePoint)
            .where(*conditions)
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
                "actual_load_kw": None if point is None else point.actual_load_kw,
                "actual_pv_kw": None if point is None else point.actual_pv_kw,
                "load_minus_pv_kw": row.load_minus_pv_kw_avg,
                "mpc_grid_power_kw": None if point is None else point.mpc_grid_power_kw,
                "mpc_battery_power_kw": None if point is None else point.mpc_battery_power_kw,
                "mpc_soc": None if point is None else point.mpc_soc,
                "mpc_load_kw": None if point is None else point.mpc_load_kw,
                "mpc_pv_kw": None if point is None else point.mpc_pv_kw,
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
                "actual_load_kw": point.actual_load_kw,
                "actual_pv_kw": point.actual_pv_kw,
                "load_minus_pv_kw": point.load_minus_pv_kw,
                "mpc_grid_power_kw": point.mpc_grid_power_kw,
                "mpc_battery_power_kw": point.mpc_battery_power_kw,
                "mpc_soc": point.mpc_soc,
                "mpc_load_kw": point.mpc_load_kw,
                "mpc_pv_kw": point.mpc_pv_kw,
                "buy_price": point.buy_price,
                "sell_price": point.sell_price,
                "quality_flag": "ok",
            }
        )
    return series


def _comparison_payload_for_points(
    *,
    run_id: str,
    points: list[StrategyCurvePoint],
    c_deg: float = 0.05,
    demand_rate: float = 39.0,
    billing_days: float = 30.0,
) -> dict:
    actual = compute_actual_strategy_metrics(
        grid_power_kw=[float(point.actual_grid_power_kw) for point in points],
        battery_power_kw=[float(point.actual_battery_power_kw) for point in points],
        soc=[float(point.actual_soc) for point in points],
        buy_price=[float(point.buy_price) for point in points],
        sell_price=[float(point.sell_price) for point in points],
        c_deg=c_deg,
        demand_rate=demand_rate,
        billing_days=billing_days,
    )
    mpc = compute_actual_strategy_metrics(
        grid_power_kw=[float(point.mpc_grid_power_kw) for point in points],
        battery_power_kw=[float(point.mpc_battery_power_kw) for point in points],
        soc=[float(point.mpc_soc) for point in points],
        buy_price=[float(point.buy_price) for point in points],
        sell_price=[float(point.sell_price) for point in points],
        c_deg=c_deg,
        demand_rate=demand_rate,
        billing_days=billing_days,
    )
    comparison = compare_strategy_metrics(actual, mpc)
    return {
        "run_id": run_id,
        "actual_peak_kw": actual.peak_kw,
        "mpc_peak_kw": mpc.peak_kw,
        "peak_reduction_kw": comparison.peak_reduction_kw,
        "peak_reduction_pct": comparison.peak_reduction_pct,
        "actual_cost_yuan": actual.total_cost_yuan,
        "mpc_cost_yuan": mpc.total_cost_yuan,
        "cost_saving_yuan": comparison.cost_saving_yuan,
        "cost_saving_pct": comparison.cost_saving_pct,
    }


def _comparison_payload_for_series(
    *,
    run_id: str,
    series: list[dict],
    c_deg: float = 0.05,
    demand_rate: float = 39.0,
    billing_days: float = 30.0,
) -> dict | None:
    comparable_points = [
        point
        for point in series
        if point["actual_grid_power_kw"] is not None
        and point["actual_battery_power_kw"] is not None
        and point["actual_soc"] is not None
        and point["mpc_grid_power_kw"] is not None
        and point["mpc_battery_power_kw"] is not None
        and point["mpc_soc"] is not None
    ]
    if not comparable_points:
        return None

    actual = compute_actual_strategy_metrics(
        grid_power_kw=[float(point["actual_grid_power_kw"]) for point in comparable_points],
        battery_power_kw=[float(point["actual_battery_power_kw"]) for point in comparable_points],
        soc=[float(point["actual_soc"]) for point in comparable_points],
        buy_price=[float(point["buy_price"] or 0.986) for point in comparable_points],
        sell_price=[float(point["sell_price"] or 0.0) for point in comparable_points],
        c_deg=c_deg,
        demand_rate=demand_rate,
        billing_days=billing_days,
    )
    mpc = compute_actual_strategy_metrics(
        grid_power_kw=[float(point["mpc_grid_power_kw"]) for point in comparable_points],
        battery_power_kw=[float(point["mpc_battery_power_kw"]) for point in comparable_points],
        soc=[float(point["mpc_soc"]) for point in comparable_points],
        buy_price=[float(point["buy_price"] or 0.986) for point in comparable_points],
        sell_price=[float(point["sell_price"] or 0.0) for point in comparable_points],
        c_deg=c_deg,
        demand_rate=demand_rate,
        billing_days=billing_days,
    )
    comparison = compare_strategy_metrics(actual, mpc)
    return {
        "run_id": run_id,
        "actual_peak_kw": actual.peak_kw,
        "mpc_peak_kw": mpc.peak_kw,
        "peak_reduction_kw": comparison.peak_reduction_kw,
        "peak_reduction_pct": comparison.peak_reduction_pct,
        "actual_cost_yuan": actual.total_cost_yuan,
        "mpc_cost_yuan": mpc.total_cost_yuan,
        "cost_saving_yuan": comparison.cost_saving_yuan,
        "cost_saving_pct": comparison.cost_saving_pct,
    }


def _dashboard_payload_for_run(
    session: Session,
    *,
    plant_id: str,
    run_id: str,
    start_time: str | None = None,
    end_time: str | None = None,
) -> dict:
    comparison = _comparison_by_run_id(session, plant_id=plant_id, run_id=run_id)
    points = _curve_points_for_run(session, run_id=run_id, start_time=start_time, end_time=end_time)
    latest = points[-1]
    selected_comparison = (
        comparison_payload(comparison)
        if start_time is None and end_time is None
        else _comparison_payload_for_points(run_id=run_id, points=points)
    )
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
        "comparison": selected_comparison,
        "series": _run_series_payload(points),
    }


def build_dashboard_payload(
    session: Session,
    *,
    plant_id: str,
    window_hours: int = 24,
    run_id: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
) -> dict:
    if run_id:
        return _dashboard_payload_for_run(
            session,
            plant_id=plant_id,
            run_id=run_id,
            start_time=start_time,
            end_time=end_time,
        )

    if window_hours < 1 or window_hours > 168:
        raise HTTPException(status_code=422, detail="window_hours must be between 1 and 168")

    latest = _latest_telemetry(session, plant_id)
    comparison = _latest_comparison(session, plant_id)
    rows = _telemetry_window(
        session,
        plant_id=plant_id,
        latest=latest,
        window_hours=window_hours,
        start_time=start_time,
        end_time=end_time,
    )
    if not rows:
        raise HTTPException(status_code=404, detail="no telemetry found in selected time range")
    curve_by_time = _curve_points_by_time(session, comparison)
    series = _series_payload(rows, curve_by_time)
    selected_current = rows[-1]
    selected_comparison = (
        None
        if comparison is None
        else _comparison_payload_for_series(run_id=comparison.run_id, series=series)
    )

    return {
        "plant_id": plant_id,
        "current": {
            "time": selected_current.end_time.isoformat(),
            "grid_power_kw": selected_current.grid_power_kw_avg,
            "battery_power_kw": selected_current.battery_power_kw_avg,
            "load_minus_pv_kw": selected_current.load_minus_pv_kw_avg,
            "soc": selected_current.soc_end,
            "quality_flag": selected_current.quality_flag,
        },
        "comparison": selected_comparison,
        "series": series,
    }
