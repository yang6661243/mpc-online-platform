// localDb.js - persistent local storage for collected eCloud rows.

(function(root) {
  "use strict";

  const DB_NAME = "ecloud_collector";
  const DB_VERSION = 1;
  const STORE_NAME = "rows";
  const DEFAULT_LIMIT = 100;

  let dbPromise = null;

  function normalizeText(value) {
    return String(value || "").trim();
  }

  function makeRowKey(row) {
    return `${normalizeText(row?.plantId || row?.stationId || "default")}::${normalizeText(row?.tableName)}::${normalizeText(row?.date)}`;
  }

  function normalizeRow(row, savedAt = new Date().toISOString()) {
    return {
      id: makeRowKey(row),
      plantId: normalizeText(row?.plantId),
      plantName: normalizeText(row?.plantName),
      stationId: normalizeText(row?.stationId),
      tableName: normalizeText(row?.tableName),
      date: normalizeText(row?.date),
      value: normalizeText(row?.value),
      timestamp: normalizeText(row?.timestamp),
      savedAt,
    };
  }

  function normalizeDateInput(value) {
    const text = normalizeText(value);
    if (!text) return "";
    return text.replace("T", " ").slice(0, 19);
  }

  function filterRows(rows, options = {}) {
    const tableName = normalizeText(options.tableName).toLowerCase();
    const plantId = normalizeText(options.plantId).toLowerCase();
    const stationId = normalizeText(options.stationId).toLowerCase();
    const start = normalizeDateInput(options.start);
    const end = normalizeDateInput(options.end);
    const limit = Math.max(1, Number(options.limit || DEFAULT_LIMIT));

    return (rows || [])
      .filter((row) => {
        const rowName = normalizeText(row.tableName).toLowerCase();
        const rowPlantId = normalizeText(row.plantId).toLowerCase();
        const rowStationId = normalizeText(row.stationId).toLowerCase();
        const rowDate = normalizeDateInput(row.date);
        if (plantId && !rowPlantId.includes(plantId)) return false;
        if (stationId && !rowStationId.includes(stationId)) return false;
        if (tableName && !rowName.includes(tableName)) return false;
        if (start && rowDate < start) return false;
        if (end && rowDate > end) return false;
        return true;
      })
      .sort((a, b) => {
        const dateCompare = normalizeDateInput(b.date).localeCompare(normalizeDateInput(a.date));
        if (dateCompare !== 0) return dateCompare;
        return normalizeText(a.tableName).localeCompare(normalizeText(b.tableName));
      })
      .slice(0, limit);
  }

  function requestToPromise(request) {
    return new Promise((resolve, reject) => {
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error || new Error("IndexedDB request failed"));
    });
  }

  function transactionDone(transaction) {
    return new Promise((resolve, reject) => {
      transaction.oncomplete = () => resolve();
      transaction.onerror = () => reject(transaction.error || new Error("IndexedDB transaction failed"));
      transaction.onabort = () => reject(transaction.error || new Error("IndexedDB transaction aborted"));
    });
  }

  function openDb() {
    if (dbPromise) return dbPromise;
    if (!root.indexedDB) {
      return Promise.reject(new Error("当前浏览器不支持 IndexedDB"));
    }

    dbPromise = new Promise((resolve, reject) => {
      const request = root.indexedDB.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains(STORE_NAME)) {
          const store = db.createObjectStore(STORE_NAME, { keyPath: "id" });
          store.createIndex("date", "date", { unique: false });
          store.createIndex("tableName", "tableName", { unique: false });
          store.createIndex("savedAt", "savedAt", { unique: false });
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error || new Error("打开本地数据库失败"));
    });
    return dbPromise;
  }

  async function upsertRows(rows) {
    const normalizedRows = (rows || [])
      .map((row) => normalizeRow(row))
      .filter((row) => row.tableName && row.date);
    if (normalizedRows.length === 0) {
      return { savedCount: 0 };
    }

    const db = await openDb();
    const transaction = db.transaction(STORE_NAME, "readwrite");
    const done = transactionDone(transaction);
    const store = transaction.objectStore(STORE_NAME);
    normalizedRows.forEach((row) => store.put(row));
    await done;
    return { savedCount: normalizedRows.length };
  }

  async function countRows() {
    const db = await openDb();
    const transaction = db.transaction(STORE_NAME, "readonly");
    const done = transactionDone(transaction);
    const count = await requestToPromise(transaction.objectStore(STORE_NAME).count());
    await done;
    return count;
  }

  async function getRows(options = {}) {
    const db = await openDb();
    const transaction = db.transaction(STORE_NAME, "readonly");
    const done = transactionDone(transaction);
    const rows = await requestToPromise(transaction.objectStore(STORE_NAME).getAll());
    await done;
    return filterRows(rows, options);
  }

  async function clearRows() {
    const db = await openDb();
    const transaction = db.transaction(STORE_NAME, "readwrite");
    const done = transactionDone(transaction);
    transaction.objectStore(STORE_NAME).clear();
    await done;
    return true;
  }

  const api = {
    DB_NAME,
    STORE_NAME,
    clearRows,
    countRows,
    filterRows,
    getRows,
    makeRowKey,
    normalizeRow,
    upsertRows,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  root.EcloudLocalDb = api;
})(typeof self !== "undefined" ? self : globalThis);
