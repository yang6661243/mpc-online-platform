# EMS Style Dashboard Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework the React MPC customer dashboard into a dark EMS-style real-time display with a central real-time power chart and a bottom real-time revenue chart.

**Architecture:** Keep the existing `web/mpc-dashboard` Vite/React application and the existing dashboard API. Add focused chart option builders and small chart components, then rearrange `App.tsx` into top bar, left metrics, center power/revenue panels, right status rail, and bottom comparison/table panels.

**Tech Stack:** React 18, TypeScript, Vite, ECharts, Vitest.

---

### Task 1: Revenue Chart Option

**Files:**
- Modify: `web/mpc-dashboard/src/chartOptions.ts`
- Modify: `web/mpc-dashboard/src/chartOptions.test.ts`
- Create: `web/mpc-dashboard/src/components/RevenueChart.tsx`

- [ ] **Step 1: Write the failing test**

Add a test that imports `buildRevenueChartOption`, passes two dashboard points, and expects the option to expose a single line named `累计收益`. The expected data should be `[0.13, 0.13]` when only the first point has both actual and MPC grid power.

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm test -- --run src/chartOptions.test.ts`

Expected: FAIL because `buildRevenueChartOption` is not exported.

- [ ] **Step 3: Implement the minimal chart option**

Add `buildRevenueChartOption(series)` to `chartOptions.ts`. Use `buy_price` when present, otherwise `0.986` yuan/kWh. For each 15-minute point, compute `(actual_grid_power_kw - mpc_grid_power_kw) * price * 0.25`, accumulate positive or negative values, round to two decimals, and keep the existing cumulative value when either side is missing.

- [ ] **Step 4: Add the React chart component**

Create `RevenueChart.tsx` following the same pattern as `PowerChart.tsx`, using `buildRevenueChartOption`.

- [ ] **Step 5: Run the chart tests**

Run: `npm test -- --run src/chartOptions.test.ts`

Expected: PASS.

### Task 2: EMS Layout

**Files:**
- Modify: `web/mpc-dashboard/src/App.tsx`
- Modify: `web/mpc-dashboard/src/styles.css`
- Modify: `web/mpc-dashboard/src/components/MetricCard.tsx`

- [ ] **Step 1: Update the page structure**

Replace the current light dashboard layout with a dark EMS-style layout:
- `topbar`: title, data timestamp, window selector, refresh button
- `stat-sidebar`: key metric cards
- `center-stage`: real-time power chart and revenue chart
- `status-rail`: MPC/data status and target values
- `bottom-grid`: battery/SOC chart and detail table

- [ ] **Step 2: Update metric cards**

Allow metric cards to accept optional compact styling through the existing `className` pattern or plain wrapper classes. Keep the component simple and avoid changing its public data semantics.

- [ ] **Step 3: Restyle CSS**

Use a dark background, cyan/green/orange/magenta accents, fixed chart heights, responsive grid behavior, and readable Chinese labels. Do not place cards inside cards.

- [ ] **Step 4: Run frontend tests and build**

Run:

```bash
npm test
npm run build
```

Expected: Vitest passes and Vite build succeeds.

### Task 3: Visual Verification

**Files:**
- No code changes expected unless verification finds layout defects.

- [ ] **Step 1: Start the dev server**

Run: `npm run dev -- --host 127.0.0.1 --port 5173`

- [ ] **Step 2: Open the dashboard**

Open `http://127.0.0.1:5173/dashboard?plant_id=ecloud_factory`.

- [ ] **Step 3: Check layout**

Verify the page has a dark EMS-style dashboard, the central panel is the real-time power curve, the bottom center panel is the real-time revenue curve, text does not overlap, and the layout remains usable at desktop width.
