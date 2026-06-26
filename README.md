# beads-mcp-server

Remote, OAuth-protected **Streamable-HTTP MCP gateway for beads (bd)**. Lets a
third-party agent (e.g. the Hermes Agent, claude.ai, iOS) read/write beads across
the whole fleet **without a local checkout** — all data lives on the central Dolt
server.

## Two components

1. **Outer wrapper (this repo)** — Node/TS. Express + OAuth 2.1
   (`@modelcontextprotocol/sdk` auth router) + `mcp-proxy`. Terminates HTTPS/OAuth,
   then spawns one **inner** `beads-mcp` per session and proxies tool calls to it.
   Also provisions/repairs a per-repo workspace on startup (`workspace-sync.ts`).
2. **Inner MCP** — the upstream `beads-mcp` Python tool (FastMCP), installed
   separately on the host via `uv tool install beads-mcp`. It runs `bd` commands
   inside the per-repo workspace.

## Where it runs

- Host: **erp4projects** / `dolt.cognovis.de` (Hetzner, 116.202.111.75) — the same
  box as the central Dolt server (`dolt-server.service`). The unit `Requires=
  dolt-server.service`.
- Path: `/opt/beads-mcp-server` (root). Loopback-only; reached externally via
  Caddy at `https://dolt.cognovis.de/mcp` (Bearer token).
- Per-repo workspaces: `/opt/beads-workspaces/<repo>` — **server-mode bd stubs**
  that connect to the local Dolt SQL server (`127.0.0.1:3306`, user `malte`) and
  read the canonical `beads_<repo>` database. No git checkout, no embedded copy.

## How workspace-sync works

On startup, for every `beads_*` database on the Dolt server it ensures a
workspace exists and is usable:

- Missing workspace -> `bd init --database beads_<repo> --server-host/-port/-user`
  (creates a **server-mode** stub; data stays on the Dolt server).
- Existing workspace -> **repair in place** (non-destructive; no `bd init`, no
  writes to the canonical DB):
  - force `dolt_mode: "server"` (else bd reads an empty local store -> 0 beads),
  - drop the deprecated `dolt_server_port` from `metadata.json` + write
    `.beads/dolt-server.port`,
  - align `metadata.json` `project_id` with the database's canonical id
    (`metadata` table, `key='_project_id'`) so bd's PROJECT IDENTITY MISMATCH
    guard does not refuse the connection.

### Gotcha this self-heal guards against (2026-06-26 outage)

Full git checkouts ship a committed `.beads/metadata.json` with
`dolt_mode: "embedded"` (and a stale `project_id`). When the provisioning marker
was changed to "skip if metadata.json exists", those repos were never converted
to server mode -> bd read an empty local store -> the gateway returned **0 beads**
for mira/polaris/codegen/fhir_*/library/macmaint/cognovis_core/hetzner even though
the canonical databases were fully populated. The repair branch above fixes and
prevents this.

## Deploy

```bash
./deploy.sh           # rsync src + package files to /opt/beads-mcp-server, npm install, restart
```

Requires privileges on the remote (writes `/opt`, `systemctl restart
beads-mcp-server`). Deployment glue (systemd unit, hardening drop-in, env
template, ops notes) lives in `infra-devops/hetzner/atlas/services/beads-mcp/`.

Secrets are in `/opt/beads-mcp-server/.env` on the host (not in git) — see
`.env.example`.

## Provenance

Originally built for **LXC 116** (Elysium) with a local Dolt — commit
`81047c9` in `infra-devops/elysium-proxmox`. After 116's Dolt was decommissioned
the service moved to the Hetzner Dolt box; the code was relocated here
(`~/code/ai/beads-mcp-server`) on 2026-06-26 and removed from
`elysium-proxmox/lxc/services/`.
