"""beads MCP over Streamable HTTP (FastMCP) with a static bearer token.

One persistent process -> no per-session spawn, no cold-start hang. Multi-repo
is native via the official beads-mcp `workspace_root` tool parameter. On startup
we self-heal the per-repo server-mode workspaces under BEADS_WORKING_DIR and
optionally pre-warm hot repos (BEADS_PREWARM_REPOS) so the first real call to
them does not pay Dolt's lazy DB-load cost."""
import json, os, subprocess
from pathlib import Path
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from beads_mcp.server import mcp

DOLT_HOST = os.environ.get("DOLT_HOST", "127.0.0.1")
DOLT_PORT = os.environ.get("DOLT_PORT", "3306")
DOLT_USER = os.environ.get("DOLT_USER", "malte")
DOLT_PW = os.environ.get("BEADS_DOLT_PASSWORD") or os.environ.get("DOLT_PASSWORD", "")
WORK_DIR = os.environ.get("BEADS_WORKING_DIR", "/opt/beads-workspaces")
BD = os.environ.get("BEADS_PATH", "/usr/bin/bd")

def _dolt(query):
    r = subprocess.run(["dolt","--host",DOLT_HOST,"--port",DOLT_PORT,"--no-tls",
        "--user",DOLT_USER,"--password",DOLT_PW,"sql","-q",query,"-r","json"],
        capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip())
    return json.loads(r.stdout or '{"rows":[]}').get("rows", [])

def _db_project_id(db):
    try:
        rows = _dolt(f"SELECT value FROM `{db}`.metadata WHERE `key`='_project_id' LIMIT 1")
        return rows[0]["value"] if rows else None
    except Exception:
        return None

def provision():
    """Ensure each beads_* database has a usable server-mode workspace stub.

    Full git checkouts ship a committed metadata.json with dolt_mode "embedded"
    (and a stale project_id) which makes bd read an empty local store -> 0 beads.
    We heal those in place: force server mode, write the dolt-server.port file,
    and align project_id with the canonical DB. Non-destructive: no writes to
    the canonical Dolt database."""
    dbs = [
        next(iter(r.values()))
        for r in _dolt(
            "SELECT DISTINCT table_schema FROM information_schema.tables "
            "WHERE table_name='issues' ORDER BY table_schema"
        )
    ]
    print(f"[provision] {len(dbs)} beads databases", flush=True)
    Path(WORK_DIR).mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "BEADS_DOLT_PASSWORD": DOLT_PW}
    for db in dbs:
        prefix = db[len("beads_"):] if db.startswith("beads_") else db
        ws = Path(WORK_DIR) / prefix
        meta_path = ws / ".beads" / "metadata.json"
        if not meta_path.exists():
            ws.mkdir(parents=True, exist_ok=True)
            try:
                subprocess.run([BD,"init","--database",db,"--server-host",DOLT_HOST,
                    "--server-port",DOLT_PORT,"--server-user",DOLT_USER],
                    cwd=str(ws), env=env, capture_output=True, text=True, timeout=30, check=True)
                print(f"[provision] init {prefix}", flush=True)
            except Exception as e:
                print(f"[provision]   init failed {prefix}: {e}", flush=True)
        try:
            meta = json.loads(meta_path.read_text()); changed = False
            if meta.get("dolt_mode") != "server":
                meta["dolt_mode"] = "server"; meta.pop("dolt_server_port", None)
                (ws / ".beads" / "dolt-server.port").write_text(DOLT_PORT); changed = True
            dbid = _db_project_id(db)
            if dbid and meta.get("project_id") != dbid:
                meta["project_id"] = dbid; changed = True
            if changed:
                meta_path.write_text(json.dumps(meta, indent=2))
                print(f"[provision] healed {prefix}", flush=True)
        except Exception as e:
            print(f"[provision]   heal failed {prefix}: {e}", flush=True)

def prewarm():
    """Load hot repos' issues table into Dolt so the first real call is fast.

    Only a transient mitigation: Dolt evicts idle databases under memory
    pressure, so an idle repo can still pay the lazy-load cost later. The
    durable fix for that is Dolt-side memory/cache tuning."""
    repos = [r.strip() for r in os.environ.get("BEADS_PREWARM_REPOS","").split(",") if r.strip()]
    for r in repos:
        try:
            _dolt(f"SELECT COUNT(*) FROM `beads_{r}`.issues")
            print(f"[prewarm] {r}", flush=True)
        except Exception as e:
            print(f"[prewarm]   {r} failed: {e}", flush=True)

# Health endpoint (no auth) so monitoring/healthcheck has a 200 to hit.
try:
    from starlette.responses import JSONResponse
    @mcp.custom_route("/health", methods=["GET"])
    async def _health(request):
        return JSONResponse({"status": "ok", "service": "beads-mcp"})
except Exception as e:
    print(f"[health] route not registered: {e}", flush=True)

def main():
    try: provision()
    except Exception as e: print(f"[provision] skipped: {e}", flush=True)
    try: prewarm()
    except Exception as e: print(f"[prewarm] skipped: {e}", flush=True)
    token = os.environ["BEADS_MCP_TOKEN"]
    mcp.auth = StaticTokenVerifier({token: {"client_id": "fleet", "scopes": ["read","write"]}})
    print(f"[serve] FastMCP http {os.environ.get('HOST','127.0.0.1')}:{os.environ.get('PORT','8092')}{os.environ.get('MCP_PATH','/mcp')}", flush=True)
    mcp.run(transport="http", host=os.environ.get("HOST","127.0.0.1"),
            port=int(os.environ.get("PORT","8092")), path=os.environ.get("MCP_PATH","/mcp"))

if __name__ == "__main__":
    main()
