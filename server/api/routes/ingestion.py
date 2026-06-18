"""Telemetry ingestion endpoints: input-data and import-raw-data."""
from __future__ import annotations

import io
import os
import time
from datetime import datetime as _dt
from pathlib import Path as _Path

import openpyxl
from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, UploadFile
from sqlalchemy.orm import Session

from api.constants import InputDataRequest, normalize_plant_id
from api.services.aggregation import aggregate_telemetry_15min
from api.services.ingestion import ingest_telemetry_records
from api.services.input_mapping import normalize_input_records
from api.services.irradiance import fetch_and_store_irradiance
from api.services.plant_config import load_plant_config
from api.utils.dependencies import get_session
from api.utils.signature import verify_signature
from api.utils.time import parse_timestamp

router = APIRouter()


@router.post("/api/v1/mpc/input-data")
async def input_data(
    payload: InputDataRequest,
    request: Request,
    session: Session = Depends(get_session),
    x_timestamp: str | None = Header(default=None, alias="X-Timestamp"),
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
    x_signature: str | None = Header(default=None, alias="X-Signature"),
):
    try:
        try:
            verify_signature(
                secret=request.app.state.input_signature_secret,
                body=await request.body(),
                timestamp=x_timestamp,
                request_id=x_request_id,
                signature=x_signature,
            )
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

        records = normalize_input_records(
            payload.data_type,
            payload.records,
            field_mapping=payload.field_mapping,
            power_signs=payload.power_signs,
            soc_unit=payload.soc_unit,
        )
        plant_id = normalize_plant_id(payload.plant_id)
        if payload.data_type not in ("grid_meter", "battery"):
            raise HTTPException(status_code=400, detail=f"unsupported data_type: {payload.data_type}")
        accepted = ingest_telemetry_records(
            session,
            plant_id=plant_id,
            records=records,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "success": True,
        "request_id": payload.request_id,
        "plant_id": plant_id,
        "data_type": payload.data_type,
        "accepted_count": accepted,
        "duplicate": False,
        "message": "accepted",
    }


def _get_plant_coords(plant_id: str) -> dict | None:
    """从电站配置读取经纬度。光伏容量为 0 的电站返回 None。"""
    from api.constants import PROJECT_ROOT

    config_names = {
        "hehong_huajin": "hehong_huajin",
        "aolaide": "aodelai",
    }
    name = config_names.get(plant_id)
    if not name:
        return None
    cfg_path = PROJECT_ROOT / "mpc" / "configs" / "plants" / f"{name}.yaml"
    if not cfg_path.exists():
        return None
    cfg = load_plant_config(cfg_path)
    if getattr(cfg.pv, "capacity_kw", 0) <= 0:
        return None
    return {"lat": cfg.location.latitude, "lng": cfg.location.longitude}


# ── 列名识别：电价列支持多种写法 ──
_PRICE_COLUMN_CANDIDATES = ("实时电价", "购电价")


def _safe_numeric(v) -> float | None:
    """将 Excel 单元格值安全转为 float，空值和空字符串返回 None。"""
    if v is None:
        return None
    if isinstance(v, str) and v.strip() == "":
        return None
    return float(v)


def _detect_plant_from_name(name: str) -> str | None:
    """从 Sheet 名或文件名识别电站标识。"""
    lower = (name or "").lower()
    if any(kw in lower for kw in ("和宏", "华进", "huajin", "hehong")):
        return "hehong_huajin"
    if any(kw in lower for kw in ("奥来德", "aolaide")):
        return "aolaide"
    return None


def _resolve_plant(sheet_name: str, filename_lower: str, url_plant_id: str | None) -> str:
    """综合 Sheet 名、文件名、URL 参数解析电站。"""
    detected = _detect_plant_from_name(sheet_name) or _detect_plant_from_name(filename_lower)
    if detected and url_plant_id and normalize_plant_id(url_plant_id) != detected:
        raise HTTPException(
            status_code=400,
            detail=f"识别电站为 {detected}，但与 URL 参数 {url_plant_id} 不一致，请检查",
        )
    resolved = detected or (normalize_plant_id(url_plant_id) if url_plant_id else None)
    if resolved is None:
        raise HTTPException(
            status_code=400,
            detail=f"无法识别电站（Sheet 名=\"{sheet_name}\"）。"
                   "请确保 Sheet 名包含「和宏华进」「奥来德」，或通过 ?plant_id=xxx 指定",
        )
    return normalize_plant_id(resolved)


def _find_buy_price_col(headers: list[str], col_map: dict[str, int]) -> int | None:
    """查找电价列索引，支持「实时电价」「购电价」「购电价(元/kWh)」等写法。"""
    for name in _PRICE_COLUMN_CANDIDATES:
        if name in col_map:
            return col_map[name]
    # 模糊匹配：以「购电价」开头的列名
    for idx, h in enumerate(headers):
        if h.startswith("购电价"):
            return idx
    return None


def _parse_one_sheet(
    rows,
    sheet_name: str,
    filename_lower: str,
    url_plant_id: str | None,
) -> tuple[str, list[dict], int, list[str]]:
    """解析一个 Sheet，返回 (plant_id, records, skipped, headers)。"""
    sheet_plant = _resolve_plant(sheet_name, filename_lower, url_plant_id)

    headers = [str(h).strip() if h is not None else "" for h in rows[0]]
    col_map: dict[str, int] = {}
    for idx, h in enumerate(headers):
        if h in ("时间", "电网功率", "储能功率", "SOC") or h in _PRICE_COLUMN_CANDIDATES:
            col_map[h] = idx
    # 购电价模糊列也加入 col_map
    buy_price_idx = _find_buy_price_col(headers, col_map)
    if buy_price_idx is not None and "购电价" not in col_map:
        col_map["购电价"] = buy_price_idx

    if "时间" not in col_map:
        raise HTTPException(
            status_code=400,
            detail=f"Sheet「{sheet_name}」缺少「时间」列，实际列: {headers}",
        )

    records: list[dict] = []
    skipped = 0
    for row in rows[1:]:
        if all(v is None for v in row):
            skipped += 1
            continue
        time_val = row[col_map["时间"]]
        if time_val is None:
            skipped += 1
            continue
        if isinstance(time_val, str):
            time_val = parse_timestamp(time_val)
        elif not hasattr(time_val, "isoformat"):
            skipped += 1
            continue

        time_str = time_val.isoformat() if hasattr(time_val, "isoformat") else str(time_val)
        record: dict = {"time": time_str}
        if "电网功率" in col_map:
            v = row[col_map["电网功率"]]
            f = _safe_numeric(v)
            if f is not None:
                record["grid_power_kw"] = f
        if "储能功率" in col_map:
            v = row[col_map["储能功率"]]
            f = _safe_numeric(v)
            if f is not None:
                record["battery_power_kw"] = f
        if "SOC" in col_map:
            raw_soc = _safe_numeric(row[col_map["SOC"]])
            if raw_soc is not None:
                # SOC 可能是 0~1 或 0~100，>2 则视为百分比
                record["soc"] = raw_soc / 100.0 if raw_soc > 2 else raw_soc
        price_col = _find_buy_price_col(headers, col_map)
        if price_col is not None:
            f = _safe_numeric(row[price_col])
            if f is not None:
                record["buy_price"] = f

        records.append(record)

    return sheet_plant, records, skipped, headers


@router.post("/api/v1/plants/import-raw-data")
async def import_raw_data(
    file: UploadFile = File(...),
    plant_id: str | None = Query(default=None, description="电站标识，留空则从 Sheet 名自动识别"),
    auto_aggregate: bool = Query(default=False, description="导入后自动触发 15 分钟聚合（默认关闭，由 MPC 启动时驱动）"),
    include_irradiance: bool = Query(default=False, description="聚合后是否同步拉取辐照度；默认关闭，避免上传接口等待外部网络"),
    session: Session = Depends(get_session),
):
    """导入原始电表数据（.xlsx/.xls/.numbers）。

    Excel/numbers 列名：
    - 时间（必填）
    - 电网功率（kW）
    - 储能功率（kW）
    - SOC（0~1 或 0~100 百分比）
    - 实时电价 / 购电价 / 购电价(元/kWh)（可选）

    电站通过 Sheet 名自动识别（支持多 Sheet）：
    - Sheet 名含「和宏/华进/huajin/hehong」→ hehong_huajin
    - Sheet 名含「奥来德/aolaide」→ aolaide

    支持 1 分钟、5 分钟、15 分钟等任意粒度混存。
    已存在的 (plant_id, time) 自动覆盖。
    返回进度摘要。
    """
    t0 = time.monotonic()
    log_lines: list[str] = []

    def _log(msg: str) -> None:
        elapsed = time.monotonic() - t0
        line = f"[{elapsed:6.2f}s] {msg}"
        log_lines.append(line)
        print(line)

    log_dir = _Path("logs/imports")
    log_dir.mkdir(parents=True, exist_ok=True)
    upload_ts = _dt.now().strftime("%Y%m%d_%H%M%S")

    _log(f"========== 导入开始 ==========")
    _log(f"文件名: {file.filename}")

    if not file.filename:
        raise HTTPException(status_code=400, detail="未提供文件名")
    filename_lower = file.filename.lower()
    if not (filename_lower.endswith(".xlsx") or filename_lower.endswith(".xls")):
        raise HTTPException(status_code=400, detail="仅支持 .xlsx / .xls 文件，Numbers 请先导出为 Excel")

    # ── 1. 读取文件 ──
    _log(f"步骤1: 读取文件... ({file.filename})")
    content = await file.read()
    file_size_kb = len(content) / 1024
    _log(f"文件大小: {file_size_kb:.1f} KB")

    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    sheet_names = wb.sheetnames
    _log(f"发现 {len(sheet_names)} 个 Sheet: {sheet_names}")

    # ── 2. 逐 Sheet 解析 ──
    all_plant_records: dict[str, list[dict]] = {}
    all_skipped = 0
    all_total_rows = 0
    sheet_results: list[dict] = []

    for sn in sheet_names:
        ws = wb[sn]
        rows = list(ws.iter_rows(values_only=True))
        if len(rows) < 2:
            _log(f"跳过 Sheet「{sn}」: 数据不足（需至少一行表头+一行数据）")
            continue

        _log(f"步骤2: 解析 Sheet「{sn}」... ({len(rows) - 1} 行数据)")
        sheet_plant, records, skipped, headers = _parse_one_sheet(
            rows, sn, filename_lower, url_plant_id=plant_id,
        )
        _log(f"  → 电站={sheet_plant}, 列={[h for h in headers if h]}, "
             f"有效={len(records)}, 跳过={skipped}")

        if sheet_plant not in all_plant_records:
            all_plant_records[sheet_plant] = []
        all_plant_records[sheet_plant].extend(records)
        all_skipped += skipped
        all_total_rows += len(rows) - 1
        sheet_results.append({
            "sheet_name": sn,
            "plant_id": sheet_plant,
            "records": len(records),
            "skipped": skipped,
        })

    wb.close()

    if not all_plant_records:
        raise HTTPException(status_code=400, detail="文件中所有 Sheet 均无有效数据行")

    # ── 3. 写入 raw_telemetry（按电站分组） ──
    total_count = 0
    plant_write_results: dict[str, int] = {}
    for pid, records in all_plant_records.items():
        _log(f"步骤3: 写入 raw_telemetry... 电站={pid}, {len(records)} 条")
        count = ingest_telemetry_records(session, plant_id=pid, records=records)
        plant_write_results[pid] = count
        total_count += count
        _log(f"  写入完成: {count} 条已持久化")

    t2 = time.monotonic()

    # ── 4. 结果组装 ──
    primary_plant = max(all_plant_records.keys(), key=lambda p: len(all_plant_records[p]))
    result: dict = {
        "success": True,
        "plant_id": primary_plant,
        "sheet_count": len(sheet_results),
        "sheets": sheet_results,
        "records_count": total_count,
        "total_rows": all_total_rows,
        "skipped_rows": all_skipped,
        "file_size_kb": round(file_size_kb, 1),
        "parse_sec": round(t2 - t0, 2),
        "write_sec": round(t2 - t0, 2),
    }

    # ── 5. 自动聚合（按电站分组） ──
    if auto_aggregate:
        agg_total = 0
        irrad_total = 0
        for pid, records in all_plant_records.items():
            times = sorted(r["time"] for r in records)
            _log(f"步骤4: 15分钟聚合... 电站={pid} ({times[0]} ~ {times[-1]})")
            try:
                agg_rows = aggregate_telemetry_15min(
                    session, plant_id=pid,
                    start_time=times[0], end_time=times[-1],
                )
                agg_total += len(agg_rows)
                _log(f"  聚合完成: {len(agg_rows)} 个15分钟窗口")

                if include_irradiance:
                    try:
                        plant_cfg = _get_plant_coords(pid)
                        if plant_cfg:
                            _log(f"步骤5: 获取辐照度... 电站={pid} "
                                 f"(lat={plant_cfg['lat']}, lng={plant_cfg['lng']})")
                            irrad_updated = fetch_and_store_irradiance(
                                session, plant_id=pid,
                                start_time=agg_rows[0].start_time,
                                end_time=agg_rows[-1].end_time,
                                latitude=plant_cfg["lat"],
                                longitude=plant_cfg["lng"],
                            )
                            irrad_total += irrad_updated
                            _log(f"  辐照度完成: {irrad_updated} 窗口已回填")
                        else:
                            _log(f"  跳过辐照度: 电站={pid} 无光伏")
                    except Exception as exc:
                        _log(f"  辐照度获取失败(不影响主流程): {exc}")
                else:
                    _log(f"  跳过辐照度: include_irradiance=false")
            except ValueError as exc:
                _log(f"  聚合失败: {exc}")

        result["aggregated_windows"] = agg_total
        if irrad_total > 0:
            result["irradiance_updated"] = irrad_total

    t3 = time.monotonic()
    result["total_sec"] = round(t3 - t0, 2)

    plant_summary = ", ".join(f"{p}={c}条" for p, c in plant_write_results.items())
    log_name = f"{upload_ts}_{primary_plant}_import.log"
    log_path = log_dir / log_name
    _log(f"========== 导入完成 ==========")
    _log(f"总结: 文件={file.filename}, 电站=[{plant_summary}], "
         f"聚合={result.get('aggregated_windows', 'N/A')}窗口, "
         f"总耗时={result['total_sec']}s")
    log_path.write_text("\n".join(log_lines), encoding="utf-8")
    result["log_file"] = str(log_path)

    return result


@router.post("/api/v1/admin/clear-data")
async def clear_data(session: Session = Depends(get_session)):
    """清空本项目导入和生成的所有数据（raw_telemetry / telemetry_15min / MPC runs 等）。

    保留数据库表结构，仅删除数据行。
    """
    from api.database.orm import (
        IntraMinutePoint,
        MonthlyDemandRef,
        MpcRun,
        MpcRunProgress,
        MpcTarget,
        RawTelemetry,
        StrategyComparison,
        StrategyCurvePoint,
        Telemetry15Min,
    )

    tables = [
        (RawTelemetry, "raw_telemetry"),
        (Telemetry15Min, "telemetry_15min"),
        (MpcRun, "mpc_runs"),
        (MpcTarget, "mpc_targets"),
        (MonthlyDemandRef, "monthly_demand_refs"),
        (IntraMinutePoint, "intra_minute_points"),
        (StrategyComparison, "strategy_comparison"),
        (StrategyCurvePoint, "strategy_curve_points"),
        (MpcRunProgress, "mpc_run_progress"),
    ]

    deleted: dict[str, int] = {}
    for model, name in tables:
        count = session.query(model).count()
        if count > 0:
            session.query(model).delete()
        deleted[name] = count

    session.commit()
    return {"success": True, "deleted": deleted}
