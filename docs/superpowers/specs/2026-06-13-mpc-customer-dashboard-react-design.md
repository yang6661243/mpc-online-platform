# MPC 客户策略对比看板 React 化设计

## 目标

把当前 FastAPI 内置的基础 `/dashboard` 页面升级成客户可演示的正式看板。页面要基于实时 eCloud 采集数据展示工厂当前策略，并在 MPC 结果存在时展示“工厂当前策略 vs MPC 策略”的曲线、最大需量和成本对比。

首版重点是可看、可解释、可验证，不在这一版处理长期数据库迁移、HTTPS 域名、控制策略平台下发闭环。

## 首版范围

包含：

- 新建 `web/mpc-dashboard/` 前端工程，使用 Vite、React、TypeScript、ECharts。
- FastAPI 服务构建后托管前端静态文件，`/dashboard` 返回 React 页面。
- 扩展 `GET /api/v1/plants/{plant_id}/dashboard`，支持返回最近 24/48 小时实时聚合曲线。
- 页面展示实时数据状态、实际策略曲线、MPC 策略曲线、指标卡片和 15 分钟明细表。
- 当还没有 MPC 结果时，页面明确显示“实时数据已接入，MPC 策略待生成”。

不包含：

- 不改已有实习平台前后端容器。
- 不迁移线上数据库到 PostgreSQL/MySQL。
- 不做 HTTPS 域名和正式登录鉴权。
- 不自动向控制策略平台发送控制目标。
- 不改 eCloud 插件采集点位，除非后续验证发现点位口径错误。

## 技术栈

前端：

- Vite：轻量构建，方便 Docker 多阶段构建。
- React + TypeScript：组件化仪表盘，降低后续扩展成本。
- ECharts：用于功率、SOC、策略对比曲线。
- 原生 CSS：先不用复杂 UI 框架，降低依赖和镜像体积。

后端：

- 保持 FastAPI。
- 继续使用当前 SQLAlchemy 模型和聚合表。
- 通过静态文件目录托管前端构建产物。

Docker：

- 构建阶段安装 Node 依赖并执行前端 build。
- 最终运行阶段仍是 Python + FastAPI，避免线上容器常驻 Node 服务。

## 页面结构

默认页面为正式操作看板，不做营销式首页。

顶部：

- 工厂名称。
- 最新数据时间。
- 数据状态：正常、缺少储能、缺少电网、MPC 待生成、MPC 异常。
- 刷新按钮。
- 时间窗口选择：最近 24 小时、最近 48 小时。

第一组指标卡：

- 当前电网功率。
- 当前储能功率。
- 当前 SOC。
- 最新数据质量。

第二组指标卡：

- 工厂当前最大需量。
- MPC 最大需量。
- 削峰量和削峰率。
- 当前成本、MPC 成本、预计节省。

主图：

- 工厂实际电网功率。
- MPC 策略电网功率。
- 峰值参考线。

副图：

- 工厂实际储能功率。
- MPC 储能功率。
- 实际 SOC。
- MPC SOC。

明细表：

- 时间。
- 实际电网功率。
- MPC 电网功率。
- 实际储能功率。
- MPC 储能功率。
- 实际 SOC。
- MPC SOC。
- 数据质量。

## 数据接口

保留现有接口：

```text
GET /api/v1/plants/{plant_id}/dashboard
```

新增查询参数：

```text
window_hours=24|48
```

返回结构扩展为：

```json
{
  "plant_id": "ecloud_factory",
  "current": {
    "time": "2026-06-13T04:30:00",
    "grid_power_kw": 216.2,
    "battery_power_kw": 0.09,
    "load_minus_pv_kw": 216.29,
    "soc": 0.05,
    "quality_flag": "ok"
  },
  "comparison": {
    "actual_peak_kw": 430.0,
    "mpc_peak_kw": 390.0,
    "peak_reduction_kw": 40.0,
    "peak_reduction_pct": 9.3,
    "actual_cost_yuan": 210.0,
    "mpc_cost_yuan": 185.0,
    "cost_saving_yuan": 25.0,
    "cost_saving_pct": 11.9
  },
  "series": [
    {
      "time": "2026-06-13T04:15:00",
      "actual_grid_power_kw": 216.2,
      "actual_battery_power_kw": 0.09,
      "actual_soc": 0.05,
      "load_minus_pv_kw": 216.29,
      "mpc_grid_power_kw": null,
      "mpc_battery_power_kw": null,
      "mpc_soc": null,
      "quality_flag": "ok"
    }
  ]
}
```

如果没有 MPC 运行结果，`comparison` 为 `null`，`series` 仍返回实际聚合曲线，MPC 字段为 `null`。

## 数据流

```text
Edge 插件
  -> POST /api/v1/mpc/input-data
  -> raw_grid_meter / raw_battery
  -> POST /api/v1/plants/{plant_id}/aggregate
  -> telemetry_15min
  -> GET /api/v1/plants/{plant_id}/dashboard
  -> React 看板
```

当 MPC 结果存在：

```text
POST /api/v1/mpc/run
  -> strategy_comparison
  -> strategy_curve_points
  -> GET /api/v1/plants/{plant_id}/dashboard
  -> React 看板显示策略对比
```

## 视觉要求

页面风格应是工业能源监控产品，不做夸张装饰。

- 背景使用浅灰或低饱和深色分区，重点曲线颜色清晰。
- 卡片信息密度高，但间距要足够，客户截图时能读清楚。
- 曲线图使用 ECharts tooltip、图例、缩放。
- 没有数据时显示明确空状态，不留白。
- 数据异常状态要醒目，但不使用过度警报式视觉。
- 移动端可以浏览，但首要优化桌面宽屏。

## 错误处理

前端：

- API 404：显示“暂无实时数据”。
- API 500：显示“服务异常，请检查后端日志”。
- `comparison=null`：显示“实时数据已接入，MPC 策略待生成”。
- `quality_flag` 非 `ok`：在状态条和明细表标记。

后端：

- `window_hours` 限制为 1 到 168 小时。
- 没有 telemetry 时继续返回 404，前端负责友好展示。
- 有 telemetry 但没有 MPC 结果时返回实际曲线，不返回空页面。

## 测试计划

后端：

- 测试 `window_hours` 参数过滤 telemetry 时间窗口。
- 测试没有 MPC 结果时仍返回实际曲线。
- 测试有 MPC 结果时返回对比曲线和指标。
- 保留现有 `tests/online` 全量通过。

前端：

- TypeScript 构建通过。
- 单元测试数据格式适配函数。
- 手动打开 `/dashboard?plant_id=ecloud_factory`，验证空状态、实时数据状态、MPC 对比状态。

部署：

- 本地 Docker build 成功。
- 容器健康检查 `/healthz` 正常。
- `/dashboard` 能访问 React 页面。
- 不影响服务器已有 `internship-frontend`、`internship-backend` 容器。

## 实施顺序

1. 扩展 dashboard API，让真实曲线数据先可用。
2. 新建 React 前端并接入接口。
3. 修改 FastAPI 静态文件托管和 Docker 构建。
4. 增加后端和前端测试。
5. 本地验证后推送 GitHub。
6. 再决定是否拉到服务器部署。

## 验收标准

- 浏览器打开 `/dashboard?plant_id=ecloud_factory` 显示 React 看板。
- 页面能展示最近 24/48 小时真实采集曲线。
- 没有 MPC 结果时，页面清楚说明 MPC 待生成。
- 有 MPC 结果时，页面展示实际策略和 MPC 策略曲线对比。
- 本地测试通过，Docker 构建通过。
- 服务器部署时不停止、不重建、不覆盖已有实习平台容器。
