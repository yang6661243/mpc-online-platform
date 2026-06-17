from __future__ import annotations

from collections.abc import Iterable, Sequence


def build_vpp_window_mask(
    timestamps: Iterable,
    start_hour: int = 11,
    end_hour: int = 13,
) -> list[bool]:
    """Return True for timestamps inside the VPP declaration window."""
    return [start_hour <= ts.hour < end_hour for ts in timestamps]


def build_workday_average_baseline(
    grid_kw: Sequence[float],
    baseline_days: int = 5,
    steps_per_day: int = 96,
) -> list[float]:
    """Use previous same-slot workday values; fallback to current value if history is short."""
    baseline = []
    for i, current in enumerate(grid_kw):
        same_slot_history = [
            float(grid_kw[i - day * steps_per_day])
            for day in range(1, baseline_days + 1)
            if i - day * steps_per_day >= 0
        ]
        if same_slot_history:
            baseline.append(sum(same_slot_history) / len(same_slot_history))
        else:
            baseline.append(float(current))
    return baseline


def compute_vpp_step_benefit(
    grid_import_kw: float,
    baseline_kw: float,
    buy_price: float,
    charge_price: float,
    eligible: bool,
    response_cap_kw: float | None,
    dt_hours: float,
) -> tuple[float, float]:
    """Return above-baseline VPP settlement power and benefit for one dispatch step."""
    if not eligible:
        return 0.0, 0.0

    response_kw = max(float(grid_import_kw) - float(baseline_kw), 0.0)
    if response_cap_kw is not None:
        response_kw = min(response_kw, float(response_cap_kw))

    benefit = max(float(buy_price) - float(charge_price), 0.0) * response_kw * dt_hours
    return response_kw, benefit
