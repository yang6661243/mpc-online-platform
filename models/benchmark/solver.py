"""
全信息离线最优调度基准求解器 (MILP).

在已知完整仿真周期内光伏/风电出力、负荷、分时电价、设备参数和约束边界
的前提下，求解理论最优储能充放电调度方案，作为 AI 策略的验收基准。

使用 Pyomo + HiGHS (appsi_highs) 求解混合整数线性规划。

参考: docs/superpowers/specs/2026-05-14-benchmark-solver-redesign.md
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Optional
import time

import pyomo.environ as pyo


# ═══════════════════════════════════════════════════════════════
# 1. 配置与结果数据结构
# ═══════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════
# 2. 主求解函数
# ═══════════════════════════════════════════════════════════════

def solve_benchmark(
    pv_kw: list[float],
    wind_kw: list[float] | None = None,
    load_kw: list[float] | None = None,
    buy_price: list[float] | None = None,
    sell_price: list[float] | None = None,
    config: BenchmarkConfig | None = None,
    grid_limit: list[float] | None = None,
    verbose: bool = False,
    debug_model_callback: Callable | None = None,
) -> BenchmarkResult:
    """
    求解全信息离线最优调度基准.

    Parameters
    ----------
    pv_kw : 光伏出力序列 (kW), 长度 T.
    wind_kw : 风电出力序列 (kW), 长度 T. 若 None 则全为零.
    load_kw : 负荷功率序列 (kW), 长度 T. 若 None 则全为零.
    buy_price : 购电价序列 (元/kWh), 长度 T. 若 None 则全为零.
    sell_price : 售电价序列 (元/kWh), 长度 T. 若 None 则全为零.
    config : 设备及约束配置. 若 None 则使用默认值.
    grid_limit : DR购电上限 (kW), 长度 T. 若 None 则不施加此约束.
    verbose : 是否打印求解日志.

    Returns
    -------
    BenchmarkResult
    """
    cfg = config or BenchmarkConfig()
    T = len(pv_kw)

    _wind       = wind_kw if wind_kw is not None else [0.0] * T
    _load       = load_kw if load_kw is not None else [0.0] * T
    _buy_price  = buy_price if buy_price is not None else [0.0] * T
    _sell_price = sell_price if sell_price is not None else [0.0] * T
    _grid_limit = grid_limit if grid_limit is not None else None
    _vpp_baseline = cfg.vpp_baseline_kw if cfg.vpp_baseline_kw else [0.0] * T
    _vpp_window = cfg.vpp_window_mask if cfg.vpp_window_mask else [False] * T
    _soc_min_profile = cfg.battery_soc_min_profile if cfg.battery_soc_min_profile else [cfg.battery_soc_min] * T

    assert len(_wind) == len(_load) == len(_buy_price) == len(_sell_price) == T, \
        "所有输入序列长度必须相等"
    assert len(_soc_min_profile) == T, "battery_soc_min_profile 长度必须等于 T"
    assert all(cfg.battery_soc_min <= soc_min <= cfg.battery_soc_max for soc_min in _soc_min_profile), \
        "battery_soc_min_profile 必须位于 battery_soc_min 和 battery_soc_max 之间"
    if _grid_limit is not None:
        assert len(_grid_limit) == T, "grid_limit 长度必须等于 T"
    if cfg.vpp_enabled:
        assert len(_vpp_baseline) == T, "vpp_baseline_kw 长度必须等于 T"
        assert len(_vpp_window) == T, "vpp_window_mask 长度必须等于 T"
    _reserve_enabled = (
        cfg.reserve_check_step is not None
        and 0 <= cfg.reserve_check_step < T
        and cfg.reserve_target_soc > cfg.battery_soc_min
        and cfg.reserve_slack_penalty > 0
    )
    if cfg.reserve_check_step is not None:
        assert 0 <= cfg.reserve_check_step < T, "reserve_check_step 必须位于优化窗口内"
    if cfg.reserve_target_soc > 0:
        assert cfg.battery_soc_min <= cfg.reserve_target_soc <= cfg.battery_soc_max, \
            "reserve_target_soc 必须位于 battery_soc_min 和 battery_soc_max 之间"

    dt = cfg.dt_hours
    D_days = T * dt / 24.0
    _vpp_indices = [t for t, eligible in enumerate(_vpp_window) if eligible] if cfg.vpp_enabled else []
    vpp_big_m = cfg.vpp_big_m_kw or (
        max(cfg.grid_import_max_kw, cfg.transformer_capacity_kw) + max(_vpp_baseline or [0.0])
    )
    # ═══════════════════════════════════════════════════════
    # Pyomo 模型
    # ═══════════════════════════════════════════════════════
    m = pyo.ConcreteModel()
    m.T = pyo.RangeSet(0, T - 1)
    m.T_vpp = pyo.Set(initialize=_vpp_indices, ordered=True)

    # §3 决策变量
    m.B_chg    = pyo.Var(m.T, within=pyo.NonNegativeReals)
    m.B_dis    = pyo.Var(m.T, within=pyo.NonNegativeReals)
    m.SOC      = pyo.Var(m.T, bounds=(cfg.battery_soc_min, cfg.battery_soc_max))
    m.Grid_in  = pyo.Var(m.T, within=pyo.NonNegativeReals)
    m.Grid_out = pyo.Var(m.T, within=pyo.NonNegativeReals)
    m.PV_curt  = pyo.Var(m.T, within=pyo.NonNegativeReals)
    m.Wind_curt = pyo.Var(m.T, within=pyo.NonNegativeReals)

    if cfg.use_milp:
        m.z = pyo.Var(m.T, within=pyo.Binary)

    if cfg.r_demand > 0:
        m.D_peak = pyo.Var(within=pyo.NonNegativeReals)
        if cfg.peak_so_far_kw > 0:
            m.D_increment = pyo.Var(within=pyo.NonNegativeReals)

    if cfg.vpp_enabled:
        m.VPP_resp = pyo.Var(m.T_vpp, within=pyo.NonNegativeReals)
        m.y_above = pyo.Var(m.T_vpp, within=pyo.Binary)

    if _reserve_enabled:
        m.Reserve_slack = pyo.Var(within=pyo.NonNegativeReals)

    _ramp_limit = cfg.battery_ramp_limit_kw if cfg.battery_ramp_limit_kw and cfg.battery_ramp_limit_kw > 0 else None
    _smooth_enabled = cfg.battery_smooth_penalty > 0
    _delta_enabled = _ramp_limit is not None or _smooth_enabled
    if _delta_enabled:
        m.B_delta_abs = pyo.Var(m.T, within=pyo.NonNegativeReals)
    if _ramp_limit is not None:
        m.B_ramp_slack = pyo.Var(m.T, within=pyo.NonNegativeReals)

    # ═══════════════════════════════════════════════════════
    # §4 等式约束
    # ═══════════════════════════════════════════════════════

    # E1 功率平衡
    @m.Constraint(m.T)
    def eq_power_balance(m, t):
        return (pv_kw[t] + _wind[t] + m.B_dis[t] + m.Grid_in[t] ==
                _load[t] + m.B_chg[t] + m.Grid_out[t] + m.PV_curt[t] + m.Wind_curt[t])

    # E2 SOC 递推
    @m.Constraint(m.T)
    def eq_soc_dynamics(m, t):
        if t == 0:
            soc_prev = cfg.battery_soc_init
        else:
            soc_prev = m.SOC[t - 1]
        delta = (cfg.battery_charge_eff * m.B_chg[t]
                 - m.B_dis[t] / cfg.battery_discharge_eff) * dt / cfg.battery_capacity_kwh
        return m.SOC[t] == soc_prev + delta

    # E3 SOC 终端归位 (可关闭, 用于MPC评估)
    if cfg.terminal_constraint:
        @m.Constraint()
        def eq_soc_terminal(m):
            return m.SOC[T - 1] == cfg.battery_soc_init

    # ═══════════════════════════════════════════════════════
    # §5 不等式约束
    # ═══════════════════════════════════════════════════════

    # I2 充电上限
    @m.Constraint(m.T)
    def ineq_chg_max(m, t):
        return m.B_chg[t] <= cfg.battery_charge_max_kw

    # I3 放电上限
    @m.Constraint(m.T)
    def ineq_dis_max(m, t):
        return m.B_dis[t] <= cfg.battery_discharge_max_kw

    # I4 充放互斥 (仅 MILP)
    if cfg.use_milp:
        M = cfg.battery_charge_max_kw + cfg.battery_discharge_max_kw
        @m.Constraint(m.T)
        def ineq_chg_mutex(m, t):
            return m.B_chg[t] <= M * m.z[t]

        @m.Constraint(m.T)
        def ineq_dis_mutex(m, t):
            return m.B_dis[t] <= M * (1 - m.z[t])

    # I5 购电上限
    @m.Constraint(m.T)
    def ineq_grid_in_max(m, t):
        return m.Grid_in[t] <= cfg.grid_import_max_kw

    # I6 售电上限
    @m.Constraint(m.T)
    def ineq_grid_out_max(m, t):
        return m.Grid_out[t] <= cfg.grid_export_max_kw

    # I7 变压器容量
    @m.Constraint(m.T)
    def ineq_transformer(m, t):
        return m.Grid_in[t] <= cfg.transformer_capacity_kw

    # I8 防逆流
    if cfg.anti_backflow:
        @m.Constraint(m.T)
        def ineq_backflow(m, t):
            return m.Grid_out[t] == 0

    # I8b 逐时 SOC 下限: MPC 可用它表达削峰备用 SOC 曲线
    @m.Constraint(m.T)
    def ineq_soc_min_profile(m, t):
        return m.SOC[t] >= _soc_min_profile[t]

    # I9 需量: D_peak = max(peak_so_far, max Grid_in[t])
    if cfg.r_demand > 0:
        @m.Constraint(m.T)
        def ineq_demand(m, t):
            return m.Grid_in[t] <= m.D_peak

        if cfg.peak_so_far_kw > 0:
            @m.Constraint()
            def ineq_peak_floor(m):
                return m.D_peak >= cfg.peak_so_far_kw

            @m.Constraint()
            def ineq_demand_increment(m):
                return m.D_increment >= m.D_peak - cfg.peak_so_far_kw

    # I9b 峰值软约束: effective_target = max(peak_so_far, optimal_peak) or target_peak
    _effective_target = cfg.target_peak_kw
    if cfg.optimal_peak_kw > 0 or cfg.peak_so_far_kw > 0:
        _effective_target = max(cfg.peak_so_far_kw, cfg.optimal_peak_kw)

    if _effective_target > 0:
        m.Peak_slack = pyo.Var(m.T, within=pyo.NonNegativeReals)

        @m.Constraint(m.T)
        def ineq_peak_soft(m, t):
            return m.Grid_in[t] <= _effective_target + m.Peak_slack[t]

    # I10 弃光
    @m.Constraint(m.T)
    def ineq_pv_curt(m, t):
        return m.PV_curt[t] <= pv_kw[t]

    # I11 弃风
    @m.Constraint(m.T)
    def ineq_wind_curt(m, t):
        return m.Wind_curt[t] <= _wind[t]

    # DR 约束 (GridLimit)
    if _grid_limit is not None:
        @m.Constraint(m.T)
        def ineq_dr_limit(m, t):
            return m.Grid_in[t] <= _grid_limit[t]

    # 软备电检查点: 允许不足, 但按缺少的备用电量(kWh)惩罚
    if _reserve_enabled:
        @m.Constraint()
        def ineq_reserve_checkpoint(m):
            return m.SOC[cfg.reserve_check_step] + m.Reserve_slack >= cfg.reserve_target_soc

    # VPP settlement: charging is disabled in declared windows; only above-baseline Grid_in is settled.
    if cfg.vpp_enabled:
        @m.Constraint(m.T_vpp)
        def eq_vpp_chg_idle(m, t):
            return m.B_chg[t] == 0

        @m.Constraint(m.T_vpp)
        def eq_vpp_settlement_power(m, t):
            return m.VPP_resp[t] <= (
                m.Grid_in[t] - _vpp_baseline[t] + vpp_big_m * (1 - m.y_above[t])
            )

        @m.Constraint(m.T_vpp)
        def ineq_vpp_resp_above_delta_lower(m, t):
            return m.VPP_resp[t] >= (
                m.Grid_in[t] - _vpp_baseline[t] - vpp_big_m * (1 - m.y_above[t])
            )

        @m.Constraint(m.T_vpp)
        def ineq_vpp_delta_upper(m, t):
            return m.Grid_in[t] - _vpp_baseline[t] <= vpp_big_m * m.y_above[t]

        @m.Constraint(m.T_vpp)
        def ineq_vpp_delta_lower(m, t):
            return m.Grid_in[t] - _vpp_baseline[t] >= -vpp_big_m * (1 - m.y_above[t])

        @m.Constraint(m.T_vpp)
        def ineq_vpp_resp_zero_when_below(m, t):
            return m.VPP_resp[t] <= vpp_big_m * m.y_above[t]

        if cfg.vpp_response_cap_kw is not None:
            @m.Constraint(m.T_vpp)
            def ineq_vpp_resp_cap(m, t):
                return m.VPP_resp[t] <= cfg.vpp_response_cap_kw

    # I12 电池净功率爬坡/平滑：P_bat = B_dis - B_chg, 正=放电
    if _delta_enabled:
        def _battery_net_power(model, t):
            return model.B_dis[t] - model.B_chg[t]

        def _battery_prev_power(model, t):
            if t == 0:
                return cfg.battery_initial_power_kw
            return _battery_net_power(model, t - 1)

        _delta_indices = [
            t for t in range(T)
            if t > 0 or cfg.battery_initial_power_kw is not None
        ]
        m.T_delta = pyo.Set(initialize=_delta_indices, ordered=True)

        @m.Constraint(m.T_delta)
        def ineq_battery_delta_abs_pos(m, t):
            return m.B_delta_abs[t] >= _battery_net_power(m, t) - _battery_prev_power(m, t)

        @m.Constraint(m.T_delta)
        def ineq_battery_delta_abs_neg(m, t):
            return m.B_delta_abs[t] >= -(_battery_net_power(m, t) - _battery_prev_power(m, t))

        if _ramp_limit is not None:
            @m.Constraint(m.T_delta)
            def ineq_battery_ramp_limit(m, t):
                return m.B_delta_abs[t] <= _ramp_limit + m.B_ramp_slack[t]

    # ═══════════════════════════════════════════════════════
    # §6 目标函数
    # ═══════════════════════════════════════════════════════
    purchase  = sum(_buy_price[t] * m.Grid_in[t] * dt for t in m.T)
    vpp_benefit = 0.0
    if cfg.vpp_enabled:
        vpp_benefit = sum(
            max(_buy_price[t] - cfg.vpp_charge_price, 0.0) * m.VPP_resp[t] * dt
            for t in m.T_vpp
        )
    revenue   = sum(_sell_price[t] * m.Grid_out[t] * dt for t in m.T)
    deg_cost  = sum(cfg.c_deg * (m.B_chg[t] + m.B_dis[t]) * dt for t in m.T)
    curt_cost = sum(cfg.c_pv * m.PV_curt[t] * dt + cfg.c_wind * m.Wind_curt[t] * dt
                    for t in m.T)

    J_parts = [purchase - revenue + deg_cost + curt_cost - vpp_benefit]

    if cfg.r_capacity > 0:
        capacity_fee = cfg.r_capacity * cfg.transformer_capacity_kw * D_days / 30.0
        J_parts.append(capacity_fee)

    if cfg.r_demand > 0:
        billing_days = cfg.optimization_billing_days or D_days
        if cfg.peak_so_far_kw > 0:
            demand_fee = cfg.r_demand * m.D_increment * billing_days / 30.0
        else:
            demand_fee = cfg.r_demand * m.D_peak * billing_days / 30.0
        J_parts.append(demand_fee)

    if _effective_target > 0 and cfg.peak_slack_penalty > 0:
        slack_penalty = cfg.peak_slack_penalty * sum(m.Peak_slack[t] * dt for t in m.T)
        J_parts.append(slack_penalty)

    if _reserve_enabled:
        reserve_penalty = cfg.reserve_slack_penalty * m.Reserve_slack * cfg.battery_capacity_kwh
        J_parts.append(reserve_penalty)

    if _smooth_enabled:
        smooth_penalty = cfg.battery_smooth_penalty * sum(m.B_delta_abs[t] for t in m.T_delta)
        J_parts.append(smooth_penalty)

    if _ramp_limit is not None and cfg.battery_ramp_slack_penalty > 0:
        ramp_slack_penalty = cfg.battery_ramp_slack_penalty * sum(m.B_ramp_slack[t] for t in m.T_delta)
        J_parts.append(ramp_slack_penalty)

    m.cost = pyo.Objective(expr=sum(J_parts), sense=pyo.minimize)
    if debug_model_callback is not None:
        debug_model_callback(m)

    # ═══════════════════════════════════════════════════════
    # 求解
    # ═══════════════════════════════════════════════════════
    t0 = time.time()
    solver = pyo.SolverFactory('appsi_highs')
    if not verbose:
        solver.options['log_to_console'] = False
    result = solver.solve(m)
    elapsed = time.time() - t0

    # ═══════════════════════════════════════════════════════
    # §9 提取结果
    # ═══════════════════════════════════════════════════════
    r = BenchmarkResult()
    r.solve_time_s = elapsed
    r.solver_status = str(result.solver.status)

    if result.solver.status not in (pyo.SolverStatus.ok, pyo.SolverStatus.warning):
        r.total_cost = float('inf')
        r.comprehensive_cost = float('inf')
        return r

    # 时序轨迹
    r.soc_trajectory = [float(pyo.value(m.SOC[t])) for t in range(T)]
    r.grid_import    = [max(0.0, float(pyo.value(m.Grid_in[t]))) for t in range(T)]
    r.grid_export    = [max(0.0, float(pyo.value(m.Grid_out[t]))) for t in range(T)]
    r.pv_curtail     = [max(0.0, float(pyo.value(m.PV_curt[t]))) for t in range(T)]
    r.wind_curtail   = [max(0.0, float(pyo.value(m.Wind_curt[t]))) for t in range(T)]

    b_chg_val = [max(0.0, float(pyo.value(m.B_chg[t]))) for t in range(T)]
    b_dis_val = [max(0.0, float(pyo.value(m.B_dis[t]))) for t in range(T)]
    r.battery_power = [b_dis_val[t] - b_chg_val[t] for t in range(T)]
    if cfg.vpp_enabled:
        vpp_values = {t: max(0.0, float(pyo.value(m.VPP_resp[t]))) for t in m.T_vpp}
        r.vpp_response_kw = [vpp_values.get(t, 0.0) for t in range(T)]
    else:
        r.vpp_response_kw = [0.0] * T

    # 成本明细
    r.purchase_cost    = sum(_buy_price[t] * r.grid_import[t] * dt for t in range(T))
    r.export_revenue   = sum(_sell_price[t] * r.grid_export[t] * dt for t in range(T))
    r.vpp_benefit      = sum(
        max(_buy_price[t] - cfg.vpp_charge_price, 0.0) * r.vpp_response_kw[t] * dt
        for t in range(T)
    )
    r.degradation_cost = sum(cfg.c_deg * (b_chg_val[t] + b_dis_val[t]) * dt for t in range(T))
    r.curtailment_cost = sum(cfg.c_pv * r.pv_curtail[t] * dt + cfg.c_wind * r.wind_curtail[t] * dt
                             for t in range(T))

    if cfg.r_capacity > 0:
        r.capacity_charge = cfg.r_capacity * cfg.transformer_capacity_kw * D_days / 30.0

    if cfg.r_demand > 0:
        r.peak_demand_kw = float(pyo.value(m.D_peak))
        billing_days = cfg.optimization_billing_days or D_days
        r.demand_charge_report = cfg.r_demand * r.peak_demand_kw * D_days / 30.0
        r.demand_charge_optimization = cfg.r_demand * r.peak_demand_kw * billing_days / 30.0
        r.demand_charge = r.demand_charge_report

    r.total_cost = r.purchase_cost - r.export_revenue - r.vpp_benefit + r.degradation_cost \
                   + r.curtailment_cost + r.demand_charge + r.capacity_charge

    # 固定成本 (不影响调度, 仅报告)
    r.pv_share_cost   = sum(cfg.c_pv * pv_kw[t] * dt for t in range(T))
    r.wind_share_cost = sum(cfg.c_wind * _wind[t] * dt for t in range(T))
    r.calendar_decay_cost = cfg.battery_capacity_kwh * cfg.battery_unit_cost \
                            * cfg.battery_annual_decay_rate * D_days / 365.0

    # 综合成本
    r.comprehensive_cost = r.total_cost + r.pv_share_cost + r.wind_share_cost \
                           + r.calendar_decay_cost

    r.objective_value = float(pyo.value(m.cost))

    # 新能源指标
    r.pv_total_kwh   = sum(pv_kw[t] * dt for t in range(T))
    r.wind_total_kwh = sum(_wind[t] * dt for t in range(T))
    total_gen = r.pv_total_kwh + r.wind_total_kwh
    r.total_curtailment_kwh = sum((r.pv_curtail[t] + r.wind_curtail[t]) * dt for t in range(T))
    r.total_export_kwh      = sum(r.grid_export[t] * dt for t in range(T))
    r.local_consumption_kwh = total_gen - r.total_curtailment_kwh - r.total_export_kwh
    r.local_consumption_rate = (r.local_consumption_kwh / total_gen) if total_gen > 0 else 0.0

    return r


# ═══════════════════════════════════════════════════════════════
# 3. 验收指标
# ═══════════════════════════════════════════════════════════════

def compute_achievement_rate(ai_cost: float, benchmark_cost: float) -> float:
    """
    成本优化达成率 = (离线最优综合成本 / AI策略综合成本) × 100%.

    Parameters
    ----------
    ai_cost : AI策略的综合成本 (元)
    benchmark_cost : 离线最优综合成本 comprehensive_cost (元)

    Returns
    -------
    float : 达成率 (%), 0~100. 若任一成本 ≤0 则返回 0.
    """
    if ai_cost <= 0 or benchmark_cost <= 0:
        return 0.0
    return (benchmark_cost / ai_cost) * 100.0


def compute_consumption_achievement_rate(
    ai_rate: float, benchmark_rate: float
) -> float:
    """
    新能源消纳达成率 = (AI策略消纳率 / 最优消纳率) × 100%.

    Parameters
    ----------
    ai_rate : AI策略新能源本地消纳率 (0~1)
    benchmark_rate : 离线最优新能源本地消纳率 (0~1)

    Returns
    -------
    float : 达成率 (%), 0~100. 若基准消纳率为 0 则返回 0.
    """
    if benchmark_rate <= 0 or ai_rate <= 0:
        return 0.0
    return (ai_rate / benchmark_rate) * 100.0
