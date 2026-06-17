# Load Forecast Covariates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve the 15-minute LightGBM load forecast by adding holiday/workday and optional weather covariates, then measure whether the enhanced forecast reduces peak underestimation and improves MPC peak shaving.

**Architecture:** Keep the existing lag, rolling, hour, weekday, weekend, month, and cyclical features. Add a focused covariate layer that aligns known-future calendar fields and optional future weather forecasts to the same 15-minute timeline used by training, recursive prediction, backtesting, and MPC. Models trained with weather must receive forecast weather at inference; actual future weather must never be used during MPC backtests.

**Tech Stack:** Python, pandas, NumPy, LightGBM, openpyxl, PyYAML, pytest

---

## Current-State Notes

- `models/forecasting/features.py` already creates:
  - `hour`, `dayofweek`, `month`, `is_weekend`, `quarter_hour`
  - `hour_sin/cos`, `dow_sin/cos`, `month_sin/cos`
  - lag and rolling-load features
- The first implementation must therefore add only:
  - official holiday and adjusted-workday features
  - optional weather covariates such as temperature and humidity
  - a shared training/prediction/MPC covariate interface
- Holiday data must support Chinese adjusted workdays. Do not infer all workdays from Monday-Friday alone.
- Weather used by MPC must be weather forecast data available at the decision time, not future observed weather.
- Model success must be judged by both average error and peak-underestimation behavior.

## File Structure

- Create: `models/forecasting/covariates.py`
  - Load, validate, align, and query holiday/workday and weather covariates.
- Create: `tests/test_forecasting_covariates.py`
  - Cover alignment, feature parity, missing future weather, and peak-error metrics.
- Modify: `models/forecasting/features.py`
  - Add external covariates to training rows and recursive prediction rows.
- Modify: `models/forecasting/load_forecaster.py`
  - Accept covariates during train, predict, and evaluate; persist required covariate schema.
- Modify: `models/forecasting/metrics.py`
  - Add positive under-forecast and peak-focused metrics.
- Modify: `microgrid/forecaster.py`
  - Load historical covariates, run baseline/enhanced comparisons, and export evaluation results.
- Modify: `microgrid/mpc.py`
  - Load future covariates and pass each 48-hour covariate horizon to LightGBM.
- Modify: `models/forecasting/config.yaml`
  - Define feature flags and covariate column names.
- Modify: `examples/forecaster_hehong.yaml`
  - Configure training holiday/weather sources.
- Modify: `examples/mpc_hehong.yaml`
  - Configure future weather source and holiday calendar.
- No dependency changes are required; use a project-provided holiday calendar file to represent holidays and adjusted workdays.

## External Data Required for Final Acceptance

- Historical weather aligned to the training period, with at least:
  - timestamp
  - temperature
  - humidity, if available
- Future weather forecast aligned to the MPC replay/operation period.
- Official holiday calendar containing both holidays and adjusted working weekends.

Implementation and automated tests can proceed before these files exist. Real weather-model training and the final A/B acceptance comparison cannot be completed until the weather files are supplied.

---

### Task 1: Define and Test the Covariate Data Contract

**Files:**
- Create: `models/forecasting/covariates.py`
- Create: `tests/test_forecasting_covariates.py`

- [ ] **Step 1: Write failing tests for 15-minute covariate alignment**

Add tests that require:

```python
import pandas as pd
import pytest

from models.forecasting.covariates import (
    align_covariates,
    build_calendar_covariates,
    covariates_for_horizon,
)


def test_calendar_covariates_support_holiday_and_adjusted_workday():
    timestamps = pd.to_datetime(["2026-10-01 00:00", "2026-10-10 09:00"])
    calendar = pd.DataFrame({
        "date": [pd.Timestamp("2026-10-01"), pd.Timestamp("2026-10-10")],
        "day_type": ["holiday", "workday"],
    })

    result = build_calendar_covariates(timestamps, calendar)

    assert result.loc[0, "is_holiday"] == 1.0
    assert result.loc[0, "is_workday"] == 0.0
    assert result.loc[1, "is_holiday"] == 0.0
    assert result.loc[1, "is_workday"] == 1.0


def test_align_covariates_interpolates_weather_to_15_minutes():
    target = pd.date_range("2026-04-01 00:00", periods=5, freq="15min")
    hourly = pd.DataFrame({
        "time": pd.to_datetime(["2026-04-01 00:00", "2026-04-01 01:00"]),
        "temperature_c": [20.0, 24.0],
    })

    result = align_covariates(target, hourly, numeric_columns=["temperature_c"])

    assert result["temperature_c"].tolist() == [20.0, 21.0, 22.0, 23.0, 24.0]


def test_future_weather_required_when_feature_is_enabled():
    target = pd.date_range("2026-04-01 00:00", periods=4, freq="15min")

    with pytest.raises(ValueError, match="temperature_c"):
        covariates_for_horizon(
            target,
            covariates=pd.DataFrame({"time": []}),
            required_columns=["temperature_c"],
        )
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
pytest tests/test_forecasting_covariates.py -q
```

Expected: collection fails because `models.forecasting.covariates` does not exist.

- [ ] **Step 3: Implement the covariate helpers**

Implement these interfaces in `models/forecasting/covariates.py`:

```python
def build_calendar_covariates(
    timestamps: pd.DatetimeIndex,
    calendar: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Return time, is_holiday, is_workday, is_day_before_holiday, is_day_after_holiday."""


def align_covariates(
    timestamps: pd.DatetimeIndex,
    covariates: pd.DataFrame,
    numeric_columns: list[str],
) -> pd.DataFrame:
    """Align external numeric data to the exact 15-minute forecast timeline."""


def covariates_for_horizon(
    timestamps: pd.DatetimeIndex,
    covariates: pd.DataFrame,
    required_columns: list[str],
) -> pd.DataFrame:
    """Return ordered future rows and fail clearly when required future values are absent."""
```

Rules:

- `calendar` rows use `date` and `day_type`.
- Supported `day_type` values are `holiday`, `workday`, and `normal`.
- Explicit `workday` overrides weekend logic for adjusted working weekends.
- Explicit `holiday` overrides weekday logic.
- Numeric weather columns are time-interpolated only inside the provided time range.
- Missing weather at either horizon boundary raises `ValueError`; do not silently use future actual weather or zeros.

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```powershell
pytest tests/test_forecasting_covariates.py -q
```

Expected: all Task 1 tests pass.

- [ ] **Step 5: Commit**

This workspace currently has no `.git` repository. If git is initialized before execution:

```powershell
git add models/forecasting/covariates.py tests/test_forecasting_covariates.py
git commit -m "feat: add forecasting covariate alignment"
```

---

### Task 2: Add Holiday and Weather Features With Training/Prediction Parity

**Files:**
- Modify: `models/forecasting/features.py`
- Modify: `tests/test_forecasting_covariates.py`

- [ ] **Step 1: Write failing tests for feature parity**

Add:

```python
import numpy as np

from models.forecasting.features import build_features, prepare_prediction_row


def test_training_and_prediction_rows_use_same_external_feature_order():
    times = pd.date_range("2026-04-01 00:00", periods=700, freq="15min")
    df = pd.DataFrame({"time": times, "value": np.linspace(0.2, 0.8, len(times))})
    covariates = pd.DataFrame({
        "time": times,
        "is_holiday": 0.0,
        "is_workday": 1.0,
        "is_day_before_holiday": 0.0,
        "is_day_after_holiday": 0.0,
        "temperature_c": 22.0,
        "humidity_pct": 60.0,
    })
    config = {
        "lags": [1, 96, 672],
        "rolling_windows": [96],
        "calendar": True,
        "cyclical": True,
        "external_columns": [
            "is_holiday",
            "is_workday",
            "is_day_before_holiday",
            "is_day_after_holiday",
            "temperature_c",
            "humidity_pct",
        ],
    }

    built, feature_cols = build_features(df, config, covariates=covariates)
    future_covariates = covariates.iloc[-1].to_dict()
    row = prepare_prediction_row(
        history=df["value"].values[:-1],
        timestamps=times[:-1],
        future_time=times[-1],
        config=config,
        feature_cols=feature_cols,
        future_covariates=future_covariates,
    )

    expected = built.iloc[-1][feature_cols].to_numpy(dtype=float)
    assert np.allclose(row, expected)
```

- [ ] **Step 2: Run the parity test and verify RED**

Run:

```powershell
pytest tests/test_forecasting_covariates.py::test_training_and_prediction_rows_use_same_external_feature_order -q
```

Expected: fail because `build_features` and `prepare_prediction_row` do not accept covariates.

- [ ] **Step 3: Extend feature construction**

Change signatures:

```python
def build_features(
    df: pd.DataFrame,
    config: dict | None = None,
    covariates: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, list[str]]:
```

```python
def prepare_prediction_row(
    history: np.ndarray,
    timestamps: pd.DatetimeIndex,
    future_time: pd.Timestamp,
    config: dict | None = None,
    feature_cols: list[str] | None = None,
    future_covariates: dict[str, float] | None = None,
) -> np.ndarray:
```

Implementation rules:

- Keep all existing lag, rolling, hour, weekday, weekend, month, and cyclical features.
- Merge covariates by exact `time`.
- Append only columns listed in `features.external_columns`.
- Fail when a configured external feature is missing during training or prediction.
- Preserve exact `feature_cols` ordering saved with the model.

- [ ] **Step 4: Run feature tests**

Run:

```powershell
pytest tests/test_forecasting_covariates.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add models/forecasting/features.py tests/test_forecasting_covariates.py
git commit -m "feat: add holiday and weather forecast features"
```

---

### Task 3: Extend LoadForecaster APIs and Model Persistence

**Files:**
- Modify: `models/forecasting/load_forecaster.py`
- Modify: `tests/test_forecasting_covariates.py`

- [ ] **Step 1: Write failing train/predict tests**

Add:

```python
def test_load_forecaster_requires_future_covariates_for_weather_model():
    cfg = {
        "features": {
            "lags": [1],
            "rolling_windows": [],
            "calendar": True,
            "cyclical": True,
            "external_columns": ["temperature_c"],
        },
        "lightgbm": {"n_estimators": 10, "early_stopping_rounds": 2},
        "data": {"train_ratio": 0.8},
    }
    times = pd.date_range("2026-04-01", periods=200, freq="15min")
    values = np.sin(np.arange(200) / 20) * 0.1 + 0.5
    covariates = pd.DataFrame({"time": times, "temperature_c": 20.0})
    model = LoadForecaster(cfg)
    model.train(values, times, covariates=covariates)

    with pytest.raises(ValueError, match="future covariates"):
        model.predict(values, times[-1] + pd.Timedelta(minutes=15), horizon_steps=4)
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
pytest tests/test_forecasting_covariates.py::test_load_forecaster_requires_future_covariates_for_weather_model -q
```

Expected: fail because `train` and `predict` do not accept covariates.

- [ ] **Step 3: Extend LoadForecaster**

Change APIs:

```python
def train(
    self,
    load_ratio=None,
    timestamps=None,
    config_path=None,
    scenario_file=None,
    covariates: pd.DataFrame | None = None,
) -> dict:
```

```python
def predict(
    self,
    history: np.ndarray,
    start_time,
    horizon_steps: int = 192,
    future_covariates: pd.DataFrame | None = None,
) -> np.ndarray:
```

```python
def evaluate(
    self,
    load_ratio=None,
    timestamps=None,
    config_path=None,
    scenario_file=None,
    backtest_every_steps: int = 96,
    covariates: pd.DataFrame | None = None,
) -> dict:
```

Implementation rules:

- During training, merge aligned covariates into feature construction.
- During recursive prediction, query the covariate row for each future 15-minute timestamp.
- Persist `external_columns` and feature ordering in the existing joblib payload.
- Loading old models without `external_columns` must continue to work.
- Backtesting must slice covariates only for each simulated future horizon.

- [ ] **Step 4: Run forecasting tests**

Run:

```powershell
pytest tests/test_forecasting_covariates.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add models/forecasting/load_forecaster.py tests/test_forecasting_covariates.py
git commit -m "feat: support future covariates in load forecaster"
```

---

### Task 4: Add Peak-Focused Forecast Metrics

**Files:**
- Modify: `models/forecasting/metrics.py`
- Modify: `models/forecasting/load_forecaster.py`
- Modify: `tests/test_forecasting_covariates.py`

- [ ] **Step 1: Write failing peak-metric tests**

Add:

```python
from models.forecasting.metrics import compute_peak_risk_metrics


def test_peak_risk_metrics_measure_under_forecast_tail():
    actual = np.array([400.0, 500.0, 700.0, 450.0])
    predicted = np.array([410.0, 460.0, 550.0, 470.0])

    metrics = compute_peak_risk_metrics(actual, predicted, peak_quantile=0.75)

    assert metrics["max_under_forecast"] == 150.0
    assert metrics["positive_error_p90"] > 0
    assert metrics["actual_peak_error"] == 150.0
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
pytest tests/test_forecasting_covariates.py::test_peak_risk_metrics_measure_under_forecast_tail -q
```

Expected: fail because `compute_peak_risk_metrics` does not exist.

- [ ] **Step 3: Implement peak-focused metrics**

Add:

```python
def compute_peak_risk_metrics(
    actual: np.ndarray,
    predicted: np.ndarray,
    peak_quantile: float = 0.90,
) -> dict:
```

Return:

- `positive_error_p80`
- `positive_error_p90`
- `max_under_forecast`
- `actual_peak_error`
- `peak_period_mae`, calculated only where actual load is above the selected quantile

Include these metrics in `LoadForecaster.evaluate()`.

- [ ] **Step 4: Run tests**

Run:

```powershell
pytest tests/test_forecasting_covariates.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add models/forecasting/metrics.py models/forecasting/load_forecaster.py tests/test_forecasting_covariates.py
git commit -m "feat: add peak-focused forecast metrics"
```

---

### Task 5: Wire Historical Covariates Into Training and Backtesting

**Files:**
- Modify: `microgrid/forecaster.py`
- Modify: `models/forecasting/config.yaml`
- Modify: `examples/forecaster_hehong.yaml`
- Modify: `tests/test_forecasting_covariates.py`

- [ ] **Step 1: Add a failing configuration-loading test**

Test a helper that loads:

- training load timeline
- official holiday/adjusted-workday calendar
- historical observed weather aligned to training timestamps

Expected training covariate columns:

```python
[
    "is_holiday",
    "is_workday",
    "is_day_before_holiday",
    "is_day_after_holiday",
    "temperature_c",
    "humidity_pct",
]
```

- [ ] **Step 2: Run the configuration test and verify RED**

Run:

```powershell
pytest tests/test_forecasting_covariates.py -q
```

Expected: fail because trainer covariate loading does not exist.

- [ ] **Step 3: Update trainer configuration**

Add to `examples/forecaster_hehong.yaml`:

```yaml
covariates:
  holiday_calendar_file: scenarios/china_workday_calendar.xlsx
  holiday_calendar_sheet: calendar
  historical_weather_file: scenarios/hehong_weather_history.xlsx
  historical_weather_sheet: weather
  weather_columns:
    temperature_c: [temperature, temp, 温度]
    humidity_pct: [humidity, 湿度]
```

Add to `models/forecasting/config.yaml`:

```yaml
features:
  external_columns:
    - is_holiday
    - is_workday
    - is_day_before_holiday
    - is_day_after_holiday
    - temperature_c
    - humidity_pct
```

Update `microgrid/forecaster.py` to:

- build calendar features for the training timeline
- align historical weather to 15-minute timestamps
- pass the combined DataFrame into `LoadForecaster.train`
- pass aligned covariates into every backtest prediction window
- export baseline and enhanced metrics, including peak-focused metrics

- [ ] **Step 4: Add clear missing-file behavior**

Required behavior:

- Holiday calendar may be absent; fallback uses weekday/weekend and logs that official holiday/adjusted-workday features are unavailable.
- If weather features are enabled but historical weather is absent, training must stop with a clear error.
- Do not silently fill all weather values with zero.

- [ ] **Step 5: Run trainer tests and a real training smoke test**

Run:

```powershell
pytest tests/test_forecasting_covariates.py -q
python -m microgrid.forecaster --config examples/forecaster_hehong.yaml
```

Expected:

- Tests pass.
- Training prints feature importance including configured external columns.
- Evaluation includes MAE, RMSE, MAPE, positive-error P90, maximum under-forecast, and actual-peak error.

- [ ] **Step 6: Commit**

```powershell
git add microgrid/forecaster.py models/forecasting/config.yaml examples/forecaster_hehong.yaml tests/test_forecasting_covariates.py
git commit -m "feat: train load forecast with calendar and weather covariates"
```

---

### Task 6: Pass Future Covariates Into MPC Predictions

**Files:**
- Modify: `microgrid/mpc.py`
- Modify: `examples/mpc_hehong.yaml`
- Modify: `tests/test_forecasting_covariates.py`

- [ ] **Step 1: Write a failing MPC horizon-covariate test**

The test must prove:

- an MPC decision at time `t` passes exactly `t:t+H` future covariate rows
- the rows are ordered at 15-minute resolution
- a model requiring temperature fails before solving when future weather is missing

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
pytest tests/test_forecasting_covariates.py -q
```

Expected: fail because MPC does not pass future covariates to `forecaster.predict`.

- [ ] **Step 3: Configure MPC future covariates**

Add to `examples/mpc_hehong.yaml`:

```yaml
forecast_covariates:
  holiday_calendar_file: scenarios/china_workday_calendar.xlsx
  holiday_calendar_sheet: calendar
  future_weather_file: scenarios/hehong_weather_forecast.xlsx
  future_weather_sheet: weather
```

The future weather file must contain the full simulation timeline during historical replay. In real operation it must be refreshed from a weather forecast provider before each MPC run.

- [ ] **Step 4: Wire future covariates into MPC**

Update `microgrid/mpc.py` to:

- load/generate calendar covariates for the full scenario timeline
- load future weather forecast covariates
- build `future_covariates = covariates_for_horizon(ts_all[abs_t:abs_end], ...)`
- call:

```python
fc_r = forecaster.predict(
    hist,
    st,
    end - t,
    future_covariates=future_covariates,
)
```

- fail before optimization with a timestamp-specific error if a required future weather row is missing
- leave `file`, `naive96`, and `naive672` forecast modes unchanged

- [ ] **Step 5: Run tests and a short MPC smoke test**

Temporarily run a short configuration with `mpc.days: 1`:

```powershell
pytest tests/test_forecasting_covariates.py -q
python -m microgrid.mpc --config examples/mpc_hehong.yaml
```

Expected:

- No covariate alignment errors.
- MPC logs the loaded covariate columns.
- The model completes the short run.

- [ ] **Step 6: Commit**

```powershell
git add microgrid/mpc.py examples/mpc_hehong.yaml tests/test_forecasting_covariates.py
git commit -m "feat: use future covariates in MPC load forecasts"
```

---

### Task 7: Compare Baseline and Enhanced Forecasts Before Enabling in Full MPC

**Files:**
- Modify: `microgrid/forecaster.py`
- Create: `docs/load_forecast_covariates.md`

- [ ] **Step 1: Add baseline-versus-enhanced evaluation output**

Export an Excel workbook with:

- `model_summary`
  - baseline historical-load model metrics
  - enhanced covariate model metrics
- `horizon_metrics`
  - `0-6h`, `6-12h`, `12-24h`, `24-36h`, `36-48h`
- `peak_metrics`
  - positive-error P80/P90
  - maximum under-forecast
  - actual-peak error
  - peak-period MAE
- `forecast_samples`
  - timestamp, actual load, baseline prediction, enhanced prediction, both errors

- [ ] **Step 2: Document data contracts and leakage rules**

In `docs/load_forecast_covariates.md`, document:

```text
Historical weather:
  Used only for model training and historical feature construction.

Future weather forecast:
  Used for MPC future horizons and rolling backtests.
  Must represent information available at the decision time.

Holiday calendar:
  Must include official holidays and adjusted working weekends.
```

- [ ] **Step 3: Run full verification**

Run:

```powershell
pytest -q
python -m compileall microgrid models tests
python -c "import yaml; from pathlib import Path; [yaml.safe_load(Path(p).read_text(encoding='utf-8')) for p in ['models/forecasting/config.yaml','examples/forecaster_hehong.yaml','examples/mpc_hehong.yaml']]; print('yaml ok')"
```

Expected:

- All tests pass.
- Compilation succeeds.
- All YAML files parse.

- [ ] **Step 4: Run acceptance comparison**

Train and compare both models:

```powershell
python -m microgrid.forecaster --config examples/forecaster_hehong.yaml
```

Acceptance criteria:

- Enhanced model does not worsen overall MAE by more than 5%.
- Enhanced model lowers positive under-forecast P90 or actual-peak error.
- Feature importance confirms at least one new covariate contributes.

Then run 7-day MPC A/B comparisons:

```text
A: baseline model using existing historical/calendar features
B: enhanced model using holiday and weather covariates
```

Acceptance criteria:

- B does not create a higher unexplained charging peak.
- B lowers or preserves MPC peak demand.
- B reduces the size or frequency of realtime power-layer corrections.

- [ ] **Step 5: Commit**

```powershell
git add microgrid/forecaster.py docs/load_forecast_covariates.md
git commit -m "docs: describe forecast covariate evaluation"
```
