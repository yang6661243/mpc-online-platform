"""Direct multi-horizon feature construction for load forecasting."""

from __future__ import annotations

import numpy as np
import pandas as pd


def _add_target_calendar_features(row: dict, target_time: pd.Timestamp, config: dict) -> None:
    if not config.get("calendar", True):
        return

    h = target_time.hour + target_time.minute / 60.0
    dow = target_time.dayofweek
    month = target_time.month

    row["hour"] = h
    row["dayofweek"] = dow
    row["month"] = month
    row["is_weekend"] = 1.0 if dow >= 5 else 0.0
    row["quarter_hour"] = target_time.minute // 15

    if config.get("cyclical", True):
        row["hour_sin"] = np.sin(2 * np.pi * h / 24)
        row["hour_cos"] = np.cos(2 * np.pi * h / 24)
        row["dow_sin"] = np.sin(2 * np.pi * dow / 7)
        row["dow_cos"] = np.cos(2 * np.pi * dow / 7)
        row["month_sin"] = np.sin(2 * np.pi * month / 12)
        row["month_cos"] = np.cos(2 * np.pi * month / 12)


def _history_features(values: np.ndarray, origin_idx: int, config: dict) -> dict:
    row = {}
    for lag in config.get("lags", []):
        src_idx = origin_idx - lag + 1
        if src_idx < 0:
            row[f"lag_{lag}"] = np.nan
        else:
            row[f"lag_{lag}"] = float(values[src_idx])

    for window in config.get("rolling_windows", []):
        start = origin_idx - window + 1
        if start < 0:
            row[f"rolling_mean_{window}"] = np.nan
            row[f"rolling_std_{window}"] = np.nan
        else:
            window_vals = values[start:origin_idx + 1]
            row[f"rolling_mean_{window}"] = float(np.mean(window_vals))
            row[f"rolling_std_{window}"] = float(np.std(window_vals))
    return row


def _merge_external(row: dict, target_time: pd.Timestamp, future_covariates: pd.DataFrame | None,
                    external_cols: list[str]) -> None:
    if not external_cols:
        return
    if future_covariates is None:
        raise ValueError(f"Config requires external_columns {external_cols} but no covariates provided.")
    cov_row = future_covariates[future_covariates["time"] == target_time]
    if len(cov_row) == 0:
        raise ValueError(f"No covariate row found for target_time={target_time}.")
    for col in external_cols:
        if col not in cov_row.columns:
            raise ValueError(f"External column '{col}' not found in covariates.")
        value = cov_row[col].iloc[0]
        if pd.isna(value):
            raise ValueError(f"External column '{col}' is null for target_time={target_time}.")
        row[col] = float(value)


def _feature_columns(config: dict) -> list[str]:
    cols = []
    cols.extend([f"lag_{lag}" for lag in config.get("lags", [])])
    for window in config.get("rolling_windows", []):
        cols.append(f"rolling_mean_{window}")
        cols.append(f"rolling_std_{window}")
    if config.get("calendar", True):
        cols.extend(["hour", "dayofweek", "month", "is_weekend", "quarter_hour"])
        if config.get("cyclical", True):
            cols.extend([
                "hour_sin", "hour_cos", "dow_sin", "dow_cos",
                "month_sin", "month_cos",
            ])
    cols.extend(["horizon_step", "horizon_hours"])
    cols.extend(config.get("external_columns", []))
    return cols


def build_direct_training_frame(
    df: pd.DataFrame,
    config: dict,
    covariates: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """Build direct multi-horizon training rows.

    The input df must contain columns: time, value.
    """
    horizon_steps = int(config.get("horizon_steps", 192))
    values = df["value"].to_numpy(dtype=float)
    times = pd.DatetimeIndex(df["time"])
    external_cols = config.get("external_columns", [])
    records = []

    for origin_idx in range(len(df)):
        max_target_idx = min(origin_idx + horizon_steps, len(df) - 1)
        if max_target_idx <= origin_idx:
            continue
        base = _history_features(values, origin_idx, config)
        if any(pd.isna(v) for v in base.values()):
            continue
        for target_idx in range(origin_idx + 1, max_target_idx + 1):
            horizon_step = target_idx - origin_idx
            target_time = pd.Timestamp(times[target_idx])
            row = dict(base)
            row["origin_time"] = pd.Timestamp(times[origin_idx])
            row["target_time"] = target_time
            row["horizon_step"] = horizon_step
            row["horizon_hours"] = horizon_step * 0.25
            _add_target_calendar_features(row, target_time, config)
            _merge_external(row, target_time, covariates, external_cols)
            row["target"] = float(values[target_idx])
            records.append(row)

    result = pd.DataFrame(records)
    feature_cols = _feature_columns(config)
    if not result.empty:
        result = result.dropna(subset=feature_cols + ["target"]).reset_index(drop=True)
    return result, feature_cols


def build_direct_prediction_frame(
    history: np.ndarray,
    start_time: pd.Timestamp,
    config: dict,
    horizon_steps: int,
    future_covariates: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """Build one prediction row per future horizon using the same true history."""
    history = np.asarray(history, dtype=float)
    origin_idx = len(history) - 1
    base = _history_features(history, origin_idx, config)
    if any(pd.isna(v) for v in base.values()):
        raise ValueError("History is too short for configured direct horizon lag features.")

    external_cols = config.get("external_columns", [])
    records = []
    start_time = pd.Timestamp(start_time)
    for h in range(1, horizon_steps + 1):
        target_time = start_time + pd.Timedelta(minutes=15 * (h - 1))
        row = dict(base)
        row["target_time"] = target_time
        row["horizon_step"] = h
        row["horizon_hours"] = h * 0.25
        _add_target_calendar_features(row, target_time, config)
        _merge_external(row, target_time, future_covariates, external_cols)
        records.append(row)

    feature_cols = _feature_columns(config)
    return pd.DataFrame(records), feature_cols
