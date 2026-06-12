"""LightGBM recursive multi-step load forecaster.

Unified interface:
    forecast_load(history, start_time, horizon_steps=192) -> list[float]
"""

import os
import yaml
import joblib
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional

from lightgbm import LGBMRegressor, early_stopping, log_evaluation

from .features import build_features, prepare_prediction_row
from .direct_features import build_direct_training_frame, build_direct_prediction_frame
from .data import load_data_as_series

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class LoadForecaster:
    """LightGBM recursive multi-step load forecaster.

    Trains a single LGBMRegressor to predict the next 15-min load value.
    Multi-step forecasting uses recursive roll-out: each predicted value is
    appended to history and used as a lag feature for the next prediction.

    Parameters
    ----------
    config : dict or str
        Config dict or path to config YAML.
    """

    def __init__(self, config: dict | str | None = None):
        if config is None:
            config = os.path.join(ROOT, 'models', 'forecasting', 'config.yaml')
        if isinstance(config, str):
            with open(config, encoding='utf-8') as f:
                self._cfg = yaml.safe_load(f)
        else:
            self._cfg = config

        lgb_cfg = self._cfg.get('lightgbm', {})
        self._model = LGBMRegressor(
            n_estimators=lgb_cfg.get('n_estimators', 500),
            learning_rate=lgb_cfg.get('learning_rate', 0.05),
            num_leaves=lgb_cfg.get('num_leaves', 63),
            max_depth=lgb_cfg.get('max_depth', 8),
            min_data_in_leaf=lgb_cfg.get('min_data_in_leaf', 50),
            subsample=lgb_cfg.get('subsample', 0.8),
            colsample_bytree=lgb_cfg.get('colsample_bytree', 0.8),
            random_state=lgb_cfg.get('random_state', 42),
            verbose=lgb_cfg.get('verbose', -1),
            force_col_wise=True,  # avoid threading issues on Windows
        )

        self._model_type = self._cfg.get('model', {}).get('type', 'lightgbm_recursive')
        self._feature_config = self._cfg.get('features', {})
        self._feature_cols: list[str] = []
        self._fitted = False
        self._eval_metrics: dict = {}

    # ── training ──────────────────────────────────────────────

    def train(self, load_ratio: np.ndarray | None = None,
              timestamps: pd.DatetimeIndex | None = None,
              config_path: str | None = None,
              scenario_file: str | None = None,
              covariates: pd.DataFrame | None = None) -> dict:
        """Train the LightGBM model on load ratio data.

        Data source: provide (load_ratio, timestamps) arrays directly,
        or a config_path/scenario_file to load from disk.

        Args:
            load_ratio: 1-D array of load ratio values.
            timestamps: DatetimeIndex matching load_ratio.
            config_path: path to YAML config.
            scenario_file: path to scenario Excel file.
            covariates: optional DataFrame with 'time' column and covariate
                        feature values for training.
        """
        if load_ratio is None:
            load_ratio, timestamps = load_data_as_series(
                config_path=config_path, scenario_file=scenario_file)

        # Build feature DataFrame
        df = pd.DataFrame({'time': timestamps, 'value': load_ratio})

        if self._model_type == 'lightgbm_direct_horizon':
            direct_cfg = dict(self._feature_config)
            direct_cfg['horizon_steps'] = self._cfg.get('model', {}).get('horizon_steps', 192)
            df, feature_cols = build_direct_training_frame(
                df, config=direct_cfg, covariates=covariates)
            target_col = 'target'
        else:
            df, feature_cols = build_features(df, self._feature_config,
                                              covariates=covariates)
            df = df.dropna(subset=feature_cols).reset_index(drop=True)
            target_col = 'value'

        self._feature_cols = feature_cols

        # Time-based split: first train_ratio for training
        train_ratio = self._cfg.get('data', {}).get('train_ratio', 0.8)
        split_idx = int(len(df) * train_ratio)

        train_df = df.iloc[:split_idx]
        val_df = df.iloc[split_idx:]

        X_train = train_df[feature_cols].values
        y_train = train_df[target_col].values
        X_val = val_df[feature_cols].values
        y_val = val_df[target_col].values

        lgb_cfg = self._cfg.get('lightgbm', {})
        callbacks = [
            early_stopping(lgb_cfg.get('early_stopping_rounds', 50), verbose=False),
            log_evaluation(100),
        ]

        self._model.fit(
            X_train, y_train,
            eval_set=[(X_train, y_train), (X_val, y_val)],
            eval_names=['train', 'val'],
            eval_metric='l1',
            callbacks=callbacks,
        )

        self._fitted = True

        # Evaluate on validation set
        y_pred = self._model.predict(X_val)
        errors = y_val - y_pred
        val_mae = float(np.mean(np.abs(errors)))
        val_rmse = float(np.sqrt(np.mean(errors ** 2)))
        denom = np.where(np.abs(y_val) > 1e-6, np.abs(y_val), np.nan)
        val_mape = float(np.nanmean(np.abs(errors) / denom) * 100)

        self._eval_metrics = {
            'model_type': self._model_type,
            'train_mae': float(np.mean(np.abs(y_train - self._model.predict(X_train)))),
            'val_mae': val_mae,
            'val_rmse': val_rmse,
            'val_mape': val_mape,
            'n_features': len(feature_cols),
            'n_train': len(train_df),
            'n_val': len(val_df),
            'feature_importance': dict(zip(feature_cols,
                                            self._model.feature_importances_.tolist())),
        }

        return self._eval_metrics

    # ── prediction ────────────────────────────────────────────

    def predict(self, history: np.ndarray, start_time: datetime | pd.Timestamp,
                horizon_steps: int = 192,
                future_covariates: pd.DataFrame | None = None) -> np.ndarray:
        """Recursive multi-step load forecast.

        Args:
            history: 1-D array of past load ratio values (oldest first).
                     Must include at least max(lags) + a few steps.
            start_time: datetime of the first predicted step.
            horizon_steps: number of 15-min steps to forecast (default 192 = 48h).
            future_covariates: optional DataFrame with 'time' column and covariate
                              values covering the prediction horizon.

        Returns:
            np.ndarray of length horizon_steps with predicted load ratios.
        """
        if not self._fitted:
            raise RuntimeError("Model not trained. Call train() first.")

        external_cols = self._feature_config.get('external_columns', [])
        if external_cols and future_covariates is None:
            raise ValueError(
                f"Model trained with external_columns {external_cols} "
                f"but future_covariates is None. Provide future covariate values."
            )

        # Direct mode: one-shot prediction for all horizon steps
        if self._model_type == 'lightgbm_direct_horizon':
            direct_cfg = dict(self._feature_config)
            pred_df, _ = build_direct_prediction_frame(
                history=np.asarray(history, dtype=np.float64).copy(),
                start_time=pd.Timestamp(start_time),
                config=direct_cfg,
                horizon_steps=horizon_steps,
                future_covariates=future_covariates,
            )
            X = pred_df[self._feature_cols]
            forecast = self._model.predict(X)
            return np.clip(np.asarray(forecast, dtype=np.float64), 0.0, 1.0)

        history = np.asarray(history, dtype=np.float64).copy()
        timestamps = pd.date_range(
            start=start_time - timedelta(minutes=15 * (len(history) - 1)),
            periods=len(history), freq='15min'
        )

        forecast = np.zeros(horizon_steps)
        for i in range(horizon_steps):
            future_ts = start_time + timedelta(minutes=15 * i)
            feats = prepare_prediction_row(history, timestamps, future_ts,
                                           self._feature_config,
                                           feature_cols=self._feature_cols,
                                           future_covariates=future_covariates)
            # LightGBM expects feature names — use DataFrame single row
            feats_df = pd.DataFrame([feats], columns=self._feature_cols)
            pred = self._model.predict(feats_df)[0]
            pred = float(np.clip(pred, 0.0, 1.0))  # ratio bounds
            forecast[i] = pred

            # Append prediction to history for next recursive step
            history = np.append(history, pred)
            timestamps = timestamps.append(pd.DatetimeIndex([future_ts]))

        return forecast

    def forecast_load(self, history: np.ndarray, start_time: datetime | pd.Timestamp,
                      horizon_steps: int = 192,
                      future_covariates: pd.DataFrame | None = None) -> list[float]:
        """MPC-compatible interface: returns list[float].

        Args:
            history: past load ratio values (oldest first).
            start_time: datetime of first predicted step.
            horizon_steps: forecast horizon in 15-min steps.
            future_covariates: optional covariate values for prediction horizon.

        Returns:
            list of predicted load ratios.
        """
        return self.predict(history, start_time, horizon_steps,
                            future_covariates=future_covariates).tolist()

    # ── evaluation ────────────────────────────────────────────

    def evaluate(self, load_ratio: np.ndarray | None = None,
                 timestamps: pd.DatetimeIndex | None = None,
                 config_path: str | None = None,
                 scenario_file: str | None = None,
                 backtest_every_steps: int = 96,
                 covariates: pd.DataFrame | None = None) -> dict:
        """Time-series backtest: rolling 48h forecasts on test data.

        Splits data at train_ratio, then every backtest_every_steps performs
        a 48h forecast and compares to actual. Returns error metrics broken
        down by forecast horizon.

        Args:
            load_ratio: full load ratio array.
            timestamps: full timestamp array.
            config_path: alternative config path.
            scenario_file: alternative scenario file.
            backtest_every_steps: interval between backtest start points.
            covariates: optional DataFrame of covariate values covering all data.

        Returns:
            dict with overall metrics and horizon-segmented errors.
        """
        if load_ratio is None:
            load_ratio, timestamps = load_data_as_series(
                config_path=config_path, scenario_file=scenario_file)

        train_ratio = self._cfg.get('data', {}).get('train_ratio', 0.8)
        split_idx = int(len(load_ratio) * train_ratio)
        horizon_steps = self._cfg.get('model', {}).get('horizon_steps', 192)

        test_values = load_ratio[split_idx:]
        test_times = timestamps[split_idx:]

        all_errors = []  # list of (horizon_step, error) tuples

        for start in range(0, len(test_values) - horizon_steps, backtest_every_steps):
            hist = load_ratio[:split_idx + start]
            start_time = test_times[start]
            actual = test_values[start:start + horizon_steps]

            try:
                # Extract future covariates for this backtest window
                future_cov = None
                if covariates is not None:
                    future_ts = pd.date_range(
                        start=start_time, periods=horizon_steps, freq='15min'
                    )
                    future_cov = covariates[
                        covariates['time'].isin(future_ts)
                    ].reset_index(drop=True)

                pred = self.predict(hist, start_time, horizon_steps,
                                    future_covariates=future_cov)
                for h, (a, p) in enumerate(zip(actual, pred)):
                    all_errors.append({'horizon': h, 'error': a - p, 'actual': a, 'predicted': p})
            except ValueError:
                # Hard errors (missing covariates, etc.) — propagate
                raise
            except RuntimeError:
                # Model not fitted — propagate
                raise
            except Exception:
                continue  # transient errors in a single backtest window

        if not all_errors:
            return {'error': 'No backtest windows completed'}

        err_df = pd.DataFrame(all_errors)

        # Overall metrics
        abs_err = err_df['error'].abs()
        overall_mae = float(abs_err.mean())
        overall_rmse = float(np.sqrt((err_df['error'] ** 2).mean()))
        denom = err_df['actual'].abs().replace(0, np.nan)
        overall_mape = float((abs_err / denom).mean() * 100)

        # By horizon segment
        segments = {'0-6h': (0, 24), '6-12h': (24, 48), '12-24h': (48, 96),
                    '24-36h': (96, 144), '36-48h': (144, 192)}
        horizon_metrics = {}
        for seg_name, (h0, h1) in segments.items():
            seg = err_df[(err_df['horizon'] >= h0) & (err_df['horizon'] < h1)]
            if len(seg) > 0:
                seg_abs = seg['error'].abs()
                horizon_metrics[seg_name] = {
                    'mae': float(seg_abs.mean()),
                    'rmse': float(np.sqrt((seg['error'] ** 2).mean())),
                }

        return {
            'overall_mae': overall_mae,
            'overall_rmse': overall_rmse,
            'overall_mape': overall_mape,
            'n_backtest_windows': len(range(0, len(test_values) - horizon_steps, backtest_every_steps)),
            'by_horizon': horizon_metrics,
        }

    # ── persistence ───────────────────────────────────────────

    def save(self, path: str | None = None) -> str:
        """Save model to .joblib file."""
        if path is None:
            model_dir = os.path.join(ROOT, self._cfg.get('output', {}).get('model_dir', 'outputs/models'))
            os.makedirs(model_dir, exist_ok=True)
            model_name = self._cfg.get('output', {}).get('model_name', 'load_forecaster.joblib')
            path = os.path.join(model_dir, model_name)
        joblib.dump({
            'model': self._model,
            'feature_cols': self._feature_cols,
            'feature_config': self._feature_config,
            'config': self._cfg,
            'eval_metrics': self._eval_metrics,
            'model_type': self._model_type,
        }, path)
        return path

    @classmethod
    def load(cls, path: str) -> 'LoadForecaster':
        """Load model from .joblib file.

        Backward compatible: old models without 'config' or 'feature_config'
        keys are loaded with sensible defaults.
        """
        data = joblib.load(path)
        obj = cls.__new__(cls)
        # Support old joblib format that may lack 'config' key
        obj._cfg = data.get('config') or {}
        obj._model = data['model']
        obj._feature_cols = data.get('feature_cols', [])
        obj._feature_config = data.get('feature_config', {})
        obj._eval_metrics = data.get('eval_metrics', {})
        obj._model_type = data.get('model_type') or obj._cfg.get('model', {}).get('type', 'lightgbm_recursive')
        obj._fitted = True
        # If _cfg is empty, try to reconstruct minimal config from feature_config
        if not obj._cfg and obj._feature_config:
            obj._cfg = {'features': obj._feature_config}
        return obj

    # ── properties ────────────────────────────────────────────

    @property
    def is_fitted(self) -> bool:
        return self._fitted

    @property
    def eval_metrics(self) -> dict:
        return self._eval_metrics

    @property
    def feature_importance(self) -> dict:
        if not self._fitted or not self._feature_cols:
            return {}
        return dict(zip(self._feature_cols, self._model.feature_importances_.tolist()))

    @property
    def feature_cols(self) -> list[str]:
        return self._feature_cols
