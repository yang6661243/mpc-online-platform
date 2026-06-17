# Microgrid CLI Tools

微电网储能调度三件套：负荷预测 → 最优求解 → MPC 滚动调度。

## 安装

```bash
pip install -r requirements.txt
```
1
## 数据准备

所有输入数据放在一个 Excel 里，每个 sheet 一类数据：

| Sheet | 内容 | 示例列名 |
|-------|------|------|
| `load_apr` | 负荷（时间 + 有功） | 时间, 有功 |
| `pv_apr` | 光伏辐照 + 风速 | time, global_tilted_irradiance (W/m2), wind_speed_100m (km/h) |
| `price` | 购售电价 | 时间, 购电价, 售电价 |
| `train_mar` | 训练用负荷 | 时间, 有功 |

参考：`mpc/scenarios/hehonghuajin.xlsx`

## 三个命令

一个配置文件 `config.yaml`，三个命令共用：

### 1. 训练负荷预测模型

```bash
python -m mpc.microgrid.forecaster --config mpc/configs/examples/forecaster.yaml --profile hehong_weather
```

### 2. 跑 MILP 最优求解

```bash
python -m mpc.microgrid.solver --config mpc/configs/examples/solver.yaml --profile hehong
```

### 3. 跑 MPC 滚动调度

```bash
python -m mpc.microgrid.mpc --config mpc/configs/examples/mpc.yaml --profile hehong_weather
```

## 配置文件

```yaml
# 数据文件
scenario:
  data_file: mpc/scenarios/hehonghuajin.xlsx
  sheets:
    load: load_apr        # 负荷数据所在 sheet
    pv_wind: pv_apr       # 光伏/风电数据所在 sheet
    price: price          # 电价数据所在 sheet

# 电池
battery:
  capacity_kwh: 783
  charge_max_kw: 375
  discharge_max_kw: 375
  soc_init: 0.5
  soc_min: 0.1
  soc_max: 0.9
  charge_eff: 0.95
  discharge_eff: 0.95

# 设备
device:
  load_base_kw: 1000         # 实际负荷 = 表中数值 x base
  pv_capacity_kw: 350
  pv_efficiency: 0.95
  wind_capacity_kw: 0
  wind_efficiency: 0

# 电网
grid:
  anti_backflow: true
  import_max_kw: 5000
  export_max_kw: 0
  transformer_capacity_kw: 5000

# 成本
cost:
  c_deg: 0.05                # 电池衰减（元/kWh）
  demand_rate: 39.0          # 需量费（元/kW/月）
  capacity_rate: 0
  billing_days: 30

# 输出
output: outputs/result.xlsx
```

forecaster 配置额外需要：
```yaml
data:
  file: mpc/scenarios/hehonghuajin.xlsx
  sheet: train_mar
model_path: outputs/models/my_model.joblib
train_ratio: 0.8
```

MPC 配置额外需要：
```yaml
mpc:
  days: 30
  horizon_steps: 192
  forecast_mode: lightgbm
  model_path: outputs/models/my_model.joblib
  start_step: 0
```

## 输出

每个工具输出一个 Excel，3 个 sheet：
- `15min_trajectory` — 每 15 分钟轨迹
- `daily_summary` — 日度汇总
- `cost_summary` — 月度成本（MPC 含达成率）
