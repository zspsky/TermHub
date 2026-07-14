import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import server


def test_load_nodes_returns_local_node_when_config_is_missing(tmp_path):
    nodes = server.load_nodes(tmp_path / "missing.json")

    assert len(nodes) == 1
    assert nodes[0].id == "local"
    assert nodes[0].name == "Local"
    assert nodes[0].local is True


def test_load_nodes_appends_remote_nodes_from_json(tmp_path):
    nodes_file = tmp_path / "nodes.json"
    nodes_file.write_text(
        json.dumps(
            [
                {
                    "id": "server-a",
                    "name": "Server A",
                    "base_url": "http://192.168.1.21:7860/",
                    "token": "secret-a",
                }
            ]
        )
    )

    nodes = server.load_nodes(nodes_file)

    assert [node.id for node in nodes] == ["local", "server-a"]
    assert nodes[1].name == "Server A"
    assert nodes[1].base_url == "http://192.168.1.21:7860"
    assert nodes[1].token == "secret-a"
    assert nodes[1].local is False


def test_api_authorized_allows_requests_when_token_is_unset():
    assert server.api_authorized({}, "") is True


def test_api_authorized_requires_matching_bearer_token():
    assert server.api_authorized({"Authorization": "Bearer secret"}, "secret") is True
    assert server.api_authorized({"Authorization": "Bearer wrong"}, "secret") is False
    assert server.api_authorized({}, "secret") is False


def run_json_server():
    requests = []

    class JsonHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(
                {
                    "method": "GET",
                    "path": self.path,
                    "authorization": self.headers.get("Authorization", ""),
                    "body": "",
                }
            )
            self.send_json([{"id": "task-a", "name": "Task A"}])

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode() if length else ""
            requests.append(
                {
                    "method": "POST",
                    "path": self.path,
                    "authorization": self.headers.get("Authorization", ""),
                    "body": body,
                }
            )
            self.send_json({"ok": True}, status=201)

        def send_json(self, payload, status=200):
            content = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, fmt, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), JsonHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, requests, f"http://127.0.0.1:{httpd.server_port}"


def test_remote_tasks_fetches_json_with_bearer_token():
    httpd, requests, base_url = run_json_server()
    try:
        node = server.Node(id="remote-a", name="Remote A", base_url=base_url, token="secret")

        tasks = server.remote_tasks(node)

        assert tasks == [{"id": "task-a", "name": "Task A"}]
        assert requests[0]["method"] == "GET"
        assert requests[0]["path"] == "/api/tasks"
        assert requests[0]["authorization"] == f"Basic {base64.b64encode(b'admin:secret').decode()}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_remote_create_task_posts_json_payload():
    httpd, requests, base_url = run_json_server()
    try:
        node = server.Node(id="remote-a", name="Remote A", base_url=base_url, token="secret")

        result = server.remote_create_task(
            node,
            {"name": "Codex", "workdir": "/srv/app", "command": "codex", "autostart": True},
        )

        assert result == {"ok": True}
        assert requests[0]["method"] == "POST"
        assert requests[0]["path"] == "/api/tasks"
        assert json.loads(requests[0]["body"]) == {
            "name": "Codex",
            "workdir": "/srv/app",
            "command": "codex",
            "autostart": True,
        }
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_remote_task_action_posts_to_api_action_path():
    httpd, requests, base_url = run_json_server()
    try:
        node = server.Node(id="remote-a", name="Remote A", base_url=base_url, token="secret")

        result = server.remote_task_action(node, "task-a", "restart")

        assert result == {"ok": True}
        assert requests[0]["method"] == "POST"
        assert requests[0]["path"] == "/api/tasks/task-a/restart"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_node_task_url_uses_remote_node_host_and_ttyd_port():
    node = server.Node(id="remote-a", name="Remote A", base_url="http://192.168.1.21:7860")

    url = server.node_task_url(None, node, {"ttyd_port": 7701})

    assert url == "/node-ttyd/remote-a/7701/"


def test_ttyd_command_includes_base_path_when_configured(monkeypatch):
    task = server.Task(
        id="task-a",
        name="Task A",
        workdir="/tmp",
        command="bash",
        ttyd_port=7701,
        tmux_socket="/tmp/task-a.tmux",
        tmux_session="task-a",
        ttyd_pid=None,
        status="stopped",
        last_error="",
        created_at="2026-06-08 18:00:00",
        updated_at="2026-06-08 18:00:00",
    )

    monkeypatch.setattr(server, "TTYD_BASE_PATH_PREFIX", "/ttyd")

    cmd = server.build_ttyd_cmd(task)

    assert "-b" in cmd
    assert "/ttyd/7701" in cmd


def test_render_nodes_links_to_each_configured_node():
    nodes = [
        server.Node(id="local", name="Local", local=True),
        server.Node(id="remote-a", name="Remote A", base_url="http://192.168.1.21:7860"),
    ]

    html = server.render_nodes(nodes).decode()

    assert "Local" in html
    assert "Remote A" in html
    assert "/nodes/local" in html
    assert "/nodes/remote-a" in html


def test_render_index_lists_tasks_from_all_nodes(monkeypatch):
    nodes = [
        server.Node(id="local", name="Local", local=True),
        server.Node(id="remote-a", name="Remote A", base_url="http://192.168.1.21:7860"),
    ]

    def fake_load_nodes():
        return nodes

    def fake_list_node_tasks(node):
        return [
            {
                "id": f"{node.id}-task",
                "name": f"{node.name} Task",
                "workdir": "/srv/app",
                "command": "codex",
                "ttyd_port": 7701,
                "status": "running",
                "updated_at": "2026-06-08 18:00:00",
            }
        ]

    monkeypatch.setattr(server, "load_nodes", fake_load_nodes)
    monkeypatch.setattr(server, "load_mode", lambda: "controller")
    monkeypatch.setattr(server, "list_node_tasks", fake_list_node_tasks)

    html = server.render_index(None).decode()

    assert "机器" in html
    assert "Local Task" in html
    assert "Remote A Task" in html
    assert "/nodes/local/tasks/local-task" in html
    assert "/nodes/remote-a/tasks/remote-a-task" in html


def test_request_authorized_accepts_basic_auth_and_bearer_password():
    basic = base64.b64encode(b"admin:secret").decode()

    assert server.request_authorized({"Authorization": f"Basic {basic}"}, "secret") is True
    assert server.request_authorized({"Authorization": "Bearer secret"}, "secret") is True
    assert server.request_authorized({"Authorization": "Bearer wrong"}, "secret") is False
    assert server.request_authorized({}, "secret") is False


def test_client_ip_allowed_supports_single_ips_and_cidr_ranges():
    allowed = "127.0.0.1,172.16.1.0/24,192.168.180.34"

    assert server.client_ip_allowed("127.0.0.1", allowed) is True
    assert server.client_ip_allowed("172.16.1.83", allowed) is True
    assert server.client_ip_allowed("192.168.180.34", allowed) is True
    assert server.client_ip_allowed("172.16.2.83", allowed) is False
