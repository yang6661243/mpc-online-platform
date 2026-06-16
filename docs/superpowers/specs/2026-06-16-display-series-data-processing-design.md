# Frontend Display Series Data Processing Design

## Goal

Make newly collected eCloud telemetry display correctly and smoothly in the MPC dashboard while keeping the MPC input path trustworthy.

## Constraints

- Do not change the raw database facts in `raw_grid_meter` or `raw_battery`.
- Do not feed display interpolation into MPC.
- Keep `telemetry_15min` as the stable statistics and MPC input layer.
- Keep all deployment and database work isolated to the MPC service. Do not affect the existing internship platform containers, ports, mounts, database, or reverse proxy.

## Current Data Model

Raw data lands in two source tables:

- `raw_grid_meter`: `plant_id`, `time`, `grid_power_kw`.
- `raw_battery`: `plant_id`, `time`, `battery_power_kw`, `soc`, availability flags.

The existing operational layer is:

- `telemetry_15min`: 15-minute aggregates, sample counts, and `quality_flag`.

This layer is already appropriate for dashboard metric cards, readiness checks, and MPC input. It should remain independent from any display-only interpolation.

## Proposed Architecture

Add a display-series layer exposed by a new read-only API:

```text
GET /api/v1/plants/{plant_id}/display-series?window_hours=2
```

The endpoint reads recent raw grid and battery rows, builds a 1-minute display timeline, and returns field-level values plus quality labels for chart rendering.

The existing dashboard endpoint remains responsible for 15-minute telemetry, strategy comparison, and status cards.

## Display Series Shape

Each row represents one display timestamp:

```json
{
  "time": "2026-06-16T11:35:00",
  "grid_power_kw": 523.5,
  "battery_power_kw": 0.4,
  "soc": 0.05,
  "load_minus_pv_kw": 523.9,
  "quality": {
    "grid_power_kw": "observed",
    "battery_power_kw": "interpolated_quadratic",
    "soc": "observed",
    "load_minus_pv_kw": "derived"
  },
  "display_only": true
}
```

Allowed field quality values:

- `observed`: value came from an actual raw sample at that display timestamp.
- `interpolated_quadratic`: value was generated only for display smoothing.
- `gap`: value is absent because the gap is too large or interpolation is unsafe.
- `derived`: value was calculated from other display values, such as `load_minus_pv_kw`.

## Interpolation Rules

Interpolation applies only to display output:

- Timeline step: 1 minute.
- Default window: last 2 hours.
- Supported windows: 2, 6, and 24 hours.
- Interpolation method: local quadratic interpolation.
- Short-gap limit: interpolate only when the surrounding observed samples span no more than 5 minutes.
- Minimum evidence: at least 3 nearby observed samples for that field.
- Long gaps return `null` and quality `gap`.

Field-specific bounds:

- `soc`: clamp to `[0, 1]`.
- `grid_power_kw`: clamp to a small envelope around nearby observed points to avoid visual overshoot.
- `battery_power_kw`: clamp to a small envelope around nearby observed points to avoid visual overshoot.

`load_minus_pv_kw` is derived only when both display grid and display battery values exist:

```text
load_minus_pv_kw = grid_power_kw + battery_power_kw
```

If either component is missing, `load_minus_pv_kw` is `null`.

## Frontend Behavior

The dashboard should use the new display-series endpoint for the main real-time chart.

Metric cards and MPC readiness continue to use the existing dashboard payload backed by `telemetry_15min`.

If display-series fails, the frontend falls back to the existing 15-minute dashboard series so the page is not blank.

Tooltip labels should identify whether each value is real or interpolated:

```text
电网功率: 523.5 kW (真实)
储能功率: 0.4 kW (插值)
SOC: 5.0% (真实)
```

## Non-Goals

- Do not add interpolation rows to SQLite tables.
- Do not change MPC input generation.
- Do not change the 15-minute aggregation algorithm in this phase.
- Do not deploy or modify internship-platform infrastructure.

## Testing Plan

Backend tests:

- Quadratic interpolation fills a short gap with bounded values.
- Long gaps return `null` with `gap` quality.
- SOC interpolation is clamped to `[0, 1]`.
- Display endpoint returns `display_only: true`.
- Existing dashboard and aggregation tests continue to pass.

Frontend tests:

- API client parses display-series payloads.
- Main chart uses display-series when available.
- Main chart falls back to dashboard series when display-series fails.
- Tooltip text distinguishes observed and interpolated values.

## Rollout

1. Implement and test backend display-series generation.
2. Add the read-only display-series API.
3. Update frontend API client and chart data selection.
4. Validate with live `hehong_huajin` and `ecloud_station_3341` data.
5. Keep MPC runs on `telemetry_15min`.
