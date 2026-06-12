"""Load forecasting module for microgrid MPC integration.

Primary model: LightGBM recursive multi-step predictor.
Baselines: Seasonal Naive (t-96, t-672).
Covariates: public holidays, factory operations, weather.
Metrics: standard MAE/RMSE/MAPE + peak risk metrics.
Unified interface: forecast_load(history, start_time, horizon_steps=192) -> list[float]
"""

from .load_forecaster import LoadForecaster
from .baselines import seasonal_naive_t96, seasonal_naive_t672
from .data import load_data, load_data_as_series
from .metrics import compute_all_metrics, compute_peak_risk_metrics
from .covariates import build_calendar_covariates, align_covariates, covariates_for_horizon
from .direct_features import build_direct_training_frame, build_direct_prediction_frame

__all__ = [
    "LoadForecaster",
    "seasonal_naive_t96",
    "seasonal_naive_t672",
    "load_data",
    "load_data_as_series",
    "compute_all_metrics",
    "compute_peak_risk_metrics",
    "build_calendar_covariates",
    "align_covariates",
    "covariates_for_horizon",
    "build_direct_training_frame",
    "build_direct_prediction_frame",
]
