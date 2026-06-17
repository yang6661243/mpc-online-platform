"""Shared FastAPI dependencies for route handlers."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import Request
from sqlalchemy.orm import Session


def get_session(request: Request) -> Session:
    """Yield a SQLAlchemy session from the app's session factory."""
    session = request.app.state.session_factory()
    try:
        yield session
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()


def get_mpc_runner(request: Request):
    """Return the MPC runner configured on the app."""
    return request.app.state.mpc_runner


def get_run_output_dir(request: Request) -> Path:
    """Return the MPC run output directory."""
    return Path(request.app.state.run_output_dir)


def get_project_root() -> Path:
    """Return the project root directory."""
    return Path(__file__).resolve().parents[3]
