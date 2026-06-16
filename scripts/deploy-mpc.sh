#!/bin/bash
set -euo pipefail

# ============================================================
# MPC 独立服务一键部署脚本
# ============================================================
# 用途：构建镜像 → 推送到 GHCR → 部署到云端服务器
# 使用：bash scripts/deploy-mpc.sh
#
# 前置条件：
#   1. 本机安装 Docker Desktop 并启用 buildx
#   2. 本机已登录 ghcr.io（docker login ghcr.io）
#   3. 本机 SSH 密钥已授权：~/.ssh/id_ed25519_aliyun_mpc
#   4. 服务器 root@8.163.49.151 可 SSH 连接
# ============================================================

# ── 配置 ──────────────────────────────────────────────
IMAGE_NAME="ghcr.io/yang6661243/mpc-online"
CONTAINER_NAME="mpc-online-platform"
SERVER="root@8.163.49.151"
SSH_KEY="$HOME/.ssh/id_ed25519_aliyun_mpc"
PORT_HOST=18000
PORT_CONTAINER=8000

# 禁止改动的容器（安全检查）
PROTECTED_CONTAINERS="internship-frontend|internship-backend"

# 颜色输出
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${GREEN}[$(date +%H:%M:%S)]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ── 生成版本标签 ─────────────────────────────────────
COMMIT_HASH=$(git rev-parse --short HEAD)
VERSION_TAG="$(date +%Y%m%d)-${COMMIT_HASH}"
FULL_IMAGE="${IMAGE_NAME}:${VERSION_TAG}"

log "版本标签: ${VERSION_TAG}"

# ── Step 1: 本地构建并推送 ───────────────────────────
log "Step 1/5: 构建 amd64 镜像并推送到 GHCR..."
docker buildx build \
  --platform linux/amd64 \
  --tag "${FULL_IMAGE}" \
  --tag "${IMAGE_NAME}:latest" \
  --push \
  .

log "镜像已推送: ${FULL_IMAGE}"

# ── Step 2: 远端安全检查 ────────────────────────────
log "Step 2/5: 远端安全检查..."

ssh -i "${SSH_KEY}" "${SERVER}" bash -s << 'SAFETY'
PROTECTED="internship-frontend|internship-backend"
RUNNING=$(docker ps --format '{{.Names}}' | grep -E "$PROTECTED" || true)
if [ -z "$RUNNING" ]; then
  echo "WARNING: 受保护容器未在运行！请确认实习平台状态。"
  docker ps --format 'table {{.Names}}\t{{.Status}}'
  exit 1
fi
echo "受保护容器运行正常:"
echo "$RUNNING"
SAFETY

# ── Step 3: 拉取新镜像 ──────────────────────────────
log "Step 3/5: 远端拉取新镜像..."
ssh -i "${SSH_KEY}" "${SERVER}" "docker pull ${FULL_IMAGE}"

# ── Step 4: 替换容器 ────────────────────────────────
log "Step 4/5: 替换 MPC 容器..."

ssh -i "${SSH_KEY}" "${SERVER}" bash -s << DEPLOY
set -e

IMAGE="${FULL_IMAGE}"
NAME="${CONTAINER_NAME}"
PORT_HOST=${PORT_HOST}
PORT_CONTAINER=${PORT_CONTAINER}
BAK="\${NAME}.bak-\$(date +%Y%m%d%H%M%S)"

# 停止并备份旧容器
if docker ps -a --format '{{.Names}}' | grep -qx "\${NAME}"; then
  docker stop "\${NAME}" || true
  docker rename "\${NAME}" "\${BAK}"
  echo "旧容器已备份为: \${BAK}"
else
  echo "未找到旧容器，跳过备份。"
fi

# 启动新容器
docker run -d \
  --name "\${NAME}" \
  --restart unless-stopped \
  -p \${PORT_HOST}:\${PORT_CONTAINER} \
  -e MPC_DATABASE_URL=sqlite:////app/data/mpc_online.db \
  -e MPC_INPUT_SIGNATURE_SECRET= \
  -e ONLINE_MPC_CORS_ORIGINS="https://ecloud.hoenergypower.cn,chrome-extension://becnmfbeidffckhenedfiahikaagpgek,chrome-extension://occmghdfgdbioibghjlhgadggnjfjoga" \
  -v /opt/mpc-online/data:/app/data \
  -v /opt/mpc-online/outputs:/app/outputs \
  -v /opt/mpc-online/scenarios:/app/scenarios \
  "\${IMAGE}"

echo "等待容器启动..."
sleep 8
DEPLOY

# ── Step 5: 验证 ─────────────────────────────────────
log "Step 5/5: 验证部署..."

ssh -i "${SSH_KEY}" "${SERVER}" bash -s << VERIFY
set -e
NAME="${CONTAINER_NAME}"
PORT=${PORT_HOST}

echo "=== 容器状态 ==="
docker ps --filter "name=\${NAME}" --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'

echo ""
echo "=== Health Check ==="
curl -fsS "http://127.0.0.1:\${PORT}/healthz"

echo ""
echo "=== 和宏华进 display-series ==="
curl -fsS "http://127.0.0.1:\${PORT}/api/v1/plants/hehong_huajin/display-series?window_hours=2" \
  | python3 -c "import json,sys;d=json.load(sys.stdin);print(f'  plant={d[\"plant_id\"]}  points={len(d[\"series\"])}  last={d[\"series\"][-1][\"time\"] if d[\"series\"] else \"empty\"}')"

echo ""
echo "=== 奥莱德 display-series ==="
curl -fsS "http://127.0.0.1:\${PORT}/api/v1/plants/ecloud_station_3341/display-series?window_hours=2" \
  | python3 -c "import json,sys;d=json.load(sys.stdin);print(f'  plant={d[\"plant_id\"]}  points={len(d[\"series\"])}  last={d[\"series\"][-1][\"time\"] if d[\"series\"] else \"empty\"}')"

echo ""
echo "=== 全部容器 ==="
docker ps --format 'table {{.Names}}\t{{.Status}}'
VERIFY

log "============================================"
log "部署完成！"
log "镜像: ${FULL_IMAGE}"
log "地址: http://${SERVER#*@}:${PORT_HOST}/dashboard?plant_id=hehong_huajin"
log "============================================"
