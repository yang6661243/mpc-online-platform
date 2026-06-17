"""Benchmark solver configuration and result data structures."""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class BenchmarkConfig:
    """离线最优基准求解的设备参数与约束配置."""

    dt_hours: float = 0.25  # 时间步长 (小时), Δt=15min

    # ── 电池参数 ──
    battery_capacity_kwh: float = 200.0       # E_max
    battery_charge_max_kw: float = 100.0      # P_chg_max
    battery_discharge_max_kw: float = 100.0   # P_dis_max
    battery_soc_min: float = 0.10             # SOC_min
    battery_soc_max: float = 0.95             # SOC_max
    battery_soc_init: float = 0.50            # SOC_init
    battery_soc_min_profile: list[float] = field(default_factory=list)  # 逐时 SOC 下限, 为空则使用 SOC_min
    battery_charge_eff: float = 0.95          # η_chg
    battery_discharge_eff: float = 0.95       # η_dis
    battery_ramp_limit_kw: float | None = None  # 相邻步净电池功率最大变化, None/0=不限制
    battery_initial_power_kw: float | None = None  # 窗口前一时刻净功率, 用于约束第0步爬坡
    battery_smooth_penalty: float = 0.0       # 净功率变化惩罚 (元/kW), 0=不启用
    battery_ramp_slack_penalty: float = 100.0  # 爬坡超限惩罚 (元/kW), 软化爬坡避免不可行

    # ── 电网约束 ──
    grid_import_max_kw: float = 150.0          # P_import_max
    grid_export_max_kw: float = 150.0          # P_export_max
    transformer_capacity_kw: float = 200.0     # P_trans_max
    anti_backflow: bool = False                # 防逆流

    # ── 成本系数 ──
    c_deg: float = 0.05        # 储能循环衰减成本 (元/kWh吞吐)
    c_pv: float = 0.0          # 光伏分成单价 (元/kWh), 0=不计
    c_wind: float = 0.0        # 风电分成单价 (元/kWh), 0=不计
    battery_unit_cost: float = 0.0         # 电池单位造价 (元/kWh), 日历衰减用
    battery_annual_decay_rate: float = 0.0  # 电池年衰减率 (小数), 日历衰减用

    # ── 计费参数 ──
    r_demand: float = 0.0      # 需量费率 (元/kW/月), >0启用
    r_capacity: float = 0.0    # 容量费率 (元/kVA/月), >0启用

    # ── 求解选项 ──
    use_milp: bool = True      # True=MILP(充放互斥), False=LP
    terminal_constraint: bool = True  # True=SOC终点归位, False=无约束(MPC用)

    # ── 需量费增量计算 ──
    peak_so_far_kw: float = 0.0      # 本结算周期已发生的峰值 (kW), MPC跨窗口传递
    target_peak_kw: float = 0.0       # 预测不确定性护栏: 软约束上限 (kW)
    peak_slack_penalty: float = 0.0   # 超出 target_peak 的惩罚系数 (元/kWh)
    optimal_peak_kw: float = 0.0     # 月初步扫描的最优峰值, 0=未计算
    optimization_billing_days: float | None = None  # None=按窗口折算, 30=月度价值
    reserve_check_step: int | None = None       # 软备电检查点, None=不启用
    reserve_target_soc: float = 0.0             # 检查点目标 SOC
    reserve_slack_penalty: float = 0.0          # 缺少备用电量惩罚 (元/kWh)

    # ── VPP / 长三角互济结算 ──
    vpp_enabled: bool = False
    vpp_charge_price: float = 0.2
    vpp_window_mask: list[bool] = field(default_factory=list)
    vpp_response_cap_kw: float | None = None
    vpp_baseline_kw: list[float] = field(default_factory=list)
    vpp_big_m_kw: float | None = None


@dataclass
class BenchmarkResult:
    """离线最优调度求解结果."""

    # ── 时序轨迹 (长度 T) ──
    battery_power: list[float] = field(default_factory=list)   # B_dis - B_chg, 正=放电
    soc_trajectory: list[float] = field(default_factory=list)   # SOC(t)
    grid_import: list[float] = field(default_factory=list)     # Grid_in(t)
    grid_export: list[float] = field(default_factory=list)     # Grid_out(t)
    pv_curtail: list[float] = field(default_factory=list)      # PV_curt(t)
    wind_curtail: list[float] = field(default_factory=list)    # Wind_curt(t)

    # ── 成本明细 (目标函数六项) ──
    total_cost: float = 0.0              # 目标函数最小值 J
    comprehensive_cost: float = 0.0      # 综合成本 = J + 固定成本
    purchase_cost: float = 0.0           # 购电费
    export_revenue: float = 0.0          # 售电收益
    degradation_cost: float = 0.0        # 循环衰减成本
    curtailment_cost: float = 0.0        # 弃电惩罚成本
    demand_charge: float = 0.0           # 需量电费 (报告用，按仿真天数折算)
    demand_charge_optimization: float = 0.0  # 优化目标中的需量费
    demand_charge_report: float = 0.0        # 报告需量费 = rate × peak × simulated_days/30
    capacity_charge: float = 0.0         # 容量电费
    vpp_benefit: float = 0.0             # 长三角互济结算收益/抵扣
    vpp_response_kw: list[float] = field(default_factory=list)

    # ── 固定成本 (不影响调度, 仅报告输出) ──
    pv_share_cost: float = 0.0           # 光伏分成
    wind_share_cost: float = 0.0         # 风电分成
    calendar_decay_cost: float = 0.0     # 日历衰减

    # ── 新能源指标 ──
    pv_total_kwh: float = 0.0
    wind_total_kwh: float = 0.0
    total_curtailment_kwh: float = 0.0
    total_export_kwh: float = 0.0
    local_consumption_kwh: float = 0.0
    local_consumption_rate: float = 0.0

    # ── 需量 ──
    peak_demand_kw: float = 0.0          # D_peak

    # ── 求解信息 ──
    solve_time_s: float = 0.0
    solver_status: str = ""
    objective_value: float = 0.0
