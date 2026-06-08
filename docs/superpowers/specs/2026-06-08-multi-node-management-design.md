# Multi-Node Management Design

## Goal

Allow one Task Terminal Manager instance to act as a central dashboard for other reachable machines that run the same service.

## Architecture

Each machine continues to own its local `tmux`, `ttyd`, task database, ports, and process lifecycle. The center instance reads a node list from JSON, calls remote node HTTP APIs, and renders remote task lists and task detail pages. Remote nodes expose the same task APIs they already use locally, protected by an optional bearer token.

## Node Configuration

The center reads `TASK_MANAGER_NODES_FILE`, defaulting to `data/nodes.json`. If the file is missing, the service behaves as a single-node local manager.

Example:

```json
[
  {
    "id": "server-a",
    "name": "Server A",
    "base_url": "http://192.168.1.21:7860",
    "token": "shared-secret"
  }
]
```

The local node is always available as `local`. Remote node IDs must be URL-safe enough for path segments.

## Security

If `TASK_MANAGER_TOKEN` is set on a node, API requests must include `Authorization: Bearer <token>`. Browser HTML pages stay accessible because the central dashboard loads remote state through server-side API calls, not direct browser API calls.

The ttyd terminal itself is still exposed on its own port. Operators should keep all manager and ttyd ports inside a trusted LAN, VPN, or firewall boundary.

## User Experience

`/nodes` shows known machines with health state. `/nodes/<node_id>` shows tasks on that machine. Task actions use center-side forms and are forwarded to the selected node. Terminal links and iframes point directly at the selected node's ttyd port.

Existing single-machine URLs keep working:

- `/`
- `/tasks/new`
- `/tasks/<task_id>`
- `/api/tasks`

## Error Handling

If a remote node is unreachable, its row shows an error and task pages display the connection error. Failed forwarded operations return an error page with the remote response details.

## Testing

Unit tests cover node config parsing, auth header enforcement, remote task URL generation, and the remote client methods using a local test HTTP server.
