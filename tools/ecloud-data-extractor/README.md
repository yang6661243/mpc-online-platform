# eCloud数据自动采集Chrome扩展

## 功能概述

本Chrome扩展用于自动采集 https://ecloud.hoenergypower.cn 网站上的电池分析数据，实现：

1. 在目标页面注入悬浮按钮
2. 自动填写"最近10分钟"时间范围
3. 自动点击"刷新"按钮
4. 从ECharts图表中提取数据（两条曲线）
5. 保存为CSV文件（首次完整保存，之后追加新数据）
6. 每分钟自动重复执行
7. 将匹配到的电网表、储能功率、SOC数据实时推送到线上MPC服务

## MPC实时推送

当前默认推送到：

```text
http://8.163.49.151:18000
```

默认工厂ID：

```text
hehong_huajin
```

如果弹窗里的“MPC后端”仍显示 `http://127.0.0.1:18000`，说明Chrome扩展本地配置里保存过旧地址；请在弹窗中改为 `http://8.163.49.151:18000` 并点击“保存后端配置”，或者清空扩展数据后重新加载扩展。

插件采集到表格数据后，会先保留原来的内存缓存和CSV下载能力，然后把可识别的表格行转换成MPC服务接口格式：

```text
POST /api/v1/mpc/input-data
POST /api/v1/plants/hehong_huajin/aggregate
```

### 默认表名映射

| eCloud表名特征 | 推送到MPC字段 | 说明 |
|---|---|---|
| 包含 `防逆流`、`ADW` 或 `电网` | `grid_power_kw` | 电网功率，正数表示购电 |
| `计量电表/总有功功率` | `battery_power_kw` | 储能侧计量电表总功率，作为储能总功率使用 |
| 包含 `储能` 或 `电池`，且包含 `功率` | `battery_power_kw` | 储能功率，正数放电、负数充电 |
| 包含 `SOC` 或 `荷电` | `soc` | SOC，按百分比推送，例如58表示58% |

如果现场页面表名不同，需要修改 `mpcBridge.js` 里的 `classifyTableName()`。

### MPC推送状态

点击浏览器工具栏里的扩展图标，可以看到：

- MPC推送：未开始、推送中、正常、跳过、异常
- MPC工厂：当前推送使用的工厂ID
- 已推送：MPC接口累计接收的记录数
- 最近成功：最近一次成功推送时间
- 最近错误：最近一次错误原因

### 验证方法

健康检查：

```text
http://8.163.49.151:18000/healthz
```

客户展示页面：

```text
http://8.163.49.151:18000/dashboard?plant_id=hehong_huajin
```

### 网络代理注意事项

如果浏览器或扩展访问 MPC 服务时报 `Failed to fetch`、`ERR_EMPTY_RESPONSE`，但终端用 `NO_PROXY='*' curl http://8.163.49.151:18000/healthz` 可以返回 `{"status":"ok"}`，通常是本机系统代理拦截了 `8.163.49.151:18000`。

处理方式是在本机网络代理例外中加入：

```text
8.163.49.151
```

当前 Mac 已在 Ethernet 和 Wi-Fi 的代理例外里加入该 IP。这个修改只影响本机浏览器访问 MPC 服务，不会修改服务器和 Docker 容器。

如果只采集到电网功率，没有储能功率或SOC，MPC服务仍会保存电网数据，但15分钟聚合质量会标记缺少储能数据，完整MPC策略暂时不能计算。

## 安装步骤

### 1. 加载扩展到Chrome

1. 打开Chrome浏览器
2. 在地址栏输入：`chrome://extensions/`
3. 开启右上角的"开发者模式"
4. 点击"加载已解压的扩展程序"
5. 选择本扩展所在的文件夹（`ecloud-data-extractor`）

### 2. 配置DOM选择器

扩展需要根据实际页面的DOM结构来定位元素。请按以下步骤获取选择器：

#### 2.1 打开开发者工具

1. 打开目标网页：https://ecloud.hoenergypower.cn/#/surveillance/battery-analysis
2. 按 `F12` 打开开发者工具
3. 点击左上角的"元素选择器"图标（或按 `Ctrl+Shift+C`）

#### 2.2 获取时间输入框选择器

1. 使用元素选择器点击"开始时间"输入框
2. 在开发者工具的Elements面板中，右键点击高亮的元素
3. 选择 `Copy` -> `Copy selector`，获取CSS选择器
4. 打开 `content.js` 文件
5. 找到 `CONFIG` 对象（第6行附近）
6. 将 `timeInputStart: ''` 改为 `timeInputStart: '刚才复制的选择器'`
7. 重复上述步骤，获取"结束时间"输入框的选择器，填入 `timeInputEnd`

示例：
```javascript
const CONFIG = {
  timeInputStart: 'input.el-input__inner:nth-child(1)',  // 示例选择器
  timeInputEnd: 'input.el-input__inner:nth-child(2)',    // 示例选择器
  // ... 其他配置
};
```

#### 2.3 获取"刷新"按钮选择器

1. 使用元素选择器点击"刷新"按钮
2. 同样复制CSS选择器
3. 在 `content.js` 中，将 `refreshButton: ''` 改为 `refreshButton: '刚才复制的选择器'`

示例：
```javascript
refreshButton: 'button.el-button--primary',  // 示例选择器
```

#### 2.4 获取图表容器选择器

1. 使用元素选择器点击ECharts图表区域
2. 找到包含 `echarts` 的DOM元素（通常是 `<div class="echarts-dom">` 这样的元素）
3. 复制其CSS选择器或直接使用类名
4. 在 `content.js` 中，将 `chartContainer: '.echarts-dom-selector'` 改为 `chartContainer: '实际的选择器'`

示例：
```javascript
chartContainer: 'div.echarts-instance',  // 示例选择器
```

### 3. 重新加载扩展

1. 修改完 `content.js` 后，保存文件
2. 回到Chrome的 `chrome://extensions/` 页面
3. 找到本扩展，点击"重新加载"按钮（刷新图标）

## 使用方法

### 1. 打开目标网页

1. 在Chrome中打开 https://ecloud.hoenergypower.cn/#/surveillance/battery-analysis
2. 登录并进入电池分析页面
3. 等待页面完全加载

### 2. 选择CSV文件

1. 点击Chrome工具栏上的扩展图标（eCloud数据采集）
2. 在弹出窗口中，点击"选择CSV文件"按钮
3. 选择一个已有的CSV文件（用于追加数据），或创建一个新文件
4. 如果选择成功，会显示"已选择"

### 3. 启动采集

有两种方式启动采集：

**方式一：使用悬浮按钮（推荐）**

1. 在页面右下角会出现一个悬浮按钮（📊 开始采集）
2. 点击该按钮，开始采集
3. 按钮会变成红色，显示"⏸ 停止采集"
4. 再次点击可停止采集

**方式二：使用弹出窗口**

1. 点击Chrome工具栏上的扩展图标
2. 在弹出窗口中，点击"启动采集"按钮
3. 按钮会变成"停止采集"

### 4. 查看采集状态

在弹出窗口中，可以查看：

- **采集状态**：运行中（绿色）/ 已停止（红色）
- **已采集**：已采集的数据条数
- **CSV文件**：是否已选择CSV文件

### 5. 停止采集

- 点击悬浮按钮，或
- 点击弹出窗口中的"停止采集"按钮

## 数据格式

CSV文件格式示例：

```csv
时间戳,计量电表/总有功功率,防逆流电表/ADW-总有功功率
2026-06-10 23:29:00,0.24,102
2026-06-10 23:30:00,0.25,103
```

## 数据提取方案

扩展尝试三种方案提取数据（按优先级）：

### 方案A：通过ECharts实例获取（推荐）

- 直接访问页面的ECharts实例
- 调用 `getOption()` 获取数据
- 优点：数据最准确，性能好
- 缺点：需要页面暴露ECharts实例

### 方案B：监听Tooltip

- 使用MutationObserver监听Tooltip DOM变化
- 从Tooltip HTML中提取数据
- 优点：不依赖ECharts实例访问
- 缺点：需要Tooltip显示后才能提取

### 方案C：从DOM提取（通用）

- 从页面的DOM元素中提取数据
- 优点：通用性强
- 缺点：需要了解页面DOM结构

## 调试方法

### 查看日志

1. 打开开发者工具（F12）
2. 切换到"Console"选项卡
3. 查看以 `[eCloud采集]` 开头的日志

### 常见问题

#### 1. 悬浮按钮未出现

- 检查是否在目标页面（URL包含 `ecloud.hoenergypower.cn`）
- 检查扩展是否已启用
- 查看Console是否有错误信息

#### 2. 时间填写失败

- 检查 `timeInputStart` 和 `timeInputEnd` 选择器是否正确
- 查看Console日志，确认是否找到输入框

#### 3. 点击刷新按钮失败

- 检查 `refreshButton` 选择器是否正确
- 查看Console日志，确认是否找到按钮

#### 4. 数据提取失败

- 检查 `chartContainer` 选择器是否正确
- 查看Console日志，确认哪种提取方案失败
- 尝试手动触发Tooltip，看是否能提取

#### 5. CSV文件保存失败

- 确认是否已选择CSV文件
- 检查File System Access API是否被浏览器支持
- 查看Console日志，确认错误信息

## 配置参数说明

在 `content.js` 的 `CONFIG` 对象中，可以调整以下参数：

```javascript
const CONFIG = {
  // 时间输入框选择器（必须配置）
  timeInputStart: '',  // 开始时间输入框的CSS选择器
  timeInputEnd: '',    // 结束时间输入框的CSS选择器

  // 刷新按钮选择器（必须配置）
  refreshButton: '',   // 刷新按钮的CSS选择器

  // 图表容器选择器（必须配置）
  chartContainer: '',  // ECharts图表的容器选择器

  // 采集间隔（毫秒）
  interval: 60000,     // 60000ms = 1分钟

  // 时间窗口（分钟）
  timeWindow: 10,      // 最近10分钟

  // 是否启用调试模式
  debug: true          // true = 显示日志，false = 不显示日志
};
```

## 注意事项

1. **页面更新**：如果网站更新导致DOM结构变化，需要重新获取选择器并配置
2. **登录状态**：确保浏览器已登录目标网站，扩展不会处理登录流程
3. **数据准确性**：建议定期抽查采集的数据是否准确
4. **文件权限**：首次使用需要选择CSV文件，授予写入权限
5. **定时器**：关闭页面或Tab会停止定时采集

## 后续优化方向

1. 添加配置页面（可自定义时间范围、采集间隔）
2. 添加数据预览功能
3. 支持多种数据导出格式（JSON、Excel）
4. 添加错误处理和重试机制
5. 支持多个图表同时采集
6. 添加数据去重功能

## 技术支持

如有问题，请查看Console日志，或根据实际需求修改代码。

## 文件结构

```
ecloud-data-extractor/
├── manifest.json          # 扩展配置文件
├── content.js             # 内容脚本（核心逻辑）
├── background.js          # 后台服务（处理数据保存）
├── popup.html             # 弹出窗口HTML
├── popup.js               # 弹出窗口逻辑
├── icons/                 # 扩展图标
│   ├── icon16.png
│   ├── icon48.png
│   └── icon128.png
└── README.md              # 使用说明（本文件）
```

## 许可

本扩展仅供学习和研究使用。
