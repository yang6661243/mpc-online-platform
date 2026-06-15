const assert = require("node:assert/strict");
const test = require("node:test");

const LocalDb = require("./localDb.js");

test("normalizes collected rows with deterministic ids for local storage", () => {
  const row = LocalDb.normalizeRow(
    {
      tableName: "3-BMS/BMS-系统SOC",
      date: "2026-06-15 09:30:00.01",
      value: "61.2",
      timestamp: "2026-06-15T01:30:02.000Z",
    },
    "2026-06-15T01:30:03.000Z",
  );

  assert.deepEqual(row, {
    id: "default::3-BMS/BMS-系统SOC::2026-06-15 09:30:00.01",
    plantId: "",
    plantName: "",
    stationId: "",
    tableName: "3-BMS/BMS-系统SOC",
    date: "2026-06-15 09:30:00.01",
    value: "61.2",
    timestamp: "2026-06-15T01:30:02.000Z",
    savedAt: "2026-06-15T01:30:03.000Z",
  });
});

test("includes plant identity in local row ids", () => {
  const row = LocalDb.normalizeRow({
    plantId: "hehong_huajin",
    plantName: "和宏华进",
    stationId: "1188",
    tableName: "3-BMS/BMS-系统SOC",
    date: "2026-06-15 09:30:00.01",
    value: "61.2",
  });

  assert.equal(row.id, "hehong_huajin::3-BMS/BMS-系统SOC::2026-06-15 09:30:00.01");
  assert.equal(row.plantName, "和宏华进");
  assert.equal(row.stationId, "1188");
});

test("filters local rows by table name and source time descending", () => {
  const rows = [
    LocalDb.normalizeRow({ tableName: "计量电表/总有功功率", date: "2026-06-15 09:20:00", value: "10" }, "s1"),
    LocalDb.normalizeRow({ tableName: "3-BMS/BMS-系统SOC", date: "2026-06-15 09:25:00", value: "60" }, "s2"),
    LocalDb.normalizeRow({ tableName: "3-BMS/BMS-系统SOC", date: "2026-06-15 09:30:00", value: "61" }, "s3"),
  ];

  const result = LocalDb.filterRows(rows, {
    tableName: "soc",
    start: "2026-06-15 09:24:00",
    end: "2026-06-15 09:30:00",
    limit: 1,
  });

  assert.deepEqual(result.map((row) => [row.tableName, row.date, row.value]), [
    ["3-BMS/BMS-系统SOC", "2026-06-15 09:30:00", "61"],
  ]);
});
