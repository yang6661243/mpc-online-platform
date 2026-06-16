// background.js - 后台服务，处理数据保存和MPC推送

importScripts("mpcBridge.js", "localDb.js", "xlsxExport.js");

// 存储采集的数据
let collectedData = [];
let fileName = '';
let mpcStatus = {
  enabled: true,
  state: 'idle',
  lastSuccessAt: '',
  lastError: '',
  pushedPayloadCount: 0,
  pushedRecordCount: 0,
  acceptedCount: 0,
  aggregateQualityCounts: null
};

const MPC_CONFIG = {
  plantId: MpcBridge.DEFAULT_CONFIG.plantId,
  baseUrl: MpcBridge.DEFAULT_CONFIG.mpcBaseUrl,
  aggregateWindowMinutes: MpcBridge.DEFAULT_CONFIG.aggregateWindowMinutes
};

async function loadMpcConfig() {
  const stored = await chrome.storage.local.get(['mpcBaseUrl', 'mpcPlantId']);
  MPC_CONFIG.baseUrl = String(stored.mpcBaseUrl || MpcBridge.DEFAULT_CONFIG.mpcBaseUrl).trim() || MpcBridge.DEFAULT_CONFIG.mpcBaseUrl;
  MPC_CONFIG.plantId = String(stored.mpcPlantId || MpcBridge.DEFAULT_CONFIG.plantId).trim() || MpcBridge.DEFAULT_CONFIG.plantId;
  return { ...MPC_CONFIG };
}

async function saveMpcConfig(config) {
  const baseUrl = String(config?.baseUrl || '').trim();
  const plantId = String(config?.plantId || '').trim();
  if (!baseUrl) throw new Error('MPC后端地址不能为空');
  if (!/^https?:\/\//i.test(baseUrl)) throw new Error('MPC后端地址必须以 http:// 或 https:// 开头');
  if (!plantId) throw new Error('MPC工厂ID不能为空');
  await chrome.storage.local.set({ mpcBaseUrl: baseUrl.replace(/\/$/, ''), mpcPlantId: plantId });
  return loadMpcConfig();
}

function groupRowsByPlant(rows) {
  const grouped = new Map();
  (rows || []).forEach((row) => {
    const plantId = row.plantId || MPC_CONFIG.plantId;
    const current = grouped.get(plantId) || [];
    current.push(row);
    grouped.set(plantId, current);
  });
  return grouped;
}

function mpcUrl(path) {
  return `${MPC_CONFIG.baseUrl.replace(/\/$/, '')}${path}`;
}

function buildMpcSkipReason(rows) {
  const summary = MpcBridge.summarizeMpcRows(rows || []);
  const counts = summary.matchedCounts || {};
  const plants = summary.plants?.length ? `；电站: ${summary.plants.join('、')}` : '';
  const unmatched = summary.unmatchedTableNames?.length
    ? `；未匹配表名示例: ${summary.unmatchedTableNames.join('、')}`
    : '';
  return [
    `本批${summary.totalRows}条数据未生成MPC payload`,
    `已识别 电网${counts.grid_power_kw || 0}条/储能功率${counts.battery_power_kw || 0}条/SOC${counts.soc || 0}条`,
    plants,
    unmatched
  ].join('');
}

async function postJson(path, payload) {
  const response = await fetch(mpcUrl(path), {
    method: 'POST',
    headers: {
      'Accept': 'application/json',
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(payload)
  });
  const bodyText = await response.text();
  let body = {};
  if (bodyText) {
    try {
      body = JSON.parse(bodyText);
    } catch (error) {
      body = { raw: bodyText };
    }
  }
  if (!response.ok) {
    throw new Error(`MPC接口失败 ${response.status}: ${bodyText || response.statusText}`);
  }
  return body;
}

async function pushDataToMpc(rows) {
  await loadMpcConfig();

  if (!rows || rows.length === 0) {
    return { skipped: true, reason: '没有新增数据' };
  }

  mpcStatus = {
    ...mpcStatus,
    state: 'pushing',
    lastError: ''
  };

  const generatedAt = new Date().toISOString();
  const rowsByPlant = groupRowsByPlant(rows);
  const allPayloads = [];
  rowsByPlant.forEach((plantRows, plantId) => {
    const payloads = MpcBridge.buildMpcIngestPayloads(plantRows, {
      plantId,
      generatedAt
    });
    allPayloads.push(...payloads.map((payload) => ({ plantId, payload, plantRows })));
  });

  if (allPayloads.length === 0) {
    mpcStatus = {
      ...mpcStatus,
      state: 'skipped',
      lastError: buildMpcSkipReason(rows)
    };
    return { skipped: true, reason: mpcStatus.lastError };
  }

  let acceptedCount = 0;
  let pushedRecordCount = 0;
  for (const item of allPayloads) {
    const result = await postJson('/api/v1/mpc/input-data', item.payload);
    acceptedCount += Number(result.accepted_count || 0);
    pushedRecordCount += item.payload.records.length;
  }

  const aggregateQualityCountsByPlant = {};
  for (const [plantId, plantRows] of rowsByPlant.entries()) {
    const aggregateWindow = MpcBridge.buildAggregateWindow(plantRows, MPC_CONFIG.aggregateWindowMinutes);
    if (aggregateWindow) {
      const aggregateResult = await postJson(`/api/v1/plants/${encodeURIComponent(plantId)}/aggregate`, aggregateWindow);
      aggregateQualityCountsByPlant[plantId] = aggregateResult.quality_counts || null;
    }
  }

  mpcStatus = {
    enabled: true,
    state: 'ok',
    lastSuccessAt: generatedAt,
    lastError: '',
    pushedPayloadCount: mpcStatus.pushedPayloadCount + allPayloads.length,
    pushedRecordCount: mpcStatus.pushedRecordCount + pushedRecordCount,
    acceptedCount: mpcStatus.acceptedCount + acceptedCount,
    aggregateQualityCounts: aggregateQualityCountsByPlant
  };

  return {
    skipped: false,
    payloadCount: allPayloads.length,
    pushedRecordCount,
    acceptedCount,
    aggregateQualityCounts: mpcStatus.aggregateQualityCounts
  };
}

async function handleSaveData(request) {
  // 将数据添加到内存中，保留原CSV下载能力。
  if (request.data && Array.isArray(request.data)) {
    collectedData.push(...request.data);
    console.log('数据已添加到内存，当前共', collectedData.length, '条');
  }

  let localDbResult = { savedCount: 0 };
  let localDbError = '';
  try {
    localDbResult = await EcloudLocalDb.upsertRows(request.data || []);
  } catch (error) {
    localDbError = error.message || String(error);
    console.error('写入本地数据库失败:', error);
  }

  try {
    const mpcResult = await pushDataToMpc(request.data || []);
    const localDbCount = await getLocalRowCount();
    return { success: true, dataCount: localDbCount, localDbCount, localDbResult, localDbError, mpcStatus, mpcResult };
  } catch (error) {
    mpcStatus = {
      ...mpcStatus,
      state: 'error',
      lastError: error.message || String(error)
    };
    console.error('推送MPC失败:', error);
    const localDbCount = await getLocalRowCount();
    return { success: true, dataCount: localDbCount, localDbCount, localDbResult, localDbError, mpcStatus, mpcError: mpcStatus.lastError };
  }
}

async function getLocalRowCount() {
  try {
    return await EcloudLocalDb.countRows();
  } catch (error) {
    console.error('读取本地数据库条数失败:', error);
    return collectedData.length;
  }
}

async function handleRuntimeMessage(request) {
  if (request.action === 'saveData') {
    return handleSaveData(request);
  }

  if (request.action === 'saveQueryTemplate') {
    const result = await EcloudLocalDb.upsertQueryTemplates([request.template]);
    return { success: true, result };
  }

  if (request.action === 'getQueryTemplates') {
    const templates = await EcloudLocalDb.getQueryTemplates();
    return { success: true, templates };
  }

  if (request.action === 'clearQueryTemplates') {
    await EcloudLocalDb.clearQueryTemplates();
    return { success: true };
  }

  if (request.action === 'saveMpcConfig') {
    const config = await saveMpcConfig(request.config || {});
    return { success: true, mpcConfig: config };
  }

  if (request.action === 'getData') {
    const data = await EcloudLocalDb.getRows({ limit: 100000 });
    return { success: true, data };
  }

  if (request.action === 'clearData') {
    collectedData = [];
    await EcloudLocalDb.clearRows();
    mpcStatus = {
      ...mpcStatus,
      state: 'idle',
      lastError: '',
      pushedPayloadCount: 0,
      pushedRecordCount: 0,
      acceptedCount: 0,
      aggregateQualityCounts: null
    };
    return { success: true };
  }

  if (request.action === 'getLocalRows') {
    const rows = await EcloudLocalDb.getRows(request.filters || {});
    const localDbCount = await getLocalRowCount();
    return { success: true, rows, localDbCount };
  }

  if (request.action === 'downloadData') {
    await downloadDataAsXLSX();
    return { success: true };
  }

  if (request.action === 'getStatus') {
    await loadMpcConfig();
    const localDbCount = await getLocalRowCount();
    return {
      dataCount: localDbCount,
      localDbCount,
      fileName: fileName,
      mpcStatus: mpcStatus,
      mpcConfig: {
        plantId: MPC_CONFIG.plantId,
        baseUrl: MPC_CONFIG.baseUrl
      }
    };
  }

  return { success: false, error: `未知动作: ${request.action}` };
}

// 监听来自content script和popup的消息
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  handleRuntimeMessage(request)
    .then(sendResponse)
    .catch((error) => {
      console.error('处理消息失败:', error);
      sendResponse({ success: false, error: error.message || String(error) });
    });
  return true;
});

// 下载本地数据库为Excel文件
async function downloadDataAsXLSX() {
  try {
    const rows = await EcloudLocalDb.getRows({ limit: 1000000 });
    if (rows.length === 0) {
      throw new Error('没有数据可下载');
    }

    // 使用data URL避免MV3 service worker缺少createObjectURL。
    const url = EcloudXlsxExport.generateXlsxDataUrl(rows);
    const timestamp = new Date().toISOString().slice(0, 19).replace(/[:-]/g, '');
    fileName = `ecloud_data_${timestamp}.xlsx`;

    await chrome.downloads.download({
      url: url,
      filename: fileName,
      saveAs: true  // 让用户选择保存位置
    });

    console.log('Excel数据已下载:', fileName);
  } catch (error) {
    console.error('下载Excel失败:', error);
    throw error;
  }
}
