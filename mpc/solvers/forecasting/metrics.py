"""Evaluation metrics for load forecasting."""

import numpy as np
import pandas as pd
from typing import Optional


def compute_all_metrics(actual: np.ndarray, predicted: np.ndarray,
                        name: str = "model") -> dict:
    """Compute MAE, RMSE, MAPE for a single prediction window.

    Args:
        actual: ground truth load values.
        predicted: forecasted load values (same length).
        name: label for this prediction set.

    Returns:
        dict with name, mae, rmse, mape keys.
    """
    actual = np.asarray(actual, dtype=np.float64)
    predicted = np.asarray(predicted, dtype=np.float64)

    errors = actual - predicted
    mae = float(np.mean(np.abs(errors)))
    rmse = float(np.sqrt(np.mean(errors ** 2)))

    denom = np.where(np.abs(actual) > 1e-6, np.abs(actual), np.nan)
    mape = float(np.nanmean(np.abs(errors) / denom) * 100)

    return {
        'name': name,
        'mae': mae,
        'rmse': rmse,
        'mape': mape,
        'max_error': float(np.max(np.abs(errors))),
        'n_samples': len(actual),
    }


def compute_horizon_segmented_metrics(actual: np.ndarray, predicted: np.ndarray,
                                      step_minutes: int = 15) -> pd.DataFrame:
    """Break down prediction errors by horizon segment.

    Args:
        actual: 1-D array of actual values for one forecast window.
        predicted: 1-D array of predicted values (same length).
        step_minutes: time step in minutes.

    Returns:
        DataFrame with columns: horizon_hours, mae, rmse, mape.
    """
    horizon_steps = len(actual)
    records = []
    for h in range(horizon_steps):
        err = actual[h] - predicted[h]
        records.append({
            'horizon_step': h,
            'horizon_hours': h * step_minutes / 60.0,
            'error': err,
            'abs_error': abs(err),
        })
    df = pd.DataFrame(records)

    # Group by 6-hour segments
    segment_hours = 6
    segment_steps = segment_hours * 60 // step_minutes
    results = []
    for seg_start in range(0, horizon_steps, segment_steps):
        seg_end = min(seg_start + segment_steps, horizon_steps)
        seg = df.iloc[seg_start:seg_end]
        h_start = seg_start * step_minutes / 60
        h_end = seg_end * step_minutes / 60
        if len(seg) == 0:
            continue
        results.append({
            'horizon_segment': f'{h_start:.0f}-{h_end:.0f}h',
            'mae': float(seg['abs_error'].mean()),
            'rmse': float(np.sqrt((seg['error'] ** 2).mean())),
            'mape': float((seg['abs_error'] / actual[seg_start:seg_end].clip(1e-6)).mean() * 100)
            if np.any(np.abs(actual[seg_start:seg_end]) > 1e-6) else np.nan,
        })
    return pd.DataFrame(results)


def compute_peak_risk_metrics(actual: np.ndarray, predicted: np.ndarray,
                              peak_quantile: float = 0.90) -> dict:
    """Compute peak-related error metrics focused on under-forecast risk.

    Standard MAE dilutes peak underestimation across many low-load periods.
    These metrics isolate the tail risk that matters for MPC peak shaving.

    Args:
        actual: ground truth load values.
        predicted: forecast load values (same length).
        peak_quantile: quantile threshold defining "peak period" (default 0.90).

    Returns:
        dict with keys:
            positive_error_p80: P80 of positive errors (under-forecasts).
            positive_error_p90: P90 of positive errors.
            max_under_forecast: largest under-forecast error.
            actual_peak_error: prediction error at the actual peak point.
            peak_period_mae: MAE during periods above the peak_quantile threshold.
    """
    actual = np.asarray(actual, dtype=np.float64)
    predicted = np.asarray(predicted, dtype=np.float64)

    errors = actual - predicted  # positive = under-forecast

    # Positive errors (under-forecasts only)
    positive_errors = errors[errors > 0]

    positive_error_p80 = float(np.percentile(positive_errors, 80)) if len(positive_errors) > 0 else 0.0
    positive_error_p90 = float(np.percentile(positive_errors, 90)) if len(positive_errors) > 0 else 0.0
    max_under = max(0.0, float(np.max(errors)))  # floor at 0: over-forecast is not under-forecast

    # Error at the actual peak
    peak_idx = int(np.argmax(actual))
    actual_peak_error = float(errors[peak_idx])

    # High-load period MAE
    threshold = np.quantile(actual, peak_quantile)
    peak_mask = actual >= threshold
    if peak_mask.sum() > 0:
        peak_period_mae = float(np.mean(np.abs(errors[peak_mask])))
    else:
        peak_period_mae = float('nan')

    return {
        'positive_error_p80': positive_error_p80,
        'positive_error_p90': positive_error_p90,
        'max_under_forecast': max_under,
        'actual_peak_error': actual_peak_error,
        'peak_period_mae': peak_period_mae,
        'peak_quantile': peak_quantile,
        'n_peak_points': int(peak_mask.sum()),
        'n_positive_errors': len(positive_errors),
    }


def compare_models(model_metrics: list[dict]) -> pd.DataFrame:
    """Build a comparison table from multiple model metric dicts.

    Args:
        model_metrics: list of dicts, each from compute_all_metrics.

    Returns:
        DataFrame sorted by MAE.
    """
    df = pd.DataFrame(model_metrics)
    df = df.sort_values('mae').reset_index(drop=True)
    return df
