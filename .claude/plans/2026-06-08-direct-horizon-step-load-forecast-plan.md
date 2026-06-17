# 单模型 Direct Horizon 负荷预测 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增“单模型 Direct + horizon_step”负荷预测模式，用一个 LightGBM 直接输出 48 小时 192 点预测，避免当前递归模型的误差滚动累积。

**Architecture:** 保留现有递归 LightGBM 作为兼容模式，新增 `model.type: lightgbm_direct_horizon`。Direct 模式训练时把每个预测发起时刻展开为多个 horizon 样本，每行包含“发起时刻可见的历史负荷特征 + 目标时刻日历/外部协变量 + horizon_step”，标签是目标时刻真实负荷。预测时一次构造 192 行特征并一次性 `predict`，不把预测值回填到历史。

**Tech Stack:** Python、pandas、NumPy、LightGBM、joblib、PyYAML、pytest、openpyxl。

---

## 一、范围说明

本计划只做第一步：**单模型 Direct + horizon_step**。

本计划不做：

- 不做 192 个模型。
- 不做分段模型。
- 不接入新的天气或节假日数据源。
- 不改 MPC 优化器逻辑，只保证 `LoadForecaster.predict(..., horizon_steps=192)` 仍返回 192 点数组。

当前工作目录不是 Git 仓库时，不要求执行 `git commit`。如果执行环境后来变成 Git 仓库，可以按每个任务末尾的文件清单提交。

---

## 二、文件结构

**新增文件**

- `models/forecasting/direct_features.py`  
  Direct 多步训练样本和预测样本构造。只负责 DataFrame 特征展开，不训练模型。

- `tests/test_forecasting_direct_horizon.py`  
  Direct 样本构造、非递归预测、保存加载、评估行为测试。

**修改文件**

- `models/forecasting/load_forecaster.py`  
  增加 `lightgbm_direct_horizon` 模式；训练、预测、保存、加载兼容两种模式。

- `models/forecasting/__init__.py`  
  导出 Direct 特征构造函数，便于测试和后续使用。

- `models/forecasting/config.yaml`  
  增加 Direct 模式配置示例字段，默认仍可保持递归模式，避免破坏旧流程。

- `examples/forecaster_hehong.yaml`  
  增加第一阶段 Direct 模式配置，保留 `external_columns: []`。

- `docs/load_forecast_covariates.md`  
  补充 Direct 模式说明和验收口径。

---

## 三、设计要点

### Direct 训练样本定义

给定 15 分钟负荷序列 `value[t]`，发起时刻索引 `i`，预测步长 `h`：

```text
输入特征：
  lag_1                = value[i]
  lag_2                = value[i - 1]
  lag_3                = value[i - 2]
  lag_96               = value[i - 95]
  lag_672              = value[i - 671]
  rolling_mean_96      = mean(value[i-95 : i+1])
  rolling_std_96       = std(value[i-95 : i+1])
  target calendar      = time[i + h] 的 hour/dayofweek/month/is_weekend/quarter_hour/sin/cos
  horizon_step         = h
  horizon_hours        = h * 0.25
  external_columns     = time[i + h] 对应的未来协变量，第一阶段默认为空

标签：
  y = value[i + h]
```

注意：Direct 训练时，`lag_1` 是预测发起时刻最后一个已知真实负荷，而不是目标时刻前 15 分钟的真实负荷。这样预测时不会需要未来真实值。

### Direct 预测定义

预测时给定当前历史 `history` 和第一个预测时刻 `start_time`：

```text
current_time = start_time - 15min
h = 1..horizon_steps
target_time = start_time + (h - 1) * 15min
```

每个 `h` 构造一行特征，所有行共享同一份真实历史负荷特征，只改变 `horizon_step / horizon_hours / target_time calendar / future_covariates`。

---

## 四、任务一：新增 Direct 特征构造模块

**Files:**

- Create: `models/forecasting/direct_features.py`
- Create: `tests/test_forecasting_direct_horizon.py`

- [ ] **Step 1: 写失败测试：Direct 训练样本不使用未来负荷做 lag**

在 `tests/test_forecasting_direct_horizon.py` 写入：

```python
import numpy as np
import pandas as pd
import pytest

from models.forecasting.direct_features import build_direct_training_frame


def test_direct_training_rows_use_origin_history_not_future_lags():
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
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```powershell
pytest tests/test_forecasting_direct_horizon.py::test_direct_training_rows_use_origin_history_not_future_lags -q
```

Expected:

```text
ModuleNotFoundError: No module named 'models.forecasting.direct_features'
```

- [ ] **Step 3: 实现 `models/forecasting/direct_features.py` 最小版本**

新增文件：

```python
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
```

- [ ] **Step 4: 运行测试确认通过**

Run:

```powershell
pytest tests/test_forecasting_direct_horizon.py::test_direct_training_rows_use_origin_history_not_future_lags -q
```

Expected:

```text
1 passed
```

---

## 五、任务二：Direct 预测帧必须一次性构造 192 行且不递归

**Files:**

- Modify: `tests/test_forecasting_direct_horizon.py`
- Modify: `models/forecasting/direct_features.py`

- [ ] **Step 1: 写失败测试：预测帧每个 horizon 共享同一份历史 lag**

追加测试：

```python
from models.forecasting.direct_features import build_direct_prediction_frame


def test_direct_prediction_frame_reuses_same_true_history_for_all_horizons():
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
```

- [ ] **Step 2: 运行测试**

Run:

```powershell
pytest tests/test_forecasting_direct_horizon.py::test_direct_prediction_frame_reuses_same_true_history_for_all_horizons -q
```

Expected:

```text
1 passed
```

如果失败，修正 `build_direct_prediction_frame`，保证所有 horizon 的 lag 特征都来自传入的同一份 `history`。

---

## 六、任务三：在 LoadForecaster 中接入 Direct 模式

**Files:**

- Modify: `models/forecasting/load_forecaster.py`
- Modify: `tests/test_forecasting_direct_horizon.py`

- [ ] **Step 1: 写失败测试：Direct 模式训练并返回完整 horizon**

追加测试：

```python
from models.forecasting.load_forecaster import LoadForecaster


def test_load_forecaster_direct_mode_predicts_full_horizon_without_recursion():
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
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```powershell
pytest tests/test_forecasting_direct_horizon.py::test_load_forecaster_direct_mode_predicts_full_horizon_without_recursion -q
```

Expected:

```text
FAIL
```

失败原因应是 `LoadForecaster` 尚未识别 `lightgbm_direct_horizon`。

- [ ] **Step 3: 修改 `models/forecasting/load_forecaster.py`**

在 import 区域增加：

```python
from .direct_features import build_direct_training_frame, build_direct_prediction_frame
```

在 `__init__` 中设置模型类型：

```python
self._model_type = self._cfg.get("model", {}).get("type", "lightgbm_recursive")
```

在 `train()` 中，将原来的 feature 构造分支改为：

```python
df = pd.DataFrame({"time": timestamps, "value": load_ratio})

if self._model_type == "lightgbm_direct_horizon":
    direct_cfg = dict(self._feature_config)
    direct_cfg["horizon_steps"] = self._cfg.get("model", {}).get("horizon_steps", 192)
    df, feature_cols = build_direct_training_frame(
        df,
        config=direct_cfg,
        covariates=covariates,
    )
    target_col = "target"
else:
    df, feature_cols = build_features(
        df,
        self._feature_config,
        covariates=covariates,
    )
    df = df.dropna(subset=feature_cols).reset_index(drop=True)
    target_col = "value"

self._feature_cols = feature_cols
```

并将训练目标读取从：

```python
y_train = train_df["value"].values
y_val = val_df["value"].values
```

改为：

```python
y_train = train_df[target_col].values
y_val = val_df[target_col].values
```

在评估训练误差时保持使用 `y_train/y_val`。

在 `_eval_metrics` 中增加：

```python
"model_type": self._model_type,
```

在 `predict()` 开头保留现有 fitted 和 covariate 校验，然后在递归逻辑前增加 Direct 分支：

```python
if self._model_type == "lightgbm_direct_horizon":
    direct_cfg = dict(self._feature_config)
    pred_df, feature_cols = build_direct_prediction_frame(
        history=history,
        start_time=pd.Timestamp(start_time),
        config=direct_cfg,
        horizon_steps=horizon_steps,
        future_covariates=future_covariates,
    )
    X = pred_df[self._feature_cols]
    forecast = self._model.predict(X)
    return np.clip(np.asarray(forecast, dtype=np.float64), 0.0, 1.0)
```

在 `save()` payload 中增加：

```python
"model_type": self._model_type,
```

在 `load()` 中增加：

```python
obj._model_type = data.get("model_type") or obj._cfg.get("model", {}).get("type", "lightgbm_recursive")
```

兼容旧模型：如果没有 `model_type`，默认 `lightgbm_recursive`。

- [ ] **Step 4: 运行 Direct 模式测试**

Run:

```powershell
pytest tests/test_forecasting_direct_horizon.py::test_load_forecaster_direct_mode_predicts_full_horizon_without_recursion -q
```

Expected:

```text
1 passed
```

- [ ] **Step 5: 运行现有协变量回归测试**

Run:

```powershell
pytest tests/test_forecasting_covariates.py -q
```

Expected:

```text
全部通过
```

---

## 七、任务四：保存加载必须保留 Direct 模式

**Files:**

- Modify: `tests/test_forecasting_direct_horizon.py`
- Modify: `models/forecasting/load_forecaster.py`

- [ ] **Step 1: 写失败测试：Direct 模型保存加载后仍按 Direct 预测**

追加测试：

```python
def test_direct_model_save_load_preserves_model_type(tmp_path):
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
```

- [ ] **Step 2: 运行测试**

Run:

```powershell
pytest tests/test_forecasting_direct_horizon.py::test_direct_model_save_load_preserves_model_type -q
```

Expected:

```text
1 passed
```

如果失败，修正 `save/load` 中的 `model_type` 持久化。

---

## 八、任务五：训练入口支持 Direct 配置并保持评估工作簿不变

**Files:**

- Modify: `examples/forecaster_hehong.yaml`
- Modify: `models/forecasting/config.yaml`
- Modify: `docs/load_forecast_covariates.md`

- [ ] **Step 1: 修改 `examples/forecaster_hehong.yaml`**

将模型配置调整为第一阶段 Direct 模式：

```yaml
model:
  type: lightgbm_direct_horizon
  horizon_steps: 192
  min_history_steps: 672
```

保留历史特征：

```yaml
features:
  lags: [1, 2, 3, 96, 672]
  rolling_windows: [96]
  calendar: true
  cyclical: true
  external_columns: []
```

保留协变量配置为空数据源：

```yaml
covariates:
  holiday_calendar_file: null
  holiday_calendar_sheet: calendar
  historical_weather_file: null
  historical_weather_sheet: weather
  weather_columns:
    temperature_c: [temperature, temp, 温度]
    humidity_pct: [humidity, 湿度]
```

- [ ] **Step 2: 修改 `models/forecasting/config.yaml` 增加注释**

在 `model:` 下说明可选类型：

```yaml
model:
  type: lightgbm_recursive      # 可选: lightgbm_recursive / lightgbm_direct_horizon
  horizon_steps: 192
  min_history_steps: 672
```

默认可保持 `lightgbm_recursive`，避免影响旧默认行为。

- [ ] **Step 3: 更新文档**

在 `docs/load_forecast_covariates.md` 增加一节：

````markdown
## Direct Horizon 模式

`lightgbm_direct_horizon` 使用一个 LightGBM 模型直接预测未来 1..192 个 15 分钟点。
它通过 `horizon_step` 和 `horizon_hours` 区分预测步长，不把前一步预测值回填为下一步输入，因此不会产生递归误差累积。

推荐第一阶段配置：

```yaml
model:
  type: lightgbm_direct_horizon
  horizon_steps: 192

features:
  lags: [1, 2, 3, 96, 672]
  rolling_windows: [96]
  calendar: true
  cyclical: true
  external_columns: []
```

验收时重点比较 `horizon_metrics` 中 24-36h 和 36-48h 的 MAE 是否较递归模型明显下降。
````

- [ ] **Step 4: YAML 解析检查**

Run:

```powershell
python -c "import yaml; from pathlib import Path; [yaml.safe_load(Path(p).read_text(encoding='utf-8')) for p in ['models/forecasting/config.yaml','examples/forecaster_hehong.yaml','examples/mpc_hehong.yaml']]; print('yaml ok')"
```

Expected:

```text
yaml ok
```

---

## 九、任务六：完整训练和评估验收

**Files:**

- Modify: `microgrid/forecaster.py` only if existing code assumes recursive-only metrics.
- No change needed if existing evaluation workbook works through `LoadForecaster.predict`.

- [ ] **Step 1: 跑 Direct 专项测试**

Run:

```powershell
pytest tests/test_forecasting_direct_horizon.py -q
```

Expected:

```text
全部通过
```

- [ ] **Step 2: 跑全量测试**

Run:

```powershell
pytest -q
```

Expected:

```text
全部通过
```

- [ ] **Step 3: 编译检查**

Run:

```powershell
python -m compileall microgrid models tests
```

Expected:

```text
exit code 0
```

- [ ] **Step 4: 运行训练入口**

Run:

```powershell
python -m microgrid.forecaster --config examples/forecaster_hehong.yaml
```

Expected:

```text
TRAINING COMPLETE
Saved: outputs/models/hehonghuajin.joblib
Saved: outputs/load_forecast_evaluation.xlsx
```

- [ ] **Step 5: 验收评估工作簿**

用 Python 检查输出：

```powershell
$script = @'
from openpyxl import load_workbook
wb = load_workbook('outputs/load_forecast_evaluation.xlsx', read_only=True, data_only=True)
print(wb.sheetnames)
for row in wb['horizon_metrics'].iter_rows(values_only=True):
    print(row)
wb.close()
'@
$script | python -
```

Expected:

```text
['model_summary', 'horizon_metrics', 'peak_metrics', 'forecast_samples']
```

并人工比较：

```text
Direct LightGBM 的 24-36h、36-48h MAE 应明显低于当前递归模型旧结果：
24-36h 旧递归约 0.193793
36-48h 旧递归约 0.271251
```

第一阶段不要求 Direct 一定全面战胜 `Naive_t96`，但必须证明 48h 远期不再出现递归发散形状。

---

## 十、验收标准

第一阶段通过标准：

```text
1. pytest 全量通过。
2. YAML 和 compileall 通过。
3. Direct 模式模型文件能保存加载。
4. outputs/load_forecast_evaluation.xlsx 正常生成四个 sheet。
5. feature_cols 中包含 horizon_step 和 horizon_hours。
6. 36-48h MAE 较当前递归模型 0.271251 明显下降。
7. 预测过程中不把前一步预测值写回 history。
```

如果 Direct 模式仍然输给 `Naive_t96`，不要继续调参超过一次。先输出评估结果，再决定是否进入第二步“分段模型”计划。

---

## 十一、自检清单

- [ ] 计划只覆盖单模型 Direct + horizon_step。
- [ ] 没有要求实现 192 个模型。
- [ ] 没有要求实现分段模型。
- [ ] 所有新增测试都有明确代码和运行命令。
- [ ] Direct 样本定义没有使用未来真实负荷作为 lag。
- [ ] 预测接口仍兼容 MPC 当前调用方式。
- [ ] 真实训练入口仍生成原评估工作簿。
