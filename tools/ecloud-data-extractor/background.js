// background.js - 后台服务，处理数据保存和MPC推送

importScripts("mpcBridge.js");

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

function mpcUrl(path) {
  return `${MPC_CONFIG.baseUrl.replace(/\/$/, '')}${path}`;
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
  if (!rows || rows.length === 0) {
    return { skipped: true, reason: '没有新增数据' };
  }

  mpcStatus = {
    ...mpcStatus,
    state: 'pushing',
    lastError: ''
  };

  const generatedAt = new Date().toISOString();
  const payloads = MpcBridge.buildMpcIngestPayloads(rows, {
    plantId: MPC_CONFIG.plantId,
    generatedAt
  });

  if (payloads.length === 0) {
    mpcStatus = {
      ...mpcStatus,
      state: 'skipped',
      lastError: '本批数据未匹配到电网功率、储能功率或SOC表名'
    };
    return { skipped: true, reason: mpcStatus.lastError };
  }

  let acceptedCount = 0;
  let pushedRecordCount = 0;
  for (const payload of payloads) {
    const result = await postJson('/api/v1/mpc/input-data', payload);
    acceptedCount += Number(result.accepted_count || 0);
    pushedRecordCount += payload.records.length;
  }

  let aggregateResult = null;
  const aggregateWindow = MpcBridge.buildAggregateWindow(rows, MPC_CONFIG.aggregateWindowMinutes);
  if (aggregateWindow) {
    aggregateResult = await postJson(`/api/v1/plants/${encodeURIComponent(MPC_CONFIG.plantId)}/aggregate`, aggregateWindow);
  }

  mpcStatus = {
    enabled: true,
    state: 'ok',
    lastSuccessAt: generatedAt,
    lastError: '',
    pushedPayloadCount: mpcStatus.pushedPayloadCount + payloads.length,
    pushedRecordCount: mpcStatus.pushedRecordCount + pushedRecordCount,
    acceptedCount: mpcStatus.acceptedCount + acceptedCount,
    aggregateQualityCounts: aggregateResult ? aggregateResult.quality_counts : null
  };

  return {
    skipped: false,
    payloadCount: payloads.length,
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

  try {
    const mpcResult = await pushDataToMpc(request.data || []);
    return { success: true, dataCount: collectedData.length, mpcStatus, mpcResult };
  } catch (error) {
    mpcStatus = {
      ...mpcStatus,
      state: 'error',
      lastError: error.message || String(error)
    };
    console.error('推送MPC失败:', error);
    return { success: true, dataCount: collectedData.length, mpcStatus, mpcError: mpcStatus.lastError };
  }
}

// 监听来自content script和popup的消息
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === 'saveData') {
    handleSaveData(request).then(sendResponse);
    return true;  // 异步响应
  }

  if (request.action === 'getData') {
    // 返回采集的数据
    sendResponse({ success: true, data: collectedData });
    return false;
  }

  if (request.action === 'clearData') {
    // 清空数据
    collectedData = [];
    mpcStatus = {
      ...mpcStatus,
      state: 'idle',
      lastError: '',
      pushedPayloadCount: 0,
      pushedRecordCount: 0,
      acceptedCount: 0,
      aggregateQualityCounts: null
    };
    sendResponse({ success: true });
    return false;
  }

  if (request.action === 'downloadData') {
    // 下载数据
    downloadDataAsCSV()
      .then(() => {
        sendResponse({ success: true });
      })
      .catch(error => {
        sendResponse({ success: false, error: error.message });
      });
    return true;  // 异步响应
  }

  if (request.action === 'getStatus') {
    sendResponse({
      dataCount: collectedData.length,
      fileName: fileName,
      mpcStatus: mpcStatus,
      mpcConfig: {
        plantId: MPC_CONFIG.plantId,
        baseUrl: MPC_CONFIG.baseUrl
      }
    });
  }
});

// 下载数据为CSV文件
async function downloadDataAsCSV() {
  try {
    if (collectedData.length === 0) {
      throw new Error('没有数据可下载');
    }

    // 构建CSV内容
    let csvContent = '采集时间,电表名称,数据时间,值\n';

    collectedData.forEach(data => {
      const row = `"${data.timestamp}","${data.tableName}","${data.date}",${data.value}`;
      csvContent += row + '\n';
    });

    // 创建Blob
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });

    // 使用chrome.downloads API下载
    const url = URL.createObjectURL(blob);
    const timestamp = new Date().toISOString().slice(0, 19).replace(/[:-]/g, '');
    fileName = `ecloud_data_${timestamp}.csv`;

    await chrome.downloads.download({
      url: url,
      filename: fileName,
      saveAs: true  // 让用户选择保存位置
    });

    // 清理URL
    setTimeout(() => URL.revokeObjectURL(url), 1000);

    console.log('数据已下载:', fileName);
  } catch (error) {
    console.error('下载CSV失败:', error);
    throw error;
  }
}
