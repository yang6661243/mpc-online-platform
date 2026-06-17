"""Train a LightGBM load forecaster from telemetry_15min data."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.constants import PROJECT_ROOT
from api.database.orm import Telemetry15Min

logger = logging.getLogger(__name__)

MODELS_DIR = PROJECT_ROOT / "outputs" / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)


def _get_pv_capacity(plant_id: str) -> float:
    """Read PV capacity from plant config. Returns 0 if no PV."""
    try:
        from api.services.plant_config import load_plant_config
        config_names = {"hehong_huajin": "hehong_huajin", "aolaide": "aodelai"}
        config_name = config_names.get(plant_id, plant_id)
        cfg_path = PROJECT_ROOT / "mpc" / "configs" / "plants" / f"{config_name}.yaml"
        if cfg_path.exists():
            cfg = load_plant_config(cfg_path)
            return float(getattr(cfg.pv, "capacity_kw", 0) or 0)
    except Exception:
        pass
    return 0.0


def train_forecaster_for_month(
    session: Session,
    *,
    plant_id: str,
    train_month: str,  # "2026-04"
) -> tuple[str, float]:
    """Train a LightGBM forecaster on one month's telemetry data.

    Returns (model_path, load_base_kw).
    """
    from mpc.solvers.forecasting import LoadForecaster

    year, month = train_month.split("-")
    start = datetime(int(year), int(month), 1)
    if int(month) == 12:
        end = datetime(int(year) + 1, 1, 1)
    else:
        end = datetime(int(year), int(month) + 1, 1)

    rows = list(
        session.scalars(
            select(Telemetry15Min)
            .where(
                Telemetry15Min.plant_id == plant_id,
                Telemetry15Min.start_time >= start,
                Telemetry15Min.start_time < end,
                Telemetry15Min.quality_flag == "ok",
                Telemetry15Min.load_minus_pv_kw_avg.isnot(None),
            )
            .order_by(Telemetry15Min.start_time)
        )
    )

    if not rows:
        raise ValueError(f"No telemetry data for {plant_id} in {train_month}")

    # Get plant PV capacity to compute real load (load = net_load + PV)
    pv_capacity_kw = _get_pv_capacity(plant_id)

    loads_kw = []
    irrad_count = 0
    for r in rows:
        net_load = float(r.load_minus_pv_kw_avg)
        irrad = float(r.irradiance_w_m2 or 0)
        if irrad > 0:
            irrad_count += 1
        pv_kw = irrad * pv_capacity_kw / 1000.0
        real_load = net_load + pv_kw  # 净负荷 + 光伏 = 真实负荷
        loads_kw.append(real_load)

    if pv_capacity_kw > 0 and irrad_count == 0:
        logger.warning(
            "No irradiance data for %s in %s. PV=%.0fkW but all irradiance is NULL. "
            "Load training values may be negative (net_load without PV).",
            plant_id, train_month, pv_capacity_kw,
        )

    load_arr = np.array(loads_kw)
    timestamps = pd.DatetimeIndex([r.start_time for r in rows])
    load_base_kw = float(load_arr.max()) if load_arr.max() > 0 else 1.0
    load_ratio = load_arr / load_base_kw

    logger.info("Training forecaster for %s month=%s: %d samples, base_kw=%.1f",
                plant_id, train_month, len(load_ratio), load_base_kw)

    cfg = {
        "model": {
            "type": "lightgbm_direct_horizon",
            "horizon_steps": 192,
        },
        "data": {"train_ratio": 0.9},
        "features": {
            "lags": [1, 2, 3, 96, 672],
            "rolling_windows": [96],
            "calendar": True,
            "cyclical": True,
            "external_columns": [],
        },
    }

    fc = LoadForecaster(cfg)
    # Quick check: build features to verify data is sufficient
    from mpc.solvers.forecasting.direct_features import build_direct_training_frame
    df_feat, cols = build_direct_training_frame(
        pd.DataFrame({"time": timestamps, "value": load_ratio}), cfg["features"]
    )
    logger.info("Direct training frame: %d rows, %d cols (min rows needed: ~%d)",
                len(df_feat), len(cols), (len(load_ratio) - 672) * 192)
    if len(df_feat) == 0:
        raise ValueError(
            f"No training samples generated. Data: {len(load_ratio)} samples, "
            f"load_range=[{load_ratio.min():.3f}, {load_ratio.max():.3f}]. "
            f"Need at least {max(cfg['features']['lags']) + cfg['model']['horizon_steps']} samples."
        )

    metrics = fc.train(load_ratio=load_ratio, timestamps=timestamps)
    logger.info("Train done: MAE=%.4f RMSE=%.4f MAPE=%.1f%%",
                metrics.get("train_mae", 0),
                metrics.get("train_rmse", 0),
                (metrics.get("train_mape", 0) or 0) * 100)

    model_path = str(MODELS_DIR / f"{plant_id}_{train_month}.joblib")
    fc.save(model_path)
    logger.info("Model saved: %s", model_path)

    return model_path, load_base_kw


def _export_training_excel(
    session: Session,
    *,
    plant_id: str,
    train_month: str,
) -> tuple[Path, float]:
    """Export one month's real load as Excel for MPC forecast_history warm-start.

    Returns (excel_path, load_base_kw).
    """
    year, month = train_month.split("-")
    start = datetime(int(year), int(month), 1)
    if int(month) == 12:
        end = datetime(int(year) + 1, 1, 1)
    else:
        end = datetime(int(year), int(month) + 1, 1)

    rows = list(
        session.scalars(
            select(Telemetry15Min)
            .where(
                Telemetry15Min.plant_id == plant_id,
                Telemetry15Min.start_time >= start,
                Telemetry15Min.start_time < end,
                Telemetry15Min.quality_flag == "ok",
                Telemetry15Min.load_minus_pv_kw_avg.isnot(None),
            )
            .order_by(Telemetry15Min.start_time)
        )
    )
    if not rows:
        raise ValueError(f"No telemetry data for {plant_id} in {train_month}")

    pv_capacity_kw = _get_pv_capacity(plant_id)
    loads_kw = []
    for r in rows:
        net_load = float(r.load_minus_pv_kw_avg)
        irrad = float(r.irradiance_w_m2 or 0)
        pv_kw = irrad * pv_capacity_kw / 1000.0
        loads_kw.append(net_load + pv_kw)

    load_base_kw = max(loads_kw) if loads_kw else 1.0
    if load_base_kw <= 0:
        load_base_kw = 1.0
    load_ratio = [l / load_base_kw for l in loads_kw]
    times = [r.start_time for r in rows]

    import pandas as pd
    df = pd.DataFrame({"time": times, "load": load_ratio})
    excel_path = (
        PROJECT_ROOT / "scenarios" / f"{plant_id}_{train_month}_history.xlsx"
    )
    excel_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="load", index=False)

    logger.info("Exported forecast_history: %s (%d rows, base_kw=%.1f)",
                excel_path, len(rows), load_base_kw)
    return excel_path, load_base_kw
