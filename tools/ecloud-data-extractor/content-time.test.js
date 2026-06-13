const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

class FakeEvent {
  constructor(type, options = {}) {
    this.type = type;
    this.bubbles = Boolean(options.bubbles);
  }
}

class FakeInput {
  constructor() {
    this.value = "";
    this.events = [];
    this.attributes = new Set(["readonly"]);
  }

  focus() {
    this.events.push("focus-call");
  }

  blur() {
    this.events.push("blur-call");
  }

  removeAttribute(name) {
    this.attributes.delete(name);
  }

  dispatchEvent(event) {
    this.events.push(event.type);
  }
}

function loadContentWithDocument(document, overrides = {}) {
  const source = fs.readFileSync(path.join(__dirname, "content.js"), "utf8");
  const context = {
    console,
    document,
    Event: FakeEvent,
    Date: overrides.Date || Date,
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

test("fillVisibleTimeRangeInputs writes visible Element UI range inputs without opening the picker", async () => {
  const startInput = new FakeInput();
  const endInput = new FakeInput();
  const picker = {
    querySelectorAll(selector) {
      assert.equal(selector, "input");
      return [startInput, endInput];
    },
  };
  const document = {
    readyState: "complete",
    querySelector(selector) {
      return selector === ".el-date-editor--datetimerange" ? picker : null;
    },
    addEventListener() {},
    body: { click() {} },
  };

  const hooks = loadContentWithDocument(document);
  assert.equal(typeof hooks.fillVisibleTimeRangeInputs, "function");

  const ok = await hooks.fillVisibleTimeRangeInputs({
    start: "2026-06-12 22:58:00",
    end: "2026-06-12 23:58:00",
  });

  assert.equal(ok, true);
  assert.equal(startInput.value, "2026-06-12 22:58:00");
  assert.equal(endInput.value, "2026-06-12 23:58:00");
  assert.deepEqual(
    startInput.events.filter((event) => ["focus-call", "blur-call"].includes(event)),
    [],
  );
  assert.deepEqual(
    endInput.events.filter((event) => ["focus-call", "blur-call"].includes(event)),
    [],
  );
  assert.deepEqual(
    startInput.events.filter((event) => ["input", "change", "blur"].includes(event)),
    ["input", "change"],
  );
  assert.deepEqual(
    endInput.events.filter((event) => ["input", "change", "blur"].includes(event)),
    ["input", "change"],
  );
}
);

test("getTimeRange uses a wide enough window for delayed eCloud telemetry", () => {
  const RealDate = Date;
  class FixedDate extends RealDate {
    constructor(...args) {
      if (args.length > 0) {
        super(...args);
        return;
      }
      super(2026, 5, 13, 0, 47, 30);
    }

    static now() {
      return new FixedDate().getTime();
    }
  }

  const document = {
    readyState: "complete",
    querySelector() {
      return null;
    },
    addEventListener() {},
    body: { click() {} },
  };

  const hooks = loadContentWithDocument(document, { Date: FixedDate });
  assert.equal(typeof hooks.getTimeRange, "function");

  assert.deepEqual(JSON.parse(JSON.stringify(hooks.getTimeRange())), {
    start: "2026-06-12 00:47:00",
    end: "2026-06-13 00:47:00",
  });
});

test("detects an existing valid visible time range", () => {
  const picker = {
    querySelectorAll(selector) {
      assert.equal(selector, "input");
      return [
        { value: "2026-06-13 00:37:00" },
        { value: "2026-06-13 00:47:00" },
      ];
    },
  };
  const document = {
    readyState: "complete",
    querySelector(selector) {
      return selector === ".el-date-editor--datetimerange" ? picker : null;
    },
    addEventListener() {},
    body: { click() {} },
  };

  const hooks = loadContentWithDocument(document);
  assert.equal(typeof hooks.hasValidVisibleTimeRange, "function");
  assert.equal(hooks.hasValidVisibleTimeRange(), true);
});
