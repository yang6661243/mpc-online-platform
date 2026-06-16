from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import os

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from microgrid_online.aggregation import aggregate_telemetry_15min
from microgrid_online.dashboard_data import build_dashboard_payload, comparison_payload
from microgrid_online.dashboard_page import render_dashboard_page
from microgrid_online.data_health import build_data_health_payload
from microgrid_online.display_series import build_display_series_payload
from microgrid_online.excel_import import import_mpc_run_from_excel
from microgrid_online.time_utils import parse_timestamp
from microgrid_online.database import create_session_factory
from microgrid_online.ingestion import ingest_battery_records, ingest_grid_records
from microgrid_online.input_mapping import normalize_input_records
from microgrid_online.models import MpcRun, StrategyComparison
from microgrid_online.mpc_cli_runner import (
    CommandRunner,
    MicrogridMpcCliRunner,
    MicrogridMpcCliRunnerConfig,
)
from microgrid_online.mpc_run import MpcRunner, MpcRunnerNotConfigured, run_online_mpc
from microgrid_online.plant_config import load_plant_config, plant_config_to_mpc_cli_config
from microgrid_online.signature import verify_signature


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DASHBOARD_DIST_DIR = PROJECT_ROOT / "web" / "mpc-dashboard" / "dist"
DEFAULT_PLANT_CONFIG_PATH = PROJECT_ROOT / "configs" / "plants" / "hehong_huajin.yaml"

DEFAULT_CORS_ORIGINS = [
    "https://ecloud.hoenergypower.cn",
    "chrome-extension://becnmfbeidffckhenedfiahikaagpgek",
]

PLANT_ID_ALIASES = {
    "ecloud_factory": "hehong_huajin",
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


def _cors_origins_from_env() -> list[str]:
    configured = os.getenv("ONLINE_MPC_CORS_ORIGINS")
    if not configured:
        return DEFAULT_CORS_ORIGINS
    origins = [origin.strip() for origin in configured.split(",") if origin.strip()]
    return origins or DEFAULT_CORS_ORIGINS


def _get_session_factory(app: FastAPI) -> Callable[[], Session]:
    return app.state.session_factory


def normalize_plant_id(plant_id: str) -> str:
    return PLANT_ID_ALIASES.get(plant_id, plant_id)


def _resolve_plant_config_path(plant_config_path: str | Path | None) -> Path:
    configured = plant_config_path or os.getenv("MPC_PLANT_CONFIG_PATH") or DEFAULT_PLANT_CONFIG_PATH
    path = Path(configured)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def _default_mpc_runner_config(
    *,
    plant_config_path: str | Path | None,
    project_root: str | Path | None,
) -> MicrogridMpcCliRunnerConfig:
    root = Path(project_root or PROJECT_ROOT)
    plant = load_plant_config(_resolve_plant_config_path(plant_config_path))
    return plant_config_to_mpc_cli_config(plant, project_root=root)


def create_app(
    session_factory: Callable[[], Session] | None = None,
    *,
    mpc_runner: MpcRunner | None = None,
    enable_default_mpc_runner: bool = True,
    mpc_command_runner: CommandRunner | None = None,
    mpc_runner_project_root: str | Path | None = None,
    mpc_runner_config: MicrogridMpcCliRunnerConfig | None = None,
    plant_config_path: str | Path | None = None,
    run_output_dir: str | Path = "outputs/online_mpc_runs",
    input_signature_secret: str | None = None,
    dashboard_dist_dir: str | Path | None = None,
) -> FastAPI:
    app = FastAPI(title="Online MPC Service")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins_from_env(),
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )
    app.state.session_factory = session_factory or create_session_factory()
    app.state.input_signature_secret = input_signature_secret or os.getenv("MPC_INPUT_SIGNATURE_SECRET")
    if mpc_runner is None and enable_default_mpc_runner:
        app.state.mpc_runner = MicrogridMpcCliRunner(
            mpc_runner_config
            or _default_mpc_runner_config(
                plant_config_path=plant_config_path,
                project_root=mpc_runner_project_root or PROJECT_ROOT,
            ),
            command_runner=mpc_command_runner,
        )
    else:
        app.state.mpc_runner = mpc_runner
    app.state.run_output_dir = Path(run_output_dir)
    app.state.dashboard_dist_dir = Path(dashboard_dist_dir or DEFAULT_DASHBOARD_DIST_DIR)
    dashboard_assets_dir = app.state.dashboard_dist_dir / "assets"
    if dashboard_assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=dashboard_assets_dir), name="dashboard-assets")

    def get_session():
        session = _get_session_factory(app)()
        try:
            yield session
        finally:
            close = getattr(session, "close", None)
            if callable(close):
                close()

    def dashboard_response(default_plant_id: str):
        index_path = app.state.dashboard_dist_dir / "index.html"
        if index_path.exists():
            return FileResponse(index_path)
        return HTMLResponse(render_dashboard_page(default_plant_id=default_plant_id))

    @app.get("/", response_class=HTMLResponse)
    def index(plant_id: str = "hehong_huajin"):
        plant_id = normalize_plant_id(plant_id)
        return dashboard_response(default_plant_id=plant_id)

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard_page(plant_id: str = "hehong_huajin"):
        plant_id = normalize_plant_id(plant_id)
        return dashboard_response(default_plant_id=plant_id)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "service": "online-mpc"}

    @app.post("/api/v1/mpc/input-data")
    async def input_data(
        payload: InputDataRequest,
        request: Request,
        session: Session = Depends(get_session),
        x_timestamp: str | None = Header(default=None, alias="X-Timestamp"),
        x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
        x_signature: str | None = Header(default=None, alias="X-Signature"),
    ):
        try:
            try:
                verify_signature(
                    secret=app.state.input_signature_secret,
                    body=await request.body(),
                    timestamp=x_timestamp,
                    request_id=x_request_id,
                    signature=x_signature,
                )
            except ValueError as exc:
                raise HTTPException(status_code=401, detail=str(exc)) from exc

            records = normalize_input_records(
                payload.data_type,
                payload.records,
                field_mapping=payload.field_mapping,
                power_signs=payload.power_signs,
                soc_unit=payload.soc_unit,
            )
            plant_id = normalize_plant_id(payload.plant_id)
            if payload.data_type == "grid_meter":
                accepted = ingest_grid_records(
                    session,
                    plant_id=plant_id,
                    records=records,
                )
            elif payload.data_type == "battery":
                accepted = ingest_battery_records(
                    session,
                    plant_id=plant_id,
                    records=records,
                )
            else:
                raise HTTPException(status_code=400, detail=f"unsupported data_type: {payload.data_type}")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return {
            "success": True,
            "request_id": payload.request_id,
            "plant_id": plant_id,
            "data_type": payload.data_type,
            "accepted_count": accepted,
            "duplicate": False,
            "message": "accepted",
        }

    @app.post("/api/v1/mpc/run")
    def run_mpc(payload: RunMpcRequest, session: Session = Depends(get_session)):
        plant_id = normalize_plant_id(payload.plant_id)
        try:
            result = run_online_mpc(
                session,
                request_id=payload.request_id,
                plant_id=plant_id,
                start_time=payload.start_time,
                end_time=payload.end_time,
                profile=payload.profile,
                runner=app.state.mpc_runner,
                output_dir=app.state.run_output_dir,
                load_base_kw=payload.load_base_kw,
                buy_price=payload.buy_price,
                sell_price=payload.sell_price,
                c_deg=payload.c_deg,
                demand_rate=payload.demand_rate,
                billing_days=payload.billing_days,
                target_peak_kw=payload.target_peak_kw,
            )
        except MpcRunnerNotConfigured as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        return {
            "success": True,
            "run_id": result.run.run_id,
            "plant_id": result.run.plant_id,
            "status": result.run.status,
            "target_peak_kw": payload.target_peak_kw,
            "scenario_path": str(result.scenario.output_path),
            "comparison": comparison_payload(result.comparison),
            "message": "mpc run succeeded",
        }

    @app.post("/api/v1/plants/{plant_id}/aggregate")
    def aggregate_plant_telemetry(
        plant_id: str,
        payload: AggregateRequest,
        session: Session = Depends(get_session),
    ):
        plant_id = normalize_plant_id(plant_id)
        try:
            rows = aggregate_telemetry_15min(
                session,
                plant_id=plant_id,
                start_time=payload.start_time,
                end_time=payload.end_time,
                window_minutes=payload.window_minutes,
                resample_minutes=payload.resample_minutes,
                max_staleness_minutes=payload.max_staleness_minutes,
                battery_power_mode=payload.battery_power_mode
                or os.getenv("MPC_BATTERY_POWER_MODE", "signed_meter"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        quality_counts: dict[str, int] = {}
        for row in rows:
            quality_counts[row.quality_flag] = quality_counts.get(row.quality_flag, 0) + 1

        return {
            "success": True,
            "plant_id": plant_id,
            "window_count": len(rows),
            "quality_counts": quality_counts,
        }

    @app.get("/api/v1/mpc/runs/{run_id}")
    def mpc_run_result(run_id: str, session: Session = Depends(get_session)):
        run = session.scalar(select(MpcRun).where(MpcRun.run_id == run_id))
        if run is None:
            raise HTTPException(status_code=404, detail="mpc run not found")

        comparison = session.scalar(
            select(StrategyComparison)
            .where(StrategyComparison.run_id == run_id)
            .order_by(StrategyComparison.created_at.desc())
            .limit(1)
        )
        return {
            "run_id": run.run_id,
            "plant_id": run.plant_id,
            "profile": run.profile,
            "status": run.status,
            "started_at": None if run.started_at is None else run.started_at.isoformat(),
            "finished_at": None if run.finished_at is None else run.finished_at.isoformat(),
            "input_start_time": None if run.input_start_time is None else run.input_start_time.isoformat(),
            "input_end_time": None if run.input_end_time is None else run.input_end_time.isoformat(),
            "scenario_path": run.scenario_path,
            "error_message": run.error_message,
            "comparison": comparison_payload(comparison),
        }

    @app.get("/api/v1/plants/{plant_id}/dashboard")
    def dashboard(
        plant_id: str,
        window_hours: int = 24,
        run_id: str | None = None,
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
            start_time=start_time,
            end_time=end_time,
        )

    @app.get("/api/v1/plants/{plant_id}/display-series")
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

    @app.post("/api/v1/plants/{plant_id}/import-mpc-run")
    async def import_mpc_run(
        plant_id: str,
        profile: str = Query(default="imported", description="策略名称，如 100%需量+峰谷套利"),
        file: UploadFile = File(...),
        session: Session = Depends(get_session),
    ):
        plant_id = normalize_plant_id(plant_id)
        if not file.filename or not (file.filename.endswith(".xlsx") or file.filename.endswith(".xls")):
            raise HTTPException(status_code=400, detail="仅支持 .xlsx 或 .xls 文件")
        try:
            content = await file.read()
            result = import_mpc_run_from_excel(
                session,
                plant_id=plant_id,
                file_bytes=content,
                profile=profile,
            )
            return result
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/v1/plants/{plant_id}/data-health")
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

    return app


app = create_app()
