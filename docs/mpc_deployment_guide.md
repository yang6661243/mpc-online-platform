# MPC 独立服务部署指南

## 架构概览

服务器 `8.163.49.151` 上运行三组容器：

| 容器 | 端口 | 归属 | 部署方式 |
|---|---|---|---|
| `mpc-online-platform` | `18000:8000` | MPC 独立服务 | **本文档覆盖** |
| `internship-frontend` | `8080:80` | 大学生实习平台 | **禁止触碰** |
| `internship-backend` | `5000:5002` | 大学生实习平台 | **禁止触碰** |

---

## 一键部署（推荐）

```bash
# 前置：本机需安装 Docker Desktop + 登录 ghcr.io
docker login ghcr.io

# 执行一键脚本
bash scripts/deploy-mpc.sh
```

脚本会自动完成：构建 → 推送 → 安全检查 → 替换容器 → 验证。

---

## 手动部署

### 前置条件

- 本机：Docker Desktop（启用 buildx）、已登录 `ghcr.io`
- SSH：`~/.ssh/id_ed25519_aliyun_mpc` 已授权到 `root@8.163.49.151`

### Step 1: 构建并推送镜像

```bash
COMMIT=$(git rev-parse --short HEAD)
TAG="$(date +%Y%m%d)-${COMMIT}"
IMAGE="ghcr.io/yang6661243/mpc-online:${TAG}"

docker buildx build \
  --platform linux/amd64 \
  --tag "${IMAGE}" \
  --tag ghcr.io/yang6661243/mpc-online:latest \
  --push \
  .
```

### Step 2: SSH 到服务器

```bash
ssh -i ~/.ssh/id_ed25519_aliyun_mpc root@8.163.49.151
```

### Step 3: 安全检查

```bash
# 确认实习平台容器在运行
docker ps --format 'table {{.Names}}\t{{.Status}}' | grep -E 'internship-frontend|internship-backend'
```

> 两个都必须显示 `Up`，否则**停止操作**并排查。

### Step 4: 拉取新镜像

```bash
docker pull ghcr.io/yang6661243/mpc-online:<TAG>
```

将 `<TAG>` 替换为 Step 1 生成的版本标签。

### Step 5: 替换容器

```bash
# 停旧容器
docker stop mpc-online-platform 2>/dev/null || true

# 改名备份
docker rename mpc-online-platform mpc-online-platform.bak-$(date +%Y%m%d%H%M%S) 2>/dev/null || true

# 启动新容器（这是一整行，不要换行）
docker run -d \
  --name mpc-online-platform \
  --restart unless-stopped \
  -p 18000:8000 \
  -e MPC_DATABASE_URL=sqlite:////app/data/mpc_online.db \
  -e MPC_INPUT_SIGNATURE_SECRET= \
  -e ONLINE_MPC_CORS_ORIGINS="https://ecloud.hoenergypower.cn,chrome-extension://becnmfbeidffckhenedfiahikaagpgek,chrome-extension://occmghdfgdbioibghjlhgadggnjfjoga" \
  -v /opt/mpc-online/data:/app/data \
  -v /opt/mpc-online/outputs:/app/outputs \
  -v /opt/mpc-online/scenarios:/app/scenarios \
  ghcr.io/yang6661243/mpc-online:<TAG>
```

### Step 6: 验证

```bash
# 等容器启动
sleep 8

# 容器状态
docker ps --filter name=mpc-online-platform --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'

# Health check
curl -fsS http://127.0.0.1:18000/healthz

# 和宏华进
curl -fsS 'http://127.0.0.1:18000/api/v1/plants/hehong_huajin/display-series?window_hours=2' \
  | python3 -m json.tool | head -30

# 奥莱德
curl -fsS 'http://127.0.0.1:18000/api/v1/plants/ecloud_station_3341/display-series?window_hours=2' \
  | python3 -m json.tool | head -30

# 确认实习平台未受影响
docker ps --format 'table {{.Names}}\t{{.Status}}'
```

### Step 7: 前端验收

浏览器打开：
- `http://8.163.49.151:18000/dashboard?plant_id=hehong_huajin`
- `http://8.163.49.151:18000/dashboard?plant_id=ecloud_station_3341`

检查主曲线有数据、tooltip 能区分真实/插值、顶部栏时间显示正确。

---

## 回滚

如果新版本有问题，回滚到上一个备份容器：

```bash
# 查看备份容器
docker ps -a --format '{{.Names}}' | grep mpc-online-platform.bak

# 停掉当前容器
docker stop mpc-online-platform

# 改名当前为坏版本
docker rename mpc-online-platform mpc-online-platform.bad

# 恢复备份
docker rename mpc-online-platform.bak-<时间戳> mpc-online-platform

# 启动
docker start mpc-online-platform

# 验证
curl -fsS http://127.0.0.1:18000/healthz
```

---

## 日常运维命令

```bash
# 查看日志
docker logs --tail 100 -f mpc-online-platform

# 进入容器
docker exec -it mpc-online-platform bash

# 查看数据库
docker exec -it mpc-online-platform sqlite3 /app/data/mpc_online.db \
  "SELECT end_time, quality_flag FROM telemetry_15min ORDER BY end_time DESC LIMIT 5;"

# 查看原始数据最新时间
docker exec -it mpc-online-platform sqlite3 /app/data/mpc_online.db \
  "SELECT plant_id, time FROM raw_grid_meter ORDER BY time DESC LIMIT 3;"
```
