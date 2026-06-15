from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from microgrid_online.mpc_cli_runner import MicrogridMpcCliRunnerConfig


@dataclass(frozen=True)
class PlantLocation:
    latitude: float
    longitude: float
    timezone: str


@dataclass(frozen=True)
class PlantBattery:
    power_kw: float
    capacity_kwh: float
    soc_min: float
    soc_max: float
    soc_init: float
    charge_efficiency: float
    discharge_efficiency: float


@dataclass(frozen=True)
class PlantPv:
    capacity_kw: float
    performance_ratio: float


@dataclass(frozen=True)
class PlantWind:
    capacity_kw: float


@dataclass(frozen=True)
class PlantGrid:
    anti_backflow: bool
    import_max_kw: float
    export_max_kw: float
    transformer_capacity_kw: float


@dataclass(frozen=True)
class TariffPeriod:
    name: str
    price_yuan_per_kwh: float
    ranges: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class PlantTariff:
    name: str
    demand_charge_yuan_per_kw_month: float
    periods: tuple[TariffPeriod, ...]


@dataclass(frozen=True)
class PlantConfig:
    plant_id: str
    name: str
    location: PlantLocation
    battery: PlantBattery
    pv: PlantPv
    wind: PlantWind
    grid: PlantGrid
    tariff: PlantTariff


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a mapping")
    return value


def _number(value: Any, field_name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric") from exc


def _positive_number(value: Any, field_name: str) -> float:
    number = _number(value, field_name)
    if number <= 0:
        raise ValueError(f"{field_name} must be positive")
    return number


def _soc(value: Any, field_name: str) -> float:
    number = _number(value, field_name)
    if not 0 <= number <= 1:
        raise ValueError(f"{field_name} must be between 0 and 1")
    return number


def _string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _tariff_periods(value: Any) -> tuple[TariffPeriod, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("tariff.periods must be a non-empty list")
    periods = []
    for index, item in enumerate(value):
        period = _mapping(item, f"tariff.periods[{index}]")
        ranges = period.get("ranges")
        if not isinstance(ranges, list) or not ranges:
            raise ValueError(f"tariff.periods[{index}].ranges must be a non-empty list")
        parsed_ranges = []
        for range_index, pair in enumerate(ranges):
            if not isinstance(pair, list | tuple) or len(pair) != 2:
                raise ValueError(f"tariff.periods[{index}].ranges[{range_index}] must contain start and end")
            parsed_ranges.append(
                (
                    _string(pair[0], f"tariff.periods[{index}].ranges[{range_index}][0]"),
                    _string(pair[1], f"tariff.periods[{index}].ranges[{range_index}][1]"),
                )
            )
        periods.append(
            TariffPeriod(
                name=_string(period.get("name"), f"tariff.periods[{index}].name"),
                price_yuan_per_kwh=_positive_number(
                    period.get("price_yuan_per_kwh"),
                    f"tariff.periods[{index}].price_yuan_per_kwh",
                ),
                ranges=tuple(parsed_ranges),
            )
        )
    return tuple(periods)


def load_plant_config(path: str | Path) -> PlantConfig:
    config_path = Path(path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    data = _mapping(raw, str(config_path))

    location = _mapping(data.get("location"), "location")
    battery = _mapping(data.get("battery"), "battery")
    pv = _mapping(data.get("pv"), "pv")
    wind = _mapping(data.get("wind"), "wind")
    grid = _mapping(data.get("grid"), "grid")
    tariff = _mapping(data.get("tariff"), "tariff")

    soc_min = _soc(battery.get("soc_min"), "battery.soc_min")
    soc_max = _soc(battery.get("soc_max"), "battery.soc_max")
    soc_init = _soc(battery.get("soc_init"), "battery.soc_init")
    if soc_min >= soc_max:
        raise ValueError("battery.soc_min must be less than battery.soc_max")
    if not soc_min <= soc_init <= soc_max:
        raise ValueError("battery.soc_init must be within soc_min and soc_max")

    return PlantConfig(
        plant_id=_string(data.get("plant_id"), "plant_id"),
        name=_string(data.get("name"), "name"),
        location=PlantLocation(
            latitude=_number(location.get("latitude"), "location.latitude"),
            longitude=_number(location.get("longitude"), "location.longitude"),
            timezone=_string(location.get("timezone"), "location.timezone"),
        ),
        battery=PlantBattery(
            power_kw=_positive_number(battery.get("power_kw"), "battery.power_kw"),
            capacity_kwh=_positive_number(battery.get("capacity_kwh"), "battery.capacity_kwh"),
            soc_min=soc_min,
            soc_max=soc_max,
            soc_init=soc_init,
            charge_efficiency=_positive_number(battery.get("charge_efficiency"), "battery.charge_efficiency"),
            discharge_efficiency=_positive_number(
                battery.get("discharge_efficiency"),
                "battery.discharge_efficiency",
            ),
        ),
        pv=PlantPv(
            capacity_kw=_number(pv.get("capacity_kw"), "pv.capacity_kw"),
            performance_ratio=_positive_number(pv.get("performance_ratio"), "pv.performance_ratio"),
        ),
        wind=PlantWind(capacity_kw=_number(wind.get("capacity_kw"), "wind.capacity_kw")),
        grid=PlantGrid(
            anti_backflow=bool(grid.get("anti_backflow", True)),
            import_max_kw=_positive_number(grid.get("import_max_kw"), "grid.import_max_kw"),
            export_max_kw=_number(grid.get("export_max_kw"), "grid.export_max_kw"),
            transformer_capacity_kw=_positive_number(
                grid.get("transformer_capacity_kw"),
                "grid.transformer_capacity_kw",
            ),
        ),
        tariff=PlantTariff(
            name=_string(tariff.get("name"), "tariff.name"),
            demand_charge_yuan_per_kw_month=_positive_number(
                tariff.get("demand_charge_yuan_per_kw_month"),
                "tariff.demand_charge_yuan_per_kw_month",
            ),
            periods=_tariff_periods(tariff.get("periods")),
        ),
    )


def plant_config_to_mpc_cli_config(
    plant: PlantConfig,
    *,
    project_root: str | Path,
    horizon_steps: int = 96,
) -> MicrogridMpcCliRunnerConfig:
    return MicrogridMpcCliRunnerConfig(
        project_root=project_root,
        battery_capacity_kwh=plant.battery.capacity_kwh,
        battery_charge_max_kw=plant.battery.power_kw,
        battery_discharge_max_kw=plant.battery.power_kw,
        battery_soc_min=plant.battery.soc_min,
        battery_soc_max=plant.battery.soc_max,
        battery_charge_eff=plant.battery.charge_efficiency,
        battery_discharge_eff=plant.battery.discharge_efficiency,
        grid_import_max_kw=plant.grid.import_max_kw,
        grid_export_max_kw=plant.grid.export_max_kw,
        transformer_capacity_kw=plant.grid.transformer_capacity_kw,
        anti_backflow=plant.grid.anti_backflow,
        horizon_steps=horizon_steps,
    )
