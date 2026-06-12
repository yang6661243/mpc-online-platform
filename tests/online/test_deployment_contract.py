from pathlib import Path

from fastapi.testclient import TestClient

from microgrid_online.api import create_app
from microgrid_online.database import create_session_factory, create_sqlite_memory_session


ROOT = Path(__file__).resolve().parents[2]


def test_healthz_endpoint_reports_service_status():
    session = create_sqlite_memory_session()
    client = TestClient(create_app(session_factory=lambda: session))

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "online-mpc"}


def test_dockerfile_runs_online_mpc_service():
    dockerfile = ROOT / "Dockerfile"

    text = dockerfile.read_text(encoding="utf-8")

    assert "ARG PYTHON_BASE_IMAGE=python:3.12-slim" in text
    assert "FROM ${PYTHON_BASE_IMAGE}" in text
    assert "ARG APT_MIRROR=" in text
    assert "ARG APT_SECURITY_MIRROR=" in text
    assert "ARG PIP_INDEX_URL=" in text
    assert "deb.debian.org/debian" in text
    assert "deb.debian.org/debian-security" in text
    assert "pip install --no-cache-dir -r requirements.txt" in text
    assert "uvicorn" in text
    assert "microgrid_online.api:app" in text
    assert "--host" in text
    assert "0.0.0.0" in text
    assert "--port" in text
    assert "8000" in text
    assert "HEALTHCHECK" in text
    assert "/healthz" in text


def test_dockerignore_excludes_runtime_outputs_and_caches():
    dockerignore = ROOT / ".dockerignore"

    lines = {
        line.strip()
        for line in dockerignore.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }

    assert "__pycache__/" in lines
    assert "*.pyc" in lines
    assert ".venv/" in lines
    assert ".venv*/" in lines
    assert ".env" in lines
    assert "data/" in lines
    assert "outputs/" in lines
    assert "logs/" in lines
    assert "tmp/" in lines
    assert "scenarios/*.xlsx" in lines
    assert "scenarios/*.csv" in lines
    assert ".DS_Store" in lines


def test_gitignore_excludes_runtime_outputs_secrets_and_factory_data():
    gitignore = ROOT / ".gitignore"

    lines = {
        line.strip()
        for line in gitignore.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }

    assert ".env" in lines
    assert ".venv*/" in lines
    assert "data/" in lines
    assert "outputs/" in lines
    assert "logs/" in lines
    assert "tmp/" in lines
    assert "scenarios/*.xlsx" in lines
    assert "scenarios/*.csv" in lines


def test_default_session_factory_uses_database_url_environment(monkeypatch, tmp_path):
    db_path = tmp_path / "online_mpc.db"
    monkeypatch.setenv("MPC_DATABASE_URL", f"sqlite:///{db_path}")

    session_factory = create_session_factory()
    session = session_factory()
    session.close()

    assert db_path.exists()


def test_docker_compose_declares_service_ports_volumes_and_environment():
    compose = ROOT / "docker-compose.online.yml"

    text = compose.read_text(encoding="utf-8")

    assert "mpc-online" in text
    assert "${MPC_ONLINE_PORT:-8000}:8000" in text
    assert "./data:/app/data" in text
    assert "./outputs:/app/outputs" in text
    assert "./scenarios:/app/scenarios" in text
    assert "MPC_DATABASE_URL" in text
    assert "MPC_INPUT_SIGNATURE_SECRET" in text
    assert "/healthz" in text


def test_release_compose_uses_prebuilt_image_without_build_context():
    compose = ROOT / "docker-compose.release.yml"

    text = compose.read_text(encoding="utf-8")

    assert "MPC_ONLINE_IMAGE" in text
    assert "build:" not in text
    assert "${MPC_ONLINE_PORT:-8000}:8000" in text
    assert "./data:/app/data" in text
    assert "./outputs:/app/outputs" in text
    assert "./scenarios:/app/scenarios" in text
    assert "MPC_DATABASE_URL" in text
    assert "MPC_INPUT_SIGNATURE_SECRET" in text
    assert "/healthz" in text


def test_env_example_documents_online_mpc_runtime_variables():
    env_example = ROOT / ".env.example"

    text = env_example.read_text(encoding="utf-8")

    assert "MPC_DATABASE_URL=sqlite:////app/data/mpc_online.db" in text
    assert "MPC_INPUT_SIGNATURE_SECRET=" in text
    assert "MPC_ONLINE_BASE_URL=http://127.0.0.1:8000" in text


def test_docker_release_script_builds_and_optionally_pushes_registry_image():
    script = ROOT / "scripts" / "docker_release.sh"

    text = script.read_text(encoding="utf-8")

    assert "#!/usr/bin/env bash" in text
    assert "--image" in text
    assert "--version" in text
    assert "--base-image" in text
    assert "--apt-mirror" in text
    assert "--apt-security-mirror" in text
    assert "--pip-index-url" in text
    assert "--push" in text
    assert "docker build" in text
    assert "--platform" in text
    assert "--build-arg" in text
    assert "PYTHON_BASE_IMAGE" in text
    assert "APT_MIRROR" in text
    assert "APT_SECURITY_MIRROR" in text
    assert "PIP_INDEX_URL" in text
    assert "docker push" in text
    assert "MPC_ONLINE_IMAGE=" in text
