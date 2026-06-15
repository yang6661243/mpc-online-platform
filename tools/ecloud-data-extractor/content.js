// content.js - eCloud 接口监听型数据采集逻辑

(function() {
  "use strict";

  const CONFIG = {
    endpointPath: "/api/business/point/pointDataShowList",
    endpointSuffix: "/business/point/pointDataShowList",
    intervalMs: 60 * 1000,
    rollingWindowMinutes: 10,
    queryResultWaitMs: 1800,
    directApiPageSize: 2000,
    knownStationPlantIds: {
      "1188": "hehong_huajin",
    },
    debug: true,
    requiredMetrics: [
      {
        fullName: "计量电表/总有功功率",
        role: "battery_power_kw",
        aliases: [
          ["计量电表", "总有功功率"],
          ["计量电表", "有功功率pt"],
          ["储能", "功率"],
          ["电池", "功率"],
        ],
      },
      {
        fullName: "3-BMS/BMS-系统SOC",
        role: "soc",
        aliases: [
          ["系统soc"],
          ["bms", "soc"],
          ["储能", "soc"],
          ["电池", "soc"],
        ],
      },
      {
        fullName: "防逆流电表/ADW-总有功功率",
        role: "grid_power_kw",
        aliases: [
          ["防逆流", "总有功功率"],
          ["防逆流", "合相有功功率pt"],
          ["adw", "总有功功率"],
          ["电网", "功率"],
        ],
      },
    ],
  };

  let isCollecting = false;
  let collectIntervalId = null;
  let staggerTimeoutIds = [];
  let floatingButton = null;
  let isLearning = false;
  let observedQueryTemplates = [];
  let collectInFlightTemplateKeys = new Set();
  let isFirstCollect = true;
  let previousDataKeys = new Set();
  let dataCache = [];
  let lastStatus = "初始化中";
  let lastError = "";
  let lastCollectAt = "";
  let lastReturnedCount = 0;
  let lastNewRowCount = 0;
  let lastLatestDataAt = "";
  let lastPushStatus = "";

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

  function delay(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function getTimeRange() {
    const end = new Date(Date.now() - 60 * 1000);
    const start = new Date(end.getTime() - CONFIG.rollingWindowMinutes * 60 * 1000);
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

  function findLatestRowDate(rows) {
    let latestMs = -Infinity;
    let latestDate = "";
    (rows || []).forEach((row) => {
      const rowDate = String(row?.date || "");
      const rowMs = parseChinaDateTimeMs(rowDate);
      if (Number.isFinite(rowMs) && rowMs > latestMs) {
        latestMs = rowMs;
        latestDate = rowDate;
      }
    });
    return latestDate;
  }

  function buildCollectionDiagnostic(rows, newRows) {
    const returnedCount = Array.isArray(rows) ? rows.length : 0;
    const newCount = Array.isArray(newRows) ? newRows.length : 0;
    const latestDataAt = findLatestRowDate(rows);
    return {
      returnedCount,
      newCount,
      latestDataAt,
      label: latestDataAt
        ? `接口返回 ${returnedCount} 条，新增 ${newCount} 条，最新数据 ${latestDataAt}`
        : `接口返回 ${returnedCount} 条，新增 ${newCount} 条，未返回目标数据`,
    };
  }

  function metricMatches(metric, text) {
    const normalized = normalizeText(text).toLowerCase();
    return (metric.aliases || []).some((keywords) =>
      keywords.every((keyword) => normalized.includes(String(keyword).toLowerCase())),
    );
  }

  function rowMatchesRequiredMetric(tableName) {
    return CONFIG.requiredMetrics.some((metric) => metricMatches(metric, tableName));
  }

  function getSelectedMetricText() {
    const inputs = Array.from(document.querySelectorAll('input[placeholder="请选择指标"]'));
    const metricInput = inputs.find((input) => String(input.value || "").trim().length > 0) || inputs[0];
    return String(metricInput?.value || "");
  }

  function getSelectedStationText() {
    const candidates = Array.from(document.querySelectorAll(".el-select input, input, .el-select__selected-item, .el-input__inner"));
    const texts = candidates
      .map((element) => normalizeText(element.value || element.textContent))
      .filter((text) => text && text.length <= 80)
      .filter((text) => !text.includes("请选择指标"))
      .filter((text) => !text.includes("开始时间"))
      .filter((text) => !text.includes("结束时间"));
    return texts.find((text) => /MW|MWh|kW|储能|电站|华进|和宏|工厂/.test(text)) || texts[0] || "";
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

  function getTemplateValidation(payload) {
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
    const missing = metricText ? getMissingRequiredMetrics(metricText) : [];
    return {
      metricText,
      missingRequiredMetrics: missing.map((metric) => metric.fullName),
    };
  }

  function validateObservedTemplate(payload) {
    return getTemplateValidation(payload);
  }

  function sanitizePlantId(text, stationId) {
    const known = CONFIG.knownStationPlantIds[String(stationId || "")];
    if (known) return known;
    if (String(text || "").includes("和宏") || String(text || "").includes("华进")) return "hehong_huajin";
    return `ecloud_station_${String(stationId || "unknown").replace(/[^A-Za-z0-9_-]/g, "")}`;
  }

  function buildTemplateSummary(message, payload, validation) {
    const stationId = String(payload.stationId);
    const plantName = normalizeText(
      payload.stationName ||
      payload.name ||
      payload.stationNameCn ||
      payload.stationAlias ||
      getSelectedStationText() ||
      `eCloud电站${stationId}`,
    );
    return {
      key: stationId,
      stationId,
      plantId: sanitizePlantId(plantName, stationId),
      plantName,
      url: message.url,
      payload: cloneJson(payload, {}),
      observedAt: new Date().toISOString(),
      metricText: validation.metricText || "",
      missingRequiredMetrics: validation.missingRequiredMetrics || [],
    };
  }

  function upsertObservedQueryTemplate(template) {
    const index = observedQueryTemplates.findIndex((item) => item.key === template.key);
    if (index >= 0) {
      observedQueryTemplates[index] = template;
    } else {
      observedQueryTemplates.push(template);
    }
    return observedQueryTemplates.length;
  }

  function getDefaultObservedTemplate() {
    return observedQueryTemplates[0] || null;
  }

  function summarizeTemplates() {
    return observedQueryTemplates.map((template) => ({
      stationId: template.stationId,
      plantId: template.plantId,
      plantName: template.plantName,
      observedAt: template.observedAt,
      missingRequiredMetrics: template.missingRequiredMetrics || [],
    }));
  }

  function formatTemplateStatus() {
    if (observedQueryTemplates.length === 0) return "未学习模板";
    return observedQueryTemplates
      .map((template, index) => `${index + 1}.${template.plantName || template.plantId}(${template.stationId})`)
      .join("；");
  }

  function metricWarningText(template) {
    const missing = template?.missingRequiredMetrics || [];
    return missing.length > 0 ? `，缺少: ${missing.join(", ")}` : "";
  }

  function isPointDataShowListUrl(url) {
    return String(url || "").includes(CONFIG.endpointSuffix);
  }

  function observeEcloudQueryTemplate(message) {
    if (!message || !isPointDataShowListUrl(message.url)) {
      return null;
    }

    const payload = cloneJson(message.payload);
    const validation = validateObservedTemplate(payload);
    const template = buildTemplateSummary(message, payload, validation);
    const templateCount = upsertObservedQueryTemplate(template);
    setLastError("");
    log(`已记录eCloud接口查询模板: ${template.plantName} stationId=${template.stationId}，当前${templateCount}个模板${metricWarningText(template)}`);
    return cloneJson(template);
  }

  function buildObservedQueryPayload(timeRange = getTimeRange(), template = getDefaultObservedTemplate()) {
    if (!template) {
      throw new Error("未捕获eCloud查询模板，请先在页面选择正确模板并查询一次");
    }

    const payload = cloneJson(template.payload, {});
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

  function normalizePointDataShowListRows(apiData, timestamp = new Date().toISOString(), template = null) {
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
        const row = {
          tableName,
          date,
          value: String(value),
          timestamp,
        };
        if (template) {
          row.stationId = template.stationId;
          row.plantId = template.plantId;
          row.plantName = template.plantName;
        }
        rows.push(row);
      });
    });
    return rows;
  }

  function dedupeRows(rows) {
    const newRows = [];
    (rows || []).forEach((row) => {
      const key = `${row.stationId || row.plantId || "default"}:${row.tableName}:${row.date}`;
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

  async function queryPointDataShowList(timeRange = getTimeRange(), template = getDefaultObservedTemplate()) {
    const payload = buildObservedQueryPayload(timeRange, template);
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
    return normalizePointDataShowListRows(body.data, new Date().toISOString(), template);
  }

  function saveCollectedRows(rows, diagnostic = null) {
    if (!rows || rows.length === 0) {
      lastPushStatus = "无新增数据，未推送MPC";
      log(diagnostic?.latestDataAt ? `本轮没有新增数据，最新数据 ${diagnostic.latestDataAt}` : "本轮没有新增数据");
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
            lastPushStatus = `MPC推送异常: ${response.mpcError}`;
            log(`后台已保存，MPC推送异常: ${response.mpcError}`);
          } else {
            const mpcResult = response.mpcResult || {};
            const acceptedCount = Number(mpcResult.acceptedCount || 0);
            const payloadCount = Number(mpcResult.payloadCount || 0);
            lastPushStatus = payloadCount > 0
              ? `MPC推送成功: ${payloadCount} 个payload，接收 ${acceptedCount} 条`
              : "后台已保存，MPC无可推送数据";
            const latestText = diagnostic?.latestDataAt ? `，最新数据 ${diagnostic.latestDataAt}` : "";
            log(`${lastPushStatus}${latestText}`);
          }
        } else {
          const error = response?.error || "未知错误";
          setLastError(error);
          lastPushStatus = `数据发送失败: ${error}`;
          log(`数据发送失败: ${error}`);
        }
      },
    );

    return true;
  }

  async function collectTemplate(template, timeRange = getTimeRange()) {
    if (!template) {
      log("没有可用模板，跳过采集");
      return false;
    }
    if (collectInFlightTemplateKeys.has(template.key)) {
      log(`${template.plantName || template.plantId} 上一轮采集尚未结束，跳过本轮`);
      return false;
    }
    collectInFlightTemplateKeys.add(template.key);
    try {
      log(`采集 ${template.plantName || template.plantId} 最近${CONFIG.rollingWindowMinutes}分钟数据`);
      const rows = await queryPointDataShowList(timeRange, template);
      const newRows = dedupeRows(rows);
      lastCollectAt = new Date().toISOString();
      const diagnostic = buildCollectionDiagnostic(rows, newRows);
      lastReturnedCount = diagnostic.returnedCount;
      lastNewRowCount = diagnostic.newCount;
      lastLatestDataAt = diagnostic.latestDataAt;
      log(diagnostic.label);
      saveCollectedRows(newRows, diagnostic);
      return true;
    } catch (error) {
      const message = error && error.message ? error.message : String(error);
      setLastError(message);
      log(`数据采集失败: ${message}`);
      return false;
    } finally {
      collectInFlightTemplateKeys.delete(template.key);
    }
  }

  async function collectData() {
    const template = getDefaultObservedTemplate();
    if (!template) {
      log("未学习eCloud接口模板，请点击开始学习并手动查询两个电站");
      return false;
    }
    return collectTemplate(template);
  }

  function clearStaggerTimers() {
    if (collectIntervalId) {
      clearInterval(collectIntervalId);
      collectIntervalId = null;
    }
    staggerTimeoutIds.forEach((id) => clearTimeout(id));
    staggerTimeoutIds = [];
  }

  function runStaggeredCollectionCycle() {
    const templates = observedQueryTemplates.slice();
    if (templates.length === 0) {
      log("没有学习到模板，无法定时采集");
      return false;
    }
    staggerTimeoutIds.forEach((id) => clearTimeout(id));
    staggerTimeoutIds = [];
    const spacingMs = templates.length > 1 ? Math.floor(CONFIG.intervalMs / templates.length) : 0;
    templates.forEach((template, index) => {
      const delayMs = index * spacingMs;
      const timeoutId = setTimeout(() => {
        if (!isCollecting) return;
        collectTemplate(template);
      }, delayMs);
      staggerTimeoutIds.push(timeoutId);
    });
    log(`已安排${templates.length}个电站错峰采集，间隔约${Math.round(spacingMs / 1000)}秒`);
    return true;
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
    } else if (isLearning) {
      btnMain.classList.add("collecting");
      btnMain.title = "停止学习";
      btnText.textContent = "停止学习";
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
    if (observedQueryTemplates.length === 0) {
      log("请先点击开始学习，分别查询需要采集的电站，再停止学习后启动采集");
      return;
    }
    isCollecting = true;
    updateButtonState();
    log(`开始定时采集: ${formatTemplateStatus()}`);
    runStaggeredCollectionCycle();
    collectIntervalId = setInterval(runStaggeredCollectionCycle, CONFIG.intervalMs);
  }

  function stopCollection() {
    if (!isCollecting) {
      log("采集未运行");
      return;
    }
    isCollecting = false;
    clearStaggerTimers();
    updateButtonState();
    log("已停止采集");
  }

  function startLearning() {
    stopCollection();
    isLearning = true;
    observedQueryTemplates = [];
    previousDataKeys.clear();
    setLastError("");
    updateButtonState();
    log("开始学习：请分别操作每个电站选择变量并查询，完成后点击停止学习");
  }

  function stopLearning() {
    isLearning = false;
    updateButtonState();
    log(`停止学习，已学习${observedQueryTemplates.length}个模板: ${formatTemplateStatus()}`);
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
      if (isLearning || observedQueryTemplates.length === 0) {
        observeEcloudQueryTemplate(data);
      } else {
        log("捕获到页面查询，但当前不在学习模式，保留已有模板");
      }
    } catch (error) {
      const message = error && error.message ? error.message : String(error);
      setLastError("");
      log(`页面查询模板不完整: ${message}`);
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
      if (isLearning) {
        stopLearning();
      } else if (isCollecting) {
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

  function isVisibleElement(element) {
    if (!element) return false;
    if (element.disabled) return false;
    if (typeof element.getBoundingClientRect !== "function") return true;
    const rect = element.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }

  function findVisibleQueryButton() {
    const buttons = Array.from(document.querySelectorAll("button, .el-button"));
    return buttons.find((button) => {
      if (!isVisibleElement(button)) return false;
      const text = normalizeText(button.textContent);
      if (!text) return false;
      if (text.includes("重置") || text.includes("导出") || text.includes("原始值")) return false;
      return text.includes("查询") || text.includes("搜索");
    }) || null;
  }

  async function fillRecentTimeRangeAndClickQuery(timeRange = getTimeRange()) {
    const filled = await fillVisibleTimeRangeInputs(timeRange);
    if (!filled) return false;
    const queryButton = findVisibleQueryButton();
    if (!queryButton) return false;
    queryButton.click();
    return true;
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
      if (request.action === "startLearning") {
        startLearning();
        sendResponse({ success: true });
        return false;
      }
      if (request.action === "stopLearning") {
        stopLearning();
        sendResponse({ success: true });
        return false;
      }
      if (request.action === "getStatus") {
        sendResponse({
          isCollecting,
          isLearning,
          dataCount: dataCache.reduce((sum, batch) => sum + batch.dataCount, 0),
          hasQueryTemplate: observedQueryTemplates.length > 0,
          templateCount: observedQueryTemplates.length,
          templates: summarizeTemplates(),
          lastStatus,
          lastError,
          lastCollectAt,
          lastReturnedCount,
          lastNewRowCount,
          lastLatestDataAt,
          lastPushStatus,
        });
        return false;
      }
      if (request.action === "clearData") {
        previousDataKeys.clear();
        dataCache = [];
        isFirstCollect = true;
        observedQueryTemplates = [];
        isLearning = false;
        stopCollection();
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
      buildCollectionDiagnostic,
      buildObservedQueryPayload,
      collectData,
      dedupeRows,
      extractDataFromTables,
      fillRecentTimeRangeAndClickQuery,
      fillVisibleTimeRangeInputs,
      findVisibleQueryButton,
      formatTemplateStatus,
      getMissingRequiredMetrics,
      getTimeRange,
      hasValidVisibleTimeRange,
      normalizePointDataShowListRows,
      observeEcloudQueryTemplate,
      rowMatchesRequiredMetric,
      summarizeTemplates,
      buildTemplateSummary,
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
