from pathlib import Path

from microgrid_online.api import create_app
from microgrid_online.mpc_cli_runner import MicrogridMpcCliRunner
from microgrid_online.plant_config import load_plant_config, plant_config_to_mpc_cli_config


def test_load_hehong_huajin_plant_config_contains_static_assets():
    cfg = load_plant_config(Path("configs/plants/hehong_huajin.yaml"))

    assert cfg.plant_id == "hehong_huajin"
    assert cfg.location.latitude == 30.8300938
    assert cfg.location.longitude == 121.2213572
    assert cfg.battery.power_kw == 375.0
    assert cfg.battery.capacity_kwh == 783.0
    assert cfg.battery.soc_min == 0.1
    assert cfg.battery.soc_max == 0.9
    assert cfg.pv.capacity_kw == 350.0
    assert cfg.wind.capacity_kw == 0.0
    assert cfg.tariff.demand_charge_yuan_per_kw_month == 39.0
    assert [period.name for period in cfg.tariff.periods] == ["谷", "平", "峰"]


def test_hehong_plant_config_can_build_online_mpc_runner_config():
    cfg = load_plant_config(Path("configs/plants/hehong_huajin.yaml"))

    runner_cfg = plant_config_to_mpc_cli_config(cfg, project_root=Path("."))

    assert runner_cfg.battery_capacity_kwh == 783.0
    assert runner_cfg.battery_charge_max_kw == 375.0
    assert runner_cfg.battery_discharge_max_kw == 375.0
    assert runner_cfg.battery_soc_min == 0.1
    assert runner_cfg.battery_soc_max == 0.9
    assert runner_cfg.battery_charge_eff == 0.95
    assert runner_cfg.battery_discharge_eff == 0.95
    assert runner_cfg.grid_export_max_kw == 0.0
    assert runner_cfg.anti_backflow is True


def test_default_app_mpc_runner_uses_hehong_plant_config():
    app = create_app()

    assert isinstance(app.state.mpc_runner, MicrogridMpcCliRunner)
    runner_cfg = app.state.mpc_runner.config
    assert runner_cfg.battery_capacity_kwh == 783.0
    assert runner_cfg.battery_charge_max_kw == 375.0
    assert runner_cfg.battery_discharge_max_kw == 375.0
    assert runner_cfg.battery_soc_min == 0.1
    assert runner_cfg.battery_soc_max == 0.9
