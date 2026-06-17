const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

test("declares MPC host permissions with and without the service port", () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, "manifest.json"), "utf8"));

  assert.ok(manifest.host_permissions.includes("http://8.163.49.151/*"));
  assert.ok(manifest.host_permissions.includes("http://8.163.49.151:18000/*"));
});
