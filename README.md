# TermHub

TermHub is a multi-server web terminal task manager for `tmux` and `ttyd`.

It lets one control node create, view, start, stop, restart, and open terminal
tasks across multiple machines. Each node runs its own local tasks, while the
control page aggregates all node task lists and proxies terminal access through
Caddy with Basic Auth.

## Features

- Central dashboard for tasks across many servers
- Per-node task execution with local `tmux` and `ttyd`
- Caddy-protected public entry point on `:7860`
- Manager and ttyd ports bound to `127.0.0.1`
- Remote terminal proxy paths such as `/node-ttyd/server-a/7700/`
- Node API calls authenticated with Basic Auth
- Optional client IP allowlist with single IP and CIDR support
- Docker Compose deployment

## Architecture

```text
Browser
  -> Caddy :7860
      -> TermHub manager 127.0.0.1:7861
      -> local ttyd 127.0.0.1:7700-7799
      -> remote node Caddy /node-ttyd/<node-id>/<port>/

Remote node
  -> Caddy :7860
      -> local manager 127.0.0.1:7861
      -> local ttyd 127.0.0.1:7700-7799
```

The control node reads `data/nodes.json`, calls each remote node API, and
generates Caddy routes for remote terminal proxying. Multiple nodes can reuse the
same ttyd ports because remote terminal URLs include the node ID.

## Requirements

- Linux host
- Docker and Docker Compose
- Python 3 on the host
- `tmux` on the host
- `ttyd` downloaded by `scripts/download_ttyd.sh`
- Optional task commands such as `codex` installed on each node that needs them

The manager container uses `network_mode: host`, `privileged: true`, and host
PID/IPC. It enters the host namespaces with `nsenter` so tasks use the host
filesystem, commands, ports, and processes.

## Quick Start

Create `.env` from the example:

```bash
cp .env.example .env
```

Edit `.env`:

```env
CADDY_HTTP_PORT=7860
CADDY_AUTH_USER=admin
CADDY_AUTH_PASSWORD=change-me
TASK_MANAGER_INTERNAL_PORT=7861
TASK_MANAGER_ALLOWED_CLIENTS=
```

Download ttyd:

```bash
TTYD_VERSION=1.7.7 ./scripts/download_ttyd.sh
```

Start:

```bash
./scripts/docker_start.sh
```

Open:

```text
http://SERVER_IP:7860
```

Sign in with `CADDY_AUTH_USER` and `CADDY_AUTH_PASSWORD`.

## Multi-Node Setup

Run TermHub on every machine you want to manage. Each node should have its own
`.env` with a Caddy password.

On the control node, create `data/nodes.json`:

```bash
cp examples/nodes.json data/nodes.json
```

Example:

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

`token` must be the remote node's plaintext `CADDY_AUTH_PASSWORD`. TermHub uses
it for server-to-server Basic Auth.

After editing `data/nodes.json`, regenerate Caddy node routes and restart:

```bash
python3 scripts/render_caddy_nodes.py
docker compose restart caddy task-terminal-manager
```

`./scripts/docker_start.sh` does this automatically before starting Docker.

## Security Model

Caddy is the only public entry point. By default:

- Caddy listens on `0.0.0.0:7860`
- The manager listens on `127.0.0.1:7861`
- ttyd listens on `127.0.0.1:7700-7799`

Direct access to `SERVER_IP:7700` should fail. Terminals are exposed through
Caddy paths:

```text
/ttyd/7700/
/node-ttyd/server-a/7700/
```

You can restrict accepted client addresses:

```env
TASK_MANAGER_ALLOWED_CLIENTS=127.0.0.1,172.16.1.0/24,192.168.180.34
```

Keep Caddy `:7860` inside a trusted LAN, VPN, Tailscale, EasyTier, or firewall
boundary.

## Useful Commands

Start Docker services:

```bash
./scripts/docker_start.sh
```

Stop Docker services:

```bash
./scripts/docker_stop.sh
```

Run locally without Docker:

```bash
./scripts/start_manager.sh
```

Stop local tmux manager:

```bash
./scripts/stop_manager.sh
```

Run tests:

```bash
python3 -m pytest -q
```

## Project Files

- `server.py` - manager web UI, API, task lifecycle, remote node client
- `Caddyfile` - public auth and proxy entry point
- `docker-compose.yml` - Caddy plus manager deployment
- `scripts/render_caddy_nodes.py` - generates remote terminal proxy routes
- `scripts/download_ttyd.sh` - downloads ttyd
- `tests/` - pytest coverage for paths, nodes, auth, and Caddy route generation
