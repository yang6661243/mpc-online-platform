"""
Intra-minute storage controller — decomposes 15‑minute MPC targets into
per‑minute (or finer) PCS power commands.

Architecture (adapted from fuzzy_pid/work/fuzzy_pid_gateway_sim.py):

    P_cmd = P_feedforward + Gateway_PID(band_error) + fade × SOC_PID(guide_error)

Hard constraints (enforced after the control law):
  ① Anti‑backflow:  P_grid ≥ 0          →  P_batt ≤ P_load − P_pv
  ② Peak limit:     P_grid ≤ P_target    →  P_batt ≥ P_load − P_pv − P_target
  ③ SOC bounds:     SOC ∈ [SOC_min, SOC_max]
  ④ Rated power:    |P_batt| ≤ P_rated
  ⑤ Slew rate:      |ΔP_batt| ≤ slew_limit × dt

SOC guidance trajectory:
  Within each 15‑minute window the SOC target moves linearly from the
  *actual* SOC at window start to the MPC‑planned target at window end.
  This avoids aggressive early追赶 and produces smooth power profiles.

Sign convention (matches microgrid/mpc.py):
  - P_battery > 0  →  discharging to AC bus
  - P_battery < 0  →  charging from AC bus
  - P_grid = P_load − P_pv − P_battery
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from scipy.interpolate import interp1d


# ═══════════════════════════════════════════════════════════════════════════
# 1. Configuration
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class ControllerConfig:
    """Tunable parameters for the intra‑minute fuzzy‑PID controller."""

    # ── Timing ──
    sample_seconds: float = 1.0        # gateway meter sampling period
    control_seconds: float = 5.0       # PCS command update period
    window_seconds: float = 900.0      # 15‑minute MPC window

    # ── Battery / PCS ──
    energy_capacity_kwh: float = 1000.0
    pcs_power_limit_kw: float = 375.0
    soc_min: float = 0.10
    soc_max: float = 0.90
    charge_eff: float = 0.95
    discharge_eff: float = 0.95

    # ── Gateway constraints ──
    target_peak_kw: float = 5000.0     # P_target: max allowed grid import
    anti_backflow: bool = True         # grid_export_max_kw = 0

    # ── PCS first‑order plant model ──
    response_seconds_0_to_100kw: float = 5.0

    # ── PID bases (gateway band tracking) ──
    deadband_kw: float = 0.5           # ignore gateway errors below this
    error_filter_alpha: float = 0.65   # low‑pass filter on gateway error
    base_kp: float = 0.85
    base_ki: float = 0.16
    base_kd: float = 0.04
    integral_limit_kw_s: float = 4500.0

    # ── SOC guidance (soft reference) ──
    soc_kp: float = 130.0              # kW per unit SOC error
    soc_fade_band_kw: float = 50.0     # transition band width near gateway boundaries
    soc_guide_enabled: bool = True     # enable linear SOC guidance trajectory

    # ── Slew rate ──
    command_slew_kw_per_s: float = 60.0

    # ── Gateway target step detection ──
    target_step_reset_kw: float = 30.0

    # ── Simulation duration ──
    duration_hours: float = 24.0


# ═══════════════════════════════════════════════════════════════════════════
# 2. Controller
# ═══════════════════════════════════════════════════════════════════════════


class FuzzyPIDIntraMinuteController:
    """Per‑minute PCS command controller that tracks a gateway‑power band
    while guiding SOC along a linear trajectory inside each 15‑minute window.

    The controller treats gateway‑power compliance as the **rigid** objective
    and SOC tracking as a **soft** objective that fades out near band edges.
    """

    def __init__(self, config: ControllerConfig):
        self.config = config
        self.integral_kw_s: float = 0.0
        self.previous_error_kw: float = 0.0
        self.previous_command_kw: float = 0.0
        self.filtered_error_kw: float = 0.0
        self.previous_gateway_target_kw: Optional[float] = None

        # SOC guidance trajectory state (per 15‑minute window)
        self._soc_window_start_actual: float = 0.5
        self._soc_window_end_target: float = 0.5
        self._window_start_time_s: float = 0.0

    # ── helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _clamp(value: float, lower: float, upper: float) -> float:
        return max(lower, min(upper, value))

    @staticmethod
    def _deadband(value: float, band: float) -> float:
        """Return 0.0 when |value| < band, else pass through."""
        return 0.0 if abs(value) < band else value

    # ── gateway band error ───────────────────────────────────────────────

    def _compute_gateway_band_error(
        self,
        P_grid_actual: float,
        P_target: float,
    ) -> Tuple[float, str]:
        """Compute gateway error relative to the safe band [0, P_target].

        Returns
        -------
        error_kw : float
            > 0  →  grid import is too high  →  need more DISCHARGE
            < 0  →  grid is exporting         →  need more CHARGE
            = 0  →  inside safe band
        severity : str
            "backflow" | "over_peak" | "safe"
        """
        cfg = self.config
        if P_grid_actual < -cfg.deadband_kw:
            # backflow — grid is exporting
            return P_grid_actual, "backflow"
        elif P_grid_actual > P_target + cfg.deadband_kw:
            # over peak — grid import exceeds target
            return P_grid_actual - P_target, "over_peak"
        else:
            return 0.0, "safe"

    # ── fuzzy gain scheduler ─────────────────────────────────────────────

    def _fuzzy_gain_scale(
        self,
        error_kw: float,
        error_rate_kw_s: float,
        severity: str,
    ) -> Tuple[float, float, float]:
        """Return (kp_scale, ki_scale, kd_scale) multipliers."""
        abs_error = abs(error_kw)
        abs_rate = abs(error_rate_kw_s)

        if severity == "backflow":
            # backflow must be corrected immediately — always aggressive
            kp_scale = 1.55
            ki_scale = 0.45
        elif abs_error >= 80.0:
            kp_scale = 1.35
            ki_scale = 0.60
        elif abs_error >= 20.0:
            kp_scale = 1.10
            ki_scale = 0.85
        else:
            kp_scale = 0.80
            ki_scale = 1.10

        if abs_rate >= 8.0:
            kd_scale = 0.65
        elif abs_rate >= 2.0:
            kd_scale = 0.85
        else:
            kd_scale = 1.00

        return kp_scale, ki_scale, kd_scale

    # ── SOC guidance trajectory ──────────────────────────────────────────

    def set_soc_window(
        self,
        soc_actual: float,
        soc_mpc_target: float,
        window_start_time_s: float,
    ) -> None:
        """Record the SOC guidance parameters for the upcoming 15‑minute window.

        Must be called at the start of every MPC window (every 15 minutes).
        The guide line runs from *actual* SOC (not MPC‑planned) to the
        MPC target, so adjacent windows always join seamlessly.
        """
        self._soc_window_start_actual = float(soc_actual)
        self._soc_window_end_target = float(soc_mpc_target)
        self._window_start_time_s = float(window_start_time_s)

    def compute_soc_guide(self, elapsed_window_s: float) -> float:
        """Return the SOC guidance value at *elapsed_window_s* into the
        current 15‑minute window (linear interpolation)."""
        if not self.config.soc_guide_enabled:
            return self._soc_window_end_target
        progress = self._clamp(elapsed_window_s / self.config.window_seconds, 0.0, 1.0)
        return (
            self._soc_window_start_actual
            + (self._soc_window_end_target - self._soc_window_start_actual) * progress
        )

    # ── main update ──────────────────────────────────────────────────────

    def update(
        self,
        *,
        gateway_power_target_kw: float,
        P_load_actual: float,
        P_pv_actual: float,
        P_battery_actual: float,
        soc_actual: float,
        P_target: Optional[float] = None,
        P_feedforward: float = 0.0,
        elapsed_window_s: float = 0.0,
    ) -> Dict[str, float]:
        """Compute the PCS command for this control step.

        Parameters
        ----------
        gateway_power_target_kw :
            MPC's planned grid import for this 15‑minute window (held constant).
            Used for gateway step‑detection and as the feedforward reference.
        P_load_actual, P_pv_actual :
            Real‑time measurements (or simulated values).
        P_battery_actual :
            Current actual PCS power (after first‑order plant lag).
        soc_actual :
            Current measured / simulated SOC (0–1).
        P_target :
            Upper bound of the grid‑import safe band.  Defaults to config.
        P_feedforward :
            MPC's planned battery power at this moment (spline‑interpolated).
        elapsed_window_s :
            Seconds elapsed since the start of the current 15‑minute window.

        Returns
        -------
        dict with keys:
            pcs_command_kw, gateway_error_kw, filtered_gateway_error_kw,
            kp, ki, kd, soc_reference_kw, feedforward_kw,
            severity, soc_guide, soc_guide_error
        """
        cfg = self.config
        dt = cfg.control_seconds
        P_target = P_target if P_target is not None else cfg.target_peak_kw

        # ── 1. Gateway power and band error ─────────────────────────────
        P_grid_actual = P_load_actual - P_pv_actual - P_battery_actual
        gateway_error_raw, severity = self._compute_gateway_band_error(
            P_grid_actual, P_target
        )

        # ── 2. Gateway target step detection ────────────────────────────
        target_step = (
            self.previous_gateway_target_kw is not None
            and abs(gateway_power_target_kw - self.previous_gateway_target_kw)
            >= cfg.target_step_reset_kw
        )
        if target_step:
            self.filtered_error_kw = 0.0
            self.previous_error_kw = 0.0
            self.integral_kw_s = 0.0

        # ── 3. Filtered error & rate ────────────────────────────────────
        if not target_step:
            self.filtered_error_kw = (
                cfg.error_filter_alpha * gateway_error_raw
                + (1.0 - cfg.error_filter_alpha) * self.filtered_error_kw
            )
        error_rate_kw_s = (
            0.0
            if target_step
            else (self.filtered_error_kw - self.previous_error_kw) / dt
        )

        # ── 4. Fuzzy gain scheduling ────────────────────────────────────
        kp_scale, ki_scale, kd_scale = self._fuzzy_gain_scale(
            self.filtered_error_kw, error_rate_kw_s, severity
        )
        kp = cfg.base_kp * kp_scale
        ki = cfg.base_ki * ki_scale
        kd = cfg.base_kd * kd_scale

        # ── 5. Integral term ────────────────────────────────────────────
        if not target_step:
            self.integral_kw_s = self._clamp(
                self.integral_kw_s + self.filtered_error_kw * dt,
                -cfg.integral_limit_kw_s,
                cfg.integral_limit_kw_s,
            )

        # ── 6. Gateway PID correction ───────────────────────────────────
        if target_step or severity == "safe":
            pid_power_kw = 0.0
        else:
            pid_power_kw = (
                kp * self.filtered_error_kw
                + ki * self.integral_kw_s
                + kd * error_rate_kw_s
            )

        # ── 7. SOC guidance (soft reference) ────────────────────────────
        soc_guide = self.compute_soc_guide(elapsed_window_s)
        soc_guide_error = soc_guide - soc_actual
        soc_reference_kw = cfg.soc_kp * soc_guide_error

        # fade SOC influence near gateway band edges
        if severity == "safe":
            distance_to_backflow = max(0.0, P_grid_actual)  # distance from 0
            distance_to_peak = max(0.0, P_target - P_grid_actual)  # distance from P_target
            distance_to_boundary = min(distance_to_backflow, distance_to_peak)
            fade = self._clamp(distance_to_boundary / cfg.soc_fade_band_kw, 0.0, 1.0)
        else:
            distance_outside = abs(gateway_error_raw)
            fade = max(0.0, 1.0 - distance_outside / cfg.soc_fade_band_kw)

        # ── 8. Assemble raw command ─────────────────────────────────────
        raw_command_kw = P_feedforward + pid_power_kw + fade * soc_reference_kw

        # ── 9. Hard constraint limits ───────────────────────────────────
        # ⑨a Anti‑backflow: P_batt ≤ P_load − P_pv  (so P_grid ≥ 0)
        if cfg.anti_backflow:
            upper_backflow = P_load_actual - P_pv_actual
        else:
            upper_backflow = cfg.pcs_power_limit_kw

        # ⑨b Peak limit: P_batt ≥ P_load − P_pv − P_target  (so P_grid ≤ P_target)
        lower_peak = P_load_actual - P_pv_actual - P_target

        # ⑨c SOC hard bounds
        if soc_actual >= cfg.soc_max:
            soc_lower = 0.0       # cannot charge any more
            soc_upper = cfg.pcs_power_limit_kw
        elif soc_actual <= cfg.soc_min:
            soc_lower = -cfg.pcs_power_limit_kw
            soc_upper = 0.0       # cannot discharge any more
        else:
            soc_lower = -cfg.pcs_power_limit_kw
            soc_upper = cfg.pcs_power_limit_kw

        # Combine
        lower = max(lower_peak, soc_lower, -cfg.pcs_power_limit_kw)
        upper = min(upper_backflow, soc_upper, cfg.pcs_power_limit_kw)
        limited_kw = self._clamp(raw_command_kw, lower, upper)

        # ── 10. Slew‑rate limit ─────────────────────────────────────────
        max_step = cfg.command_slew_kw_per_s * dt
        command_kw = self._clamp(
            limited_kw,
            self.previous_command_kw - max_step,
            self.previous_command_kw + max_step,
        )

        # ── 11. Anti‑windup on saturation ───────────────────────────────
        saturated_high = raw_command_kw > upper
        saturated_low = raw_command_kw < lower
        if (saturated_high and self.filtered_error_kw > 0) or (
            saturated_low and self.filtered_error_kw < 0
        ):
            self.integral_kw_s = self._clamp(
                self.integral_kw_s - self.filtered_error_kw * dt,
                -cfg.integral_limit_kw_s,
                cfg.integral_limit_kw_s,
            )
            # Re‑compute without integral windup for reporting
            pid_power_kw = (
                kp * self.filtered_error_kw
                + ki * self.integral_kw_s
                + kd * error_rate_kw_s
            )
            raw_command_kw = P_feedforward + pid_power_kw + fade * soc_reference_kw
            limited_kw = self._clamp(raw_command_kw, lower, upper)
            command_kw = self._clamp(
                limited_kw,
                self.previous_command_kw - max_step,
                self.previous_command_kw + max_step,
            )

        # ── 12. Update state ────────────────────────────────────────────
        self.previous_error_kw = self.filtered_error_kw
        self.previous_command_kw = command_kw
        self.previous_gateway_target_kw = gateway_power_target_kw

        return {
            "pcs_command_kw": command_kw,
            "gateway_error_kw": gateway_error_raw,
            "filtered_gateway_error_kw": self.filtered_error_kw,
            "kp": kp,
            "ki": ki,
            "kd": kd,
            "soc_reference_kw": soc_reference_kw,
            "feedforward_kw": P_feedforward,
            "severity": severity,
            "soc_guide": soc_guide,
            "soc_guide_error": soc_guide_error,
            "fade": fade,
            "pid_power_kw": pid_power_kw,
        }


# ═══════════════════════════════════════════════════════════════════════════
# 3. Schedule interpolation
# ═══════════════════════════════════════════════════════════════════════════

# Columns expected in the MPC output Excel / DataFrame.
MPC_SCHEDULE_COLUMNS = {
    "pv_kw": "光伏出力(kW)",
    "wind_kw": "风电出力(kW)",
    "load_kw": "负荷功率(kW)",
    "soc_target": "SOC",
    "battery_power_kw": "电池功率(kW)",
    "grid_power_kw": "电网功率(kW)",
    "buy_price": "购电价(元/kWh)",
    "sell_price": "售电价(元/kWh)",
}

# Keys whose values are held constant across a 15‑minute window.
HELD_KEYS = {"soc_target", "grid_power_kw"}

# Keys that are spline‑interpolated (quadratic) across 15‑minute points.
SPLINE_KEYS = {
    "pv_kw",
    "wind_kw",
    "load_kw",
    "battery_power_kw",
    "buy_price",
    "sell_price",
}


@dataclass
class MpcSchedule:
    """15‑minute MPC schedule interpolated to continuous time."""

    start: datetime
    end_seconds: float
    x_seconds: List[float]          # timestamp offsets for each 15‑min row
    held_values: Dict[str, List[float]]
    splines: Dict[str, interp1d]


def build_mpc_schedule(df: pd.DataFrame) -> MpcSchedule:
    """Build a continuous‑time schedule from MPC's 15‑minute output DataFrame.

    The DataFrame must contain a '时间' column and the columns listed in
    MPC_SCHEDULE_COLUMNS.
    """
    # Parse timestamps
    times = pd.to_datetime(df["时间"])
    start = times.iloc[0]
    x_seconds = [(t - start).total_seconds() for t in times]

    # Validate columns exist
    available = set(df.columns)
    for col_name in MPC_SCHEDULE_COLUMNS.values():
        if col_name not in available:
            raise ValueError(
                f"Required column '{col_name}' not found in MPC output. "
                f"Available: {sorted(available)}"
            )

    # Build splines for interpolated keys
    splines: Dict[str, interp1d] = {}
    for key, col in MPC_SCHEDULE_COLUMNS.items():
        if key not in SPLINE_KEYS:
            continue
        values = df[col].astype(float).to_numpy()
        splines[key] = interp1d(
            x_seconds,
            values,
            kind="quadratic",
            bounds_error=False,
            fill_value=(float(values[0]), float(values[-1])),
            assume_sorted=True,
        )

    # Build held‑constant arrays
    held_values: Dict[str, List[float]] = {}
    for key, col in MPC_SCHEDULE_COLUMNS.items():
        if key not in HELD_KEYS:
            continue
        held_values[key] = df[col].astype(float).tolist()

    return MpcSchedule(
        start=start,
        end_seconds=x_seconds[-1],
        x_seconds=x_seconds,
        held_values=held_values,
        splines=splines,
    )


def sample_mpc_schedule(
    schedule: MpcSchedule,
    current_time: datetime,
) -> Dict[str, float]:
    """Sample the MPC schedule at *current_time*.

    Returns a dict with keys matching MPC_SCHEDULE_COLUMNS plus:
      - window_index: which 15‑minute window (0‑based)
      - elapsed_window_s: seconds elapsed inside the current window
    """
    elapsed = (current_time - schedule.start).total_seconds()
    elapsed = max(0.0, min(schedule.end_seconds, elapsed))

    sampled: Dict[str, float] = {}
    for key, spline in schedule.splines.items():
        sampled[key] = float(spline(elapsed))

    window_idx = min(
        int(elapsed // 900.0),
        len(schedule.x_seconds) - 1,
    )
    for key, values in schedule.held_values.items():
        sampled[key] = float(values[window_idx])

    sampled["window_index"] = float(window_idx)
    sampled["elapsed_window_s"] = elapsed - window_idx * 900.0
    return sampled


# ═══════════════════════════════════════════════════════════════════════════
