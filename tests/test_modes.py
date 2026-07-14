import json

import pytest

import server


def test_mode_is_unset_until_first_login_selection(tmp_path):
    settings = tmp_path / "settings.json"

    assert server.load_mode(settings) is None

    server.save_mode("controller", settings)

    assert server.load_mode(settings) == "controller"


def test_agent_mode_only_exposes_local_node():
    nodes = [
        server.Node(id="local", name="Local", local=True),
        server.Node(id="server-a", name="Server A", base_url="http://192.168.1.21:7860"),
    ]

    assert [node.id for node in server.nodes_for_mode(nodes, "agent")] == ["local"]
    assert [node.id for node in server.nodes_for_mode(nodes, "controller")] == ["local", "server-a"]


def test_add_node_persists_validated_node(tmp_path):
    nodes_file = tmp_path / "nodes.json"

    node = server.add_node("server-a", "Server A", "http://192.168.1.21:7860/", "secret", nodes_file)

    assert node.base_url == "http://192.168.1.21:7860"
    assert json.loads(nodes_file.read_text()) == [
        {
            "id": "server-a",
            "name": "Server A",
            "base_url": "http://192.168.1.21:7860",
            "token": "secret",
        }
    ]

    with pytest.raises(ValueError, match="already exists"):
        server.add_node("server-a", "Duplicate", "http://192.168.1.22:7860", "secret", nodes_file)


def test_setup_page_offers_controller_and_agent_modes():
    content = server.render_setup().decode()

    assert 'value="controller"' in content
    assert 'value="agent"' in content
    assert "控端" in content
    assert "被控端" in content


def test_controller_can_open_add_node_form():
    content = server.render_add_node().decode()

    assert 'action="/nodes"' in content
    assert 'name="base_url"' in content
    assert 'name="token"' in content
