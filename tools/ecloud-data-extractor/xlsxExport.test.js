const assert = require("node:assert/strict");
const test = require("node:test");

const XlsxExport = require("./xlsxExport.js");

test("builds an xlsx zip with worksheet content for collected rows", () => {
  const bytes = XlsxExport.generateXlsxBytes([
    {
      timestamp: "2026-06-15T01:30:02.000Z",
      tableName: "3-BMS/BMS-系统SOC",
      date: "2026-06-15 09:30:00.01",
      value: "61.2",
      savedAt: "2026-06-15T01:30:03.000Z",
    },
  ]);
  const text = Buffer.from(bytes).toString("utf8");

  assert.equal(Buffer.from(bytes.subarray(0, 2)).toString("utf8"), "PK");
  assert.match(text, /xl\/worksheets\/sheet1\.xml/);
  assert.match(text, /采集时间/);
  assert.match(text, /3-BMS\/BMS-系统SOC/);
  assert.match(text, /2026-06-15 09:30:00\.01/);
});

test("builds a downloadable xlsx data url", () => {
  const url = XlsxExport.generateXlsxDataUrl([
    { tableName: "计量电表/总有功功率", date: "2026-06-15 09:31:00", value: "12.3" },
  ]);

  assert.match(url, /^data:application\/vnd\.openxmlformats-officedocument\.spreadsheetml\.sheet;base64,/);
});
