# eCloud 插件实时接入 MPC 设计说明

## 目标

把桌面上的 `ecloud-data-extractor` 浏览器插件从“采集并下载 CSV”扩展为“采集、缓存、推送到线上 MPC 服务”。MPC 服务继续负责入库、聚合、计算和看板展示。

## 当前基础

MPC 服务已经提供并验证过以下能力：

- `POST /api/v1/mpc/input-data`：接收电网表和储能表原始数据。
- `POST /api/v1/plants/{plant_id}/aggregate`：生成 15 分钟聚合数据。
- `GET /api/v1/plants/{plant_id}/dashboard`：返回客户展示看板数据。
- 数据库当前使用容器内 SQLite 文件，路径由 `MPC_DATABASE_URL` 控制。

`ecloud-data-extractor` 当前采集的数据结构是通用表格行：

```json
{
  "tableName": "防逆流电表/ADW-总有功功率",
  "date": "2026-06-12 10:15:00",
  "value": "410.2",
  "timestamp": "2026-06-12T10:15:05.000Z"
}
```

## 推荐架构

插件只负责采集和 HTTP 推送，不直接写数据库。MPC 服务负责字段校验、正负号转换、SOC 单位转换、幂等写入、聚合和看板读取。

```text
eCloud 页面表格
  -> content.js 提取 tableName/date/value
  -> background.js 映射为 grid_meter / battery payload
  -> POST 到 MPC 服务
  -> MPC 服务写入 raw_grid_meter / raw_battery
  -> 调用 aggregate 生成 telemetry_15min
  -> dashboard 展示实时曲线
```

## 默认字段映射

第一版按表名文本匹配：

| eCloud 表名特征 | 标准字段 | 说明 |
|---|---|---|
| 包含 `防逆流` 或 `ADW` 或 `电网` | `grid_power_kw` | 电网功率，正数表示购电 |
| 包含 `储能` 或 `电池`，且包含 `功率` | `battery_power_kw` | 储能功率，正数放电、负数充电 |
| 包含 `SOC` 或 `荷电` | `soc` | SOC，默认按百分比上传 |

如果只采到电网功率，没有储能功率或 SOC，则只推送 `grid_meter`。看板会显示当前电网数据，但聚合质量可能是 `missing_battery`，此时不能完整跑 MPC。

## 插件改动

- `manifest.json`：增加 MPC 服务地址权限。
- `background.js`：增加表格行转换、POST 推送、聚合触发、状态统计。
- `popup.html`：增加 MPC 推送状态区域。
- `popup.js`：展示最近推送结果、错误信息和推送条数。
- `README.md`：补充中文部署和验证说明。

## 测试策略

用 Node.js 测试纯函数，不依赖浏览器环境：

- 表格行能按表名映射成 `grid_meter` payload。
- 储能功率和 SOC 行能合并成 `battery` payload。
- 时间能从 `YYYY-MM-DD HH:mm:ss` 转成带 `+08:00` 的 ISO 字符串。
- 缺少 SOC 时不生成无效 battery payload。

浏览器端手动验证：

- 加载插件后能继续启动采集。
- 弹窗显示 MPC 推送状态。
- 线上 `/dashboard?plant_id=ecloud_factory` 能看到入库后的数据。
