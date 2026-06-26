#!/usr/bin/env bash
set -euo pipefail

# Deploy the beads MCP HTTP service (FastMCP launcher) to the central Dolt host.
#
# Architecture: the official beads-mcp (FastMCP) + fastmcp run under a uv-managed
# tool venv on the host (`uv tool install beads-mcp`). serve.py imports
# `beads_mcp.server:mcp` from that venv and serves it over Streamable HTTP with a
# static bearer token. ONE persistent process -> no per-session spawn.
#
# Requires privileges on $REMOTE: writes /opt/beads-mcp-server, runs
# `systemctl restart`. Mirrors ~/code/ai/crawl4ai-mcp/deploy.sh.

REMOTE="${REMOTE:-erp4projects}"          # ssh alias for dolt.cognovis.de / 116.202.111.75
MCP_DIR="/opt/beads-mcp-server"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Deploying beads-mcp HTTP service to $REMOTE:$MCP_DIR ==="

echo "[1/3] Ensuring target dir + inner beads-mcp (uv tool)..."
ssh "$REMOTE" "mkdir -p $MCP_DIR/logs && (uv tool install beads-mcp >/dev/null 2>&1 || uv tool upgrade beads-mcp >/dev/null 2>&1 || true)"

echo "[2/3] Syncing launcher..."
rsync -avz \
  "$SCRIPT_DIR/serve.py" \
  "$SCRIPT_DIR/.env.example" \
  "$REMOTE:$MCP_DIR/"

echo "[3/3] Restarting service..."
ssh "$REMOTE" "systemctl restart beads-mcp-server && sleep 3 && systemctl is-active beads-mcp-server"

echo "=== Done. Tail logs with: ssh $REMOTE 'tail -f $MCP_DIR/logs/beads-mcp-server.log' ==="
