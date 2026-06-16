#!/bin/bash
set -euo pipefail

# ============================================================
# MPC 独立服务一键部署脚本
# ============================================================
# 用途：提交改动 → 测试 → 构建镜像 → 推送 GHCR → 云端部署
#
# 使用：
#   bash scripts/deploy-mpc.sh              # 完整流程
#   bash scripts/deploy-mpc.sh --skip-tests # 跳过测试（快速部署）
#   bash scripts/deploy-mpc.sh --dry-run    # 只检查，不实际部署
#
# 前置条件：
#   1. Docker Desktop 已安装并启用 buildx
#   2. 已登录 ghcr.io（docker login ghcr.io）
#   3. SSH 密钥 ~/.ssh/id_ed25519_aliyun_mpc 已授权
#   4. 本机安装了 Python 虚拟环境（.venv）
# ============================================================

# ── 参数解析 ──────────────────────────────────────────
SKIP_TESTS=false
DRY_RUN=false
COMMIT_MSG=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-tests) SKIP_TESTS=true ;;
    --dry-run)    DRY_RUN=true ;;
    -m)
      COMMIT_MSG="$2"
      shift
      ;;
    *) ;;
  esac
  shift
done

# ── 配置 ──────────────────────────────────────────────
IMAGE_NAME="ghcr.io/yang6661243/mpc-online"
CONTAINER_NAME="mpc-online-platform"
SERVER="root@8.163.49.151"
SSH_KEY="$HOME/.ssh/id_ed25519_aliyun_mpc"
PORT_HOST=18000
PORT_CONTAINER=8000
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DASHBOARD_DIR="$PROJECT_ROOT/web/mpc-dashboard"
VENV_PYTHON="$PROJECT_ROOT/.venv/bin/python"

# ── 颜色 ──────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log()   { echo -e "${GREEN}[$(date +%H:%M:%S)]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[FAIL]${NC}  $*"; exit 1; }
info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
step()  { echo -e "\n${BOLD}${CYAN}═══ $* ═══${NC}"; }
ok()    { echo -e "  ${GREEN}✓${NC} $*"; }
fail()  { echo -e "  ${RED}✗${NC} $*"; }

dry() {
  if [ "$DRY_RUN" = true ]; then
    info "[DRY-RUN] 跳过: $*"
    return 0
  fi
  return 1
}

# ── 切换到项目根目录 ──────────────────────────────────
cd "$PROJECT_ROOT"

# ── 打印配置 ───────────────────────────────────────────
echo ""
echo -e "${BOLD}${CYAN}  ⚡ MPC 独立服务部署脚本${NC}"
echo -e "  ${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  项目根目录: ${PROJECT_ROOT}"
echo -e "  目标服务器: ${SERVER}"
echo -e "  目标端口:   ${PORT_HOST}"
echo -e "  SSH 密钥:   ${SSH_KEY}"
if [ "$DRY_RUN" = true ]; then
  echo -e "  模式:       ${YELLOW}DRY-RUN (仅检查)${NC}"
fi
if [ "$SKIP_TESTS" = true ]; then
  echo -e "  测试:       ${YELLOW}跳过${NC}"
fi
echo ""

# ════════════════════════════════════════════════════════
step "Step 1/7: 检查前置条件"
# ════════════════════════════════════════════════════════

# Docker
if ! docker info >/dev/null 2>&1; then
  err "Docker 未运行，请启动 Docker Desktop"
fi
ok "Docker 运行中"

# buildx
if ! docker buildx version >/dev/null 2>&1; then
  err "docker buildx 不可用"
fi
ok "docker buildx 可用"

# Git repo
if ! git rev-parse --git-dir >/dev/null 2>&1; then
  err "不在 Git 仓库中"
fi
ok "Git 仓库正常"

# SSH
if ! ssh -i "$SSH_KEY" -o ConnectTimeout=5 -o StrictHostKeyChecking=no -o BatchMode=yes "$SERVER" 'echo ok' >/dev/null 2>&1; then
  err "SSH 连接失败: $SERVER"
fi
ok "SSH 连接正常: $SERVER"

# ════════════════════════════════════════════════════════
step "Step 2/7: 提交本地改动"
# ════════════════════════════════════════════════════════

CHANGED=$(git status --porcelain | grep -v '^?' || true)
UNTRACKED=$(git status --porcelain | grep '^?' || true)

if [ -z "$CHANGED" ] && [ -z "$UNTRACKED" ]; then
  info "无未提交改动，跳过 commit"
else
  echo -e "  ${YELLOW}已修改文件:${NC}"
  git status --short | grep -v '^?' | while read -r line; do
    echo -e "    ${YELLOW}$line${NC}"
  done

  if [ -n "$UNTRACKED" ]; then
    echo -e "  ${YELLOW}未跟踪文件:${NC}"
    echo "$UNTRACKED" | while read -r line; do
      echo -e "    ${YELLOW}$line${NC}"
    done
  fi

  echo ""

  # 确定提交信息
  if [ -z "$COMMIT_MSG" ]; then
    # 尝试从改动中自动生成提交信息
    AUTO_MSG=""
    if echo "$CHANGED" | grep -q "display_series\|displaySeries\|App.tsx"; then
      AUTO_MSG="feat: update display series frontend"
    elif echo "$CHANGED" | grep -q "chartOptions\|PowerChart"; then
      AUTO_MSG="feat: update chart display"
    elif echo "$CHANGED" | grep -q "styles.css\|layout.ts"; then
      AUTO_MSG="style: update dashboard UI"
    elif echo "$CHANGED" | grep -q "api.py\|ingestion\|aggregation"; then
      AUTO_MSG="feat: update backend service"
    else
      AUTO_MSG="chore: update"
    fi

    echo -e "  ${CYAN}提交信息 [$AUTO_MSG]:${NC}"
    read -r USER_MSG
    COMMIT_MSG="${USER_MSG:-$AUTO_MSG}"
  fi

  # 只添加已跟踪的修改文件（不添加未跟踪文件）
  TRACKED_CHANGED=$(git status --porcelain | grep -v '^?' | awk '{print $NF}' || true)
  if [ -n "$TRACKED_CHANGED" ]; then
    if dry "git add + commit"; then
      info "跳过提交"
    else
      git add $TRACKED_CHANGED
      git commit -m "$COMMIT_MSG"
      ok "已提交: $COMMIT_MSG"
    fi
  fi
fi

# ════════════════════════════════════════════════════════
step "Step 3/7: 前端测试 & 构建"
# ════════════════════════════════════════════════════════

cd "$DASHBOARD_DIR"

if [ "$SKIP_TESTS" = true ]; then
  warn "跳过前端测试"
else
  info "运行前端测试..."
  if dry "npm test"; then
    info "跳过"
  else
    npm test -- --run 2>&1 | tail -20
    ok "前端测试通过"
  fi
fi

info "TypeScript 编译检查 + Vite 构建..."
if dry "npm run build"; then
  info "跳过"
else
  npm run build 2>&1 | tail -10
  ok "前端构建成功"
fi

cd "$PROJECT_ROOT"

# ════════════════════════════════════════════════════════
step "Step 4/7: 后端测试"
# ════════════════════════════════════════════════════════

if [ "$SKIP_TESTS" = true ]; then
  warn "跳过后端测试"
else
  info "运行后端在线测试..."
  if dry "pytest"; then
    info "跳过"
  else
    $VENV_PYTHON -m pytest tests/online -q 2>&1 | tail -5
    ok "后端测试通过"
  fi
fi

# ════════════════════════════════════════════════════════
step "Step 5/7: 构建 Docker 镜像 & 推送"
# ════════════════════════════════════════════════════════

COMMIT_HASH=$(git rev-parse --short HEAD)
VERSION_TAG="$(date +%Y%m%d)-${COMMIT_HASH}"
FULL_IMAGE="${IMAGE_NAME}:${VERSION_TAG}"

info "版本: ${VERSION_TAG}"
info "镜像: ${FULL_IMAGE}"

if dry "docker buildx build --push"; then
  info "跳过 Docker 构建与推送"
else
  docker buildx build \
    --platform linux/amd64 \
    --tag "${FULL_IMAGE}" \
    --tag "${IMAGE_NAME}:latest" \
    --push \
    . 2>&1 | tail -10

  ok "镜像已推送: ${FULL_IMAGE}"
fi

# ════════════════════════════════════════════════════════
step "Step 6/7: 远端部署"
# ════════════════════════════════════════════════════════

if dry "远端部署"; then
  info "[DRY-RUN] 跳过实际部署，以下是会执行的操作："
  echo "  1. 远端安全检查（实习平台容器状态）"
  echo "  2. docker pull ${FULL_IMAGE}"
  echo "  3. docker stop ${CONTAINER_NAME}"
  echo "  4. docker rename ${CONTAINER_NAME} (备份)"
  echo "  5. docker run ... ${FULL_IMAGE}"
  echo "  6. health check + display-series 验证"
  exit 0
fi

# 6a. 远端安全检查
log "远端安全检查..."

ssh -i "$SSH_KEY" "$SERVER" bash -s << 'SAFETY'
set -e
PROTECTED="internship-frontend|internship-backend"
RUNNING=$(docker ps --format '{{.Names}}' | grep -E "$PROTECTED" || true)
if [ -z "$RUNNING" ]; then
  echo "!!! 受保护容器未在运行！中止部署。"
  docker ps --format 'table {{.Names}}\t{{.Status}}'
  exit 1
fi
echo "实习平台容器正常:"
echo "$RUNNING"
SAFETY

ok "实习平台容器正常，可以安全部署"

# 6b. 拉取镜像
log "远端拉取镜像..."
ssh -i "$SSH_KEY" "$SERVER" "docker pull ${FULL_IMAGE}"
ok "镜像已拉取"

# 6c. 替换容器
log "替换容器..."

ssh -i "$SSH_KEY" "$SERVER" bash -s << DEPLOY
set -e
IMAGE="${FULL_IMAGE}"
NAME="${CONTAINER_NAME}"
PORT_HOST=${PORT_HOST}
PORT_CONTAINER=${PORT_CONTAINER}
BAK="\${NAME}.bak-\$(date +%Y%m%d%H%M%S)"

if docker ps -a --format '{{.Names}}' | grep -qx "\${NAME}"; then
  echo "停止旧容器..."
  docker stop "\${NAME}" || true
  docker rename "\${NAME}" "\${BAK}"
  echo "已备份为: \${BAK}"
fi

echo "启动新容器..."
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

echo "等待容器启动 (8s)..."
sleep 8

# 基本存活检查
if ! docker ps --filter "name=\${NAME}" --format '{{.Status}}' | grep -q 'healthy'; then
  echo "!!! 容器可能未正常启动，请检查日志:"
  docker logs --tail 30 "\${NAME}"
  exit 1
fi
echo "容器状态: healthy"
DEPLOY

ok "容器已替换"

# ════════════════════════════════════════════════════════
step "Step 7/7: 验证"
# ════════════════════════════════════════════════════════

log "公网验收..."

# Health check
if curl -fsS --connect-timeout 10 "http://${SERVER#*@}:${PORT_HOST}/healthz" >/dev/null 2>&1; then
  ok "healthz: OK"
else
  fail "healthz 不通"
fi

# 和宏华进
HH_RESULT=$(curl -fsS --connect-timeout 10 "http://${SERVER#*@}:${PORT_HOST}/api/v1/plants/hehong_huajin/display-series?window_hours=2" 2>/dev/null \
  | python3 -c "import json,sys;d=json.load(sys.stdin);print(f'plant={d[\"plant_id\"]} points={len(d[\"series\"])} display_only={d[\"display_only\"]}')" 2>/dev/null || echo "FAIL")
if echo "$HH_RESULT" | grep -q "plant="; then
  ok "和宏华进: $HH_RESULT"
else
  fail "和宏华进 display-series 异常"
fi

# 奥莱德
AL_RESULT=$(curl -fsS --connect-timeout 10 "http://${SERVER#*@}:${PORT_HOST}/api/v1/plants/ecloud_station_3341/display-series?window_hours=2" 2>/dev/null \
  | python3 -c "import json,sys;d=json.load(sys.stdin);print(f'plant={d[\"plant_id\"]} points={len(d[\"series\"])}')" 2>/dev/null || echo "FAIL")
if echo "$AL_RESULT" | grep -q "plant="; then
  ok "奥莱德:   $AL_RESULT"
else
  fail "奥莱德 display-series 异常"
fi

# ════════════════════════════════════════════════════════
echo ""
echo -e "  ${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  ${BOLD}${GREEN}  部署完成 ✓${NC}"
echo -e "  ${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  版本:     ${BOLD}${VERSION_TAG}${NC}"
echo -e "  镜像:     ${FULL_IMAGE}"
echo ""
echo -e "  前端:     ${CYAN}http://${SERVER#*@}:${PORT_HOST}/dashboard?plant_id=hehong_huajin${NC}"
echo -e "  前端:     ${CYAN}http://${SERVER#*@}:${PORT_HOST}/dashboard?plant_id=ecloud_station_3341${NC}"
echo ""
# ════════════════════════════════════════════════════════
