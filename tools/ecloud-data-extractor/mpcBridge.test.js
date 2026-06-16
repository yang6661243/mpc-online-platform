const test = require("node:test");
const assert = require("node:assert/strict");
const {
  DEFAULT_CONFIG,
  buildAggregateWindow,
  buildMpcIngestPayloads,
  classifyTableName,
  summarizeMpcRows,
  toChinaIsoTimestamp,
} = require("./mpcBridge.js");

test("defaults uploads to hehong huajin plant id", () => {
  assert.equal(DEFAULT_CONFIG.plantId, "hehong_huajin");
});

test("defaults uploads to the cloud MPC backend", () => {
  assert.equal(DEFAULT_CONFIG.mpcBaseUrl, "http://8.163.49.151:18000");
});

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
  assert.ok(batteryPayload);
  assert.deepEqual(batteryPayload.records, [
    {
      time: "2026-06-12T10:15:00+08:00",
      battery_power_kw: -20,
      soc: 58,
    },
  ]);
  assert.equal(batteryPayload.soc_unit, "percent");
});

test("uses station metering table as aggregate battery power", () => {
  const payloads = buildMpcIngestPayloads(
    [
      { tableName: "计量电表/总有功功率", date: "2026-06-10 00:00:42.005", value: "120.4800033569336" },
      { tableName: "1-BMS/BMS-系统SOC", date: "2026-06-10 00:00:42.005", value: "58.4" },
      { tableName: "防逆流电表/ADW-总有功功率", date: "2026-06-10 00:00:42.005", value: "240.2" },
    ],
    { plantId: "ecloud_factory", generatedAt: "2026-06-12T10:16:00+08:00" }
  );

  const gridPayload = payloads.find((payload) => payload.data_type === "grid_meter");
  assert.deepEqual(gridPayload.records, [
    {
      time: "2026-06-10T00:00:42+08:00",
      grid_power_kw: 240.2,
    },
  ]);

  const batteryPayload = payloads.find((payload) => payload.data_type === "battery");
  assert.ok(batteryPayload);
  assert.deepEqual(batteryPayload.records, [
    {
      time: "2026-06-10T00:00:42+08:00",
      battery_power_kw: 120.4800033569336,
      soc: 58.4,
    },
  ]);
});

test("does not create battery payload when SOC is missing", () => {
  const payloads = buildMpcIngestPayloads(
    [{ tableName: "储能功率", date: "2026-06-12 10:15:00", value: "-20" }],
    { plantId: "ecloud_factory", generatedAt: "2026-06-12T10:16:00+08:00" }
  );

  assert.equal(payloads.some((payload) => payload.data_type === "battery"), false);
});

test("reuses nearest SOC when battery power updates more frequently than SOC", () => {
  const payloads = buildMpcIngestPayloads(
    [
      { tableName: "3-BMS/BMS-系统SOC", date: "2026-06-15 10:00:00", value: "80" },
      { tableName: "计量电表/总有功功率", date: "2026-06-15 10:03:00", value: "105" },
      { tableName: "计量电表/总有功功率", date: "2026-06-15 10:06:00", value: "106" },
    ],
    { plantId: "hehong_huajin", generatedAt: "2026-06-15T10:06:30+08:00" },
  );

  const batteryPayload = payloads.find((payload) => payload.data_type === "battery");
  assert.deepEqual(batteryPayload.records, [
    { time: "2026-06-15T10:03:00+08:00", battery_power_kw: 105, soc: 80 },
    { time: "2026-06-15T10:06:00+08:00", battery_power_kw: 106, soc: 80 },
  ]);
});

test("formats local eCloud timestamp as China ISO timestamp", () => {
  assert.equal(toChinaIsoTimestamp("2026-06-12 10:15:00"), "2026-06-12T10:15:00+08:00");
});

test("classifies default eCloud table names", () => {
  assert.equal(classifyTableName("防逆流电表/ADW-总有功功率"), "grid_power_kw");
  assert.equal(classifyTableName("计量电表/总有功功率"), "battery_power_kw");
  assert.equal(classifyTableName("储能功率"), "battery_power_kw");
  assert.equal(classifyTableName("储能SOC"), "soc");
  assert.equal(classifyTableName("防逆流电表-666/666-合相有功功率Pt"), "grid_power_kw");
  assert.equal(classifyTableName("计量电表-1352/1352-总有功功率"), "battery_power_kw");
  assert.equal(classifyTableName("4-BMS/系统SOC"), "soc");
  assert.equal(classifyTableName("无关指标"), null);
});

test("builds payloads from alternate station metric names", () => {
  const payloads = buildMpcIngestPayloads(
    [
      { tableName: "防逆流电表-666/666-合相有功功率Pt", date: "2026-06-15 15:00:00", value: "890.1" },
      { tableName: "计量电表-1352/1352-总有功功率", date: "2026-06-15 15:00:00", value: "305.2" },
      { tableName: "4-BMS/系统SOC", date: "2026-06-15 15:00:00", value: "15.8" },
    ],
    { plantId: "aolai", generatedAt: "2026-06-15T15:01:00+08:00" },
  );

  assert.deepEqual(payloads.find((payload) => payload.data_type === "grid_meter").records, [
    { time: "2026-06-15T15:00:00+08:00", grid_power_kw: 890.1 },
  ]);
  assert.deepEqual(payloads.find((payload) => payload.data_type === "battery").records, [
    { time: "2026-06-15T15:00:00+08:00", battery_power_kw: 305.2, soc: 15.8 },
  ]);
});

test("builds aggregate window aligned to 15 minute boundaries", () => {
  const window = buildAggregateWindow(
    [
      { tableName: "防逆流电表/ADW-总有功功率", date: "2026-06-12 10:08:00", value: "410.2" },
      { tableName: "防逆流电表/ADW-总有功功率", date: "2026-06-12 10:17:00", value: "411.2" },
    ],
    15
  );

  assert.deepEqual(window, {
    start_time: "2026-06-12T10:00:00+08:00",
    end_time: "2026-06-12T10:30:00+08:00",
    window_minutes: 15,
  });
});

test("summarizes matched and unmatched table names for MPC diagnostics", () => {
  const summary = summarizeMpcRows([
    { plantId: "hehong_huajin", tableName: "防逆流电表/ADW-总有功功率", date: "2026-06-12 10:00:00", value: "100" },
    { plantId: "aodelai", tableName: "未知功率字段A", date: "2026-06-12 10:00:00", value: "100" },
    { plantId: "aodelai", tableName: "未知功率字段A", date: "2026-06-12 10:01:00", value: "101" },
    { plantId: "aodelai", tableName: "未知电量字段B", date: "2026-06-12 10:00:00", value: "58" },
  ]);

  assert.deepEqual(summary, {
    totalRows: 4,
    plants: ["aodelai", "hehong_huajin"],
    matchedCounts: {
      grid_power_kw: 1,
      battery_power_kw: 0,
      soc: 0,
    },
    unmatchedTableNames: ["未知功率字段A", "未知电量字段B"],
  });
});
