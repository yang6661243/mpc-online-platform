// popup.js - 弹出窗口逻辑

document.addEventListener('DOMContentLoaded', () => {
  const btnToggle = document.getElementById('btnToggle');
  const btnDownload = document.getElementById('btnDownload');
  const btnClear = document.getElementById('btnClear');
  const statusText = document.getElementById('statusText');
  const dataCount = document.getElementById('dataCount');
  const fileStatus = document.getElementById('fileStatus');
  const mpcStatusText = document.getElementById('mpcStatusText');
  const mpcPlantId = document.getElementById('mpcPlantId');
  const mpcPushedCount = document.getElementById('mpcPushedCount');
  const mpcLastSuccess = document.getElementById('mpcLastSuccess');
  const mpcLastError = document.getElementById('mpcLastError');

  let isCollecting = false;

  // 更新状态显示
  function updateStatus() {
    // 获取采集状态
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      if (tabs[0]) {
        chrome.tabs.sendMessage(tabs[0].id, { action: 'getStatus' }, (response) => {
          if (response) {
            isCollecting = response.isCollecting;
            statusText.textContent = isCollecting ? '运行中' : '已停止';
            statusText.className = `status-value ${isCollecting ? 'running' : 'stopped'}`;
            btnToggle.textContent = isCollecting ? '停止采集' : '启动采集';
            btnToggle.className = `btn-${isCollecting ? 'danger' : 'primary'}`;
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
        mpcPushedCount.textContent = `${mpcStatus.acceptedCount || 0} 条`;
        mpcLastSuccess.textContent = mpcStatus.lastSuccessAt || '--';
        mpcLastError.textContent = mpcStatus.lastError || '--';
      }
    });
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

  // 下载数据
  btnDownload.addEventListener('click', () => {
    btnDownload.disabled = true;
    btnDownload.textContent = '下载中...';

    chrome.runtime.sendMessage({ action: 'downloadData' }, (response) => {
      btnDownload.disabled = false;
      btnDownload.textContent = '下载数据';

      if (response && response.success) {
        alert('数据下载成功！');
      } else {
        alert('下载失败：' + (response?.error || '未知错误'));
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
          updateStatus();
        }
      });
    }
  });

  // 定时更新状态
  updateStatus();
  setInterval(updateStatus, 2000);
});
