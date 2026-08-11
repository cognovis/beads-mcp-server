# beads-mcp-server

Thin **FastMCP HTTP launcher** that serves the official `beads-mcp` over
Streamable HTTP with a **static bearer token**, so fleet agents (Hermes, your own
tools) can read/write beads across all repos **without a local checkout** — all
data lives on the central Dolt server.

## Why this exists

The official `beads-mcp` (gastownhall/beads, FastMCP-based) is **stdio-only and
single-repo** out of the box (`main()` calls `mcp.run_async(transport="stdio")`).
But:

- FastMCP supports **Streamable HTTP transport + auth** natively.
- `beads-mcp` already supports **multi-repo** via the `workspace_root` tool param.

So instead of a custom HTTP/OAuth gateway, `serve.py` just imports the official
`beads_mcp.server:mcp`, attaches a `StaticTokenVerifier` (one fixed token from
`.env`), and runs it over HTTP. ~70 lines, no bespoke protocol code.

> History: this replaced a ~600-line Node OAuth 2.1 wrapper (2026-06-26). That
> wrapper spawned a fresh `beads-mcp` **per HTTP session**, so every interactive
> call paid Python startup + Dolt cold-load (~14s, felt like a hang). One
> persistent FastMCP process removes that: connect ~150ms, warm call ~0.6s.

## Architecture

```
client --HTTPS--> Caddy (dolt.cognovis.de/mcp) --> FastMCP HTTP :8092 (serve.py)
                                                       |  static bearer auth
                                                       v
                                          official beads-mcp tools (workspace_root)
                                                       v
                                          bd  -->  central Dolt SQL :3306
```

- Runs on **erp4projects** / `dolt.cognovis.de` (Hetzner), next to
  `dolt-server.service`. Loopback-only; Caddy terminates TLS + routes `/mcp`.
- Per-repo **server-mode** workspaces under `/opt/beads-workspaces/<repo>` connect
  to every Dolt database that carries a Beads `issues` table. Conventional
  `beads_<repo>` databases map to `<repo>`; legacy databases without that prefix
  retain their full database name. `serve.py` self-heals the workspaces on startup
  (forces `dolt_mode: server`, aligns `project_id` with the DB) and pre-warms hot
  repos (`BEADS_PREWARM_REPOS`).

## Auth

Static bearer token (`BEADS_MCP_TOKEN` in `.env`), validated by FastMCP's
`StaticTokenVerifier`. Clients send `Authorization: Bearer <token>`.

> Trade-off: no OAuth 2.1 dynamic client registration -> the **claude.ai / iOS**
> custom-connector flow (which requires DCR) is not supported. Hermes and any
> bearer-capable client work fine. Re-add an OAuth proxy (FastMCP `OAuthProxy`)
> if claude.ai connector support is needed again.

## Run / deploy

Inner MCP (once, on the host): `uv tool install beads-mcp`
Then:

```bash
./deploy.sh           # rsync serve.py + restart the service
```

systemd unit, hardening drop-in, and ops notes live in
`infra-devops/hetzner/atlas/services/beads-mcp/`. Secrets in
`/opt/beads-mcp-server/.env` (not in git) — see `.env.example`.

## Known residual: Dolt cold-load

The structural per-session cost is gone, but the **first** call to a repo that
Dolt has evicted still pays a lazy DB-load (~9s for large repos like
mira/polaris); subsequent calls are ~0.6s. `BEADS_PREWARM_REPOS` only mitigates
transiently — the durable fix is Dolt-side memory/cache tuning so working sets
stay resident.
