"""Tests for MPC forecast history gating."""

import numpy as np
import pytest
from openpyxl import Workbook

from mpc.microgrid.mpc import (
    _compose_forecast_history,
    _lightgbm_min_history_steps,
    _load_forecast_history_prefix,
    _parse_manual_target_peak,
    _resolve_target_peak_mode,
)


class DummyForecaster:
    def __init__(self, cfg=None, feature_config=None):
        self._cfg = cfg or {}
        self._feature_config = feature_config or {}


def test_lightgbm_history_gate_uses_largest_lag_not_fixed_96():
    forecaster = DummyForecaster(
        cfg={'model': {'min_history_steps': 672}},
        feature_config={'lags': [1, 2, 3, 96, 672], 'rolling_windows': [96]},
    )

    assert _lightgbm_min_history_steps(forecaster) == 672


def test_lightgbm_history_gate_uses_model_min_history_when_larger():
    forecaster = DummyForecaster(
        cfg={'model': {'min_history_steps': 800}},
        feature_config={'lags': [1, 96, 672], 'rolling_windows': [96]},
    )

    assert _lightgbm_min_history_steps(forecaster) == 800


def test_lightgbm_history_gate_defaults_to_96_for_old_models():
    forecaster = DummyForecaster()

    assert _lightgbm_min_history_steps(forecaster) == 96


def test_compose_forecast_history_uses_prefix_and_past_only():
    prefix = np.array([0.1, 0.2], dtype=np.float64)
    scenario = [0.3, 0.4, 0.5]

    at_start = _compose_forecast_history(scenario, abs_t=0, history_prefix=prefix)
    after_one_step = _compose_forecast_history(scenario, abs_t=1, history_prefix=prefix)

    assert at_start.tolist() == [0.1, 0.2]
    assert after_one_step.tolist() == [0.1, 0.2, 0.3]
    assert 0.4 not in after_one_step
    assert 0.5 not in after_one_step


def test_load_forecast_history_prefix_from_config(tmp_path):
    path = tmp_path / "history.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "train"
    ws.append(["时间", "有功"])
    ws.append(["2026-03-01 00:00:00", 0.12])
    ws.append(["2026-03-01 00:15:00", 0.34])
    wb.save(path)

    cfg = {
        "forecast_history": {
            "data_file": str(path),
            "sheet": "train",
            "unit": "ratio",
        }
    }

    prefix, label = _load_forecast_history_prefix(cfg, DummyForecaster(), load_base_kw=1000)

    assert prefix.tolist() == [0.12, 0.34]
    assert "forecast_history" in label


def test_target_peak_mode_defaults_are_backward_compatible():
    assert _resolve_target_peak_mode({"target_peak_kw": None}) == "oracle_full_period"
    assert _resolve_target_peak_mode({"target_peak_kw": 0}) == "oracle_full_period"
    assert _resolve_target_peak_mode({"target_peak_kw": 520}) == "manual"


def test_target_peak_mode_aliases_and_manual_validation():
    assert _resolve_target_peak_mode({"target_peak_mode": "deployable"}) == "forecast_month_plan"
    assert _parse_manual_target_peak({"target_peak_mode": "manual", "target_peak_kw": "520"}) == 520.0

    with pytest.raises(ValueError, match="requires a positive target_peak_kw"):
        _parse_manual_target_peak({"target_peak_mode": "manual", "target_peak_kw": None})

    with pytest.raises(ValueError, match="Invalid mpc.target_peak_mode"):
        _resolve_target_peak_mode({"target_peak_mode": "future_truth"})
