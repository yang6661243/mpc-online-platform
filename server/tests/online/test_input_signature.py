import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from server.api.main import create_app
from server.api.database import create_sqlite_memory_session


def _signature(secret: str, body: bytes, timestamp: str, request_id: str) -> str:
    message = timestamp.encode() + b"\n" + request_id.encode() + b"\n" + body
    return "sha256=" + hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def test_input_data_accepts_valid_hmac_signature():
    session = create_sqlite_memory_session()
    client = TestClient(create_app(session_factory=lambda: session, input_signature_secret="secret"))
    payload = {
        "request_id": "req_signed_grid",
        "plant_id": "aodelai",
        "data_type": "grid_meter",
        "generated_at": "2026-06-12T10:15:00+08:00",
        "records": [{"time": "2026-06-12T10:00:00+08:00", "grid_power_kw": 400.0}],
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    timestamp = "2026-06-12T10:15:00+08:00"

    response = client.post(
        "/api/v1/mpc/input-data",
        content=body,
        headers={
            "content-type": "application/json",
            "X-Timestamp": timestamp,
            "X-Request-Id": payload["request_id"],
            "X-Signature": _signature("secret", body, timestamp, payload["request_id"]),
        },
    )

    assert response.status_code == 200
    assert response.json()["accepted_count"] == 1


def test_input_data_rejects_invalid_hmac_signature():
    session = create_sqlite_memory_session()
    client = TestClient(create_app(session_factory=lambda: session, input_signature_secret="secret"))

    response = client.post(
        "/api/v1/mpc/input-data",
        json={
            "request_id": "req_bad_signature",
            "plant_id": "aodelai",
            "data_type": "grid_meter",
            "generated_at": "2026-06-12T10:15:00+08:00",
            "records": [{"time": "2026-06-12T10:00:00+08:00", "grid_power_kw": 400.0}],
        },
        headers={
            "X-Timestamp": "2026-06-12T10:15:00+08:00",
            "X-Request-Id": "req_bad_signature",
            "X-Signature": "sha256=bad",
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "invalid signature"


def test_input_data_rejects_missing_signature_when_secret_is_configured():
    session = create_sqlite_memory_session()
    client = TestClient(create_app(session_factory=lambda: session, input_signature_secret="secret"))

    response = client.post(
        "/api/v1/mpc/input-data",
        json={
            "request_id": "req_missing_signature",
            "plant_id": "aodelai",
            "data_type": "grid_meter",
            "generated_at": "2026-06-12T10:15:00+08:00",
            "records": [{"time": "2026-06-12T10:00:00+08:00", "grid_power_kw": 400.0}],
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "missing signature headers"
