const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

class FakeElement {
  constructor(text = "", selectors = {}) {
    this.textContent = text;
    this.selectors = selectors;
    this.parentElement = null;
  }

  querySelectorAll(selector) {
    return this.selectors[selector] || [];
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }
}

function linkParent(parent, children) {
  for (const child of children) {
    child.parentElement = parent;
  }
}

function makeMetricSection(title, rows) {
  const titleElement = new FakeElement(title);
  const headerDate = new FakeElement("日期");
  const headerValue = new FakeElement("值");
  const bodyRows = rows.map(([date, value]) => {
    const dateCell = new FakeElement(date);
    const valueCell = new FakeElement(String(value));
    const row = new FakeElement("", {
      "td .cell": [dateCell, valueCell],
    });
    linkParent(row, [dateCell, valueCell]);
    return row;
  });

  const tableBody = new FakeElement("", {
    "tbody tr.el-table__row": bodyRows,
    "tbody tr": bodyRows,
  });
  linkParent(tableBody, bodyRows);

  const section = new FakeElement(title, {
    ".el-card__header span": [],
    ".el-table__body": [tableBody],
    ".el-table__header th .cell": [headerDate, headerValue],
    "span, div, p": [titleElement],
  });
  linkParent(section, [titleElement, tableBody, headerDate, headerValue]);
  tableBody.parentElement = section;
  return { section, tableBody };
}

function loadContentWithDocument(document) {
  const source = fs.readFileSync(path.join(__dirname, "content.js"), "utf8");
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

test("extractDataFromTables reads current eCloud metric sections without el-card classes", () => {
  const soc = makeMetricSection("1-BMS/系统SOC", [["2026-06-13 00:20:00.01", 11]]);
  const battery = makeMetricSection("计量电表/1352-总有功功率", [["2026-06-13 00:20:00.01", 50.6]]);
  const grid = makeMetricSection("防逆流电表-ADW300/ADW-总有功功率", [["2026-06-13 00:20:00.01", 85.6]]);
  const tableBodies = [soc.tableBody, battery.tableBody, grid.tableBody];

  const document = {
    readyState: "complete",
    querySelector(selector) {
      return this.querySelectorAll(selector)[0] || null;
    },
    querySelectorAll(selector) {
      if (selector === ".el-card.card-item") return [];
      if (selector === ".el-table__body") return tableBodies;
      return [];
    },
    addEventListener() {},
    body: { click() {} },
  };

  const hooks = loadContentWithDocument(document);
  assert.equal(typeof hooks.extractDataFromTables, "function");

  const rows = hooks.extractDataFromTables();

  const normalizedRows = JSON.parse(JSON.stringify(rows.map((row) => [row.tableName, row.date, row.value])));

  assert.deepEqual(
    normalizedRows,
    [
      ["1-BMS/系统SOC", "2026-06-13 00:20:00.01", "11"],
      ["计量电表/1352-总有功功率", "2026-06-13 00:20:00.01", "50.6"],
      ["防逆流电表-ADW300/ADW-总有功功率", "2026-06-13 00:20:00.01", "85.6"],
    ],
  );
});
