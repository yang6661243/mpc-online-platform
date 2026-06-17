"""MPC run, status, aggregation, and monthly demand reference endpoints."""
from __future__ import annotations

import os
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.constants import AggregateRequest, RunMpcRequest, normalize_plant_id
from api.database.orm import MonthlyDemandRef, MpcRun, StrategyComparison
from api.services.aggregation import aggregate_telemetry_15min
from api.services.dashboard import comparison_payload
from api.services.excel_import import import_mpc_run_from_excel
from api.services.mpc.orchestrator import MpcRunnerNotConfigured, run_online_mpc
from api.utils.dependencies import get_session

router = APIRouter()


@router.post("/api/v1/mpc/run")
def run_mpc(payload: RunMpcRequest, request: Request, session: Session = Depends(get_session)):
    plant_id = normalize_plant_id(payload.plant_id)
    try:
        result = run_online_mpc(
            session,
            request_id=payload.request_id,
            plant_id=plant_id,
            start_time=payload.start_time,
            end_time=payload.end_time,
            profile=payload.profile,
            runner=request.app.state.mpc_runner,
            output_dir=request.app.state.run_output_dir,
            load_base_kw=payload.load_base_kw,
            buy_price=payload.buy_price,
            sell_price=payload.sell_price,
            c_deg=payload.c_deg,
            demand_rate=payload.demand_rate,
            billing_days=payload.billing_days,
            target_peak_kw=payload.target_peak_kw,
        )
    except MpcRunnerNotConfigured as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "success": True,
        "run_id": result.run.run_id,
        "plant_id": result.run.plant_id,
        "status": result.run.status,
        "target_peak_kw": payload.target_peak_kw,
        "scenario_path": str(result.scenario.output_path),
        "comparison": comparison_payload(result.comparison),
        "message": "mpc run succeeded",
    }


@router.get("/api/v1/mpc/runs/{run_id}")
def mpc_run_result(run_id: str, session: Session = Depends(get_session)):
    run = session.scalar(select(MpcRun).where(MpcRun.run_id == run_id))
    if run is None:
        raise HTTPException(status_code=404, detail="mpc run not found")

    comparison = session.scalar(
        select(StrategyComparison)
        .where(StrategyComparison.run_id == run_id)
        .order_by(StrategyComparison.created_at.desc())
        .limit(1)
    )
    return {
        "run_id": run.run_id,
        "plant_id": run.plant_id,
        "profile": run.profile,
        "status": run.status,
        "started_at": None if run.started_at is None else run.started_at.isoformat(),
        "finished_at": None if run.finished_at is None else run.finished_at.isoformat(),
        "input_start_time": None if run.input_start_time is None else run.input_start_time.isoformat(),
        "input_end_time": None if run.input_end_time is None else run.input_end_time.isoformat(),
        "scenario_path": run.scenario_path,
        "error_message": run.error_message,
        "comparison": comparison_payload(comparison),
    }


@router.post("/api/v1/plants/{plant_id}/aggregate")
def aggregate_plant_telemetry(
    plant_id: str,
    payload: AggregateRequest,
    session: Session = Depends(get_session),
):
    plant_id = normalize_plant_id(plant_id)
    try:
        rows = aggregate_telemetry_15min(
            session,
            plant_id=plant_id,
            start_time=payload.start_time,
            end_time=payload.end_time,
            window_minutes=payload.window_minutes,
            resample_minutes=payload.resample_minutes,
            max_staleness_minutes=payload.max_staleness_minutes,
            battery_power_mode=payload.battery_power_mode
            or os.getenv("MPC_BATTERY_POWER_MODE", "signed_meter"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    quality_counts: dict[str, int] = {}
    for row in rows:
        quality_counts[row.quality_flag] = quality_counts.get(row.quality_flag, 0) + 1

    return {
        "success": True,
        "plant_id": plant_id,
        "window_count": len(rows),
        "quality_counts": quality_counts,
    }


@router.post("/api/v1/plants/{plant_id}/import-mpc-run")
async def import_mpc_run_route(
    plant_id: str,
    profile: str = Query(default="imported"),
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
):
    plant_id = normalize_plant_id(plant_id)
    if not file.filename or not (file.filename.endswith(".xlsx") or file.filename.endswith(".xls")):
        raise HTTPException(status_code=400, detail="仅支持 .xlsx 或 .xls 文件")
    try:
        content = await file.read()
        result = import_mpc_run_from_excel(
            session,
            plant_id=plant_id,
            file_bytes=content,
            profile=profile,
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/v1/plants/{plant_id}/mpc-status")
def mpc_status(
    plant_id: str,
    request: Request,
    profile: str = Query(default="demand100"),
    session: Session = Depends(get_session),
):
    plant_id = normalize_plant_id(plant_id)
    key = f"{plant_id}/{profile}"
    checker = request.app.state.health_checkers.get(key)
    if checker is None:
        raise HTTPException(status_code=404, detail=f"health checker not configured for: {key}")
    status = checker.get_status(session)
    return {
        "plant_id": status.plant_id,
        "profile": status.profile,
        "state": status.state,
        "label": status.label,
        "telemetry_windows": status.telemetry_windows,
        "ok_windows": status.ok_windows,
        "interpolated_windows": status.interpolated_windows,
        "continuous_from_month_start": status.continuous_from_month_start,
        "continuous_ok_windows": status.continuous_ok_windows,
        "last_run_id": status.last_run_id,
        "last_run_status": status.last_run_status,
        "last_run_finished_at": status.last_run_finished_at,
        "last_run_error": status.last_run_error,
        "minutes_since_last_run": status.minutes_since_last_run,
        "has_gap": status.has_gap,
        "max_gap_minutes": status.max_gap_minutes,
        "new_windows_since_last": status.new_windows_since_last,
        "monthly_demand_ref_set": status.monthly_demand_ref_set,
        "checked_at": status.checked_at,
    }


@router.get("/api/v1/plants/{plant_id}/monthly-demand-ref")
def get_monthly_demand_ref(
    plant_id: str,
    session: Session = Depends(get_session),
):
    plant_id = normalize_plant_id(plant_id)
    now = datetime.now()
    year_month = now.strftime("%Y-%m")
    ref = session.scalar(
        select(MonthlyDemandRef).where(
            MonthlyDemandRef.plant_id == plant_id,
            MonthlyDemandRef.year_month == year_month,
        )
    )
    if ref is None:
        return {"plant_id": plant_id, "year_month": year_month, "reference_peak_kw": None}
    return {
        "plant_id": ref.plant_id,
        "year_month": ref.year_month,
        "reference_peak_kw": ref.reference_peak_kw,
    }


@router.post("/api/v1/plants/{plant_id}/monthly-demand-ref")
def set_monthly_demand_ref(
    plant_id: str,
    reference_peak_kw: float = Query(gt=0),
    session: Session = Depends(get_session),
):
    plant_id = normalize_plant_id(plant_id)
    now = datetime.now()
    year_month = now.strftime("%Y-%m")
    ref = session.scalar(
        select(MonthlyDemandRef).where(
            MonthlyDemandRef.plant_id == plant_id,
            MonthlyDemandRef.year_month == year_month,
        )
    )
    if ref is None:
        ref = MonthlyDemandRef(
            plant_id=plant_id,
            year_month=year_month,
            reference_peak_kw=reference_peak_kw,
        )
        session.add(ref)
    else:
        ref.reference_peak_kw = reference_peak_kw
    session.commit()
    return {
        "success": True,
        "plant_id": plant_id,
        "year_month": year_month,
        "reference_peak_kw": reference_peak_kw,
    }
