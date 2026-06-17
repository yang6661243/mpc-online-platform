"""FastAPI application factory for the Online MPC Service."""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

# ── MPC 全链路日志 ──
LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
_mpc_log_handler = logging.FileHandler(LOG_DIR / "mpc_chain.log", encoding="utf-8")
_mpc_log_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
))
for _name in ("api.services.mpc.runner", "api.services.mpc.orchestrator",
              "api.services.mpc.health", "api.routes.mpc",
              "api.services.aggregation"):
    _l = logging.getLogger(_name)
    _l.setLevel(logging.INFO)
    _l.addHandler(_mpc_log_handler)

from api.constants import (
    ALL_PLANTS,
    ALL_PROFILES,
    DEFAULT_DASHBOARD_DIST_DIR,
    DEFAULT_PLANT_CONFIG_PATH,
    PROFILE_CONFIGS,
    cors_origins_from_env,
)
from api.database import create_session_factory
from api.services.mpc.health import (
    MpcHealthChecker,
    MpcScheduler,
    run_health_checker_loop,
    run_periodic_scheduler,
)
from api.services.mpc.orchestrator import MpcRunner, OnlineMpcRunInput
from api.services.mpc.runner import (
    CommandRunner,
    MicrogridMpcCliRunner,
    MicrogridMpcCliRunnerConfig,
)
from api.services.plant_config import load_plant_config, plant_config_to_mpc_cli_config
from api.services.mpc.adapter import export_mpc_scenario_from_telemetry

PROJECT_ROOT = Path(__file__).resolve().parents[2]
import sys
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


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
    health_checker_plants: list[str] | None = None,
    health_checker_profiles: list[str] | None = None,
) -> FastAPI:
    sf = session_factory or create_session_factory()
    plants = health_checker_plants or ALL_PLANTS
    profiles = health_checker_profiles or ALL_PROFILES

    # ── Build per‑profile health checkers ──
    health_checkers: dict[str, MpcHealthChecker] = {}
    for plant_id in plants:
        for profile in profiles:
            key = f"{plant_id}/{profile}"
            health_checkers[key] = MpcHealthChecker(
                sf, plant_id=plant_id, profile=profile,
            )

    # ── Build per‑plant schedulers ──
    def _mk_mpc_runner(plant_id: str):
        def _run(pid: str, prof: str, rid: str, ft: datetime, tt: datetime) -> str | None:
            mpc_r = app.state.mpc_runner if hasattr(app.state, "mpc_runner") else None
            if mpc_r is None:
                raise RuntimeError("MPC runner not configured")
            sess = sf()
            try:
                scenario = export_mpc_scenario_from_telemetry(
                    sess, plant_id=pid, start_time=ft, end_time=tt,
                    output_path=Path(run_output_dir) / rid / "scenario.xlsx",
                )
                cfg = PROFILE_CONFIGS.get(pid, {}).get(prof, {})
                result = mpc_r(
                    OnlineMpcRunInput(
                        run_id=rid, plant_id=pid, profile=prof,
                        start_time=ft, end_time=tt,
                        scenario=scenario, telemetry_rows=[],
                        actual_metrics=None,  # type: ignore[arg-type]
                        buy_price=0.8, sell_price=0.3,
                        c_deg=0.05,
                        demand_rate=cfg.get("demand_rate", 39.0),
                        billing_days=30,
                        target_peak_kw=None,
                    )
                )
                return rid
            finally:
                sess.close()
        return _run

    def _mk_fuzzy_runner(plant_id: str):
        from api.services.mpc.fuzzy_pid import run_fuzzy_pid_decomposition

        def _run(pid: str, prof: str, rid: str, ft: datetime, tt: datetime) -> list:
            sess = sf()
            try:
                plant_config_path_ = Path(
                    f"mpc/configs/plants/{'hehong_huajin' if pid == 'hehong_huajin' else 'aodelai'}.yaml"
                )
                if not plant_config_path_.is_absolute():
                    plant_config_path_ = PROJECT_ROOT / plant_config_path_
                plant = load_plant_config(plant_config_path_) if plant_config_path_.exists() else None
                pcs_limit = plant.battery.power_kw if plant else 375.0
                capacity = plant.battery.capacity_kwh if plant else 783.0
                peak = plant.grid.transformer_capacity_kw if plant else 5000.0
                ab = plant.grid.anti_backflow if plant else True
                return run_fuzzy_pid_decomposition(
                    sess, plant_id=pid, profile=prof, run_id=rid,
                    from_time=ft, to_time=tt,
                    pcs_power_limit_kw=pcs_limit,
                    energy_capacity_kwh=capacity,
                    target_peak_kw=peak,
                    anti_backflow=ab,
                )
            finally:
                sess.close()
        return _run

    schedulers: dict[str, MpcScheduler] = {}
    for plant_id in plants:
        schedulers[plant_id] = MpcScheduler(
            plant_id=plant_id,
            profiles=list(profiles),
            run_mpc=_mk_mpc_runner(plant_id),
            run_fuzzy=_mk_fuzzy_runner(plant_id),
        )

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        tasks: list[asyncio.Task] = []
        for checker in health_checkers.values():
            tasks.append(asyncio.create_task(run_health_checker_loop(checker)))
        for scheduler in schedulers.values():
            tasks.append(asyncio.create_task(run_periodic_scheduler(scheduler, sf)))
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    app = FastAPI(title="Online MPC Service", lifespan=_lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins_from_env(),
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )
    app.state.session_factory = sf
    app.state.input_signature_secret = input_signature_secret or os.getenv("MPC_INPUT_SIGNATURE_SECRET")
    if mpc_runner is None and enable_default_mpc_runner:
        app.state.mpc_runner = MicrogridMpcCliRunner(
            mpc_runner_config
            or _default_mpc_runner_config(
                plant_config_path=plant_config_path,
                project_root=mpc_runner_project_root or PROJECT_ROOT,
            ),
            command_runner=mpc_command_runner,
            session_factory=sf,
        )
    else:
        app.state.mpc_runner = mpc_runner
    app.state.health_checkers = health_checkers
    app.state.schedulers = schedulers
    app.state.run_output_dir = Path(run_output_dir)
    app.state.dashboard_dist_dir = Path(dashboard_dist_dir or DEFAULT_DASHBOARD_DIST_DIR)
    dashboard_assets_dir = app.state.dashboard_dist_dir / "assets"
    if dashboard_assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=dashboard_assets_dir), name="dashboard-assets")

    # ── Register route modules ──
    from api.routes.health import router as health_router
    from api.routes.ingestion import router as ingestion_router
    from api.routes.mpc import router as mpc_router
    from api.routes.dashboard import router as dashboard_router

    app.include_router(health_router)
    app.include_router(ingestion_router)
    app.include_router(mpc_router)
    app.include_router(dashboard_router)

    return app


app = create_app()
