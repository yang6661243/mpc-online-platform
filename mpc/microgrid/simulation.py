"""Intra-minute simulation: PV noise, load noise, one-day sim, summary, Excel output, CLI.

Run standalone:
    python -m mpc.microgrid.simulation --mpc-file outputs/mpc_result.xlsx
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

from mpc.microgrid.intra_minute_controller import (
    ControllerConfig,
    FuzzyPIDIntraMinuteController,
    MpcSchedule,
    build_mpc_schedule,
    sample_mpc_schedule,
)


# ═══════════════════════════════════════════════════════════════════════════
# 1. PV fluctuation model
# ═══════════════════════════════════════════════════════════════════════════


def generate_pv_fluctuation_kw(
    elapsed_seconds: float,
    base_pv_kw: float,
    cloud_amplitude_frac: float = 0.60,
    cloud_period_seconds: float = 180.0,
    fast_amplitude_frac: float = 0.08,
    fast_period_seconds: float = 30.0,
) -> float:
    """Generate PV fluctuation around a base value.

    Models two frequency components:
    - Slow: cloud passage (~3‑minute period, up to ±60% of base PV)
    - Fast: small irradiance flicker (~30‑s period, ±8%)

    Returns the *fluctuation* in kW (add to base PV to get actual).
    The fluctuation is clamped so actual PV never goes negative.
    """
    slow = (
        base_pv_kw
        * cloud_amplitude_frac
        * math.sin(2.0 * math.pi * elapsed_seconds / cloud_period_seconds)
    )
    fast = (
        base_pv_kw
        * fast_amplitude_frac
        * math.sin(2.0 * math.pi * elapsed_seconds / fast_period_seconds + 0.7)
    )
    fluctuation = slow + fast
    return max(-base_pv_kw, fluctuation)


def generate_load_noise_kw(
    elapsed_seconds: float,
    amplitude_kw: float = 8.0,
    slow_period_seconds: float = 73.0,
    fast_amplitude_kw: float = 2.0,
    fast_period_seconds: float = 17.0,
) -> float:
    """Generate small load noise around the 15‑minute forecast."""
    slow = amplitude_kw * math.sin(
        2.0 * math.pi * elapsed_seconds / slow_period_seconds
    )
    fast = fast_amplitude_kw * math.sin(
        2.0 * math.pi * elapsed_seconds / fast_period_seconds + 0.7
    )
    return slow + fast


# ═══════════════════════════════════════════════════════════════════════════
# 2. One‑day simulation
# ═══════════════════════════════════════════════════════════════════════════


def simulate_one_day(
    mpc_df: pd.DataFrame,
    config: ControllerConfig | None = None,
    *,
    pv_fluctuation_enabled: bool = True,
    load_noise_enabled: bool = True,
) -> pd.DataFrame:
    """Run a full‑day intra‑minute simulation driven by MPC's 15‑minute plan.

    Parameters
    ----------
    mpc_df : DataFrame
        MPC output with columns: 时间, SOC, 电池功率(kW), 电网功率(kW),
        负荷功率(kW), 光伏出力(kW), 风电出力(kW), 购电价(元/kWh), 售电价(元/kWh).
    config : ControllerConfig, optional
    pv_fluctuation_enabled : bool
        Whether to add cloud‑passage / flicker noise to PV.
    load_noise_enabled : bool
        Whether to add small random noise to load.

    Returns
    -------
    DataFrame with per‑sample rows.
    """
    cfg = config or ControllerConfig()
    controller = FuzzyPIDIntraMinuteController(cfg)
    schedule = build_mpc_schedule(mpc_df)

    rows: List[Dict] = []
    start = schedule.start
    total_steps = int(cfg.duration_hours * 3600 / cfg.sample_seconds)

    # Initial state from MPC
    soc = float(mpc_df["SOC"].iloc[0])
    actual_pcs_kw = float(mpc_df["电池功率(kW)"].iloc[0])
    pv_base_0 = float(mpc_df["光伏出力(kW)"].iloc[0])
    load_base_0 = float(mpc_df["负荷功率(kW)"].iloc[0])
    mpc_soc_target_0 = float(mpc_df["SOC"].iloc[0])

    for step in range(total_steps):
        elapsed = step * cfg.sample_seconds
        now = start + pd.Timedelta(seconds=elapsed)

        # ── MPC schedule lookup ──
        sched = sample_mpc_schedule(schedule, now)
        mpc_soc_target = sched["soc_target"]
        mpc_battery_kw = sched["battery_power_kw"]
        mpc_grid_kw = sched["grid_power_kw"]
        pv_base = sched["pv_kw"]
        load_base = sched["load_kw"]

        # ── PV fluctuation ──
        pv_fluctuation = 0.0
        if pv_fluctuation_enabled and pv_base > 0:
            pv_fluctuation = generate_pv_fluctuation_kw(elapsed, pv_base)
        actual_pv = pv_base + pv_fluctuation

        # ── Load noise ──
        load_noise = 0.0
        if load_noise_enabled:
            load_noise = generate_load_noise_kw(elapsed)
        actual_load = load_base + load_noise

        # ── Controller step ──
        update = controller.step(
            actual_load_kw=actual_load,
            actual_pv_kw=actual_pv,
            actual_wind_kw=sched["wind_kw"],
            current_soc=soc,
            mpc_soc_target=mpc_soc_target,
            mpc_battery_power_kw=mpc_battery_kw,
            buy_price=sched["buy_price"],
            sell_price=sched["sell_price"],
            previous_pcs_kw=actual_pcs_kw,
        )

        actual_pcs_kw = update["pcs_power_kw"]
        soc = update["new_soc"]

        # Only log at control update points
        control_update = step == 0 or (
            elapsed % cfg.control_seconds < cfg.sample_seconds / 2
        )

        rows.append(
            {
                "时间": now,
                "15分钟初始时间": sched["window_start"],
                "15分钟目标SOC": round(mpc_soc_target, 6),
                "实际SOC": round(soc, 6),
                "SOC引导误差": round(update["soc_guide_error"], 6),
                "MPC目标电池功率(kW)": round(mpc_battery_kw, 4),
                "MPC关口功率(kW)": round(mpc_grid_kw, 4),
                "光伏基础功率(kW)": round(pv_base, 4),
                "光伏波动(kW)": round(pv_fluctuation, 4),
                "实际光伏(kW)": round(actual_pv, 4),
                "负荷基础功率(kW)": round(load_base, 4),
                "负荷噪声(kW)": round(load_noise, 4),
                "实际负荷(kW)": round(actual_load, 4),
                "PCS指令功率(kW)": round(actual_pcs_kw, 4),
                "PCS实际功率(kW)": round(actual_pcs_kw, 4),
                "实际关口功率(kW)": round(update["gateway_power_kw"], 4),
                "关口误差(kW)": round(update["gateway_error_kw"], 4),
                "安全状态": update["severity"],
                "fade权重": round(update["fade"], 4),
                "控制更新": control_update,
                "Kp": round(update["kp"], 4),
                "Ki": round(update["ki"], 4),
                "Kd": round(update["kd"], 4),
                "PID修正功率(kW)": round(update["pid_power_kw"], 4),
                "SOC软参考功率(kW)": round(update["soc_reference_kw"], 4),
                "前馈功率(kW)": round(update["feedforward_kw"], 4),
                "购电价(元/kWh)": round(sched["buy_price"], 4),
                "售电价(元/kWh)": round(sched["sell_price"], 4),
            }
        )

    return pd.DataFrame(rows)


# ═══════════════════════════════════════════════════════════════════════════
# 3. Summary & Excel output
# ═══════════════════════════════════════════════════════════════════════════


def build_summary(result_df: pd.DataFrame, config: ControllerConfig) -> Dict[str, float]:
    """Compute key metrics from the simulation result."""
    dt_hours = config.sample_seconds / 3600.0
    pcs = result_df["PCS实际功率(kW)"]

    charge_kwh = float((-pcs[pcs < 0]).sum() * dt_hours)
    discharge_kwh = float((pcs[pcs > 0]).sum() * dt_hours)

    gateway_error = result_df["关口误差(kW)"]
    gateway_abs = gateway_error.abs()

    backflow_count = int((result_df["安全状态"] == "backflow").sum())
    over_peak_count = int((result_df["安全状态"] == "over_peak").sum())
    safe_count = int((result_df["安全状态"] == "safe").sum())

    backflow_max = float(
        result_df.loc[result_df["安全状态"] == "backflow", "关口误差(kW)"].min()
        if backflow_count > 0
        else 0.0
    )

    soc_final_target = float(result_df["15分钟目标SOC"].iloc[-1])
    soc_actual_final = float(result_df["实际SOC"].iloc[-1])

    return {
        "仿真步长(s)": config.sample_seconds,
        "控制周期(s)": config.control_seconds,
        "记录条数": len(result_df),
        "充电电量(kWh)": round(charge_kwh, 4),
        "放电电量(kWh)": round(discharge_kwh, 4),
        "关口误差MAE(kW)": round(float(gateway_abs.mean()), 4),
        "关口误差RMSE(kW)": round(float(np.sqrt((gateway_error**2).mean())), 4),
        "最大正向关口误差(kW)": round(float(gateway_error.max()), 4),
        "最大倒送功率(kW)": round(abs(backflow_max), 4),
        "倒送步数": backflow_count,
        "越限步数": over_peak_count,
        "安全步数": safe_count,
        "安全步数占比": round(safe_count / len(result_df), 4),
        "SOC最小值": round(float(result_df["实际SOC"].min()), 6),
        "SOC最大值": round(float(result_df["实际SOC"].max()), 6),
        "SOC结束值": round(soc_actual_final, 6),
        "MPC目标SOC结束值": round(soc_final_target, 6),
        "SOC终点偏差": round(soc_actual_final - soc_final_target, 6),
        "SOC引导MAE": round(float(result_df["SOC引导误差"].abs().mean()), 6),
    }


def write_excel(
    result_df: pd.DataFrame,
    output_path: Path,
    summary: Dict[str, float],
    sample_seconds: float,
) -> None:
    """Write simulation results to an Excel workbook."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    sheet_name = (
        f"{int(sample_seconds)}s_simulation"
        if float(sample_seconds).is_integer()
        else f"{str(sample_seconds).replace('.', '_')}s_simulation"
    )

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        result_df.to_excel(writer, sheet_name=sheet_name, index=False)
        pd.DataFrame([summary]).to_excel(writer, sheet_name="summary", index=False)


# ═══════════════════════════════════════════════════════════════════════════
# 4. CLI entry point
# ═══════════════════════════════════════════════════════════════════════════


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run intra‑minute fuzzy‑PID storage controller simulation."
    )
    parser.add_argument(
        "--mpc-file",
        required=True,
        type=Path,
        help="Path to MPC 15min_trajectory output (.xlsx)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/intra_minute_simulation.xlsx"),
        help="Output Excel path (default: outputs/intra_minute_simulation.xlsx)",
    )
    parser.add_argument(
        "--sample-seconds",
        type=float,
        default=1.0,
        help="Sampling period in seconds (default: 1.0)",
    )
    parser.add_argument(
        "--no-fluctuation",
        action="store_true",
        help="Disable PV cloud‑passage fluctuation",
    )
    parser.add_argument(
        "--no-load-noise",
        action="store_true",
        help="Disable load measurement noise",
    )
    args = parser.parse_args()

    mpc_df = pd.read_excel(args.mpc_file, sheet_name="15min_trajectory")
    config = ControllerConfig(sample_seconds=args.sample_seconds)

    result = simulate_one_day(
        mpc_df,
        config,
        pv_fluctuation_enabled=not args.no_fluctuation,
        load_noise_enabled=not args.no_load_noise,
    )

    summary = build_summary(result, config)
    write_excel(result, args.output, summary, args.sample_seconds)

    print(f"Simulation complete. {len(result)} rows → {args.output}")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
