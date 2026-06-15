const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function loadContent(options = {}) {
  const source = fs.readFileSync(path.join(__dirname, "content.js"), "utf8");
  const fixedNow = options.now ? new Date(options.now) : null;
  const DateClass = fixedNow
    ? class extends Date {
        constructor(...args) {
          super(...(args.length ? args : [fixedNow.getTime()]));
        }

        static now() {
          return fixedNow.getTime();
        }
      }
    : Date;
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
    Date: DateClass,
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

test("getTimeRange uses a ten minute rolling window ending one minute before now", () => {
  const hooks = loadContent({ now: "2026-06-13T01:50:30+08:00" });

  assert.deepEqual(JSON.parse(JSON.stringify(hooks.getTimeRange())), {
    start: "2026-06-13 01:39:00",
    end: "2026-06-13 01:49:00",
  });
});

test("buildDirectQueryPayload keeps current selected eCloud points and applies latest time window", () => {
  const hooks = loadContent();
  assert.equal(typeof hooks.buildDirectQueryPayload, "function");

  const payload = hooks.buildDirectQueryPayload(
    {
      filters: {
        stationId: 391,
        sampleTime: "1",
      },
      result: {
        stationId: 391,
        deviceIdList: [
          { srcId: 1101, cols: ["soc"], colNames: ["3-BMS/BMS-系统SOC"] },
          { srcId: 1201, cols: ["p"], colNames: ["计量电表/总有功功率"] },
        ],
      },
      isOriginal: true,
    },
    {
      start: "2026-06-13 01:40:00",
      end: "2026-06-13 01:50:00",
    },
  );

  assert.deepEqual(JSON.parse(JSON.stringify(payload)), {
    stationId: 391,
    deviceIdList: [
      { srcId: 1101, cols: ["soc"], colNames: ["3-BMS/BMS-系统SOC"] },
      { srcId: 1201, cols: ["p"], colNames: ["计量电表/总有功功率"] },
    ],
    beginTime: "2026-06-13 01:40:00",
    endTime: "2026-06-13 01:50:00",
    sampleTime: "1",
    isOriginal: 1,
    pageNum: 1,
    pageSize: 2000,
  });
});

test("normalizePointDataShowListRows converts eCloud API cards to existing table row shape", () => {
  const hooks = loadContent();
  assert.equal(typeof hooks.normalizePointDataShowListRows, "function");

  const rows = hooks.normalizePointDataShowListRows(
    [
      {
        list: [
          {
            date0: "2026-06-13 01:45:40.01",
            digital0: "32",
            row_name_digital0: "3-BMS/BMS-系统SOC",
          },
        ],
      },
      {
        list: [
          {
            row_name_date0: "2026-06-13 01:45:40.01",
            row_digital0: "103.6",
            row_name_digital0: "防逆流电表/ADW-总有功功率",
          },
        ],
      },
    ],
    "2026-06-13T01:50:00.000+08:00",
  );

  assert.deepEqual(JSON.parse(JSON.stringify(rows)), [
    {
      tableName: "3-BMS/BMS-系统SOC",
      date: "2026-06-13 01:45:40.01",
      value: "32",
      timestamp: "2026-06-13T01:50:00.000+08:00",
    },
    {
      tableName: "防逆流电表/ADW-总有功功率",
      date: "2026-06-13 01:45:40.01",
      value: "103.6",
      timestamp: "2026-06-13T01:50:00.000+08:00",
    },
  ]);
});
