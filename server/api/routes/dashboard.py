"""Dashboard, display series, data health, and plant info endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.orm import Session

from api.constants import normalize_plant_id, PROJECT_ROOT
from api.services.dashboard import build_dashboard_payload
from api.services.data_health import build_data_health_payload
from api.services.display import build_display_series_payload
from api.services.plant_config import load_plant_config
from api.utils.dashboard_page import render_dashboard_page
from api.utils.dependencies import get_session
from api.utils.time import parse_timestamp

router = APIRouter()


def _dashboard_response(request: Request, default_plant_id: str):
    index_path = request.app.state.dashboard_dist_dir / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return HTMLResponse(render_dashboard_page(default_plant_id=default_plant_id))


@router.get("/", response_class=HTMLResponse)
def index(request: Request, plant_id: str = "hehong_huajin"):
    plant_id = normalize_plant_id(plant_id)
    return _dashboard_response(request, default_plant_id=plant_id)


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request, plant_id: str = "hehong_huajin"):
    plant_id = normalize_plant_id(plant_id)
    return _dashboard_response(request, default_plant_id=plant_id)


@router.get("/api/v1/plants/{plant_id}/info")
def plant_info(plant_id: str):
    plant_id = normalize_plant_id(plant_id)
    config_name = "hehong_huajin" if plant_id == "hehong_huajin" else "aodelai"
    config_path = PROJECT_ROOT / "mpc" / "configs" / "plants" / f"{config_name}.yaml"
    if not config_path.exists():
        raise HTTPException(status_code=404, detail=f"plant config not found: {plant_id}")
    plant = load_plant_config(config_path)
    return {
        "plant_id": plant.plant_id,
        "name": plant.name,
        "latitude": plant.location.latitude,
        "longitude": plant.location.longitude,
        "pv_capacity_kw": plant.pv.capacity_kw,
        "battery_power_kw": plant.battery.power_kw,
        "battery_capacity_kwh": plant.battery.capacity_kwh,
        "transformer_capacity_kw": plant.grid.transformer_capacity_kw,
    }


@router.get("/api/v1/plants/{plant_id}/dashboard")
def dashboard(
    plant_id: str,
    window_hours: int = 24,
    run_id: str | None = None,
    profile: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    session: Session = Depends(get_session),
):
    plant_id = normalize_plant_id(plant_id)
    return build_dashboard_payload(
        session,
        plant_id=plant_id,
        window_hours=window_hours,
        run_id=run_id,
        profile=profile,
        start_time=start_time,
        end_time=end_time,
    )


@router.get("/api/v1/plants/{plant_id}/display-series")
def display_series(
    plant_id: str,
    window_hours: int = Query(default=2),
    reference_time: str | None = None,
    max_gap_minutes: int = Query(default=5, gt=0),
    session: Session = Depends(get_session),
):
    plant_id = normalize_plant_id(plant_id)
    try:
        return build_display_series_payload(
            session,
            plant_id=plant_id,
            reference_time=parse_timestamp(reference_time) if reference_time else None,
            window_hours=window_hours,
            max_gap_minutes=max_gap_minutes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/v1/plants/{plant_id}/data-health")
def data_health(
    plant_id: str,
    reference_time: str | None = None,
    max_raw_delay_minutes: int = Query(default=30, gt=0),
    max_telemetry_delay_minutes: int = Query(default=30, gt=0),
    session: Session = Depends(get_session),
):
    plant_id = normalize_plant_id(plant_id)
    try:
        return build_data_health_payload(
            session,
            plant_id=plant_id,
            reference_time=reference_time,
            max_raw_delay_minutes=max_raw_delay_minutes,
            max_telemetry_delay_minutes=max_telemetry_delay_minutes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
