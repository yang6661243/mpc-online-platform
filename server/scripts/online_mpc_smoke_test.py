from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


class SmokeError(RuntimeError):
    pass


def build_signature_headers(*, secret: str, body: bytes, timestamp: str, request_id: str) -> dict[str, str]:
    message = timestamp.encode() + b"\n" + request_id.encode() + b"\n" + body
    digest = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return {
        "X-Timestamp": timestamp,
        "X-Request-Id": request_id,
        "X-Signature": "sha256=" + digest,
    }


class HttpJsonClient:
    def __init__(self, base_url: str, secret: str | None = None, timeout: float = 10.0):
        self.base_url = base_url
        self.secret = secret
        self.timeout = timeout

    def get_json(self, path: str) -> dict:
        return self._request_json("GET", path)

    def post_json(self, path: str, payload: dict) -> dict:
        return self._request_json("POST", path, payload)

    def _request_json(self, method: str, path: str, payload: dict | None = None) -> dict:
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
            if self.secret and path == "/api/v1/mpc/input-data":
                request_id = str(payload["request_id"])
                timestamp = str(payload["generated_at"])
                headers.update(
                    build_signature_headers(
                        secret=self.secret,
                        body=body,
                        timestamp=timestamp,
                        request_id=request_id,
                    )
                )

        request = Request(
            url=urljoin(self.base_url.rstrip("/") + "/", path.lstrip("/")),
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                response_body = response.read()
                return json.loads(response_body.decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise SmokeError(f"{method} {path} failed with HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise SmokeError(f"{method} {path} failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise SmokeError(f"{method} {path} timed out") from exc


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeError(message)


def run_smoke(client, *, plant_id: str) -> dict:
    health = client.get_json("/healthz")
    _assert(health.get("status") == "ok", f"unexpected health response: {health}")

    start_time = "2026-06-12T10:00:00+08:00"
    mid_time = "2026-06-12T10:05:00+08:00"
    end_time = "2026-06-12T10:15:00+08:00"

    grid_payload = {
        "request_id": f"smoke_grid_{plant_id}",
        "plant_id": plant_id,
        "data_type": "grid_meter",
        "generated_at": end_time,
        "field_mapping": {"time": "ts", "grid_power_kw": "p_grid"},
        "power_signs": {"grid_power_kw": -1},
        "records": [
            {"ts": start_time, "p_grid": -400.0},
            {"ts": mid_time, "p_grid": -420.0},
        ],
    }
    grid_result = client.post_json("/api/v1/mpc/input-data", grid_payload)
    _assert(grid_result.get("success") is True, f"grid ingest failed: {grid_result}")

    battery_payload = {
        "request_id": f"smoke_battery_{plant_id}",
        "plant_id": plant_id,
        "data_type": "battery",
        "generated_at": end_time,
        "field_mapping": {
            "time": "ts",
            "battery_power_kw": "p_bess",
            "soc": "soc_pct",
        },
        "soc_unit": "percent",
        "records": [
            {"ts": start_time, "p_bess": 20.0, "soc_pct": 60.0},
            {"ts": mid_time, "p_bess": 30.0, "soc_pct": 58.0},
        ],
    }
    battery_result = client.post_json("/api/v1/mpc/input-data", battery_payload)
    _assert(battery_result.get("success") is True, f"battery ingest failed: {battery_result}")

    aggregate_result = client.post_json(
        f"/api/v1/plants/{plant_id}/aggregate",
        {
            "start_time": start_time,
            "end_time": end_time,
            "window_minutes": 15,
        },
    )
    _assert(aggregate_result.get("success") is True, f"aggregate failed: {aggregate_result}")
    _assert(aggregate_result.get("quality_counts", {}).get("ok", 0) >= 1, f"bad aggregate quality: {aggregate_result}")

    dashboard = client.get_json(f"/api/v1/plants/{plant_id}/dashboard")
    current = dashboard.get("current") or {}
    _assert(current.get("quality_flag") == "ok", f"bad dashboard quality: {dashboard}")
    _assert(current.get("load_minus_pv_kw") is not None, f"dashboard missing load_minus_pv_kw: {dashboard}")

    return {
        "plant_id": plant_id,
        "health": health,
        "grid_accepted_count": grid_result.get("accepted_count"),
        "battery_accepted_count": battery_result.get("accepted_count"),
        "aggregate_quality_counts": aggregate_result.get("quality_counts"),
        "dashboard_quality": current.get("quality_flag"),
        "grid_power_kw": current.get("grid_power_kw"),
        "battery_power_kw": current.get("battery_power_kw"),
        "load_minus_pv_kw": current.get("load_minus_pv_kw"),
        "soc": current.get("soc"),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a smoke test against the online MPC service.")
    parser.add_argument("--base-url", default=os.getenv("MPC_ONLINE_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--plant-id", default=os.getenv("MPC_SMOKE_PLANT_ID", "smoke_factory"))
    parser.add_argument("--secret", default=os.getenv("MPC_INPUT_SIGNATURE_SECRET"))
    parser.add_argument("--timeout", type=float, default=10.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    client = HttpJsonClient(base_url=args.base_url, secret=args.secret, timeout=args.timeout)
    try:
        summary = run_smoke(client, plant_id=args.plant_id)
    except SmokeError as exc:
        print(f"SMOKE TEST FAILED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
