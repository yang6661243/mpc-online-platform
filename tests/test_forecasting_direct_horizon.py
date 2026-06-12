"""Tests for Direct Horizon forecasting: feature construction, prediction, save/load."""

import numpy as np
import pandas as pd
import pytest

from models.forecasting.direct_features import (
    build_direct_training_frame,
    build_direct_prediction_frame,
)
from models.forecasting.load_forecaster import LoadForecaster


# ── Task 1: Direct training frames ────────────────────────────

class TestDirectTrainingFrame:
    """Tests for build_direct_training_frame."""

    def test_direct_training_rows_use_origin_history_not_future_lags(self):
        times = pd.date_range("2026-01-01 00:00", periods=220, freq="15min")
        values = np.arange(220, dtype=float) / 1000.0
        df = pd.DataFrame({"time": times, "value": values})
        config = {
            "lags": [1, 2, 3, 96],
            "rolling_windows": [96],
            "calendar": True,
            "cyclical": True,
            "horizon_steps": 4,
        }

        direct_df, feature_cols = build_direct_training_frame(df, config=config)

        row = direct_df[(direct_df["origin_time"] == times[100]) &
                        (direct_df["target_time"] == times[102])].iloc[0]

        assert row["horizon_step"] == 2
        assert row["lag_1"] == pytest.approx(values[100])
        assert row["lag_2"] == pytest.approx(values[99])
        assert row["lag_3"] == pytest.approx(values[98])
        assert row["target"] == pytest.approx(values[102])
        assert "horizon_step" in feature_cols
        assert "horizon_hours" in feature_cols


# ── Task 2: Direct prediction frames ──────────────────────────

class TestDirectPredictionFrame:
    """Tests for build_direct_prediction_frame."""

    def test_direct_prediction_frame_reuses_same_true_history_for_all_horizons(self):
        history = np.arange(700, dtype=float) / 1000.0
        start_time = pd.Timestamp("2026-01-08 00:00")
        config = {
            "lags": [1, 2, 96, 672],
            "rolling_windows": [96],
            "calendar": True,
            "cyclical": True,
            "external_columns": [],
        }

        pred_df, feature_cols = build_direct_prediction_frame(
            history=history,
            start_time=start_time,
            config=config,
            horizon_steps=192,
        )

        assert len(pred_df) == 192
        assert pred_df["horizon_step"].iloc[0] == 1
        assert pred_df["horizon_step"].iloc[-1] == 192
        assert pred_df["lag_1"].nunique() == 1
        assert pred_df["lag_1"].iloc[0] == pytest.approx(history[-1])
        assert pred_df["lag_96"].iloc[0] == pytest.approx(history[-96])
        assert pred_df["lag_96"].iloc[-1] == pytest.approx(history[-96])
        assert "horizon_step" in feature_cols


# ── Task 3/4: LoadForecaster Direct mode ──────────────────────

class TestLoadForecasterDirectMode:
    """Tests for LoadForecaster with lightgbm_direct_horizon model type."""

    def test_load_forecaster_direct_mode_predicts_full_horizon_without_recursion(self):
        times = pd.date_range("2026-01-01", periods=18 * 96, freq="15min")
        x = np.arange(len(times), dtype=float)
        values = 0.2 + 0.1 * np.sin(2 * np.pi * x / 96)
        values = values.astype(float)

        fc = LoadForecaster({
            "model": {"type": "lightgbm_direct_horizon", "horizon_steps": 24},
            "lightgbm": {
                "n_estimators": 40,
                "learning_rate": 0.1,
                "num_leaves": 15,
                "max_depth": 4,
                "min_data_in_leaf": 10,
                "subsample": 0.9,
                "colsample_bytree": 0.9,
                "random_state": 42,
                "verbose": -1,
                "early_stopping_rounds": 10,
            },
            "features": {
                "lags": [1, 2, 96],
                "rolling_windows": [96],
                "calendar": True,
                "cyclical": True,
                "external_columns": [],
            },
            "data": {"train_ratio": 0.8},
        })

        metrics = fc.train(values, times)
        pred = fc.predict(values[-200:], times[-1] + pd.Timedelta(minutes=15), horizon_steps=24)

        assert fc.is_fitted
        assert metrics["model_type"] == "lightgbm_direct_horizon"
        assert len(pred) == 24
        assert np.all(np.isfinite(pred))
        assert "horizon_step" in fc.feature_cols

    def test_direct_model_save_load_preserves_model_type(self, tmp_path):
        times = pd.date_range("2026-01-01", periods=18 * 96, freq="15min")
        values = 0.2 + 0.1 * np.sin(2 * np.pi * np.arange(len(times)) / 96)

        fc = LoadForecaster({
            "model": {"type": "lightgbm_direct_horizon", "horizon_steps": 12},
            "lightgbm": {
                "n_estimators": 30,
                "learning_rate": 0.1,
                "num_leaves": 15,
                "max_depth": 4,
                "min_data_in_leaf": 10,
                "subsample": 0.9,
                "colsample_bytree": 0.9,
                "random_state": 42,
                "verbose": -1,
                "early_stopping_rounds": 10,
            },
            "features": {
                "lags": [1, 2, 96],
                "rolling_windows": [96],
                "calendar": True,
                "cyclical": True,
                "external_columns": [],
            },
            "data": {"train_ratio": 0.8},
        })
        fc.train(values, times)
        path = tmp_path / "direct_model.joblib"
        fc.save(str(path))

        loaded = LoadForecaster.load(str(path))
        pred = loaded.predict(values[-200:], times[-1] + pd.Timedelta(minutes=15), horizon_steps=12)

        assert loaded.eval_metrics["model_type"] == "lightgbm_direct_horizon"
        assert len(pred) == 12
        assert "horizon_step" in loaded.feature_cols
