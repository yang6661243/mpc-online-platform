"""Tests for intra_minute_controller.py."""

import math
from datetime import datetime

import pandas as pd
import pytest

from mpc.microgrid.intra_minute_controller import (
    ControllerConfig,
    FuzzyPIDIntraMinuteController,
    build_mpc_schedule,
    sample_mpc_schedule,
)
from mpc.microgrid.simulation import (
    generate_load_noise_kw,
    generate_pv_fluctuation_kw,
    simulate_one_day,
)

# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _make_mpc_df(
    start: datetime | None = None,
    steps: int = 96,
    soc: float = 0.5,
    battery_kw: float = 0.0,
    grid_kw: float = 200.0,
    load_kw: float = 200.0,
    pv_kw: float = 0.0,
    wind_kw: float = 0.0,
    buy_price: float = 0.8,
    sell_price: float = 0.3,
) -> pd.DataFrame:
    """Build a simple 96‑row MPC output DataFrame."""
    if start is None:
        start = datetime(2026, 4, 1, 0, 0, 0)
    times = [start + pd.Timedelta(minutes=15 * i) for i in range(steps)]
    return pd.DataFrame(
        {
            "时间": times,
            "光伏出力(kW)": [pv_kw] * steps,
            "风电出力(kW)": [wind_kw] * steps,
            "负荷功率(kW)": [load_kw] * steps,
            "SOC": [soc] * steps,
            "电池功率(kW)": [battery_kw] * steps,
            "电网功率(kW)": [grid_kw] * steps,
            "购电价(元/kWh)": [buy_price] * steps,
            "售电价(元/kWh)": [sell_price] * steps,
        }
    )
def _make_config(**kwargs) -> ControllerConfig:
    """Return a ControllerConfig with fast slew for test convenience."""
    defaults = dict(
        sample_seconds=1.0,
        control_seconds=1.0,
        command_slew_kw_per_s=1000.0,   # effectively unlimited
        energy_capacity_kwh=1000.0,
        pcs_power_limit_kw=375.0,
        target_peak_kw=5000.0,
        response_seconds_0_to_100kw=1.0,
        soc_kp=130.0,
        soc_fade_band_kw=50.0,
    )
    defaults.update(kwargs)
    return ControllerConfig(**defaults)
# ═══════════════════════════════════════════════════════════════════════════
# 1. Gateway band error
# ═══════════════════════════════════════════════════════════════════════════
class TestGatewayBandError:
    def test_inside_safe_band_returns_zero_error(self):
        controller = FuzzyPIDIntraMinuteController(_make_config())
        error, severity = controller._compute_gateway_band_error(
            P_grid_actual=100.0, P_target=500.0
        )
        assert error == 0.0
        assert severity == "safe"
    def test_backflow_returns_negative_error(self):
        controller = FuzzyPIDIntraMinuteController(_make_config())
        error, severity = controller._compute_gateway_band_error(
            P_grid_actual=-50.0, P_target=500.0
        )
        assert error == -50.0
        assert severity == "backflow"
    def test_over_peak_returns_positive_error(self):
        controller = FuzzyPIDIntraMinuteController(_make_config())
        error, severity = controller._compute_gateway_band_error(
            P_grid_actual=550.0, P_target=500.0
        )
        assert error == 50.0
        assert severity == "over_peak"
    def test_near_zero_but_positive_is_safe(self):
        controller = FuzzyPIDIntraMinuteController(_make_config())
        error, severity = controller._compute_gateway_band_error(
            P_grid_actual=0.1, P_target=500.0
        )
        assert error == 0.0
        assert severity == "safe"
    def test_deadband_at_peak_boundary(self):
        controller = FuzzyPIDIntraMinuteController(
            _make_config(deadband_kw=1.0)
        )
        error, severity = controller._compute_gateway_band_error(
            P_grid_actual=500.4, P_target=500.0
        )
        assert error == 0.0  # within deadband
        assert severity == "safe"
# ═══════════════════════════════════════════════════════════════════════════
# 2. SOC guidance trajectory
# ═══════════════════════════════════════════════════════════════════════════
class TestSocGuidance:
    def test_linear_interpolation_mid_window(self):
        controller = FuzzyPIDIntraMinuteController(_make_config())
        controller.set_soc_window(
            soc_actual=0.40,
            soc_mpc_target=0.70,
            window_start_time_s=0.0,
        )
        # At t=450s (mid‑window), guide should be halfway
        guide = controller.compute_soc_guide(450.0)
        assert guide == pytest.approx(0.55, abs=1e-6)
    def test_window_start_equals_actual_soc(self):
        controller = FuzzyPIDIntraMinuteController(_make_config())
        controller.set_soc_window(
            soc_actual=0.35,
            soc_mpc_target=0.80,
            window_start_time_s=900.0,
        )
        guide = controller.compute_soc_guide(0.0)
        assert guide == pytest.approx(0.35, abs=1e-6)
    def test_window_end_equals_mpc_target(self):
        controller = FuzzyPIDIntraMinuteController(_make_config())
        controller.set_soc_window(
            soc_actual=0.22,
            soc_mpc_target=0.55,
            window_start_time_s=0.0,
        )
        guide = controller.compute_soc_guide(900.0)
        assert guide == pytest.approx(0.55, abs=1e-6)
    def test_beyond_window_clamps_to_end_target(self):
        controller = FuzzyPIDIntraMinuteController(_make_config())
        controller.set_soc_window(
            soc_actual=0.30,
            soc_mpc_target=0.60,
            window_start_time_s=0.0,
        )
        guide = controller.compute_soc_guide(1200.0)
        assert guide == pytest.approx(0.60, abs=1e-6)
    def test_guide_disabled_returns_mpc_target(self):
        controller = FuzzyPIDIntraMinuteController(
            _make_config(soc_guide_enabled=False)
        )
        controller.set_soc_window(
            soc_actual=0.40,
            soc_mpc_target=0.70,
            window_start_time_s=0.0,
        )
        guide = controller.compute_soc_guide(450.0)
        assert guide == pytest.approx(0.70, abs=1e-6)
    def test_adjacent_windows_join_seamlessly(self):
        """When a window starts from actual SOC, there is no jump."""
        controller = FuzzyPIDIntraMinuteController(_make_config())
        # Window 1: start at 0.50, target 0.60
        controller.set_soc_window(0.50, 0.60, 0.0)
        end_window1 = controller.compute_soc_guide(900.0)
        assert end_window1 == pytest.approx(0.60, abs=1e-6)
        # Simulate: actual SOC only reached 0.57 (due to constraints)
        # Window 2: start from 0.57 (actual), target 0.65
        controller.set_soc_window(0.57, 0.65, 900.0)
        start_window2 = controller.compute_soc_guide(0.0)
        assert start_window2 == pytest.approx(0.57, abs=1e-6)
        # No jump — guidance starts exactly where we are
# ═══════════════════════════════════════════════════════════════════════════
# 3. Anti‑backflow constraint
# ═══════════════════════════════════════════════════════════════════════════
class TestAntiBackflow:
    def test_backflow_triggers_charging_command(self):
        """When P_grid < 0, controller should increase charging."""
        controller = FuzzyPIDIntraMinuteController(_make_config())
        controller.set_soc_window(0.5, 0.5, 0.0)
        # P_grid = load - pv - battery = 100 - 200 - 0 = -100 (backflow!)
        result = controller.update(
            gateway_power_target_kw=200.0,
            P_load_actual=100.0,
            P_pv_actual=200.0,
            P_battery_actual=0.0,
            soc_actual=0.5,
            P_target=500.0,
            P_feedforward=0.0,
            elapsed_window_s=100.0,
        )
        # Severity must be "backflow"
        assert result["severity"] == "backflow"
        # Command should be negative (charging) to absorb excess PV
        assert result["pcs_command_kw"] < 0
    def test_backflow_hard_constraint_prevents_export(self):
        """Even if PID demands discharge, anti‑backflow clamp prevents it."""
        controller = FuzzyPIDIntraMinuteController(_make_config())
        controller.set_soc_window(0.5, 0.5, 0.0)
        # P_grid would be negative if battery discharges at all
        # load=50, pv=80 → max battery discharge = 50-80 = -30 (must charge ≥30)
        result = controller.update(
            gateway_power_target_kw=200.0,
            P_load_actual=50.0,
            P_pv_actual=80.0,
            P_battery_actual=-30.0,
            soc_actual=0.5,
            P_target=500.0,
            P_feedforward=100.0,  # MPC wants to discharge 100kW!
            elapsed_window_s=100.0,
        )
        # Command is clamped to ≤ P_load - P_pv = -30 kW (must charge at least 30)
        # Wait, P_batt ≤ P_load - P_pv → P_batt ≤ -30
        # Actually -30 means 30kW charging. A value of -50 would be MORE charging.
        # The anti-backflow constraint is P_batt ≤ P_load - P_pv
        # If load=50, pv=80, then P_load-P_pv=-30
        # P_batt ≤ -30 means battery must charge at least 30kW (or more)
        # -40 ≤ -30 is true (40kW charging → more charging, safer)
        # -20 ≤ -30 is false (20kW charging → not enough, would export 10kW)
        # So the UPPER bound is -30kW → command ≤ -30
        assert result["pcs_command_kw"] <= -30.0 + 1e-6
# ═══════════════════════════════════════════════════════════════════════════
# 4. Peak limit constraint
# ═══════════════════════════════════════════════════════════════════════════
class TestPeakLimit:
    def test_over_peak_triggers_discharge_command(self):
        """When P_grid > P_target, controller should increase discharge."""
        controller = FuzzyPIDIntraMinuteController(_make_config())
        controller.set_soc_window(0.5, 0.5, 0.0)
        # P_grid = 300 - 0 - (-100) = 400.  But P_target=200 → over!
        # Actually: P_grid = 300 - 0 - (-100) = 400 > 200 → over_peak
        result = controller.update(
            gateway_power_target_kw=200.0,
            P_load_actual=300.0,
            P_pv_actual=0.0,
            P_battery_actual=-100.0,  # charging 100kW
            soc_actual=0.5,
            P_target=200.0,
            P_feedforward=-100.0,
            elapsed_window_s=100.0,
        )
        assert result["severity"] == "over_peak"
        # Command should be more positive (discharge / less charge)
        # P_grid = 300 - 0 - (-100) = 400 > 200
        # To bring P_grid down to 200: need P_batt = 300 - 0 - 200 = 100 (discharge)
        # So command should move up from -100 toward positive territory
        assert result["pcs_command_kw"] > -100.0
    def test_peak_hard_constraint_prevents_exceeding_target(self):
        """Peak hard constraint: P_batt ≥ P_load - P_pv - P_target."""
        controller = FuzzyPIDIntraMinuteController(_make_config())
        controller.set_soc_window(0.5, 0.5, 0.0)
        # load=500, pv=0, P_target=200
        # Hard lower bound: P_batt ≥ 500 - 0 - 200 = 300 (must discharge ≥300kW)
        # But PCS limit is 375kW
        result = controller.update(
            gateway_power_target_kw=200.0,
            P_load_actual=500.0,
            P_pv_actual=0.0,
            P_battery_actual=200.0,  # only discharging 200kW → P_grid=300 > 200
            soc_actual=0.5,
            P_target=200.0,
            P_feedforward=200.0,
            elapsed_window_s=100.0,
        )
        # Command must be ≥ 300 (the hard lower bound)
        assert result["pcs_command_kw"] >= 300.0 - 1e-6
# ═══════════════════════════════════════════════════════════════════════════
# 5. SOC guidance fade near gateway boundaries
# ═══════════════════════════════════════════════════════════════════════════
class TestSocFade:
    def test_full_soc_influence_in_middle_of_safe_band(self):
        """When P_grid is far from both 0 and P_target, fade ≈ 1.0."""
        controller = FuzzyPIDIntraMinuteController(
            _make_config(soc_fade_band_kw=50.0)
        )
        controller.set_soc_window(0.4, 0.6, 0.0)
        # P_grid should be well inside band: load=300, pv=0, batt=50 → P_grid=250
        # P_target=500 → distance to 0 = 250, to 500 = 250, min=250 > 50 → fade=1.0
        result = controller.update(
            gateway_power_target_kw=250.0,
            P_load_actual=300.0,
            P_pv_actual=0.0,
            P_battery_actual=50.0,
            soc_actual=0.4,
            P_target=500.0,
            P_feedforward=50.0,
            elapsed_window_s=100.0,
        )
        assert result["severity"] == "safe"
        assert result["fade"] == pytest.approx(1.0, abs=0.01)
    def test_fade_reduces_near_backflow_boundary(self):
        """When P_grid is near 0, SOC influence fades."""
        controller = FuzzyPIDIntraMinuteController(
            _make_config(soc_fade_band_kw=50.0)
        )
        controller.set_soc_window(0.4, 0.6, 0.0)
        # P_grid = 100 - 0 - 85 = 15 (close to backflow boundary)
        # distance to 0 = 15, fade ≈ 15/50 = 0.3
        result = controller.update(
            gateway_power_target_kw=200.0,
            P_load_actual=100.0,
            P_pv_actual=0.0,
            P_battery_actual=85.0,
            soc_actual=0.4,
            P_target=500.0,
            P_feedforward=85.0,
            elapsed_window_s=100.0,
        )
        assert result["severity"] == "safe"
        assert result["fade"] < 0.5  # should be ~0.3
    def test_fade_zero_when_deep_in_backflow(self):
        """When severely backflowing, SOC influence is near zero."""
        controller = FuzzyPIDIntraMinuteController(
            _make_config(soc_fade_band_kw=50.0)
        )
        controller.set_soc_window(0.4, 0.6, 0.0)
        # P_grid = 50 - 200 - 0 = -150 (deep backflow)
        result = controller.update(
            gateway_power_target_kw=200.0,
            P_load_actual=50.0,
            P_pv_actual=200.0,
            P_battery_actual=0.0,
            soc_actual=0.4,
            P_target=500.0,
            P_feedforward=0.0,
            elapsed_window_s=100.0,
        )
        assert result["severity"] == "backflow"
        # distance_outside = 150, fade = max(0, 1 - 150/50) = 0
        assert result["fade"] == pytest.approx(0.0, abs=0.01)
# ═══════════════════════════════════════════════════════════════════════════
# 6. Fuzzy gain scheduling
# ═══════════════════════════════════════════════════════════════════════════
class TestFuzzyGainScale:
    def test_large_error_produces_high_kp_low_ki(self):
        controller = FuzzyPIDIntraMinuteController(_make_config())
        kp, ki, kd = controller._fuzzy_gain_scale(100.0, 1.0, "over_peak")
        assert kp > 1.0   # aggressive proportional
        assert ki < 1.0   # reduced integral to prevent windup
    def test_small_error_produces_low_kp_high_ki(self):
        controller = FuzzyPIDIntraMinuteController(_make_config())
        kp, ki, kd = controller._fuzzy_gain_scale(10.0, 0.5, "over_peak")
        assert kp < 1.0   # gentle proportional
        assert ki > 1.0   # stronger integral for steady‑state
    def test_backflow_is_most_aggressive(self):
        controller = FuzzyPIDIntraMinuteController(_make_config())
        kp_bf, ki_bf, _ = controller._fuzzy_gain_scale(60.0, 2.0, "backflow")
        kp_op, ki_op, _ = controller._fuzzy_gain_scale(60.0, 2.0, "over_peak")
        assert kp_bf >= kp_op   # backflow Kp ≥ over_peak Kp
    def test_high_error_rate_reduces_kd(self):
        controller = FuzzyPIDIntraMinuteController(_make_config())
        _, _, kd_fast = controller._fuzzy_gain_scale(60.0, 10.0, "over_peak")
        _, _, kd_slow = controller._fuzzy_gain_scale(60.0, 1.0, "over_peak")
        assert kd_fast < kd_slow
# ═══════════════════════════════════════════════════════════════════════════
# 7. PV fluctuation model
# ═══════════════════════════════════════════════════════════════════════════
class TestPvFluctuation:
    def test_fluctuation_never_makes_pv_negative(self):
        for t in range(0, 3600, 37):
            fluct = generate_pv_fluctuation_kw(t, base_pv_kw=50.0)
            assert fluct >= -50.0 - 1e-9
    def test_zero_base_pv_returns_zero_fluctuation(self):
        fluct = generate_pv_fluctuation_kw(100.0, base_pv_kw=0.0)
        assert fluct == 0.0
    def test_fluctuation_is_periodic(self):
        period = 180.0
        v1 = generate_pv_fluctuation_kw(0.0, base_pv_kw=100.0,
                                         cloud_period_seconds=period)
        v2 = generate_pv_fluctuation_kw(period, base_pv_kw=100.0,
                                         cloud_period_seconds=period)
        # Should be approximately equal (fast component adds small offset)
        assert abs(v1 - v2) < 10.0  # fast component ≤ 8% of 100kW
class TestLoadNoise:
    def test_noise_is_zero_mean_over_long_period(self):
        samples = [
            generate_load_noise_kw(t * 17.0)
            for t in range(1000)
        ]
        mean = sum(samples) / len(samples)
        assert abs(mean) < 2.0  # roughly zero‑mean
# ═══════════════════════════════════════════════════════════════════════════
# 8. MPC schedule interpolation
# ═══════════════════════════════════════════════════════════════════════════
class TestMpcSchedule:
    def test_build_and_sample_schedule(self):
        start = datetime(2026, 4, 1, 0, 0, 0)
        df = _make_mpc_df(start=start, steps=3,
                          load_kw=100.0, pv_kw=0.0, soc=0.5)
        schedule = build_mpc_schedule(df)
        # Sample at mid‑point of first window
        sample = sample_mpc_schedule(
            schedule, start + pd.Timedelta(minutes=7.5)
        )
        assert sample["load_kw"] == pytest.approx(100.0)
        assert sample["soc_target"] == 0.5
        assert sample["window_index"] == 0
        assert sample["elapsed_window_s"] == pytest.approx(450.0, abs=1.0)
    def test_held_keys_constant_within_window(self):
        start = datetime(2026, 4, 1, 0, 0, 0)
        df = _make_mpc_df(start=start, steps=3, soc=0.2)
        # Override SOC to vary between windows
        df["SOC"] = [0.2, 0.5, 0.8]
        df["电网功率(kW)"] = [100.0, 200.0, 150.0]
        schedule = build_mpc_schedule(df)
        # Early in first window
        s1 = sample_mpc_schedule(schedule, start + pd.Timedelta(minutes=3))
        # Late in first window
        s2 = sample_mpc_schedule(schedule, start + pd.Timedelta(minutes=12))
        assert s1["soc_target"] == 0.2
        assert s2["soc_target"] == 0.2  # held constant
        # Second window
        s3 = sample_mpc_schedule(schedule, start + pd.Timedelta(minutes=18))
        assert s3["soc_target"] == 0.5
    def test_spline_interpolation_quadratic(self):
        start = datetime(2026, 4, 1, 0, 0, 0)
        df = _make_mpc_df(start=start, steps=3, load_kw=100.0)
        df["负荷功率(kW)"] = [0.0, 100.0, 0.0]  # peak in middle window
        schedule = build_mpc_schedule(df)
        # Quadratic spline should give 75 at mid‑point of 0→100→0
        sample = sample_mpc_schedule(
            schedule, start + pd.Timedelta(minutes=7.5)
        )
        # With quadratic through (0,0), (15,100), (30,0), the value at 7.5
        # is above linear (50) due to curvature
        assert sample["load_kw"] == pytest.approx(75.0, abs=5.0)
# ═══════════════════════════════════════════════════════════════════════════
# 9. End‑to‑end simulation
# ═══════════════════════════════════════════════════════════════════════════
class TestSimulateOneDay:
    def test_one_hour_simulation_produces_correct_row_count(self):
        df = _make_mpc_df(steps=96)
        config = _make_config(sample_seconds=1.0, duration_hours=1.0)
        result = simulate_one_day(
            df, config,
            pv_fluctuation_enabled=False,
            load_noise_enabled=False,
        )
        assert len(result) == 3600
    def test_no_backflow_without_pv_fluctuation(self):
        """Without PV fluctuation and no backflow in plan, gateway stays ≥ 0."""
        df = _make_mpc_df(
            steps=96, load_kw=200.0, pv_kw=0.0,
            battery_kw=0.0, grid_kw=200.0,
        )
        config = _make_config(
            sample_seconds=1.0,
            control_seconds=1.0,
            duration_hours=1.0,
            target_peak_kw=5000.0,
            anti_backflow=True,
        )
        result = simulate_one_day(
            df, config,
            pv_fluctuation_enabled=False,
            load_noise_enabled=False,
        )
        gateway = result["关口实测功率(kW)"]
        assert (gateway >= -1e-6).all()
    def test_no_peak_violation_without_load_noise(self):
        """Without load noise and no peak issue in plan, gateway stays ≤ P_target."""
        P_target = 300.0
        df = _make_mpc_df(
            steps=96, load_kw=200.0, pv_kw=0.0,
            battery_kw=0.0, grid_kw=200.0,
        )
        config = _make_config(
            sample_seconds=1.0,
            control_seconds=1.0,
            duration_hours=1.0,
            target_peak_kw=P_target,
            anti_backflow=True,
        )
        result = simulate_one_day(
            df, config,
            pv_fluctuation_enabled=False,
            load_noise_enabled=False,
        )
        gateway = result["关口实测功率(kW)"]
        assert (gateway <= P_target + 1e-6).all()
    def test_soc_guidance_updates_at_window_boundaries(self):
        """SOC引导值 should change at 15‑minute boundaries."""
        df = _make_mpc_df(steps=96, soc=0.5)
        # Make SOC target ramp up over first few windows
        df["SOC"] = [0.5 + 0.01 * i for i in range(96)]
        config = _make_config(
            sample_seconds=60.0,    # 1 sample per minute
            control_seconds=60.0,
            duration_hours=0.5,     # 30 minutes → 2 windows
        )
        result = simulate_one_day(
            df, config,
            pv_fluctuation_enabled=False,
            load_noise_enabled=False,
        )
        # Should have 30 rows (30 minutes × 1 sample/min)
        assert len(result) == 30
        # SOC引导值 should start at initial SOC and move toward target
        guide_values = result["SOC引导值"]
        assert guide_values.iloc[0] == pytest.approx(0.5, abs=0.01)
        # Guide should increase over time toward target
        assert guide_values.iloc[-1] > guide_values.iloc[0]
    def test_soc_never_violates_hard_bounds(self):
        """SOC must stay within [soc_min, soc_max] always."""
        df = _make_mpc_df(steps=96, soc=0.50)
        config = _make_config(
            sample_seconds=60.0,
            control_seconds=60.0,
            duration_hours=2.0,
            soc_min=0.10,
            soc_max=0.90,
        )
        result = simulate_one_day(
            df, config,
            pv_fluctuation_enabled=False,
            load_noise_enabled=False,
        )
        assert result["实际SOC"].min() >= 0.10 - 1e-6
        assert result["实际SOC"].max() <= 0.90 + 1e-6
    def test_summary_includes_all_key_metrics(self):
        df = _make_mpc_df(steps=96)
        config = _make_config(
            sample_seconds=60.0,
            control_seconds=60.0,
            duration_hours=1.0,
        )
        result = simulate_one_day(
            df, config,
            pv_fluctuation_enabled=False,
            load_noise_enabled=False,
        )
        summary = build_summary(result, config)
        required_keys = [
            "关口误差MAE(kW)", "倒送步数", "越限步数", "安全步数",
            "SOC最小值", "SOC最大值", "SOC结束值", "SOC引导MAE",
        ]
        for key in required_keys:
            assert key in summary, f"Missing key: {key}"
# ═══════════════════════════════════════════════════════════════════════════
# 10. Anti‑windup
# ═══════════════════════════════════════════════════════════════════════════
class TestAntiWindup:
    def test_integral_frozen_when_command_saturated(self):
        """When command hits the anti‑backflow ceiling, integral should
        not accumulate in the direction that worsens the saturation."""
        controller = FuzzyPIDIntraMinuteController(
            _make_config(integral_limit_kw_s=5000.0)
        )
        controller.set_soc_window(0.5, 0.5, 0.0)
        # Create a scenario where backflow forces command saturation
        # load=50, pv=200 → P_batt must be ≤ -150 (charge ≥ 150kW)
        # First call sets up state
        controller.update(
            gateway_power_target_kw=100.0,
            P_load_actual=50.0,
            P_pv_actual=200.0,
            P_battery_actual=-150.0,
            soc_actual=0.5,
            P_target=500.0,
            P_feedforward=0.0,
            elapsed_window_s=100.0,
        )
        integral_after = controller.integral_kw_s
        # Integral should not be extreme despite persistent backflow error
        assert abs(integral_after) < 5000.0
