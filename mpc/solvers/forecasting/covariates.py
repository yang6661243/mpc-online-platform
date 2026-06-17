"""Covariate module for load forecasting: public holidays, factory operations, weather.

Provides:
    build_calendar_covariates  — generate holiday/factory calendar features
    align_covariates           — align hourly weather to 15-min timeline
    covariates_for_horizon     — extract covariates for a prediction horizon
"""

import numpy as np
import pandas as pd


def build_calendar_covariates(timestamps, public_calendar=None, factory_calendar=None):
    """Generate public holiday, adjusted-workday, and optional factory operation features.

    Args:
        timestamps: pd.DatetimeIndex of target times.
        public_calendar: optional DataFrame with 'date' column and a type column
            ('day_type' or 'holiday'). Rows with value 'holiday' mark public holidays.
        factory_calendar: optional DataFrame with columns:
            date, is_factory_workday, is_factory_overtime, is_factory_shutdown.

    Returns:
        DataFrame with 'time' column and covariate columns. All rows match timestamps.

    Rules:
        1. is_weekend is always generated from natural date.
        2. is_public_holiday and is_adjusted_workday come from the public calendar.
        3. is_day_before/after are derived from public holidays.
        4. Factory features only when factory_calendar is provided.
        5. Public calendar does NOT infer factory production status.
    """
    df = pd.DataFrame({'time': timestamps})

    # Weekend from natural date (always)
    df['is_weekend'] = (df['time'].dt.dayofweek >= 5).astype(int)

    # Public holiday features
    if public_calendar is not None:
        cal = public_calendar.copy()
        cal['date'] = pd.to_datetime(cal['date'])

        # Determine the column that marks holidays
        if 'day_type' in cal.columns:
            day_type = cal['day_type'].astype(str).str.lower()
            holiday_dates = set(cal.loc[day_type == 'holiday', 'date'].dt.date)
            adjusted_workday_dates = set(cal.loc[day_type == 'workday', 'date'].dt.date)
        elif 'holiday' in cal.columns:
            holiday_type = cal['holiday'].astype(str).str.lower()
            holiday_dates = set(cal.loc[holiday_type == 'holiday', 'date'].dt.date)
            adjusted_workday_dates = set(cal.loc[holiday_type == 'workday', 'date'].dt.date)
        else:
            holiday_dates = set()
            adjusted_workday_dates = set()

        df['is_public_holiday'] = df['time'].dt.date.isin(holiday_dates).astype(int)
        df['is_adjusted_workday'] = (
            df['time'].dt.date.isin(adjusted_workday_dates)
        ).astype(int)

        # Day before holiday: date + 1 day is in holiday_dates
        df['is_day_before_holiday'] = (
            (df['time'] + pd.Timedelta(days=1)).dt.date.isin(holiday_dates)
        ).astype(int)

        # Day after holiday: date - 1 day is in holiday_dates
        df['is_day_after_holiday'] = (
            (df['time'] - pd.Timedelta(days=1)).dt.date.isin(holiday_dates)
        ).astype(int)

    # Factory operation features (only when explicitly provided)
    if factory_calendar is not None:
        fcal = factory_calendar.copy()
        fcal['date'] = pd.to_datetime(fcal['date'])

        for col in ['is_factory_workday', 'is_factory_overtime', 'is_factory_shutdown']:
            if col in fcal.columns:
                mapping = fcal.set_index('date')[col].to_dict()
                df[col] = df['time'].dt.date.map(
                    lambda d: int(mapping.get(pd.Timestamp(d), 0))
                )

    return df


def align_covariates(timestamps, covariates, numeric_columns):
    """Align hourly or other-frequency data to a 15-minute timeline via linear interpolation.

    Args:
        timestamps: pd.DatetimeIndex of target 15-min times.
        covariates: DataFrame with 'time' column and numeric data columns.
        numeric_columns: list of column names to interpolate.

    Returns:
        DataFrame with 'time' and interpolated numeric columns, aligned to timestamps.

    Rules:
        1. Linear interpolation from source frequency to 15-min.
        2. Only interpolates within the available data range (no extrapolation).
        3. Error if required numeric columns are missing.
        4. No silent zero-filling of missing data.
    """
    cov = covariates.copy()
    cov['time_num'] = cov['time'].astype('int64')

    # Validate required columns exist
    missing = [c for c in numeric_columns if c not in cov.columns]
    if missing:
        raise ValueError(
            f"Required numeric column(s) not found in covariates: {missing}. "
            f"Available columns: {list(covariates.columns)}"
        )

    # Check timestamps are within the covariate time range
    t_min = cov['time'].min()
    t_max = cov['time'].max()
    if timestamps.min() < t_min or timestamps.max() > t_max:
        raise ValueError(
            f"Timestamps [{timestamps.min()}, {timestamps.max()}] are outside "
            f"covariate data range [{t_min}, {t_max}]. "
            "Cannot extrapolate beyond available data."
        )

    target_num = timestamps.astype('int64').values
    source_num = cov['time_num'].values

    result = pd.DataFrame({'time': timestamps})

    for col in numeric_columns:
        if col == 'weather_code':
            stepped = pd.merge_asof(
                pd.DataFrame({'time': timestamps}).sort_values('time'),
                cov[['time', col]].sort_values('time'),
                on='time',
                direction='backward',
            )
            result[col] = stepped[col].astype(int).values
        else:
            result[col] = np.interp(target_num, source_num, cov[col].values)

    return result


def covariates_for_horizon(timestamps, covariates, required_columns):
    """Extract covariate rows for a specific MPC future prediction horizon.

    Args:
        timestamps: pd.DatetimeIndex for the prediction horizon.
        covariates: DataFrame from build_calendar_covariates or align_covariates
                    (must have 'time' column).
        required_columns: list of column names that must be present.

    Returns:
        DataFrame with rows matching timestamps, containing only the required columns.

    Raises:
        ValueError if any required column is missing from covariates.
    """
    missing = [c for c in required_columns if c not in covariates.columns]
    if missing:
        raise ValueError(
            f"Missing required covariate column(s): {missing}. "
            f"Available: {list(covariates.columns)}"
        )

    # Filter to the requested timestamps via merge on time
    target_df = pd.DataFrame({'time': timestamps})
    merged = target_df.merge(covariates, on='time', how='left')

    # Verify no missing rows after merge
    if merged[required_columns].isnull().any().any():
        null_cols = [c for c in required_columns if merged[c].isnull().any()]
        raise ValueError(
            f"Some required covariate values are null for columns: {null_cols}. "
            "Ensure covariates cover the full horizon time range."
        )

    return merged[['time'] + required_columns].reset_index(drop=True)
