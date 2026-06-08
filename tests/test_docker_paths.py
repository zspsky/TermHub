from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_docker_files_do_not_depend_on_host_project_path():
    dockerfile = (ROOT / "Dockerfile").read_text()
    compose = (ROOT / "docker-compose.yml").read_text()
    docker_start = (ROOT / "scripts" / "docker_start.sh").read_text()

    assert "/server/test" not in dockerfile
    assert "/server/test" not in compose
    assert "TASK_MANAGER_BASE_DIR" in dockerfile
    assert "${TASK_MANAGER_BASE_DIR}/server.py" in dockerfile
    assert "TASK_MANAGER_BASE_DIR: ${TASK_MANAGER_BASE_DIR:-${PWD}}" in compose
    assert "TTYD_BIN: ${TASK_MANAGER_BASE_DIR:-${PWD}}/tools/ttyd/1.7.7/ttyd" in compose
    assert "- ${TASK_MANAGER_BASE_DIR:-${PWD}}:${TASK_MANAGER_BASE_DIR:-${PWD}}" in compose
    assert "CADDY_AUTH_PASSWORD: ${CADDY_AUTH_PASSWORD:-}" in compose
    assert "CADDY_BASIC_AUTH_HASH: ${CADDY_BASIC_AUTH_HASH:-}" in compose
    assert "caddy hash-password --plaintext" in compose
    assert "import /etc/caddy/nodes.caddy" in (ROOT / "Caddyfile").read_text()
    assert "node-ttyd" in (ROOT / "scripts" / "render_caddy_nodes.py").read_text()
    assert "strip_prefix /ttyd" not in (ROOT / "Caddyfile").read_text()
    assert "export TASK_MANAGER_BASE_DIR=\"$BASE_DIR\"" in docker_start
    assert "scripts/render_caddy_nodes.py" in docker_start
