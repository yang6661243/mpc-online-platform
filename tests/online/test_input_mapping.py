import pytest

from microgrid_online.input_mapping import normalize_input_records


def test_grid_records_can_be_normalized_from_cloud_field_names():
    records = normalize_input_records(
        "grid_meter",
        [{"ts": "2026-06-12T10:00:00+08:00", "p_grid": -410.0}],
        field_mapping={"time": "ts", "grid_power_kw": "p_grid"},
        power_signs={"grid_power_kw": -1},
    )

    assert records == [
        {"time": "2026-06-12T10:00:00+08:00", "grid_power_kw": 410.0},
    ]


def test_battery_records_can_normalize_percent_soc_and_power_sign():
    records = normalize_input_records(
        "battery",
        [
            {
                "采集时间": "2026-06-12T10:00:00+08:00",
                "储能功率": -20.0,
                "SOC百分比": 58.0,
                "电池可用": True,
                "PCS可用": False,
            }
        ],
        field_mapping={
            "time": "采集时间",
            "battery_power_kw": "储能功率",
            "soc": "SOC百分比",
            "battery_available": "电池可用",
            "pcs_available": "PCS可用",
        },
        power_signs={"battery_power_kw": -1},
        soc_unit="percent",
    )

    assert records == [
        {
            "time": "2026-06-12T10:00:00+08:00",
            "battery_power_kw": 20.0,
            "soc": 0.58,
            "battery_available": True,
            "pcs_available": False,
        }
    ]


def test_mapping_rejects_missing_required_source_field():
    with pytest.raises(ValueError, match="missing source field"):
        normalize_input_records(
            "grid_meter",
            [{"time": "2026-06-12T10:00:00+08:00"}],
            field_mapping={"grid_power_kw": "p_grid"},
        )


def test_mapping_rejects_unknown_soc_unit():
    with pytest.raises(ValueError, match="soc_unit"):
        normalize_input_records(
            "battery",
            [{"time": "2026-06-12T10:00:00+08:00", "battery_power_kw": 20.0, "soc": 58.0}],
            soc_unit="percentage_points",
        )
