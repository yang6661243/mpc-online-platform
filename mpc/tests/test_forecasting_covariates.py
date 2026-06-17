"""Tests for covariates module: holiday, factory calendar, weather alignment,
and integration with feature construction."""

import numpy as np
import pandas as pd
import pytest
from datetime import datetime

from mpc.solvers.forecasting.covariates import (
    build_calendar_covariates,
    align_covariates,
    covariates_for_horizon,
)
from mpc.solvers.forecasting.features import build_features, prepare_prediction_row
from mpc.solvers.forecasting.load_forecaster import LoadForecaster
from mpc.solvers.forecasting.metrics import compute_peak_risk_metrics
from mpc.microgrid.config_profiles import load_profiled_config
from mpc.microgrid.forecaster import _build_covariates_from_config


OFFICIAL_2026_HOLIDAYS = {
    '2026-01-01', '2026-01-02', '2026-01-03',
    '2026-02-15', '2026-02-16', '2026-02-17', '2026-02-18', '2026-02-19',
    '2026-02-20', '2026-02-21', '2026-02-22', '2026-02-23',
    '2026-04-04', '2026-04-05', '2026-04-06',
    '2026-05-01', '2026-05-02', '2026-05-03', '2026-05-04', '2026-05-05',
    '2026-06-19', '2026-06-20', '2026-06-21',
    '2026-09-25', '2026-09-26', '2026-09-27',
    '2026-10-01', '2026-10-02', '2026-10-03', '2026-10-04', '2026-10-05',
    '2026-10-06', '2026-10-07',
}

OFFICIAL_2026_ADJUSTED_WORKDAYS = {
    '2026-01-04',
    '2026-02-14', '2026-02-28',
    '2026-05-09',
    '2026-09-20', '2026-10-10',
}


def test_hehong_example_mpc_weather_forecast_covers_configured_run():
    """MPC example should provide future weather covering the full configured run."""
    cfg = load_profiled_config('mpc/configs/examples/mpc.yaml', 'hehong_weather')

    weather_file = cfg['forecast_covariates']['future_weather_file']
    assert weather_file

    weather = pd.read_excel(weather_file, sheet_name=cfg['forecast_covariates']['future_weather_sheet'])
    weather['time'] = pd.to_datetime(weather['time'])

    scenario = cfg['scenario']
    load_df = pd.read_excel(scenario['data_file'], sheet_name=scenario['sheets']['load'])
    load_time_col = load_df.columns[0]
    load_times = pd.to_datetime(load_df[load_time_col])

    start_step = cfg['mpc'].get('start_step', 0)
    days = cfg['mpc']['days']
    horizon_steps = cfg['mpc']['horizon_steps']
    run_steps = days * 96
    cov_start = load_times.iloc[start_step]
    cov_end_idx = min(start_step + run_steps + horizon_steps, len(load_times) - 1)
    cov_end = load_times.iloc[cov_end_idx]

    assert weather['time'].min() <= cov_start
    assert weather['time'].max() >= cov_end

    required_cols = {
        'temperature_c',
        'humidity_pct',
        'shortwave_radiation',
        'cloud_cover',
        'precipitation',
        'weather_code',
    }
    assert required_cols.issubset(set(weather.columns))


# ── Fixtures ──────────────────────────────────────────────────

@pytest.fixture
def sample_timestamps():
    """15-min timestamps spanning a week with a holiday in the middle."""
    return pd.date_range('2026-09-30', periods=8 * 96, freq='15min')  # 8 days, starts Sep 30


@pytest.fixture
def public_calendar():
    """Sample public holiday calendar DataFrame."""
    return pd.DataFrame({
        'date': pd.to_datetime(['2026-10-01', '2026-10-02', '2026-10-03']),
        'day_type': ['holiday', 'holiday', 'holiday'],
    })


@pytest.fixture
def factory_calendar():
    """Sample factory operations calendar DataFrame."""
    return pd.DataFrame({
        'date': pd.to_datetime(['2026-10-01', '2026-10-02', '2026-10-03',
                                '2026-10-05', '2026-10-06', '2026-10-07']),
        'is_factory_workday': [0, 0, 0, 1, 1, 1],
        'is_factory_overtime': [0, 0, 0, 0, 1, 0],
        'is_factory_shutdown': [0, 0, 1, 0, 0, 0],
    })


@pytest.fixture
def hourly_weather():
    """Hourly temperature and humidity data."""
    times = pd.date_range('2026-09-30 00:00', '2026-10-08 00:00', freq='1h')
    np.random.seed(42)
    return pd.DataFrame({
        'time': times,
        'temperature_c': 20 + 5 * np.sin(np.linspace(0, 4 * np.pi, len(times))),
        'humidity_pct': 60 + 20 * np.cos(np.linspace(0, 4 * np.pi, len(times))),
    })


# ── build_calendar_covariates ─────────────────────────────────

def test_china_public_holiday_calendar_2026_matches_official_schedule():
    """Bundled 2026 China calendar includes every official holiday and workday."""
    calendar = pd.read_excel('mpc/scenarios/china_public_holiday_calendar_2026.xlsx',
                             sheet_name='calendar')

    holiday_dates = set(
        calendar.loc[calendar['day_type'] == 'holiday', 'date']
        .dt.strftime('%Y-%m-%d')
    )
    adjusted_workdays = set(
        calendar.loc[calendar['day_type'] == 'workday', 'date']
        .dt.strftime('%Y-%m-%d')
    )

    assert holiday_dates == OFFICIAL_2026_HOLIDAYS
    assert adjusted_workdays == OFFICIAL_2026_ADJUSTED_WORKDAYS


class TestBuildCalendarCovariates:
    """Tests for build_calendar_covariates function."""

    def test_holiday_recognition(self, sample_timestamps, public_calendar):
        """Public holidays are correctly identified."""
        df = build_calendar_covariates(sample_timestamps, public_calendar=public_calendar)

        # Oct 1-3 are holidays
        oct1_mask = df['time'].dt.date == pd.Timestamp('2026-10-01').date()
        assert df.loc[oct1_mask, 'is_public_holiday'].iloc[0] == 1

        oct4_mask = df['time'].dt.date == pd.Timestamp('2026-10-04').date()
        assert df.loc[oct4_mask, 'is_public_holiday'].iloc[0] == 0

    def test_day_before_holiday(self, sample_timestamps, public_calendar):
        """Day before a holiday is correctly identified."""
        df = build_calendar_covariates(sample_timestamps, public_calendar=public_calendar)

        # Sep 30 is day before Oct 1 holiday
        sep30_mask = df['time'].dt.date == pd.Timestamp('2026-09-30').date()
        assert df.loc[sep30_mask, 'is_day_before_holiday'].iloc[0] == 1

        # Oct 4 is NOT day before any holiday
        oct4_mask = df['time'].dt.date == pd.Timestamp('2026-10-04').date()
        assert df.loc[oct4_mask, 'is_day_before_holiday'].iloc[0] == 0

    def test_day_after_holiday(self, sample_timestamps, public_calendar):
        """Day after a holiday is correctly identified."""
        df = build_calendar_covariates(sample_timestamps, public_calendar=public_calendar)

        # Oct 4 is day after Oct 3 holiday
        oct4_mask = df['time'].dt.date == pd.Timestamp('2026-10-04').date()
        assert df.loc[oct4_mask, 'is_day_after_holiday'].iloc[0] == 1

        # Oct 1 is NOT day after any holiday
        oct1_mask = df['time'].dt.date == pd.Timestamp('2026-10-01').date()
        assert df.loc[oct1_mask, 'is_day_after_holiday'].iloc[0] == 0

    def test_is_weekend_generated(self, sample_timestamps):
        """is_weekend is always generated from natural date."""
        df = build_calendar_covariates(sample_timestamps)

        # Oct 3 2026 is a Saturday
        oct3_mask = df['time'].dt.date == pd.Timestamp('2026-10-03').date()
        assert df.loc[oct3_mask, 'is_weekend'].iloc[0] == 1

        # Oct 5 2026 is a Monday
        oct5_mask = df['time'].dt.date == pd.Timestamp('2026-10-05').date()
        assert df.loc[oct5_mask, 'is_weekend'].iloc[0] == 0

        # Oct 4 2026 is a Sunday
        oct4_mask = df['time'].dt.date == pd.Timestamp('2026-10-04').date()
        assert df.loc[oct4_mask, 'is_weekend'].iloc[0] == 1

    def test_factory_calendar_missing_no_factory_features(self, sample_timestamps):
        """When factory_calendar is None, no factory features are generated."""
        df = build_calendar_covariates(sample_timestamps, factory_calendar=None)

        assert 'is_factory_workday' not in df.columns
        assert 'is_factory_overtime' not in df.columns
        assert 'is_factory_shutdown' not in df.columns

    def test_factory_calendar_provided_features_generated(self, sample_timestamps, factory_calendar):
        """When factory_calendar is provided, factory features are generated."""
        df = build_calendar_covariates(sample_timestamps, factory_calendar=factory_calendar)

        assert 'is_factory_workday' in df.columns
        assert 'is_factory_overtime' in df.columns
        assert 'is_factory_shutdown' in df.columns

    def test_factory_workday_from_calendar_not_weekend(self, sample_timestamps, factory_calendar):
        """Factory workday status comes from calendar, not from weekend inference."""
        df = build_calendar_covariates(sample_timestamps, factory_calendar=factory_calendar)

        # Oct 5 is Monday (workday in factory calendar)
        oct5_mask = df['time'].dt.date == pd.Timestamp('2026-10-05').date()
        assert df.loc[oct5_mask, 'is_factory_workday'].iloc[0] == 1

        # Oct 3 is Saturday AND factory calendar says not workday (shutdown=1)
        oct3_mask = df['time'].dt.date == pd.Timestamp('2026-10-03').date()
        assert df.loc[oct3_mask, 'is_factory_workday'].iloc[0] == 0

    def test_factory_overtime_recognized(self, sample_timestamps, factory_calendar):
        """Overtime flag is read from factory calendar."""
        df = build_calendar_covariates(sample_timestamps, factory_calendar=factory_calendar)

        oct6_mask = df['time'].dt.date == pd.Timestamp('2026-10-06').date()
        assert df.loc[oct6_mask, 'is_factory_overtime'].iloc[0] == 1

    def test_factory_shutdown_recognized(self, sample_timestamps, factory_calendar):
        """Shutdown flag is read from factory calendar."""
        df = build_calendar_covariates(sample_timestamps, factory_calendar=factory_calendar)

        oct3_mask = df['time'].dt.date == pd.Timestamp('2026-10-03').date()
        assert df.loc[oct3_mask, 'is_factory_shutdown'].iloc[0] == 1

    def test_no_calendar_returns_basic_features(self, sample_timestamps):
        """Without any calendar, returns basic features (is_weekend only)."""
        df = build_calendar_covariates(sample_timestamps)

        assert 'time' in df.columns
        assert 'is_weekend' in df.columns
        assert 'is_public_holiday' not in df.columns
        assert 'is_day_before_holiday' not in df.columns
        assert 'is_day_after_holiday' not in df.columns

    def test_public_calendar_no_day_type_column(self, sample_timestamps):
        """Calendar with 'holiday' column name (without day_type) should work."""
        cal = pd.DataFrame({
            'date': pd.to_datetime(['2026-10-01']),
            'holiday': ['holiday'],
        })
        # Should not crash; implement behavior based on column names
        # If day_type not found, look for 'holiday' column
        df = build_calendar_covariates(sample_timestamps, public_calendar=cal)
        # Regardless of column naming, should produce DataFrame with time column
        assert 'time' in df.columns

    def test_adjusted_workday_recognized_from_public_calendar(self):
        """Adjusted workdays are explicit public-calendar features, not weekend inference."""
        timestamps = pd.date_range('2026-01-04', periods=96, freq='15min')
        cal = pd.DataFrame({
            'date': pd.to_datetime(['2026-01-01', '2026-01-04']),
            'day_type': ['holiday', 'workday'],
        })

        df = build_calendar_covariates(timestamps, public_calendar=cal)

        assert 'is_adjusted_workday' in df.columns
        assert df['is_weekend'].iloc[0] == 1
        assert df['is_adjusted_workday'].iloc[0] == 1
        assert df['is_public_holiday'].iloc[0] == 0


# ── align_covariates ──────────────────────────────────────────

class TestAlignCovariates:
    """Tests for align_covariates function."""

    def test_hourly_to_15min_interpolation(self, sample_timestamps, hourly_weather):
        """Hourly temperature is correctly interpolated to 15-min."""
        numeric_cols = ['temperature_c', 'humidity_pct']
        aligned = align_covariates(sample_timestamps[:192], hourly_weather, numeric_cols)

        assert 'temperature_c' in aligned.columns
        assert len(aligned) == 192  # 48 hours * 4 points/hour

        # Interpolated values should be within the range of hourly data
        t_min = hourly_weather['temperature_c'].min()
        t_max = hourly_weather['temperature_c'].max()
        assert aligned['temperature_c'].between(t_min, t_max).all()

    def test_no_extrapolation_beyond_weather_range(self, hourly_weather):
        """Cannot interpolate beyond available weather data range."""
        # Timestamps outside weather data range
        out_of_range = pd.date_range('2026-10-10', periods=96, freq='15min')
        numeric_cols = ['temperature_c', 'humidity_pct']

        with pytest.raises(ValueError, match='outside'):
            align_covariates(out_of_range, hourly_weather, numeric_cols)

    def test_exact_match_on_hour_boundary(self, hourly_weather):
        """Timestamps exactly at hour boundaries match weather data."""
        exact_hours = pd.date_range('2026-10-01 00:00', '2026-10-01 06:00', freq='1h')
        numeric_cols = ['temperature_c', 'humidity_pct']
        aligned = align_covariates(exact_hours, hourly_weather, numeric_cols)

        # At exact hour, interpolated value should match original
        weather_row = hourly_weather[hourly_weather['time'] == exact_hours[0]]
        if len(weather_row) > 0:
            expected_temp = weather_row['temperature_c'].values[0]
            assert abs(aligned['temperature_c'].iloc[0] - expected_temp) < 1e-10

    def test_missing_numeric_column_error(self, hourly_weather):
        """Error when requested numeric column is not in weather data."""
        timestamps = pd.date_range('2026-10-01', periods=4, freq='15min')

        with pytest.raises(ValueError, match='not found'):
            align_covariates(timestamps, hourly_weather, ['nonexistent_col'])

    def test_no_zero_fill_for_missing(self, hourly_weather):
        """Missing weather must not be silently filled with 0."""
        # Create weather with gaps
        gapped = hourly_weather.copy()
        gapped = gapped.drop(gapped.index[10:20])  # remove some rows

        timestamps = pd.date_range('2026-10-01', periods=96, freq='15min')
        numeric_cols = ['temperature_c', 'humidity_pct']

        # Should still work via interpolation over the gap
        aligned = align_covariates(timestamps, gapped, numeric_cols)
        # No value should be exactly 0 (would indicate silent fill)
        assert (aligned['temperature_c'] != 0).all()

    def test_weather_code_uses_previous_observation_not_linear_interpolation(self):
        """Weather codes are categorical and should stay as source-hour codes."""
        timestamps = pd.date_range('2026-04-01 00:00', periods=5, freq='15min')
        weather = pd.DataFrame({
            'time': pd.to_datetime(['2026-04-01 00:00', '2026-04-01 01:00']),
            'weather_code': [1, 3],
        })

        aligned = align_covariates(timestamps, weather, ['weather_code'])

        assert aligned['weather_code'].tolist() == [1, 1, 1, 1, 3]


# ── covariates_for_horizon ────────────────────────────────────

class TestCovariatesForHorizon:
    """Tests for covariates_for_horizon function."""

    def test_extracts_correct_horizon(self, sample_timestamps, public_calendar):
        """Extracts covariate rows for a specific future horizon."""
        all_cov = build_calendar_covariates(sample_timestamps, public_calendar=public_calendar)

        horizon_ts = sample_timestamps[100:200]
        required = ['is_public_holiday', 'is_day_before_holiday',
                     'is_day_after_holiday', 'is_weekend']

        result = covariates_for_horizon(horizon_ts, all_cov, required)

        assert len(result) == 100
        for col in required:
            assert col in result.columns

    def test_missing_required_column_error(self, sample_timestamps):
        """Error when a required column is missing from covariates."""
        all_cov = build_calendar_covariates(sample_timestamps)
        horizon_ts = sample_timestamps[:10]

        with pytest.raises(ValueError, match='Missing required'):
            covariates_for_horizon(horizon_ts, all_cov,
                                   ['is_public_holiday'])

    def test_rows_match_timestamps(self, sample_timestamps, public_calendar):
        """Output rows match exactly the requested timestamps."""
        all_cov = build_calendar_covariates(sample_timestamps, public_calendar=public_calendar)
        horizon_ts = sample_timestamps[50:75]
        required = ['is_weekend']

        result = covariates_for_horizon(horizon_ts, all_cov, required)

        assert len(result) == 25
        pd.testing.assert_index_equal(result.index, pd.RangeIndex(25))
        # Time values should match
        assert (result['time'].values == horizon_ts.values).all()


# ── Feature Integration (Task 2) ──────────────────────────────

@pytest.fixture
def train_series():
    """Build a sample training DataFrame with time and value columns."""
    timestamps = pd.date_range('2026-09-28', periods=7 * 96, freq='15min')
    np.random.seed(42)
    values = 0.5 + 0.2 * np.sin(np.linspace(0, 14 * np.pi, len(timestamps)))
    values += np.random.normal(0, 0.02, len(timestamps))
    return pd.DataFrame({'time': timestamps, 'value': values})


@pytest.fixture
def holiday_covariates():
    """Covariates covering the training data range."""
    cal = pd.DataFrame({
        'date': pd.to_datetime(['2026-10-01', '2026-10-02', '2026-10-03']),
        'day_type': ['holiday', 'holiday', 'holiday'],
    })
    ts = pd.date_range('2026-09-28', periods=10 * 96, freq='15min')
    return build_calendar_covariates(ts, public_calendar=cal)


class TestBuildFeaturesWithCovariates:
    """Tests for build_features with covariates parameter."""

    def test_external_columns_merged(self, train_series, holiday_covariates):
        """build_features merges external_columns from covariates."""
        config = {
            'lags': [1, 2, 96],
            'rolling_windows': [96],
            'calendar': True,
            'cyclical': True,
            'external_columns': ['is_public_holiday', 'is_weekend'],
        }
        df, feature_cols = build_features(train_series, config=config,
                                          covariates=holiday_covariates)

        assert 'is_public_holiday' in feature_cols
        assert 'is_weekend' in feature_cols
        # Existing features still present
        assert 'lag_1' in feature_cols
        assert 'hour' in feature_cols

    def test_only_configured_external_columns_added(self, train_series, holiday_covariates):
        """Only columns listed in external_columns are added, not all covariate columns."""
        config = {
            'lags': [1, 2],
            'calendar': False,
            'cyclical': False,
            'external_columns': ['is_public_holiday'],
        }
        df, feature_cols = build_features(train_series, config=config,
                                          covariates=holiday_covariates)

        assert 'is_public_holiday' in feature_cols
        assert 'is_weekend' not in feature_cols  # in covariates but not in external_columns
        assert 'is_day_before_holiday' not in feature_cols

    def test_no_covariates_still_works(self, train_series):
        """build_features without covariates works as before."""
        config = {
            'lags': [1, 2, 96],
            'rolling_windows': [96],
            'calendar': True,
            'cyclical': True,
        }
        df, feature_cols = build_features(train_series, config=config)

        assert 'lag_1' in feature_cols
        assert 'hour' in feature_cols

    def test_missing_configured_covariate_error(self, train_series, holiday_covariates):
        """Error when external_columns requests a column not in covariates."""
        config = {
            'lags': [1, 2],
            'external_columns': ['temperature_c'],  # not in holiday_covariates
        }

        with pytest.raises(ValueError, match='temperature_c'):
            build_features(train_series, config=config, covariates=holiday_covariates)

    def test_missing_covariate_rows_error(self, holiday_covariates):
        """Error when covariate data doesn't cover training time range (null after merge)."""
        # Training data starts before covariate time range
        early_ts = pd.date_range('2026-09-20', periods=96, freq='15min')
        early_df = pd.DataFrame({
            'time': early_ts,
            'value': np.random.uniform(0.3, 0.8, len(early_ts)),
        })
        config = {
            'lags': [1, 2],
            'external_columns': ['is_public_holiday'],
        }

        with pytest.raises(ValueError, match='null values after merge'):
            build_features(early_df, config=config, covariates=holiday_covariates)


class TestPreparePredictionRowWithCovariates:
    """Tests for prepare_prediction_row with future_covariates."""

    def test_covariate_values_used_in_prediction(self, holiday_covariates):
        """prepare_prediction_row uses covariate values for external columns."""
        history = np.array([0.5] * 200)
        timestamps = pd.date_range('2026-10-01', periods=200, freq='15min')
        future_time = pd.Timestamp('2026-10-01 12:00')

        config = {
            'lags': [1, 2, 96],
            'calendar': True,
            'cyclical': True,
            'external_columns': ['is_public_holiday'],
        }
        feature_cols = ['lag_1', 'lag_2', 'lag_96', 'rolling_mean_96', 'rolling_std_96',
                        'hour', 'dayofweek', 'month', 'is_weekend', 'quarter_hour',
                        'hour_sin', 'hour_cos', 'dow_sin', 'dow_cos', 'month_sin', 'month_cos',
                        'is_public_holiday']

        row = prepare_prediction_row(history, timestamps, future_time,
                                     config=config, feature_cols=feature_cols,
                                     future_covariates=holiday_covariates)

        # is_public_holiday should be 1 for Oct 1
        holiday_idx = feature_cols.index('is_public_holiday')
        assert row[holiday_idx] == 1.0

    def test_train_predict_feature_consistency(self, train_series, holiday_covariates):
        """Training and prediction produce identical features for the same time point."""
        config = {
            'lags': [1, 2, 96],
            'rolling_windows': [96],
            'calendar': True,
            'cyclical': True,
            'external_columns': ['is_public_holiday', 'is_weekend'],
        }

        # Train features
        train_df, feature_cols = build_features(train_series, config=config,
                                                covariates=holiday_covariates)
        train_df = train_df.dropna(subset=feature_cols)

        # Pick a specific row and time
        idx = 200
        row_time = train_df.iloc[idx]['time']
        actual_values = train_df.iloc[idx][feature_cols].values

        # Predict features for the same time point
        history = train_df.iloc[idx - 96:idx]['value'].values
        hist_times = pd.DatetimeIndex(train_df.iloc[idx - 96:idx]['time'].values)
        future_time = pd.Timestamp(row_time)

        pred_values = prepare_prediction_row(
            history, hist_times, future_time,
            config=config, feature_cols=feature_cols,
            future_covariates=holiday_covariates,
        )

        # Calendar features should match
        cal_fields = ['hour', 'dayofweek', 'month', 'is_weekend', 'quarter_hour',
                      'hour_sin', 'hour_cos', 'dow_sin', 'dow_cos', 'month_sin', 'month_cos']
        for field in cal_fields:
            if field in feature_cols:
                fi = feature_cols.index(field)
                assert actual_values[fi] == pytest.approx(pred_values[fi], abs=1e-10), \
                    f"Mismatch on {field}: train={actual_values[fi]}, pred={pred_values[fi]}"

        # External covariate fields should match
        for field in ['is_public_holiday', 'is_weekend']:
            if field in feature_cols:
                fi = feature_cols.index(field)
                assert actual_values[fi] == pred_values[fi], \
                    f"Mismatch on {field}: train={actual_values[fi]}, pred={pred_values[fi]}"

    def test_missing_future_covariate_error(self):
        """Error when config requires external columns but future_covariates is None."""
        history = np.array([0.5] * 200)
        timestamps = pd.date_range('2026-10-01', periods=200, freq='15min')
        future_time = pd.Timestamp('2026-10-01 12:00')

        config = {
            'lags': [1, 2],
            'external_columns': ['is_public_holiday'],
        }
        feature_cols = ['lag_1', 'lag_2', 'is_public_holiday']

        with pytest.raises(ValueError, match='future_covariates'):
            prepare_prediction_row(history, timestamps, future_time,
                                   config=config, feature_cols=feature_cols,
                                   future_covariates=None)


# ── LoadForecaster Integration (Task 3) ───────────────────────

@pytest.fixture
def forecaster_data():
    """Build sample data for LoadForecaster training."""
    timestamps = pd.date_range('2026-09-01', periods=30 * 96, freq='15min')
    np.random.seed(42)
    # Daily pattern + noise
    hour_of_day = timestamps.hour + timestamps.minute / 60.0
    values = 0.4 + 0.3 * np.sin(2 * np.pi * (hour_of_day - 6) / 24)
    values += np.random.normal(0, 0.03, len(timestamps))
    values = np.clip(values, 0.0, 1.0)
    return values, timestamps


@pytest.fixture
def forecaster_covariates():
    """Covariates covering forecaster training data range."""
    cal = pd.DataFrame({
        'date': pd.to_datetime(['2026-10-01', '2026-10-02', '2026-10-03']),
        'day_type': ['holiday', 'holiday', 'holiday'],
    })
    ts = pd.date_range('2026-09-01', periods=35 * 96, freq='15min')
    return build_calendar_covariates(ts, public_calendar=cal)


class TestLoadForecasterWithCovariates:
    """Tests for LoadForecaster.train/predict/evaluate/save/load with covariates."""

    def test_train_with_covariates(self, forecaster_data, forecaster_covariates):
        """Train a LightGBM model with covariate features."""
        values, timestamps = forecaster_data

        fc = LoadForecaster({
            'lightgbm': {'n_estimators': 50, 'learning_rate': 0.1,
                         'num_leaves': 31, 'max_depth': 4,
                         'min_data_in_leaf': 20, 'subsample': 0.8,
                         'colsample_bytree': 0.8, 'random_state': 42,
                         'verbose': -1, 'early_stopping_rounds': 10},
            'features': {
                'lags': [1, 2, 96],
                'rolling_windows': [96],
                'calendar': True,
                'cyclical': True,
                'external_columns': ['is_public_holiday', 'is_weekend'],
            },
            'data': {'train_ratio': 0.8},
        })

        metrics = fc.train(values, timestamps, covariates=forecaster_covariates)

        assert fc.is_fitted
        assert 'is_public_holiday' in fc.feature_cols
        assert 'is_weekend' in fc.feature_cols
        assert metrics['val_mae'] > 0

    def test_predict_with_future_covariates(self, forecaster_data, forecaster_covariates):
        """Predict uses future_covariates for external features."""
        values, timestamps = forecaster_data

        fc = LoadForecaster({
            'lightgbm': {'n_estimators': 50, 'learning_rate': 0.1,
                         'num_leaves': 31, 'max_depth': 4,
                         'min_data_in_leaf': 20, 'subsample': 0.8,
                         'colsample_bytree': 0.8, 'random_state': 42,
                         'verbose': -1, 'early_stopping_rounds': 10},
            'features': {
                'lags': [1, 2, 96],
                'rolling_windows': [96],
                'calendar': True,
                'cyclical': True,
                'external_columns': ['is_public_holiday'],
            },
            'data': {'train_ratio': 0.8},
        })

        fc.train(values, timestamps, covariates=forecaster_covariates)

        # Predict with future covariates
        history = values[-200:]
        start_time = timestamps[-1] + pd.Timedelta(minutes=15)
        forecast = fc.predict(history, start_time, horizon_steps=24,
                              future_covariates=forecaster_covariates)
        assert len(forecast) == 24
        assert np.all(forecast >= 0) and np.all(forecast <= 1)

    def test_predict_missing_weather_error(self, forecaster_data):
        """Error when external_columns configured but no future_covariates given."""
        values, timestamps = forecaster_data

        fc = LoadForecaster({
            'lightgbm': {'n_estimators': 50, 'learning_rate': 0.1,
                         'num_leaves': 31, 'max_depth': 4,
                         'min_data_in_leaf': 20, 'subsample': 0.8,
                         'colsample_bytree': 0.8, 'random_state': 42,
                         'verbose': -1, 'early_stopping_rounds': 10},
            'features': {
                'lags': [1, 2],
                'calendar': False,
                'cyclical': False,
                'external_columns': ['temperature_c'],
            },
            'data': {'train_ratio': 0.8},
        })

        # Train with temperature covariate
        weather = pd.DataFrame({
            'time': pd.date_range('2026-09-01', periods=30 * 96, freq='15min'),
            'temperature_c': np.linspace(15, 35, 30 * 96),
        })
        fc.train(values, timestamps, covariates=weather)

        history = values[-100:]
        start_time = timestamps[-1] + pd.Timedelta(minutes=15)

        with pytest.raises(ValueError, match='future_covariates'):
            fc.predict(history, start_time, horizon_steps=12)

    def test_save_load_preserves_external_columns(self, forecaster_data, forecaster_covariates, tmp_path):
        """Save and load preserves external_columns and feature order."""
        values, timestamps = forecaster_data

        fc = LoadForecaster({
            'lightgbm': {'n_estimators': 50, 'learning_rate': 0.1,
                         'num_leaves': 31, 'max_depth': 4,
                         'min_data_in_leaf': 20, 'subsample': 0.8,
                         'colsample_bytree': 0.8, 'random_state': 42,
                         'verbose': -1, 'early_stopping_rounds': 10},
            'features': {
                'lags': [1, 2, 96],
                'rolling_windows': [96],
                'calendar': True,
                'cyclical': True,
                'external_columns': ['is_public_holiday', 'is_weekend'],
            },
            'data': {'train_ratio': 0.8},
        })

        fc.train(values, timestamps, covariates=forecaster_covariates)

        path = str(tmp_path / 'test_model.joblib')
        fc.save(path)

        loaded = LoadForecaster.load(path)
        assert loaded.is_fitted
        assert loaded.feature_cols == fc.feature_cols
        assert 'is_public_holiday' in loaded.feature_cols


# ── Forecaster CLI weather config integration ─────────────────

def test_forecaster_config_loads_historical_weather_columns(tmp_path):
    """_build_covariates_from_config loads configured weather columns from Excel."""
    weather_path = tmp_path / 'weather.xlsx'
    weather = pd.DataFrame({
        'time': pd.date_range('2026-03-01 00:00', periods=4, freq='1h'),
        'temperature_c': [10.0, 11.0, 12.0, 13.0],
        'humidity_pct': [80.0, 81.0, 82.0, 83.0],
        'shortwave_radiation': [0.0, 10.0, 20.0, 30.0],
        'cloud_cover': [90.0, 80.0, 70.0, 60.0],
        'precipitation': [0.1, 0.0, 0.0, 0.0],
    })
    with pd.ExcelWriter(weather_path, engine='openpyxl') as writer:
        weather.to_excel(writer, sheet_name='weather', index=False)

    cfg = {
        'features': {
            'external_columns': [
                'temperature_c',
                'humidity_pct',
                'shortwave_radiation',
                'cloud_cover',
                'precipitation',
            ],
        },
        'covariates': {
            'historical_weather_file': str(weather_path),
            'historical_weather_sheet': 'weather',
            'weather_columns': {
                'temperature_c': ['temperature', 'temp'],
                'humidity_pct': ['humidity'],
                'shortwave_radiation': ['shortwave_radiation'],
                'cloud_cover': ['cloud_cover'],
                'precipitation': ['precipitation'],
            },
        },
    }
    target_ts = pd.date_range('2026-03-01 00:00', periods=9, freq='15min')

    covariates = _build_covariates_from_config(cfg, target_ts)

    assert list(covariates.columns) == [
        'time',
        'temperature_c',
        'humidity_pct',
        'shortwave_radiation',
        'cloud_cover',
        'precipitation',
    ]
    assert len(covariates) == len(target_ts)
    assert covariates['temperature_c'].iloc[0] == pytest.approx(10.0)
    assert covariates['temperature_c'].iloc[4] == pytest.approx(11.0)


# ── Peak Risk Metrics (Task 4) ────────────────────────────────

def test_forecaster_config_errors_when_required_holiday_calendar_missing(tmp_path):
    """Required holiday features must not be silently filled with zeros."""
    missing_calendar = tmp_path / 'missing_calendar.xlsx'
    cfg = {
        'features': {
            'external_columns': [
                'is_public_holiday',
                'is_day_before_holiday',
                'is_day_after_holiday',
            ],
        },
        'covariates': {
            'holiday_calendar_file': str(missing_calendar),
            'holiday_calendar_sheet': 'calendar',
        },
    }
    target_ts = pd.date_range('2026-04-01 00:00', periods=96, freq='15min')

    with pytest.raises(SystemExit):
        _build_covariates_from_config(cfg, target_ts)


class TestPeakRiskMetrics:
    """Tests for compute_peak_risk_metrics function."""

    def test_perfect_prediction_zero_errors(self):
        """Perfect prediction gives zero positive errors."""
        actual = np.array([0.5, 0.6, 0.7, 0.8, 0.9, 1.0])
        predicted = actual.copy()
        metrics = compute_peak_risk_metrics(actual, predicted)

        assert metrics['positive_error_p80'] == 0.0
        assert metrics['positive_error_p90'] == 0.0
        assert metrics['max_under_forecast'] == 0.0
        assert metrics['actual_peak_error'] == 0.0

    def test_under_forecast_detected(self):
        """Under-forecast (predicted < actual) produces positive errors."""
        actual = np.array([0.5, 0.6, 0.7, 0.8, 0.9, 1.0])
        predicted = np.array([0.5, 0.6, 0.7, 0.7, 0.8, 0.8])  # under-forecast peaks

        metrics = compute_peak_risk_metrics(actual, predicted)

        # Positive errors should exist
        assert metrics['positive_error_p90'] > 0
        assert metrics['max_under_forecast'] > 0

        # Actual peak at index 5: actual=1.0, predicted=0.8 => error = 0.2
        assert metrics['actual_peak_error'] == pytest.approx(0.2)

    def test_over_forecast_ignored_in_positive_errors(self):
        """Over-forecast (predicted > actual) is not counted as positive error."""
        actual = np.array([0.5, 0.6, 0.7, 0.8, 0.5, 0.4])
        predicted = np.array([0.6, 0.7, 0.8, 0.9, 0.5, 0.4])  # over-forecast

        metrics = compute_peak_risk_metrics(actual, predicted)

        # All errors are negative (over-forecast), so positive errors = 0
        assert metrics['positive_error_p80'] == 0.0
        assert metrics['max_under_forecast'] == 0.0

    def test_peak_period_mae(self):
        """peak_period_mae only considers high-load hours (top quantile)."""
        np.random.seed(42)
        actual = np.random.uniform(0.3, 1.0, 1000)
        predicted = actual + np.random.normal(0, 0.05, 1000)

        metrics = compute_peak_risk_metrics(actual, predicted, peak_quantile=0.90)

        # peak_period_mae should be computed
        assert 'peak_period_mae' in metrics
        assert metrics['peak_period_mae'] > 0

    def test_custom_quantile(self):
        """peak_quantile parameter controls the high-load threshold."""
        actual = np.array([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0])
        predicted = actual.copy()

        m80 = compute_peak_risk_metrics(actual, predicted, peak_quantile=0.80)
        m95 = compute_peak_risk_metrics(actual, predicted, peak_quantile=0.95)

        # Different quantiles give different thresholds, but with perfect pred both have zero error
        assert m80['positive_error_p90'] == 0.0
        assert m95['positive_error_p90'] == 0.0

    def test_max_under_forecast_zero_when_all_over_forecast(self):
        """max_under_forecast = 0 when all predictions are higher than actual."""
        actual = np.array([1.0, 1.0])
        predicted = np.array([2.0, 3.0])  # all over-forecast
        metrics = compute_peak_risk_metrics(actual, predicted)

        assert metrics['max_under_forecast'] == 0.0
        assert metrics['positive_error_p80'] == 0.0
        assert metrics['positive_error_p90'] == 0.0


# ── LoadForecaster Backward Compatibility (Fix 7) ─────────────

class TestLoadForecasterBackwardCompat:
    """Tests for loading old-format joblib models."""

    def test_load_old_format_no_config_key(self, tmp_path):
        """Loading a joblib without 'config' key succeeds with defaults."""
        import joblib
        from lightgbm import LGBMRegressor

        # Simulate an old-format joblib (no 'config' key, no 'feature_config')
        old_data = {
            'model': LGBMRegressor(n_estimators=10, verbose=-1, force_col_wise=True),
            'feature_cols': ['lag_1', 'lag_2', 'hour', 'is_weekend'],
            # No 'config', no 'feature_config'
        }
        path = str(tmp_path / 'old_model.joblib')
        joblib.dump(old_data, path)

        loaded = LoadForecaster.load(path)
        assert loaded.is_fitted
        assert loaded.feature_cols == ['lag_1', 'lag_2', 'hour', 'is_weekend']
        # feature_importance on unfitted model raises NotFittedError — expected

    def test_load_old_format_no_feature_config(self, tmp_path):
        """Loading a joblib with config but no feature_config works."""
        import joblib
        from lightgbm import LGBMRegressor

        old_data = {
            'model': LGBMRegressor(n_estimators=10, verbose=-1, force_col_wise=True),
            'feature_cols': ['lag_1', 'is_weekend'],
            'config': {'features': {'lags': [1, 2], 'calendar': True}},
            # 'feature_config' key absent
        }
        path = str(tmp_path / 'old_model2.joblib')
        joblib.dump(old_data, path)

        loaded = LoadForecaster.load(path)
        assert loaded.is_fitted
        assert loaded.feature_cols == ['lag_1', 'is_weekend']
