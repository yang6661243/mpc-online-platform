"""Fuzzy PID decomposition runner.

Reads the latest MPC 15‑minute targets (MpcTarget), runs the intra‑minute
Fuzzy PID controller for each minute, and persists per‑minute PCS commands
as IntraMinutePoint rows.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from mpc.microgrid.intra_minute_controller import (
    ControllerConfig,
    FuzzyPIDIntraMinuteController,
    build_mpc_schedule,
    sample_mpc_schedule,
)
from api.database.orm import IntraMinutePoint, MpcTarget, Telemetry15Min

logger = logging.getLogger(__name__)


def _load_mpc_targets(
    session: Session,
    *,
    plant_id: str,
    profile: str,
    from_time: datetime,
    to_time: datetime,
) -> list[MpcTarget]:
    return list(
        session.scalars(
            select(MpcTarget)
            .where(
                MpcTarget.plant_id == plant_id,
                MpcTarget.profile == profile,
                MpcTarget.valid_from >= from_time,
                MpcTarget.valid_until <= to_time,
            )
            .order_by(MpcTarget.valid_from)
        )
    )


def _build_mpc_dataframe(targets: list[MpcTarget]) -> "pd.DataFrame":
    """Build a 15‑minute MPC output DataFrame from MpcTarget rows.

    The DataFrame must match the columns expected by
    ``build_mpc_schedule`` in intra_minute_controller.py.
    """
    import pandas as pd

    rows = []
    for t in targets:
        rows.append(
            {
                "时间": t.valid_from,
                "光伏出力(kW)": 0.0,
                "风电出力(kW)": 0.0,
                "负荷功率(kW)": 0.0,
                "SOC": t.target_soc,
                "电池功率(kW)": 0.0,
                "电网功率(kW)": t.target_peak_kw,
                "购电价(元/kWh)": 0.8,
                "售电价(元/kWh)": 0.3,
            }
        )
    return pd.DataFrame(rows)


def _load_telemetry_at(
    session: Session,
    *,
    plant_id: str,
    at_time: datetime,
    tolerance: timedelta = timedelta(seconds=60),
) -> Telemetry15Min | None:
    """Return the Telemetry15Min window that covers *at_time*."""
    return session.scalar(
        select(Telemetry15Min)
        .where(
            Telemetry15Min.plant_id == plant_id,
            Telemetry15Min.start_time <= at_time,
            Telemetry15Min.end_time >= at_time,
        )
        .order_by(Telemetry15Min.start_time.desc())
        .limit(1)
    )


def run_fuzzy_pid_decomposition(
    session: Session,
    *,
    plant_id: str,
    profile: str,
    run_id: str,
    from_time: datetime,
    to_time: datetime,
    pcs_power_limit_kw: float = 375.0,
    energy_capacity_kwh: float = 783.0,
    target_peak_kw: float = 5000.0,
    anti_backflow: bool = True,
    sample_seconds: float = 60.0,
    control_seconds: float = 60.0,
    on_point: Callable[[IntraMinutePoint], None] | None = None,
) -> list[IntraMinutePoint]:
    """Run Fuzzy PID decomposition over [from_time, to_time] at per‑minute
    resolution, persist results, and return the list of IntraMinutePoint rows.

    Parameters
    ----------
    on_point :
        Optional callback invoked for every persisted point (useful for
        real‑time streaming / WebSocket push).
    """
    targets = _load_mpc_targets(
        session,
        plant_id=plant_id,
        profile=profile,
        from_time=from_time,
        to_time=to_time,
    )
    if not targets:
        logger.warning("No MPC targets for %s/%s in [%s, %s]", plant_id, profile, from_time, to_time)
        return []

    mpc_df = _build_mpc_dataframe(targets)
    schedule = build_mpc_schedule(mpc_df)

    config = ControllerConfig(
        sample_seconds=sample_seconds,
        control_seconds=control_seconds,
        energy_capacity_kwh=energy_capacity_kwh,
        pcs_power_limit_kw=pcs_power_limit_kw,
        target_peak_kw=target_peak_kw,
        anti_backflow=anti_backflow,
        soc_guide_enabled=True,
        duration_hours=max(1.0, (to_time - from_time).total_seconds() / 3600.0),
    )
    controller = FuzzyPIDIntraMinuteController(config)

    # Initial SOC from the first telemetry window
    first_telem = _load_telemetry_at(session, plant_id=plant_id, at_time=from_time)
    soc = float(first_telem.soc_end) if first_telem and first_telem.soc_end is not None else float(targets[0].target_soc)
    actual_pcs_kw = float(first_telem.battery_power_kw_avg) if first_telem and first_telem.battery_power_kw_avg is not None else 0.0

    # Set initial SOC guidance window
    first_target = targets[0]
    controller.set_soc_window(soc, first_target.target_soc, 0.0)

    # First‑order plant time constant
    tau_seconds = config.response_seconds_0_to_100kw / 4.6
    alpha = 1.0 - __import__("math").exp(-config.sample_seconds / tau_seconds)

    points: list[IntraMinutePoint] = []
    total_steps = int((to_time - from_time).total_seconds() / config.sample_seconds)
    prev_window_idx = -1
    last_update: dict = {
        "pcs_command_kw": actual_pcs_kw,
        "severity": "safe",
        "soc_guide": soc,
        "soc_guide_error": 0.0,
        "fade": 1.0,
        "pid_power_kw": 0.0,
        "soc_reference_kw": 0.0,
        "feedforward_kw": 0.0,
        "kp": 0.0,
        "ki": 0.0,
        "kd": 0.0,
        "gateway_error_kw": 0.0,
        "filtered_gateway_error_kw": 0.0,
    }

    for step in range(total_steps):
        now = from_time + timedelta(seconds=step * config.sample_seconds)
        sched = sample_mpc_schedule(schedule, now)

        # Get real‑time telemetry for this minute
        telem = _load_telemetry_at(session, plant_id=plant_id, at_time=now)
        quality = "ok"

        if telem and telem.quality_flag in ("ok", "interpolated"):
            load_actual = float(telem.load_minus_pv_kw_avg or 0.0)
            pv_actual = 0.0  # load_minus_pv already nets out PV
            pcs_actual = float(telem.battery_power_kw_avg or 0.0)
            telem_soc = float(telem.soc_end or soc)
        else:
            # Stale or missing — use last known values
            load_actual = 0.0
            pv_actual = 0.0
            pcs_actual = actual_pcs_kw
            telem_soc = soc
            quality = "stale" if telem is not None else "interpolated"

        # Detect window boundary
        window_idx = int(sched.get("window_index", 0))
        if window_idx != prev_window_idx and window_idx < len(targets):
            controller.set_soc_window(
                soc_actual=soc,
                soc_mpc_target=float(targets[window_idx].target_soc),
                window_start_time_s=window_idx * config.window_seconds if hasattr(config, 'window_seconds') else window_idx * 900.0,
            )
            prev_window_idx = window_idx

        # Control update
        control_update = (step == 0) or __import__("math").isclose(
            (step * config.sample_seconds) % config.control_seconds,
            0.0,
            abs_tol=1e-9,
        )
        if control_update:
            last_update = controller.update(
                gateway_power_target_kw=float(sched.get("grid_power_kw", target_peak_kw)),
                P_load_actual=load_actual,
                P_pv_actual=pv_actual,
                P_battery_actual=actual_pcs_kw,
                soc_actual=soc,
                P_target=target_peak_kw,
                P_feedforward=float(sched.get("battery_power_kw", 0.0)),
                elapsed_window_s=float(sched.get("elapsed_window_s", 0.0)),
            )

        # PCS plant response
        actual_pcs_kw += (last_update["pcs_command_kw"] - actual_pcs_kw) * alpha

        # SOC update
        if actual_pcs_kw >= 0:
            soc -= (actual_pcs_kw * config.sample_seconds / 3600.0) / config.energy_capacity_kwh / config.discharge_eff
        else:
            soc += (abs(actual_pcs_kw) * config.sample_seconds / 3600.0) * config.charge_eff / config.energy_capacity_kwh
        soc = max(config.soc_min, min(config.soc_max, soc))

        # Grid power
        P_grid_final = load_actual - pv_actual - actual_pcs_kw

        point = IntraMinutePoint(
            run_id=run_id,
            plant_id=plant_id,
            profile=profile,
            time=now,
            pcs_command_kw=round(last_update["pcs_command_kw"], 4),
            pcs_actual_kw=round(actual_pcs_kw, 4),
            soc_guide=round(last_update["soc_guide"], 6),
            soc_actual=round(soc, 6),
            grid_power_kw=round(P_grid_final, 4),
            grid_target_kw=round(float(sched.get("grid_power_kw", target_peak_kw)), 4),
            severity=str(last_update.get("severity", "safe")),
            quality_flag=quality,
        )
        session.add(point)
        points.append(point)

        if on_point is not None:
            on_point(point)

    session.commit()
    logger.info(
        "Fuzzy PID decomposition complete: %s/%s run=%s points=%d",
        plant_id, profile, run_id, len(points),
    )
    return points
