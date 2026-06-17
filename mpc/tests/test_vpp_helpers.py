import pandas as pd

from mpc.microgrid.vpp import (
    build_workday_average_baseline,
    build_vpp_window_mask,
    compute_vpp_step_benefit,
)


def test_vpp_window_mask_marks_1100_to_before_1300():
    times = pd.to_datetime(
        [
            "2026-05-06 10:45:00",
            "2026-05-06 11:00:00",
            "2026-05-06 12:45:00",
            "2026-05-06 13:00:00",
        ]
    )

    assert build_vpp_window_mask(times) == [False, True, True, False]


def test_workday_average_baseline_uses_previous_same_slot_values():
    grid_kw = [
        10.0,
        20.0,
        30.0,
        40.0,
        50.0,
        60.0,
    ]

    baseline = build_workday_average_baseline(
        grid_kw,
        baseline_days=2,
        steps_per_day=2,
    )

    assert baseline == [10.0, 20.0, 10.0, 20.0, 20.0, 30.0]


def test_vpp_step_benefit_settles_above_baseline_power_and_uses_optional_cap():
    uncapped = compute_vpp_step_benefit(
        grid_import_kw=120.0,
        baseline_kw=80.0,
        buy_price=1.0,
        charge_price=0.2,
        eligible=True,
        response_cap_kw=None,
        dt_hours=0.25,
    )
    capped = compute_vpp_step_benefit(
        grid_import_kw=120.0,
        baseline_kw=80.0,
        buy_price=1.0,
        charge_price=0.2,
        eligible=True,
        response_cap_kw=10.0,
        dt_hours=0.25,
    )

    assert uncapped == (40.0, 8.0)
    assert capped == (10.0, 2.0)
