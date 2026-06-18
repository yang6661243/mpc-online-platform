"""MPC run, status, aggregation, and monthly demand reference endpoints."""
from __future__ import annotations

import logging
import os
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.constants import AggregateRequest, RunMpcRequest, normalize_plant_id
from api.database.orm import MonthlyDemandRef, MpcRun, MpcRunProgress, StrategyComparison, Telemetry15Min
from api.services.aggregation import aggregate_telemetry_15min
from api.services.dashboard import comparison_payload
from api.services.excel_import import import_mpc_run_from_excel
from api.services.mpc.orchestrator import MpcRunnerNotConfigured, run_online_mpc
from api.utils.dependencies import get_session
from api.utils.time import parse_timestamp

logger = logging.getLogger(__name__)

router = APIRouter()


def _resolve_target_peak_kw(payload: RunMpcRequest, demand_ref: MonthlyDemandRef) -> float:
    return float(payload.target_peak_kw or demand_ref.reference_peak_kw)


@router.post("/api/v1/mpc/run")
def run_mpc(payload: RunMpcRequest, request: Request, session: Session = Depends(get_session)):
    """手动启动 MPC: 聚合 → 辐照度 → 按月分组 → 训练模型 → 逐月 MPC → 持久化 → 启用 scheduler。"""
    from collections import defaultdict
    from api.database.orm import RawTelemetry, StrategyCurvePoint
    from api.services.irradiance import fetch_and_store_irradiance
    from api.services.mpc.health import _persist_mpc_results
    from api.services.mpc.forecaster_trainer import train_forecaster_for_month

    plant_id = normalize_plant_id(payload.plant_id)

    # ── ① 检查 raw 数据 ──
    raw_times = list(
        session.scalars(
            select(RawTelemetry.time)
            .where(RawTelemetry.plant_id == plant_id)
            .order_by(RawTelemetry.time)
        )
    )
    if not raw_times:
        raise HTTPException(status_code=400, detail="没有 raw 数据，请先导入电表数据")
    raw_start, raw_end = raw_times[0], raw_times[-1]

    # ── ② 检查本月目标需量是否已设置 ──
    from api.database.orm import MonthlyDemandRef
    now = datetime.now()
    year_month = now.strftime("%Y-%m")
    demand_ref = session.scalar(
        select(MonthlyDemandRef).where(
            MonthlyDemandRef.plant_id == plant_id,
            MonthlyDemandRef.year_month == year_month,
        )
    )
    if demand_ref is None:
        raise HTTPException(
            status_code=400,
            detail=f"请先在右侧面板设置 {year_month} 的目标需量值（kW），再启动 MPC。",
        )
    target_peak_kw = _resolve_target_peak_kw(payload, demand_ref)

    # ── ③ 聚合全部 raw 数据 ──
    try:
        aggregate_telemetry_15min(
            session, plant_id=plant_id,
            start_time=raw_start.isoformat(), end_time=raw_end.isoformat(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"聚合失败: {exc}") from exc

    # ── ③ 辐照度（如有光伏）──
    _fetch_irradiance_if_pv(session, plant_id=plant_id)

    # ── ④ 按月分组 ──
    month_groups: dict[str, list[datetime]] = defaultdict(list)
    for t in raw_times:
        month_groups[t.strftime("%Y-%m")].append(t)
    months = sorted(month_groups.keys())

    # 第一个月无上月数据 → 跳过
    if len(months) < 2:
        raise HTTPException(
            status_code=400,
            detail=f"数据仅覆盖 {months[0]}，缺少上一个月训练数据，无法运行 MPC。请导入至少两个月数据。",
        )

    run_ids = []
    for i in range(1, len(months)):
        train_month = months[i - 1]
        mpc_month = months[i]
        mpc_start = month_groups[mpc_month][0]
        mpc_end_raw = month_groups[mpc_month][-1]
        # month end: last day 23:45
        mpc_end = mpc_end_raw.replace(hour=23, minute=45, second=0, microsecond=0)

        logger.info("=== MPC month %s: train on %s ===", mpc_month, train_month)

        # ⑤ 训练负荷预测模型（用上月数据）
        try:
            model_path, load_base_kw = train_forecaster_for_month(
                session, plant_id=plant_id, train_month=train_month,
            )
        except Exception as exc:
            logger.error("Forecaster training failed for %s month=%s: %s", plant_id, train_month, exc)
            raise HTTPException(status_code=500, detail=f"训练 {train_month} 负荷模型失败: {exc}") from exc

        # ⑥ 为这个月创建临时训练数据 Excel（用作 forecast_history）
        from api.services.mpc.forecaster_trainer import _export_training_excel
        history_excel, _ = _export_training_excel(
            session, plant_id=plant_id, train_month=train_month,
        )

        # ⑦ 运行 MPC
        request_id = f"{payload.request_id}_{mpc_month}"
        try:
            result = run_online_mpc(
                session, request_id=request_id, plant_id=plant_id,
                start_time=mpc_start.isoformat(), end_time=mpc_end.isoformat(),
                profile=payload.profile, runner=request.app.state.mpc_runner,
                output_dir=request.app.state.run_output_dir,
                load_base_kw=payload.load_base_kw or load_base_kw,
                buy_price=payload.buy_price, sell_price=payload.sell_price,
                c_deg=payload.c_deg, demand_rate=payload.demand_rate,
                billing_days=payload.billing_days,
                target_peak_kw=target_peak_kw,
                forecast_model_path=model_path,
                forecast_history_file=str(history_excel),
            )
            _persist_mpc_results(session, result.run.run_id, plant_id)
            run_ids.append(result.run.run_id)
            logger.info("=== MPC month %s done: run_id=%s ===", mpc_month, result.run.run_id)

        except MpcRunnerNotConfigured as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"MPC {mpc_month} 失败: {exc}") from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"MPC {mpc_month} 失败: {exc}") from exc

    # ── ⑧ 启用 scheduler ──
    scheduler = request.app.state.schedulers.get(plant_id)
    if scheduler:
        scheduler.mark_aggregated(raw_end)
        scheduler.enable()

    return {
        "success": True,
        "plant_id": plant_id,
        "months_processed": len(run_ids),
        "run_ids": run_ids,
        "scheduler_enabled": scheduler.enabled if scheduler else False,
        "message": f"完成 {len(run_ids)} 个月 MPC: {', '.join(run_ids)}",
    }


def _fetch_irradiance_if_pv(session: Session, *, plant_id: str) -> None:
    """If plant config has PV capacity > 0, fetch irradiance data."""
    try:
        from api.constants import PROJECT_ROOT
        from api.services.plant_config import load_plant_config
        from api.services.irradiance import fetch_and_store_irradiance
        config_names = {"hehong_huajin": "hehong_huajin", "aolaide": "aodelai"}
        config_name = config_names.get(plant_id, plant_id)
        cfg_path = PROJECT_ROOT / "mpc" / "configs" / "plants" / f"{config_name}.yaml"
        if cfg_path.exists():
            cfg = load_plant_config(cfg_path)
            if getattr(cfg.pv, "capacity_kw", 0) > 0:
                agg_rows = list(
                    session.scalars(
                        select(Telemetry15Min)
                        .where(Telemetry15Min.plant_id == plant_id)
                        .order_by(Telemetry15Min.start_time)
                    )
                )
                if agg_rows:
                    fetch_and_store_irradiance(
                        session, plant_id=plant_id,
                        start_time=agg_rows[0].start_time,
                        end_time=agg_rows[-1].end_time,
                        latitude=cfg.location.latitude,
                        longitude=cfg.location.longitude,
                    )
    except Exception as exc:
        logger.warning("Irradiance fetch skipped for %s: %s", plant_id, exc)


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


@router.get("/api/v1/mpc/runs/{run_id}/progress/latest")
def mpc_run_latest_progress(run_id: str, session: Session = Depends(get_session)):
    row = session.scalar(
        select(MpcRunProgress)
        .where(MpcRunProgress.run_id == run_id)
        .order_by(MpcRunProgress.step.desc())
        .limit(1)
    )
    if row is None:
        return {"run_id": run_id, "status": "not_started"}
    return {
        "run_id": row.run_id,
        "step": row.step,
        "total_steps": row.total_steps,
        "soc": row.soc,
        "peak_kw": row.peak_kw,
        "running_cost": row.running_cost,
        "battery_power_kw": row.battery_power_kw,
        "grid_power_kw": row.grid_power_kw,
        "load_kw": row.load_kw,
        "pv_kw": row.pv_kw,
        "elapsed_seconds": row.elapsed_seconds,
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
    scheduler = request.app.state.schedulers.get(plant_id)
    scheduler_enabled = scheduler.enabled if scheduler else False

    # 超时仅在 scheduler 启用后检测（历史数据无需延续到当前时间）
    if not scheduler_enabled:
        status.data_timed_out = False
    elif status.data_timed_out:
        scheduler.disable()
        scheduler_enabled = False

    return {
        "plant_id": status.plant_id,
        "profile": status.profile,
        "state": status.state,
        "label": status.label,
        "telemetry_windows": status.telemetry_windows,
        "ok_windows": status.ok_windows,
        "interpolated_windows": status.interpolated_windows,
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
        "data_timed_out": status.data_timed_out,
        "minutes_since_latest_data": status.minutes_since_latest_data,
        "scheduler_enabled": scheduler_enabled,
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
