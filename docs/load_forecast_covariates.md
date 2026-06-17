# 负荷预测协变量实施文档

## 概述

在现有 15 分钟 LightGBM 负荷预测模型中集成了外部协变量支持：
- **公共节假日特征**：is_public_holiday、is_adjusted_workday、is_day_before_holiday、is_day_after_holiday
- **可选工厂运营特征**：is_factory_workday、is_factory_overtime、is_factory_shutdown
- **可选天气特征**：temperature_c、humidity_pct（线性插值到 15 分钟）

## 新增文件

| 文件 | 说明 |
|------|------|
| `ml_core/forecasting/covariates.py` | 协变量模块：日历特征生成、天气对齐、预测窗口提取 |
| `tests/test_forecasting_covariates.py` | 协变量和特征集成的完整测试（54 个测试） |

## 修改文件

| 文件 | 改动 |
|------|------|
| `ml_core/forecasting/features.py` | build_features/prepare_prediction_row 支持 covariates/future_covariates 参数 |
| `ml_core/forecasting/load_forecaster.py` | train/predict/evaluate/save/load 扩展协变量支持，向后兼容 |
| `ml_core/forecasting/metrics.py` | 新增 compute_peak_risk_metrics |
| `ml_core/forecasting/config.yaml` | 新增 covariates 配置段 |
| `ml_core/forecasting/__init__.py` | 导出新函数 |
| `microgrid/forecaster.py` | 训练脚本读取协变量配置并传入模型 |
| `microgrid/mpc.py` | MPC 运行时读取未来协变量并传入预测 |
| `configs/examples/forecaster.yaml` | 在 `hehong_weather` 等 profile 中配置 covariates |
| `configs/examples/mpc.yaml` | 在 `hehong_weather` 等 profile 中配置 forecast_covariates |

## 核心接口

### build_calendar_covariates(timestamps, public_calendar=None, factory_calendar=None)

从公共节假日日历和可选工厂运营日历生成协变量 DataFrame。

```python
from models.forecasting.covariates import build_calendar_covariates

cov = build_calendar_covariates(
    timestamps=pd.date_range('2026-10-01', periods=96, freq='15min'),
    public_calendar=pd.DataFrame({
        'date': pd.to_datetime(['2026-10-01', '2026-10-02']),
        'day_type': ['holiday', 'holiday'],
    }),
)
# 输出列: time, is_weekend, is_public_holiday, is_adjusted_workday,
#         is_day_before_holiday, is_day_after_holiday
```

### align_covariates(timestamps, covariates, numeric_columns)

将小时级天气数据线性插值到 15 分钟时间线。

```python
from models.forecasting.covariates import align_covariates

weather_15min = align_covariates(
    timestamps=target_15min_ts,
    covariates=hourly_weather_df,  # 含 'time' 列
    numeric_columns=['temperature_c', 'humidity_pct'],
)
```

### covariates_for_horizon(timestamps, covariates, required_columns)

从完整协变量 DataFrame 中提取 MPC 预测窗口对应的行。

```python
from models.forecasting.covariates import covariates_for_horizon

future_cov = covariates_for_horizon(
    timestamps=future_48h_ts,
    covariates=all_covariates,
    required_columns=['is_public_holiday', 'temperature_c'],
)
```

### compute_peak_risk_metrics(actual, predicted, peak_quantile=0.90)

计算削峰相关的预测误差指标。

```python
from models.forecasting.metrics import compute_peak_risk_metrics

metrics = compute_peak_risk_metrics(actual, predicted)
# 输出: positive_error_p80, positive_error_p90, max_under_forecast,
#       actual_peak_error, peak_period_mae
```

## 配置方法

### 训练配置 (configs/examples/forecaster.yaml --profile hehong_weather)

```yaml
features:
  external_columns:
    - is_public_holiday
    - is_adjusted_workday
    - is_day_before_holiday
    - is_day_after_holiday
    # - temperature_c       # 天气数据到位后取消注释
    # - humidity_pct

covariates:
  holiday_calendar_file: scenarios/china_public_holiday_calendar_2026.xlsx
  holiday_calendar_sheet: calendar
  historical_weather_file: scenarios/hehong_weather_history.xlsx
  historical_weather_sheet: weather
  weather_columns:
    temperature_c: [temperature, temp, 温度]
    humidity_pct: [humidity, 湿度]
```

### MPC 配置 (configs/examples/mpc.yaml --profile hehong_weather)

```yaml
forecast_covariates:
  holiday_calendar_file: scenarios/china_public_holiday_calendar_2026.xlsx
  holiday_calendar_sheet: calendar
  future_weather_file: scenarios/hehong_weather_forecast.xlsx
  future_weather_sheet: weather
```

## 数据格式

### 节假日日历 (.xlsx)

| date | day_type |
|------|----------|
| 2026-10-01 | holiday |
| 2026-10-02 | holiday |

### 天气数据 (.xlsx)

| time | temperature | humidity |
|------|-------------|----------|
| 2026-10-01 00:00 | 25.3 | 68.2 |
| 2026-10-01 01:00 | 24.8 | 70.1 |

### 可选工厂运营日历 (.xlsx)

| date | is_factory_workday | is_factory_overtime | is_factory_shutdown |
|------|--------------------|--------------------|--------------------|
| 2026-10-10 | 1 | 1 | 0 |
| 2026-10-03 | 0 | 0 | 1 |

## 关键约束

1. **禁止静默填充缺失天气**：天气数据缺失时必须明确报错，禁止用 0 填充
2. **禁止用未来实际天气替代预测天气**：MPC 回放仿真必须使用天气预报数据
3. **工厂运营状态不自动推断**：不能从周末或公共节假日推断工厂是否生产
4. **训练-预测特征一致性**：训练和递归预测使用完全相同的特征列和顺序
5. **向后兼容**：旧模型（无 external_columns）加载后正常使用，不影响现有功能

## 验收标准

1. 增强模型总体 MAE 不得恶化超过 5%
2. 增强模型应降低正向低估误差 P90 或实际峰值误差
3. 特征重要性中至少有一个新增协变量产生贡献
4. 增强模型不能制造更高的异常充电峰
5. 增强模型应降低或至少保持 MPC 电网峰值

## Direct Horizon 模式

`lightgbm_direct_horizon` 使用一个 LightGBM 模型直接预测未来 1..192 个 15 分钟点。通过 `horizon_step` 和 `horizon_hours` 区分预测步长，不把前一步预测值回填为下一步输入，因此不会产生递归误差累积。

推荐配置：

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

## 验证命令

```powershell
# 运行全部测试
pytest tests/ -q

# 语法检查
python -m compileall microgrid models tests

# YAML 配置检查
python -c "import yaml; from pathlib import Path; [yaml.safe_load(Path(p).read_text(encoding='utf-8')) for p in ['ml_core/forecasting/config.yaml','mpc/configs/examples/forecaster.yaml','mpc/configs/examples/mpc.yaml']]; print('ok')"

# 真实数据训练（天气数据到位后）
python -m mpc.microgrid.forecaster --config configs/examples/forecaster.yaml --profile hehong_weather
```
