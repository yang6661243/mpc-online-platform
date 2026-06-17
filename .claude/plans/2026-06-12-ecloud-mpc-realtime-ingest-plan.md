# eCloud MPC Realtime Ingest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the existing eCloud browser extension so collected table rows are pushed into the deployed online MPC service and persisted through the existing MPC API/database.

**Architecture:** Keep browser collection and server persistence separate. `content.js` continues extracting table rows; `background.js` converts rows to MPC API payloads, sends them to `/api/v1/mpc/input-data`, then triggers `/api/v1/plants/{plant_id}/aggregate`. The MPC service remains the only database writer.

**Tech Stack:** Chrome/Edge Manifest V3 extension JavaScript, Node.js built-in test runner for pure conversion tests, existing FastAPI/SQLAlchemy online MPC service.

---

### Task 1: Extract MPC Payload Conversion Logic

**Files:**
- Create: `/Users/yangjiaowei/Desktop/ecloud-data-extractor/mpcBridge.js`
- Create: `/Users/yangjiaowei/Desktop/ecloud-data-extractor/mpcBridge.test.js`

- [ ] **Step 1: Write failing tests**

Create Node tests that require `mpcBridge.js` and verify table-row conversion:

```javascript
const test = require("node:test");
const assert = require("node:assert/strict");
const {
  buildMpcIngestPayloads,
  toChinaIsoTimestamp,
} = require("./mpcBridge.js");

test("converts eCloud grid table rows into a grid_meter payload", () => {
  const payloads = buildMpcIngestPayloads(
    [
      {
        tableName: "防逆流电表/ADW-总有功功率",
        date: "2026-06-12 10:15:00",
        value: "410.2",
      },
    ],
    { plantId: "ecloud_factory", generatedAt: "2026-06-12T10:16:00+08:00" }
  );

  assert.equal(payloads.length, 1);
  assert.equal(payloads[0].data_type, "grid_meter");
  assert.deepEqual(payloads[0].records, [
    { time: "2026-06-12T10:15:00+08:00", grid_power_kw: 410.2 },
  ]);
});

test("combines battery power and SOC rows by timestamp", () => {
  const payloads = buildMpcIngestPayloads(
    [
      { tableName: "储能功率", date: "2026-06-12 10:15:00", value: "-20" },
      { tableName: "储能SOC", date: "2026-06-12 10:15:00", value: "58" },
    ],
    { plantId: "ecloud_factory", generatedAt: "2026-06-12T10:16:00+08:00" }
  );

  const batteryPayload = payloads.find((payload) => payload.data_type === "battery");
  assert.deepEqual(batteryPayload.records, [
    {
      time: "2026-06-12T10:15:00+08:00",
      battery_power_kw: -20,
      soc: 58,
    },
  ]);
  assert.equal(batteryPayload.soc_unit, "percent");
});

test("does not create battery payload when SOC is missing", () => {
  const payloads = buildMpcIngestPayloads(
    [{ tableName: "储能功率", date: "2026-06-12 10:15:00", value: "-20" }],
    { plantId: "ecloud_factory", generatedAt: "2026-06-12T10:16:00+08:00" }
  );

  assert.equal(payloads.some((payload) => payload.data_type === "battery"), false);
});

test("formats local eCloud timestamp as China ISO timestamp", () => {
  assert.equal(toChinaIsoTimestamp("2026-06-12 10:15:00"), "2026-06-12T10:15:00+08:00");
});
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
cd /Users/yangjiaowei/Desktop/ecloud-data-extractor
node --test mpcBridge.test.js
```

Expected: FAIL because `mpcBridge.js` does not exist yet.

- [ ] **Step 3: Implement conversion logic**

Create `mpcBridge.js` with pure functions:

- `toChinaIsoTimestamp(value)`
- `classifyTableName(tableName)`
- `buildMpcIngestPayloads(rows, options)`
- `buildAggregateWindow(rows, windowMinutes)`
- CommonJS exports for tests and browser use.

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```bash
cd /Users/yangjiaowei/Desktop/ecloud-data-extractor
node --test mpcBridge.test.js
```

Expected: all tests pass.

### Task 2: Wire Background Push to MPC

**Files:**
- Modify: `/Users/yangjiaowei/Desktop/ecloud-data-extractor/manifest.json`
- Modify: `/Users/yangjiaowei/Desktop/ecloud-data-extractor/background.js`

- [ ] **Step 1: Add MPC host permission**

Add `http://8.163.49.151:18000/*` to `host_permissions`.

- [ ] **Step 2: Load bridge logic in service worker**

Add `importScripts("mpcBridge.js");` at the top of `background.js`.

- [ ] **Step 3: Push records after `saveData`**

When `saveData` receives rows:

- Keep existing in-memory CSV cache.
- Convert rows using `buildMpcIngestPayloads`.
- POST each payload to `/api/v1/mpc/input-data`.
- If any payload is sent, POST aggregate window to `/api/v1/plants/{plantId}/aggregate`.
- Store `mpcStatus` with latest success/error/accepted counts.

- [ ] **Step 4: Preserve existing popup actions**

Keep `getData`, `clearData`, `downloadData`, and CSV behavior unchanged.

### Task 3: Expose Push Status in Popup

**Files:**
- Modify: `/Users/yangjiaowei/Desktop/ecloud-data-extractor/popup.html`
- Modify: `/Users/yangjiaowei/Desktop/ecloud-data-extractor/popup.js`

- [ ] **Step 1: Add status fields**

Add fields for:

- MPC 推送状态
- 最近推送时间
- 已推送记录
- 最近错误

- [ ] **Step 2: Render status from background**

Extend `updateStatus()` to display `response.mpcStatus`.

### Task 4: Update Chinese Documentation

**Files:**
- Modify: `/Users/yangjiaowei/Desktop/ecloud-data-extractor/README.md`

- [ ] **Step 1: Document the MPC push path**

Add:

- Current MPC service URL.
- Default plant ID.
- Data field mapping.
- How to verify `/healthz`, `/dashboard`, and popup push status.

- [ ] **Step 2: Document limitations**

State:

- Browser must stay logged into eCloud.
- If table names differ, mapping in `mpcBridge.js` must be adjusted.
- Missing battery/SOC data means dashboard can show raw grid data but MPC cannot fully compute.

### Task 5: Verify End-to-End Locally and Against Server

**Files:**
- No code changes.

- [ ] **Step 1: Run extension unit tests**

```bash
cd /Users/yangjiaowei/Desktop/ecloud-data-extractor
node --test mpcBridge.test.js
```

- [ ] **Step 2: Run MPC backend tests**

```bash
cd /Users/yangjiaowei/Desktop/mpc无连接沙盘-虚拟电厂
python -B -m pytest -q tests/online
```

- [ ] **Step 3: Verify deployed service health**

```bash
curl -fsS --connect-timeout 10 http://8.163.49.151:18000/healthz
```

- [ ] **Step 4: Verify deployed service accepts smoke data**

```bash
NO_PROXY='*' no_proxy='*' MPC_ONLINE_BASE_URL=http://8.163.49.151:18000 python -B scripts/online_mpc_smoke_test.py
```
