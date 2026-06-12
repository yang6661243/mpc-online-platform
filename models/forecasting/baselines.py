"""Seasonal Naive baselines for load forecasting.

Baseline 1: t-96  — same time yesterday (96 steps = 1 day at 15-min resolution)
Baseline 2: t-672 — same time last week (672 steps = 7 days)
"""

import numpy as np


def seasonal_naive_t96(history: np.ndarray, horizon_steps: int = 192) -> np.ndarray:
    """Predict using yesterday's same time.

    forecast[t] = history[-96 + t] for t in 0..horizon_steps-1.
    If history is insufficient, falls back to the earliest available value.

    Args:
        history: 1-D array of recent load values (ratios, oldest to newest).
                 Needs at least 96 steps for a full 48h forecast.
        horizon_steps: number of future steps to predict (default 192 = 48h).

    Returns:
        np.ndarray of length horizon_steps.
    """
    n = len(history)
    forecast = np.zeros(horizon_steps)
    for i in range(horizon_steps):
        idx = n - 96 + i
        if idx >= 0 and idx < n:
            forecast[i] = history[idx]
        elif idx < 0:
            forecast[i] = history[0]  # fallback
        else:
            forecast[i] = history[-1]  # fallback
    return forecast


def seasonal_naive_t672(history: np.ndarray, horizon_steps: int = 192) -> np.ndarray:
    """Predict using last week's same time.

    forecast[t] = history[-672 + t] for t in 0..horizon_steps-1.

    Args:
        history: 1-D array of recent load values (ratios, oldest to newest).
                 Needs at least 672 + horizon_steps steps.
        horizon_steps: number of future steps to predict (default 192 = 48h).

    Returns:
        np.ndarray of length horizon_steps.
    """
    n = len(history)
    forecast = np.zeros(horizon_steps)
    for i in range(horizon_steps):
        idx = n - 672 + i
        if idx >= 0 and idx < n:
            forecast[i] = history[idx]
        elif idx < 0:
            forecast[i] = history[0]
        else:
            forecast[i] = history[-1]
    return forecast


def evaluate_baseline(forecast_fn, history: np.ndarray, actual: np.ndarray) -> dict:
    """Evaluate a baseline forecaster on a test window.

    Args:
        forecast_fn: function(history, horizon_steps) -> forecast array.
        history: full history available before the test window.
        actual: ground truth for the test window.

    Returns:
        dict with mae, rmse, mape keys.
    """
    forecast = forecast_fn(history, len(actual))
    errors = actual - forecast
    mae = float(np.mean(np.abs(errors)))
    rmse = float(np.sqrt(np.mean(errors ** 2)))
    # MAPE with protection against divide by zero
    denom = np.where(np.abs(actual) > 1e-6, np.abs(actual), np.nan)
    mape = float(np.nanmean(np.abs(errors) / denom) * 100)
    return {'mae': mae, 'rmse': rmse, 'mape': mape, 'name': forecast_fn.__name__}
