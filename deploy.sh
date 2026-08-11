#!/usr/bin/env bash
set -euo pipefail

# Deploy the owned SDK v2 package to the central Dolt host.
# The script does not copy .env and never creates credentials.

REMOTE="${REMOTE:-erp4projects}"
MCP_DIR="/opt/beads-mcp-server"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Deploying beads-mcp HTTP service to $REMOTE:$MCP_DIR ==="

echo "[1/4] Ensuring the target directory exists..."
ssh "$REMOTE" "mkdir -p $MCP_DIR/src $MCP_DIR/logs"

echo "[2/4] Syncing package sources and lockfile..."
rsync -avz \
  "$SCRIPT_DIR/pyproject.toml" \
  "$SCRIPT_DIR/uv.lock" \
  "$SCRIPT_DIR/.env.example" \
  "$REMOTE:$MCP_DIR/"
rsync -avz "$SCRIPT_DIR/src/" "$REMOTE:$MCP_DIR/src/"

echo "[3/4] Installing the locked package..."
ssh "$REMOTE" "cd $MCP_DIR && uv sync --frozen --no-dev"

echo "[4/4] Restarting and checking the local endpoint..."
ssh "$REMOTE" "systemctl restart beads-mcp-server && systemctl is-active beads-mcp-server && curl -fsS http://127.0.0.1:8092/health >/dev/null"

echo "=== Deployment completed ==="
