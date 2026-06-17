# MPC 独立服务部署

这份文档只讲一件事：下次如何把本地改好的 MPC 服务打包成 Docker 镜像，并部署到服务器 `8.163.49.151`。

请记住一个原则：

```text
只操作 MPC 容器 mpc-online-platform
不要操作 internship-frontend
不要操作 internship-backend
```

## 0. 你会用到两个地方

### 地方 A：本地 Mac 终端

就是你自己电脑上的 Terminal / PyCharm Terminal。

用途：

- 跑测试
- 打包 Docker 镜像
- 推送镜像到 GHCR
- 从本地检查公网接口

### 地方 B：阿里云 Workbench 服务器终端

就是浏览器里打开的：

```text
root@8.163.49.151
```

用途：

- 拉取新镜像
- 停掉旧的 MPC 容器
- 启动新的 MPC 容器
- 检查服务器本机是否启动成功

不要在标着“实习生平台”的 Workbench 会话里乱执行部署命令。

## 1. 本地 Mac：进入项目目录

在哪里输入：本地 Mac 终端。

命令：

```bash
cd "/Users/yangjiaowei/Desktop/mpc无连接沙盘-虚拟电厂"
```

作用：

进入 MPC 项目目录。后面的本地命令都在这个目录执行。

## 2. 本地 Mac：确认代码测试通过

在哪里输入：本地 Mac 终端。

命令 1：跑后端测试。

```bash
python3 -m pytest tests -q
```

看到类似下面结果才继续：

```text
152 passed
```

命令 2：跑插件测试。

```bash
node --test tools/ecloud-data-extractor/*.test.js
```

看到类似下面结果才继续：

```text
# pass 43
# fail 0
```

命令 3：跑前端测试和构建。

```bash
cd web/mpc-dashboard
npm test -- --run
npm run build
cd ../..
```

看到类似下面结果才继续：

```text
Tests passed
✓ built
```

作用：

确保本地代码没有明显问题。测试不过不要部署。

## 3. 本地 Mac：生成本次版本号

在哪里输入：本地 Mac 终端。

命令：

```bash
VERSION="$(date +%Y%m%d)-$(git rev-parse --short HEAD)"
IMAGE="ghcr.io/yang6661243/mpc-online:$VERSION"
echo "$IMAGE"
```

你会看到类似：

```text
ghcr.io/yang6661243/mpc-online:20260616-030204f
```

作用：

给这次镜像起一个版本号。后面服务器也要用这个 `IMAGE`。

## 4. 本地 Mac：本地打包一个镜像先试跑

在哪里输入：本地 Mac 终端。

命令：

```bash
docker build \
  -t mpc-online-platform:"$VERSION" \
  -t mpc-online-platform:latest \
  .
```

作用：

把当前代码打包成 Docker 镜像。这个本地镜像主要用来在你电脑上先验证能不能启动。

注意：

如果你是 Apple 芯片 Mac，这一步打出来的通常是 `arm64` 镜像，只适合本地试跑。服务器是 `x86_64`，后面还要单独推送 `amd64` 镜像。

## 5. 本地 Mac：本地启动临时容器验证

在哪里输入：本地 Mac 终端。

命令 1：启动临时容器。

```bash
docker rm -f mpc-online-platform-local-verify >/dev/null 2>&1 || true
mkdir -p /tmp/mpc-online-verify/data /tmp/mpc-online-verify/outputs /tmp/mpc-online-verify/scenarios

docker run -d \
  --name mpc-online-platform-local-verify \
  --restart no \
  -p 18001:8000 \
  -e MPC_DATABASE_URL=sqlite:////app/data/mpc_online.db \
  -e MPC_INPUT_SIGNATURE_SECRET= \
  -e ONLINE_MPC_CORS_ORIGINS=https://ecloud.hoenergypower.cn,chrome-extension://becnmfbeidffckhenedfiahikaagpgek,chrome-extension://occmghdfgdbioibghjlhgadggnjfjoga \
  -v /tmp/mpc-online-verify/data:/app/data \
  -v /tmp/mpc-online-verify/outputs:/app/outputs \
  -v /tmp/mpc-online-verify/scenarios:/app/scenarios \
  mpc-online-platform:"$VERSION"
```

命令 2：检查本地服务。

```bash
sleep 5
curl -fsS http://127.0.0.1:18001/healthz
curl -fsS 'http://127.0.0.1:18001/api/v1/plants/hehong_huajin/display-series?window_hours=2' | python3 -m json.tool | head -40
docker ps --filter name=mpc-online-platform-local-verify --format 'table {{.Names}}\t{{.Ports}}\t{{.Status}}'
```

看到类似下面结果说明本地容器正常：

```text
{"status":"ok","service":"online-mpc"}
```

命令 3：验证完后清理临时容器。

```bash
docker rm -f mpc-online-platform-local-verify
```

作用：

先在本地确认镜像能启动，避免直接部署到服务器才发现镜像有问题。

## 6. 本地 Mac：推送服务器可用的 amd64 镜像

在哪里输入：本地 Mac 终端。

命令：

```bash
docker --context=default buildx build --platform linux/amd64 \
  -t "$IMAGE" \
  --push .
```

作用：

重新打包一个服务器能跑的 `amd64` 镜像，并推送到 GHCR。

如果这一步报：

```text
denied
unauthorized
```

说明 Docker 没登录 GHCR，需要先登录：

```bash
docker login ghcr.io
```

## 7. 本地 Mac：确认镜像已经推上去

在哪里输入：本地 Mac 终端。

命令：

```bash
docker manifest inspect "$IMAGE" > /tmp/mpc_manifest.json
python3 - <<'PY'
import json
m = json.load(open('/tmp/mpc_manifest.json'))
for item in m.get('manifests', []):
    p = item.get('platform', {})
    print(p.get('os'), p.get('architecture'), item.get('digest'))
PY
```

看到类似下面结果说明 OK：

```text
linux amd64 sha256:...
```

作用：

确认镜像仓库里确实有服务器能用的 `amd64` 镜像。

## 8. 服务器 Workbench：部署前检查

在哪里输入：阿里云 Workbench 服务器终端。

先打开 `root@8.163.49.151`。

命令：

```bash
hostname
uname -m
who
docker ps --format 'table {{.Names}}\t{{.Ports}}\t{{.Status}}'
curl -fsS http://127.0.0.1:18000/healthz
```

你要确认：

```text
uname -m 是 x86_64
mpc-online-platform 存在
internship-frontend 还在 Up
internship-backend 还在 Up
healthz 返回 {"status":"ok","service":"online-mpc"}
```

作用：

确认当前服务器状态正常，尤其确认实习平台容器正在运行。

## 9. 服务器 Workbench：设置这次要部署的镜像

在哪里输入：阿里云 Workbench 服务器终端。

把下面 `IMAGE=...` 改成你第 3 步看到的镜像地址。

命令：

```bash
IMAGE="ghcr.io/yang6661243/mpc-online:20260616-030204f"
BASE="/opt/mpc-online"
BAK="mpc-online-platform.bak-$(date +%Y%m%d%H%M%S)"

echo "$IMAGE"
echo "$BASE"
echo "$BAK"
```

作用：

告诉服务器这次要拉哪个镜像，同时准备旧容器的备份名字。

## 10. 服务器 Workbench：拉取新镜像

在哪里输入：阿里云 Workbench 服务器终端。

命令：

```bash
mkdir -p "$BASE"/{data,outputs,scenarios}
cd "$BASE"
docker pull "$IMAGE"
docker image inspect "$IMAGE" --format 'image arch={{.Architecture}} id={{.Id}}'
```

看到类似下面结果说明镜像架构正确：

```text
image arch=amd64 id=...
```

作用：

把新镜像下载到服务器，并确认它是 `amd64`。

## 11. 服务器 Workbench：备份旧 MPC 容器

在哪里输入：阿里云 Workbench 服务器终端。

命令：

```bash
docker stop mpc-online-platform
docker rename mpc-online-platform "$BAK"
docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}' | grep 'mpc-online-platform'
```

作用：

停止旧的 MPC 容器，并把它改名保存。这样新容器有问题时还能回滚。

注意：

这里停的是：

```text
mpc-online-platform
```

不要停：

```text
internship-frontend
internship-backend
```

## 12. 服务器 Workbench：写入环境变量文件

在哪里输入：阿里云 Workbench 服务器终端。

命令：

```bash
cat > "$BASE/mpc-online.env" <<'EOF'
MPC_DATABASE_URL=sqlite:////app/data/mpc_online.db
MPC_INPUT_SIGNATURE_SECRET=
ONLINE_MPC_CORS_ORIGINS=https://ecloud.hoenergypower.cn,chrome-extension://becnmfbeidffckhenedfiahikaagpgek,chrome-extension://occmghdfgdbioibghjlhgadggnjfjoga
EOF

cat "$BASE/mpc-online.env"
```

作用：

把很长的 CORS 配置写到文件里，避免 `docker run` 命令太长导致 Workbench 自动退出。

## 13. 服务器 Workbench：启动新 MPC 容器

在哪里输入：阿里云 Workbench 服务器终端。

命令：

```bash
docker run -d --name mpc-online-platform --restart unless-stopped -p 18000:8000 \
  --env-file "$BASE/mpc-online.env" \
  -v "$BASE/data:/app/data" \
  -v "$BASE/outputs:/app/outputs" \
  -v "$BASE/scenarios:/app/scenarios" \
  "$IMAGE"
```

作用：

启动新的 MPC 服务容器。

如果 Workbench 还是怕多行粘贴，可以先建脚本：

```bash
cat > "$BASE/run_mpc_online.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

IMAGE="${1:?usage: run_mpc_online.sh IMAGE}"
BASE="/opt/mpc-online"

docker run -d --name mpc-online-platform --restart unless-stopped -p 18000:8000 \
  --env-file "$BASE/mpc-online.env" \
  -v "$BASE/data:/app/data" \
  -v "$BASE/outputs:/app/outputs" \
  -v "$BASE/scenarios:/app/scenarios" \
  "$IMAGE"
EOF

chmod +x "$BASE/run_mpc_online.sh"
```

然后只执行这一行：

```bash
"$BASE/run_mpc_online.sh" "$IMAGE"
```

## 14. 服务器 Workbench：服务器本机验证

在哪里输入：阿里云 Workbench 服务器终端。

命令：

```bash
sleep 8
docker ps --filter name=mpc-online-platform --format 'table {{.Names}}\t{{.Ports}}\t{{.Status}}'
curl -fsS http://127.0.0.1:18000/healthz
curl -fsS 'http://127.0.0.1:18000/api/v1/plants/hehong_huajin/display-series?window_hours=2' | python3 -m json.tool | head -60
```

看到：

```text
mpc-online-platform ... Up ... healthy
{"status":"ok","service":"online-mpc"}
```

说明服务器本机启动正常。

## 15. 本地 Mac：公网验收

在哪里输入：本地 Mac 终端。

命令 1：检查健康接口。

```bash
curl -i -sS http://8.163.49.151:18000/healthz
```

命令 2：检查和宏华进展示接口。

```bash
curl -i -sS 'http://8.163.49.151:18000/api/v1/plants/hehong_huajin/display-series?window_hours=2' | head -80
```

命令 3：检查奥莱德展示接口。

```bash
curl -i -sS 'http://8.163.49.151:18000/api/v1/plants/ecloud_station_3341/display-series?window_hours=2' | head -80
```

命令 4：检查插件 CORS。

```bash
curl -i -sS -X OPTIONS \
  'http://8.163.49.151:18000/api/v1/mpc/input-data' \
  -H 'Origin: chrome-extension://occmghdfgdbioibghjlhgadggnjfjoga' \
  -H 'Access-Control-Request-Method: POST' \
  -H 'Access-Control-Request-Headers: content-type' \
  | head -60
```

你要看到：

```text
HTTP/1.1 200 OK
access-control-allow-origin: chrome-extension://occmghdfgdbioibghjlhgadggnjfjoga
```

作用：

确认公网访问正常，插件也能继续推送数据。

## 16. 浏览器打开看板

在哪里输入：浏览器地址栏。

和宏华进：

```text
http://8.163.49.151:18000/dashboard?plant_id=hehong_huajin
```

奥莱德：

```text
http://8.163.49.151:18000/dashboard?plant_id=ecloud_station_3341
```

作用：

确认前端页面能打开，实时曲线能显示。

如果实时曲线前半段有断点，不一定是错误。长时间没有原始数据时，后端会返回 `gap/null`，前端应该断开曲线。

## 17. 如果新容器出问题，怎么回滚

在哪里输入：阿里云 Workbench 服务器终端。

命令 1：找到最近备份的旧容器。

```bash
docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}' | grep 'mpc-online-platform'
```

命令 2：回滚。

```bash
PREV="$(docker ps -a --format '{{.Names}}' | grep '^mpc-online-platform.bak-' | sort | tail -1)"
echo "$PREV"

docker rm -f mpc-online-platform
docker rename "$PREV" mpc-online-platform
docker start mpc-online-platform
```

命令 3：验证回滚结果。

```bash
sleep 8
docker ps --filter name=mpc-online-platform --format 'table {{.Names}}\t{{.Ports}}\t{{.Status}}'
curl -fsS http://127.0.0.1:18000/healthz
```

作用：

如果新版本有问题，就把旧 MPC 容器改回原名并启动。

## 18. 常见问题

### 18.1 Workbench 自动退到登录页

处理方法：

1. 不要继续执行部署命令
2. 关闭所有旧 Workbench 标签页
3. 重新登录阿里云控制台
4. 只打开一个 `root@8.163.49.151`
5. 从第 8 步重新检查

### 18.2 同时连接数很多

先执行：

```bash
who
```

如果看到很多会话，先关闭多余浏览器 Workbench 标签。不要随便 `kill`，避免把当前会话断开。

### 18.3 SSH 登录失败

如果本地 Mac 执行：

```bash
ssh root@8.163.49.151
```

返回：

```text
Permission denied (publickey).
```

说明本机没有服务器 SSH key。继续用 Workbench，或者在阿里云控制台绑定 SSH key。

### 18.4 display-series 前半段全是 gap

这是正常现象。表示那段时间没有原始采集数据。

只要后面有：

```text
observed
interpolated_quadratic
```

就说明展示层接口正常。

### 18.5 看到实习平台容器怎么办

看到这些容器是正常的：

```text
internship-frontend
internship-backend
```

部署 MPC 时不要操作它们。

只能操作：

```text
mpc-online-platform
```

