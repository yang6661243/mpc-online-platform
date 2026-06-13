const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function loadContentWithMetricValue(value) {
  const source = fs.readFileSync(path.join(__dirname, "content.js"), "utf8");
  const metricInput = {
    value,
    placeholder: "请选择指标",
  };
  const document = {
    readyState: "complete",
    querySelector(selector) {
      if (selector === 'input[placeholder="请选择指标"]') return metricInput;
      return null;
    },
    querySelectorAll(selector) {
      if (selector === 'input[placeholder="请选择指标"]') return [metricInput];
      return [];
    },
    addEventListener() {},
    body: { click() {} },
  };
  const context = {
    console,
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

test("detects whether all default eCloud metrics are already selected", () => {
  const hooks = loadContentWithMetricValue(
    "计量电表/1352-总有功功率,防逆流电表-ADW300/ADW-总有功功率,1-BMS/系统SOC,",
  );

  assert.equal(typeof hooks.getMissingRequiredMetrics, "function");
  assert.deepEqual(JSON.parse(JSON.stringify(hooks.getMissingRequiredMetrics())), []);
});

test("reports missing default eCloud metrics", () => {
  const hooks = loadContentWithMetricValue("计量电表/1352-总有功功率,");

  assert.deepEqual(
    JSON.parse(JSON.stringify(hooks.getMissingRequiredMetrics().map((metric) => metric.fullName))),
    [
      "1-BMS/系统SOC",
      "防逆流电表-ADW300/ADW-总有功功率",
    ],
  );
});
