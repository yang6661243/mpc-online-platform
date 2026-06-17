# 展示曲线数据处理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增一个只服务前端展示的 1 分钟曲线数据层，让 eCloud 插件采集来的不同步、不等步长原始数据能稳定画出实时曲线，同时不改变 15 分钟聚合和 MPC 输入数据。

**Architecture:** 后端新增 `microgrid_online/display_series.py`，从 `raw_grid_meter` 和 `raw_battery` 读取最近原始数据，按分钟分桶，短缺口使用本地二次项插值，长缺口返回空值和质量标记。FastAPI 新增只读接口 `/api/v1/plants/{plant_id}/display-series`；前端优先用该接口驱动主曲线，指标卡、收益/需量、MPC 对比仍使用现有 `/dashboard` 的 15 分钟数据。

**Tech Stack:** Python 3, FastAPI, SQLAlchemy, SQLite, pytest, React, TypeScript, Vitest, ECharts。

---

## 关键原则

- 插值只用于前端展示，不入库，不写入 `telemetry_15min`，不进入 MPC。
- 原始事实表 `raw_grid_meter`、`raw_battery` 不改结构、不改历史数据。
- `telemetry_15min` 继续作为指标卡、MPC 准备状态、MPC 输入的稳定数据层。
- 长时间中断不补线，返回 `null` 并标记 `gap`。
- 短缺口默认不超过 5 分钟才允许二次项插值。
- 部署和验证只操作独立 MPC 服务，不能影响现有大学生实习平台容器、端口、挂载、数据库、反向代理。

## 文件结构

- Create: `microgrid_online/display_series.py`
  - 职责：构建展示层 1 分钟时间轴、按分钟分桶原始样本、二次项插值、质量标记、响应 payload。
- Create: `tests/online/test_display_series.py`
  - 职责：覆盖插值、长缺口、SOC 限幅、按分钟分桶、派生净负荷。
- Modify: `microgrid_online/api.py`
  - 职责：注册 `/api/v1/plants/{plant_id}/display-series` 只读接口。
- Modify: `tests/online/test_api_contract.py`
  - 职责：补充 display-series API 合同测试。
- Modify: `web/mpc-dashboard/src/types.ts`
  - 职责：新增展示层响应类型和字段质量类型。
- Modify: `web/mpc-dashboard/src/api.ts`
  - 职责：新增 display-series URL 构造和 fetch 函数。
- Modify: `web/mpc-dashboard/src/api.test.ts`
  - 职责：覆盖 display-series URL。
- Create: `web/mpc-dashboard/src/displaySeries.ts`
  - 职责：把 display-series 响应转换成现有 `DashboardSeriesPoint[]`，减少图表组件改动。
- Create: `web/mpc-dashboard/src/displaySeries.test.ts`
  - 职责：覆盖转换、质量标记合并、dashboard fallback 数据形状。
- Modify: `web/mpc-dashboard/src/chartOptions.ts`
  - 职责：让主曲线 tooltip 能标识真实值和插值值。
- Modify: `web/mpc-dashboard/src/chartOptions.test.ts`
  - 职责：覆盖 tooltip formatter 或 quality metadata。
- Modify: `web/mpc-dashboard/src/App.tsx`
  - 职责：并行请求 dashboard 和 display-series；主曲线优先使用 display-series，失败时回退 dashboard series。

---

### Task 1: 后端展示层核心算法

**Files:**
- Create: `microgrid_online/display_series.py`
- Create: `tests/online/test_display_series.py`

- [ ] **Step 1: 写失败测试，覆盖短缺口二次项插值**

Create `tests/online/test_display_series.py` with this initial content:

```python
from microgrid_online.database import create_sqlite_memory_session
from microgrid_online.display_series import build_display_series_payload
from microgrid_online.models import RawBattery, RawGridMeter
from microgrid_online.time_utils import parse_timestamp


def _add_grid(session, times_and_values):
    for time_value, grid_kw in times_and_values:
        session.add(
            RawGridMeter(
                plant_id="hehong_huajin",
                time=parse_timestamp(time_value),
                grid_power_kw=grid_kw,
            )
        )


def _add_battery(session, times_power_soc):
    for time_value, battery_kw, soc in times_power_soc:
        session.add(
            RawBattery(
                plant_id="hehong_huajin",
                time=parse_timestamp(time_value),
                battery_power_kw=battery_kw,
                soc=soc,
            )
        )


def test_display_series_quadratic_interpolates_short_gap_for_display_only():
    session = create_sqlite_memory_session()
    _add_grid(
        session,
        [
            ("2026-06-16T10:00:10+08:00", 100.0),
            ("2026-06-16T10:02:20+08:00", 140.0),
            ("2026-06-16T10:04:30+08:00", 260.0),
        ],
    )
    _add_battery(
        session,
        [
            ("2026-06-16T10:00:05+08:00", 10.0, 0.50),
            ("2026-06-16T10:02:05+08:00", 12.0, 0.52),
            ("2026-06-16T10:04:05+08:00", 14.0, 0.54),
        ],
    )
    session.commit()

    payload = build_display_series_payload(
        session,
        plant_id="hehong_huajin",
        reference_time=parse_timestamp("2026-06-16T10:04:59+08:00"),
        window_hours=2,
        max_gap_minutes=5,
    )

    by_time = {point["time"]: point for point in payload["series"]}
    interpolated = by_time["2026-06-16T02:01:00"]

    assert payload["plant_id"] == "hehong_huajin"
    assert payload["display_only"] is True
    assert interpolated["grid_power_kw"] is not None
    assert interpolated["quality"]["grid_power_kw"] == "interpolated_quadratic"
    assert interpolated["battery_power_kw"] is not None
    assert interpolated["quality"]["battery_power_kw"] == "interpolated_quadratic"
    assert interpolated["soc"] is not None
    assert interpolated["quality"]["soc"] == "interpolated_quadratic"
    assert interpolated["load_minus_pv_kw"] == round(
        interpolated["grid_power_kw"] + interpolated["battery_power_kw"],
        6,
    )
    assert interpolated["quality"]["load_minus_pv_kw"] == "derived"
```

- [ ] **Step 2: 运行测试，确认因为模块不存在而失败**

Run:

```bash
pytest tests/online/test_display_series.py::test_display_series_quadratic_interpolates_short_gap_for_display_only -q
```

Expected:

```text
ModuleNotFoundError: No module named 'microgrid_online.display_series'
```

- [ ] **Step 3: 创建展示层模块的最小实现**

Create `microgrid_online/display_series.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from microgrid_online.models import RawBattery, RawGridMeter


DisplayQuality = Literal["observed", "interpolated_quadratic", "gap", "derived"]


@dataclass(frozen=True)
class Sample:
    minute: datetime
    value: float


def _floor_minute(value: datetime) -> datetime:
    return value.replace(second=0, microsecond=0)


def _timeline(start: datetime, end: datetime) -> list[datetime]:
    current = _floor_minute(start)
    final = _floor_minute(end)
    minutes: list[datetime] = []
    while current <= final:
        minutes.append(current)
        current += timedelta(minutes=1)
    return minutes


def _bucket_samples(rows: list[tuple[datetime, float]]) -> dict[datetime, Sample]:
    buckets: dict[datetime, tuple[datetime, float]] = {}
    for raw_time, value in rows:
        minute = _floor_minute(raw_time)
        previous = buckets.get(minute)
        if previous is None or raw_time >= previous[0]:
            buckets[minute] = (raw_time, float(value))
    return {minute: Sample(minute=minute, value=value) for minute, (_raw_time, value) in buckets.items()}


def _query_grid_samples(session: Session, plant_id: str, start: datetime, end: datetime) -> dict[datetime, Sample]:
    rows = session.execute(
        select(RawGridMeter.time, RawGridMeter.grid_power_kw)
        .where(
            RawGridMeter.plant_id == plant_id,
            RawGridMeter.time >= start,
            RawGridMeter.time <= end,
        )
        .order_by(RawGridMeter.time)
    ).all()
    return _bucket_samples([(time_value, value) for time_value, value in rows])


def _query_battery_samples(
    session: Session,
    plant_id: str,
    start: datetime,
    end: datetime,
    field_name: str,
) -> dict[datetime, Sample]:
    column = RawBattery.battery_power_kw if field_name == "battery_power_kw" else RawBattery.soc
    rows = session.execute(
        select(RawBattery.time, column)
        .where(
            RawBattery.plant_id == plant_id,
            RawBattery.time >= start,
            RawBattery.time <= end,
        )
        .order_by(RawBattery.time)
    ).all()
    return _bucket_samples([(time_value, value) for time_value, value in rows])


def _quadratic_value(samples: list[Sample], target: datetime) -> float:
    points = [((sample.minute - target).total_seconds() / 60.0, sample.value) for sample in samples]
    result = 0.0
    for index, (x_i, y_i) in enumerate(points):
        basis = 1.0
        for other_index, (x_j, _y_j) in enumerate(points):
            if other_index == index:
                continue
            basis *= (0.0 - x_j) / (x_i - x_j)
        result += y_i * basis
    return result


def _clamp_power(value: float, samples: list[Sample]) -> float:
    observed = [sample.value for sample in samples]
    low = min(observed)
    high = max(observed)
    span = max(high - low, abs(high) * 0.05, 1.0)
    return min(max(value, low - span * 0.10), high + span * 0.10)


def _value_for_minute(
    samples_by_minute: dict[datetime, Sample],
    minute: datetime,
    *,
    max_gap_minutes: int,
    clamp: Literal["power", "soc"],
) -> tuple[float | None, DisplayQuality]:
    observed = samples_by_minute.get(minute)
    if observed is not None:
        return observed.value, "observed"

    samples = sorted(samples_by_minute.values(), key=lambda sample: sample.minute)
    before = [sample for sample in samples if sample.minute < minute]
    after = [sample for sample in samples if sample.minute > minute]
    if not before or not after:
        return None, "gap"

    nearest_before = before[-1]
    nearest_after = after[0]
    gap_minutes = (nearest_after.minute - nearest_before.minute).total_seconds() / 60.0
    if gap_minutes > max_gap_minutes:
        return None, "gap"

    nearby = sorted(samples, key=lambda sample: abs((sample.minute - minute).total_seconds()))[:3]
    if len(nearby) < 3:
        return None, "gap"
    nearby = sorted(nearby, key=lambda sample: sample.minute)
    if len({sample.minute for sample in nearby}) < 3:
        return None, "gap"

    value = _quadratic_value(nearby, minute)
    if clamp == "soc":
        value = min(max(value, 0.0), 1.0)
    else:
        value = _clamp_power(value, nearby)
    return round(value, 6), "interpolated_quadratic"


def build_display_series_payload(
    session: Session,
    *,
    plant_id: str,
    reference_time: datetime | None = None,
    window_hours: int = 2,
    max_gap_minutes: int = 5,
) -> dict:
    if window_hours not in {2, 6, 24}:
        raise ValueError("window_hours must be one of 2, 6, or 24")

    latest_grid = session.scalar(
        select(RawGridMeter.time)
        .where(RawGridMeter.plant_id == plant_id)
        .order_by(RawGridMeter.time.desc())
        .limit(1)
    )
    latest_battery = session.scalar(
        select(RawBattery.time)
        .where(RawBattery.plant_id == plant_id)
        .order_by(RawBattery.time.desc())
        .limit(1)
    )
    if reference_time is None:
        latest_times = [time_value for time_value in [latest_grid, latest_battery] if time_value is not None]
        if not latest_times:
            return {
                "plant_id": plant_id,
                "window_hours": window_hours,
                "step_minutes": 1,
                "display_only": True,
                "series": [],
            }
        reference_time = max(latest_times)

    end = _floor_minute(reference_time)
    start = end - timedelta(hours=window_hours)
    query_start = start - timedelta(minutes=max_gap_minutes)
    query_end = end + timedelta(minutes=max_gap_minutes)

    grid_samples = _query_grid_samples(session, plant_id, query_start, query_end)
    battery_samples = _query_battery_samples(session, plant_id, query_start, query_end, "battery_power_kw")
    soc_samples = _query_battery_samples(session, plant_id, query_start, query_end, "soc")

    series = []
    for minute in _timeline(start, end):
        grid_value, grid_quality = _value_for_minute(
            grid_samples,
            minute,
            max_gap_minutes=max_gap_minutes,
            clamp="power",
        )
        battery_value, battery_quality = _value_for_minute(
            battery_samples,
            minute,
            max_gap_minutes=max_gap_minutes,
            clamp="power",
        )
        soc_value, soc_quality = _value_for_minute(
            soc_samples,
            minute,
            max_gap_minutes=max_gap_minutes,
            clamp="soc",
        )
        if grid_value is not None and battery_value is not None:
            load_minus_pv = round(grid_value + battery_value, 6)
            load_quality: DisplayQuality = "derived"
        else:
            load_minus_pv = None
            load_quality = "gap"
        series.append(
            {
                "time": minute.isoformat(),
                "grid_power_kw": grid_value,
                "battery_power_kw": battery_value,
                "soc": soc_value,
                "load_minus_pv_kw": load_minus_pv,
                "quality": {
                    "grid_power_kw": grid_quality,
                    "battery_power_kw": battery_quality,
                    "soc": soc_quality,
                    "load_minus_pv_kw": load_quality,
                },
                "display_only": True,
            }
        )

    return {
        "plant_id": plant_id,
        "window_hours": window_hours,
        "step_minutes": 1,
        "display_only": True,
        "series": series,
    }
```

- [ ] **Step 4: 运行短缺口插值测试**

Run:

```bash
pytest tests/online/test_display_series.py::test_display_series_quadratic_interpolates_short_gap_for_display_only -q
```

Expected:

```text
1 passed
```

- [ ] **Step 5: 增加长缺口、SOC 限幅、同一分钟取最新样本测试**

Append to `tests/online/test_display_series.py`:

```python
def test_display_series_keeps_long_gap_null():
    session = create_sqlite_memory_session()
    _add_grid(
        session,
        [
            ("2026-06-16T10:00:00+08:00", 100.0),
            ("2026-06-16T10:10:00+08:00", 200.0),
            ("2026-06-16T10:20:00+08:00", 300.0),
        ],
    )
    session.commit()

    payload = build_display_series_payload(
        session,
        plant_id="hehong_huajin",
        reference_time=parse_timestamp("2026-06-16T10:20:00+08:00"),
        window_hours=2,
        max_gap_minutes=5,
    )

    by_time = {point["time"]: point for point in payload["series"]}
    gap_point = by_time["2026-06-16T02:05:00"]

    assert gap_point["grid_power_kw"] is None
    assert gap_point["quality"]["grid_power_kw"] == "gap"


def test_display_series_clamps_soc_interpolation_to_ratio_bounds():
    session = create_sqlite_memory_session()
    _add_battery(
        session,
        [
            ("2026-06-16T10:00:00+08:00", 0.0, 0.95),
            ("2026-06-16T10:02:00+08:00", 0.0, 1.20),
            ("2026-06-16T10:04:00+08:00", 0.0, 1.10),
        ],
    )
    session.commit()

    payload = build_display_series_payload(
        session,
        plant_id="hehong_huajin",
        reference_time=parse_timestamp("2026-06-16T10:04:00+08:00"),
        window_hours=2,
        max_gap_minutes=5,
    )

    by_time = {point["time"]: point for point in payload["series"]}
    point = by_time["2026-06-16T02:01:00"]

    assert point["soc"] == 1.0
    assert point["quality"]["soc"] == "interpolated_quadratic"


def test_display_series_buckets_raw_samples_by_minute_and_keeps_latest_sample():
    session = create_sqlite_memory_session()
    _add_grid(
        session,
        [
            ("2026-06-16T10:00:05+08:00", 100.0),
            ("2026-06-16T10:00:55+08:00", 123.0),
            ("2026-06-16T10:01:05+08:00", 140.0),
            ("2026-06-16T10:02:05+08:00", 160.0),
        ],
    )
    session.commit()

    payload = build_display_series_payload(
        session,
        plant_id="hehong_huajin",
        reference_time=parse_timestamp("2026-06-16T10:02:30+08:00"),
        window_hours=2,
    )

    by_time = {point["time"]: point for point in payload["series"]}
    point = by_time["2026-06-16T02:00:00"]

    assert point["grid_power_kw"] == 123.0
    assert point["quality"]["grid_power_kw"] == "observed"
```

- [ ] **Step 6: 运行展示层完整测试**

Run:

```bash
pytest tests/online/test_display_series.py -q
```

Expected:

```text
4 passed
```

- [ ] **Step 7: 再次运行展示层测试**

Run:

```bash
pytest tests/online/test_display_series.py -q
```

Expected:

```text
4 passed
```

- [ ] **Step 8: 提交后端算法**

Run:

```bash
git add microgrid_online/display_series.py tests/online/test_display_series.py
git commit -m "feat: add display-only series interpolation"
```

Expected:

```text
[codex/mpc-customer-dashboard-react ...] feat: add display-only series interpolation
```

---

### Task 2: FastAPI 新增只读 display-series 接口

**Files:**
- Modify: `microgrid_online/api.py`
- Modify: `tests/online/test_api_contract.py`

- [ ] **Step 1: 写失败的 API 合同测试**

Append to `tests/online/test_api_contract.py`:

```python
def test_display_series_endpoint_returns_display_only_raw_series():
    session = create_sqlite_memory_session()
    client = TestClient(create_app(session_factory=lambda: session))
    client.post(
        "/api/v1/mpc/input-data",
        json={
            "request_id": "req_grid_display",
            "plant_id": "aodelai",
            "data_type": "grid_meter",
            "generated_at": "2026-06-12T10:15:00+08:00",
            "records": [
                {"time": "2026-06-12T10:00:00+08:00", "grid_power_kw": 400.0},
                {"time": "2026-06-12T10:02:00+08:00", "grid_power_kw": 440.0},
                {"time": "2026-06-12T10:04:00+08:00", "grid_power_kw": 520.0},
            ],
        },
    )
    client.post(
        "/api/v1/mpc/input-data",
        json={
            "request_id": "req_battery_display",
            "plant_id": "aodelai",
            "data_type": "battery",
            "generated_at": "2026-06-12T10:15:00+08:00",
            "records": [
                {"time": "2026-06-12T10:00:00+08:00", "battery_power_kw": 20.0, "soc": 0.60},
                {"time": "2026-06-12T10:02:00+08:00", "battery_power_kw": 30.0, "soc": 0.58},
                {"time": "2026-06-12T10:04:00+08:00", "battery_power_kw": 40.0, "soc": 0.56},
            ],
        },
    )

    response = client.get(
        "/api/v1/plants/aodelai/display-series",
        params={"window_hours": 2, "reference_time": "2026-06-12T10:04:00+08:00"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["plant_id"] == "aodelai"
    assert body["display_only"] is True
    assert body["step_minutes"] == 1
    assert body["series"][-1]["time"] == "2026-06-12T02:04:00"
    assert body["series"][-1]["grid_power_kw"] == 520.0
    assert body["series"][-1]["quality"]["grid_power_kw"] == "observed"


def test_display_series_endpoint_rejects_unsupported_window():
    session = create_sqlite_memory_session()
    client = TestClient(create_app(session_factory=lambda: session))

    response = client.get("/api/v1/plants/aodelai/display-series", params={"window_hours": 3})

    assert response.status_code == 400
    assert response.json()["detail"] == "window_hours must be one of 2, 6, or 24"
```

- [ ] **Step 2: 运行 API 测试，确认接口未注册**

Run:

```bash
pytest tests/online/test_api_contract.py::test_display_series_endpoint_returns_display_only_raw_series -q
```

Expected:

```text
assert 404 == 200
```

- [ ] **Step 3: 在 API 中注册接口**

Modify `microgrid_online/api.py`.

Add import near existing imports:

```python
from microgrid_online.display_series import build_display_series_payload
from microgrid_online.time_utils import parse_timestamp
```

Add route before `data_health` route:

```python
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
```

- [ ] **Step 4: 运行 API 合同测试**

Run:

```bash
pytest tests/online/test_api_contract.py::test_display_series_endpoint_returns_display_only_raw_series tests/online/test_api_contract.py::test_display_series_endpoint_rejects_unsupported_window -q
```

Expected:

```text
2 passed
```

- [ ] **Step 5: 运行在线后端测试集**

Run:

```bash
pytest tests/online -q
```

Expected:

```text
全部通过，末尾显示 passed
```

- [ ] **Step 6: 提交 API 接口**

Run:

```bash
git add microgrid_online/api.py tests/online/test_api_contract.py
git commit -m "feat: expose display series endpoint"
```

Expected:

```text
[codex/mpc-customer-dashboard-react ...] feat: expose display series endpoint
```

---

### Task 3: 前端 API 类型和数据转换层

**Files:**
- Modify: `web/mpc-dashboard/src/types.ts`
- Modify: `web/mpc-dashboard/src/api.ts`
- Modify: `web/mpc-dashboard/src/api.test.ts`
- Create: `web/mpc-dashboard/src/displaySeries.ts`
- Create: `web/mpc-dashboard/src/displaySeries.test.ts`

- [ ] **Step 1: 写失败的前端 API URL 测试**

Modify `web/mpc-dashboard/src/api.test.ts`.

Change import:

```ts
import { buildDashboardUrl, buildDisplaySeriesUrl, buildRunMpcRequestUrl, toApiTime } from "./api";
```

Append test:

```ts
  test("builds a display series URL for raw realtime chart data", () => {
    const url = buildDisplaySeriesUrl("hehong_huajin", {
      windowHours: 2,
      referenceTime: "2026-06-16T10:04:00",
    });

    expect(url).toBe(
      "/api/v1/plants/hehong_huajin/display-series?window_hours=2&reference_time=2026-06-16T10%3A04%3A00",
    );
  });
```

- [ ] **Step 2: 运行前端 API 测试，确认缺少函数**

Run:

```bash
cd web/mpc-dashboard && npm test -- src/api.test.ts --run
```

Expected:

```text
buildDisplaySeriesUrl is not exported
```

- [ ] **Step 3: 增加展示层类型**

Append to `web/mpc-dashboard/src/types.ts`:

```ts
export type DisplayQuality = "observed" | "interpolated_quadratic" | "gap" | "derived";

export interface DisplaySeriesQuality {
  grid_power_kw: DisplayQuality;
  battery_power_kw: DisplayQuality;
  soc: DisplayQuality;
  load_minus_pv_kw: DisplayQuality;
}

export interface DisplaySeriesPoint {
  time: string;
  grid_power_kw: number | null;
  battery_power_kw: number | null;
  soc: number | null;
  load_minus_pv_kw: number | null;
  quality: DisplaySeriesQuality;
  display_only: boolean;
}

export interface DisplaySeriesResponse {
  plant_id: string;
  window_hours: number;
  step_minutes: number;
  display_only: boolean;
  series: DisplaySeriesPoint[];
}
```

- [ ] **Step 4: 增加前端 API 函数**

Modify the import in `web/mpc-dashboard/src/api.ts`:

```ts
import type { DashboardResponse, DisplaySeriesResponse, RunMpcRequest, RunMpcResponse } from "./types";
```

Add interfaces and functions after `DashboardRequestOptions`:

```ts
export interface DisplaySeriesRequestOptions {
  windowHours: number;
  referenceTime?: string;
  maxGapMinutes?: number;
}
```

Add functions after `fetchDashboard`:

```ts
export function buildDisplaySeriesUrl(plantId: string, options: DisplaySeriesRequestOptions): string {
  const params = new URLSearchParams({ window_hours: String(options.windowHours) });
  if (options.referenceTime) {
    params.set("reference_time", options.referenceTime);
  }
  if (options.maxGapMinutes) {
    params.set("max_gap_minutes", String(options.maxGapMinutes));
  }
  return `/api/v1/plants/${encodeURIComponent(plantId)}/display-series?${params}`;
}

export async function fetchDisplaySeries(
  plantId: string,
  options: DisplaySeriesRequestOptions,
  signal?: AbortSignal,
): Promise<DisplaySeriesResponse> {
  const response = await fetch(buildDisplaySeriesUrl(plantId, options), { signal });
  if (!response.ok) {
    throw new Error(`display series request failed: HTTP ${response.status}`);
  }
  return response.json() as Promise<DisplaySeriesResponse>;
}
```

- [ ] **Step 5: 运行前端 API 测试**

Run:

```bash
cd web/mpc-dashboard && npm test -- src/api.test.ts --run
```

Expected:

```text
PASS src/api.test.ts
```

- [ ] **Step 6: 写失败的数据转换测试**

Create `web/mpc-dashboard/src/displaySeries.test.ts`:

```ts
import { describe, expect, test } from "vitest";
import { displaySeriesToDashboardSeries, qualitySummary } from "./displaySeries";
import type { DisplaySeriesResponse } from "./types";

const response: DisplaySeriesResponse = {
  plant_id: "hehong_huajin",
  window_hours: 2,
  step_minutes: 1,
  display_only: true,
  series: [
    {
      time: "2026-06-16T02:00:00",
      grid_power_kw: 100,
      battery_power_kw: 10,
      soc: 0.5,
      load_minus_pv_kw: 110,
      quality: {
        grid_power_kw: "observed",
        battery_power_kw: "interpolated_quadratic",
        soc: "observed",
        load_minus_pv_kw: "derived",
      },
      display_only: true,
    },
  ],
};

describe("display series conversion", () => {
  test("maps display-only raw fields into dashboard series shape", () => {
    const series = displaySeriesToDashboardSeries(response);

    expect(series).toEqual([
      {
        time: "2026-06-16T02:00:00",
        actual_grid_power_kw: 100,
        actual_battery_power_kw: 10,
        actual_soc: 0.5,
        actual_load_kw: null,
        actual_pv_kw: null,
        load_minus_pv_kw: 110,
        mpc_grid_power_kw: null,
        mpc_battery_power_kw: null,
        mpc_soc: null,
        mpc_load_kw: null,
        mpc_pv_kw: null,
        buy_price: null,
        sell_price: null,
        quality_flag: "display:interpolated_quadratic",
        display_quality: response.series[0].quality,
      },
    ]);
  });

  test("summarizes observed quality as display:observed", () => {
    expect(
      qualitySummary({
        grid_power_kw: "observed",
        battery_power_kw: "observed",
        soc: "observed",
        load_minus_pv_kw: "derived",
      }),
    ).toBe("display:observed");
  });
});
```

- [ ] **Step 7: 运行转换测试，确认文件不存在**

Run:

```bash
cd web/mpc-dashboard && npm test -- src/displaySeries.test.ts --run
```

Expected:

```text
Cannot find module './displaySeries'
```

- [ ] **Step 8: 实现转换模块**

Create `web/mpc-dashboard/src/displaySeries.ts`:

```ts
import type { DashboardSeriesPoint, DisplaySeriesQuality, DisplaySeriesResponse } from "./types";

export function qualitySummary(quality: DisplaySeriesQuality): string {
  const values = Object.values(quality);
  if (values.includes("gap")) return "display:gap";
  if (values.includes("interpolated_quadratic")) return "display:interpolated_quadratic";
  return "display:observed";
}

export function displaySeriesToDashboardSeries(response: DisplaySeriesResponse): Array<DashboardSeriesPoint & { display_quality: DisplaySeriesQuality }> {
  return response.series.map((point) => ({
    time: point.time,
    actual_grid_power_kw: point.grid_power_kw,
    actual_battery_power_kw: point.battery_power_kw,
    actual_soc: point.soc,
    actual_load_kw: null,
    actual_pv_kw: null,
    load_minus_pv_kw: point.load_minus_pv_kw,
    mpc_grid_power_kw: null,
    mpc_battery_power_kw: null,
    mpc_soc: null,
    mpc_load_kw: null,
    mpc_pv_kw: null,
    buy_price: null,
    sell_price: null,
    quality_flag: qualitySummary(point.quality),
    display_quality: point.quality,
  }));
}
```

- [ ] **Step 9: 允许 dashboard series 携带展示质量元数据**

Modify `web/mpc-dashboard/src/types.ts` inside `DashboardSeriesPoint`:

```ts
  display_quality?: DisplaySeriesQuality;
```

- [ ] **Step 10: 运行转换测试**

Run:

```bash
cd web/mpc-dashboard && npm test -- src/displaySeries.test.ts --run
```

Expected:

```text
PASS src/displaySeries.test.ts
```

- [ ] **Step 11: 提交前端 API 和转换层**

Run:

```bash
git add web/mpc-dashboard/src/types.ts web/mpc-dashboard/src/api.ts web/mpc-dashboard/src/api.test.ts web/mpc-dashboard/src/displaySeries.ts web/mpc-dashboard/src/displaySeries.test.ts
git commit -m "feat: add dashboard display series client"
```

Expected:

```text
[codex/mpc-customer-dashboard-react ...] feat: add dashboard display series client
```

---

### Task 4: 主曲线接入展示层数据并保留回退

**Files:**
- Modify: `web/mpc-dashboard/src/App.tsx`
- Modify: `web/mpc-dashboard/src/chartOptions.ts`
- Modify: `web/mpc-dashboard/src/chartOptions.test.ts`

- [ ] **Step 1: 写 chart tooltip 质量标签测试**

Modify `web/mpc-dashboard/src/chartOptions.test.ts` by adding a test that builds power chart option with display quality:

```ts
  test("power chart tooltip formatter labels display interpolation quality", () => {
    const option = buildPowerChartOption([
      {
        time: "2026-06-16T02:00:00",
        actual_grid_power_kw: 100,
        actual_battery_power_kw: 10,
        actual_soc: 0.5,
        actual_load_kw: null,
        actual_pv_kw: null,
        load_minus_pv_kw: 110,
        mpc_grid_power_kw: null,
        mpc_battery_power_kw: null,
        mpc_soc: null,
        mpc_load_kw: null,
        mpc_pv_kw: null,
        buy_price: null,
        sell_price: null,
        quality_flag: "display:interpolated_quadratic",
        display_quality: {
          grid_power_kw: "interpolated_quadratic",
          battery_power_kw: "observed",
          soc: "observed",
          load_minus_pv_kw: "derived",
        },
      },
    ]);

    expect(typeof option.tooltip.formatter).toBe("function");
    const formatter = option.tooltip.formatter as (params: Array<{ seriesName: string; data: number; dataIndex: number }>) => string;
    const html = formatter([{ seriesName: "工厂电网功率", data: 100, dataIndex: 0 }]);

    expect(html).toContain("工厂电网功率");
    expect(html).toContain("插值");
  });
```

- [ ] **Step 2: 运行 chart 测试，确认 tooltip 类型不足**

Run:

```bash
cd web/mpc-dashboard && npm test -- src/chartOptions.test.ts --run
```

Expected:

```text
formatter is undefined
```

- [ ] **Step 3: 扩展 chart option tooltip 类型和 formatter**

Modify `web/mpc-dashboard/src/chartOptions.ts`.

Change tooltip type:

```ts
  tooltip: { trigger: "axis"; formatter?: (params: Array<{ seriesName: string; data: number | null; dataIndex: number }>) => string };
```

Add helper functions near `valueOrNull`:

```ts
function qualityLabel(point: DashboardSeriesPoint, seriesName: string): string {
  const quality = point.display_quality;
  if (!quality) return "";
  const field =
    seriesName === "工厂电网功率"
      ? quality.grid_power_kw
      : seriesName === "工厂储能功率"
        ? quality.battery_power_kw
        : seriesName === "工厂SOC"
          ? quality.soc
          : "";
  if (field === "observed") return "真实";
  if (field === "interpolated_quadratic") return "插值";
  if (field === "gap") return "缺口";
  if (field === "derived") return "派生";
  return "";
}

function formatTooltipValue(value: number | null): string {
  return value === null || value === undefined ? "--" : String(value);
}
```

Change `buildPowerChartOption` return tooltip:

```ts
    tooltip: {
      trigger: "axis",
      formatter: (params) => {
        return params
          .map((param) => {
            const point = series[param.dataIndex];
            const label = point ? qualityLabel(point, param.seriesName) : "";
            const suffix = label ? ` (${label})` : "";
            return `${param.seriesName}: ${formatTooltipValue(param.data)}${suffix}`;
          })
          .join("<br/>");
      },
    },
```

- [ ] **Step 4: 运行 chart 测试**

Run:

```bash
cd web/mpc-dashboard && npm test -- src/chartOptions.test.ts --run
```

Expected:

```text
PASS src/chartOptions.test.ts
```

- [ ] **Step 5: 接入 App 并保留 dashboard 回退**

Modify imports in `web/mpc-dashboard/src/App.tsx`:

```ts
import { fetchDashboard, fetchDisplaySeries, toApiTime } from "./api";
import { displaySeriesToDashboardSeries } from "./displaySeries";
import type { DashboardResponse, DashboardSeriesPoint, DisplaySeriesResponse } from "./types";
```

Add state after `data`:

```ts
  const [displayData, setDisplayData] = useState<DisplaySeriesResponse | null>(null);
```

Replace the `fetchDashboard(...).then(...).catch(...).finally(...)` block with:

```ts
    Promise.allSettled([
      fetchDashboard(
        plantId,
        {
          windowHours,
          runId: runId.trim() || undefined,
          startTime: toApiTime(rangeStart),
          endTime: toApiTime(rangeEnd),
        },
        controller.signal,
      ),
      fetchDisplaySeries(
        plantId,
        {
          windowHours: windowHours === 24 ? 24 : 2,
        },
        controller.signal,
      ),
    ])
      .then(([dashboardResult, displayResult]) => {
        if (dashboardResult.status === "fulfilled") {
          setData(dashboardResult.value);
          setLastLoadedAt(new Date());
          setError(null);
        } else if (dashboardResult.reason?.name !== "AbortError") {
          setData(null);
          setError(dashboardResult.reason.message);
        }

        if (displayResult.status === "fulfilled") {
          setDisplayData(displayResult.value);
        } else if (displayResult.reason?.name !== "AbortError") {
          setDisplayData(null);
        }
      })
      .finally(() => setLoading(false));
```

Add memo before `status`:

```ts
  const realtimeSeries = useMemo<DashboardSeriesPoint[]>(() => {
    if (displayData?.series.length) {
      return displaySeriesToDashboardSeries(displayData);
    }
    return data?.series || [];
  }, [data?.series, displayData]);
```

Change the main `PowerChart` only:

```tsx
                expandedChildren={<PowerChart series={realtimeSeries} />}
```

and:

```tsx
                <PowerChart series={realtimeSeries} />
```

Leave `RevenueChart`, KPI estimates, comparison cards, demand/revenue comparison on `data?.series` and `data?.comparison`.

- [ ] **Step 6: 运行前端测试**

Run:

```bash
cd web/mpc-dashboard && npm test -- --run
```

Expected:

```text
全部通过，末尾显示 Test Files passed
```

- [ ] **Step 7: 运行前端生产构建**

Run:

```bash
cd web/mpc-dashboard && npm run build
```

Expected:

```text
✓ built
```

- [ ] **Step 8: 提交前端展示层接入**

Run:

```bash
git add web/mpc-dashboard/src/App.tsx web/mpc-dashboard/src/chartOptions.ts web/mpc-dashboard/src/chartOptions.test.ts
git commit -m "feat: use display series for realtime chart"
```

Expected:

```text
[codex/mpc-customer-dashboard-react ...] feat: use display series for realtime chart
```

---

### Task 5: 端到端本地验证

**Files:**
- No code changes expected.

- [ ] **Step 1: 运行后端在线测试**

Run:

```bash
pytest tests/online -q
```

Expected:

```text
全部通过，末尾显示 passed
```

- [ ] **Step 2: 运行插件测试，确认没有破坏采集和推送**

Run:

```bash
node --test tools/ecloud-data-extractor/*.test.js
```

Expected:

```text
# pass
# fail 0
```

- [ ] **Step 3: 运行前端测试和构建**

Run:

```bash
cd web/mpc-dashboard && npm test -- --run && npm run build
```

Expected:

```text
Test Files ... passed
✓ built
```

- [ ] **Step 4: 本地启动独立后端，不影响实习平台**

Run:

```bash
MPC_DATABASE_URL=sqlite:///./data/mpc_online.db uvicorn microgrid_online.api:app --host 127.0.0.1 --port 18001
```

Expected:

```text
Uvicorn running on http://127.0.0.1:18001
```

- [ ] **Step 5: 本地检查接口响应**

In a second terminal:

```bash
curl -fsS 'http://127.0.0.1:18001/api/v1/plants/hehong_huajin/display-series?window_hours=2' | python3 -m json.tool | head -80
```

Expected:

```text
{
    "plant_id": "hehong_huajin",
    "window_hours": 2,
    "step_minutes": 1,
    "display_only": true,
    "series": [
```

- [ ] **Step 6: 停止本地 18001 后端**

Press `Ctrl+C` in the uvicorn terminal.

Expected:

```text
Application shutdown complete.
```

---

### Task 6: 云端只验证 MPC 独立服务

**Files:**
- No code changes expected.

- [ ] **Step 1: 确认不要碰实习平台容器**

Run on server:

```bash
docker ps --format 'table {{.Names}}\t{{.Ports}}\t{{.Status}}'
```

Expected:

```text
mpc-online-platform 出现在列表中；不要 stop、rename、restart internship-frontend 或 internship-backend
```

- [ ] **Step 2: 部署前只重建 MPC 镜像和 MPC 容器**

Use the existing isolated MPC deployment flow only. If a command would change reverse proxy, internship volume, internship database, or internship container, stop and ask for confirmation.

Expected:

```text
Only mpc-online-platform changes
```

- [ ] **Step 3: 云端 healthz**

Run from local machine:

```bash
curl -fsS 'http://8.163.49.151:18000/healthz'
```

Expected:

```json
{"status":"ok","service":"online-mpc"}
```

- [ ] **Step 4: 云端 display-series 检查和宏华进**

Run:

```bash
curl -fsS 'http://8.163.49.151:18000/api/v1/plants/hehong_huajin/display-series?window_hours=2' | python3 -m json.tool | head -100
```

Expected:

```text
"plant_id": "hehong_huajin"
"display_only": true
"quality"
```

- [ ] **Step 5: 云端 display-series 检查奥莱德**

Run:

```bash
curl -fsS 'http://8.163.49.151:18000/api/v1/plants/ecloud_station_3341/display-series?window_hours=2' | python3 -m json.tool | head -100
```

Expected:

```text
"plant_id": "ecloud_station_3341"
"display_only": true
"quality"
```

- [ ] **Step 6: 前端人工验收**

Open:

```text
http://8.163.49.151:18000/dashboard?plant_id=hehong_huajin
http://8.163.49.151:18000/dashboard?plant_id=ecloud_station_3341
```

Expected:

```text
主曲线有 1 分钟级别数据；tooltip 能区分真实和插值；长断点不连线；指标卡仍能显示 15 分钟聚合结果。
```

---

## 验收标准

- `raw_grid_meter` 和 `raw_battery` 数据结构不变。
- `telemetry_15min` 聚合逻辑不变。
- `/api/v1/plants/{plant_id}/display-series?window_hours=2` 返回 1 分钟展示序列。
- 二次项插值仅出现在响应 payload，不写数据库。
- 短缺口显示 `interpolated_quadratic`；长缺口显示 `gap` 且值为 `null`。
- SOC 插值被限制在 `[0, 1]`。
- 主曲线优先使用 display-series；display-series 失败时页面回退到 dashboard series。
- MPC 运行仍只依赖 `telemetry_15min`。
- 和宏华进 `hehong_huajin` 与奥莱德 `ecloud_station_3341` 都能打开曲线。
- 实习平台容器、端口、挂载、数据库、反向代理未被改动。

## 最终验证命令

```bash
pytest tests/online -q
node --test tools/ecloud-data-extractor/*.test.js
cd web/mpc-dashboard && npm test -- --run && npm run build
```

Expected:

```text
后端测试全部通过
插件测试 fail 0
前端测试全部通过
前端构建显示 built
```
