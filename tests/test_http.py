from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp.types import HEADER_MISMATCH
from starlette.applications import Starlette

from beads_mcp_server.config import ServerConfig, WorkspaceRegistry
from beads_mcp_server.runner import BdResult
from beads_mcp_server.server import create_http_app


class HttpRunner:
    async def run(
        self,
        workspace: Path,
        command: str,
        *arguments: str,
        actor: str | None = None,
    ) -> BdResult:
        return BdResult(data={"workspace": workspace.name, "command": command}, elapsed_ms=2)


@pytest.fixture
def app(tmp_path: Path) -> Starlette:
    metadata = tmp_path / "hetzner" / ".beads" / "metadata.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text("{}")
    config = ServerConfig(
        bearer_token="test-token",
        workspaces=WorkspaceRegistry.from_root(tmp_path),
        readiness_workspace_id="hetzner",
        host="127.0.0.1",
        allowed_hosts=("mcp.test",),
        allowed_origins=("https://client.test",),
        public_url="https://mcp.test/mcp",
    )
    return create_http_app(config, runner=HttpRunner())


@asynccontextmanager
async def client(
    app: Starlette, *, base_url: str = "http://mcp.test"
) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=base_url,
    ) as http_client:
        yield http_client


def modern_headers(
    *, token: str = "test-token", method: str, name: str | None = None
) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": "2026-07-28",
        "MCP-Method": method,
    }
    if name is not None:
        headers["MCP-Name"] = name
    return headers


def modern_meta() -> dict[str, Any]:
    return {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientInfo": {"name": "integration-test", "version": "1"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }


async def post_modern(
    app: Starlette,
    method: str,
    params: dict[str, Any],
    *,
    request_id: int,
) -> httpx.Response:
    payload_params = {**params, "_meta": modern_meta()}
    async with client(app) as http_client:
        return await http_client.post(
            "/mcp",
            headers=modern_headers(
                method=method,
                name=params.get("name") if isinstance(params.get("name"), str) else None,
            ),
            json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": payload_params},
        )


async def test_health_is_public_and_contains_no_sensitive_configuration(app: Starlette) -> None:
    async with app.router.lifespan_context(app), client(app) as http_client:
        response = await http_client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "beads-mcp-server",
        "version": "0.1.0",
    }


async def test_readiness_probes_one_configured_workspace_without_exposing_ids(
    app: Starlette,
) -> None:
    async with app.router.lifespan_context(app), client(app) as http_client:
        response = await http_client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "service": "beads-mcp-server",
        "workspace_count": 1,
    }


@pytest.mark.parametrize("authorization", [None, "Bearer wrong-token", "Basic dGVzdDp0ZXN0"])
async def test_mcp_endpoint_denies_missing_or_invalid_bearer_tokens(
    app: Starlette, authorization: str | None
) -> None:
    headers = modern_headers(method="tools/list")
    if authorization is None:
        headers.pop("Authorization")
    else:
        headers["Authorization"] = authorization

    async with app.router.lifespan_context(app), client(app) as http_client:
        response = await http_client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {"_meta": modern_meta()},
            },
        )

    assert response.status_code == 401


async def test_independent_modern_requests_need_no_session_identifier(app: Starlette) -> None:
    async with app.router.lifespan_context(app):
        discover = await post_modern(app, "server/discover", {}, request_id=1)
        tools = await post_modern(app, "tools/list", {}, request_id=2)
        call = await post_modern(
            app,
            "tools/call",
            {"name": "stats", "arguments": {"workspace_id": "hetzner"}},
            request_id=3,
        )

    assert discover.status_code == tools.status_code == call.status_code == 200, (
        discover.text,
        tools.text,
        call.text,
    )
    assert discover.json()["result"]["supportedVersions"] == ["2026-07-28"]
    assert "Mcp-Session-Id" not in discover.headers
    assert "Mcp-Session-Id" not in tools.headers
    assert "Mcp-Session-Id" not in call.headers
    assert call.json()["result"]["structuredContent"]["data"] == {
        "workspace": "hetzner",
        "command": "stats",
    }


async def test_transport_security_rejects_unlisted_host(app: Starlette) -> None:
    async with (
        app.router.lifespan_context(app),
        client(app, base_url="http://unlisted.test") as http_client,
    ):
        response = await http_client.post(
            "/mcp",
            headers=modern_headers(method="tools/list"),
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {"_meta": modern_meta()},
            },
        )

    assert response.status_code == 421


async def test_transport_security_rejects_unlisted_origin(app: Starlette) -> None:
    headers = modern_headers(method="tools/list")
    headers["Origin"] = "https://evil.test"
    async with app.router.lifespan_context(app), client(app) as http_client:
        response = await http_client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {"_meta": modern_meta()},
            },
        )

    assert response.status_code == 403


def _header_mismatch_error(response: httpx.Response) -> dict[str, object]:
    assert response.status_code == 400
    payload = response.json()
    error = payload["error"]
    assert payload["jsonrpc"] == "2.0"
    assert error["code"] == HEADER_MISMATCH == -32020
    return error


async def test_protocol_version_header_mismatch_is_rejected(app: Starlette) -> None:
    # Handshake-era header values take the legacy path and skip this comparison.
    # A non-handshake header that disagrees with the 2026-07-28 envelope is rejected.
    headers = modern_headers(method="tools/list")
    headers["MCP-Protocol-Version"] = "2026-06-18"
    async with app.router.lifespan_context(app), client(app) as http_client:
        response = await http_client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 11,
                "method": "tools/list",
                "params": {"_meta": modern_meta()},
            },
        )

    error = _header_mismatch_error(response)
    assert "mcp-protocol-version" in error["message"].lower()


async def test_method_header_mismatch_is_rejected(app: Starlette) -> None:
    headers = modern_headers(method="tools/call", name="stats")
    headers["MCP-Method"] = "tools/list"
    async with app.router.lifespan_context(app), client(app) as http_client:
        response = await http_client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 12,
                "method": "tools/call",
                "params": {
                    "name": "stats",
                    "arguments": {"workspace_id": "hetzner"},
                    "_meta": modern_meta(),
                },
            },
        )

    error = _header_mismatch_error(response)
    assert "mcp-method" in error["message"].lower()


async def test_name_header_mismatch_is_rejected(app: Starlette) -> None:
    headers = modern_headers(method="tools/call", name="ready")
    async with app.router.lifespan_context(app), client(app) as http_client:
        response = await http_client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 13,
                "method": "tools/call",
                "params": {
                    "name": "stats",
                    "arguments": {"workspace_id": "hetzner"},
                    "_meta": modern_meta(),
                },
            },
        )

    error = _header_mismatch_error(response)
    assert "mcp-name" in error["message"].lower()
