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
        "ecloud_station_3341": "aodelai",
        "aodelai": "aodelai",
    }
    name = config_names.get(plant_id)
    if not name:
        return None
    cfg_path = PROJECT_ROOT / "mpc" / "configs" / "plants" / f"{name}.yaml"
    if not cfg_path.exists():
        return None
    cfg = load_plant_config(cfg_path)
    if getattr(cfg, "pv_capacity_kw", 0) <= 0:
        return None
    return {"lat": cfg.location.latitude, "lng": cfg.location.longitude}


@router.post("/api/v1/plants/import-raw-data")
async def import_raw_data(
    file: UploadFile = File(...),
    plant_id: str | None = Query(default=None, description="电站标识，留空则从 Sheet 名自动识别"),
    auto_aggregate: bool = Query(default=True, description="导入后自动触发 15 分钟聚合"),
    session: Session = Depends(get_session),
):
    """导入原始电表数据（.xlsx/.xls/.numbers）。

    Excel/numbers 列名：
    - 时间（必填）
    - 电网功率（kW）
    - 储能功率（kW）
    - SOC（0~1）
    - 实时电价（元/kWh，可选）

    电站通过 Sheet 名自动识别：
    - Sheet 名含「和宏/华进/huajin/hehong」→ hehong_huajin
    - Sheet 名含「奥来德/aodelai」→ aodelai

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
    ws = wb.active
    sheet_name = ws.title
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    total_rows = len(rows) - 1
    if len(rows) < 2:
        raise HTTPException(status_code=400, detail="文件至少需要一行表头 + 一行数据")

    # ── 2. 电站识别：Sheet 名优先，URL 参数兜底 ──
    _log(f"步骤2: 电站识别... Sheet名={sheet_name}")
    sheet_lower = (sheet_name or "").lower()
    detected_plant: str | None = None
    if any(kw in sheet_lower for kw in ("和宏", "华进", "huajin", "hehong")):
        detected_plant = "hehong_huajin"
    elif any(kw in sheet_lower for kw in ("奥来德", "aodelai")):
        detected_plant = "aodelai"

    if not detected_plant:
        if any(kw in filename_lower for kw in ("和宏", "华进", "huajin", "hehong")):
            detected_plant = "hehong_huajin"
        elif any(kw in filename_lower for kw in ("奥来德", "aodelai")):
            detected_plant = "aodelai"

    if detected_plant and plant_id and normalize_plant_id(plant_id) != detected_plant:
        raise HTTPException(
            status_code=400,
            detail=f"识别电站为 {detected_plant}，但与 URL 参数 {plant_id} 不一致，请检查",
        )

    resolved_plant = detected_plant or (normalize_plant_id(plant_id) if plant_id else None)
    if resolved_plant is None:
        raise HTTPException(
            status_code=400,
            detail="无法识别电站。请确保 Sheet 名包含「和宏华进」「奥来德」，或通过 ?plant_id=xxx 指定",
        )
    plant_id = resolved_plant
    _log(f"电站识别结果: {plant_id}{' (来自Sheet名)' if detected_plant else ' (来自URL参数)' if plant_id else ''}")

    # ── 3. 解析表头 ──
    headers = [str(h).strip() if h is not None else "" for h in rows[0]]
    col_map: dict[str, int] = {}
    for idx, h in enumerate(headers):
        if h in ("时间", "电网功率", "储能功率", "SOC", "实时电价"):
            col_map[h] = idx

    if "时间" not in col_map:
        raise HTTPException(status_code=400, detail=f"缺少「时间」列，实际列: {headers}")

    t1 = time.monotonic()
    _log(f"步骤3: 表头解析完成, 列: {[h for h in headers if h]}, 数据行: {total_rows}")

    # ── 4. 逐行解析并写入 ──
    _log(f"步骤4: 逐行解析...")
    records = []
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
            if v is not None:
                record["grid_power_kw"] = float(v)
        if "储能功率" in col_map:
            v = row[col_map["储能功率"]]
            if v is not None:
                record["battery_power_kw"] = float(v)
        if "SOC" in col_map:
            v = row[col_map["SOC"]]
            if v is not None:
                record["soc"] = float(v)
        if "实时电价" in col_map:
            v = row[col_map["实时电价"]]
            if v is not None:
                record["buy_price"] = float(v)

        records.append(record)

    if not records:
        raise HTTPException(status_code=400, detail="文件中无有效数据行")

    _log(f"解析完成: 有效记录={len(records)}, 跳过={skipped}, 耗时={time.monotonic()-t1:.2f}s")
    _log(f"步骤5: 写入 raw_telemetry... ({len(records)} 条)")
    count = ingest_telemetry_records(session, plant_id=plant_id, records=records)
    _log(f"写入完成: {count} 条已持久化到 raw_telemetry")

    t2 = time.monotonic()

    # ── 5. 结果组装 ──
    result: dict = {
        "success": True,
        "plant_id": plant_id,
        "sheet_name": sheet_name,
        "records_count": count,
        "total_rows": total_rows,
        "skipped_rows": skipped,
        "file_size_kb": round(file_size_kb, 1),
        "parse_sec": round(t1 - t0, 2),
        "write_sec": round(t2 - t1, 2),
    }

    # ── 6. 自动聚合 ──
    if auto_aggregate:
        times = sorted(r["time"] for r in records)
        _log(f"步骤6: 15分钟聚合... ({times[0]} ~ {times[-1]})")
        try:
            agg_rows = aggregate_telemetry_15min(
                session,
                plant_id=plant_id,
                start_time=times[0],
                end_time=times[-1],
            )
            result["aggregated_windows"] = len(agg_rows)
            _log(f"聚合完成: {len(agg_rows)} 个15分钟窗口")

            try:
                plant_cfg = _get_plant_coords(plant_id)
                if plant_cfg:
                    _log(f"步骤7: 获取辐照度... (lat={plant_cfg['lat']}, lng={plant_cfg['lng']})")
                    irrad_updated = fetch_and_store_irradiance(
                        session,
                        plant_id=plant_id,
                        start_time=agg_rows[0].start_time,
                        end_time=agg_rows[-1].end_time,
                        latitude=plant_cfg["lat"],
                        longitude=plant_cfg["lng"],
                    )
                    result["irradiance_updated"] = irrad_updated
                    _log(f"辐照度完成: {irrad_updated} 窗口已回填")
                else:
                    _log("跳过辐照度: 电站无光伏")
            except Exception as exc:
                _log(f"辐照度获取失败(不影响主流程): {exc}")
                pass

        except ValueError as exc:
            result["aggregation_error"] = str(exc)
            _log(f"聚合失败: {exc}")

    t3 = time.monotonic()
    result["total_sec"] = round(t3 - t0, 2)

    log_name = f"{upload_ts}_{plant_id}_import.log"
    log_path = log_dir / log_name
    _log(f"========== 导入完成 ==========")
    _log(f"总结: 文件={file.filename}, 电站={plant_id}, 写入={count}条, "
         f"聚合={result.get('aggregated_windows', 'N/A')}窗口, "
         f"总耗时={result['total_sec']}s")
    log_path.write_text("\n".join(log_lines), encoding="utf-8")
    result["log_file"] = str(log_path)

    return result
