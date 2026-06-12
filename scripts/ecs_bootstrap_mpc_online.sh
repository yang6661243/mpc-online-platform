#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
用法:
  scripts/ecs_bootstrap_mpc_online.sh [选项]

用途:
  在 ECS 服务器上拉取 MPC 项目并启动 Docker 服务。

常用示例:
  # 方式一：服务器拉镜像运行，推荐用于正式上线。
  MPC_ONLINE_IMAGE=registry.cn-guangzhou.aliyuncs.com/your_namespace/mpc-online:20260612-001 \
    bash scripts/ecs_bootstrap_mpc_online.sh --install-docker

  # 方式二：服务器从 GitHub 拉源码后本地构建，适合镜像仓库还没准备好时测试。
  bash scripts/ecs_bootstrap_mpc_online.sh --install-docker --build-local

选项:
  --app-dir <目录>       部署目录，默认 /opt/mpc-online
  --repo-url <地址>      Git 仓库地址，默认 git@github.com:yang6661243/mpc-online-platform.git
  --branch <分支>        Git 分支，默认 main
  --image <镜像>         要拉取运行的 Docker 镜像；等同于 MPC_ONLINE_IMAGE
  --port <端口>          宿主机暴露端口，默认 8000
  --install-docker       若服务器没有 Docker，尝试通过 apt 安装
  --build-local          不拉镜像，直接在服务器本地 docker compose build
  --skip-git             不更新代码，仅使用 --app-dir 里的现有文件
  -h, --help             显示帮助

前提:
  1. 私有 GitHub 仓库需要服务器已有可读权限，例如服务器专用 deploy key。
  2. 私有镜像仓库需要先在服务器执行 docker login。
  3. ECS 安全组需要放行对外端口，例如 8000。
EOF
}

APP_DIR="${MPC_ONLINE_APP_DIR:-/opt/mpc-online}"
REPO_URL="${MPC_ONLINE_REPO_URL:-git@github.com:yang6661243/mpc-online-platform.git}"
BRANCH="${MPC_ONLINE_BRANCH:-main}"
IMAGE="${MPC_ONLINE_IMAGE:-}"
PORT="${MPC_ONLINE_PORT:-8000}"
INSTALL_DOCKER=0
BUILD_LOCAL=0
SKIP_GIT=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --app-dir)
      APP_DIR="${2:-}"
      shift 2
      ;;
    --repo-url)
      REPO_URL="${2:-}"
      shift 2
      ;;
    --branch)
      BRANCH="${2:-}"
      shift 2
      ;;
    --image)
      IMAGE="${2:-}"
      shift 2
      ;;
    --port)
      PORT="${2:-}"
      shift 2
      ;;
    --install-docker)
      INSTALL_DOCKER=1
      shift
      ;;
    --build-local)
      BUILD_LOCAL=1
      shift
      ;;
    --skip-git)
      SKIP_GIT=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "未知参数: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "${APP_DIR}" || -z "${REPO_URL}" || -z "${BRANCH}" || -z "${PORT}" ]]; then
  echo "部署目录、仓库地址、分支和端口不能为空。" >&2
  exit 2
fi

run_sudo() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose "$@"
  elif command -v docker-compose >/dev/null 2>&1; then
    docker-compose "$@"
  else
    echo "缺少 docker compose。请安装 Docker Compose 插件或 docker-compose。" >&2
    exit 127
  fi
}

install_docker_if_needed() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    return
  fi
  if [[ "${INSTALL_DOCKER}" -ne 1 ]]; then
    echo "服务器缺少 Docker 或 Docker Compose。重新执行时增加 --install-docker。" >&2
    exit 127
  fi

  echo "安装 Docker、Docker Compose 和 Git..."
  run_sudo apt-get update
  if ! run_sudo apt-get install -y ca-certificates curl git docker.io docker-compose-v2; then
    run_sudo apt-get install -y ca-certificates curl git docker.io docker-compose
  fi
  run_sudo systemctl enable --now docker
}

prepare_checkout_dir() {
  run_sudo mkdir -p "${APP_DIR}"
  if [[ "$(id -u)" -ne 0 ]]; then
    run_sudo chown -R "$(id -un)":"$(id -gn)" "${APP_DIR}"
  fi
}

sync_repo() {
  if [[ "${SKIP_GIT}" -eq 1 ]]; then
    return
  fi

  if [[ -d "${APP_DIR}/.git" ]]; then
    echo "更新已有仓库: ${APP_DIR}"
    git -C "${APP_DIR}" fetch origin "${BRANCH}"
    git -C "${APP_DIR}" checkout "${BRANCH}"
    git -C "${APP_DIR}" pull --ff-only origin "${BRANCH}"
  elif [[ -e "${APP_DIR}" && -n "$(find "${APP_DIR}" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
    echo "${APP_DIR} 已存在且不是空目录。请清空目录、指定其他 --app-dir，或使用 --skip-git。" >&2
    exit 1
  else
    echo "克隆仓库: ${REPO_URL}"
    git clone --branch "${BRANCH}" --depth 1 "${REPO_URL}" "${APP_DIR}"
  fi
}

prepare_runtime_dirs() {
  mkdir -p "${APP_DIR}/data" "${APP_DIR}/outputs" "${APP_DIR}/scenarios"
}

write_env_file() {
  local env_file="${APP_DIR}/.env"
  if [[ -f "${env_file}" ]]; then
    echo "保留已有 .env: ${env_file}"
    return
  fi

  echo "生成默认 .env: ${env_file}"
  cat >"${env_file}" <<EOF
MPC_ONLINE_IMAGE=${IMAGE}
MPC_ONLINE_PORT=${PORT}
MPC_ONLINE_CONTAINER_NAME=mpc-online-platform
MPC_DATABASE_URL=sqlite:////app/data/mpc_online.db
MPC_INPUT_SIGNATURE_SECRET=
MPC_SMOKE_PLANT_ID=smoke_factory
EOF
}

start_service() {
  cd "${APP_DIR}"

  if [[ "${BUILD_LOCAL}" -eq 1 || -z "${IMAGE}" ]]; then
    echo "本地构建并启动 MPC 服务..."
    export MPC_ONLINE_PORT="${PORT}"
    compose -f docker-compose.online.yml up -d --build
  else
    echo "拉取镜像并启动 MPC 服务: ${IMAGE}"
    export MPC_ONLINE_IMAGE="${IMAGE}"
    export MPC_ONLINE_PORT="${PORT}"
    compose -f docker-compose.release.yml pull
    compose -f docker-compose.release.yml up -d
  fi
}

verify_service() {
  echo "检查容器状态..."
  compose -f docker-compose.release.yml ps || compose -f docker-compose.online.yml ps || true

  echo "检查健康接口..."
  curl -fsS "http://127.0.0.1:${PORT}/healthz"
  echo
  echo "本机访问: http://127.0.0.1:${PORT}/dashboard"
}

install_docker_if_needed
prepare_checkout_dir
sync_repo
prepare_runtime_dirs
write_env_file
start_service
verify_service
