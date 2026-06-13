// mpcBridge.js - eCloud table rows to online MPC API payloads.

(function(root) {
  "use strict";

  const DEFAULT_CONFIG = {
    plantId: "ecloud_factory",
    mpcBaseUrl: "http://8.163.49.151:18000",
    aggregateWindowMinutes: 15,
  };

  function pad2(value) {
    return String(value).padStart(2, "0");
  }

  function parseChinaTimestamp(value) {
    const text = String(value || "").trim();
    const match = text.match(
      /^(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:[ T](\d{1,2}):(\d{1,2})(?::(\d{1,2}))?)?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?$/
    );
    if (!match) {
      throw new Error(`无法解析时间: ${value}`);
    }
    return {
      year: Number(match[1]),
      month: Number(match[2]),
      day: Number(match[3]),
      hour: Number(match[4] || 0),
      minute: Number(match[5] || 0),
      second: Number(match[6] || 0),
    };
  }

  function timestampPartsToChinaIso(parts) {
    return [
      String(parts.year).padStart(4, "0"),
      "-",
      pad2(parts.month),
      "-",
      pad2(parts.day),
      "T",
      pad2(parts.hour),
      ":",
      pad2(parts.minute),
      ":",
      pad2(parts.second),
      "+08:00",
    ].join("");
  }

  function toChinaIsoTimestamp(value) {
    return timestampPartsToChinaIso(parseChinaTimestamp(value));
  }

  function partsToChinaEpochMs(parts) {
    return Date.UTC(parts.year, parts.month - 1, parts.day, parts.hour - 8, parts.minute, parts.second);
  }

  function chinaEpochMsToParts(epochMs) {
    const shifted = new Date(epochMs + 8 * 60 * 60 * 1000);
    return {
      year: shifted.getUTCFullYear(),
      month: shifted.getUTCMonth() + 1,
      day: shifted.getUTCDate(),
      hour: shifted.getUTCHours(),
      minute: shifted.getUTCMinutes(),
      second: shifted.getUTCSeconds(),
    };
  }

  function classifyTableName(tableName) {
    const text = String(tableName || "").trim();
    const lower = text.toLowerCase();
    if (!text) return null;
    if (lower.includes("soc") || text.includes("荷电")) return "soc";
    if (text.includes("计量电表") && text.includes("总有功功率")) {
      return "battery_power_kw";
    }
    if ((text.includes("储能") || text.includes("电池")) && text.includes("功率")) {
      return "battery_power_kw";
    }
    if (text.includes("防逆流") || lower.includes("adw") || text.includes("电网")) {
      return "grid_power_kw";
    }
    return null;
  }

  function toFiniteNumber(value) {
    if (typeof value === "number") {
      if (Number.isFinite(value)) return value;
      throw new Error(`数值无效: ${value}`);
    }
    const text = String(value || "").replace(/,/g, "").trim();
    const match = text.match(/[-+]?\d+(?:\.\d+)?/);
    if (!match) {
      throw new Error(`无法解析数值: ${value}`);
    }
    const number = Number(match[0]);
    if (!Number.isFinite(number)) {
      throw new Error(`数值无效: ${value}`);
    }
    return number;
  }

  function makeRequestId(prefix, plantId, generatedAt) {
    const compactTime = String(generatedAt)
      .replace(/[^0-9]/g, "")
      .slice(0, 14);
    return `${prefix}_${plantId}_${compactTime}`;
  }

  function buildMpcIngestPayloads(rows, options = {}) {
    const plantId = options.plantId || DEFAULT_CONFIG.plantId;
    const generatedAt = options.generatedAt || toChinaIsoTimestamp(new Date().toISOString());
    const gridRecords = [];
    const batteryByTime = new Map();

    for (const row of rows || []) {
      const field = classifyTableName(row.tableName);
      if (!field) continue;

      const time = toChinaIsoTimestamp(row.date);
      const value = toFiniteNumber(row.value);

      if (field === "grid_power_kw") {
        gridRecords.push({ time, grid_power_kw: value });
        continue;
      }

      const batteryRow = batteryByTime.get(time) || { time };
      batteryRow[field] = value;
      batteryByTime.set(time, batteryRow);
    }

    const payloads = [];
    if (gridRecords.length > 0) {
      payloads.push({
        request_id: makeRequestId("ecloud_grid", plantId, generatedAt),
        plant_id: plantId,
        data_type: "grid_meter",
        generated_at: generatedAt,
        records: gridRecords,
      });
    }

    const batteryRecords = Array.from(batteryByTime.values())
      .filter((record) => record.battery_power_kw !== undefined && record.soc !== undefined)
      .sort((a, b) => a.time.localeCompare(b.time));
    if (batteryRecords.length > 0) {
      payloads.push({
        request_id: makeRequestId("ecloud_battery", plantId, generatedAt),
        plant_id: plantId,
        data_type: "battery",
        generated_at: generatedAt,
        soc_unit: "percent",
        records: batteryRecords,
      });
    }

    return payloads;
  }

  function buildAggregateWindow(rows, windowMinutes = DEFAULT_CONFIG.aggregateWindowMinutes) {
    const timestamps = [];
    for (const row of rows || []) {
      const field = classifyTableName(row.tableName);
      if (!field) continue;
      timestamps.push(partsToChinaEpochMs(parseChinaTimestamp(row.date)));
    }
    if (timestamps.length === 0) return null;

    const windowMs = Number(windowMinutes) * 60 * 1000;
    const minTs = Math.min(...timestamps);
    const maxTs = Math.max(...timestamps);
    const start = Math.floor(minTs / windowMs) * windowMs;
    const end = Math.ceil((maxTs + 1) / windowMs) * windowMs;

    return {
      start_time: timestampPartsToChinaIso(chinaEpochMsToParts(start)),
      end_time: timestampPartsToChinaIso(chinaEpochMsToParts(end)),
      window_minutes: Number(windowMinutes),
    };
  }

  const api = {
    DEFAULT_CONFIG,
    buildAggregateWindow,
    buildMpcIngestPayloads,
    classifyTableName,
    toChinaIsoTimestamp,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  root.MpcBridge = api;
})(typeof self !== "undefined" ? self : globalThis);
