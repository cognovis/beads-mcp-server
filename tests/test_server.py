from pathlib import Path

from mcp.client import Client

from beads_mcp_server.config import ServerConfig, WorkspaceRegistry
from beads_mcp_server.runner import BdCommandError, BdResult
from beads_mcp_server.server import TOOL_NAMES, create_server


class SurfaceRunner:
    async def run(
        self,
        workspace: Path,
        command: str,
        *arguments: str,
        actor: str | None = None,
    ) -> BdResult:
        return BdResult(
            data={
                "workspace": workspace.name,
                "command": command,
                "arguments": list(arguments),
                "actor": actor,
            },
            elapsed_ms=3,
        )


class AssigneeMismatchRunner:
    async def run(
        self,
        workspace: Path,
        command: str,
        *arguments: str,
        actor: str | None = None,
    ) -> BdResult:
        raise BdCommandError(
            "bd close exited with status 1",
            stderr=(
                'cannot close hetzner-ci8: assignee is "agent-a", actor is "agent-b"; '
                "reclaim or use --force to override\n"
                "token=must-not-leak https://user:url-secret@dolt.invalid"
            ),
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
            "actor": None,
        },
    }


async def test_regression_claim_preserves_explicit_actor(tmp_path: Path) -> None:
    """Guard against running a claim as the MCP service identity."""
    server = create_server(make_config(tmp_path), runner=SurfaceRunner())

    async with Client(server) as client:
        result = await client.call_tool(
            "claim",
            {
                "workspace_id": "hetzner",
                "issue_id": "hetzner-ci8",
                "actor": "codex-clc-trhu",
            },
        )

    assert result.is_error is False
    assert result.structured_content == {
        "workspace_id": "hetzner",
        "elapsed_ms": 3,
        "data": {
            "workspace": "hetzner",
            "command": "update",
            "arguments": ["hetzner-ci8", "--claim"],
            "actor": "codex-clc-trhu",
        },
    }


async def test_regression_close_preserves_claim_actor(tmp_path: Path) -> None:
    """Guard against closing an agent claim as the MCP service identity."""
    server = create_server(make_config(tmp_path), runner=SurfaceRunner())

    async with Client(server) as client:
        result = await client.call_tool(
            "close",
            {
                "workspace_id": "hetzner",
                "issue_id": "hetzner-ci8",
                "reason": "Verified implementation",
                "actor": "codex-clc-trhu",
            },
        )

    assert result.is_error is False
    assert result.structured_content == {
        "workspace_id": "hetzner",
        "elapsed_ms": 3,
        "data": {
            "workspace": "hetzner",
            "command": "close",
            "arguments": ["hetzner-ci8", "--reason", "Verified implementation"],
            "actor": "codex-clc-trhu",
        },
    }


async def test_all_mutating_tools_preserve_request_actor(tmp_path: Path) -> None:
    server = create_server(make_config(tmp_path), runner=SurfaceRunner())
    calls = [
        ("create", {"workspace_id": "hetzner", "title": "Actor smoke"}),
        (
            "update",
            {
                "workspace_id": "hetzner",
                "issue_id": "hetzner-ci8",
                "title": "Updated title",
            },
        ),
        (
            "reopen",
            {"workspace_id": "hetzner", "issue_id": "hetzner-ci8", "reason": "Retry"},
        ),
        (
            "dep",
            {
                "workspace_id": "hetzner",
                "operation": "add",
                "issue_id": "hetzner-ci8",
                "depends_on_id": "hetzner-base",
            },
        ),
        (
            "comment",
            {"workspace_id": "hetzner", "issue_id": "hetzner-ci8", "text": "Evidence"},
        ),
        (
            "note",
            {"workspace_id": "hetzner", "issue_id": "hetzner-ci8", "text": "Audit"},
        ),
        ("heartbeat", {"workspace_id": "hetzner", "issue_id": "hetzner-ci8"}),
        (
            "unclaim",
            {"workspace_id": "hetzner", "issue_id": "hetzner-ci8", "reason": "Pause"},
        ),
    ]

    async with Client(server) as client:
        results = [
            await client.call_tool(name, {**arguments, "actor": "codex-clc-trhu"})
            for name, arguments in calls
        ]

    assert all(result.is_error is False for result in results)
    assert [result.structured_content["data"]["actor"] for result in results] == [
        "codex-clc-trhu"
    ] * len(calls)


async def test_assignee_change_is_compare_and_swap_guarded(tmp_path: Path) -> None:
    server = create_server(make_config(tmp_path), runner=SurfaceRunner())

    async with Client(server) as client:
        result = await client.call_tool(
            "update",
            {
                "workspace_id": "hetzner",
                "issue_id": "hetzner-ci8",
                "actor": "supervisor",
                "assignee": "agent-b",
                "if_assignee": "agent-a",
            },
        )

    assert result.is_error is False
    assert result.structured_content["data"] == {
        "workspace": "hetzner",
        "command": "update",
        "arguments": [
            "hetzner-ci8",
            "--assignee",
            "agent-b",
            "--if-assignee",
            "agent-a",
        ],
        "actor": "supervisor",
    }


async def test_reclaim_is_explicit_and_never_forces(tmp_path: Path) -> None:
    server = create_server(make_config(tmp_path), runner=SurfaceRunner())

    async with Client(server) as client:
        result = await client.call_tool(
            "reclaim",
            {
                "workspace_id": "hetzner",
                "actor": "supervisor",
                "issue_ids": ["hetzner-ci8"],
                "assignees": ["agent-a"],
                "older_than": "10m",
            },
        )

    assert result.is_error is False
    data = result.structured_content["data"]
    assert data == {
        "workspace": "hetzner",
        "command": "reclaim",
        "arguments": [
            "--older-than",
            "10m",
            "--id",
            "hetzner-ci8",
            "--assignee",
            "agent-a",
        ],
        "actor": "supervisor",
    }
    assert "--force" not in data["arguments"]
    assert "--any-replica" not in data["arguments"]


async def test_actor_mismatch_returns_actionable_redacted_tool_error(tmp_path: Path) -> None:
    server = create_server(make_config(tmp_path), runner=AssigneeMismatchRunner())

    async with Client(server) as client:
        result = await client.call_tool(
            "close",
            {
                "workspace_id": "hetzner",
                "issue_id": "hetzner-ci8",
                "reason": "Verified implementation",
                "actor": "agent-b",
            },
        )

    assert result.is_error is True
    detail = result.content[0].text
    assert "BD_ASSIGNEE_MISMATCH" in detail
    assert 'assignee is "agent-a", actor is "agent-b"' in detail
    assert "reclaim" in detail
    assert "must-not-leak" not in detail
    assert "url-secret" not in detail


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
