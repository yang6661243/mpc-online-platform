// popup.js - 弹出窗口逻辑

document.addEventListener('DOMContentLoaded', () => {
  const btnToggle = document.getElementById('btnToggle');
  const btnStartLearning = document.getElementById('btnStartLearning');
  const btnStopLearning = document.getElementById('btnStopLearning');
  const btnDownload = document.getElementById('btnDownload');
  const btnClear = document.getElementById('btnClear');
  const statusText = document.getElementById('statusText');
  const learningStatusText = document.getElementById('learningStatusText');
  const templateCount = document.getElementById('templateCount');
  const templateList = document.getElementById('templateList');
  const dataCount = document.getElementById('dataCount');
  const localDbCount = document.getElementById('localDbCount');
  const fileStatus = document.getElementById('fileStatus');
  const mpcStatusText = document.getElementById('mpcStatusText');
  const mpcPlantId = document.getElementById('mpcPlantId');
  const mpcBaseUrl = document.getElementById('mpcBaseUrl');
  const mpcPushedCount = document.getElementById('mpcPushedCount');
  const mpcLastSuccess = document.getElementById('mpcLastSuccess');
  const mpcLastError = document.getElementById('mpcLastError');
  const configStatus = document.getElementById('configStatus');
  const mpcBaseUrlInput = document.getElementById('mpcBaseUrlInput');
  const mpcPlantIdInput = document.getElementById('mpcPlantIdInput');
  const btnSaveMpcConfig = document.getElementById('btnSaveMpcConfig');
  const btnQuery = document.getElementById('btnQuery');
  const queryTableName = document.getElementById('queryTableName');
  const queryStart = document.getElementById('queryStart');
  const queryEnd = document.getElementById('queryEnd');
  const queryRows = document.getElementById('queryRows');

  let isCollecting = false;
  let isLearning = false;

  function renderTemplates(templates) {
    templateList.textContent = '';
    if (!templates || templates.length === 0) {
      const li = document.createElement('li');
      li.textContent = '暂无模板';
      templateList.appendChild(li);
      return;
    }
    templates.forEach((template) => {
      const li = document.createElement('li');
      const missing = template.missingRequiredMetrics && template.missingRequiredMetrics.length
        ? `；缺少 ${template.missingRequiredMetrics.join('、')}`
        : '';
      li.textContent = `${template.plantName || template.plantId} stationId=${template.stationId} plantId=${template.plantId}${missing}`;
      templateList.appendChild(li);
    });
  }

  // 更新状态显示
  function updateStatus() {
    // 获取采集状态
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      if (tabs[0]) {
        chrome.tabs.sendMessage(tabs[0].id, { action: 'getStatus' }, (response) => {
          if (response) {
            isCollecting = response.isCollecting;
            isLearning = response.isLearning;
            statusText.textContent = isCollecting ? '运行中' : '已停止';
            statusText.className = `status-value ${isCollecting ? 'running' : 'stopped'}`;
            btnToggle.textContent = isCollecting ? '停止采集' : '启动采集';
            btnToggle.className = `btn-${isCollecting ? 'danger' : 'primary'}`;
            learningStatusText.textContent = isLearning ? '学习中' : '已停止';
            learningStatusText.className = `status-value ${isLearning ? 'running' : 'stopped'}`;
            templateCount.textContent = `${response.templateCount || 0} 个`;
            renderTemplates(response.templates || []);
          } else {
            statusText.textContent = '页面未加载';
            statusText.className = 'status-value stopped';
          }
        });
      }
    });

    // 获取数据状态
    chrome.runtime.sendMessage({ action: 'getStatus' }, (response) => {
      if (response) {
        dataCount.textContent = `${response.dataCount || 0} 条`;
        localDbCount.textContent = `${response.localDbCount || response.dataCount || 0} 条`;
        if (response.fileName) {
          fileStatus.textContent = response.fileName;
          fileStatus.style.color = '#28a745';
        } else {
          fileStatus.textContent = '未下载';
          fileStatus.style.color = '#666';
        }

        const mpcStatus = response.mpcStatus || {};
        const stateText = {
          idle: '未开始',
          pushing: '推送中',
          ok: '正常',
          skipped: '跳过',
          error: '异常'
        }[mpcStatus.state || 'idle'] || mpcStatus.state || '未知';

        mpcStatusText.textContent = stateText;
        mpcStatusText.style.color = mpcStatus.state === 'error' ? '#dc3545' : '#28a745';
        mpcPlantId.textContent = response.mpcConfig?.plantId || '--';
        mpcBaseUrl.textContent = response.mpcConfig?.baseUrl || '--';
        if (document.activeElement !== mpcBaseUrlInput) {
          mpcBaseUrlInput.value = response.mpcConfig?.baseUrl || '';
        }
        if (document.activeElement !== mpcPlantIdInput) {
          mpcPlantIdInput.value = response.mpcConfig?.plantId || '';
        }
        mpcPushedCount.textContent = `${mpcStatus.acceptedCount || 0} 条`;
        mpcLastSuccess.textContent = mpcStatus.lastSuccessAt || '--';
        mpcLastError.textContent = mpcStatus.lastError || '--';
      }
    });
  }

  function sendContentAction(action, button, runningText) {
    button.disabled = true;
    const originalText = button.textContent;
    button.textContent = runningText;
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      if (tabs[0]) {
        chrome.tabs.sendMessage(tabs[0].id, { action }, () => {
          button.disabled = false;
          button.textContent = originalText;
          updateStatus();
        });
      } else {
        button.disabled = false;
        button.textContent = originalText;
      }
    });
  }

  function setQueryMessage(message) {
    queryRows.textContent = '';
    const tr = document.createElement('tr');
    const td = document.createElement('td');
    td.colSpan = 4;
    td.textContent = message;
    tr.appendChild(td);
    queryRows.appendChild(tr);
  }

  function renderQueryRows(rows) {
    queryRows.textContent = '';
    if (!rows || rows.length === 0) {
      setQueryMessage('没有匹配数据');
      return;
    }

    rows.forEach((row) => {
      const tr = document.createElement('tr');
      [row.plantName || row.plantId || row.stationId || '', row.date || '', row.tableName || '', row.value || ''].forEach((value) => {
        const td = document.createElement('td');
        td.textContent = value;
        tr.appendChild(td);
      });
      queryRows.appendChild(tr);
    });
  }

  function queryLocalRows() {
    btnQuery.disabled = true;
    btnQuery.textContent = '查询中...';
    chrome.runtime.sendMessage(
      {
        action: 'getLocalRows',
        filters: {
          tableName: queryTableName.value,
          start: queryStart.value,
          end: queryEnd.value,
          limit: 50
        }
      },
      (response) => {
        btnQuery.disabled = false;
        btnQuery.textContent = '查询最近50条';
        if (response && response.success) {
          renderQueryRows(response.rows || []);
          localDbCount.textContent = `${response.localDbCount || 0} 条`;
        } else {
          setQueryMessage(`查询失败: ${response?.error || '未知错误'}`);
        }
      }
    );
  }

  // 启动/停止采集
  btnToggle.addEventListener('click', () => {
    btnToggle.disabled = true;

    // 立即更新按钮文本，提升用户体验
    if (!isCollecting) {
      btnToggle.textContent = '启动中...';
      btnToggle.className = 'btn-primary';
    } else {
      btnToggle.textContent = '停止中...';
      btnToggle.className = 'btn-danger';
    }

    const action = isCollecting ? 'stopCollection' : 'startCollection';

    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      if (tabs[0]) {
        chrome.tabs.sendMessage(tabs[0].id, { action: action }, (response) => {
          btnToggle.disabled = false;
          updateStatus();
        });
      }
    });
  });

  btnStartLearning.addEventListener('click', () => {
    sendContentAction('startLearning', btnStartLearning, '学习中...');
  });

  btnStopLearning.addEventListener('click', () => {
    sendContentAction('stopLearning', btnStopLearning, '停止中...');
  });

  btnSaveMpcConfig.addEventListener('click', () => {
    btnSaveMpcConfig.disabled = true;
    btnSaveMpcConfig.textContent = '保存中...';
    chrome.runtime.sendMessage(
      {
        action: 'saveMpcConfig',
        config: {
          baseUrl: mpcBaseUrlInput.value,
          plantId: mpcPlantIdInput.value
        }
      },
      (response) => {
        btnSaveMpcConfig.disabled = false;
        btnSaveMpcConfig.textContent = '保存后端配置';
        if (response && response.success) {
          configStatus.textContent = '已保存';
          configStatus.style.color = '#28a745';
        } else {
          configStatus.textContent = response?.error || '保存失败';
          configStatus.style.color = '#dc3545';
        }
        updateStatus();
      }
    );
  });

  // 下载数据
  btnDownload.addEventListener('click', () => {
    btnDownload.disabled = true;
    btnDownload.textContent = '导出中...';

    chrome.runtime.sendMessage({ action: 'downloadData' }, (response) => {
      btnDownload.disabled = false;
      btnDownload.textContent = '导出Excel';

      if (response && response.success) {
        alert('Excel导出成功！');
      } else {
        alert('导出失败：' + (response?.error || '未知错误'));
      }

      updateStatus();
    });
  });

  // 清空数据
  btnClear.addEventListener('click', () => {
    if (confirm('确定要清空所有采集的数据吗？')) {
      chrome.runtime.sendMessage({ action: 'clearData' }, (response) => {
        if (response && response.success) {
          alert('数据已清空');
          setQueryMessage('暂无查询结果');
          updateStatus();
        }
      });
    }
  });

  btnQuery.addEventListener('click', queryLocalRows);

  // 定时更新状态
  updateStatus();
  setInterval(updateStatus, 2000);
});
