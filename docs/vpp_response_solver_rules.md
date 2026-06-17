# 长三角互济/虚拟电厂结算口径

本文档说明当前项目中长三角互济/虚拟电厂的简化建模方式，以及它在求解器和 MPC 中的代码落点。

## 1. 建模口径

系统在每天 `11:00-13:00` 参与互济。当前简化为每天都中标，不单独建模竞标概率、出清失败或分配容量。

当前采用“前 5 天同点基线 + 申报窗口禁止储能充电 + 超基线部分低价结算”的口径：

```text
P_vpp[t] = max(Grid_in[t] - P_base[t], 0),  t 属于 11:00-13:00
P_vpp[t] = 0,                               t 不属于 11:00-13:00
```

其中：

```text
Grid_in[t]  第 t 个时段关口购电功率
P_base[t]   第 t 个时段互济基线功率
P_vpp[t]    第 t 个时段互济结算功率
dt          时段长度，当前为 0.25 h
```

申报窗口内禁止储能充电，但允许储能放电：

```text
B_chg[t] = 0
B_dis[t] >= 0，仍受储能功率、SOC 等原有约束限制
```

这样做的含义是：窗口内不允许储能通过充电人为抬高关口购电功率；若系统因需量控制或经济性选择放电，放电会降低 `Grid_in[t]`，并自然降低互济结算功率。

## 2. 基线计算

互济基线按前 `baseline_days` 天同一 15 分钟点的电网功率均值计算，默认 `baseline_days = 5`，`steps_per_day = 96`。

```text
P_base[d,k] =
average(Grid_ref[d-1,k], Grid_ref[d-2,k], ..., Grid_ref[d-5,k])
```

其中：

```text
d        日期编号
k        一天内第 k 个 15 分钟点
Grid_ref 当前项目中使用未控关口购电功率估计
```

当前项目没有独立历史关口表输入，因此先用未控净购电功率作为基线参考序列：

```text
Grid_ref[t] = max(load[t] - pv[t] - wind[t], 0)
```

历史不足 5 天时，代码使用已有历史同点平均；若完全没有历史，则使用当前未控净购电功率作为兜底。后续如果有真实历史关口功率数据，应优先用真实关口表计算 `P_base[t]`。

## 3. 成本与收益

常规购电成本仍按原分时电价计算：

```text
purchase = sum(buy_price[t] * Grid_in[t] * dt)
```

互济窗口内的超基线结算电量按中标等效电价 `c_vpp = 0.2 元/kWh` 处理，因此收益/抵扣为：

```text
vpp_benefit =
sum(max(buy_price[t] - c_vpp, 0) * P_vpp[t] * dt)
```

目标函数中扣减该收益：

```text
min J =
  purchase
- export_revenue
+ degradation_cost
+ demand_charge
- vpp_benefit
```

使用 `max(buy_price[t] - c_vpp, 0)` 是为了避免当原电价低于互济等效价时，互济项反向增加成本。

## 4. 可选上限

如果配置了 `response_cap_kw`，则申报窗口内结算功率还受上限限制：

```text
P_vpp[t] <= response_cap_kw
```

如果 `response_cap_kw: null`，则不额外设置互济结算上限。此时结算功率主要由超基线功率、电网购电上限、变压器容量等已有约束决定。

## 5. 配置示例

```yaml
vpp:
  enabled: true
  charge_price: 0.2
  start_hour: 11
  end_hour: 13
  baseline_days: 5
  steps_per_day: 96
  response_cap_kw: null
```

字段说明：

```text
enabled          是否启用互济结算
charge_price     互济中标等效电价，默认 0.2 元/kWh
start_hour        申报窗口开始小时，默认 11
end_hour          申报窗口结束小时，默认 13
baseline_days     基线使用前几天同点平均，默认 5
steps_per_day     每日点数，15 分钟粒度为 96
response_cap_kw   可选结算功率上限；null 表示不额外设置上限
```

## 6. 输出指标

离线求解器 Excel 输出保留核心指标：

`15min_trajectory`：

```text
互济窗口
互济基线(kW)
互济结算功率(kW)
互济结算电量(kWh)
互济收益(元)
```

`daily_summary`：

```text
互济结算电量(kWh)
互济收益(元)
```

`cost_summary`：

```text
互济总收益(元)
互济总结算电量(kWh)
等效互济均价(元/kWh)
```

## 7. 代码位置

核心求解器位于 `ml_core/benchmark/solver.py`：

```python
m.T_vpp = pyo.Set(initialize=_vpp_indices, ordered=True)
m.VPP_resp = pyo.Var(m.T_vpp, within=pyo.NonNegativeReals)
m.y_above = pyo.Var(m.T_vpp, within=pyo.Binary)

@m.Constraint(m.T_vpp)
def eq_vpp_chg_idle(m, t):
    return m.B_chg[t] == 0

# VPP_resp[t] = max(Grid_in[t] - baseline[t], 0)
```

窗口生成、基线计算和 MPC 单步收益辅助函数位于 `microgrid/vpp.py`。离线求解器入口位于 `microgrid/solver.py`，MPC 主流程位于 `microgrid/mpc.py`。
