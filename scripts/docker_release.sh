#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
用法:
  scripts/docker_release.sh --image <镜像仓库地址> [--version <版本号>] [--platform <平台>] [--base-image <基础镜像>] [--apt-mirror <Debian源>] [--apt-security-mirror <Debian安全源>] [--pip-index-url <PyPI源>] [--push]

示例:
  scripts/docker_release.sh \
    --image registry.cn-guangzhou.aliyuncs.com/your_namespace/mpc-online \
    --version 20260612-001 \
    --base-image public.ecr.aws/docker/library/python:3.12-slim \
    --apt-mirror https://mirrors.tuna.tsinghua.edu.cn/debian \
    --apt-security-mirror https://mirrors.tuna.tsinghua.edu.cn/debian-security \
    --pip-index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    --push

说明:
  --image     不带 tag 的镜像仓库地址。
  --version   镜像版本 tag；不传则使用当前时间。
  --platform  默认 linux/amd64，适配常见 ECS x86_64 服务器。
  --base-image Python 基础镜像；默认 python:3.12-slim。Docker Hub 网络不好时可切换镜像源。
  --apt-mirror Debian apt 源；默认不替换。
  --apt-security-mirror Debian security apt 源；默认不替换。
  --pip-index-url Python 包源；默认不替换。
  --push      构建后推送 version 和 latest 两个 tag。
EOF
}

IMAGE_REPOSITORY="${MPC_ONLINE_IMAGE_REPOSITORY:-}"
IMAGE_VERSION="${MPC_ONLINE_IMAGE_TAG:-}"
IMAGE_PLATFORM="${MPC_ONLINE_IMAGE_PLATFORM:-linux/amd64}"
PYTHON_BASE_IMAGE="${MPC_ONLINE_PYTHON_BASE_IMAGE:-python:3.12-slim}"
APT_MIRROR="${MPC_ONLINE_APT_MIRROR:-}"
APT_SECURITY_MIRROR="${MPC_ONLINE_APT_SECURITY_MIRROR:-}"
PIP_INDEX_URL="${MPC_ONLINE_PIP_INDEX_URL:-}"
PUSH_IMAGE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --image)
      IMAGE_REPOSITORY="${2:-}"
      shift 2
      ;;
    --version)
      IMAGE_VERSION="${2:-}"
      shift 2
      ;;
    --platform)
      IMAGE_PLATFORM="${2:-}"
      shift 2
      ;;
    --base-image)
      PYTHON_BASE_IMAGE="${2:-}"
      shift 2
      ;;
    --apt-mirror)
      APT_MIRROR="${2:-}"
      shift 2
      ;;
    --apt-security-mirror)
      APT_SECURITY_MIRROR="${2:-}"
      shift 2
      ;;
    --pip-index-url)
      PIP_INDEX_URL="${2:-}"
      shift 2
      ;;
    --push)
      PUSH_IMAGE=1
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

if [[ -z "${IMAGE_REPOSITORY}" ]]; then
  echo "缺少 --image，例如 registry.cn-guangzhou.aliyuncs.com/your_namespace/mpc-online" >&2
  usage >&2
  exit 2
fi

if [[ -z "${IMAGE_VERSION}" ]]; then
  IMAGE_VERSION="$(date +%Y%m%d%H%M%S)"
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "当前机器没有 docker 命令，无法构建镜像。" >&2
  exit 127
fi

VERSIONED_IMAGE="${IMAGE_REPOSITORY}:${IMAGE_VERSION}"
LATEST_IMAGE="${IMAGE_REPOSITORY}:latest"

echo "构建镜像: ${VERSIONED_IMAGE}"
docker build \
  --platform "${IMAGE_PLATFORM}" \
  --build-arg "PYTHON_BASE_IMAGE=${PYTHON_BASE_IMAGE}" \
  --build-arg "APT_MIRROR=${APT_MIRROR}" \
  --build-arg "APT_SECURITY_MIRROR=${APT_SECURITY_MIRROR}" \
  --build-arg "PIP_INDEX_URL=${PIP_INDEX_URL}" \
  -t "${VERSIONED_IMAGE}" \
  -t "${LATEST_IMAGE}" \
  .

if [[ "${PUSH_IMAGE}" -eq 1 ]]; then
  echo "推送镜像: ${VERSIONED_IMAGE}"
  docker push "${VERSIONED_IMAGE}"
  echo "推送镜像: ${LATEST_IMAGE}"
  docker push "${LATEST_IMAGE}"
else
  echo "已跳过推送。如需推送，请增加 --push。"
fi

cat <<EOF

服务器部署时使用:
  export MPC_ONLINE_IMAGE=${VERSIONED_IMAGE}
  docker compose -f docker-compose.release.yml pull
  docker compose -f docker-compose.release.yml up -d
EOF
