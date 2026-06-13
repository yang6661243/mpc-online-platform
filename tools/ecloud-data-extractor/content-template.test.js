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
      if (options.selectedMetricText) {
        return [{ value: options.selectedMetricText }];
      }
      return [];
    },
    addEventListener() {},
    body: {
      appendChild() {},
      click() {},
    },
    createElement() {
      return {
        set id(value) {
          this._id = value;
        },
        set textContent(value) {
          this._textContent = value;
        },
        remove() {},
      };
    },
    documentElement: {
      appendChild() {},
    },
    head: {
      appendChild() {},
    },
  };

  const context = {
    console,
    Date: DateClass,
    document,
    Event: class {
      constructor(type, init = {}) {
        this.type = type;
        this.bubbles = Boolean(init.bubbles);
      }
    },
    fetch: options.fetch || (async () => ({ ok: true, text: async () => "{}" })),
    setInterval() {
      return 1;
    },
    clearInterval() {},
    setTimeout,
    window: {
      __ECLOUD_COLLECTOR_TEST__: true,
      location: {
        href: "https://unit.test/#/surveillance/battery-analysis",
        origin: "https://unit.test",
      },
      addEventListener() {},
      removeEventListener() {},
      localStorage: {
        getItem() {
          return "";
        },
      },
      sessionStorage: {
        getItem() {
          return "zh";
        },
      },
    },
    chrome: {
      runtime: {
        onMessage: { addListener() {} },
        sendMessage() {},
      },
    },
  };
  context.globalThis = context;
  vm.runInNewContext(source, context, { filename: "content.js" });
  return context.window.__ecloudCollectorTestHooks;
}

test("stores observed eCloud query template and rewrites only the rolling time window", () => {
  const hooks = loadContent({ now: "2026-06-13T08:30:40+08:00" });

  hooks.observeEcloudQueryTemplate({
    url: "https://ecloud.hoenergypower.cn/api/business/point/pointDataShowList",
    payload: {
      stationId: 391,
      deviceIdList: [
        { srcId: 1, cols: ["p"], colNames: ["计量电表/1352-总有功功率"] },
        { srcId: 2, cols: ["soc"], colNames: ["1-BMS/系统SOC"] },
        { srcId: 3, cols: ["p"], colNames: ["防逆流电表-ADW300/ADW-总有功功率"] },
      ],
      beginTime: "2026-06-01 00:00:00",
      endTime: "2026-06-02 00:00:00",
      sampleTime: "1",
      isOriginal: 0,
      pageNum: 1,
      pageSize: 20,
    },
  });

  const query = hooks.buildObservedQueryPayload();

  assert.deepEqual(JSON.parse(JSON.stringify(query)), {
    stationId: 391,
    deviceIdList: [
      { srcId: 1, cols: ["p"], colNames: ["计量电表/1352-总有功功率"] },
      { srcId: 2, cols: ["soc"], colNames: ["1-BMS/系统SOC"] },
      { srcId: 3, cols: ["p"], colNames: ["防逆流电表-ADW300/ADW-总有功功率"] },
    ],
    beginTime: "2026-06-12 08:30:00",
    endTime: "2026-06-13 08:30:00",
    sampleTime: "1",
    isOriginal: 0,
    pageNum: 1,
    pageSize: 2000,
  });
});

test("accepts the relative pointDataShowList URL captured from axios before the /api base path is applied", () => {
  const hooks = loadContent({ now: "2026-06-13T08:30:40+08:00" });

  hooks.observeEcloudQueryTemplate({
    url: "/business/point/pointDataShowList",
    payload: {
      stationId: 391,
      deviceIdList: [
        { srcId: 1, cols: ["p"], colNames: ["计量电表/1352-总有功功率"] },
        { srcId: 2, cols: ["soc"], colNames: ["1-BMS/系统SOC"] },
        { srcId: 3, cols: ["p"], colNames: ["防逆流电表-ADW300/ADW-总有功功率"] },
      ],
      beginTime: "2026-06-01 00:00:00",
      endTime: "2026-06-02 00:00:00",
      sampleTime: "1",
      isOriginal: 0,
      pageNum: 1,
      pageSize: 20,
    },
  });

  assert.equal(hooks.buildObservedQueryPayload().beginTime, "2026-06-12 08:30:00");
});

test("uses the factory default query template before the page captures a template", () => {
  const hooks = loadContent({ now: "2026-06-13T08:30:40+08:00" });

  const query = hooks.buildObservedQueryPayload();

  assert.deepEqual(JSON.parse(JSON.stringify(query)), {
    stationId: 2289,
    deviceIdList: [
      { srcId: 432000083, cols: ["YC0014"], colNames: ["计量电表/1352-总有功功率"] },
      { srcId: 432000001, cols: ["YC0004"], colNames: ["1-BMS/系统SOC"] },
      { srcId: 432000084, cols: ["YC0014"], colNames: ["防逆流电表-ADW300/ADW-总有功功率"] },
    ],
    beginTime: "2026-06-12 08:30:00",
    endTime: "2026-06-13 08:30:00",
    sampleTime: "1",
    isOriginal: 0,
    pageNum: 1,
    pageSize: 2000,
  });
});

test("uses the visible selected metric text when captured payload only includes partial metric names", () => {
  const hooks = loadContent({
    selectedMetricText:
      "1-BMS/系统SOC,计量电表/1352-总有功功率,防逆流电表-ADW300/ADW-总有功功率",
  });

  hooks.observeEcloudQueryTemplate({
    url: "/business/point/pointDataShowList",
    payload: {
      stationId: 391,
      deviceIdList: [
        { srcId: 1, cols: ["p"], colNames: ["防逆流电表-ADW300/ADW-总有功功率"] },
        { srcId: 2, cols: ["p"] },
        { srcId: 3, cols: ["soc"] },
      ],
    },
  });

  assert.equal(hooks.buildObservedQueryPayload().stationId, 391);
});

test("rejects observed templates that do not contain all required metrics", () => {
  const hooks = loadContent();

  assert.throws(
    () =>
      hooks.observeEcloudQueryTemplate({
        url: "/api/business/point/pointDataShowList",
        payload: {
          stationId: 391,
          deviceIdList: [
            { srcId: 1, cols: ["p"], colNames: ["计量电表/1352-总有功功率"] },
          ],
        },
      }),
    /缺少必需指标/,
  );
});

test("keeps only required eCloud metric rows from API cards", () => {
  const hooks = loadContent();
  const rows = hooks.normalizePointDataShowListRows([
    {
      list: [
        {
          row_name_digital0: "计量电表/1352-总有功功率",
          date0: "2026-06-13 08:29:00.01",
          digital0: "15.2",
        },
        {
          row_name_digital0: "无关温度",
          date0: "2026-06-13 08:29:00.01",
          digital0: "30",
        },
        {
          row_name_digital0: "防逆流电表-ADW300/ADW-总有功功率",
          date0: "2026-06-13 08:29:00.01",
          digital0: "110",
        },
        {
          row_name_digital0: "1-BMS/系统SOC",
          date0: "2026-06-13 08:29:00.01",
          digital0: "66",
        },
      ],
    },
  ]);

  assert.deepEqual(
    JSON.parse(JSON.stringify(rows.map((row) => row.tableName))),
    [
      "计量电表/1352-总有功功率",
      "防逆流电表-ADW300/ADW-总有功功率",
      "1-BMS/系统SOC",
    ],
  );
});
