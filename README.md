# Cognovis Beads MCP Server

An owned Python MCP server built on the official MCP SDK v2. It exposes a
curated typed surface over `bd` and keeps `bd` as the Beads domain authority.

## Architecture

```text
MCP SDK v2 Streamable HTTP
  -> static bearer verification
  -> typed tools with mandatory workspace_id
  -> explicit workspace registry
  -> one bounded async bd subprocess per call
  -> workspace-configured shared Dolt server
```

The server does not import or wrap the upstream `beads-mcp` package. It never
queries Dolt directly and does not reproduce Beads business logic. Each call
starts a fresh `bd` process using an argv list, a hard timeout, bounded stdout
and stderr, cancellation cleanup, and an explicit child-environment allowlist.

Protocol sessions are not application state. Modern 2026-07-28 requests are
sessionless and every workspace-sensitive call carries `workspace_id`. The
registry resolves that stable identifier to an operator-controlled absolute
path; clients cannot submit filesystem paths.

The first release deliberately has no request queue, semaphore, per-workspace
lock, connection pool, or Dolt adapter. Sparse traffic from 40 to 50 connected
agents should be measured before adding coordination that may duplicate what
`bd` and the shared Dolt server already provide.

## Tool surface

The server exposes `ready`, `list`, `show`, `create`, `claim`, `update`,
`close`, `reopen`, `dep`, `comment`, `comments`, `note`, `stats`, and `blocked`.
There is no mutable context tool, cwd discovery, arbitrary command passthrough,
workspace provisioning, or direct Dolt operation.

## Configuration

Copy `.env.example` to the service's existing protected environment file and
set values through the operator's secret-management process. The service fails
closed when the bearer token, public URL, allowed hosts, or workspace registry
is absent.

The registry is a JSON object:

```text
BEADS_WORKSPACES_JSON={"hetzner":"/opt/beads-workspaces/hetzner","polaris":"/opt/beads-workspaces/polaris"}
```

Only these variables can reach `bd` when present: `PATH`, `HOME`, `USER`,
`LOGNAME`, `LANG`, `LC_ALL`, `BEADS_ACTOR`, `BEADS_DOLT_SERVER_HOST`,
`BEADS_DOLT_SERVER_PORT`, `BEADS_DOLT_SERVER_USER`, `BEADS_DOLT_PASSWORD`, and
`DOLT_PASSWORD`. The server never logs their values.

The public `/health` route returns only service identity, version, and status.
The `/mcp` route requires the configured static bearer token. Host and Origin
validation is enforced by the SDK's `TransportSecuritySettings`.

## Local development

Python 3.14 and `uv` are required.

```bash
uv sync --frozen
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv build
```

Run the service with an operator-provided environment:

```bash
uv run beads-mcp-server
```

## Deployment boundary

`deploy.sh` syncs the package sources and lockfile, installs with
`uv sync --frozen --no-dev`, restarts the existing systemd service, and checks
the local health endpoint. It does not copy `.env`, create a token, modify
workspace metadata, or initialize a Beads database.

The systemd service must execute:

```text
/opt/beads-mcp-server/.venv/bin/beads-mcp-server
```

This repository change does not itself deploy the service. Updating the Atlas
unit and validating real configured clients remains a separate infrastructure
cutover with rollback evidence.

## Compatibility evidence

The test suite uses the official in-memory SDK client for modern discovery and
typed tool calls. HTTP integration tests exercise the real ASGI transport,
bearer rejection, Host and Origin rejection, and independent 2026-07-28
requests without `Mcp-Session-Id`.

No handshake-era client behavior is promised. `stateless_http=True` therefore
disables legacy back-channels; this server has no server-initiated requests or
legacy notifications that require them. Official final-spec conformance is run
against a local HTTP endpoint before release and is reported separately from
unit and client compatibility evidence.
