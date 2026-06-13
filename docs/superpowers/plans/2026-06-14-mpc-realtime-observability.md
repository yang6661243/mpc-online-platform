# MPC Realtime Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the eCloud collection path and MPC dashboard clearly show whether data is fresh, pushed, and refreshed.

**Architecture:** Keep the existing isolated MPC service and browser extension flow. Add small diagnostics in the eCloud extension and small time/refresh utilities in the React dashboard without changing the ingestion schema or touching the internship platform containers.

**Tech Stack:** Chrome/Edge extension JavaScript, Node `node:test`, React + TypeScript + Vite + Vitest.

---

### Task 1: eCloud Plugin Collection Diagnostics

**Files:**
- Modify: `tools/ecloud-data-extractor/content.js`
- Test: `tools/ecloud-data-extractor/content-status.test.js`

- [ ] **Step 1: Write the failing test**

Create `content-status.test.js` that loads `content.js` in the existing VM harness and asserts `buildCollectionDiagnostic(rows, newRows)` returns row count, new row count, latest source timestamp, and a Chinese status label.

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test tools/ecloud-data-extractor/content-status.test.js`

Expected: FAIL because `buildCollectionDiagnostic` is not exposed yet.

- [ ] **Step 3: Implement plugin diagnostics**

Add a helper that calculates latest data time from returned rows, stores last row counts on each collection, and changes the floating message to include source latest time and push result. Keep default template fallback behavior.

- [ ] **Step 4: Run plugin tests**

Run: `node --test tools/ecloud-data-extractor/*.test.js`

Expected: all plugin tests pass.

### Task 2: Dashboard Auto Refresh And Data Delay

**Files:**
- Create: `web/mpc-dashboard/src/time.ts`
- Create: `web/mpc-dashboard/src/time.test.ts`
- Modify: `web/mpc-dashboard/src/App.tsx`
- Modify: `web/mpc-dashboard/src/format.ts`
- Modify: `web/mpc-dashboard/src/format.test.ts`
- Modify: `web/mpc-dashboard/src/chartOptions.test.ts`

- [ ] **Step 1: Write the failing test**

Create `time.test.ts` that asserts API timestamps without timezone are treated as UTC, displayed in `Asia/Shanghai`, and converted into a delay label.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web/mpc-dashboard && npm test -- src/time.test.ts`

Expected: FAIL because `time.ts` does not exist yet.

- [ ] **Step 3: Implement dashboard time utilities**

Add `parseDashboardTime`, `formatChinaTime`, and `formatDataDelay`.

- [ ] **Step 4: Wire UI refresh**

Add a 60-second interval that triggers the existing fetch path, show the last API load time, and add a metric card for data delay.

- [ ] **Step 5: Run dashboard tests and build**

Run: `cd web/mpc-dashboard && npm test && npm run build`

Expected: tests and build pass.

### Task 3: Sync Active Extension Copy

**Files:**
- Sync: `tools/ecloud-data-extractor/content.js` to `/Users/yangjiaowei/Desktop/ecloud-data-extractor/content.js`

- [ ] **Step 1: Copy the tested plugin files to the active unpacked Edge extension directory**

Run: `cp tools/ecloud-data-extractor/content.js /Users/yangjiaowei/Desktop/ecloud-data-extractor/content.js`

Expected: the active extension directory matches the repo file except historical backup files.
