# MPC Docker 镜像发布与服务器部署流程

## 目标

当前采用这条上线链路：

```text
本地代码 -> Git 仓库 -> Docker 镜像仓库 -> 实习平台 ECS 服务器拉镜像运行
```

Git 只保存源码、Dockerfile、配置模板和部署脚本；Docker 镜像仓库保存可运行镜像；服务器只需要拉镜像和启动容器。

## 一、Git 仓库状态

当前源码仓库：

```text
git@github.com:yang6661243/mpc-online-platform.git
https://github.com/yang6661243/mpc-online-platform
```

注意：

- `.env` 不提交。
- `data/`、`outputs/`、`logs/`、`tmp/` 不提交。
- `scenarios/*.xlsx`、`scenarios/*.csv` 默认不提交，避免把工厂原始数据、天气数据、节假日表打进 Git 或镜像。
- 线上运行需要的数据文件，后续放到服务器 `/opt/mpc-online/scenarios/` 并通过 compose 挂载。

## 二、准备镜像仓库

推荐使用阿里云容器镜像服务 ACR，地域优先选华南 3 或离 ECS 最近的地域。

需要创建：

- 命名空间，例如 `vpp-mpc`。
- 镜像仓库，例如 `mpc-online`。
- 仓库权限：测试期可私有，服务器上用 `docker login` 登录后拉取。

登录示例：

```bash
docker login --username=<阿里云账号或 RAM 用户> registry.cn-guangzhou.aliyuncs.com
```

## 三、本地或 CI 构建并推送镜像

本项目提供发布脚本：

```bash
scripts/docker_release.sh \
  --image registry.cn-guangzhou.aliyuncs.com/<命名空间>/mpc-online \
  --version 20260612-001 \
  --push
```

脚本会构建两个 tag：

```text
registry.cn-guangzhou.aliyuncs.com/<命名空间>/mpc-online:20260612-001
registry.cn-guangzhou.aliyuncs.com/<命名空间>/mpc-online:latest
```

如果只是本地试构建，不想推送：

```bash
scripts/docker_release.sh \
  --image registry.cn-guangzhou.aliyuncs.com/<命名空间>/mpc-online \
  --version test-local
```

如果本地或服务器访问 Docker Hub 超时，可以临时切换 Python 基础镜像源：

```bash
scripts/docker_release.sh \
  --image registry.cn-guangzhou.aliyuncs.com/<命名空间>/mpc-online \
  --version 20260612-001 \
  --base-image public.ecr.aws/docker/library/python:3.12-slim \
  --node-base-image docker.m.daocloud.io/library/node:20-bookworm-slim \
  --apt-mirror https://mirrors.tuna.tsinghua.edu.cn/debian \
  --apt-security-mirror https://mirrors.tuna.tsinghua.edu.cn/debian-security \
  --pip-index-url https://pypi.tuna.tsinghua.edu.cn/simple \
  --push
```

## 四、服务器拉镜像运行

### 方式 A：使用 bootstrap 脚本

如果服务器可以访问 GitHub 仓库，推荐先拉源码，然后执行服务器 bootstrap 脚本：

```bash
git clone git@github.com:yang6661243/mpc-online-platform.git /opt/mpc-online
cd /opt/mpc-online
MPC_ONLINE_IMAGE=registry.cn-guangzhou.aliyuncs.com/<命名空间>/mpc-online:20260612-001 \
  bash scripts/ecs_bootstrap_mpc_online.sh --install-docker --skip-git
```

如果镜像仓库还没准备好，也可以先在服务器本地构建测试：

```bash
git clone git@github.com:yang6661243/mpc-online-platform.git /opt/mpc-online
cd /opt/mpc-online
bash scripts/ecs_bootstrap_mpc_online.sh --install-docker --skip-git --build-local
```

私有 GitHub 仓库需要服务器已有读取权限，例如服务器专用 deploy key。当前本机 deploy key 只用于本机推送，不应复制私钥到服务器。

### 方式 B：手动执行 compose

服务器上创建运行目录：

```bash
sudo mkdir -p /opt/mpc-online/data /opt/mpc-online/outputs /opt/mpc-online/scenarios
sudo chown -R "$USER":"$USER" /opt/mpc-online
cd /opt/mpc-online
```

把以下文件放到 `/opt/mpc-online/`：

```text
docker-compose.release.yml
.env
```

`.env` 可以从 `.env.example` 复制后修改：

```bash
cp .env.example .env
```

服务器登录镜像仓库：

```bash
docker login --username=<阿里云账号或 RAM 用户> registry.cn-guangzhou.aliyuncs.com
```

设置要运行的镜像：

```bash
export MPC_ONLINE_IMAGE=registry.cn-guangzhou.aliyuncs.com/<命名空间>/mpc-online:20260612-001
```

启动：

```bash
docker compose -f docker-compose.release.yml pull
docker compose -f docker-compose.release.yml up -d
```

验证：

```bash
docker compose -f docker-compose.release.yml ps
curl -fsS http://127.0.0.1:8000/healthz
```

外部访问：

```text
http://8.163.49.151:8000/dashboard
```

前提是 ECS 安全组已开放 `8000` 端口。正式环境建议后续接 Nginx 和 HTTPS，用 `80/443` 暴露。

## 五、这种流程能避免哪些权限问题

可以避免：

- Workbench 没有文件上传能力的问题。
- 本地和服务器之间手工复制大量项目文件的问题。
- 服务器上保存完整源码的问题。

不能避免：

- 仍然需要一次服务器命令执行权限，例如 SSH、Workbench 或 VNC 登录。
- 仍然需要 Docker 权限。
- 私有镜像仍然需要镜像仓库登录权限。
- 外网访问仍然需要安全组开放端口。

## 六、上线检查清单

- 本地或 CI 能执行 `scripts/docker_release.sh --push`。
- 镜像仓库里能看到指定版本 tag。
- 服务器能执行 `docker --version`。
- 服务器能执行 `docker compose version`。
- 服务器能 `docker login` 镜像仓库。
- 服务器 `/opt/mpc-online/scenarios/` 中有线上需要的数据文件。
- 容器 `healthz` 正常。
- 浏览器能打开 `/dashboard`。
- 云端数据接口已经配置字段映射和签名密钥。
