from pathlib import Path

from mcp.client import Client

from beads_mcp_server.config import ServerConfig, WorkspaceRegistry
from beads_mcp_server.runner import BdResult
from beads_mcp_server.server import TOOL_NAMES, create_server


class SurfaceRunner:
    async def run(self, workspace: Path, command: str, *arguments: str) -> BdResult:
        return BdResult(
            data={"workspace": workspace.name, "command": command, "arguments": list(arguments)},
            elapsed_ms=3,
        )


def make_config() -> ServerConfig:
    return ServerConfig(
        bearer_token="test-token",
        workspaces=WorkspaceRegistry({"hetzner": Path("/srv/beads/hetzner")}),
    )


async def test_sdk_v2_discovers_deterministic_curated_tool_surface() -> None:
    server = create_server(make_config(), runner=SurfaceRunner())

    async with Client(server, raise_exceptions=True) as client:
        result = await client.list_tools()

    assert [tool.name for tool in result.tools] == list(TOOL_NAMES)


async def test_sdk_v2_calls_typed_show_tool() -> None:
    server = create_server(make_config(), runner=SurfaceRunner())

    async with Client(server, raise_exceptions=True) as client:
        result = await client.call_tool(
            "show",
            {"workspace_id": "hetzner", "issue_id": "hetzner-ci8"},
        )

    assert result.is_error is False
    assert result.structured_content == {
        "workspace_id": "hetzner",
        "elapsed_ms": 3,
        "data": {
            "workspace": "hetzner",
            "command": "show",
            "arguments": ["--id=hetzner-ci8"],
        },
    }


async def test_sdk_v2_rejects_missing_workspace_id_at_schema_boundary() -> None:
    server = create_server(make_config(), runner=SurfaceRunner())

    async with Client(server) as client:
        result = await client.call_tool("stats", {})

    assert result.is_error is True
