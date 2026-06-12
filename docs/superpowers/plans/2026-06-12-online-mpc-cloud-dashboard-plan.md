# 线上 MPC 云端数据看板实施计划

> **给执行该计划的 agent/工程师：** 推荐使用 `superpowers:subagent-driven-development` 按任务执行；如果当前环境不支持 subagent，则使用 `superpowers:executing-plans`。任务使用 checkbox (`- [ ]`) 跟踪进度。

**目标：** 在大学生线上实习平台中实现一个线上 MPC 服务，能够接收云端防逆流表和储能数据，推导负荷-光伏净值，运行 MPC，比较 MPC 策略和工厂当前策略，并通过客户网页展示曲线和指标。

**架构：** 第一版不重写现有 MPC 求解器，而是在现有 MPC 外面增加服务层。服务层负责云端数据接收、数据库存储、15 分钟聚合、场景适配、MPC 调用、策略对比和网页/API 展示。等第一版展示链路稳定后，再逐步把 MPC 从 Excel 适配改成直接读写数据库。

**技术栈：** Python、FastAPI、SQLAlchemy、SQLite 本地开发、PostgreSQL 兼容表结构、pandas/openpyxl 兼容现有 MPC 数据格式、现有 Pyomo/HiGHS/LightGBM MPC 栈、Docker。

---

## 1. 选用的 Superpowers Skills

本项目建议按以下顺序使用 skills：

1. `brainstorming`
   - 用于确认需求边界、第一版目标和系统拆分。
   - 输出：确认后的设计说明。

2. `writing-plans`
   - 用于把设计拆成可执行任务。
   - 输出：本文档和后续更细的任务计划。

3. `test-driven-development`
   - 用于新增接口、数据库、聚合、对比计算和服务逻辑。
   - 要求：先写失败测试，再写实现。

4. `systematic-debugging`
   - 用于处理数据口径、MPC 输出、成本计算、曲线异常、接口失败等问题。
   - 要求：先定位根因，再修改代码。

5. `verification-before-completion`
   - 用于每个阶段完成前做验证。
   - 要求：必须有测试结果、运行命令和样例输出，不能只口头说完成。

6. `requesting-code-review`
   - 用于每个里程碑完成后的代码审查。
   - 重点审查：数据正确性、接口兼容性、线上安全边界、失败处理。

7. `subagent-driven-development` 或 `executing-plans`
   - 如果当前 Codex 环境支持 subagent，使用 `subagent-driven-development`。
   - 如果不支持，则使用 `executing-plans` 在当前会话中按任务执行。

## 2. 项目拆分

这个项目不能一次性做完，应该拆成七个可独立验证的里程碑。

| 里程碑 | 结果 |
|---|---|
| M1 数据口径与接口合同 | 明确云端字段、正负号、单位、校验规则 |
| M2 本地数据库 | 能保存原始电网数据、储能数据、15 分钟聚合数据 |
| M3 负荷-光伏净值推导 | 根据电网功率和储能功率得到 `load_minus_pv_kw` |
| M4 MPC 适配层 | 数据库聚合数据可以跑现有 MPC |
| M5 策略对比 | 计算工厂当前策略 vs MPC 策略的指标 |
| M6 Web/API 服务 | 页面和接口展示卡片、曲线、运行记录 |
| M7 Docker 与客户展示页部署 | 服务可容器化运行，并能打开客户展示网页 |

## 3. 第一版假设

第一版按以下假设实现，除非后续明确调整：

- `grid_power_kw > 0` 表示从电网购电。
- `battery_power_kw > 0` 表示储能放电。
- `battery_power_kw < 0` 表示储能充电。
- 云端能够提供带时间戳的防逆流表/电网表数据。
- 云端能够提供带时间戳的储能数据，包括 SOC。
- 如果没有独立光伏表，第一版只计算 `load_minus_pv_kw`，不强行拆分真实负荷和真实光伏。
- MPC 使用 15 分钟聚合数据。
- 网页第一版先支持一个工厂和一个时间窗口。
- 本地开发可以先用 SQLite；线上表结构保持 PostgreSQL 兼容。
- 第一版继续复用现有 Python MPC 实现。

## 4. 计划新增和修改的文件

预计新增文件：

- `microgrid_online/__init__.py`
- `microgrid_online/config.py`
- `microgrid_online/database.py`
- `microgrid_online/models.py`
- `microgrid_online/schemas.py`
- `microgrid_online/ingestion.py`
- `microgrid_online/input_mapping.py`
- `microgrid_online/signature.py`
- `microgrid_online/aggregation.py`
- `microgrid_online/mpc_adapter.py`
- `microgrid_online/comparison.py`
- `microgrid_online/api.py`
- `microgrid_online/dashboard_page.py`
- `tests/online/test_ingestion.py`
- `tests/online/test_input_mapping.py`
- `tests/online/test_input_signature.py`
- `tests/online/test_aggregation.py`
- `tests/online/test_comparison.py`
- `tests/online/test_api_contract.py`
- `tests/online/test_smoke_script.py`
- `Dockerfile`
- `.dockerignore`
- `docker-compose.online.yml`
- `.env.example`
- `scripts/online_mpc_smoke_test.py`

预计修改文件：

- `requirements.txt`
- `README.md`
- `docs/mpc_platform_interface_spec.md`
- `docs/online_mpc_deployment_plan.md`

## 5. 里程碑计划

### M1：数据口径与接口合同

**目标：** 明确云端数据字段、单位、正负号和接口校验规则。

- [x] 支持通过 `field_mapping` 配置电网功率字段名。
- [x] 支持通过 `power_signs` 配置电网功率正负号。
- [x] 支持通过 `field_mapping` 配置储能功率字段名。
- [x] 支持通过 `power_signs` 配置储能功率正负号。
- [x] 支持通过 `soc_unit` 配置 SOC 单位是 0-1 还是 0-100。
- [x] 接口文档中明确云端时间字段格式和时区要求。
- [ ] 确认防逆流表和储能表是否同频率、同时间戳。
- [x] 更新 `docs/mpc_platform_interface_spec.md` 中的字段说明。
- [ ] 增加真实或脱敏后的云端电网表和储能表示例。
- [x] 增加字段映射、SOC 单位、功率正负号和签名校验测试。
- [x] 定义并测试字段缺失、SOC 单位错误、签名缺失和签名错误的基础校验规则。
- [ ] 结合真实云端样例补充缺失时间、重复时间和功率异常阈值规则。

验证命令：

```bash
python - <<'PY'
from pathlib import Path
text = Path('docs/mpc_platform_interface_spec.md').read_text(encoding='utf-8')
required = ['grid_power_kw', 'battery_power_kw', 'soc', 'telemetry_15min']
missing = [x for x in required if x not in text]
raise SystemExit(f'missing: {missing}' if missing else 'interface spec ok')
PY
```

### M2：本地数据库

**目标：** 建立本地数据库，保存云端原始数据、聚合数据、MPC 运行记录和对比结果。

- [x] 在 `requirements.txt` 中增加 SQLAlchemy。
- [x] 创建 `microgrid_online/database.py`，提供数据库连接和 session 管理。
- [x] 创建 `microgrid_online/models.py`，定义以下表：
  - `raw_grid_meter`
  - `raw_battery`
  - `telemetry_15min`
  - `mpc_runs`
  - `strategy_comparison`
- [x] 编写使用内存 SQLite 的数据库测试。
- [x] 验证表可以创建、插入和查询。

说明：`mpc_targets`、`control_api_outbox`、`control_api_ack` 如果代码中已预留，只作为二期控制闭环扩展表；当前一期展示链路不依赖这些表。

验证命令：

```bash
python -m pytest tests/online/test_ingestion.py -q
```

### M3：负荷-光伏净值推导

**目标：** 将防逆流表和储能表数据转换成 MPC 可用的 15 分钟数据。

- [x] 实现电网原始数据接收和入库。
- [x] 实现储能原始数据接收和入库。
- [ ] 按时间戳对齐电网数据和储能数据。
- [x] 聚合成 15 分钟窗口。
- [x] 计算：

```text
load_minus_pv_kw = grid_power_kw + battery_power_kw
```

- [x] 保存 `load_minus_pv_kw`、电网功率、储能功率、SOC 和数据质量标记。
- [x] 增加测试覆盖充电、放电、缺失储能数据、重复时间戳。

验证命令：

```bash
python -m pytest tests/online/test_aggregation.py -q
```

### M4：MPC 适配层

**目标：** 将数据库中的 15 分钟聚合数据接入现有 MPC。

- [x] 创建 `microgrid_online/mpc_adapter.py`。
- [x] 将 `telemetry_15min` 数据转换成现有 MPC 需要的场景结构。
- [x] 生成临时场景文件或 DataFrame，兼容当前 `microgrid.mpc` 输入。
- [x] 新增可注入 MPC runner 的在线运行编排层。
- [x] 调用现有 MPC CLI 代码路径。
- [x] 解析真实 MPC 输出，转成结构化指标。
- [x] 保存 `mpc_runs` 和 MPC 策略指标。
- [x] 保存 MPC 策略曲线。

验证命令：

```bash
python -m pytest tests/test_mpc_forecast_history.py tests/online/test_comparison.py -q
```

### M5：策略对比

**目标：** 计算工厂当前策略和 MPC 策略的对比指标。

- [x] 计算工厂当前策略指标：
  - 最大需量。
  - 当前成本。
  - 需量费估算。
  - SOC 最小值和最大值。
  - 是否发生反送电。
- [x] 计算 MPC 策略指标：
  - MPC 最大需量。
  - MPC 成本。
  - MPC 目标峰值。
  - MPC SOC 最小值和最大值。
  - MPC 末端 SOC。
- [x] 计算对比指标：
  - 削峰量，单位 kW。
  - 削峰比例。
  - 节省成本，单位元。
  - 节省成本比例。
- [x] 将对比结果保存到数据库。

验证命令：

```bash
python -m pytest tests/online/test_comparison.py -q
```

### M6：Web/API 服务

**目标：** 提供网页和 API，展示 MPC 策略和工厂当前策略对比。

- [x] 在 `requirements.txt` 中增加 FastAPI 和 uvicorn。
- [x] 创建 `microgrid_online/api.py`。
- [x] 创建 `microgrid_online/dashboard_page.py`。
- [x] 实现接口：

```text
POST /api/v1/mpc/input-data
POST /api/v1/plants/{plant_id}/aggregate
POST /api/v1/mpc/run
GET  /api/v1/mpc/runs/{run_id}
GET  /api/v1/plants/{plant_id}/dashboard
GET  /
GET  /dashboard
```

说明：`POST /api/v1/mpc/run` 当前已完成服务层基础版，支持导出场景、调用默认 `microgrid.mpc` CLI runner、保存运行记录、对比指标和策略曲线。

- [x] 看板接口返回：
  - 当前状态卡片。
  - 工厂实际策略曲线。
  - MPC 策略曲线。
  - 最大需量对比。
  - 成本对比。
  - 节省成本。
  - 数据质量状态。
  - MPC 最新运行状态。
- [x] 第一版可以先做 JSON API，再做 HTML 页面。
- [x] 第一版 HTML 页面已接入指标卡片、曲线和明细表。
- [x] 增加 15 分钟聚合 API，支持原始数据入库后由外部任务触发聚合。

验证命令：

```bash
python -m pytest tests/online/test_api_contract.py -q
```

补充验证命令：

```bash
python -m pytest tests/online/test_input_mapping.py tests/online/test_input_signature.py -q
```

### M7：Docker 与客户展示页部署

**目标：** 将线上 MPC 服务打包成 Docker，并提供客户可以打开的展示网页。

- [x] 创建 `Dockerfile`。
- [x] 创建 `.dockerignore`。
- [x] 创建 `docker-compose.online.yml`。
- [x] 创建 `.env.example`。
- [x] 创建部署冒烟测试脚本 `scripts/online_mpc_smoke_test.py`。
- [x] 支持 `MPC_DATABASE_URL` 环境变量配置数据库地址。
- [x] 容器启动后运行 FastAPI 服务。
- [x] 容器挂载 `data/`、`outputs/`、`scenarios/`。
- [x] 增加健康检查接口或启动检查命令。
- [x] 新建客户展示网页，调用 `/api/v1/plants/{plant_id}/dashboard`。
- [x] 展示工厂当前策略曲线和 MPC 策略曲线。
- [x] 展示最大需量、成本、削峰量、节省成本等指标卡片。
- [x] 在页面上展示最新数据质量状态。

当前一期不发送任何控制目标。

验证命令：

```bash
cp .env.example .env
docker compose -f docker-compose.online.yml up -d --build
python scripts/online_mpc_smoke_test.py --base-url http://127.0.0.1:8000
```

说明：当前开发机器未安装 Docker，已经通过文件契约测试验证 Dockerfile、`.dockerignore`、Compose 文件和冒烟脚本，实际镜像构建需要在实习平台服务器或安装 Docker 的机器执行。

## 6. 看板最终效果

第一版网页需要展示：

- 当前电网功率。
- 当前 SOC。
- 当前负荷-光伏净值。
- 当前目标峰值。
- 最新 MPC 运行状态。
- 数据质量状态。
- 工厂当前最大需量。
- MPC 最大需量。
- 工厂当前成本。
- MPC 成本。
- 预计节省成本。
- 工厂实际电网功率曲线 vs MPC 电网功率曲线。
- 工厂实际储能功率曲线 vs MPC 储能功率曲线。
- 工厂实际 SOC 曲线 vs MPC SOC 曲线。
- 目标峰值线。

## 7. Docker 最终效果

Docker 部署应支持：

```bash
cp .env.example .env
docker compose -f docker-compose.online.yml up -d --build
```

或直接使用 `docker run`：

```bash
docker run \
  -p 8000:8000 \
  -e MPC_DATABASE_URL=sqlite:////app/data/mpc_online.db \
  -v "$PWD/scenarios:/app/scenarios" \
  -v "$PWD/outputs:/app/outputs" \
  -v "$PWD/data:/app/data" \
  mpc-online-platform
```

然后访问：

```text
http://localhost:8000
```

可以看到看板页面或 API 服务。

上线验证还应执行：

```bash
python scripts/online_mpc_smoke_test.py --base-url http://127.0.0.1:8000
```

该脚本会检查健康接口、云端字段映射入库、15 分钟聚合和看板读取。

## 8. 上线前必须拦截或标记的异常情况

以下情况不能直接进入 MPC 计算，或必须在客户看板上标记为数据异常：

- 当前 SOC 缺失。
- 储能不可用。
- PCS 不可用。
- MPC 求解失败。
- 数据超过允许时效。
- 运行模式是 `oracle`。
- 云端数据时间戳和 15 分钟聚合窗口不对齐。
- 电网表数据缺失。
- 储能表数据缺失。
- SOC 不在 0-1 范围内。
- 功率值明显异常。

## 9. 立即需要的数据

真实联调前，需要先拿到以下样例：

- 云端防逆流表/电网表样例。
- 云端储能表样例。
- 如果有，工厂现有策略输出样例。
- 客户展示网页需要的指标口径和展示时间范围。

拿到样例后，优先配置并验证 M1 字段映射，然后用 M2-M7 已实现链路跑通端到端演示。

## 10. 二期预留

以下内容不属于当前一期客户展示看板交付：

- 向控制策略平台发送目标峰值。
- 向控制策略平台发送目标 SOC。
- 控制策略平台拆解设备控制指令。
- 网关、PCS、储能设备实际执行闭环。
- 控制执行反馈回传。

如果后续要做控制闭环，需要单独编写二期实施计划和二期接口文档。
