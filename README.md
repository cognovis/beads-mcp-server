# Beads MCP Server

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

The server exposes `workspaces`, `workspace_status`, `ready`, `list`, `show`,
`create`, `claim`, `update`, `close`, `reopen`, `dep`, `comment`, `comments`,
`note`, `stats`, and `blocked`. `workspaces` returns only exact IDs and never
server paths. `workspace_status` performs a read-only `bd stats --no-activity`
probe for one ID. There is no mutable context tool, cwd discovery, arbitrary
command passthrough, workspace provisioning, or direct Dolt operation.

## Configuration

Copy `.env.example` to the service's existing protected environment file and
set values through the operator's secret-management process. The service fails
closed when the bearer token, public URL, allowed hosts, or workspace registry
is absent.

For a central service with many repositories, point the server at an
operator-controlled root:

```text
BEADS_WORKSPACE_ROOT=/srv/beads-workspaces
BEADS_READINESS_WORKSPACE_ID=project-a
```

Every immediate child containing `.beads/metadata.json` or
`.beads/config.yaml` becomes one exact `workspace_id`. The server never scans
components of a client path and never treats dashes and underscores as aliases.
Consequently, `library`, `cognovis_core`, and `cognovis-core` can coexist
without ambiguity. An explicit `BEADS_WORKSPACES_JSON` object remains available
for installations that prefer a fixed mapping; configure exactly one source.

Only these variables can reach `bd` when present: `PATH`, `HOME`, `USER`,
`LOGNAME`, `LANG`, `LC_ALL`, `BEADS_ACTOR`, `BEADS_DOLT_SERVER_HOST`,
`BEADS_DOLT_SERVER_PORT`, `BEADS_DOLT_SERVER_USER`, `BEADS_DOLT_PASSWORD`, and
`DOLT_PASSWORD`. The server never logs their values.

The public `/health` route is a process liveness check. `/ready` performs one
configured read-only workspace probe and returns no workspace ID or filesystem
path. The `/mcp` route requires the configured static bearer token. Host and
Origin validation is enforced by the SDK's `TransportSecuritySettings`.

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

This repository contains the reusable package rather than a specific server's
deployment. Install with `uv sync --frozen --no-dev` and run:

```text
.venv/bin/beads-mcp-server
```

Keep service-manager units, reverse-proxy rules, concrete hostnames, secret
delivery, and workspace locations in the consuming infrastructure repository.
The server does not copy an environment file, create a token, initialize a
Beads database, or rewrite workspace metadata.

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

## License

Released under the [MIT License](LICENSE).
