// content.js - eCloud 接口监听型数据采集逻辑

(function() {
  "use strict";

  const CONFIG = {
    endpointPath: "/api/business/point/pointDataShowList",
    endpointSuffix: "/business/point/pointDataShowList",
    intervalMs: 60 * 1000,
    rollingWindowDays: 1,
    directApiPageSize: 2000,
    debug: true,
    requiredMetrics: [
      {
        fullName: "计量电表/1352-总有功功率",
        keywords: ["计量电表", "总有功功率"],
      },
      {
        fullName: "1-BMS/系统SOC",
        keywords: ["1-BMS", "系统SOC"],
      },
      {
        fullName: "防逆流电表-ADW300/ADW-总有功功率",
        keywords: ["防逆流电表-ADW300", "总有功功率"],
      },
    ],
    defaultQueryTemplate: {
      url: "/business/point/pointDataShowList",
      payload: {
        stationId: 2289,
        deviceIdList: [
          { srcId: 432000083, cols: ["YC0014"], colNames: ["计量电表/1352-总有功功率"] },
          { srcId: 432000001, cols: ["YC0004"], colNames: ["1-BMS/系统SOC"] },
          { srcId: 432000084, cols: ["YC0014"], colNames: ["防逆流电表-ADW300/ADW-总有功功率"] },
        ],
        sampleTime: "1",
        isOriginal: 0,
        pageNum: 1,
        pageSize: 2000,
      },
    },
  };

  let isCollecting = false;
  let collectIntervalId = null;
  let floatingButton = null;
  let observedQueryTemplate = null;
  let isFirstCollect = true;
  let previousDataKeys = new Set();
  let dataCache = [];
  let lastStatus = "初始化中";
  let lastError = "";
  let lastCollectAt = "";

  function cloneJson(value, fallback = null) {
    try {
      return JSON.parse(JSON.stringify(value));
    } catch (error) {
      return fallback;
    }
  }

  function normalizeText(value) {
    return String(value || "").replace(/\s+/g, " ").trim();
  }

  function formatLogArg(arg) {
    if (arg instanceof Error) return arg.message;
    if (typeof arg === "object" && arg !== null) {
      try {
        return JSON.stringify(arg);
      } catch (error) {
        return String(arg);
      }
    }
    return String(arg);
  }

  function log(...args) {
    if (CONFIG.debug) {
      console.log("[eCloud采集]", ...args);
    }
    setFloatingStatus(args.map(formatLogArg).join(" "));
  }

  function setFloatingStatus(message) {
    lastStatus = String(message || "").slice(0, 160);
    if (!floatingButton) return;
    const status = floatingButton.querySelector(".ecloud-btn-status");
    if (status) status.textContent = lastStatus;
  }

  function setLastError(error) {
    lastError = error ? String(error).slice(0, 200) : "";
  }

  function pad2(value) {
    return String(value).padStart(2, "0");
  }

  function formatDateTime(date) {
    return [
      date.getFullYear(),
      "-",
      pad2(date.getMonth() + 1),
      "-",
      pad2(date.getDate()),
      " ",
      pad2(date.getHours()),
      ":",
      pad2(date.getMinutes()),
      ":00",
    ].join("");
  }

  function getTimeRange() {
    const end = new Date();
    const start = new Date(end.getTime() - CONFIG.rollingWindowDays * 24 * 60 * 60 * 1000);
    return {
      start: formatDateTime(start),
      end: formatDateTime(end),
    };
  }

  function parseChinaDateTimeMs(value) {
    const text = String(value || "").trim();
    const match = text.match(/^(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})(?::(\d{2}))?/);
    if (!match) return NaN;
    return new Date(
      Number(match[1]),
      Number(match[2]) - 1,
      Number(match[3]),
      Number(match[4]),
      Number(match[5]),
      Number(match[6] || 0),
    ).getTime();
  }

  function metricMatches(metric, text) {
    const normalized = normalizeText(text);
    return metric.keywords.every((keyword) => normalized.includes(keyword));
  }

  function rowMatchesRequiredMetric(tableName) {
    return CONFIG.requiredMetrics.some((metric) => metricMatches(metric, tableName));
  }

  function getSelectedMetricText() {
    const inputs = Array.from(document.querySelectorAll('input[placeholder="请选择指标"]'));
    const metricInput = inputs.find((input) => String(input.value || "").trim().length > 0) || inputs[0];
    return String(metricInput?.value || "");
  }

  function getMissingRequiredMetrics(selectedText = getSelectedMetricText()) {
    return CONFIG.requiredMetrics.filter((metric) => !metricMatches(metric, selectedText));
  }

  function getTemplateMetricText(payload) {
    const parts = [];
    const deviceIdList = Array.isArray(payload?.deviceIdList) ? payload.deviceIdList : [];
    deviceIdList.forEach((item) => {
      if (Array.isArray(item?.colNames)) {
        parts.push(...item.colNames);
      }
      if (item?.colName) {
        parts.push(item.colName);
      }
      if (item?.name) {
        parts.push(item.name);
      }
    });
    if (payload?.allColName) parts.push(payload.allColName);
    return parts.join(",");
  }

  function validateObservedTemplate(payload) {
    if (!payload || typeof payload !== "object") {
      throw new Error("接口模板不是有效对象");
    }
    if (!payload.stationId) {
      throw new Error("接口模板缺少 stationId");
    }
    if (!Array.isArray(payload.deviceIdList) || payload.deviceIdList.length === 0) {
      throw new Error("接口模板缺少 deviceIdList");
    }

    const metricText = [getTemplateMetricText(payload), getSelectedMetricText()].filter(Boolean).join(",");
    if (metricText) {
      const missing = getMissingRequiredMetrics(metricText);
      if (missing.length > 0) {
        throw new Error(`缺少必需指标: ${missing.map((metric) => metric.fullName).join(", ")}`);
      }
    }
  }

  function isPointDataShowListUrl(url) {
    return String(url || "").includes(CONFIG.endpointSuffix);
  }

  function observeEcloudQueryTemplate(message) {
    if (!message || !isPointDataShowListUrl(message.url)) {
      return null;
    }

    const payload = cloneJson(message.payload);
    validateObservedTemplate(payload);
    observedQueryTemplate = {
      url: message.url,
      payload,
      observedAt: new Date().toISOString(),
    };
    setLastError("");
    log("已记录eCloud接口查询模板");
    return cloneJson(observedQueryTemplate);
  }

  function buildObservedQueryPayload(timeRange = getTimeRange()) {
    const queryTemplate = observedQueryTemplate || CONFIG.defaultQueryTemplate;

    const payload = cloneJson(queryTemplate.payload, {});
    payload.beginTime = timeRange.start;
    payload.endTime = timeRange.end;
    payload.pageNum = 1;
    payload.pageSize = CONFIG.directApiPageSize;
    return payload;
  }

  function normalizeDeviceIdList(deviceIdList) {
    if (!Array.isArray(deviceIdList)) return [];
    return deviceIdList
      .map((item) => ({
        srcId: item?.srcId,
        cols: Array.isArray(item?.cols) ? item.cols.slice() : [],
        colNames: Array.isArray(item?.colNames) ? item.colNames.slice() : [],
      }))
      .filter((item) => item.srcId !== undefined && item.cols.length > 0);
  }

  function buildDeviceIdListFromDefaultTable(defaultTable) {
    if (!Array.isArray(defaultTable)) return [];

    const grouped = new Map();
    defaultTable.forEach((item) => {
      if (!item || item.srcId === undefined || item.col === undefined) return;
      const key = String(item.srcId);
      const current = grouped.get(key) || { srcId: item.srcId, cols: [], colNames: [] };
      current.cols.push(item.col);
      current.colNames.push(item.colName || item.col);
      grouped.set(key, current);
    });
    return Array.from(grouped.values());
  }

  function getRuntimeDeviceIdList(runtimeState) {
    const resultList = normalizeDeviceIdList(runtimeState?.result?.deviceIdList);
    if (resultList.length > 0) return resultList;
    return buildDeviceIdListFromDefaultTable(runtimeState?.defaultTable);
  }

  function buildDirectQueryPayload(runtimeState, timeRange) {
    const deviceIdList = getRuntimeDeviceIdList(runtimeState);
    const stationId = runtimeState?.result?.stationId || runtimeState?.filters?.stationId || runtimeState?.stationId;
    const sampleTime = String(runtimeState?.filters?.sampleTime || "1");
    return {
      stationId,
      deviceIdList,
      beginTime: timeRange.start,
      endTime: timeRange.end,
      sampleTime,
      isOriginal: runtimeState?.isOriginal ? 1 : 0,
      pageNum: 1,
      pageSize: CONFIG.directApiPageSize,
    };
  }

  function normalizePointDataShowListRows(apiData, timestamp = new Date().toISOString()) {
    const rows = [];
    const cards = Array.isArray(apiData) ? apiData : [];

    cards.forEach((card) => {
      const list = Array.isArray(card?.list) ? card.list : [];
      list.forEach((item) => {
        const tableName = item.row_name_digital0 || item.colName || item.col0 || "";
        const date = item.date0 || item.row_name_date0 || item.time || "";
        const value = item.digital0 ?? item.row_digital0 ?? item.value ?? "";
        if (!tableName || !date || value === "") return;
        if (!rowMatchesRequiredMetric(tableName)) return;
        rows.push({
          tableName,
          date,
          value: String(value),
          timestamp,
        });
      });
    });
    return rows;
  }

  function dedupeRows(rows) {
    const newRows = [];
    (rows || []).forEach((row) => {
      const key = `${row.tableName}:${row.date}`;
      if (previousDataKeys.has(key)) return;
      previousDataKeys.add(key);
      newRows.push(row);
    });
    return newRows;
  }

  function getCookieValue(name) {
    const prefix = `${name}=`;
    const cookie = String(document.cookie || "")
      .split(";")
      .map((part) => part.trim())
      .find((part) => part.startsWith(prefix));
    return cookie ? decodeURIComponent(cookie.slice(prefix.length)) : "";
  }

  function getEcloudAuthToken() {
    try {
      return window.localStorage?.getItem("USER_TOKEN") || getCookieValue("token") || "";
    } catch (error) {
      return "";
    }
  }

  function getEcloudLangHeader() {
    try {
      switch (window.sessionStorage?.getItem("language")) {
        case "en":
          return "en_US";
        case "vi":
          return "vi_VN";
        case "zh":
        default:
          return "zh_CN";
      }
    } catch (error) {
      return "zh_CN";
    }
  }

  async function queryPointDataShowList() {
    const payload = buildObservedQueryPayload();
    const headers = {
      Accept: "application/json",
      "Content-Type": "application/json",
      lang: getEcloudLangHeader(),
      timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    };
    const token = getEcloudAuthToken();
    if (token) headers.authorization = token;

    const response = await fetch(`${window.location.origin}${CONFIG.endpointPath}`, {
      method: "POST",
      headers,
      credentials: "include",
      body: JSON.stringify(payload),
    });

    const bodyText = await response.text();
    let body = {};
    if (bodyText) {
      try {
        body = JSON.parse(bodyText);
      } catch (error) {
        throw new Error(`eCloud接口返回非JSON: ${bodyText.slice(0, 120)}`);
      }
    }
    if (!response.ok) {
      throw new Error(`eCloud接口失败 ${response.status}: ${bodyText || response.statusText}`);
    }
    if (body.code !== undefined && body.code !== 200) {
      throw new Error(`eCloud接口业务失败 ${body.code}: ${body.msg || body.message || ""}`);
    }
    return normalizePointDataShowListRows(body.data);
  }

  function saveCollectedRows(rows) {
    if (!rows || rows.length === 0) {
      log("本轮没有新增数据");
      return true;
    }

    dataCache.push({
      timestamp: new Date().toISOString(),
      dataCount: rows.length,
      data: rows,
    });

    chrome.runtime.sendMessage(
      {
        action: "saveData",
        data: rows,
        isFirst: isFirstCollect,
        timestamp: new Date().toISOString(),
      },
      (response) => {
        if (response && response.success) {
          if (isFirstCollect) isFirstCollect = false;
          if (response.mpcError) {
            setLastError(response.mpcError);
            log(`后台已保存，MPC推送异常: ${response.mpcError}`);
          } else {
            log(`数据已发送到后台保存: ${rows.length} 条`);
          }
        } else {
          const error = response?.error || "未知错误";
          setLastError(error);
          log(`数据发送失败: ${error}`);
        }
      },
    );

    return true;
  }

  async function collectData() {
    try {
      if (!observedQueryTemplate) {
        log("未捕获页面模板，使用默认eCloud点位模板");
      }

      log("开始通过eCloud接口采集最近1天数据");
      const rows = await queryPointDataShowList();
      const newRows = dedupeRows(rows);
      lastCollectAt = new Date().toISOString();
      log(`接口返回 ${rows.length} 条目标数据，新增 ${newRows.length} 条`);
      saveCollectedRows(newRows);
      return true;
    } catch (error) {
      const message = error && error.message ? error.message : String(error);
      setLastError(message);
      log(`数据采集失败: ${message}`);
      return false;
    }
  }

  function updateButtonState() {
    if (!floatingButton) return;
    const btnMain = floatingButton.querySelector(".ecloud-btn-main");
    const btnText = floatingButton.querySelector(".ecloud-btn-text");
    if (!btnMain || !btnText) return;

    if (isCollecting) {
      btnMain.classList.add("collecting");
      btnMain.title = "停止采集";
      btnText.textContent = "停止采集";
    } else {
      btnMain.classList.remove("collecting");
      btnMain.title = "开始采集";
      btnText.textContent = "开始采集";
    }
  }

  function startCollection() {
    if (isCollecting) {
      log("采集已在运行中");
      return;
    }
    isCollecting = true;
    updateButtonState();
    log("开始定时采集");
    collectData();
    collectIntervalId = setInterval(collectData, CONFIG.intervalMs);
  }

  function stopCollection() {
    if (!isCollecting) {
      log("采集未运行");
      return;
    }
    isCollecting = false;
    if (collectIntervalId) {
      clearInterval(collectIntervalId);
      collectIntervalId = null;
    }
    updateButtonState();
    log("已停止采集");
  }

  function injectPageHook() {
    if (window.__ECLOUD_COLLECTOR_TEST__) return;
    if (!chrome?.runtime?.getURL) return;
    if (document.getElementById("ecloud-point-query-hook")) return;

    const script = document.createElement("script");
    script.id = "ecloud-point-query-hook";
    script.src = chrome.runtime.getURL("page-hook.js");
    script.onload = () => script.remove();
    (document.documentElement || document.head || document.body).appendChild(script);
  }

  function onPageMessage(event) {
    if (event.source !== window) return;
    const data = event.data || {};
    if (data.type !== "ECLOUD_POINT_QUERY_CAPTURED") return;

    try {
      observeEcloudQueryTemplate(data);
      if (isCollecting) {
        collectData();
      }
    } catch (error) {
      const message = error && error.message ? error.message : String(error);
      setLastError(message);
      log(`记录查询模板失败: ${message}`);
    }
  }

  function createFloatingButton() {
    if (floatingButton || !document.body) return;

    floatingButton = document.createElement("div");
    floatingButton.id = "ecloud-collector-floating";
    floatingButton.innerHTML = `
      <style>
        #ecloud-collector-floating {
          position: fixed;
          left: 18px;
          top: 96px;
          z-index: 2147483647;
          font-family: Arial, "Microsoft YaHei", sans-serif;
        }
        #ecloud-collector-floating .ecloud-btn-main {
          display: flex;
          align-items: center;
          gap: 6px;
          border: none;
          border-radius: 6px;
          padding: 8px 12px;
          background: #0b5cad;
          color: #fff;
          font-size: 13px;
          cursor: pointer;
          box-shadow: 0 4px 14px rgba(0,0,0,0.18);
        }
        #ecloud-collector-floating .ecloud-btn-main.collecting {
          background: #b42318;
        }
        #ecloud-collector-floating .ecloud-btn-status {
          margin-top: 6px;
          width: 280px;
          min-height: 18px;
          border-radius: 4px;
          padding: 6px 8px;
          background: rgba(255,255,255,0.96);
          color: #1f2937;
          font-size: 12px;
          line-height: 1.45;
          box-shadow: 0 4px 14px rgba(0,0,0,0.12);
          word-break: break-all;
        }
      </style>
      <button class="ecloud-btn-main" title="开始采集">
        <span class="ecloud-btn-icon">MPC</span>
        <span class="ecloud-btn-text">开始采集</span>
      </button>
      <div class="ecloud-btn-status">初始化完成，等待页面查询模板</div>
    `;
    document.body.appendChild(floatingButton);
    floatingButton.querySelector(".ecloud-btn-main")?.addEventListener("click", () => {
      if (isCollecting) {
        stopCollection();
      } else {
        startCollection();
      }
    });
  }

  function uniqueElements(elements) {
    return Array.from(new Set(elements.filter(Boolean)));
  }

  function isLikelyMetricTitle(text) {
    const normalized = normalizeText(text);
    if (!normalized || normalized.length > 80) return false;
    if (["日期", "值", "删除", "前往", "页"].includes(normalized)) return false;
    if (/^\d+(?:\.\d+)?$/.test(normalized)) return false;
    return (
      normalized.includes("/") ||
      /soc/i.test(normalized) ||
      /bms/i.test(normalized) ||
      /adw/i.test(normalized) ||
      normalized.includes("功率") ||
      normalized.includes("电表") ||
      normalized.includes("储能") ||
      normalized.includes("防逆流")
    );
  }

  function extractMetricTitle(card, fallbackIndex) {
    const headerTitle = card.querySelector(".el-card__header span")?.textContent;
    if (isLikelyMetricTitle(headerTitle)) return normalizeText(headerTitle);

    const textElements = Array.from(card.querySelectorAll("span, div, p"));
    const titleElement = textElements.find((element) => isLikelyMetricTitle(element.textContent));
    if (titleElement) return normalizeText(titleElement.textContent);
    return `未知表格${fallbackIndex + 1}`;
  }

  function findTableContainerForBody(tableBody) {
    let node = tableBody.parentElement;
    let fallback = node || tableBody;
    let depth = 0;
    while (node && node !== document.body && depth < 8) {
      if (typeof node.querySelector === "function" && node.querySelector(".el-table__body")) {
        fallback = node;
        if (!extractMetricTitle(node, 0).startsWith("未知表格")) return node;
      }
      node = node.parentElement;
      depth += 1;
    }
    return fallback;
  }

  function getTableCards() {
    const configuredCards = Array.from(document.querySelectorAll(".el-card.card-item"));
    if (configuredCards.length > 0) return configuredCards;
    const tableBodies = Array.from(document.querySelectorAll(".el-table__body"));
    return uniqueElements(tableBodies.map(findTableContainerForBody));
  }

  function extractDataFromTables() {
    const rows = [];
    const tableCards = getTableCards();
    tableCards.forEach((card, cardIndex) => {
      const tableName = extractMetricTitle(card, cardIndex);
      const tableBody = card.querySelector(".el-table__body");
      if (!tableBody) return;
      const headerCells = Array.from(card.querySelectorAll(".el-table__header th .cell"));
      const dateColumnIndex = headerCells.findIndex((cell) => normalizeText(cell.textContent) === "日期");
      const valueColumnIndex = headerCells.findIndex((cell) => normalizeText(cell.textContent) === "值");
      if (dateColumnIndex < 0 || valueColumnIndex < 0) return;
      let tableRows = Array.from(tableBody.querySelectorAll("tbody tr.el-table__row"));
      if (tableRows.length === 0) {
        tableRows = Array.from(tableBody.querySelectorAll("tbody tr"));
      }
      tableRows.forEach((row) => {
        const cells = Array.from(row.querySelectorAll("td .cell"));
        if (cells.length <= Math.max(dateColumnIndex, valueColumnIndex)) return;
        const date = normalizeText(cells[dateColumnIndex].textContent);
        const value = normalizeText(cells[valueColumnIndex].textContent);
        if (!date || !value) return;
        rows.push({
          tableName,
          date,
          value,
          timestamp: new Date().toISOString(),
        });
      });
    });
    return rows;
  }

  function triggerEvent(element, eventType) {
    element.dispatchEvent(new Event(eventType, { bubbles: true }));
  }

  function setNativeInputValue(element, value) {
    const prototype = Object.getPrototypeOf(element);
    const descriptor = Object.getOwnPropertyDescriptor(prototype, "value");
    if (descriptor && typeof descriptor.set === "function") {
      descriptor.set.call(element, value);
    } else {
      element.value = value;
    }
  }

  async function fillVisibleTimeInput(element, value) {
    if (element.removeAttribute) element.removeAttribute("readonly");
    setNativeInputValue(element, value);
    triggerEvent(element, "input");
    triggerEvent(element, "change");
    await new Promise((resolve) => setTimeout(resolve, 100));
  }

  async function fillVisibleTimeRangeInputs(timeRange) {
    const picker = document.querySelector(".el-date-editor--datetimerange");
    if (!picker) return false;
    const inputs = Array.from(picker.querySelectorAll("input"));
    if (inputs.length < 2) return false;
    await fillVisibleTimeInput(inputs[0], timeRange.start);
    await fillVisibleTimeInput(inputs[1], timeRange.end);
    if (document.body?.click) document.body.click();
    const startValue = String(inputs[0].value || "").trim();
    const endValue = String(inputs[1].value || "").trim();
    return startValue.length > 0 && endValue.length > 0;
  }

  function hasValidVisibleTimeRange() {
    const picker = document.querySelector(".el-date-editor--datetimerange");
    if (!picker) return false;
    const inputs = Array.from(picker.querySelectorAll("input"));
    if (inputs.length < 2) return false;
    const startMs = parseChinaDateTimeMs(inputs[0].value);
    const endMs = parseChinaDateTimeMs(inputs[1].value);
    return Number.isFinite(startMs) && Number.isFinite(endMs) && endMs > startMs;
  }

  function init() {
    if (!window.location.href.includes("ecloud.hoenergypower.cn")) {
      if (window.__ECLOUD_COLLECTOR_TEST__) {
        exposeTestHooks();
      }
      return;
    }

    injectPageHook();
    window.addEventListener("message", onPageMessage);
    createFloatingButton();
    chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
      if (request.action === "startCollection") {
        startCollection();
        sendResponse({ success: true });
        return false;
      }
      if (request.action === "stopCollection") {
        stopCollection();
        sendResponse({ success: true });
        return false;
      }
      if (request.action === "getStatus") {
        sendResponse({
          isCollecting,
          dataCount: dataCache.reduce((sum, batch) => sum + batch.dataCount, 0),
          hasQueryTemplate: Boolean(observedQueryTemplate),
          lastStatus,
          lastError,
          lastCollectAt,
        });
        return false;
      }
      if (request.action === "clearData") {
        previousDataKeys.clear();
        dataCache = [];
        isFirstCollect = true;
        setLastError("");
        log("已清空本地采集缓存");
        sendResponse({ success: true });
        return false;
      }
      return false;
    });
    log("初始化完成，等待页面查询模板");
  }

  function exposeTestHooks() {
    window.__ecloudCollectorTestHooks = {
      buildDirectQueryPayload,
      buildObservedQueryPayload,
      collectData,
      dedupeRows,
      extractDataFromTables,
      fillVisibleTimeRangeInputs,
      getMissingRequiredMetrics,
      getTimeRange,
      hasValidVisibleTimeRange,
      normalizePointDataShowListRows,
      observeEcloudQueryTemplate,
      rowMatchesRequiredMetric,
    };
  }

  if (window.__ECLOUD_COLLECTOR_TEST__) {
    exposeTestHooks();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
