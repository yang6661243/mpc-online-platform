from pathlib import Path

import pandas as pd
import pytest

from server.api.database import create_sqlite_memory_session
from server.api.database.orm import Telemetry15Min
from server.api.services.mpc.adapter import export_mpc_scenario_from_telemetry
from server.api.utils.time import parse_timestamp


def _add_telemetry(session, plant_id="aodelai"):
    rows = [
        Telemetry15Min(
            plant_id=plant_id,
            start_time=parse_timestamp("2026-06-12T10:00:00+08:00"),
            end_time=parse_timestamp("2026-06-12T10:15:00+08:00"),
            grid_power_kw_avg=400.0,
            battery_power_kw_avg=20.0,
            load_minus_pv_kw_avg=420.0,
            soc_start=0.60,
            soc_end=0.59,
            grid_sample_count=3,
            battery_sample_count=3,
            quality_flag="ok",
        ),
        Telemetry15Min(
            plant_id=plant_id,
            start_time=parse_timestamp("2026-06-12T10:15:00+08:00"),
            end_time=parse_timestamp("2026-06-12T10:30:00+08:00"),
            grid_power_kw_avg=430.0,
            battery_power_kw_avg=-10.0,
            load_minus_pv_kw_avg=420.0,
            soc_start=0.59,
            soc_end=0.60,
            grid_sample_count=3,
            battery_sample_count=3,
            quality_flag="ok",
        ),
    ]
    session.add_all(rows)
    session.commit()


def test_export_mpc_scenario_writes_current_mpc_workbook(tmp_path: Path):
    session = create_sqlite_memory_session()
    _add_telemetry(session)
    output_path = tmp_path / "online_mpc_scenario.xlsx"

    result = export_mpc_scenario_from_telemetry(
        session,
        plant_id="aodelai",
        start_time="2026-06-12T10:00:00+08:00",
        end_time="2026-06-12T10:30:00+08:00",
        output_path=output_path,
        load_base_kw=1000.0,
        buy_price=0.8,
        sell_price=0.3,
    )

    assert result.output_path == output_path
    assert result.load_base_kw == 1000.0
    assert result.steps == 2

    sheets = pd.ExcelFile(output_path).sheet_names
    assert sheets == ["load", "pv", "price"]

    load = pd.read_excel(output_path, sheet_name="load")
    assert list(load.columns) == ["time", "load"]
    assert load["load"].round(3).tolist() == [0.42, 0.42]

    pv = pd.read_excel(output_path, sheet_name="pv")
    assert pv["irradiance"].tolist() == [0.0, 0.0]
    assert pv["wind_speed"].tolist() == [0.0, 0.0]

    price = pd.read_excel(output_path, sheet_name="price")
    assert price["buy_price"].tolist() == [0.8, 0.8]
    assert price["sell_price"].tolist() == [0.3, 0.3]


def test_export_mpc_scenario_rejects_incomplete_telemetry(tmp_path: Path):
    session = create_sqlite_memory_session()
    session.add(
        Telemetry15Min(
            plant_id="aodelai",
            start_time=parse_timestamp("2026-06-12T10:00:00+08:00"),
            end_time=parse_timestamp("2026-06-12T10:15:00+08:00"),
            grid_power_kw_avg=400.0,
            battery_power_kw_avg=None,
            load_minus_pv_kw_avg=None,
            soc_end=None,
            grid_sample_count=3,
            battery_sample_count=0,
            quality_flag="missing_battery",
        )
    )
    session.commit()

    with pytest.raises(ValueError, match="incomplete telemetry"):
        export_mpc_scenario_from_telemetry(
            session,
            plant_id="aodelai",
            start_time="2026-06-12T10:00:00+08:00",
            end_time="2026-06-12T10:15:00+08:00",
            output_path=tmp_path / "bad.xlsx",
        )
