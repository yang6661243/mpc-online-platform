# MPC 客户展示看板部署实施方案

## 1. 当前目标

当前项目目标是：

在大学生线上实习平台服务器上部署 Docker 化 MPC 服务，并新建一个网页，用于给客户展示：

- 工厂当前策略曲线。
- 我们的 MPC 策略曲线。
- 两种策略的最大需量对比。
- 两种策略的成本对比。
- MPC 预计削峰效果。
- MPC 预计节省成本。
- 储能 SOC 曲线对比。
- 电网功率曲线对比。

当前一期不做控制策略平台通信闭环，不做网关控制，不做目标下发。

控制策略平台通信可以作为二期扩展，当前文档只关注“数据接入、MPC 计算、策略对比、网页展示”。

## 2. 当前系统边界

大学生线上实习平台负责：

- 部署 MPC 服务。
- 接收或同步云端防逆流表数据。
- 接收或同步云端储能数据。
- 保存数据到本地数据库。
- 聚合 15 分钟数据。
- 根据电网数据和储能数据推导 `负荷 - 光伏` 净值。
- 调用现有 MPC 算法。
- 计算工厂当前策略和 MPC 策略的对比指标。
- 提供网页展示给客户。

工厂云端数据源负责：

- 提供防逆流表/电网表数据。
- 提供储能数据。
- 如有条件，后续可提供光伏表、负荷表、电价表、设备状态表。

MPC 服务当前不负责：

- 直接控制网关。
- 向 PCS 或储能设备下发指令。
- 给控制策略平台发送控制目标。
- 保证现场实际执行 MPC 策略。

## 3. 当前架构

```mermaid
flowchart LR
  A["云端防逆流表/电网表"] --> B["实习平台数据接收接口"]
  C["云端储能表"] --> B
  B --> D["本地数据库"]
  D --> E["15分钟聚合与净负荷推导"]
  E --> F["Docker 内 MPC 服务"]
  F --> G["策略对比计算"]
  G --> H["客户展示网页"]
```

## 4. 数据处理口径

第一版按以下约定处理：

```text
grid_power_kw > 0      表示从电网购电
battery_power_kw > 0   表示储能放电
battery_power_kw < 0   表示储能充电
```

如果没有独立光伏表，则第一版不拆分真实负荷和真实光伏，只计算：

```text
load_minus_pv_kw = grid_power_kw + battery_power_kw
```

也就是：

```text
负荷 - 光伏 = 电网功率 + 储能功率
```

这个值可以作为 MPC 第一版的净负荷输入，用于策略对比展示。

如果后续接入独立光伏表，则可以升级为：

```text
load_kw = grid_power_kw + battery_power_kw + pv_kw
```

## 5. 线上运行流程

第一版建议按以下流程运行：

1. 云端数据进入实习平台：
   - 防逆流表/电网表数据。
   - 储能功率和 SOC 数据。

2. 数据入库：
   - 原始电网数据写入 `raw_grid_meter`。
   - 原始储能数据写入 `raw_battery`。

3. 聚合 15 分钟数据：
   - 平均电网功率。
   - 平均储能功率。
   - SOC 起止值。
   - `load_minus_pv_kw`。

4. 运行 MPC：
   - 将 15 分钟聚合数据转换为现有 MPC 场景格式。
   - 调用现有 MPC。
   - 得到 MPC 策略下的电网功率、储能功率、SOC、成本等。

5. 计算策略对比：
   - 工厂当前最大需量。
   - MPC 最大需量。
   - 当前策略成本。
   - MPC 策略成本。
   - 削峰量。
   - 削峰比例。
   - 节省成本。
   - 节省比例。

6. 网页展示：
   - 顶部卡片展示关键指标。
   - 曲线展示工厂当前策略和 MPC 策略。
   - 表格展示历史运行记录。

## 6. 当前需要提供的接口

第一版实习平台服务器需要提供这些接口：

```text
POST /api/v1/mpc/input-data
```

作用：接收云端电网表和储能表数据。

当前已支持：

```text
data_type = grid_meter
data_type = battery
```

同时支持：

```text
field_mapping  字段映射
power_signs    功率正负号转换
soc_unit       SOC 单位转换，支持 ratio 和 percent
```

如果配置 `MPC_INPUT_SIGNATURE_SECRET`，该接口会启用 HMAC-SHA256 签名校验。

```text
POST /api/v1/plants/{plant_id}/aggregate
```

作用：把已经入库的原始电网数据和储能数据聚合为 15 分钟 `telemetry_15min`，供看板和 MPC 使用。

```text
GET /api/v1/plants/{plant_id}/dashboard
```

作用：返回网页看板需要的当前状态和策略对比数据。

```text
POST /api/v1/mpc/run
```

作用：触发一次 MPC 计算。当前已接入真实 `microgrid.mpc` CLI runner。

```text
GET /api/v1/mpc/runs/{run_id}
```

作用：查询某次 MPC 运行状态和结果。当前已实现基础版。

## 7. 客户网页展示效果

网页第一版建议包含三块。

### 7.1 顶部指标卡片

- 当前电网功率。
- 当前储能 SOC。
- 当前储能功率。
- 当前 `负荷 - 光伏` 净值。
- 工厂当前最大需量。
- MPC 最大需量。
- 预计削峰量。
- 预计节省成本。

### 7.2 曲线对比

至少展示：

- 工厂当前电网功率 vs MPC 电网功率。
- 工厂当前储能功率 vs MPC 储能功率。
- 工厂当前 SOC vs MPC SOC。
- `负荷 - 光伏` 净值曲线。
- 目标峰值线或最大需量参考线。
- 电价曲线。

### 7.3 历史运行记录

展示每次 MPC 运行：

- 运行时间。
- 运行状态。
- 数据时间范围。
- 工厂当前最大需量。
- MPC 最大需量。
- 成本节省。
- 输出文件或详情链接。

## 8. 当前已完成内容

已完成：

- `microgrid_online` 后端雏形。
- 原始电网数据入库。
- 原始储能数据入库。
- 15 分钟聚合。
- `load_minus_pv_kw = grid_power_kw + battery_power_kw` 推导。
- MPC 场景 Excel 导出适配。
- MPC 在线运行服务层基础版。
- 真实 `microgrid.mpc` CLI runner 接入。
- MPC 策略曲线解析和入库。
- 看板接口返回真实曲线数据。
- `POST /api/v1/mpc/run`。
- `GET /api/v1/mpc/runs/{run_id}`。
- `POST /api/v1/plants/{plant_id}/aggregate`。
- `GET /` 和 `GET /dashboard` 客户展示网页基础版。
- `GET /healthz` 健康检查接口。
- `Dockerfile`。
- `.dockerignore`。
- `docker-compose.online.yml`。
- `.env.example`。
- `scripts/online_mpc_smoke_test.py` 部署冒烟测试脚本。
- `MPC_DATABASE_URL` 环境变量配置数据库地址。
- 云端字段映射基础版。
- 功率正负号转换。
- SOC 百分比转 0-1。
- 可选 HMAC-SHA256 请求签名校验。
- 数据口径与数据库维护策略文档：`docs/online_data_retention_policy.md`。
- SQLite 备份和过期数据清理命令：`python -m api.db_maintenance`。
- 数据保留默认配置：
  - 原始电网和储能数据保留 30 天。
  - 15 分钟聚合数据保留 365 天。
  - MPC 运行结果、策略对比和曲线保留 365 天。
  - 备份文件保留 30 天。
- 实际策略和 MPC 策略对比指标函数。
- 看板基础 API。
- 在线服务测试。

每次上线前需要重新执行 `tests/online` 和前端测试，不能依赖历史测试数量。

## 9. 下一步工作

下一步按优先级执行：

1. 在服务器验证 Docker 构建和运行。
   - 当前开发机器没有 `docker` 命令，已完成文件和接口测试，但未在本机执行镜像构建。
   - 在实习平台服务器执行 `docker compose -f docker-compose.online.yml up -d --build`。
   - 启动容器并访问 `/healthz` 和 `/dashboard`。
   - 挂载 `data/`、`outputs/`、`scenarios/`。
   - 执行 `python scripts/online_mpc_smoke_test.py --base-url http://127.0.0.1:8000`。

2. 拿真实云端字段样例并联调映射。
   - 根据云端防逆流表/电网表样例配置 `field_mapping`。
   - 根据云端储能表样例配置 `field_mapping`。
   - 根据真实数据确认 `power_signs` 是否需要取反。
   - 根据真实 SOC 口径确认 `soc_unit = ratio` 还是 `percent`。
   - 如果线上入口走公网或跨系统调用，配置 `MPC_INPUT_SIGNATURE_SECRET` 并要求调用方按接口文档签名。

3. 接入实习平台前端框架。
   - 如果线上平台已有统一菜单、权限、主题，需要把当前页面改造成平台页面组件。
   - 如果先独立部署，可以继续使用当前 FastAPI 内置页面。

4. 配置数据库日常维护任务。
   - 先执行 dry-run，确认待删除行数。
   - 再配置每天凌晨执行 `python -m api.db_maintenance --execute`。
   - 维护命令只在 `mpc-online-platform` 容器内运行，不进入或修改实习平台容器。

## 10. 二期预留

以下内容不属于当前一期交付：

- 向控制策略平台发送目标 SOC。
- 向控制策略平台发送目标峰值。
- 控制策略平台拆解控制策略。
- 网关控制。
- 储能设备实际执行闭环。

如果后续要做控制闭环，再单独建立二期实施计划和接口文档。

## 11. Docker 部署命令

在安装 Docker 的服务器上执行：

```bash
cp .env.example .env
# 按需修改 .env 中的 MPC_INPUT_SIGNATURE_SECRET 等变量
docker compose -f docker-compose.online.yml up -d --build
```

也可以不用 Compose，直接构建镜像：

```bash
docker build -t mpc-online-platform .
```

再启动服务：

```bash
docker run -d \
  --name mpc-online-platform \
  -p 8000:8000 \
  -e MPC_DATABASE_URL=sqlite:////app/data/mpc_online.db \
  -v "$PWD/data:/app/data" \
  -v "$PWD/outputs:/app/outputs" \
  -v "$PWD/scenarios:/app/scenarios" \
  mpc-online-platform
```

健康检查：

```bash
curl http://127.0.0.1:8000/healthz
```

预期返回：

```json
{"status":"ok","service":"online-mpc"}
```

客户页面：

```text
http://服务器IP:8000/dashboard?plant_id=工厂ID
```

部署冒烟测试：

```bash
python scripts/online_mpc_smoke_test.py \
  --base-url http://127.0.0.1:8000 \
  --plant-id smoke_factory
```

如果启用了 `MPC_INPUT_SIGNATURE_SECRET`，脚本会自动读取该环境变量并给 `/api/v1/mpc/input-data` 请求加签。

## 12. 数据库维护命令

查看将要清理的数据，不删除：

```bash
docker exec mpc-online-platform \
  python -m api.db_maintenance
```

实际执行备份和清理：

```bash
docker exec mpc-online-platform \
  python -m api.db_maintenance --execute
```

推荐宿主机 cron：

```cron
15 3 * * * docker exec mpc-online-platform python -m api.db_maintenance --execute >> /opt/mpc-online/data/db_maintenance.log 2>&1
```

该命令只维护 MPC 容器自己的 SQLite 数据库和备份目录，不应作用于 `internship-frontend`、`internship-backend` 或实习平台数据库。
