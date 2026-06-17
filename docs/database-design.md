# 数据库设计方案

> 最后更新：2026-06-17
> 轻易不要改动，改动需同步更新本文档。

## 总体结构

4 张表，按数据流排序：

```
raw_telemetry ──→ telemetry_15min ──→ 前端展示 / MPC 求解器
                                       │
                      intra_minute_points ──→ 主折线图（分钟级）
                      mpc_runs ──→ 周边卡片 / 对比图表
```

---

## 表 1：raw_telemetry（原始数据）

**用途**：存储来自 Excel 导入或网页插件的原始电表数据，按时间戳落库。

```
┌─────────────────────┬──────────┬──────────────────────────────────┐
│ 字段                │ 类型     │ 说明                             │
├─────────────────────┼──────────┼──────────────────────────────────┤
│ id                  │ INTEGER  │ 主键，自增                       │
│ plant_id            │ VARCHAR  │ 电站标识，索引                   │
│ time                │ DATETIME │ 数据时间戳，索引                 │
│ grid_power_kw       │ FLOAT    │ 电网功率（kW）                   │
│ battery_power_kw    │ FLOAT    │ 储能功率（kW），充电负/放电正     │
│ soc                 │ FLOAT    │ 荷电状态（0~1）                  │
│ source              │ VARCHAR  │ 来源：import / plugin / manual   │
│ created_at          │ DATETIME │ 入库时间，自动生成               │
└─────────────────────┴──────────┴──────────────────────────────────┘

唯一约束: UNIQUE(plant_id, time)  ← 同电站同时刻覆盖写入
索引:     INDEX(plant_id), INDEX(time)
```

**设计要点**：
- 电网功率和储能功率合在一张表，跟 Excel 一行一行的格式对应，导入无需拆解
- 支持 1 分钟、5 分钟等任意粒度混存，时间戳天然区分
- 覆盖写入机制：`(plant_id, time)` 相同的记录直接 update

---

## 表 2：telemetry_15min（15 分钟聚合 + MPC 计划）

**用途**：服务 MPC 求解器输入，同时作为前端 factory vs MPC 对比、收益/需量实时计算的数据源。

```
┌──────────────────────────┬──────────┬──────────────────────────────────┐
│ 字段                     │ 类型     │ 说明                             │
├──────────────────────────┼──────────┼──────────────────────────────────┤
│ id                       │ INTEGER  │ 主键，自增                       │
│ plant_id                 │ VARCHAR  │ 电站标识，索引                   │
│ start_time               │ DATETIME │ 窗口起始时间，索引               │
│ end_time                 │ DATETIME │ 窗口结束时间，索引               │
├──────────────────────────┼──────────┼──────────────────────────────────┤
│ 工厂侧（聚合时计算）      │          │                                  │
│ grid_power_kw_avg        │ FLOAT    │ 电网功率均值（kW）               │
│ grid_power_kw_max        │ FLOAT    │ 电网功率最大值（kW）             │
│ battery_power_kw_avg     │ FLOAT    │ 储能功率均值（kW）               │
│ load_minus_pv_kw_avg     │ FLOAT    │ 净负荷 = grid + battery（kW）    │
│ soc_start                │ FLOAT    │ 窗口起始 SOC                     │
│ soc_end                  │ FLOAT    │ 窗口结束 SOC                     │
│ grid_sample_count        │ INTEGER  │ 窗口内原始电网点数               │
│ battery_sample_count     │ INTEGER  │ 窗口内原始储能点数               │
│ quality_flag             │ VARCHAR  │ 质量标记（ok/partial/missing）    │
├──────────────────────────┼──────────┼──────────────────────────────────┤
│ MPC 侧（MPC 求解器回填）  │          │                                  │
│ mpc_grid_power_kw        │ FLOAT    │ MPC 计划电网功率（kW）           │
│ mpc_battery_power_kw     │ FLOAT    │ MPC 计划储能功率（kW）           │
│ mpc_soc                  │ FLOAT    │ MPC 计划 SOC                     │
│ mpc_load_kw              │ FLOAT    │ MPC 计划负荷（kW）               │
│ mpc_pv_kw                │ FLOAT    │ MPC 计划光伏出力（kW）           │
│ buy_price                │ FLOAT    │ 购电电价（元/kWh）               │
│ sell_price               │ FLOAT    │ 售电电价（元/kWh）               │
│ run_id                   │ VARCHAR  │ 关联的 MPC 运行 ID               │
├──────────────────────────┼──────────┼──────────────────────────────────┤
│ created_at               │ DATETIME │ 创建时间，自动生成               │
│ updated_at               │ DATETIME │ 更新时间，自动更新               │
└──────────────────────────┴──────────┴──────────────────────────────────┘

唯一约束: UNIQUE(plant_id, start_time, end_time)
索引:     INDEX(plant_id), INDEX(start_time), INDEX(end_time)
```

**设计要点**：
- 工厂侧和 MPC 侧合在一行，一行就是一个 15 分钟切片的完整对比数据
- 前端查询不跨表 JOIN，直接扫这一张
- MPC 求解器跑完后回填 `mpc_*` 字段，不新增行
- 收益/需量实时计算：当月表 2 数据 ~2880 行，扫描 < 10ms

**实时计算示例**：
```python
# 收益：battery_power_kw_avg × 对应电价 × 0.25h，逐窗口累加
# 需量：max(grid_power_kw_avg) vs max(mpc_grid_power_kw)
```

---

## 表 3：intra_minute_points（Fuzzy PID 分钟级曲线）

**用途**：主折线图高分辨率展示（1 分钟粒度），存储 Fuzzy PID 分解出的逐分钟 PCS 控制指令和实际执行值。

```
┌─────────────────┬──────────┬──────────────────────────────────────┐
│ 字段            │ 类型     │ 说明                                 │
├─────────────────┼──────────┼──────────────────────────────────────┤
│ id              │ INTEGER  │ 主键，自增                           │
│ run_id          │ VARCHAR  │ MPC 运行 ID，索引                    │
│ plant_id        │ VARCHAR  │ 电站标识，索引                       │
│ profile         │ VARCHAR  │ 优化目标（demand100/demand70/...）    │
│ time            │ DATETIME │ 分钟级时间戳，索引                   │
│ pcs_command_kw  │ FLOAT    │ PCS 指令功率（kW）                   │
│ pcs_actual_kw   │ FLOAT    │ PCS 实际功率（kW）                   │
│ soc_guide       │ FLOAT    │ SOC 引导值                           │
│ soc_actual      │ FLOAT    │ SOC 实际值                           │
│ grid_power_kw   │ FLOAT    │ 电网功率（kW）                       │
│ grid_target_kw  │ FLOAT    │ 电网目标（kW）                       │
│ severity        │ VARCHAR  │ 严重程度：safe / warning / critical  │
│ quality_flag    │ VARCHAR  │ 质量标记                             │
│ created_at      │ DATETIME │ 创建时间，自动生成                   │
└─────────────────┴──────────┴──────────────────────────────────────┘

唯一约束: UNIQUE(run_id, plant_id, profile, time)
索引:     INDEX(run_id), INDEX(plant_id), INDEX(time)
```

**设计要点**：
- 服务主折线图中 MPC 策略侧的高分辨率曲线
- 前端 display-series 端点从此表取数据
- 表格 2 的 telemetry_15min 提供 15 分钟粒度的工厂侧数据，两张表时间对齐渲染

---

## 表 4：mpc_runs（MPC 运行记录）

**用途**：记录每次 MPC 运行的元数据、优化目标、策略对比指标和月度需量参考。

```
┌────────────────────────┬──────────┬──────────────────────────────────┐
│ 字段                   │ 类型     │ 说明                             │
├────────────────────────┼──────────┼──────────────────────────────────┤
│ id                     │ INTEGER  │ 主键，自增                       │
│ run_id                 │ VARCHAR  │ 运行 ID，唯一索引                │
│ plant_id               │ VARCHAR  │ 电站标识，索引                   │
│ profile                │ VARCHAR  │ 优化目标（demand100/70/40）      │
│ status                 │ VARCHAR  │ 状态：created/running/succeeded/  │
│                        │          │        failed/imported           │
│ input_start_time       │ DATETIME │ 输入数据起始时间                 │
│ input_end_time         │ DATETIME │ 输入数据结束时间                 │
├────────────────────────┼──────────┼──────────────────────────────────┤
│ 策略对比指标            │          │                                  │
│ actual_peak_kw         │ FLOAT    │ 工厂策略最大需量（kW）           │
│ mpc_peak_kw            │ FLOAT    │ MPC 策略最大需量（kW）           │
│ peak_reduction_kw      │ FLOAT    │ 需量削减（kW）                   │
│ peak_reduction_pct     │ FLOAT    │ 需量削减比例（%）                │
│ actual_cost_yuan       │ FLOAT    │ 工厂策略电费（元）               │
│ mpc_cost_yuan          │ FLOAT    │ MPC 策略电费（元）               │
│ cost_saving_yuan       │ FLOAT    │ 节省电费（元）                   │
│ cost_saving_pct        │ FLOAT    │ 节省比例（%）                    │
├────────────────────────┼──────────┼──────────────────────────────────┤
│ 优化参数                │          │                                  │
│ target_peak_kw         │ FLOAT    │ 目标需量（kW）                   │
│ target_soc             │ FLOAT    │ 目标 SOC（0~1）                  │
│ mode                   │ VARCHAR  │ 模式：deployable / simulation    │
│ reference_peak_kw      │ FLOAT    │ 月度参考需量（用户输入，kW）      │
│ year_month             │ VARCHAR  │ 关联月份（如 "2026-06"）         │
├────────────────────────┼──────────┼──────────────────────────────────┤
│ scenario_path          │ VARCHAR  │ MPC 场景文件路径                 │
│ error_message          │ VARCHAR  │ 错误信息                         │
│ started_at             │ DATETIME │ 开始时间                         │
│ finished_at            │ DATETIME │ 完成时间                         │
│ created_at             │ DATETIME │ 记录创建时间，自动生成           │
└────────────────────────┴──────────┴──────────────────────────────────┘

唯一约束: UNIQUE(run_id)
索引:     INDEX(plant_id), INDEX(profile), INDEX(status)
```

**设计要点**：
- 融合了原 strategy_comparison（对比指标）、mpc_targets（优化目标）、monthly_demand_refs（月度需量参考）
- 一次 MPC 运行一行，不再到处 JOIN
- 月度需量参考跟随最近一条成功的 run 记录
- 优化目标的三个挡位（demand100/70/40）由 `profile` 字段 + 电站配置 YAML 中的参数共同决定
- 周边卡片、收益对比图、需量对比图均从此表获取数据

---

## 数据流总览

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                 │
│  Excel导入 / 网页插件                                            │
│       │                                                         │
│       ▼                                                         │
│  ┌──────────────┐                                               │
│  │ raw_telemetry│  表1: 原始落库 (1min/5min 混存)               │
│  └──────┬───────┘                                               │
│         │ 聚合                                                  │
│         ▼                                                       │
│  ┌──────────────────┐                                           │
│  │ telemetry_15min  │  表2: 15min 聚合 + MPC 计划               │
│  └───┬─────────┬────┘                                           │
│      │         │                                                │
│      │         └──→ MPC 求解器 ──→ 回填 mpc_* 字段               │
│      │                    │                                     │
│      │                    ├──→ ┌─────────────────────┐          │
│      │                    │    │ intra_minute_points │ 表3: 1min│
│      │                    │    └─────────────────────┘          │
│      │                    │                                     │
│      │                    └──→ ┌──────────┐                     │
│      │                         │ mpc_runs │ 表4: 运行记录        │
│      │                         └──────────┘                     │
│      │                                                          │
│      └──────────┬──────────┬──────────┐                         │
│                 ▼          ▼          ▼                         │
│           前端折线图   收益/需量   周边卡片                        │
│           (表2+表3)   (表2实时算)  (表4)                         │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## 前端数据来源速查

| 前端展示              | 数据来源          | 计算方式                          |
|----------------------|------------------|----------------------------------|
| 主折线图-工厂侧       | 表2 actual_*     | 直接取                           |
| 主折线图-MPC 侧       | 表3（分钟级）     | display-series 端点取            |
| 收益折线图            | 表2（当月）      | battery_kw × price × 0.25h 累加 |
| 需量对比图            | 表2 + 表4        | max(grid_kw) vs max(mpc_grid_kw) |
| KPI 卡片-累计收益     | 表4              | cost_saving_yuan                 |
| KPI 卡片-最大需量     | 表4              | actual_peak / mpc_peak           |
| KPI 卡片-峰谷套利     | 表2（当月）      | 实时算                           |
| KPI 卡片-电池循环     | 表2（当月）      | SOC 变化 / 200，实时算           |
| 数据获取时间          | 表2              | max(end_time)                    |
| 电站基础信息          | 配置文件 YAML    | 静态读取                         |
| MPC 状态              | 健康检查器       | 后台实时维护                     |
| 目标 SOC / 需量       | 表4              | 最新 run 记录                    |
| 优化目标选择          | 配置文件 YAML    | 静态参数                         |
| 月度需量参考          | 表4              | reference_peak_kw                |
