#!/usr/bin/env python3
from __future__ import annotations

import html
import base64
import binascii
import ipaddress
import json
import os
import signal
import shutil
import socket
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, quote, urlparse


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR = DATA_DIR / "logs"
SOCKET_DIR = DATA_DIR / "sockets"
DB_PATH = DATA_DIR / "tasks.sqlite3"
NODES_FILE = Path(os.environ.get("TASK_MANAGER_NODES_FILE", str(DATA_DIR / "nodes.json")))
MANAGER_TOKEN = os.environ.get("TASK_MANAGER_TOKEN", "")
MANAGER_PASSWORD = os.environ.get("TASK_MANAGER_PASSWORD", MANAGER_TOKEN)
ALLOWED_CLIENTS = os.environ.get("TASK_MANAGER_ALLOWED_CLIENTS", "")
TTYD_VERSION = os.environ.get("TTYD_VERSION", "1.7.7")
TTYD_BIN = Path(os.environ.get("TTYD_BIN", BASE_DIR / "tools" / "ttyd" / TTYD_VERSION / "ttyd"))
HOST = os.environ.get("TASK_MANAGER_HOST", "0.0.0.0")
PORT = int(os.environ.get("TASK_MANAGER_PORT", "7860"))
TASK_TTYD_HOST = os.environ.get("TASK_TTYD_HOST", "0.0.0.0")
TASK_PORT_START = int(os.environ.get("TASK_PORT_START", "7700"))
TASK_PORT_END = int(os.environ.get("TASK_PORT_END", "7799"))
TTYD_BASE_PATH_PREFIX = os.environ.get("TTYD_BASE_PATH_PREFIX", "")


SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    workdir TEXT NOT NULL,
    command TEXT NOT NULL,
    ttyd_port INTEGER NOT NULL,
    tmux_socket TEXT NOT NULL,
    tmux_session TEXT NOT NULL,
    ttyd_pid INTEGER,
    status TEXT NOT NULL DEFAULT 'stopped',
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Node:
    id: str
    name: str
    base_url: str = ""
    token: str = ""
    local: bool = False


@dataclass
class Task:
    id: str
    name: str
    workdir: str
    command: str
    ttyd_port: int
    tmux_socket: str
    tmux_session: str
    ttyd_pid: int | None
    status: str
    last_error: str
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Task":
        return cls(**dict(row))


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def ensure_dirs() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    LOG_DIR.mkdir(exist_ok=True)
    SOCKET_DIR.mkdir(exist_ok=True)


def db() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(SCHEMA)
    return conn


def ttyd_bin() -> str:
    if TTYD_BIN.exists():
        return str(TTYD_BIN)
    path = shutil.which("ttyd")
    if path:
        return path
    raise RuntimeError(f"ttyd not found at {TTYD_BIN}")


def run_quiet(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True)


def process_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def tmux_has_session(task: Task) -> bool:
    result = run_quiet(["tmux", "-S", task.tmux_socket, "has-session", "-t", task.tmux_session])
    return result.returncode == 0


def port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def allocate_port(conn: sqlite3.Connection) -> int:
    used = {row["ttyd_port"] for row in conn.execute("SELECT ttyd_port FROM tasks")}
    for port in range(TASK_PORT_START, TASK_PORT_END + 1):
        if port not in used and not port_open(port):
            return port
    raise RuntimeError(f"no free port in {TASK_PORT_START}-{TASK_PORT_END}")


def normalize_workdir(value: str) -> str:
    path = Path(value or str(BASE_DIR)).expanduser()
    if not path.is_absolute():
        path = (BASE_DIR / path).resolve()
    if not path.exists() or not path.is_dir():
        raise ValueError("workdir must be an existing directory")
    return str(path)


def local_node() -> Node:
    return Node(id="local", name="Local", local=True)


def load_nodes(path: Path | str = NODES_FILE) -> list[Node]:
    nodes = [local_node()]
    nodes_path = Path(path)
    if not nodes_path.exists():
        return nodes

    raw_nodes = json.loads(nodes_path.read_text())
    if not isinstance(raw_nodes, list):
        raise ValueError("nodes config must be a JSON array")

    for index, raw in enumerate(raw_nodes, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"node entry {index} must be an object")
        node_id = str(raw.get("id", "")).strip()
        name = str(raw.get("name", node_id)).strip() or node_id
        base_url = str(raw.get("base_url", "")).strip().rstrip("/")
        token = str(raw.get("token", "")).strip()
        if not node_id:
            raise ValueError(f"node entry {index} missing id")
        if not base_url:
            raise ValueError(f"node {node_id} missing base_url")
        nodes.append(Node(id=node_id, name=name, base_url=base_url, token=token))
    return nodes


def node_by_id(node_id: str, nodes: list[Node] | None = None) -> Node | None:
    for node in nodes if nodes is not None else load_nodes():
        if node.id == node_id:
            return node
    return None


def api_authorized(headers: Mapping[str, str], token: str = MANAGER_TOKEN) -> bool:
    if not token:
        return True
    return headers.get("Authorization", "") == f"Bearer {token}"


def request_authorized(headers: Mapping[str, str], password: str = MANAGER_PASSWORD) -> bool:
    if not password:
        return True

    authorization = headers.get("Authorization", "")
    if authorization == f"Bearer {password}":
        return True

    prefix = "Basic "
    if not authorization.startswith(prefix):
        return False
    try:
        decoded = base64.b64decode(authorization[len(prefix) :], validate=True).decode()
    except (binascii.Error, UnicodeDecodeError):
        return False
    if ":" not in decoded:
        return False
    _username, supplied_password = decoded.split(":", 1)
    return supplied_password == password


def client_ip_allowed(client_ip: str, allowed: str = ALLOWED_CLIENTS) -> bool:
    rules = [rule.strip() for rule in allowed.split(",") if rule.strip()]
    if not rules:
        return True

    try:
        address = ipaddress.ip_address(client_ip)
    except ValueError:
        return False

    for rule in rules:
        try:
            if "/" in rule:
                if address in ipaddress.ip_network(rule, strict=False):
                    return True
            elif address == ipaddress.ip_address(rule):
                return True
        except ValueError:
            continue
    return False


def remote_json(node: Node, path: str, method: str = "GET", payload: Any = None) -> Any:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    if node.token:
        token = base64.b64encode(f"admin:{node.token}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"

    request = urllib.request.Request(
        f"{node.base_url}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"{node.name} {method} {path} failed: HTTP {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{node.name} {method} {path} failed: {exc.reason}") from exc

    return json.loads(body.decode() or "{}")


def remote_tasks(node: Node) -> list[dict[str, Any]]:
    return remote_json(node, "/api/tasks")


def remote_task(node: Node, task_id: str) -> dict[str, Any]:
    return remote_json(node, f"/api/tasks/{quote(task_id)}")


def remote_create_task(node: Node, payload: dict[str, Any]) -> dict[str, Any]:
    return remote_json(node, "/api/tasks", method="POST", payload=payload)


def remote_task_action(node: Node, task_id: str, action: str) -> dict[str, Any]:
    return remote_json(node, f"/api/tasks/{quote(task_id)}/{quote(action)}", method="POST", payload={})


def list_tasks() -> list[Task]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()
    tasks = [Task.from_row(row) for row in rows]
    for task in tasks:
        refresh_task_status(task)
    return tasks


def get_task(task_id: str) -> Task | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        return None
    task = Task.from_row(row)
    refresh_task_status(task)
    return task


def save_status(task: Task, status: str, pid: int | None = None, error: str = "") -> None:
    task.status = status
    task.ttyd_pid = pid
    task.last_error = error
    task.updated_at = now()
    with db() as conn:
        conn.execute(
            "UPDATE tasks SET status = ?, ttyd_pid = ?, last_error = ?, updated_at = ? WHERE id = ?",
            (status, pid, error, task.updated_at, task.id),
        )


def refresh_task_status(task: Task) -> None:
    ttyd_running = process_alive(task.ttyd_pid)
    tmux_running = tmux_has_session(task)
    status = "running" if ttyd_running and tmux_running else "stopped"
    if task.status != status:
        save_status(task, status, task.ttyd_pid if ttyd_running else None, task.last_error)


def create_task(name: str, workdir: str, command: str, autostart: bool) -> Task:
    clean_name = (name or "").strip() or "Untitled task"
    clean_command = (command or "").strip() or "bash"
    clean_workdir = normalize_workdir(workdir)
    task_id = uuid.uuid4().hex[:12]
    session = f"task-{task_id}"
    socket_path = str(SOCKET_DIR / f"{task_id}.tmux")
    created = now()
    with db() as conn:
        port = allocate_port(conn)
        conn.execute(
            """
            INSERT INTO tasks (
                id, name, workdir, command, ttyd_port, tmux_socket, tmux_session,
                ttyd_pid, status, last_error, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 'stopped', '', ?, ?)
            """,
            (task_id, clean_name, clean_workdir, clean_command, port, socket_path, session, created, created),
        )
    task = get_task(task_id)
    if task is None:
        raise RuntimeError("failed to create task")
    if autostart:
        start_task(task)
        task = get_task(task_id) or task
    return task


def start_task(task: Task) -> None:
    refresh_task_status(task)
    if task.status == "running":
        return

    Path(task.workdir).mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{task.id}.ttyd.log"

    if not tmux_has_session(task):
        tmux_cmd = [
            "tmux",
            "-S",
            task.tmux_socket,
            "new-session",
            "-d",
            "-s",
            task.tmux_session,
            "-c",
            task.workdir,
            "bash",
            "-lc",
            task.command,
        ]
        result = run_quiet(tmux_cmd)
        if result.returncode != 0:
            save_status(task, "error", None, (result.stderr or result.stdout).strip())
            raise RuntimeError(task.last_error or "failed to start tmux session")

    ttyd_cmd = build_ttyd_cmd(task)
    log = open(log_path, "ab", buffering=0)
    proc = subprocess.Popen(
        ttyd_cmd,
        cwd=task.workdir,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    time.sleep(0.4)
    if proc.poll() is not None:
        error = log_path.read_text(errors="replace")[-2000:] if log_path.exists() else "ttyd exited"
        save_status(task, "error", None, error.strip())
        raise RuntimeError(error.strip())
    save_status(task, "running", proc.pid, "")


def build_ttyd_cmd(task: Task) -> list[str]:
    ttyd_cmd = [
        ttyd_bin(),
        "-W",
        "-i",
        TASK_TTYD_HOST,
        "-p",
        str(task.ttyd_port),
        "tmux",
        "-S",
        task.tmux_socket,
        "attach",
        "-t",
        task.tmux_session,
    ]
    prefix = TTYD_BASE_PATH_PREFIX.strip().strip("/")
    if prefix:
        ttyd_cmd[1:1] = ["-b", f"/{prefix}/{task.ttyd_port}"]
    return ttyd_cmd


def stop_task(task: Task) -> None:
    if task.ttyd_pid and process_alive(task.ttyd_pid):
        try:
            os.kill(task.ttyd_pid, signal.SIGTERM)
            time.sleep(0.2)
            if process_alive(task.ttyd_pid):
                os.kill(task.ttyd_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    if tmux_has_session(task):
        run_quiet(["tmux", "-S", task.tmux_socket, "kill-session", "-t", task.tmux_session])

    save_status(task, "stopped", None, "")


def delete_task(task: Task) -> None:
    stop_task(task)
    with db() as conn:
        conn.execute("DELETE FROM tasks WHERE id = ?", (task.id,))


def restart_task(task: Task) -> None:
    stop_task(task)
    start_task(task)


def external_host(handler: BaseHTTPRequestHandler) -> str:
    host = handler.headers.get("Host", "")
    return host or "127.0.0.1"


def external_scheme(handler: BaseHTTPRequestHandler) -> str:
    return handler.headers.get("X-Forwarded-Proto", "http").split(",", 1)[0].strip() or "http"


def task_url(handler: BaseHTTPRequestHandler, task: Task) -> str:
    return f"{external_scheme(handler)}://{external_host(handler)}/ttyd/{task.ttyd_port}/"


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def page(title: str, body: str) -> bytes:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --surface: #ffffff;
      --surface-2: #eef2f6;
      --border: #d8dde5;
      --text: #17202a;
      --muted: #637083;
      --accent: #176b87;
      --accent-2: #0f766e;
      --danger: #b42318;
      --warn: #9a5b00;
      --ok: #067647;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
      letter-spacing: 0;
    }}
    header {{
      height: 56px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 0 24px;
      border-bottom: 1px solid var(--border);
      background: var(--surface);
    }}
    .brand {{ font-size: 17px; font-weight: 650; }}
    .shell {{ width: min(1280px, 100%); margin: 0 auto; padding: 20px 24px 28px; }}
    .toolbar {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 14px; }}
    .actions {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }}
    a, button {{
      font: inherit;
    }}
    .btn {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 34px;
      padding: 0 12px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--surface);
      color: var(--text);
      text-decoration: none;
      cursor: pointer;
      white-space: nowrap;
    }}
    .btn.primary {{ background: var(--accent); color: white; border-color: var(--accent); }}
    .btn.danger {{ color: var(--danger); }}
    .btn:disabled {{ opacity: .55; cursor: not-allowed; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--surface);
      border: 1px solid var(--border);
    }}
    th, td {{
      padding: 11px 12px;
      border-bottom: 1px solid var(--border);
      text-align: left;
      vertical-align: middle;
      font-size: 14px;
    }}
    th {{ color: var(--muted); font-size: 12px; text-transform: uppercase; background: var(--surface-2); }}
    tr:last-child td {{ border-bottom: 0; }}
    .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 13px; }}
    .status {{
      display: inline-flex;
      align-items: center;
      min-width: 74px;
      justify-content: center;
      border-radius: 999px;
      padding: 3px 9px;
      font-size: 12px;
      font-weight: 650;
      background: #e7edf3;
      color: var(--muted);
    }}
    .status.running {{ background: #dcfae6; color: var(--ok); }}
    .status.error {{ background: #fff2cc; color: var(--warn); }}
    .form {{
      background: var(--surface);
      border: 1px solid var(--border);
      padding: 18px;
      display: grid;
      gap: 14px;
      max-width: 760px;
    }}
    label {{ display: grid; gap: 6px; color: var(--muted); font-size: 13px; }}
    input[type="text"] {{
      min-height: 38px;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 8px 10px;
      font: inherit;
      color: var(--text);
      background: white;
    }}
    .check {{ display: flex; align-items: center; gap: 8px; color: var(--text); }}
    .terminal-frame {{
      width: 100%;
      height: 100%;
      border: 1px solid var(--border);
      background: #0b0f14;
    }}
    .terminal-wrap {{
      width: 100%;
      height: calc(100vh - 126px);
      min-height: 520px;
      background: #0b0f14;
    }}
    .terminal-wrap:fullscreen {{
      height: 100vh;
      min-height: 100vh;
      padding: 0;
    }}
    .terminal-wrap:fullscreen .terminal-frame {{
      border: 0;
    }}
    .error {{
      border: 1px solid #f7c7c2;
      background: #fff4f2;
      color: var(--danger);
      padding: 10px 12px;
      margin-bottom: 12px;
      white-space: pre-wrap;
    }}
    .empty {{
      background: var(--surface);
      border: 1px solid var(--border);
      padding: 28px;
      color: var(--muted);
    }}
    form.inline {{ display: inline; }}
    @media (max-width: 780px) {{
      header {{ padding: 0 14px; }}
      .shell {{ padding: 14px; }}
      .toolbar {{ align-items: stretch; flex-direction: column; }}
      table {{ display: block; overflow-x: auto; }}
      th, td {{ white-space: nowrap; }}
      .terminal-wrap {{ height: calc(100vh - 156px); min-height: 420px; }}
    }}
  </style>
  <script>
    function toggleTerminalFullscreen() {{
      const wrap = document.querySelector('[data-terminal-wrap]');
      if (!wrap) return;
      if (document.fullscreenElement) {{
        document.exitFullscreen();
      }} else {{
        wrap.requestFullscreen();
      }}
    }}
    document.addEventListener('fullscreenchange', () => {{
      const btn = document.querySelector('[data-fullscreen-button]');
      if (btn) btn.textContent = document.fullscreenElement ? '退出全屏' : '全屏';
    }});
  </script>
</head>
<body>
  <header>
    <div class="brand">任务终端管理</div>
    <nav class="actions">
      <a class="btn" href="/">任务</a>
      <a class="btn" href="/nodes">机器</a>
      <a class="btn primary" href="/nodes">新建任务</a>
    </nav>
  </header>
  <main class="shell">{body}</main>
</body>
</html>""".encode()


def render_index(handler: BaseHTTPRequestHandler) -> bytes:
    entries = []
    errors = []
    for node in load_nodes():
        try:
            for task in list_node_tasks(node):
                entries.append((node, task))
        except Exception as exc:
            errors.append((node, str(exc)))

    if not entries and not errors:
        rows = '<div class="empty">暂无任务。</div>'
    else:
        body_rows = []
        for node, task in entries:
            terminal = node_task_url(handler, node, task)
            task_id = str(task["id"])
            status = str(task.get("status", "unknown"))
            body_rows.append(
                f"""<tr>
  <td><a href="/nodes/{esc(node.id)}">{esc(node.name)}</a></td>
  <td><a href="/nodes/{esc(node.id)}/tasks/{esc(task_id)}">{esc(task.get("name", ""))}</a></td>
  <td><span class="status {esc(status)}">{esc(status)}</span></td>
  <td class="mono">{esc(task.get("workdir", ""))}</td>
  <td class="mono">{esc(task.get("command", ""))}</td>
  <td class="mono">{esc(task.get("ttyd_port", ""))}</td>
  <td>{esc(task.get("updated_at", ""))}</td>
  <td class="actions">
    <a class="btn" href="/nodes/{esc(node.id)}/tasks/{esc(task_id)}">进入</a>
    <a class="btn" target="_blank" rel="noreferrer" href="{esc(terminal)}">新窗口</a>
    <form class="inline" method="post" action="/nodes/{esc(node.id)}/tasks/{esc(task_id)}/start"><button class="btn" type="submit">启动</button></form>
    <form class="inline" method="post" action="/nodes/{esc(node.id)}/tasks/{esc(task_id)}/restart"><button class="btn" type="submit">重启</button></form>
    <form class="inline" method="post" action="/nodes/{esc(node.id)}/tasks/{esc(task_id)}/stop"><button class="btn" type="submit">停止</button></form>
  </td>
</tr>"""
            )
        for node, error in errors:
            body_rows.append(
                f"""<tr>
  <td><a href="/nodes/{esc(node.id)}">{esc(node.name)}</a></td>
  <td colspan="6"><div class="error">{esc(error)}</div></td>
  <td class="actions"><a class="btn" href="/nodes/{esc(node.id)}">查看</a></td>
</tr>"""
            )
        rows = f"""<table>
  <thead><tr><th>机器</th><th>任务</th><th>状态</th><th>工作目录</th><th>命令</th><th>端口</th><th>更新</th><th>操作</th></tr></thead>
  <tbody>{''.join(body_rows)}</tbody>
</table>"""
    return page(
        "任务终端管理",
        f"""<section class="toolbar">
  <div style="font-size:18px;font-weight:650">全部任务</div>
  <div class="actions"><a class="btn primary" href="/nodes">选择机器新建</a></div>
</section>
{rows}""",
    )


def render_new(error: str = "") -> bytes:
    error_html = f'<div class="error">{esc(error)}</div>' if error else ""
    return page(
        "新建任务",
        f"""{error_html}
<form class="form" method="post" action="/tasks">
  <label>任务名
    <input type="text" name="name" value="Codex" required>
  </label>
  <label>工作目录
    <input type="text" name="workdir" value="{esc(BASE_DIR)}" required>
  </label>
  <label>启动命令
    <input type="text" name="command" value="codex" required>
  </label>
  <label class="check">
    <input type="checkbox" name="autostart" value="1" checked>
    创建后启动
  </label>
  <div class="actions">
    <button class="btn primary" type="submit">创建</button>
    <a class="btn" href="/">取消</a>
  </div>
</form>""",
    )


def render_nodes(nodes: list[Node]) -> bytes:
    body_rows = []
    for node in nodes:
        address = "local" if node.local else node.base_url
        body_rows.append(
            f"""<tr>
  <td><a href="/nodes/{esc(node.id)}">{esc(node.name)}</a></td>
  <td class="mono">{esc(node.id)}</td>
  <td class="mono">{esc(address)}</td>
  <td class="actions"><a class="btn" href="/nodes/{esc(node.id)}">查看任务</a></td>
</tr>"""
        )
    return page(
        "机器",
        f"""<section class="toolbar">
  <div style="font-size:18px;font-weight:650">机器</div>
</section>
<table>
  <thead><tr><th>名称</th><th>ID</th><th>地址</th><th>操作</th></tr></thead>
  <tbody>{''.join(body_rows)}</tbody>
</table>""",
    )


def render_task(handler: BaseHTTPRequestHandler, task: Task) -> bytes:
    terminal = task_url(handler, task)
    error_html = f'<div class="error">{esc(task.last_error)}</div>' if task.last_error else ""
    iframe = (
        f'<div class="terminal-wrap" data-terminal-wrap><iframe class="terminal-frame" src="{esc(terminal)}"></iframe></div>'
        if task.status == "running"
        else '<div class="empty">任务未运行。</div>'
    )
    return page(
        task.name,
        f"""{error_html}
<section class="toolbar">
  <div>
    <div style="font-size:18px;font-weight:650">{esc(task.name)}</div>
    <div class="mono" style="color:var(--muted);margin-top:4px">{esc(task.workdir)} · {esc(task.command)} · :{esc(task.ttyd_port)}</div>
  </div>
  <div class="actions">
    <span class="status {esc(task.status)}">{esc(task.status)}</span>
    <button class="btn" type="button" data-fullscreen-button onclick="toggleTerminalFullscreen()">全屏</button>
    <a class="btn" target="_blank" rel="noreferrer" href="{esc(terminal)}">新窗口</a>
    <form class="inline" method="post" action="/tasks/{esc(task.id)}/start"><button class="btn" type="submit">启动</button></form>
    <form class="inline" method="post" action="/tasks/{esc(task.id)}/restart"><button class="btn" type="submit">重启</button></form>
    <form class="inline" method="post" action="/tasks/{esc(task.id)}/stop"><button class="btn" type="submit">停止</button></form>
    <form class="inline" method="post" action="/tasks/{esc(task.id)}/delete"><button class="btn danger" type="submit">删除</button></form>
  </div>
</section>
{iframe}""",
    )


def task_to_dict(task: Task) -> dict[str, Any]:
    return {
        "id": task.id,
        "name": task.name,
        "workdir": task.workdir,
        "command": task.command,
        "ttyd_port": task.ttyd_port,
        "tmux_socket": task.tmux_socket,
        "tmux_session": task.tmux_session,
        "ttyd_pid": task.ttyd_pid,
        "status": task.status,
        "last_error": task.last_error,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


def node_task_url(handler: BaseHTTPRequestHandler | None, node: Node, task: dict[str, Any]) -> str:
    port = int(task["ttyd_port"])
    if node.local:
        return f"/ttyd/{port}/"

    return f"/node-ttyd/{node.id}/{port}/"


def list_node_tasks(node: Node) -> list[dict[str, Any]]:
    if node.local:
        return [task_to_dict(task) for task in list_tasks()]
    return remote_tasks(node)


def get_node_task(node: Node, task_id: str) -> dict[str, Any] | None:
    if node.local:
        task = get_task(task_id)
        return task_to_dict(task) if task else None
    return remote_task(node, task_id)


def create_node_task(node: Node, payload: dict[str, Any]) -> dict[str, Any]:
    if node.local:
        task = create_task(
            payload.get("name", ""),
            payload.get("workdir", ""),
            payload.get("command", ""),
            bool(payload.get("autostart", True)),
        )
        return task_to_dict(task)
    return remote_create_task(node, payload)


def run_node_task_action(node: Node, task_id: str, action: str) -> dict[str, Any]:
    if not node.local:
        return remote_task_action(node, task_id, action)

    task = get_task(task_id)
    if not task:
        raise KeyError("task not found")
    if action == "start":
        start_task(task)
        return task_to_dict(get_task(task.id) or task)
    if action == "stop":
        stop_task(task)
        return task_to_dict(get_task(task.id) or task)
    if action == "restart":
        restart_task(task)
        return task_to_dict(get_task(task.id) or task)
    if action == "delete":
        delete_task(task)
        return {"ok": True}
    raise ValueError(f"unknown action: {action}")


def render_node_index(handler: BaseHTTPRequestHandler, node: Node) -> bytes:
    tasks = list_node_tasks(node)
    if not tasks:
        rows = '<div class="empty">暂无任务。</div>'
    else:
        body_rows = []
        for task in tasks:
            terminal = node_task_url(handler, node, task)
            task_id = str(task["id"])
            status = str(task.get("status", "unknown"))
            body_rows.append(
                f"""<tr>
  <td><a href="/nodes/{esc(node.id)}/tasks/{esc(task_id)}">{esc(task.get("name", ""))}</a></td>
  <td><span class="status {esc(status)}">{esc(status)}</span></td>
  <td class="mono">{esc(task.get("workdir", ""))}</td>
  <td class="mono">{esc(task.get("command", ""))}</td>
  <td class="mono">{esc(task.get("ttyd_port", ""))}</td>
  <td>{esc(task.get("updated_at", ""))}</td>
  <td class="actions">
    <a class="btn" href="/nodes/{esc(node.id)}/tasks/{esc(task_id)}">进入</a>
    <a class="btn" target="_blank" rel="noreferrer" href="{esc(terminal)}">新窗口</a>
    <form class="inline" method="post" action="/nodes/{esc(node.id)}/tasks/{esc(task_id)}/start"><button class="btn" type="submit">启动</button></form>
    <form class="inline" method="post" action="/nodes/{esc(node.id)}/tasks/{esc(task_id)}/restart"><button class="btn" type="submit">重启</button></form>
    <form class="inline" method="post" action="/nodes/{esc(node.id)}/tasks/{esc(task_id)}/stop"><button class="btn" type="submit">停止</button></form>
  </td>
</tr>"""
            )
        rows = f"""<table>
  <thead><tr><th>任务</th><th>状态</th><th>工作目录</th><th>命令</th><th>端口</th><th>更新</th><th>操作</th></tr></thead>
  <tbody>{''.join(body_rows)}</tbody>
</table>"""
    return page(
        node.name,
        f"""<section class="toolbar">
  <div>
    <div style="font-size:18px;font-weight:650">{esc(node.name)}</div>
    <div class="mono" style="color:var(--muted);margin-top:4px">{esc('local' if node.local else node.base_url)}</div>
  </div>
  <div class="actions">
    <a class="btn" href="/nodes">机器</a>
    <a class="btn primary" href="/nodes/{esc(node.id)}/tasks/new">新建任务</a>
  </div>
</section>
{rows}""",
    )


def render_node_new(node: Node, error: str = "") -> bytes:
    error_html = f'<div class="error">{esc(error)}</div>' if error else ""
    default_workdir = str(BASE_DIR) if node.local else "/root"
    return page(
        f"{node.name} · 新建任务",
        f"""{error_html}
<form class="form" method="post" action="/nodes/{esc(node.id)}/tasks">
  <label>任务名
    <input type="text" name="name" value="Codex" required>
  </label>
  <label>工作目录
    <input type="text" name="workdir" value="{esc(default_workdir)}" required>
  </label>
  <label>启动命令
    <input type="text" name="command" value="codex" required>
  </label>
  <label class="check">
    <input type="checkbox" name="autostart" value="1" checked>
    创建后启动
  </label>
  <div class="actions">
    <button class="btn primary" type="submit">创建</button>
    <a class="btn" href="/nodes/{esc(node.id)}">取消</a>
  </div>
</form>""",
    )


def render_node_task(handler: BaseHTTPRequestHandler, node: Node, task: dict[str, Any]) -> bytes:
    terminal = node_task_url(handler, node, task)
    status = str(task.get("status", "unknown"))
    error_html = f'<div class="error">{esc(task.get("last_error", ""))}</div>' if task.get("last_error") else ""
    iframe = (
        f'<div class="terminal-wrap" data-terminal-wrap><iframe class="terminal-frame" src="{esc(terminal)}"></iframe></div>'
        if status == "running"
        else '<div class="empty">任务未运行。</div>'
    )
    task_id = str(task["id"])
    return page(
        str(task.get("name", "")),
        f"""{error_html}
<section class="toolbar">
  <div>
    <div style="font-size:18px;font-weight:650">{esc(task.get("name", ""))}</div>
    <div class="mono" style="color:var(--muted);margin-top:4px">{esc(node.name)} · {esc(task.get("workdir", ""))} · {esc(task.get("command", ""))} · :{esc(task.get("ttyd_port", ""))}</div>
  </div>
  <div class="actions">
    <span class="status {esc(status)}">{esc(status)}</span>
    <button class="btn" type="button" data-fullscreen-button onclick="toggleTerminalFullscreen()">全屏</button>
    <a class="btn" target="_blank" rel="noreferrer" href="{esc(terminal)}">新窗口</a>
    <form class="inline" method="post" action="/nodes/{esc(node.id)}/tasks/{esc(task_id)}/start"><button class="btn" type="submit">启动</button></form>
    <form class="inline" method="post" action="/nodes/{esc(node.id)}/tasks/{esc(task_id)}/restart"><button class="btn" type="submit">重启</button></form>
    <form class="inline" method="post" action="/nodes/{esc(node.id)}/tasks/{esc(task_id)}/stop"><button class="btn" type="submit">停止</button></form>
    <form class="inline" method="post" action="/nodes/{esc(node.id)}/tasks/{esc(task_id)}/delete"><button class="btn danger" type="submit">删除</button></form>
  </div>
</section>
{iframe}""",
    )


class Handler(BaseHTTPRequestHandler):
    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        if not self.request_allowed(parsed.path):
            return
        if parsed.path in ("/", "/health"):
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
        else:
            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        try:
            if not self.request_allowed(path):
                return
            if path == "/":
                self.html(render_index(self))
            elif path == "/health":
                self.text("ok\n")
            elif path == "/nodes":
                self.html(render_nodes(load_nodes()))
            elif path.startswith("/nodes/"):
                parts = path.split("/")
                node = node_by_id(parts[2] if len(parts) > 2 else "")
                if not node:
                    self.not_found()
                    return
                if len(parts) == 3:
                    self.html(render_node_index(self, node))
                elif len(parts) == 5 and parts[3] == "tasks" and parts[4] == "new":
                    self.html(render_node_new(node))
                elif len(parts) == 5 and parts[3] == "tasks":
                    task = get_node_task(node, parts[4])
                    if not task:
                        self.not_found()
                        return
                    self.html(render_node_task(self, node, task))
                else:
                    self.not_found()
            elif path == "/tasks/new":
                self.html(render_new())
            elif path == "/api/tasks":
                self.json([task_to_dict(task) for task in list_tasks()])
            elif path.startswith("/api/tasks/"):
                task = get_task(path.split("/")[-1])
                if not task:
                    self.not_found()
                    return
                self.json(task_to_dict(task))
            elif path.startswith("/tasks/"):
                task_id = path.split("/")[2]
                task = get_task(task_id)
                if not task:
                    self.not_found()
                    return
                self.html(render_task(self, task))
            else:
                self.not_found()
        except Exception as exc:
            self.error(str(exc))

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        try:
            if not self.request_allowed(path):
                return
            if path.startswith("/nodes/"):
                parts = path.split("/")
                node = node_by_id(parts[2] if len(parts) > 2 else "")
                if not node:
                    self.not_found()
                    return
                if len(parts) == 4 and parts[3] == "tasks":
                    form = self.read_form()
                    task = create_node_task(
                        node,
                        {
                            "name": form.get("name", [""])[0],
                            "workdir": form.get("workdir", [""])[0],
                            "command": form.get("command", [""])[0],
                            "autostart": "autostart" in form,
                        },
                    )
                    task_id = task.get("id")
                    self.redirect(f"/nodes/{node.id}/tasks/{task_id}" if task_id else f"/nodes/{node.id}")
                    return
                if len(parts) == 6 and parts[3] == "tasks":
                    task_id = parts[4]
                    action = parts[5]
                    run_node_task_action(node, task_id, action)
                    if action in ("stop", "delete"):
                        self.redirect(f"/nodes/{node.id}")
                    else:
                        self.redirect(f"/nodes/{node.id}/tasks/{task_id}")
                    return
                self.not_found()
                return
            if path == "/tasks":
                form = self.read_form()
                task = create_task(
                    form.get("name", [""])[0],
                    form.get("workdir", [""])[0],
                    form.get("command", [""])[0],
                    "autostart" in form,
                )
                self.redirect(f"/tasks/{task.id}")
                return
            if path.startswith("/tasks/"):
                parts = path.split("/")
                if len(parts) < 4:
                    self.not_found()
                    return
                task = get_task(parts[2])
                action = parts[3]
                if not task:
                    self.not_found()
                    return
                if action == "start":
                    start_task(task)
                    self.redirect(f"/tasks/{task.id}")
                elif action == "stop":
                    stop_task(task)
                    self.redirect("/")
                elif action == "restart":
                    restart_task(task)
                    self.redirect(f"/tasks/{task.id}")
                elif action == "delete":
                    delete_task(task)
                    self.redirect("/")
                else:
                    self.not_found()
                return
            if path == "/api/tasks":
                payload = self.read_json()
                task = create_task(
                    payload.get("name", ""),
                    payload.get("workdir", ""),
                    payload.get("command", ""),
                    bool(payload.get("autostart", True)),
                )
                self.json(task_to_dict(task), status=HTTPStatus.CREATED)
                return
            if path.startswith("/api/tasks/"):
                parts = path.split("/")
                if len(parts) < 5:
                    self.not_found()
                    return
                task = get_task(parts[3])
                action = parts[4]
                if not task:
                    self.not_found()
                    return
                if action == "start":
                    start_task(task)
                    self.json(task_to_dict(get_task(task.id) or task))
                elif action == "stop":
                    stop_task(task)
                    self.json(task_to_dict(get_task(task.id) or task))
                elif action == "restart":
                    restart_task(task)
                    self.json(task_to_dict(get_task(task.id) or task))
                elif action == "delete":
                    delete_task(task)
                    self.json({"ok": True})
                else:
                    self.not_found()
                return
            self.not_found()
        except Exception as exc:
            if path == "/tasks":
                self.html(render_new(str(exc)), status=HTTPStatus.BAD_REQUEST)
            else:
                self.error(str(exc))

    def read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length) if length else b""

    def read_form(self) -> dict[str, list[str]]:
        return parse_qs(self.read_body().decode())

    def read_json(self) -> dict[str, Any]:
        body = self.read_body()
        return json.loads(body.decode() or "{}")

    def request_allowed(self, path: str) -> bool:
        if not client_ip_allowed(self.client_address[0]):
            self.forbidden()
            return False
        if not request_authorized(self.headers):
            self.unauthorized(path)
            return False
        return True

    def html(self, content: bytes, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        content = json.dumps(payload, ensure_ascii=False, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def text(self, payload: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        content = payload.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.end_headers()

    def not_found(self) -> None:
        self.html(page("Not Found", '<div class="empty">Not found.</div>'), status=HTTPStatus.NOT_FOUND)

    def unauthorized(self, path: str = "") -> None:
        if path.startswith("/api/"):
            content = json.dumps({"error": "unauthorized"}, ensure_ascii=False).encode()
            content_type = "application/json; charset=utf-8"
        else:
            content = page("Unauthorized", '<div class="error">Unauthorized.</div>')
            content_type = "text/html; charset=utf-8"
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Basic realm="Task Terminal Manager"')
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def forbidden(self) -> None:
        self.html(page("Forbidden", '<div class="error">Forbidden.</div>'), status=HTTPStatus.FORBIDDEN)

    def error(self, message: str) -> None:
        self.html(page("Error", f'<div class="error">{esc(message)}</div>'), status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[{now()}] {self.client_address[0]} {fmt % args}")


def main() -> None:
    ensure_dirs()
    with db():
        pass
    print(f"Task manager: http://{HOST}:{PORT}")
    print(f"ttyd binary: {ttyd_bin()}")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
