from pathlib import Path

import pandas as pd

from microgrid.factory_policy import build_factory_policy_report, write_factory_policy_excel


def _write_workbooks(tmp_path: Path) -> tuple[Path, Path]:
    actual_path = tmp_path / "actual.xlsx"
    scenario_path = tmp_path / "scenario.xlsx"
    times = pd.date_range("2026-05-01 00:00:00", periods=4, freq="15min")

    actual = pd.DataFrame(
        {
            "time": times,
            "grid_kw": [100.0, 120.0, -10.0, 80.0],
            "battery_kw": [20.0, -10.0, 0.0, 5.0],
            "soc": [0.50, 0.51, 0.50, 0.505],
        }
    )
    actual.to_excel(actual_path, sheet_name="Sheet1", index=False)

    with pd.ExcelWriter(scenario_path, engine="openpyxl") as writer:
        pd.DataFrame({"time": times, "load": [0.5, 0.6, 0.4, 0.55]}).to_excel(
            writer, sheet_name="load_apr", index=False
        )
        pd.DataFrame(
            {
                "time": times,
                "global_tilted_irradiance (W/m2)": [0.0, 10.0, 50.0, 0.0],
                "wind_speed_100m (km/h)": [0.0, 0.0, 0.0, 0.0],
            }
        ).to_excel(writer, sheet_name="pv_apr", index=False)
        pd.DataFrame(
            {
                "time": times,
                "buy_price": [1.0, 1.0, 1.0, 1.0],
                "sell_price": [0.2, 0.2, 0.2, 0.2],
            }
        ).to_excel(writer, sheet_name="price", index=False)

    return actual_path, scenario_path


def test_factory_report_aligns_inputs_and_computes_costs(tmp_path):
    actual_path, scenario_path = _write_workbooks(tmp_path)

    report = build_factory_policy_report(
        {
            "actual": {
                "file": str(actual_path),
                "sheet": "Sheet1",
                "columns": {
                    "time": "time",
                    "grid_power_kw": "grid_kw",
                    "battery_power_kw": "battery_kw",
                    "soc": "soc",
                },
                "battery_power_sign": "positive_charge",
            },
            "scenario": {
                "file": str(scenario_path),
                "sheets": {"load": "load_apr", "pv_wind": "pv_apr", "price": "price"},
                "load_base_kw": 1000.0,
            },
            "evaluation": {
                "start": "2026-05-01 00:00:00",
                "end": "2026-05-01 00:45:00",
                "freq_minutes": 15,
            },
            "cost": {
                "c_deg": 0.05,
                "demand_rate": 30.0,
                "capacity_rate": 0.0,
                "billing_days": 30.0,
            },
        }
    )

    assert len(report.trajectory) == 4
    assert report.alignment["aligned_points"] == 4
    assert report.alignment["missing_points"] == 0
    assert report.cost_summary["purchase_cost"] == 75.0
    assert report.cost_summary["export_revenue"] == 0.5
    assert report.cost_summary["degradation_cost"] == 0.4375
    assert report.cost_summary["demand_charge"] == 5.0
    assert report.cost_summary["comprehensive_cost"] == 79.9375
    assert report.daily_summary.loc[0, "charge_energy_kwh"] == 6.25
    assert report.daily_summary.loc[0, "discharge_energy_kwh"] == 2.5


def test_factory_report_writes_expected_excel_sheets(tmp_path):
    actual_path, scenario_path = _write_workbooks(tmp_path)
    report = build_factory_policy_report(
        {
            "actual": {
                "file": str(actual_path),
                "sheet": "Sheet1",
                "columns": {
                    "time": "time",
                    "grid_power_kw": "grid_kw",
                    "battery_power_kw": "battery_kw",
                    "soc": "soc",
                },
            },
            "scenario": {
                "file": str(scenario_path),
                "sheets": {"load": "load_apr", "pv_wind": "pv_apr", "price": "price"},
                "load_base_kw": 1000.0,
            },
            "evaluation": {"start": "2026-05-01 00:00:00", "end": "2026-05-01 00:45:00"},
            "cost": {"c_deg": 0.05, "demand_rate": 30.0, "billing_days": 30.0},
        }
    )

    output_path = tmp_path / "factory_policy.xlsx"
    write_factory_policy_excel(report, output_path)

    sheets = pd.ExcelFile(output_path).sheet_names
    assert sheets == ["15min_trajectory", "daily_summary", "cost_summary", "alignment_summary"]
