#!/usr/bin/env bash
set -euo pipefail

# Deploy beads-mcp-server (outer OAuth/HTTP gateway) to the central Dolt host.
# Inner `beads-mcp` (Python/FastMCP) is installed separately on the host via
#   uv tool install beads-mcp
#
# Requires privileges on $REMOTE: writes /opt/beads-mcp-server, runs
# `systemctl restart`. Mirrors the pattern of ~/code/ai/crawl4ai-mcp/deploy.sh.

REMOTE="${REMOTE:-erp4projects}"          # ssh alias for dolt.cognovis.de / 116.202.111.75
MCP_DIR="/opt/beads-mcp-server"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Deploying beads-mcp-server to $REMOTE:$MCP_DIR ==="

echo "[1/4] Ensuring target dir..."
ssh "$REMOTE" "mkdir -p $MCP_DIR/logs"

echo "[2/4] Syncing source (excludes node_modules, .env, logs)..."
rsync -avz --delete \
  --exclude node_modules \
  --exclude .env \
  --exclude logs \
  --exclude clients.json \
  "$SCRIPT_DIR/src" \
  "$SCRIPT_DIR/package.json" \
  "$SCRIPT_DIR/package-lock.json" \
  "$SCRIPT_DIR/tsconfig.json" \
  "$SCRIPT_DIR/.env.example" \
  "$REMOTE:$MCP_DIR/"

echo "[3/4] Installing npm dependencies..."
ssh "$REMOTE" "cd $MCP_DIR && npm install --omit=dev 2>&1 | tail -3"

echo "[4/4] Restarting service..."
ssh "$REMOTE" "systemctl restart beads-mcp-server && sleep 2 && systemctl is-active beads-mcp-server"

echo "=== Done. Tail logs with: ssh $REMOTE 'tail -f $MCP_DIR/logs/beads-mcp-server.log' ==="
