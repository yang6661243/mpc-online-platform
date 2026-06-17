"""Battery SOC reserve, real-time compensation, and power limiting utilities.

These are pure functions with no side effects — they compute battery power limits
and SOC targets from forecast data, used by both the offline MPC engine and the
online intra-minute controller.
"""
from __future__ import annotations

import numpy as np

DT = 0.25  # 15-minute step in hours


def build_dynamic_reserve_soc(
    load_fc,
    pv_kw,
    wind_kw,
    effective_target_kw,
    base_soc,
    risk_soc,
    high_soc,
    risk_margin_kw,
    high_margin_kw,
    enabled=True,
    current_soc=None,
    charge_max_kw=None,
    capacity_kwh=None,
    charge_eff=0.95,
    dt_hours=DT,
    soc_max=1.0,
):
    """Build a three-level SOC reserve profile from forecasted net-load risk."""
    if not enabled or effective_target_kw <= 0:
        return [base_soc] * len(load_fc)

    profile = []
    for load, pv, wind in zip(load_fc, pv_kw, wind_kw):
        net_load = max(0.0, load - pv - wind)
        margin = effective_target_kw - net_load
        if margin <= high_margin_kw:
            reserve = high_soc
        elif margin <= risk_margin_kw:
            reserve = risk_soc
        else:
            reserve = base_soc
        profile.append(max(base_soc, min(reserve, soc_max)))

    if current_soc is None or not charge_max_kw or not capacity_kwh:
        return profile

    safe_soc = current_soc
    reachable_profile = []
    for reserve, load, pv, wind in zip(profile, load_fc, pv_kw, wind_kw):
        net_load = max(0.0, load - pv - wind)
        peak_safe_charge_kw = max(0.0, effective_target_kw - net_load)
        safe_charge_kw = min(charge_max_kw, peak_safe_charge_kw)
        safe_soc += charge_eff * safe_charge_kw * dt_hours / capacity_kwh
        reachable_profile.append(max(base_soc, min(reserve, safe_soc, soc_max)))
    return reachable_profile


def estimate_under_forecast_margin(
    under_error_history_kw,
    quantile,
    warmup_kw,
    min_kw,
    max_kw,
    min_samples,
):
    """Estimate a conservative load under-forecast margin from recent errors."""
    if not under_error_history_kw:
        return max(min_kw, min(warmup_kw, max_kw))

    errors = [e for e in under_error_history_kw if e > 0]
    if not errors:
        return max(min_kw, min(warmup_kw if len(under_error_history_kw) < min_samples else min_kw, max_kw))
    percentile = float(np.percentile(errors, quantile * 100.0))
    if len(errors) < min_samples:
        margin = max(warmup_kw, percentile)
    else:
        margin = percentile
    return max(min_kw, min(margin, max_kw))


def build_reserve_energy_request(
    load_fc,
    pv_kw,
    wind_kw,
    effective_target_kw,
    risk_margin_kw,
    soc_min,
    soc_max,
    capacity_kwh,
    discharge_eff,
    dt_hours=DT,
    reserve_lead_hours=2.0,
    enabled=True,
):
    """Convert risk-adjusted forecast peak area into a checkpoint SOC target."""
    if not enabled or effective_target_kw <= 0 or risk_margin_kw <= 0 or not load_fc:
        return {"check_step": None, "target_soc": 0.0, "reserve_energy_kwh": 0.0}

    risk_net_load = [
        max(0.0, load - pv - wind) + risk_margin_kw
        for load, pv, wind in zip(load_fc, pv_kw, wind_kw)
    ]
    excess_kw = [max(net - effective_target_kw, 0.0) for net in risk_net_load]
    reserve_energy_kwh = sum(excess_kw) * dt_hours / discharge_eff
    if reserve_energy_kwh <= 1e-9:
        return {"check_step": None, "target_soc": 0.0, "reserve_energy_kwh": 0.0}

    peak_step = max(range(len(risk_net_load)), key=lambda i: risk_net_load[i])
    lead_steps = max(0, int(round(reserve_lead_hours / dt_hours)))
    check_step = max(0, peak_step - lead_steps)
    target_soc = soc_min + reserve_energy_kwh / capacity_kwh
    target_soc = max(soc_min, min(target_soc, soc_max))
    return {
        "check_step": check_step,
        "target_soc": target_soc,
        "reserve_energy_kwh": reserve_energy_kwh,
    }


def apply_realtime_load_compensation(
    battery_power_kw,
    load_error_kw,
    discharge_max_kw,
    charge_max_kw,
    previous_battery_power_kw=None,
    ramp_limit_kw=None,
    current_soc=None,
    soc_min=0.0,
    soc_max=1.0,
    capacity_kwh=None,
    charge_eff=0.95,
    discharge_eff=0.95,
    dt_hours=DT,
    vpp_window_now=False,
    vpp_discharge_compensation=True,
    deadband_kw=0.1,
):
    """Adjust first-step battery power for actual-vs-forecast load error."""
    bp = max(0.0, battery_power_kw) if vpp_window_now and battery_power_kw < 0 else battery_power_kw
    if abs(load_error_kw) <= deadband_kw:
        pass
    elif load_error_kw > 0:
        if vpp_window_now and not vpp_discharge_compensation:
            pass
        else:
            bp = bp + min(load_error_kw, discharge_max_kw - max(0.0, bp))
    elif vpp_window_now:
        bp = max(0.0, bp - min(-load_error_kw, max(0.0, bp)))
    else:
        bp = bp - min(-load_error_kw, charge_max_kw - max(0.0, -bp))

    bp = limit_battery_power_by_ramp(
        bp,
        previous_battery_power_kw=previous_battery_power_kw,
        ramp_limit_kw=ramp_limit_kw,
    )

    return limit_battery_power_by_soc(
        bp,
        current_soc=current_soc,
        soc_min=soc_min,
        soc_max=soc_max,
        capacity_kwh=capacity_kwh,
        charge_eff=charge_eff,
        discharge_eff=discharge_eff,
        dt_hours=dt_hours,
    )


def limit_battery_power_by_ramp(
    battery_power_kw,
    previous_battery_power_kw=None,
    ramp_limit_kw=None,
):
    """Clip battery power to the allowed change from the previous command."""
    bp = float(battery_power_kw)
    if previous_battery_power_kw is None or ramp_limit_kw is None or ramp_limit_kw <= 0:
        return bp
    prev = float(previous_battery_power_kw)
    limit = float(ramp_limit_kw)
    return max(prev - limit, min(prev + limit, bp))


def limit_battery_power_by_soc(
    battery_power_kw,
    current_soc=None,
    soc_min=0.0,
    soc_max=1.0,
    capacity_kwh=None,
    charge_eff=0.95,
    discharge_eff=0.95,
    dt_hours=DT,
    soc_tolerance=1e-4,
):
    """Clip first-step battery power so it cannot cross SOC bounds.

    Positive battery power means discharge; negative means charge.
    """
    bp = float(battery_power_kw)
    if current_soc is None or capacity_kwh is None or capacity_kwh <= 0 or dt_hours <= 0:
        return bp

    if bp > 0:
        available_soc = float(current_soc) - float(soc_min)
        if available_soc <= soc_tolerance:
            return 0.0
        available_kwh = available_soc * capacity_kwh
        max_discharge_kw = available_kwh * discharge_eff / dt_hours
        return min(bp, max_discharge_kw)

    if bp < 0:
        headroom_soc = float(soc_max) - float(current_soc)
        if headroom_soc <= soc_tolerance:
            return 0.0
        headroom_kwh = headroom_soc * capacity_kwh
        max_charge_kw = headroom_kwh / max(charge_eff, 1e-9) / dt_hours
        return -min(-bp, max_charge_kw)

    return bp


def _lightgbm_min_history_steps(forecaster, default=96):
    """Return the minimum history length required before calling LightGBM."""
    cfg = getattr(forecaster, '_cfg', {}) or {}
    feature_cfg = getattr(forecaster, '_feature_config', {}) or cfg.get('features', {}) or {}
    model_cfg = cfg.get('model', {}) or {}

    lags = feature_cfg.get('lags', []) or []
    rolling_windows = feature_cfg.get('rolling_windows', []) or []
    model_min_history = model_cfg.get('min_history_steps', 0) or 0

    return int(max(
        default,
        model_min_history,
        max(lags) if lags else 0,
        max(rolling_windows) if rolling_windows else 0,
    ))
