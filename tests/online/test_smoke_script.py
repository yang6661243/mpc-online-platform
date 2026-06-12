from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "online_mpc_smoke_test.py"


def _load_smoke_module():
    spec = importlib.util.spec_from_file_location("online_mpc_smoke_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeClient:
    def __init__(self):
        self.calls = []

    def get_json(self, path):
        self.calls.append(("GET", path, None))
        if path == "/healthz":
            return {"status": "ok", "service": "online-mpc"}
        if path == "/api/v1/plants/smoke_factory/dashboard":
            return {
                "plant_id": "smoke_factory",
                "current": {
                    "grid_power_kw": 410.0,
                    "battery_power_kw": 25.0,
                    "load_minus_pv_kw": 435.0,
                    "soc": 0.58,
                    "quality_flag": "ok",
                },
                "comparison": None,
                "series": [],
            }
        raise AssertionError(f"unexpected GET {path}")

    def post_json(self, path, payload):
        self.calls.append(("POST", path, payload))
        if path == "/api/v1/mpc/input-data":
            return {"success": True, "accepted_count": len(payload["records"])}
        if path == "/api/v1/plants/smoke_factory/aggregate":
            return {
                "success": True,
                "plant_id": "smoke_factory",
                "window_count": 1,
                "quality_counts": {"ok": 1},
            }
        raise AssertionError(f"unexpected POST {path}")


def test_smoke_script_runs_expected_health_ingest_aggregate_dashboard_sequence():
    smoke = _load_smoke_module()
    client = FakeClient()

    summary = smoke.run_smoke(client, plant_id="smoke_factory")

    assert summary["plant_id"] == "smoke_factory"
    assert summary["dashboard_quality"] == "ok"
    assert summary["load_minus_pv_kw"] == 435.0
    assert [call[0:2] for call in client.calls] == [
        ("GET", "/healthz"),
        ("POST", "/api/v1/mpc/input-data"),
        ("POST", "/api/v1/mpc/input-data"),
        ("POST", "/api/v1/plants/smoke_factory/aggregate"),
        ("GET", "/api/v1/plants/smoke_factory/dashboard"),
    ]
    grid_payload = client.calls[1][2]
    battery_payload = client.calls[2][2]
    assert grid_payload["field_mapping"] == {"time": "ts", "grid_power_kw": "p_grid"}
    assert grid_payload["power_signs"] == {"grid_power_kw": -1}
    assert battery_payload["field_mapping"]["soc"] == "soc_pct"
    assert battery_payload["soc_unit"] == "percent"


def test_smoke_script_builds_hmac_signature_headers_from_raw_body():
    smoke = _load_smoke_module()
    body = b'{"request_id":"req_1"}'

    headers = smoke.build_signature_headers(
        secret="secret",
        body=body,
        timestamp="2026-06-12T10:15:00+08:00",
        request_id="req_1",
    )

    assert headers == {
        "X-Timestamp": "2026-06-12T10:15:00+08:00",
        "X-Request-Id": "req_1",
        "X-Signature": "sha256=2fb5466e0b711c572d8b4a7d69fc9ef90a2502469044e8fbadb5d34d5761c05a",
    }
