"""Feature engineering for load forecasting: lags, calendar, rolling stats."""

import numpy as np
import pandas as pd
from datetime import datetime


def add_lag_features(df: pd.DataFrame, lags: list[int]) -> pd.DataFrame:
    """Add lagged load columns: lag_{k} = value shifted by k steps.

    Args:
        df: DataFrame with 'value' column.
        lags: list of shift steps, e.g. [1, 2, 3, 96, 672].

    Returns:
        DataFrame with new 'lag_{k}' columns.
    """
    for k in lags:
        df[f'lag_{k}'] = df['value'].shift(k)
    return df


def add_calendar_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add calendar features from 'time' column.

    Creates: hour, dayofweek, month, is_weekend, quarter, sin/cos cyclical encodings.
    """
    dt = df['time']

    df['hour'] = dt.dt.hour + dt.dt.minute / 60.0
    df['dayofweek'] = dt.dt.dayofweek  # Monday=0, Sunday=6
    df['month'] = dt.dt.month
    df['is_weekend'] = (dt.dt.dayofweek >= 5).astype(float)
    df['quarter_hour'] = dt.dt.minute // 15

    # Cyclical encoding (2-column sin/cos for each periodic feature)
    df['hour_sin'] = np.sin(2 * np.pi * df['hour'] / 24)
    df['hour_cos'] = np.cos(2 * np.pi * df['hour'] / 24)
    df['dow_sin'] = np.sin(2 * np.pi * df['dayofweek'] / 7)
    df['dow_cos'] = np.cos(2 * np.pi * df['dayofweek'] / 7)
    df['month_sin'] = np.sin(2 * np.pi * df['month'] / 12)
    df['month_cos'] = np.cos(2 * np.pi * df['month'] / 12)

    return df


def add_rolling_features(df: pd.DataFrame, windows: list[int]) -> pd.DataFrame:
    """Add rolling statistics of past values.

    Creates: rolling_mean_{w}, rolling_std_{w} for each window in windows.
    Note: uses shift(1) to avoid leaking current value.
    """
    for w in windows:
        df[f'rolling_mean_{w}'] = df['value'].shift(1).rolling(w, min_periods=1).mean()
        df[f'rolling_std_{w}'] = df['value'].shift(1).rolling(w, min_periods=1).std().fillna(0)
    return df


def build_features(df: pd.DataFrame, config: dict | None = None,
                    covariates: pd.DataFrame | None = None) -> tuple[pd.DataFrame, list[str]]:
    """Build all features from a DataFrame with 'time' and 'value' columns.

    Args:
        df: DataFrame with columns time (datetime) and value (load ratio).
        config: optional feature config dict with keys:
            lags (list), rolling_windows (list), calendar (bool), cyclical (bool),
            external_columns (list).
        covariates: optional DataFrame with 'time' column and covariate feature columns.

    Returns:
        (features_df, feature_columns) — features_df is the complete DataFrame
        including the target 'value' column, ready for model training.
        feature_columns lists the predictor column names (excludes 'time' and 'value').
    """
    if config is None:
        config = {
            'lags': [1, 2, 3, 96, 672],
            'rolling_windows': [96],
            'calendar': True,
            'cyclical': True,
        }

    df = df.copy()

    # Lag features (order matters: do before rolling so rolling uses raw value)
    df = add_lag_features(df, config.get('lags', []))

    # Rolling features
    df = add_rolling_features(df, config.get('rolling_windows', []))

    # Calendar features
    if config.get('calendar', True):
        df = add_calendar_features(df)

    # External covariate features
    external_cols = config.get('external_columns', [])
    if external_cols:
        if covariates is None:
            raise ValueError(
                f"Config requires external_columns {external_cols} but no covariates provided."
            )
        missing = [c for c in external_cols if c not in covariates.columns]
        if missing:
            raise ValueError(
                f"External column(s) {missing} not found in covariates. "
                f"Available: {list(covariates.columns)}"
            )
        # Merge covariates by time (left join, keep only external columns)
        # Skip columns already in df to avoid _x/_y suffixes
        new_cols = [c for c in external_cols if c not in df.columns]
        if new_cols:
            cov_subset = covariates[['time'] + new_cols].copy()
            df = df.merge(cov_subset, on='time', how='left')
            # Check for nulls in merged covariate columns (missing covariate rows)
            null_cols = [c for c in new_cols if df[c].isnull().any()]
            if null_cols:
                null_counts = {c: int(df[c].isnull().sum()) for c in null_cols}
                raise ValueError(
                    f"Covariate column(s) have null values after merge: {null_counts}. "
                    f"Covariate data does not cover the full time range of training data. "
                    f"Training time range: [{df['time'].min()}, {df['time'].max()}]. "
                    f"Covariates time range: [{covariates['time'].min()}, {covariates['time'].max()}]."
                )

    # All predictor columns (exclude time and target value, keep lag/rolling/calendar)
    exclude = {'time', 'value'}
    feature_cols = [c for c in df.columns if c not in exclude]

    return df, feature_cols


def prepare_prediction_row(history: np.ndarray, timestamps: pd.DatetimeIndex,
                           future_time: pd.Timestamp, config: dict | None = None,
                           feature_cols: list[str] | None = None,
                           future_covariates: pd.DataFrame | None = None) -> np.ndarray:
    """Prepare a single feature row for predicting one future step.

    Used in recursive prediction: after predicting step t+1, the predicted value
    is appended to history, and this function is called again for step t+2.

    Args:
        history: 1-D array of recent load values (oldest to newest),
                 must have at least max(config['lags']) elements.
        timestamps: DatetimeIndex matching history (same length).
        future_time: timestamp of the step to predict.
        config: feature config dict.
        feature_cols: explicit column order matching training data.
                      If provided, the returned array follows this exact order.
        future_covariates: optional DataFrame with 'time' column and covariate
                          values for the prediction horizon. Must contain all
                          columns listed in config['external_columns'].

    Returns:
        1-D numpy array of feature values for this single prediction.
    """
    if config is None:
        config = {
            'lags': [1, 2, 3, 96, 672],
            'rolling_windows': [96],
            'calendar': True,
            'cyclical': True,
        }

    # Accept both datetime.datetime and pd.Timestamp
    if isinstance(future_time, datetime):
        future_time = pd.Timestamp(future_time)

    lags = config.get('lags', [])
    n = len(history)
    feats = {}

    # Lag features: pick from end of history
    for k in lags:
        if k <= n:
            feats[f'lag_{k}'] = history[n - k]
        else:
            feats[f'lag_{k}'] = 0.0  # fallback for insufficient history

    # Rolling features (computed over recent history excluding the future)
    for w in config.get('rolling_windows', []):
        window_vals = history[max(0, n - w):]
        feats[f'rolling_mean_{w}'] = np.mean(window_vals) if len(window_vals) > 0 else 0.0
        feats[f'rolling_std_{w}'] = np.std(window_vals) if len(window_vals) > 1 else 0.0

    # Calendar features
    if config.get('calendar', True):
        h = future_time.hour + future_time.minute / 60.0
        feats['hour'] = h
        feats['dayofweek'] = future_time.dayofweek
        feats['month'] = future_time.month
        feats['is_weekend'] = 1.0 if future_time.dayofweek >= 5 else 0.0
        feats['quarter_hour'] = future_time.minute // 15

        if config.get('cyclical', True):
            feats['hour_sin'] = np.sin(2 * np.pi * h / 24)
            feats['hour_cos'] = np.cos(2 * np.pi * h / 24)
            feats['dow_sin'] = np.sin(2 * np.pi * future_time.dayofweek / 7)
            feats['dow_cos'] = np.cos(2 * np.pi * future_time.dayofweek / 7)
            feats['month_sin'] = np.sin(2 * np.pi * future_time.month / 12)
            feats['month_cos'] = np.cos(2 * np.pi * future_time.month / 12)

    # External covariate features
    external_cols = config.get('external_columns', [])
    if external_cols:
        if future_covariates is None:
            raise ValueError(
                f"Config requires external_columns {external_cols} "
                f"but future_covariates is None."
            )
        cov_row = future_covariates[future_covariates['time'] == future_time]
        if len(cov_row) == 0:
            raise ValueError(
                f"No covariate row found for future_time={future_time}"
            )
        for col in external_cols:
            if col in cov_row.columns:
                feats[col] = float(cov_row[col].iloc[0])
            else:
                raise ValueError(
                    f"External column '{col}' not found in future_covariates"
                )

    # Build array matching training feature columns
    if feature_cols is not None:
        # Explicit order from trained model — use it directly
        return np.array([feats.get(c, 0.0) for c in feature_cols], dtype=np.float64)

    # Fallback: canonical order (lags first, then rolling, then calendar)
    ordered_cols = []
    for k in sorted(lags):
        ordered_cols.append(f'lag_{k}')
    for w in config.get('rolling_windows', []):
        ordered_cols.append(f'rolling_mean_{w}')
        ordered_cols.append(f'rolling_std_{w}')
    calendar_keys = ['hour', 'dayofweek', 'month', 'is_weekend', 'quarter_hour',
                     'hour_sin', 'hour_cos', 'dow_sin', 'dow_cos', 'month_sin', 'month_cos']
    for ck in calendar_keys:
        if ck in feats:
            ordered_cols.append(ck)
    # External columns at the end
    for col in external_cols:
        if col in feats:
            ordered_cols.append(col)

    return np.array([feats[c] for c in ordered_cols], dtype=np.float64)
