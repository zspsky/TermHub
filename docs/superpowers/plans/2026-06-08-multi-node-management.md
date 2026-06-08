# Multi-Node Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a central dashboard mode that can manage tasks on other reachable Task Terminal Manager nodes.

**Architecture:** Keep each node responsible for its own local tasks, tmux sessions, ttyd ports, and database. The central instance reads `data/nodes.json`, calls remote node APIs with optional bearer tokens, and renders node-scoped task pages.

**Tech Stack:** Python standard library HTTP server, SQLite, JSON config, urllib HTTP client, pytest.

---

### Task 1: Node Configuration And Auth

**Files:**
- Modify: `server.py`
- Test: `tests/test_nodes.py`

- [ ] Write tests for `load_nodes()`, local fallback, and bearer auth.
- [ ] Run `python3 -m pytest -q tests/test_nodes.py` and confirm failures for missing functions.
- [ ] Add `Node` dataclass, `NODES_FILE`, `MANAGER_TOKEN`, `load_nodes()`, `node_by_id()`, and API auth checks.
- [ ] Run `python3 -m pytest -q tests/test_nodes.py` and confirm pass.

### Task 2: Remote API Client

**Files:**
- Modify: `server.py`
- Test: `tests/test_nodes.py`

- [ ] Write tests using a local `ThreadingHTTPServer` for remote GET and POST requests with bearer token headers.
- [ ] Run `python3 -m pytest -q tests/test_nodes.py` and confirm failures for missing client helpers.
- [ ] Add `remote_json()`, `remote_tasks()`, `remote_task()`, `remote_create_task()`, and `remote_task_action()`.
- [ ] Run `python3 -m pytest -q tests/test_nodes.py` and confirm pass.

### Task 3: Node UI And Form Routing

**Files:**
- Modify: `server.py`
- Test: `tests/test_nodes.py`

- [ ] Write tests for remote terminal URL generation and node task route rendering.
- [ ] Run `python3 -m pytest -q tests/test_nodes.py` and confirm failures for missing render helpers.
- [ ] Add `/nodes`, `/nodes/<node_id>`, `/nodes/<node_id>/tasks/new`, `/nodes/<node_id>/tasks/<task_id>`, and forwarded POST routes.
- [ ] Keep existing local routes working by delegating them to the local node path.
- [ ] Run `python3 -m pytest -q tests/test_nodes.py` and confirm pass.

### Task 4: Documentation And Verification

**Files:**
- Modify: `README.md`
- Test: existing pytest suite

- [ ] Document remote deployment, `TASK_MANAGER_TOKEN`, `TASK_MANAGER_NODES_FILE`, and example `data/nodes.json`.
- [ ] Run `python3 -m pytest -q`.
- [ ] Run `docker compose config`.
- [ ] Start or restart the service and verify `/health` and `/nodes` respond.
