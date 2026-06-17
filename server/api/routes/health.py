"""Health check and version endpoints."""
from fastapi import APIRouter

router = APIRouter()
BACKEND_VERSION = 2


@router.get("/healthz")
def healthz():
    return {"status": "ok", "service": "online-mpc"}


@router.get("/api/v1/version")
def backend_version():
    return {"backend_version": BACKEND_VERSION, "version": f"0.1.{BACKEND_VERSION:02d}"}
