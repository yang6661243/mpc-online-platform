const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function loadContent() {
  const source = fs.readFileSync(path.join(__dirname, "content.js"), "utf8");
  const document = {
    readyState: "complete",
    querySelector() {
      return null;
    },
    querySelectorAll() {
      return [];
    },
    addEventListener() {},
    body: { click() {} },
  };
  const context = {
    console,
    Date,
    document,
    Event: class {
      constructor(type, options = {}) {
        this.type = type;
        this.bubbles = Boolean(options.bubbles);
      }
    },
    setTimeout,
    window: {
      __ECLOUD_COLLECTOR_TEST__: true,
      location: { href: "https://unit.test/" },
    },
    chrome: {
      runtime: {
        onMessage: { addListener() {} },
      },
    },
  };
  context.globalThis = context;
  vm.runInNewContext(source, context, { filename: "content.js" });
  return context.window.__ecloudCollectorTestHooks;
}

test("buildCollectionDiagnostic summarizes returned rows, new rows, and latest source time", () => {
  const hooks = loadContent();

  const rows = [
    {
      tableName: "防逆流电表-ADW300/ADW-总有功功率",
      date: "2026-06-14 00:27:40.004",
      value: "107.6",
      timestamp: "2026-06-14T00:28:32+08:00",
    },
    {
      tableName: "计量电表/1352-总有功功率",
      date: "2026-06-14 00:28:40.01",
      value: "44.3",
      timestamp: "2026-06-14T00:29:26+08:00",
    },
    {
      tableName: "1-BMS/系统SOC",
      date: "2026-06-14 00:25:00",
      value: "13",
      timestamp: "2026-06-14T00:26:24+08:00",
    },
  ];

  assert.deepEqual(JSON.parse(JSON.stringify(hooks.buildCollectionDiagnostic(rows, rows.slice(0, 1)))), {
    returnedCount: 3,
    newCount: 1,
    latestDataAt: "2026-06-14 00:28:40.01",
    label: "接口返回 3 条，新增 1 条，最新数据 2026-06-14 00:28:40.01",
  });
});

test("buildCollectionDiagnostic keeps the status explicit when eCloud returns no rows", () => {
  const hooks = loadContent();

  assert.deepEqual(JSON.parse(JSON.stringify(hooks.buildCollectionDiagnostic([], []))), {
    returnedCount: 0,
    newCount: 0,
    latestDataAt: "",
    label: "接口返回 0 条，新增 0 条，未返回目标数据",
  });
});
