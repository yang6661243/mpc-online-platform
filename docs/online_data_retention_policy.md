# 线上 MPC 数据口径与数据库维护策略

## 1. 数据口径

线上 MPC 服务统一使用以下功率方向：

| 字段 | 口径 |
|---|---|
| `grid_power_kw > 0` | 工厂从电网购电 |
| `grid_power_kw < 0` | 工厂向电网反送电 |
| `battery_power_kw > 0` | 储能放电 |
| `battery_power_kw < 0` | 储能充电 |

当前没有独立光伏表时，净负荷按以下公式推导：

```text
load_minus_pv_kw = grid_power_kw + battery_power_kw
```

如果后续接入独立光伏表，可以升级为：

```text
load_kw = grid_power_kw + battery_power_kw + pv_kw
```

## 2. SOC 处理

后端入库统一保存 `0-1` 小数。

采集端或上游接口可以传两种格式：

| `soc_unit` | 示例输入 | 入库值 |
|---|---:|---:|
| `ratio` | `0.43` | `0.43` |
| `percent` | `43` | `0.43` |

入库前会校验 SOC 必须在 `0-1` 范围内。超出范围时请求会被拒绝。

## 3. 缺数处理

当前不做插值，也不补假数据。

原始入库规则：

- `grid_meter` 必须包含 `time` 和 `grid_power_kw`。
- `battery` 必须包含 `time`、`battery_power_kw` 和 `soc`。
- 功率和 SOC 必须是有限数字，不能是空值、`NaN` 或无穷大。
- 同一工厂同一时间戳重复上传时，后上传的数据会覆盖同一时间点旧值。

15 分钟聚合规则：

| 情况 | `quality_flag` | `load_minus_pv_kw_avg` | 是否可用于 MPC |
|---|---|---:|---|
| 电网和储能都有样本 | `ok` | 电网均值 + 储能均值 | 是 |
| 缺电网样本 | `missing_grid` | 空 | 否 |
| 缺储能样本或 SOC | `missing_battery` | 空 | 否 |
| 电网和储能都缺 | `missing_grid_battery` | 空 | 否 |

MPC 运行只接受 `quality_flag = ok` 的连续窗口。

## 4. 数据保留策略

默认保留策略：

| 数据 | 表 | 默认保留 |
|---|---|---:|
| 原始电网功率 | `raw_grid_meter` | 30 天 |
| 原始储能功率和 SOC | `raw_battery` | 30 天 |
| 15 分钟聚合数据 | `telemetry_15min` | 365 天 |
| MPC 运行记录 | `mpc_runs` | 365 天 |
| MPC 目标值 | `mpc_targets` | 365 天 |
| 策略对比指标 | `strategy_comparison` | 365 天 |
| 策略曲线点 | `strategy_curve_points` | 365 天 |
| 控制平台出站/回执记录 | `control_api_outbox`、`control_api_ack` | 365 天 |
| SQLite 备份文件 | `*.db` 备份 | 30 天 |

## 5. 维护命令

维护命令默认是 dry-run，只统计将要删除的行，不创建备份，也不删除数据：

```bash
python -m microgrid_online.db_maintenance \
  --database-url sqlite:////app/data/mpc_online.db \
  --backup-dir /app/data/backups
```

实际执行时必须显式传 `--execute`。执行流程是先创建 SQLite 备份，再删除过期数据：

```bash
python -m microgrid_online.db_maintenance \
  --database-url sqlite:////app/data/mpc_online.db \
  --backup-dir /app/data/backups \
  --execute
```

也可以通过环境变量配置：

```bash
MPC_DB_BACKUP_DIR=/app/data/backups
MPC_RAW_RETENTION_DAYS=30
MPC_TELEMETRY_RETENTION_DAYS=365
MPC_RESULT_RETENTION_DAYS=365
MPC_CONTROL_API_RETENTION_DAYS=365
MPC_BACKUP_RETENTION_DAYS=30
```

## 6. 推荐定时任务

正式部署后建议每天凌晨执行一次维护任务。

Docker 宿主机 cron 示例：

```cron
15 3 * * * docker exec mpc-online-platform python -m microgrid_online.db_maintenance --execute >> /opt/mpc-online/data/db_maintenance.log 2>&1
```

执行前提：

- `mpc-online-platform` 是 MPC 服务容器名。
- `/app/data` 已挂载到宿主机持久化目录。
- 不要在实习平台容器内执行该命令。

上线前建议先运行 dry-run，确认待删除行数符合预期。
