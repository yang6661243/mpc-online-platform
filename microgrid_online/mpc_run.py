from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from microgrid_online.comparison import (
    StrategyMetrics,
    compute_actual_strategy_metrics,
    save_strategy_comparison,
)
from microgrid_online.models import MpcRun, StrategyComparison, StrategyCurvePoint, Telemetry15Min, utc_now
from microgrid_online.mpc_adapter import MpcScenarioExport, export_mpc_scenario_from_telemetry
from microgrid_online.time_utils import parse_timestamp


@dataclass(frozen=True)
class OnlineMpcRunInput:
    run_id: str
    plant_id: str
    profile: str | None
    start_time: datetime
    end_time: datetime
    scenario: MpcScenarioExport
    telemetry_rows: list[Telemetry15Min]
    actual_metrics: StrategyMetrics
    buy_price: float
    sell_price: float
    c_deg: float
    demand_rate: float
    billing_days: float
    target_peak_kw: float | None = None


@dataclass(frozen=True)
class OnlineMpcRunResult:
    run: MpcRun
    comparison: StrategyComparison
    scenario: MpcScenarioExport


@dataclass(frozen=True)
class MpcRunnerResult:
    metrics: StrategyMetrics
    curve: Sequence[dict] = ()


MpcRunner = Callable[[OnlineMpcRunInput], StrategyMetrics | MpcRunnerResult]


class MpcRunnerNotConfigured(RuntimeError):
    pass


def make_run_id(request_id: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "_", request_id).strip("_")
    if not token:
        raise ValueError("request_id must contain at least one valid character")
    return f"mpc_{token}"


def _load_ready_telemetry_rows(
    session: Session,
    *,
    plant_id: str,
    start_time: datetime,
    end_time: datetime,
) -> list[Telemetry15Min]:
    rows = list(
        session.scalars(
            select(Telemetry15Min)
            .where(
                Telemetry15Min.plant_id == plant_id,
                Telemetry15Min.start_time >= start_time,
                Telemetry15Min.end_time <= end_time,
            )
            .order_by(Telemetry15Min.start_time)
        )
    )
    if not rows:
        raise ValueError("no telemetry rows found")

    for row in rows:
        if row.quality_flag != "ok":
            raise ValueError(f"telemetry window is not ready: {row.quality_flag}")
        if row.grid_power_kw_avg is None:
            raise ValueError("telemetry window missing grid_power_kw_avg")
        if row.battery_power_kw_avg is None:
            raise ValueError("telemetry window missing battery_power_kw_avg")
        if row.soc_end is None:
            raise ValueError("telemetry window missing soc_end")

    return rows


def _actual_metrics_from_rows(
    rows: list[Telemetry15Min],
    *,
    buy_price: float,
    sell_price: float,
    c_deg: float,
    demand_rate: float,
    billing_days: float,
) -> StrategyMetrics:
    return compute_actual_strategy_metrics(
        grid_power_kw=[float(row.grid_power_kw_avg) for row in rows],
        battery_power_kw=[float(row.battery_power_kw_avg) for row in rows],
        soc=[float(row.soc_end) for row in rows if row.soc_end is not None],
        buy_price=[float(buy_price)] * len(rows),
        sell_price=[float(sell_price)] * len(rows),
        c_deg=float(c_deg),
        demand_rate=float(demand_rate),
        billing_days=float(billing_days),
    )


def _save_strategy_curve_points(
    session: Session,
    *,
    run_id: str,
    plant_id: str,
    rows: list[Telemetry15Min],
    mpc_curve: Sequence[dict],
    buy_price: float,
    sell_price: float,
) -> list[StrategyCurvePoint]:
    if not mpc_curve:
        return []
    if len(mpc_curve) != len(rows):
        raise ValueError(
            f"mpc curve length must match telemetry rows: {len(mpc_curve)} != {len(rows)}"
        )

    points = []
    for row, mpc in zip(rows, mpc_curve):
        point = StrategyCurvePoint(
            run_id=run_id,
            plant_id=plant_id,
            time=row.start_time,
            actual_grid_power_kw=row.grid_power_kw_avg,
            actual_battery_power_kw=row.battery_power_kw_avg,
            actual_soc=row.soc_end,
            load_minus_pv_kw=row.load_minus_pv_kw_avg,
            mpc_grid_power_kw=mpc.get("grid_power_kw"),
            mpc_battery_power_kw=mpc.get("battery_power_kw"),
            mpc_soc=mpc.get("soc"),
            buy_price=mpc.get("buy_price", buy_price),
            sell_price=mpc.get("sell_price", sell_price),
        )
        session.add(point)
        points.append(point)
    session.commit()
    return points


def run_online_mpc(
    session: Session,
    *,
    request_id: str,
    plant_id: str,
    start_time,
    end_time,
    profile: str | None,
    runner: MpcRunner | None,
    output_dir: str | Path,
    load_base_kw: float | None = None,
    buy_price: float = 0.8,
    sell_price: float = 0.3,
    c_deg: float = 0.05,
    demand_rate: float = 30.0,
    billing_days: float = 30.0,
    target_peak_kw: float | None = None,
) -> OnlineMpcRunResult:
    if runner is None:
        raise MpcRunnerNotConfigured("MPC runner is not configured")

    start = parse_timestamp(start_time)
    end = parse_timestamp(end_time)
    if end <= start:
        raise ValueError("end_time must be after start_time")

    run_id = make_run_id(request_id)
    existing = session.scalar(select(MpcRun).where(MpcRun.run_id == run_id))
    if existing is not None:
        raise ValueError(f"run_id already exists: {run_id}")

    scenario_path = Path(output_dir) / run_id / "scenario.xlsx"
    run = MpcRun(
        run_id=run_id,
        plant_id=plant_id,
        profile=profile,
        status="running",
        input_start_time=start,
        input_end_time=end,
        scenario_path=str(scenario_path),
        started_at=utc_now(),
    )
    session.add(run)
    session.commit()

    try:
        rows = _load_ready_telemetry_rows(
            session,
            plant_id=plant_id,
            start_time=start,
            end_time=end,
        )
        scenario = export_mpc_scenario_from_telemetry(
            session,
            plant_id=plant_id,
            start_time=start,
            end_time=end,
            output_path=scenario_path,
            load_base_kw=load_base_kw,
            buy_price=buy_price,
            sell_price=sell_price,
        )
        actual_metrics = _actual_metrics_from_rows(
            rows,
            buy_price=buy_price,
            sell_price=sell_price,
            c_deg=c_deg,
            demand_rate=demand_rate,
            billing_days=billing_days,
        )
        runner_result = runner(
            OnlineMpcRunInput(
                run_id=run_id,
                plant_id=plant_id,
                profile=profile,
                start_time=start,
                end_time=end,
                scenario=scenario,
                telemetry_rows=rows,
                actual_metrics=actual_metrics,
                buy_price=buy_price,
                sell_price=sell_price,
                c_deg=c_deg,
                demand_rate=demand_rate,
                billing_days=billing_days,
                target_peak_kw=target_peak_kw,
            )
        )
        if isinstance(runner_result, MpcRunnerResult):
            mpc_metrics = runner_result.metrics
            mpc_curve = runner_result.curve
        else:
            mpc_metrics = runner_result
            mpc_curve = ()
        comparison = save_strategy_comparison(
            session,
            run_id=run_id,
            plant_id=plant_id,
            actual=actual_metrics,
            mpc=mpc_metrics,
        )
        _save_strategy_curve_points(
            session,
            run_id=run_id,
            plant_id=plant_id,
            rows=rows,
            mpc_curve=mpc_curve,
            buy_price=buy_price,
            sell_price=sell_price,
        )
        run.status = "succeeded"
        run.finished_at = utc_now()
        session.commit()
        session.refresh(run)
        return OnlineMpcRunResult(run=run, comparison=comparison, scenario=scenario)
    except Exception as exc:
        run.status = "failed"
        run.error_message = str(exc)
        run.finished_at = utc_now()
        session.commit()
        raise
