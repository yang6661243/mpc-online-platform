"""Shared constants, Pydantic models, and helper functions for the API."""
from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DASHBOARD_DIST_DIR = PROJECT_ROOT / "frontend" / "dist"
DEFAULT_PLANT_CONFIG_PATH = PROJECT_ROOT / "mpc" / "configs" / "plants" / "hehong_huajin.yaml"

DEFAULT_CORS_ORIGINS = [
    "https://ecloud.hoenergypower.cn",
    "chrome-extension://becnmfbeidffckhenedfiahikaagpgek",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

PLANT_ID_ALIASES = {
    "ecloud_factory": "hehong_huajin",
    "ecloud_station_3341": "aolaide",
    "aodelai": "aolaide",
}

ALL_PLANTS = ["hehong_huajin", "aolaide"]
ALL_PROFILES = ["demand100", "demand70", "demand40"]

PROFILE_CONFIGS: dict[str, dict[str, dict]] = {
    "hehong_huajin": {
        "demand100": {"demand_rate": 39.0, "target_peak_ratio": 1.0, "demand_label": "100%需量+峰谷套利"},
        "demand70": {"demand_rate": 27.3, "target_peak_ratio": 0.70, "demand_label": "70%需量+峰谷套利"},
        "demand40": {"demand_rate": 15.6, "target_peak_ratio": 0.40, "demand_label": "40%需量+峰谷套利"},
    },
    "aolaide": {
        "demand100": {"demand_rate": 39.0, "target_peak_ratio": 1.0, "demand_label": "100%需量+峰谷套利"},
        "demand70": {"demand_rate": 27.3, "target_peak_ratio": 0.70, "demand_label": "70%需量+峰谷套利"},
        "demand40": {"demand_rate": 15.6, "target_peak_ratio": 0.40, "demand_label": "40%需量+峰谷套利"},
    },
}


class InputDataRequest(BaseModel):
    request_id: str
    plant_id: str
    data_type: str
    generated_at: str
    records: list[dict] = Field(default_factory=list)
    field_mapping: dict[str, str] = Field(default_factory=dict)
    power_signs: dict[str, float] = Field(default_factory=dict)
    soc_unit: str = "ratio"


class RunMpcRequest(BaseModel):
    request_id: str
    plant_id: str
    start_time: str
    end_time: str
    profile: str | None = None
    load_base_kw: float | None = None
    buy_price: float = 0.8
    sell_price: float = 0.3
    c_deg: float = 0.05
    demand_rate: float = 30.0
    billing_days: float = 30.0
    target_peak_kw: float | None = Field(default=None, gt=0)


class AggregateRequest(BaseModel):
    start_time: str
    end_time: str
    window_minutes: int = 15
    resample_minutes: int = 1
    max_staleness_minutes: int = 10
    battery_power_mode: str | None = None


def normalize_plant_id(plant_id: str) -> str:
    return PLANT_ID_ALIASES.get(plant_id, plant_id)


def cors_origins_from_env() -> list[str]:
    configured = os.getenv("ONLINE_MPC_CORS_ORIGINS")
    if not configured:
        return DEFAULT_CORS_ORIGINS
    origins = [origin.strip() for origin in configured.split(",") if origin.strip()]
    return origins or DEFAULT_CORS_ORIGINS
