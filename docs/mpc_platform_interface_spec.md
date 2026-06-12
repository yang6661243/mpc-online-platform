# MPC 客户展示看板接口文档

## 1. 文档目的

本文档定义当前一期需要的接口。

当前一期目标是：

- 在大学生线上实习平台服务器上部署 Docker 化 MPC 服务。
- 接收云端防逆流表/电网表数据。
- 接收云端储能数据。
- 在实习平台本地数据库中保存和聚合数据。
- 推导 `负荷 - 光伏` 净值。
- 运行 MPC 策略计算。
- 新建客户展示网页，对比工厂当前策略和 MPC 策略。

当前一期不做：

- 不向控制策略平台发送目标 SOC。
- 不向控制策略平台发送目标峰值。
- 不控制网关、PCS 或储能设备。
- 不承诺现场设备实际按 MPC 策略执行。

控制策略平台通信闭环属于二期预留，见本文档第 10 节。

## 2. 当前接口清单

| 编号 | 接口 | 方法 | 作用 | 状态 |
|---|---|---|---|---|
| 0 | `/`、`/dashboard` | GET | 客户展示网页 | 已实现基础版 |
| 0.1 | `/healthz` | GET | 服务健康检查 | 已实现 |
| 1 | `/api/v1/mpc/input-data` | POST | 接收云端电网表和储能表原始数据，支持字段映射、正负号转换和 SOC 单位转换 | 已实现基础版 |
| 2 | `/api/v1/plants/{plant_id}/aggregate` | POST | 将原始电网表和储能表数据聚合为 15 分钟数据 | 已实现基础版 |
| 3 | `/api/v1/plants/{plant_id}/dashboard` | GET | 返回客户看板所需卡片和曲线数据 | 已实现基础版 |
| 4 | `/api/v1/mpc/run` | POST | 触发一次 MPC 计算 | 已接入真实 `microgrid.mpc` CLI runner |
| 5 | `/api/v1/mpc/runs/{run_id}` | GET | 查询某次 MPC 运行结果 | 已实现基础版 |

## 3. 通用约定

### 3.1 时间格式

所有时间字段统一使用 ISO 8601 格式，并带时区。

示例：

```text
2026-06-11T10:15:00+08:00
```

服务端入库时可以转换为统一时区保存，但接口层必须保留清晰的时区语义。

### 3.2 单位约定

| 字段类型 | 单位 |
|---|---|
| 电网功率 | kW |
| 储能功率 | kW |
| 负荷功率 | kW |
| 光伏功率 | kW |
| 电量 | kWh |
| SOC | 0-1 小数，例如 0.62 表示 62% |
| 电价 | 元/kWh |

### 3.3 功率正负号

第一版按以下约定处理：

```text
grid_power_kw > 0      表示从电网购电
grid_power_kw < 0      表示向电网反送电
battery_power_kw > 0   表示储能放电
battery_power_kw < 0   表示储能充电
```

如果云端原始字段正负号相反，必须在字段映射层转换成上述标准口径后再入库。

### 3.4 幂等键

写入接口建议携带 `request_id`。

同一个 `request_id` 重复提交时，应返回同一处理结果，避免重复入库。

### 3.5 鉴权建议

建议请求头携带：

```http
X-App-Id: cloud-data-service
X-Timestamp: 2026-06-11T10:15:00+08:00
X-Request-Id: req_20260611_101500_001
X-Signature: <签名>
```

签名方式第一版可以使用共享密钥 HMAC-SHA256。若实习平台已有统一网关鉴权，应优先复用平台鉴权。

当前服务支持可选签名校验：

- 如果未配置 `MPC_INPUT_SIGNATURE_SECRET`，接口不强制校验签名，方便本地开发和内网联调。
- 如果配置了 `MPC_INPUT_SIGNATURE_SECRET`，请求必须携带 `X-Timestamp`、`X-Request-Id`、`X-Signature`。

签名计算方式：

```text
message = X-Timestamp + "\n" + X-Request-Id + "\n" + raw_request_body
X-Signature = "sha256=" + HMAC_SHA256(secret, message).hexdigest()
```

注意：`raw_request_body` 必须使用 HTTP 请求实际发送的原始 body 字节，不能在签名前后改变 JSON 空格、字段顺序或编码。

## 4. 接口 1：接收云端原始数据

### 4.1 基本信息

```text
POST /api/v1/mpc/input-data
```

接口提供方：大学生线上实习平台。

调用方：云端数据同步服务、采集服务或实习平台自己的定时同步任务。

作用：接收防逆流表/电网表数据和储能数据，并保存到本地数据库。

### 4.2 请求体结构

```json
{
  "request_id": "req_20260611_101500_001",
  "plant_id": "factory_demo",
  "data_type": "grid_meter",
  "generated_at": "2026-06-11T10:15:00+08:00",
  "field_mapping": {},
  "power_signs": {},
  "soc_unit": "ratio",
  "records": []
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `request_id` | string | 是 | 请求幂等 ID |
| `plant_id` | string | 是 | 工厂 ID |
| `data_type` | string | 是 | 数据类型，目前支持 `grid_meter`、`battery` |
| `generated_at` | string | 是 | 数据生成或推送时间 |
| `field_mapping` | object | 否 | 字段映射，key 为标准字段名，value 为云端原始字段名 |
| `power_signs` | object | 否 | 功率正负号转换，key 为标准功率字段名，value 只能是 `1` 或 `-1` |
| `soc_unit` | string | 否 | SOC 单位，支持 `ratio`/`0-1` 或 `percent`/`0-100`，默认 `ratio` |
| `records` | array | 是 | 数据记录数组 |

### 4.3 字段映射、正负号和 SOC 单位

接口内部统一使用标准字段：

| data_type | 标准字段 |
|---|---|
| `grid_meter` | `time`、`grid_power_kw` |
| `battery` | `time`、`battery_power_kw`、`soc`、`battery_available`、`pcs_available` |

如果云端字段名已经和标准字段一致，可以不传 `field_mapping`。

如果云端字段名不同，通过 `field_mapping` 指定映射：

```json
{
  "field_mapping": {
    "time": "ts",
    "grid_power_kw": "p_grid"
  }
}
```

如果云端功率正负号和本文档标准相反，通过 `power_signs` 乘以 `-1`：

```json
{
  "power_signs": {
    "grid_power_kw": -1
  }
}
```

如果云端 SOC 是百分比，例如 `58` 表示 58%，传：

```json
{
  "soc_unit": "percent"
}
```

服务会在入库前转换成 `0.58`。

### 4.4 `grid_meter` 示例

```json
{
  "request_id": "req_20260611_101500_grid",
  "plant_id": "factory_demo",
  "data_type": "grid_meter",
  "generated_at": "2026-06-11T10:15:00+08:00",
  "records": [
    {
      "time": "2026-06-11T10:15:00+08:00",
      "grid_power_kw": 410.2
    }
  ]
}
```

云端字段名和正负号需要转换时：

```json
{
  "request_id": "req_20260611_101500_grid_mapped",
  "plant_id": "factory_demo",
  "data_type": "grid_meter",
  "generated_at": "2026-06-11T10:15:00+08:00",
  "field_mapping": {
    "time": "ts",
    "grid_power_kw": "p_grid"
  },
  "power_signs": {
    "grid_power_kw": -1
  },
  "records": [
    {
      "ts": "2026-06-11T10:15:00+08:00",
      "p_grid": -410.2
    }
  ]
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `time` | string | 是 | 采样时间 |
| `grid_power_kw` | number | 是 | 电网功率，正数表示购电 |

可选扩展字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `grid_energy_import_kwh` | number | 累计购电电量 |
| `grid_energy_export_kwh` | number | 累计反送电量 |
| `meter_id` | string | 表计编号 |
| `quality` | string | 数据质量标记 |

### 4.5 `battery` 示例

```json
{
  "request_id": "req_20260611_101500_battery",
  "plant_id": "factory_demo",
  "data_type": "battery",
  "generated_at": "2026-06-11T10:15:00+08:00",
  "records": [
    {
      "time": "2026-06-11T10:15:00+08:00",
      "battery_power_kw": -30.0,
      "soc": 0.58,
      "battery_available": true,
      "pcs_available": true
    }
  ]
}
```

云端 SOC 是百分比时：

```json
{
  "request_id": "req_20260611_101500_battery_mapped",
  "plant_id": "factory_demo",
  "data_type": "battery",
  "generated_at": "2026-06-11T10:15:00+08:00",
  "field_mapping": {
    "time": "ts",
    "battery_power_kw": "p_bess",
    "soc": "soc_pct"
  },
  "soc_unit": "percent",
  "records": [
    {
      "ts": "2026-06-11T10:15:00+08:00",
      "p_bess": -30.0,
      "soc_pct": 58
    }
  ]
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `time` | string | 是 | 采样时间 |
| `battery_power_kw` | number | 是 | 储能功率，正数放电、负数充电 |
| `soc` | number | 是 | 储能 SOC，范围 0-1 |
| `battery_available` | boolean | 否 | 储能是否可用 |
| `pcs_available` | boolean | 否 | PCS 是否可用 |

### 4.6 成功响应

```json
{
  "success": true,
  "request_id": "req_20260611_101500_grid",
  "plant_id": "factory_demo",
  "data_type": "grid_meter",
  "accepted_count": 1,
  "message": "accepted"
}
```

### 4.7 失败响应

```json
{
  "success": false,
  "request_id": "req_20260611_101500_battery",
  "error_code": "INVALID_SOC",
  "message": "soc must be between 0 and 1"
}
```

建议错误码：

| 错误码 | 含义 |
|---|---|
| `UNAUTHORIZED` | 鉴权失败 |
| `INVALID_SIGNATURE` | 签名错误 |
| `INVALID_PLANT` | 工厂 ID 不存在 |
| `INVALID_DATA_TYPE` | 数据类型不支持 |
| `INVALID_TIME_FORMAT` | 时间格式错误 |
| `MISSING_FIELD` | 缺少必填字段 |
| `INVALID_FIELD_MAPPING` | 字段映射错误或映射后的源字段缺失 |
| `INVALID_SOC` | SOC 不在 0-1 范围 |
| `INVALID_POWER_VALUE` | 功率值异常 |
| `DUPLICATE_REQUEST` | 重复请求 |
| `INTERNAL_ERROR` | 服务内部错误 |

## 5. 15 分钟聚合与净值推导

实习平台接收到原始数据后，本地聚合生成 `telemetry_15min`。

第一版不要求云端直接上传 `telemetry_15min`。

聚合字段：

| 字段 | 说明 |
|---|---|
| `start_time` | 15 分钟窗口开始时间 |
| `end_time` | 15 分钟窗口结束时间 |
| `grid_power_kw_avg` | 窗口平均电网功率 |
| `grid_power_kw_max` | 窗口最大电网功率 |
| `battery_power_kw_avg` | 窗口平均储能功率 |
| `soc_start` | 窗口开始 SOC |
| `soc_end` | 窗口结束 SOC |
| `load_minus_pv_kw_avg` | 推导出的 `负荷 - 光伏` 净值 |
| `quality_flag` | 数据质量标记 |

推导公式：

```text
load_minus_pv_kw_avg = grid_power_kw_avg + battery_power_kw_avg
```

如果某个窗口缺少储能数据，则该窗口应标记为 `missing_battery`，默认不进入 MPC 计算。

## 6. 接口 2：触发 15 分钟聚合

### 6.1 基本信息

```text
POST /api/v1/plants/{plant_id}/aggregate
```

接口提供方：大学生线上实习平台。

调用方：实习平台定时任务、云端数据同步任务或运维冒烟测试脚本。

作用：把已经入库的原始电网表和储能表数据聚合成 `telemetry_15min`，供看板和 MPC 使用。

### 6.2 请求体结构

```json
{
  "start_time": "2026-06-11T10:00:00+08:00",
  "end_time": "2026-06-11T10:15:00+08:00",
  "window_minutes": 15
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `start_time` | string | 是 | 聚合开始时间 |
| `end_time` | string | 是 | 聚合结束时间 |
| `window_minutes` | integer | 否 | 聚合窗口分钟数，默认 15 |

### 6.3 成功响应

```json
{
  "success": true,
  "plant_id": "factory_demo",
  "window_count": 1,
  "quality_counts": {
    "ok": 1
  }
}
```

`quality_counts` 用于快速判断当前窗口是否缺电网表或储能表数据。

## 7. 接口 3：客户看板数据

客户展示网页访问地址：

```text
GET /
GET /dashboard?plant_id=factory_demo
```

网页会读取 `GET /api/v1/plants/{plant_id}/dashboard`。

### 7.1 基本信息

```text
GET /api/v1/plants/{plant_id}/dashboard
```

接口提供方：大学生线上实习平台。

调用方：客户展示网页。

作用：返回页面展示所需的当前状态、策略对比指标和曲线数据。

### 7.2 响应示例

```json
{
  "plant_id": "factory_demo",
  "current": {
    "time": "2026-06-11T10:15:00+08:00",
    "grid_power_kw": 410.2,
    "battery_power_kw": -30.0,
    "load_minus_pv_kw": 380.2,
    "soc": 0.58,
    "quality_flag": "ok"
  },
  "comparison": {
    "run_id": "mpc_20260611_101500",
    "actual_peak_kw": 520.4,
    "mpc_peak_kw": 389.2,
    "peak_reduction_kw": 131.2,
    "peak_reduction_pct": 25.2,
    "actual_cost_yuan": 1820.5,
    "mpc_cost_yuan": 1506.3,
    "cost_saving_yuan": 314.2,
    "cost_saving_pct": 17.3
  },
  "series": [
    {
      "time": "2026-06-11T10:00:00",
      "actual_grid_power_kw": 410.2,
      "actual_battery_power_kw": -30.0,
      "actual_soc": 0.58,
      "load_minus_pv_kw": 380.2,
      "mpc_grid_power_kw": 389.2,
      "mpc_battery_power_kw": -8.0,
      "mpc_soc": 0.60,
      "buy_price": 0.8,
      "sell_price": 0.3
    }
  ]
}
```

### 7.3 页面建议展示

顶部卡片：

- 当前电网功率。
- 当前储能 SOC。
- 当前储能功率。
- 当前 `负荷 - 光伏` 净值。
- 工厂当前最大需量。
- MPC 最大需量。
- 预计削峰量。
- 预计节省成本。

曲线：

- 工厂当前电网功率 vs MPC 电网功率。
- 工厂当前储能功率 vs MPC 储能功率。
- 工厂当前 SOC vs MPC SOC。
- `负荷 - 光伏` 净值。
- 目标峰值线或最大需量参考线。
- 电价曲线。

## 8. 接口 4：触发 MPC 计算

### 8.1 基本信息

```text
POST /api/v1/mpc/run
```

状态：已实现服务层基础版。

作用：从数据库读取指定工厂和时间范围的数据，运行 MPC，并保存结果。

说明：当前接口已经具备运行记录、场景导出、调用 `microgrid.mpc`、解析结果 Excel、保存指标和结果查询能力。若部署时显式关闭默认 runner 且没有注入 runner，会返回 `501`，避免把占位结果误展示成真实 MPC 策略。

### 8.2 请求体建议

```json
{
  "request_id": "req_20260611_101500_run",
  "plant_id": "factory_demo",
  "start_time": "2026-06-11T00:00:00+08:00",
  "end_time": "2026-06-11T23:45:00+08:00",
  "profile": "customer_demo_base"
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `request_id` | string | 是 | 请求幂等 ID |
| `plant_id` | string | 是 | 工厂 ID |
| `start_time` | string | 是 | MPC 输入数据开始时间 |
| `end_time` | string | 是 | MPC 输入数据结束时间 |
| `profile` | string | 否 | MPC 配置 profile |

### 8.3 响应建议

```json
{
  "success": true,
  "run_id": "mpc_20260611_101500",
  "status": "running",
  "message": "mpc run accepted"
}
```

## 9. 接口 5：查询 MPC 运行结果

### 9.1 基本信息

```text
GET /api/v1/mpc/runs/{run_id}
```

状态：已实现基础版。

作用：查询某次 MPC 的运行状态、指标和曲线数据。

### 9.2 响应建议

```json
{
  "run_id": "mpc_20260611_101500",
  "plant_id": "factory_demo",
  "status": "succeeded",
  "started_at": "2026-06-11T10:15:00+08:00",
  "finished_at": "2026-06-11T10:15:12+08:00",
  "metrics": {
    "actual_peak_kw": 520.4,
    "mpc_peak_kw": 389.2,
    "saving_yuan": 314.2
  },
  "error_message": null
}
```

`status` 建议取值：

| status | 含义 |
|---|---|
| `queued` | 已排队 |
| `running` | 运行中 |
| `succeeded` | 运行成功 |
| `failed` | 运行失败 |

## 10. 当前数据链路

```mermaid
flowchart LR
  A["云端防逆流表/电网表"] --> B["POST /api/v1/mpc/input-data"]
  C["云端储能数据"] --> B
  B --> D["本地数据库"]
  D --> E["POST /api/v1/plants/{plant_id}/aggregate"]
  E --> F["推导 负荷-光伏 净值"]
  F --> G["MPC 计算"]
  G --> H["策略对比"]
  H --> I["GET /api/v1/plants/{plant_id}/dashboard"]
  I --> J["客户展示网页"]
```

## 11. 二期预留：控制策略平台通信

以下内容不是当前一期交付范围。

二期如果要做真实控制闭环，再单独定义：

- 实习平台向控制策略平台发送目标峰值。
- 实习平台向控制策略平台发送目标 SOC。
- 控制策略平台返回目标接收状态。
- 控制策略平台拆解成网关、PCS、储能控制指令。
- 现场执行反馈回传实习平台。

二期接口文档应单独编写，避免和当前客户展示看板接口混在一起。

## 12. 当前待确认事项

上线前需要补齐以下信息：

- 云端防逆流表/电网表真实字段名。
- 云端储能表真实字段名。
- 电网功率正负号是否和本文档一致。
- 储能功率正负号是否和本文档一致。
- SOC 单位是 0-1 还是 0-100。
- 云端数据时间戳格式和时区。
- 云端数据推送频率。
- 电价数据来源。
- 客户展示网页需要展示的工厂名称、时间范围和指标口径。
