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


def make_config(tmp_path: Path) -> ServerConfig:
    metadata = tmp_path / "hetzner" / ".beads" / "metadata.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text("{}")
    return ServerConfig(
        bearer_token="test-token",
        workspaces=WorkspaceRegistry.from_root(tmp_path),
    )


async def test_sdk_v2_discovers_deterministic_curated_tool_surface(tmp_path: Path) -> None:
    server = create_server(make_config(tmp_path), runner=SurfaceRunner())

    async with Client(server, raise_exceptions=True) as client:
        result = await client.list_tools()

    assert [tool.name for tool in result.tools] == list(TOOL_NAMES)


async def test_sdk_v2_calls_typed_show_tool(tmp_path: Path) -> None:
    server = create_server(make_config(tmp_path), runner=SurfaceRunner())

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


async def test_sdk_v2_rejects_missing_workspace_id_at_schema_boundary(
    tmp_path: Path,
) -> None:
    server = create_server(make_config(tmp_path), runner=SurfaceRunner())

    async with Client(server) as client:
        result = await client.call_tool("stats", {})

    assert result.is_error is True


async def test_sdk_v2_lists_exact_workspace_ids_without_server_paths(tmp_path: Path) -> None:
    server = create_server(make_config(tmp_path), runner=SurfaceRunner())

    async with Client(server, raise_exceptions=True) as client:
        result = await client.call_tool("workspaces", {})

    assert result.is_error is False
    assert result.structured_content == {"workspace_ids": ["hetzner"]}
    assert str(tmp_path) not in str(result.structured_content)
