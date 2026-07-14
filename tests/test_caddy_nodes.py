import base64
import json

from scripts import render_caddy_nodes


def test_render_caddy_nodes_uses_node_id_and_per_node_auth(tmp_path, monkeypatch):
    nodes_file = tmp_path / "nodes.json"
    settings_file = tmp_path / "settings.json"
    out_dir = tmp_path / "caddy"
    out_file = out_dir / "nodes.caddy"
    nodes_file.write_text(
        json.dumps(
            [
                {
                    "id": "server83",
                    "base_url": "http://172.16.1.83:7860",
                    "token": "secret83",
                },
                {
                    "id": "server84",
                    "base_url": "http://172.16.1.84:7860",
                    "token": "secret84",
                },
            ]
        )
    )
    settings_file.write_text(json.dumps({"mode": "controller"}))
    monkeypatch.setattr(render_caddy_nodes, "NODES_FILE", nodes_file)
    monkeypatch.setattr(render_caddy_nodes, "SETTINGS_FILE", settings_file)
    monkeypatch.setattr(render_caddy_nodes, "OUT_DIR", out_dir)
    monkeypatch.setattr(render_caddy_nodes, "OUT_FILE", out_file)

    render_caddy_nodes.main()

    output = out_file.read_text()
    assert "^/node-ttyd/server83/(77[0-9][0-9])" in output
    assert "^/node-ttyd/server84/(77[0-9][0-9])" in output
    assert "reverse_proxy 172.16.1.83:7860" in output
    assert "reverse_proxy 172.16.1.84:7860" in output
    assert base64.b64encode(b"admin:secret83").decode() in output
    assert base64.b64encode(b"admin:secret84").decode() in output


def test_agent_mode_generates_no_remote_routes(tmp_path, monkeypatch):
    nodes_file = tmp_path / "nodes.json"
    settings_file = tmp_path / "settings.json"
    out_dir = tmp_path / "caddy"
    out_file = out_dir / "nodes.caddy"
    nodes_file.write_text('[{"id":"server-a","base_url":"http://192.168.1.21:7860","token":"secret"}]')
    settings_file.write_text('{"mode":"agent"}')
    monkeypatch.setattr(render_caddy_nodes, "NODES_FILE", nodes_file)
    monkeypatch.setattr(render_caddy_nodes, "SETTINGS_FILE", settings_file)
    monkeypatch.setattr(render_caddy_nodes, "OUT_DIR", out_dir)
    monkeypatch.setattr(render_caddy_nodes, "OUT_FILE", out_file)

    render_caddy_nodes.main()

    assert out_file.read_text() == "# no remote nodes configured\n"
