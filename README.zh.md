# TermHub

[English](README.md) | [中文](README.zh.md)

TermHub 是一个面向多服务器环境的 Web 终端任务管理平台，基于 `tmux` 和 `ttyd` 管理长期运行的终端任务。

它允许一个主控节点集中创建、查看、启动、停止、重启并打开多台机器上的终端任务。每台节点独立运行自己的任务，主控台聚合所有节点的任务列表，并通过 Caddy 提供统一认证和安全的终端代理访问。

## 功能特性

- 集中查看多台服务器上的终端任务
- 每个节点本地使用 `tmux` 和 `ttyd` 执行任务
- Caddy 作为唯一对外入口，默认监听 `:7860`
- Manager 和 ttyd 端口默认只绑定 `127.0.0.1`
- 支持远程终端代理路径，例如 `/node-ttyd/server-a/7700/`
- 节点 API 使用 Basic Auth 认证
- 支持单个 IP 和 CIDR 网段的客户端访问白名单
- 支持 Docker Compose 部署

## 架构

```text
浏览器
  -> Caddy :7860
      -> TermHub manager 127.0.0.1:7861
      -> 本机 ttyd 127.0.0.1:7700-7799
      -> 远程节点 Caddy /node-ttyd/<node-id>/<port>/

远程节点
  -> Caddy :7860
      -> 本机 manager 127.0.0.1:7861
      -> 本机 ttyd 127.0.0.1:7700-7799
```

主控节点读取 `data/nodes.json`，调用远程节点 API，并为远程终端代理生成 Caddy 路由。多个节点可以使用相同的 ttyd 端口，因为远程终端 URL 中包含节点 ID。

## 环境要求

- Linux 主机
- Docker 和 Docker Compose
- 主机上安装 Python 3
- 主机上安装 `tmux`
- 通过 `scripts/download_ttyd.sh` 下载 `ttyd`
- 如果任务需要运行 `codex` 等命令，对应节点也需要安装这些命令

Manager 容器使用 `network_mode: host`、`privileged: true` 和 host PID/IPC。它通过 `nsenter` 进入宿主机命名空间，因此任务会使用宿主机的文件系统、命令、端口和进程。

## 快速开始

从示例创建 `.env`：

```bash
cp .env.example .env
```

编辑 `.env`：

```env
CADDY_HTTP_PORT=7860
CADDY_AUTH_USER=admin
CADDY_AUTH_PASSWORD=change-me
TASK_MANAGER_INTERNAL_PORT=7861
TASK_MANAGER_ALLOWED_CLIENTS=
```

下载 ttyd：

```bash
TTYD_VERSION=1.7.7 ./scripts/download_ttyd.sh
```

启动：

```bash
./scripts/docker_start.sh
```

打开：

```text
http://SERVER_IP:7860
```

使用 `CADDY_AUTH_USER` 和 `CADDY_AUTH_PASSWORD` 登录。

## 多节点配置

在每台需要管理的机器上都运行 TermHub。每个节点都应该配置自己的 `.env` 和 Caddy 密码。

在主控节点创建 `data/nodes.json`：

```bash
cp examples/nodes.json data/nodes.json
```

示例：

```json
[
  {
    "id": "server-a",
    "name": "Server A",
    "base_url": "http://192.168.1.21:7860",
    "token": "change-me"
  }
]
```

`token` 必须填写远程节点的明文 `CADDY_AUTH_PASSWORD`。TermHub 会用它进行节点之间的 Basic Auth 调用。

修改 `data/nodes.json` 后，重新生成 Caddy 节点路由并重启：

```bash
python3 scripts/render_caddy_nodes.py
docker compose restart caddy task-terminal-manager
```

使用正常启动脚本时，`./scripts/docker_start.sh` 会在启动 Docker 前自动执行这一步。

## 安全模型

Caddy 是唯一对外入口。默认情况下：

- Caddy 监听 `0.0.0.0:7860`
- Manager 监听 `127.0.0.1:7861`
- ttyd 监听 `127.0.0.1:7700-7799`

直接访问 `SERVER_IP:7700` 应该失败。终端通过 Caddy 路径暴露：

```text
/ttyd/7700/
/node-ttyd/server-a/7700/
```

可以限制允许访问的客户端地址：

```env
TASK_MANAGER_ALLOWED_CLIENTS=127.0.0.1,172.16.1.0/24,192.168.180.34
```

建议把 Caddy 的 `:7860` 放在可信的局域网、VPN、Tailscale、EasyTier 或防火墙边界内。

## 常用命令

启动 Docker 服务：

```bash
./scripts/docker_start.sh
```

停止 Docker 服务：

```bash
./scripts/docker_stop.sh
```

不使用 Docker 本地运行：

```bash
./scripts/start_manager.sh
```

停止本地 tmux manager：

```bash
./scripts/stop_manager.sh
```

运行测试：

```bash
python3 -m pytest -q
```

## 项目文件

- `server.py` - 管理器 Web UI、API、任务生命周期、远程节点客户端
- `Caddyfile` - 对外认证和代理入口
- `docker-compose.yml` - Caddy 与 manager 部署配置
- `scripts/render_caddy_nodes.py` - 生成远程终端代理路由
- `scripts/download_ttyd.sh` - 下载 ttyd
- `tests/` - 路径、节点、认证和 Caddy 路由生成测试
