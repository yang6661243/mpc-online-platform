# MPC Customer Dashboard React Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a customer-facing React dashboard for the online MPC service that shows real-time eCloud telemetry and MPC-vs-factory strategy comparison without touching the existing internship platform deployment.

**Architecture:** Keep the existing FastAPI service as the only backend and database writer. Add a focused dashboard payload builder for recent telemetry windows, serve a Vite React build from the same MPC container, and keep all deployment changes isolated to the `mpc-online-platform` container, ports, image, and directories.

**Tech Stack:** FastAPI, SQLAlchemy, pytest, Vite, React, TypeScript, ECharts, Vitest, Docker multi-stage build.

---

## Safety Rule

Before any server deployment or remote command that could alter runtime state, verify the existing internship platform containers and do not touch them.

Use this command before any server deployment step:

```bash
ssh -i ~/.ssh/id_ed25519_aliyun_mpc -o IdentitiesOnly=yes -o StrictHostKeyChecking=no root@8.163.49.151 \
  'docker ps --format "table {{.Names}}\t{{.Image}}\t{{.Ports}}\t{{.Status}}"'
```

Expected: `internship-frontend` and `internship-backend` remain running. Do not stop, restart, rebuild, remove, rename, or reconfigure those containers.

## File Structure

Create:

- `microgrid_online/dashboard_data.py` — query telemetry/comparison rows and build the dashboard JSON payload.
- `tests/online/test_dashboard_data.py` — unit tests for the dashboard payload builder.
- `tests/online/test_dashboard_static.py` — tests for React static-file serving fallback.
- `web/mpc-dashboard/package.json` — frontend package scripts and dependencies.
- `web/mpc-dashboard/package-lock.json` — locked npm dependency graph after `npm install`.
- `web/mpc-dashboard/index.html` — Vite HTML shell.
- `web/mpc-dashboard/tsconfig.json` — TypeScript app config.
- `web/mpc-dashboard/tsconfig.node.json` — TypeScript config for Vite.
- `web/mpc-dashboard/vite.config.ts` — Vite/Vitest config.
- `web/mpc-dashboard/src/main.tsx` — React entrypoint.
- `web/mpc-dashboard/src/App.tsx` — dashboard app state and layout.
- `web/mpc-dashboard/src/api.ts` — dashboard API client.
- `web/mpc-dashboard/src/types.ts` — shared TypeScript response types.
- `web/mpc-dashboard/src/format.ts` — metric formatting helpers.
- `web/mpc-dashboard/src/status.ts` — status labels and severity mapping.
- `web/mpc-dashboard/src/components/MetricCard.tsx` — KPI card.
- `web/mpc-dashboard/src/components/StatusBar.tsx` — data/MPC status strip.
- `web/mpc-dashboard/src/components/PowerChart.tsx` — actual vs MPC grid power chart.
- `web/mpc-dashboard/src/components/BatteryChart.tsx` — battery power and SOC chart.
- `web/mpc-dashboard/src/components/DetailTable.tsx` — 15-minute detail table.
- `web/mpc-dashboard/src/styles.css` — dashboard visual system.
- `web/mpc-dashboard/src/format.test.ts` — Vitest tests for formatting.
- `web/mpc-dashboard/src/status.test.ts` — Vitest tests for status mapping.
- `.dockerignore` — keep local frontend dependencies and caches out of Docker context.

Modify:

- `microgrid_online/api.py` — use `dashboard_data.py`, add `window_hours`, and serve React build when present.
- `tests/online/test_api_contract.py` — add API contract coverage for windowed real telemetry series.
- `tests/online/test_dashboard_page.py` — update page/static route expectations.
- `tests/online/test_strategy_curves.py` — assert MPC curve data is merged with actual telemetry.
- `Dockerfile` — build React assets in a Node stage and copy them into the Python runtime image.

Do not modify:

- Existing internship platform code, images, containers, or ports.
- `docker-compose.online.yml` service names or default container names unless a later deployment-specific plan explicitly requires a separate override file.

---

### Task 1: Add Dashboard Payload Builder

**Files:**
- Create: `microgrid_online/dashboard_data.py`
- Create: `tests/online/test_dashboard_data.py`
- Modify: `microgrid_online/api.py`

- [ ] **Step 1: Write failing tests for actual telemetry series without MPC**

Add this file:

```python
# tests/online/test_dashboard_data.py
from microgrid_online.dashboard_data import build_dashboard_payload
from microgrid_online.database import create_sqlite_memory_session
from microgrid_online.models import Telemetry15Min
from microgrid_online.time_utils import parse_timestamp


def _add_telemetry(session):
    session.add_all(
        [
            Telemetry15Min(
                plant_id="ecloud_factory",
                start_time=parse_timestamp("2026-06-13T00:00:00+08:00"),
                end_time=parse_timestamp("2026-06-13T00:15:00+08:00"),
                grid_power_kw_avg=180.0,
                grid_power_kw_max=185.0,
                battery_power_kw_avg=2.0,
                load_minus_pv_kw_avg=182.0,
                soc_start=0.51,
                soc_end=0.50,
                grid_sample_count=15,
                battery_sample_count=15,
                quality_flag="ok",
            ),
            Telemetry15Min(
                plant_id="ecloud_factory",
                start_time=parse_timestamp("2026-06-13T00:15:00+08:00"),
                end_time=parse_timestamp("2026-06-13T00:30:00+08:00"),
                grid_power_kw_avg=210.0,
                grid_power_kw_max=216.0,
                battery_power_kw_avg=0.1,
                load_minus_pv_kw_avg=210.1,
                soc_start=0.50,
                soc_end=0.50,
                grid_sample_count=15,
                battery_sample_count=15,
                quality_flag="ok",
            ),
        ]
    )
    session.commit()


def test_build_dashboard_payload_returns_actual_series_without_mpc_result():
    session = create_sqlite_memory_session()
    _add_telemetry(session)

    payload = build_dashboard_payload(session, plant_id="ecloud_factory", window_hours=24)

    assert payload["plant_id"] == "ecloud_factory"
    assert payload["current"]["time"] == "2026-06-13T00:30:00"
    assert payload["current"]["grid_power_kw"] == 210.0
    assert payload["current"]["battery_power_kw"] == 0.1
    assert payload["current"]["soc"] == 0.5
    assert payload["current"]["quality_flag"] == "ok"
    assert payload["comparison"] is None
    assert payload["series"] == [
        {
            "time": "2026-06-13T00:15:00",
            "actual_grid_power_kw": 180.0,
            "actual_battery_power_kw": 2.0,
            "actual_soc": 0.5,
            "load_minus_pv_kw": 182.0,
            "mpc_grid_power_kw": None,
            "mpc_battery_power_kw": None,
            "mpc_soc": None,
            "buy_price": None,
            "sell_price": None,
            "quality_flag": "ok",
        },
        {
            "time": "2026-06-13T00:30:00",
            "actual_grid_power_kw": 210.0,
            "actual_battery_power_kw": 0.1,
            "actual_soc": 0.5,
            "load_minus_pv_kw": 210.1,
            "mpc_grid_power_kw": None,
            "mpc_battery_power_kw": None,
            "mpc_soc": None,
            "buy_price": None,
            "sell_price": None,
            "quality_flag": "ok",
        },
    ]
```

- [ ] **Step 2: Run test to verify RED**

Run:

```bash
python -B -m pytest -q tests/online/test_dashboard_data.py
```

Expected: FAIL with `ModuleNotFoundError: No module named 'microgrid_online.dashboard_data'`.

- [ ] **Step 3: Implement minimal dashboard payload builder**

Create:

```python
# microgrid_online/dashboard_data.py
from __future__ import annotations

from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from microgrid_online.models import StrategyComparison, StrategyCurvePoint, Telemetry15Min


def comparison_payload(comparison: StrategyComparison | None) -> dict | None:
    if comparison is None:
        return None
    return {
        "run_id": comparison.run_id,
        "actual_peak_kw": comparison.actual_peak_kw,
        "mpc_peak_kw": comparison.mpc_peak_kw,
        "peak_reduction_kw": comparison.peak_reduction_kw,
        "peak_reduction_pct": comparison.peak_reduction_pct,
        "actual_cost_yuan": comparison.actual_cost_yuan,
        "mpc_cost_yuan": comparison.mpc_cost_yuan,
        "cost_saving_yuan": comparison.cost_saving_yuan,
        "cost_saving_pct": comparison.cost_saving_pct,
    }


def _latest_telemetry(session: Session, plant_id: str) -> Telemetry15Min:
    latest = session.scalar(
        select(Telemetry15Min)
        .where(Telemetry15Min.plant_id == plant_id)
        .order_by(Telemetry15Min.end_time.desc())
        .limit(1)
    )
    if latest is None:
        raise HTTPException(status_code=404, detail="no telemetry found")
    return latest


def _telemetry_window(
    session: Session,
    *,
    plant_id: str,
    latest: Telemetry15Min,
    window_hours: int,
) -> list[Telemetry15Min]:
    start_at = latest.end_time - timedelta(hours=window_hours)
    return list(
        session.scalars(
            select(Telemetry15Min)
            .where(
                Telemetry15Min.plant_id == plant_id,
                Telemetry15Min.end_time > start_at,
                Telemetry15Min.end_time <= latest.end_time,
            )
            .order_by(Telemetry15Min.end_time)
        )
    )


def _latest_comparison(session: Session, plant_id: str) -> StrategyComparison | None:
    return session.scalar(
        select(StrategyComparison)
        .where(StrategyComparison.plant_id == plant_id)
        .order_by(StrategyComparison.created_at.desc())
        .limit(1)
    )


def _curve_points_by_time(
    session: Session,
    comparison: StrategyComparison | None,
) -> dict:
    if comparison is None:
        return {}
    points = list(
        session.scalars(
            select(StrategyCurvePoint)
            .where(StrategyCurvePoint.run_id == comparison.run_id)
            .order_by(StrategyCurvePoint.time)
        )
    )
    return {point.time: point for point in points}


def _series_payload(rows: list[Telemetry15Min], curve_by_time: dict) -> list[dict]:
    series = []
    for row in rows:
        point = curve_by_time.get(row.start_time)
        series.append(
            {
                "time": row.end_time.isoformat(),
                "actual_grid_power_kw": row.grid_power_kw_avg,
                "actual_battery_power_kw": row.battery_power_kw_avg,
                "actual_soc": row.soc_end,
                "load_minus_pv_kw": row.load_minus_pv_kw_avg,
                "mpc_grid_power_kw": None if point is None else point.mpc_grid_power_kw,
                "mpc_battery_power_kw": None if point is None else point.mpc_battery_power_kw,
                "mpc_soc": None if point is None else point.mpc_soc,
                "buy_price": None if point is None else point.buy_price,
                "sell_price": None if point is None else point.sell_price,
                "quality_flag": row.quality_flag,
            }
        )
    return series


def build_dashboard_payload(session: Session, *, plant_id: str, window_hours: int = 24) -> dict:
    if window_hours < 1 or window_hours > 168:
        raise HTTPException(status_code=422, detail="window_hours must be between 1 and 168")

    latest = _latest_telemetry(session, plant_id)
    comparison = _latest_comparison(session, plant_id)
    rows = _telemetry_window(session, plant_id=plant_id, latest=latest, window_hours=window_hours)
    curve_by_time = _curve_points_by_time(session, comparison)

    return {
        "plant_id": plant_id,
        "current": {
            "time": latest.end_time.isoformat(),
            "grid_power_kw": latest.grid_power_kw_avg,
            "battery_power_kw": latest.battery_power_kw_avg,
            "load_minus_pv_kw": latest.load_minus_pv_kw_avg,
            "soc": latest.soc_end,
            "quality_flag": latest.quality_flag,
        },
        "comparison": comparison_payload(comparison),
        "series": _series_payload(rows, curve_by_time),
    }
```

- [ ] **Step 4: Wire API to the builder**

Modify imports in `microgrid_online/api.py`:

```python
from microgrid_online.dashboard_data import build_dashboard_payload
```

Remove local helper functions `_comparison_payload` and `_series_payload` only after all references are replaced.

Replace `/api/v1/plants/{plant_id}/dashboard` with:

```python
    @app.get("/api/v1/plants/{plant_id}/dashboard")
    def dashboard(
        plant_id: str,
        window_hours: int = 24,
        session: Session = Depends(get_session),
    ):
        return build_dashboard_payload(session, plant_id=plant_id, window_hours=window_hours)
```

Update `/api/v1/mpc/runs/{run_id}` to use `comparison_payload` if `_comparison_payload` was removed:

```python
from microgrid_online.dashboard_data import build_dashboard_payload, comparison_payload
```

and:

```python
            "comparison": comparison_payload(comparison),
```

- [ ] **Step 5: Run test to verify GREEN**

Run:

```bash
python -B -m pytest -q tests/online/test_dashboard_data.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add microgrid_online/dashboard_data.py microgrid_online/api.py tests/online/test_dashboard_data.py
git commit -m "feat: add dashboard telemetry payload builder"
```

---

### Task 2: Extend Dashboard API Contract

**Files:**
- Modify: `tests/online/test_api_contract.py`
- Modify: `tests/online/test_strategy_curves.py`
- Modify: `microgrid_online/dashboard_data.py`

- [ ] **Step 1: Add API contract test for `window_hours`**

Append this test to `tests/online/test_api_contract.py`:

```python
def test_dashboard_endpoint_filters_series_by_window_hours():
    session = create_sqlite_memory_session()
    client = TestClient(create_app(session_factory=lambda: session))
    session.add_all(
        [
            Telemetry15Min(
                plant_id="aodelai",
                start_time=parse_timestamp("2026-06-12T00:00:00+08:00"),
                end_time=parse_timestamp("2026-06-12T00:15:00+08:00"),
                grid_power_kw_avg=100.0,
                grid_power_kw_max=100.0,
                battery_power_kw_avg=1.0,
                load_minus_pv_kw_avg=101.0,
                soc_start=0.5,
                soc_end=0.5,
                grid_sample_count=1,
                battery_sample_count=1,
                quality_flag="ok",
            ),
            Telemetry15Min(
                plant_id="aodelai",
                start_time=parse_timestamp("2026-06-13T00:00:00+08:00"),
                end_time=parse_timestamp("2026-06-13T00:15:00+08:00"),
                grid_power_kw_avg=200.0,
                grid_power_kw_max=200.0,
                battery_power_kw_avg=2.0,
                load_minus_pv_kw_avg=202.0,
                soc_start=0.6,
                soc_end=0.6,
                grid_sample_count=1,
                battery_sample_count=1,
                quality_flag="ok",
            ),
        ]
    )
    session.commit()

    body = client.get("/api/v1/plants/aodelai/dashboard?window_hours=1").json()

    assert [point["time"] for point in body["series"]] == ["2026-06-13T00:15:00"]
    assert body["series"][0]["actual_grid_power_kw"] == 200.0
```

Add imports at the top if missing:

```python
from microgrid_online.models import Telemetry15Min
from microgrid_online.time_utils import parse_timestamp
```

- [ ] **Step 2: Add invalid `window_hours` test**

Append:

```python
def test_dashboard_endpoint_rejects_window_hours_outside_supported_range():
    session = create_sqlite_memory_session()
    client = TestClient(create_app(session_factory=lambda: session))

    response = client.get("/api/v1/plants/aodelai/dashboard?window_hours=169")

    assert response.status_code == 422
```

- [ ] **Step 3: Run tests to verify RED or GREEN**

Run:

```bash
python -B -m pytest -q tests/online/test_api_contract.py::test_dashboard_endpoint_filters_series_by_window_hours tests/online/test_api_contract.py::test_dashboard_endpoint_rejects_window_hours_outside_supported_range
```

Expected after Task 1: first test PASS, second test PASS if `HTTPException(422)` is used. If the invalid-window test returns 404 because telemetry is checked first, move the range check before `_latest_telemetry()` in `build_dashboard_payload()`.

- [ ] **Step 4: Update strategy curve test for merged actual/MPC series**

In `tests/online/test_strategy_curves.py`, after existing series assertions add:

```python
    assert dashboard["series"][0]["time"] == "2026-06-12T10:15:00"
    assert dashboard["series"][0]["quality_flag"] == "ok"
    assert dashboard["series"][0]["actual_grid_power_kw"] == 400.0
    assert dashboard["series"][0]["mpc_grid_power_kw"] == 380.0
    assert dashboard["series"][1]["time"] == "2026-06-12T10:30:00"
    assert dashboard["series"][1]["actual_battery_power_kw"] == -10.0
    assert dashboard["series"][1]["mpc_soc"] == 0.57
```

- [ ] **Step 5: Run online backend tests**

Run:

```bash
python -B -m pytest -q tests/online
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add microgrid_online/dashboard_data.py tests/online/test_api_contract.py tests/online/test_strategy_curves.py
git commit -m "test: cover dashboard windowed series contract"
```

---

### Task 3: Serve React Build From FastAPI

**Files:**
- Create: `tests/online/test_dashboard_static.py`
- Modify: `microgrid_online/api.py`
- Modify: `tests/online/test_dashboard_page.py`

- [ ] **Step 1: Write failing static dashboard tests**

Create:

```python
# tests/online/test_dashboard_static.py
from pathlib import Path

from fastapi.testclient import TestClient

from microgrid_online.api import create_app
from microgrid_online.database import create_sqlite_memory_session


def test_dashboard_route_serves_react_index_when_dist_exists(tmp_path: Path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(
        '<!doctype html><div id="root"></div><script type="module" src="/assets/index.js"></script>',
        encoding="utf-8",
    )
    session = create_sqlite_memory_session()
    client = TestClient(create_app(session_factory=lambda: session, dashboard_dist_dir=dist))

    response = client.get("/dashboard?plant_id=ecloud_factory")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert '<div id="root"></div>' in response.text


def test_dashboard_route_keeps_python_fallback_when_dist_missing():
    session = create_sqlite_memory_session()
    client = TestClient(create_app(session_factory=lambda: session, dashboard_dist_dir=Path("/not/present")))

    response = client.get("/dashboard?plant_id=aodelai")

    assert response.status_code == 200
    assert 'id="dashboard-root"' in response.text
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
python -B -m pytest -q tests/online/test_dashboard_static.py
```

Expected: FAIL with `TypeError: create_app() got an unexpected keyword argument 'dashboard_dist_dir'`.

- [ ] **Step 3: Add static file support**

Modify imports in `microgrid_online/api.py`:

```python
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
```

Add near `PROJECT_ROOT`:

```python
DEFAULT_DASHBOARD_DIST_DIR = PROJECT_ROOT / "web" / "mpc-dashboard" / "dist"
```

Add parameter to `create_app()`:

```python
    dashboard_dist_dir: str | Path | None = None,
```

Inside `create_app()` after `app.state.run_output_dir = Path(run_output_dir)`:

```python
    app.state.dashboard_dist_dir = Path(dashboard_dist_dir or DEFAULT_DASHBOARD_DIST_DIR)
    dashboard_assets_dir = app.state.dashboard_dist_dir / "assets"
    if dashboard_assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=dashboard_assets_dir), name="dashboard-assets")
```

Add helper inside `create_app()` before route definitions:

```python
    def dashboard_response(default_plant_id: str):
        index_path = app.state.dashboard_dist_dir / "index.html"
        if index_path.exists():
            return FileResponse(index_path)
        return HTMLResponse(render_dashboard_page(default_plant_id=default_plant_id))
```

Replace `index()` and `dashboard_page()` route bodies:

```python
    @app.get("/", response_class=HTMLResponse)
    def index(plant_id: str = "ecloud_factory"):
        return dashboard_response(default_plant_id=plant_id)

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard_page(plant_id: str = "ecloud_factory"):
        return dashboard_response(default_plant_id=plant_id)
```

- [ ] **Step 4: Update existing dashboard page tests**

Modify `tests/online/test_dashboard_page.py`:

```python
def test_root_serves_customer_dashboard_page():
    session = create_sqlite_memory_session()
    client = TestClient(create_app(session_factory=lambda: session, dashboard_dist_dir="/not/present"))

    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert 'id="dashboard-root"' in response.text
    assert "MPC 策略对比看板" in response.text
    assert "/api/v1/plants/" in response.text


def test_dashboard_route_serves_same_customer_page():
    session = create_sqlite_memory_session()
    client = TestClient(create_app(session_factory=lambda: session, dashboard_dist_dir="/not/present"))

    response = client.get("/dashboard?plant_id=aodelai")

    assert response.status_code == 200
    assert "const DEFAULT_PLANT_ID = \"aodelai\"" in response.text
    assert "drawPowerChart" in response.text
```

- [ ] **Step 5: Run tests to verify GREEN**

Run:

```bash
python -B -m pytest -q tests/online/test_dashboard_static.py tests/online/test_dashboard_page.py
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add microgrid_online/api.py tests/online/test_dashboard_static.py tests/online/test_dashboard_page.py
git commit -m "feat: serve dashboard static build"
```

---

### Task 4: Scaffold React Dashboard App

**Files:**
- Create: `web/mpc-dashboard/package.json`
- Create: `web/mpc-dashboard/index.html`
- Create: `web/mpc-dashboard/tsconfig.json`
- Create: `web/mpc-dashboard/tsconfig.node.json`
- Create: `web/mpc-dashboard/vite.config.ts`
- Create: `web/mpc-dashboard/src/main.tsx`
- Create: `web/mpc-dashboard/src/App.tsx`
- Create: `web/mpc-dashboard/src/types.ts`
- Create: `web/mpc-dashboard/src/api.ts`
- Create: `web/mpc-dashboard/src/format.ts`
- Create: `web/mpc-dashboard/src/status.ts`
- Create: `web/mpc-dashboard/src/format.test.ts`
- Create: `web/mpc-dashboard/src/status.test.ts`

- [ ] **Step 1: Create frontend package files**

Create `web/mpc-dashboard/package.json`:

```json
{
  "name": "mpc-customer-dashboard",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite --host 127.0.0.1",
    "build": "tsc -b && vite build",
    "preview": "vite preview --host 127.0.0.1",
    "test": "vitest run"
  },
  "dependencies": {
    "@vitejs/plugin-react": "^4.3.4",
    "echarts": "^5.6.0",
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@types/react": "^18.3.18",
    "@types/react-dom": "^18.3.5",
    "typescript": "^5.7.3",
    "vite": "^6.0.7",
    "vitest": "^2.1.8"
  }
}
```

Create `web/mpc-dashboard/index.html`:

```html
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>MPC 策略对比看板</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

Create `web/mpc-dashboard/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["DOM", "DOM.Iterable", "ES2020"],
    "allowJs": false,
    "skipLibCheck": true,
    "esModuleInterop": true,
    "allowSyntheticDefaultImports": true,
    "strict": true,
    "forceConsistentCasingInFileNames": true,
    "module": "ESNext",
    "moduleResolution": "Node",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx"
  },
  "include": ["src"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
```

Create `web/mpc-dashboard/tsconfig.node.json`:

```json
{
  "compilerOptions": {
    "composite": true,
    "skipLibCheck": true,
    "module": "ESNext",
    "moduleResolution": "Node",
    "allowSyntheticDefaultImports": true
  },
  "include": ["vite.config.ts"]
}
```

Create `web/mpc-dashboard/vite.config.ts`:

```ts
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
  test: {
    environment: "node",
  },
});
```

- [ ] **Step 2: Create shared types**

Create `web/mpc-dashboard/src/types.ts`:

```ts
export type QualityFlag = "ok" | "missing_grid" | "missing_battery" | string;

export interface DashboardCurrent {
  time: string | null;
  grid_power_kw: number | null;
  battery_power_kw: number | null;
  load_minus_pv_kw: number | null;
  soc: number | null;
  quality_flag: QualityFlag;
}

export interface StrategyComparison {
  run_id: string;
  actual_peak_kw: number | null;
  mpc_peak_kw: number | null;
  peak_reduction_kw: number | null;
  peak_reduction_pct: number | null;
  actual_cost_yuan: number | null;
  mpc_cost_yuan: number | null;
  cost_saving_yuan: number | null;
  cost_saving_pct: number | null;
}

export interface DashboardSeriesPoint {
  time: string;
  actual_grid_power_kw: number | null;
  actual_battery_power_kw: number | null;
  actual_soc: number | null;
  load_minus_pv_kw: number | null;
  mpc_grid_power_kw: number | null;
  mpc_battery_power_kw: number | null;
  mpc_soc: number | null;
  buy_price: number | null;
  sell_price: number | null;
  quality_flag: QualityFlag;
}

export interface DashboardResponse {
  plant_id: string;
  current: DashboardCurrent;
  comparison: StrategyComparison | null;
  series: DashboardSeriesPoint[];
}
```

- [ ] **Step 3: Write frontend helper tests**

Create `web/mpc-dashboard/src/format.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { formatKw, formatPercent, formatYuan, shortTime } from "./format";

describe("metric formatting", () => {
  it("formats null values as dash", () => {
    expect(formatKw(null)).toBe("--");
    expect(formatYuan(undefined)).toBe("--");
  });

  it("formats power, money, and percentage values", () => {
    expect(formatKw(216.234)).toBe("216.2 kW");
    expect(formatYuan(12.345)).toBe("12.35 元");
    expect(formatPercent(9.345)).toBe("9.3%");
  });

  it("formats ISO timestamps for compact display", () => {
    expect(shortTime("2026-06-13T04:30:00")).toBe("06-13 04:30");
  });
});
```

Create `web/mpc-dashboard/src/status.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { dashboardStatus } from "./status";
import type { DashboardResponse } from "./types";

const base: DashboardResponse = {
  plant_id: "ecloud_factory",
  current: {
    time: "2026-06-13T04:30:00",
    grid_power_kw: 216.2,
    battery_power_kw: 0.09,
    load_minus_pv_kw: 216.29,
    soc: 0.5,
    quality_flag: "ok",
  },
  comparison: null,
  series: [],
};

describe("dashboard status", () => {
  it("shows strategy pending when telemetry exists but MPC comparison is absent", () => {
    expect(dashboardStatus(base)).toEqual({
      label: "实时数据已接入，MPC 策略待生成",
      tone: "pending",
    });
  });

  it("shows ok when telemetry and MPC comparison both exist", () => {
    expect(
      dashboardStatus({
        ...base,
        comparison: {
          run_id: "mpc_req",
          actual_peak_kw: 430,
          mpc_peak_kw: 390,
          peak_reduction_kw: 40,
          peak_reduction_pct: 9.3,
          actual_cost_yuan: 210,
          mpc_cost_yuan: 185,
          cost_saving_yuan: 25,
          cost_saving_pct: 11.9,
        },
      }),
    ).toEqual({ label: "实时数据与 MPC 策略已生成", tone: "ok" });
  });

  it("surfaces non-ok telemetry quality", () => {
    expect(
      dashboardStatus({
        ...base,
        current: { ...base.current, quality_flag: "missing_battery" },
      }),
    ).toEqual({ label: "数据质量异常：missing_battery", tone: "warning" });
  });
});
```

- [ ] **Step 4: Run tests to verify RED**

Run:

```bash
cd web/mpc-dashboard
npm install
npm test
```

Expected: FAIL because `src/format.ts` and `src/status.ts` do not exist.

- [ ] **Step 5: Implement helper modules and minimal app**

Create `web/mpc-dashboard/src/format.ts`:

```ts
export function formatNumber(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "--";
  return value.toFixed(digits);
}

export function formatKw(value: number | null | undefined): string {
  const text = formatNumber(value, 1);
  return text === "--" ? text : `${text} kW`;
}

export function formatYuan(value: number | null | undefined): string {
  const text = formatNumber(value, 2);
  return text === "--" ? text : `${text} 元`;
}

export function formatPercent(value: number | null | undefined): string {
  const text = formatNumber(value, 1);
  return text === "--" ? text : `${text}%`;
}

export function formatSoc(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "--";
  const ratio = value <= 1 ? value * 100 : value;
  return `${ratio.toFixed(1)}%`;
}

export function shortTime(value: string | null | undefined): string {
  if (!value) return "--";
  const match = value.match(/^\\d{4}-(\\d{2})-(\\d{2})T(\\d{2}:\\d{2})/);
  if (!match) return value;
  return `${match[1]}-${match[2]} ${match[3]}`;
}
```

Create `web/mpc-dashboard/src/status.ts`:

```ts
import type { DashboardResponse } from "./types";

export type StatusTone = "ok" | "pending" | "warning" | "error";

export interface DashboardStatus {
  label: string;
  tone: StatusTone;
}

export function dashboardStatus(data: DashboardResponse): DashboardStatus {
  if (data.current.quality_flag !== "ok") {
    return {
      label: `数据质量异常：${data.current.quality_flag}`,
      tone: "warning",
    };
  }
  if (!data.comparison) {
    return {
      label: "实时数据已接入，MPC 策略待生成",
      tone: "pending",
    };
  }
  return {
    label: "实时数据与 MPC 策略已生成",
    tone: "ok",
  };
}
```

Create `web/mpc-dashboard/src/api.ts`:

```ts
import type { DashboardResponse } from "./types";

export async function fetchDashboard(
  plantId: string,
  windowHours: number,
  signal?: AbortSignal,
): Promise<DashboardResponse> {
  const params = new URLSearchParams({ window_hours: String(windowHours) });
  const response = await fetch(
    `/api/v1/plants/${encodeURIComponent(plantId)}/dashboard?${params}`,
    { signal },
  );
  if (!response.ok) {
    throw new Error(`dashboard request failed: HTTP ${response.status}`);
  }
  return response.json() as Promise<DashboardResponse>;
}
```

Create `web/mpc-dashboard/src/App.tsx`:

```tsx
import { useEffect, useMemo, useState } from "react";
import { fetchDashboard } from "./api";
import { formatKw, formatSoc, shortTime } from "./format";
import { dashboardStatus } from "./status";
import type { DashboardResponse } from "./types";
import "./styles.css";

const DEFAULT_PLANT_ID = "ecloud_factory";

function plantFromQuery(): string {
  const params = new URLSearchParams(window.location.search);
  return params.get("plant_id") || DEFAULT_PLANT_ID;
}

export default function App() {
  const [plantId, setPlantId] = useState(plantFromQuery);
  const [windowHours, setWindowHours] = useState(24);
  const [data, setData] = useState<DashboardResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    fetchDashboard(plantId, windowHours, controller.signal)
      .then(setData)
      .catch((err: Error) => {
        if (err.name !== "AbortError") {
          setData(null);
          setError(err.message);
        }
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [plantId, windowHours]);

  const status = useMemo(() => (data ? dashboardStatus(data) : null), [data]);

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">虚拟电厂 MPC</p>
          <h1>MPC 策略对比看板</h1>
          <p className="subtitle">
            {data ? `${data.plant_id} · 最新数据 ${shortTime(data.current.time)}` : "等待数据"}
          </p>
        </div>
        <div className="toolbar">
          <label>
            工厂
            <input value={plantId} onChange={(event) => setPlantId(event.target.value)} />
          </label>
          <label>
            窗口
            <select value={windowHours} onChange={(event) => setWindowHours(Number(event.target.value))}>
              <option value={24}>最近24小时</option>
              <option value={48}>最近48小时</option>
            </select>
          </label>
          <button type="button" onClick={() => setWindowHours((value) => Number(value))}>
            刷新
          </button>
        </div>
      </header>

      <section className="content">
        {loading && <div className="status pending">数据加载中</div>}
        {error && <div className="status error">暂无实时数据：{error}</div>}
        {status && <div className={`status ${status.tone}`}>{status.label}</div>}

        <section className="metric-grid">
          <article className="metric-card">
            <span>当前电网功率</span>
            <strong>{formatKw(data?.current.grid_power_kw)}</strong>
          </article>
          <article className="metric-card">
            <span>当前储能功率</span>
            <strong>{formatKw(data?.current.battery_power_kw)}</strong>
          </article>
          <article className="metric-card">
            <span>当前 SOC</span>
            <strong>{formatSoc(data?.current.soc)}</strong>
          </article>
          <article className="metric-card">
            <span>数据质量</span>
            <strong>{data?.current.quality_flag || "--"}</strong>
          </article>
        </section>
      </section>
    </main>
  );
}
```

Create `web/mpc-dashboard/src/main.tsx`:

```tsx
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
```

Create `web/mpc-dashboard/src/styles.css`:

```css
:root {
  color-scheme: light;
  --bg: #eef3f8;
  --surface: #ffffff;
  --line: #d9e2ec;
  --ink: #17233c;
  --muted: #607089;
  --blue: #1f6fd1;
  --green: #17815f;
  --amber: #a85c12;
  --red: #b53933;
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
  background: var(--bg);
  color: var(--ink);
}

.app-shell {
  min-height: 100vh;
}

.topbar {
  display: flex;
  justify-content: space-between;
  gap: 24px;
  padding: 22px 28px;
  background: var(--surface);
  border-bottom: 1px solid var(--line);
}

.eyebrow,
.subtitle {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
}

h1 {
  margin: 4px 0 8px;
  font-size: 26px;
  line-height: 1.2;
  letter-spacing: 0;
}

.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

label {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--muted);
  font-size: 13px;
}

input,
select,
button {
  height: 36px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #fff;
  color: var(--ink);
  font: inherit;
  padding: 0 10px;
}

button {
  cursor: pointer;
}

.content {
  max-width: 1440px;
  margin: 0 auto;
  padding: 18px 28px 32px;
}

.status {
  min-height: 36px;
  display: flex;
  align-items: center;
  padding: 0 12px;
  border-radius: 6px;
  border: 1px solid var(--line);
  background: #fff;
  color: var(--muted);
  margin-bottom: 14px;
}

.status.ok {
  color: var(--green);
  border-color: #b8decf;
  background: #f1faf6;
}

.status.pending {
  color: var(--amber);
  border-color: #ead0aa;
  background: #fff8ef;
}

.status.warning,
.status.error {
  color: var(--red);
  border-color: #efc1bd;
  background: #fff4f3;
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(160px, 1fr));
  gap: 14px;
}

.metric-card {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 16px;
}

.metric-card span {
  display: block;
  color: var(--muted);
  font-size: 13px;
}

.metric-card strong {
  display: block;
  margin-top: 10px;
  font-size: 28px;
  line-height: 1.1;
}

@media (max-width: 900px) {
  .topbar {
    flex-direction: column;
    padding: 18px;
  }

  .content {
    padding: 16px;
  }

  .metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 560px) {
  .metric-grid {
    grid-template-columns: 1fr;
  }
}
```

- [ ] **Step 6: Run tests and build**

Run:

```bash
cd web/mpc-dashboard
npm test
npm run build
```

Expected: tests pass and Vite writes `dist/`.

- [ ] **Step 7: Commit**

```bash
git add web/mpc-dashboard
git commit -m "feat: scaffold react mpc dashboard"
```

---

### Task 5: Build Full Dashboard UI Components

**Files:**
- Create: `web/mpc-dashboard/src/components/MetricCard.tsx`
- Create: `web/mpc-dashboard/src/components/StatusBar.tsx`
- Create: `web/mpc-dashboard/src/components/PowerChart.tsx`
- Create: `web/mpc-dashboard/src/components/BatteryChart.tsx`
- Create: `web/mpc-dashboard/src/components/DetailTable.tsx`
- Modify: `web/mpc-dashboard/src/App.tsx`
- Modify: `web/mpc-dashboard/src/styles.css`

- [ ] **Step 1: Create metric and status components**

Create `web/mpc-dashboard/src/components/MetricCard.tsx`:

```tsx
interface MetricCardProps {
  label: string;
  value: string;
  sub?: string;
}

export function MetricCard({ label, value, sub }: MetricCardProps) {
  return (
    <article className="metric-card">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{sub || ""}</small>
    </article>
  );
}
```

Create `web/mpc-dashboard/src/components/StatusBar.tsx`:

```tsx
import type { StatusTone } from "../status";

interface StatusBarProps {
  label: string;
  tone: StatusTone;
}

export function StatusBar({ label, tone }: StatusBarProps) {
  return <div className={`status ${tone}`}>{label}</div>;
}
```

- [ ] **Step 2: Create chart components**

Create `web/mpc-dashboard/src/components/PowerChart.tsx`:

```tsx
import { useEffect, useRef } from "react";
import * as echarts from "echarts";
import { shortTime } from "../format";
import type { DashboardSeriesPoint } from "../types";

interface PowerChartProps {
  series: DashboardSeriesPoint[];
}

export function PowerChart({ series }: PowerChartProps) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    chart.setOption({
      color: ["#1f6fd1", "#d56a1c"],
      tooltip: { trigger: "axis" },
      legend: { top: 4, data: ["工厂当前策略", "MPC 策略"] },
      grid: { left: 54, right: 28, top: 48, bottom: 42 },
      xAxis: {
        type: "category",
        data: series.map((point) => shortTime(point.time)),
        boundaryGap: false,
      },
      yAxis: { type: "value", name: "kW", scale: true },
      dataZoom: [{ type: "inside" }, { type: "slider", height: 18, bottom: 8 }],
      series: [
        {
          name: "工厂当前策略",
          type: "line",
          showSymbol: false,
          smooth: true,
          data: series.map((point) => point.actual_grid_power_kw),
        },
        {
          name: "MPC 策略",
          type: "line",
          showSymbol: false,
          smooth: true,
          data: series.map((point) => point.mpc_grid_power_kw),
        },
      ],
    });
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.dispose();
    };
  }, [series]);

  if (!series.length) {
    return <div className="empty">暂无曲线数据</div>;
  }
  return <div ref={ref} className="chart" />;
}
```

Create `web/mpc-dashboard/src/components/BatteryChart.tsx`:

```tsx
import { useEffect, useRef } from "react";
import * as echarts from "echarts";
import { shortTime } from "../format";
import type { DashboardSeriesPoint } from "../types";

interface BatteryChartProps {
  series: DashboardSeriesPoint[];
}

function socPercent(value: number | null): number | null {
  if (value === null) return null;
  return value <= 1 ? value * 100 : value;
}

export function BatteryChart({ series }: BatteryChartProps) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    chart.setOption({
      color: ["#7445a8", "#d56a1c", "#17815f", "#38a388"],
      tooltip: { trigger: "axis" },
      legend: { top: 4, data: ["实际储能功率", "MPC 储能功率", "实际 SOC", "MPC SOC"] },
      grid: { left: 54, right: 54, top: 48, bottom: 42 },
      xAxis: {
        type: "category",
        data: series.map((point) => shortTime(point.time)),
        boundaryGap: false,
      },
      yAxis: [
        { type: "value", name: "kW", scale: true },
        { type: "value", name: "%", min: 0, max: 100 },
      ],
      dataZoom: [{ type: "inside" }, { type: "slider", height: 18, bottom: 8 }],
      series: [
        {
          name: "实际储能功率",
          type: "line",
          showSymbol: false,
          smooth: true,
          data: series.map((point) => point.actual_battery_power_kw),
        },
        {
          name: "MPC 储能功率",
          type: "line",
          showSymbol: false,
          smooth: true,
          data: series.map((point) => point.mpc_battery_power_kw),
        },
        {
          name: "实际 SOC",
          type: "line",
          showSymbol: false,
          smooth: true,
          yAxisIndex: 1,
          data: series.map((point) => socPercent(point.actual_soc)),
        },
        {
          name: "MPC SOC",
          type: "line",
          showSymbol: false,
          smooth: true,
          yAxisIndex: 1,
          data: series.map((point) => socPercent(point.mpc_soc)),
        },
      ],
    });
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.dispose();
    };
  }, [series]);

  if (!series.length) {
    return <div className="empty">暂无曲线数据</div>;
  }
  return <div ref={ref} className="chart" />;
}
```

- [ ] **Step 3: Create detail table**

Create `web/mpc-dashboard/src/components/DetailTable.tsx`:

```tsx
import { formatKw, formatSoc, shortTime } from "../format";
import type { DashboardSeriesPoint } from "../types";

interface DetailTableProps {
  series: DashboardSeriesPoint[];
}

export function DetailTable({ series }: DetailTableProps) {
  if (!series.length) {
    return <div className="empty">暂无明细数据</div>;
  }
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>时间</th>
            <th>实际电网</th>
            <th>MPC 电网</th>
            <th>实际储能</th>
            <th>MPC 储能</th>
            <th>实际 SOC</th>
            <th>MPC SOC</th>
            <th>质量</th>
          </tr>
        </thead>
        <tbody>
          {series.map((point) => (
            <tr key={point.time}>
              <td>{shortTime(point.time)}</td>
              <td>{formatKw(point.actual_grid_power_kw)}</td>
              <td>{formatKw(point.mpc_grid_power_kw)}</td>
              <td>{formatKw(point.actual_battery_power_kw)}</td>
              <td>{formatKw(point.mpc_battery_power_kw)}</td>
              <td>{formatSoc(point.actual_soc)}</td>
              <td>{formatSoc(point.mpc_soc)}</td>
              <td>{point.quality_flag}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 4: Replace `App.tsx` with full layout**

Use this content:

```tsx
import { useEffect, useMemo, useState } from "react";
import { fetchDashboard } from "./api";
import { BatteryChart } from "./components/BatteryChart";
import { DetailTable } from "./components/DetailTable";
import { MetricCard } from "./components/MetricCard";
import { PowerChart } from "./components/PowerChart";
import { StatusBar } from "./components/StatusBar";
import { formatKw, formatPercent, formatSoc, formatYuan, shortTime } from "./format";
import { dashboardStatus } from "./status";
import type { DashboardResponse } from "./types";
import "./styles.css";

const DEFAULT_PLANT_ID = "ecloud_factory";

function plantFromQuery(): string {
  const params = new URLSearchParams(window.location.search);
  return params.get("plant_id") || DEFAULT_PLANT_ID;
}

export default function App() {
  const [plantId, setPlantId] = useState(plantFromQuery);
  const [windowHours, setWindowHours] = useState(24);
  const [refreshKey, setRefreshKey] = useState(0);
  const [data, setData] = useState<DashboardResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    fetchDashboard(plantId, windowHours, controller.signal)
      .then(setData)
      .catch((err: Error) => {
        if (err.name !== "AbortError") {
          setData(null);
          setError(err.message);
        }
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [plantId, windowHours, refreshKey]);

  const status = useMemo(() => (data ? dashboardStatus(data) : null), [data]);
  const comparison = data?.comparison;

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">虚拟电厂 MPC</p>
          <h1>MPC 策略对比看板</h1>
          <p className="subtitle">
            {data ? `${data.plant_id} · 最新数据 ${shortTime(data.current.time)}` : "等待数据"}
          </p>
        </div>
        <div className="toolbar">
          <label>
            工厂
            <input value={plantId} onChange={(event) => setPlantId(event.target.value)} />
          </label>
          <label>
            窗口
            <select value={windowHours} onChange={(event) => setWindowHours(Number(event.target.value))}>
              <option value={24}>最近24小时</option>
              <option value={48}>最近48小时</option>
            </select>
          </label>
          <button type="button" onClick={() => setRefreshKey((value) => value + 1)}>
            刷新
          </button>
        </div>
      </header>

      <section className="content">
        {loading && <StatusBar label="数据加载中" tone="pending" />}
        {error && <StatusBar label={`暂无实时数据：${error}`} tone="error" />}
        {status && <StatusBar label={status.label} tone={status.tone} />}

        <section className="metric-grid">
          <MetricCard label="当前电网功率" value={formatKw(data?.current.grid_power_kw)} sub="防逆流表聚合值" />
          <MetricCard label="当前储能功率" value={formatKw(data?.current.battery_power_kw)} sub="储能计量表聚合值" />
          <MetricCard label="当前 SOC" value={formatSoc(data?.current.soc)} sub="BMS 系统 SOC" />
          <MetricCard label="数据质量" value={data?.current.quality_flag || "--"} sub="15分钟聚合窗口" />
          <MetricCard label="工厂最大需量" value={formatKw(comparison?.actual_peak_kw)} sub="当前策略" />
          <MetricCard label="MPC 最大需量" value={formatKw(comparison?.mpc_peak_kw)} sub="优化策略" />
          <MetricCard
            label="削峰量"
            value={formatKw(comparison?.peak_reduction_kw)}
            sub={formatPercent(comparison?.peak_reduction_pct)}
          />
          <MetricCard
            label="预计节省"
            value={formatYuan(comparison?.cost_saving_yuan)}
            sub={formatPercent(comparison?.cost_saving_pct)}
          />
        </section>

        <section className="panel-grid">
          <article className="panel">
            <div className="panel-head">
              <h2>电网功率对比</h2>
              <p>工厂当前策略与 MPC 策略的最大需量对比</p>
            </div>
            <PowerChart series={data?.series || []} />
          </article>

          <article className="panel">
            <div className="panel-head">
              <h2>储能功率与 SOC</h2>
              <p>观察储能动作是否连续、SOC 是否在安全范围内</p>
            </div>
            <BatteryChart series={data?.series || []} />
          </article>

          <article className="panel">
            <div className="panel-head">
              <h2>15分钟策略明细</h2>
              <p>实际值与 MPC 输出逐点对照</p>
            </div>
            <DetailTable series={data?.series || []} />
          </article>
        </section>
      </section>
    </main>
  );
}
```

- [ ] **Step 5: Extend CSS for panels, charts, and table**

Append to `web/mpc-dashboard/src/styles.css`:

```css
.metric-card small {
  display: block;
  min-height: 16px;
  margin-top: 8px;
  color: var(--muted);
  font-size: 12px;
}

.panel-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 16px;
  margin-top: 16px;
}

.panel {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 16px;
}

.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 12px;
}

.panel h2 {
  margin: 0;
  font-size: 18px;
  line-height: 1.25;
}

.panel p {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
}

.chart {
  width: 100%;
  height: 360px;
}

.empty {
  min-height: 220px;
  display: grid;
  place-items: center;
  color: var(--muted);
  background: #f8fbfd;
  border: 1px dashed #cdd9e5;
  border-radius: 6px;
}

.table-wrap {
  overflow: auto;
  border: 1px solid #e5ecf3;
  border-radius: 6px;
}

table {
  width: 100%;
  min-width: 900px;
  border-collapse: collapse;
  font-size: 13px;
}

th,
td {
  padding: 10px 12px;
  border-bottom: 1px solid #edf2f7;
  text-align: right;
  white-space: nowrap;
}

th:first-child,
td:first-child {
  text-align: left;
}

th {
  color: var(--muted);
  background: #f7f9fc;
  font-weight: 600;
}

@media (max-width: 900px) {
  .panel-head {
    flex-direction: column;
  }
}
```

- [ ] **Step 6: Run frontend tests and build**

Run:

```bash
cd web/mpc-dashboard
npm test
npm run build
```

Expected: tests pass and build completes.

- [ ] **Step 7: Commit**

```bash
git add web/mpc-dashboard
git commit -m "feat: build mpc dashboard ui"
```

---

### Task 6: Docker Build Integration

**Files:**
- Create: `.dockerignore`
- Modify: `Dockerfile`

- [ ] **Step 1: Add Docker context exclusions**

Create `.dockerignore`:

```dockerignore
.git
.pytest_cache
.mypy_cache
.ruff_cache
.venv
.venv*
__pycache__
*.pyc
data
outputs
logs
tmp
temp
web/mpc-dashboard/node_modules
web/mpc-dashboard/dist
```

- [ ] **Step 2: Modify Dockerfile for frontend build**

Replace the top of `Dockerfile` with:

```dockerfile
ARG PYTHON_BASE_IMAGE=python:3.12-slim
ARG NODE_BASE_IMAGE=node:20-bookworm-slim

FROM ${NODE_BASE_IMAGE} AS dashboard-build

WORKDIR /app/web/mpc-dashboard
COPY web/mpc-dashboard/package*.json ./
RUN npm ci
COPY web/mpc-dashboard/ ./
RUN npm run build

FROM ${PYTHON_BASE_IMAGE}
```

Keep the existing Python runtime body. After `COPY . /app`, add:

```dockerfile
COPY --from=dashboard-build /app/web/mpc-dashboard/dist /app/web/mpc-dashboard/dist
```

The resulting Dockerfile must keep:

```dockerfile
CMD ["uvicorn", "microgrid_online.api:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 3: Run local Docker build**

Run:

```bash
docker build -t mpc-online-platform:dashboard-local .
```

Expected: build succeeds.

- [ ] **Step 4: Run isolated local container**

Use a local-only test container name and port:

```bash
docker rm -f mpc-dashboard-local-test 2>/dev/null || true
docker run -d \
  --name mpc-dashboard-local-test \
  -p 18080:8000 \
  -e MPC_DATABASE_URL=sqlite:////app/data/mpc_online.db \
  mpc-online-platform:dashboard-local
```

Expected: new container starts. This does not touch server containers.

- [ ] **Step 5: Verify local container routes**

Run:

```bash
curl -fsS http://127.0.0.1:18080/healthz
curl -fsS http://127.0.0.1:18080/dashboard | head -20
```

Expected:

- `/healthz` returns `{"status":"ok","service":"online-mpc"}`.
- `/dashboard` contains `<div id="root"></div>`.

- [ ] **Step 6: Clean local test container**

Run:

```bash
docker rm -f mpc-dashboard-local-test
```

Expected: only the local test container is removed.

- [ ] **Step 7: Commit**

```bash
git add .dockerignore Dockerfile
git commit -m "build: bundle react dashboard in docker image"
```

---

### Task 7: Local End-to-End Verification

**Files:**
- No production files required.

- [ ] **Step 1: Run backend tests**

Run:

```bash
python -B -m pytest -q tests/online
```

Expected: all tests pass.

- [ ] **Step 2: Run frontend tests and build**

Run:

```bash
cd web/mpc-dashboard
npm test
npm run build
```

Expected: tests pass and `dist/` is generated.

- [ ] **Step 3: Start local FastAPI server**

Run:

```bash
python -m uvicorn microgrid_online.api:app --host 127.0.0.1 --port 8000
```

Expected: server starts on `http://127.0.0.1:8000`.

- [ ] **Step 4: Open dashboard in browser**

Use the Browser plugin to open:

```text
http://127.0.0.1:8000/dashboard?plant_id=ecloud_factory
```

Verify:

- The React page loads.
- Empty or no-data state is readable.
- No text overlaps at desktop width.
- The page does not show the old inline SVG dashboard if `web/mpc-dashboard/dist/index.html` exists.

- [ ] **Step 5: Verify API against current deployed server data**

Run:

```bash
NO_PROXY='*' no_proxy='*' curl -fsS 'http://8.163.49.151:18000/api/v1/plants/ecloud_factory/dashboard?window_hours=24' | python -m json.tool | sed -n '1,120p'
```

Expected: JSON includes `current` and `series`. If deployed server has not yet been upgraded, `series` may still reflect the old deployed behavior; local API tests remain the source of truth before deployment.

- [ ] **Step 6: Commit verification notes only if files changed**

If no files changed, do not commit. If a doc is updated with verified commands, commit:

```bash
git add docs/superpowers/plans/2026-06-13-mpc-customer-dashboard-react-plan.md
git commit -m "docs: record dashboard verification"
```

---

### Task 8: Push Code and Prepare Safe Server Deployment

**Files:**
- No required source changes.

- [ ] **Step 1: Verify git status**

Run:

```bash
git status -sb
```

Expected: no unstaged source changes except known unrelated files under `docs/superpowers/plans/2026-06-12-ecloud-mpc-realtime-ingest-plan.md` and `docs/superpowers/specs/2026-06-12-ecloud-mpc-realtime-ingest-design.md` if they still exist.

- [ ] **Step 2: Push to GitHub**

Run:

```bash
git push origin main
```

Expected: push succeeds.

- [ ] **Step 3: Check server containers before any deployment**

Run:

```bash
ssh -i ~/.ssh/id_ed25519_aliyun_mpc -o IdentitiesOnly=yes -o StrictHostKeyChecking=no root@8.163.49.151 \
  'docker ps --format "table {{.Names}}\t{{.Image}}\t{{.Ports}}\t{{.Status}}"'
```

Expected:

- `internship-frontend` is running.
- `internship-backend` is running.
- `mpc-online-platform` is running separately.

If any command would stop, rebuild, or alter `internship-frontend` or `internship-backend`, stop and ask the user.

- [ ] **Step 4: Ask for deployment confirmation**

Before running any remote pull/build/restart command, send this exact confirmation request:

```text
准备只更新 mpc-online-platform 容器，不操作 internship-frontend / internship-backend。请确认是否现在部署到服务器。
```

Do not deploy until the user confirms.

---

## Self-Review Checklist

- Spec coverage:
  - React/Vite/ECharts frontend: Tasks 4 and 5.
  - FastAPI static serving: Task 3.
  - Dashboard API recent 24/48 hour series: Tasks 1 and 2.
  - No MPC result empty state: Tasks 1, 4, and 5.
  - Docker build integration: Task 6.
  - No impact to internship platform: Safety Rule and Task 8.
- Placeholder scan: no unresolved placeholders or unspecified implementation steps.
- Type consistency:
  - Python payload keys match TypeScript interfaces.
  - `quality_flag` is present in backend series and frontend detail table.
  - `window_hours` is used consistently in API, frontend, and tests.
