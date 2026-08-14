"""Official MCP SDK v2 server and curated tool surface."""

import logging
from typing import Annotated

from mcp.server import CacheHint, MCPServer
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl, Field
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse

from beads_mcp_server import __version__
from beads_mcp_server.auth import StaticTokenVerifier
from beads_mcp_server.config import ServerConfig
from beads_mcp_server.runner import BdRunner
from beads_mcp_server.service import (
    BeadsService,
    DependencyOperation,
    IssueStatus,
    IssueType,
    Runner,
    ToolResponse,
    WorkspaceListResponse,
)

logger = logging.getLogger(__name__)

type Priority = Annotated[int, Field(ge=0, le=4)]
type ResultLimit = Annotated[int, Field(ge=1, le=1000)]
type Actor = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^\S(?:[^\r\n\x00]*\S)?$"),
]
type ReclaimDuration = Annotated[
    str,
    Field(min_length=2, max_length=32, pattern=r"^[0-9][0-9A-Za-z.]*$"),
]

TOOL_NAMES = (
    "workspaces",
    "workspace_status",
    "ready",
    "list",
    "show",
    "create",
    "claim",
    "update",
    "close",
    "reopen",
    "dep",
    "comment",
    "comments",
    "note",
    "heartbeat",
    "unclaim",
    "reclaim",
    "stats",
    "blocked",
)


def create_server(config: ServerConfig, *, runner: Runner | None = None) -> MCPServer:
    """Build the MCP server with explicit dependencies and no session state."""
    command_runner = runner or BdRunner(
        bd_path=config.bd_path,
        timeout_seconds=config.timeout_seconds,
        max_output_bytes=config.max_output_bytes,
    )

    service = BeadsService(registry=config.workspaces, runner=command_runner)
    server = MCPServer(
        "beads-mcp-server",
        version=__version__,
        description="Stateless typed adapter for bd workspaces",
        token_verifier=StaticTokenVerifier(config.bearer_token),
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(config.public_url),
            resource_server_url=AnyHttpUrl(config.public_url),
            required_scopes=["beads:read", "beads:write"],
        ),
        cache_hints={
            "server/discover": CacheHint(ttl_ms=60_000, scope="private"),
            "tools/list": CacheHint(ttl_ms=60_000, scope="private"),
        },
    )

    @server.tool(name="workspaces")
    async def workspaces_tool() -> WorkspaceListResponse:
        """List exact configured repository workspace identifiers."""
        return service.workspaces()

    @server.tool(name="workspace_status")
    async def workspace_status_tool(workspace_id: str) -> ToolResponse:
        """Probe one exact workspace through read-only bd statistics."""
        return await service.workspace_status(workspace_id)

    @server.tool(name="ready")
    async def ready_tool(
        workspace_id: str,
        limit: ResultLimit = 100,
        assignee: str | None = None,
        priority: Priority | None = None,
        issue_type: IssueType | None = None,
        labels: list[str] | None = None,
    ) -> ToolResponse:
        """Return open work with no active blockers in one workspace."""
        return await service.ready(
            workspace_id,
            limit=limit,
            assignee=assignee,
            priority=priority,
            issue_type=issue_type,
            labels=labels,
        )

    @server.tool(name="list")
    async def list_tool(
        workspace_id: str,
        status: str | None = None,
        assignee: str | None = None,
        priority: Priority | None = None,
        issue_type: IssueType | None = None,
        labels: list[str] | None = None,
        parent: str | None = None,
        limit: ResultLimit = 50,
        include_closed: bool = False,
    ) -> ToolResponse:
        """List issues using curated filters in one workspace."""
        return await service.list_issues(
            workspace_id,
            status=status,
            assignee=assignee,
            priority=priority,
            issue_type=issue_type,
            labels=labels,
            parent=parent,
            limit=limit,
            include_closed=include_closed,
        )

    @server.tool(name="show")
    async def show_tool(
        workspace_id: str,
        issue_id: str,
        include_comments: bool = False,
        include_dependents: bool = False,
    ) -> ToolResponse:
        """Show one issue without relying on prior workspace context."""
        return await service.show(
            workspace_id,
            issue_id,
            include_comments=include_comments,
            include_dependents=include_dependents,
        )

    @server.tool(name="create")
    async def create_tool(
        workspace_id: str,
        title: str,
        actor: Actor,
        issue_type: IssueType = "task",
        priority: Priority = 2,
        description: str | None = None,
        acceptance: str | None = None,
        assignee: str | None = None,
        labels: list[str] | None = None,
        dependencies: list[str] | None = None,
        parent: str | None = None,
    ) -> ToolResponse:
        """Create an issue through the supported bd fields."""
        return await service.create(
            workspace_id,
            actor=actor,
            title=title,
            issue_type=issue_type,
            priority=priority,
            description=description,
            acceptance=acceptance,
            assignee=assignee,
            labels=labels,
            dependencies=dependencies,
            parent=parent,
        )

    @server.tool(name="claim")
    async def claim_tool(workspace_id: str, issue_id: str, actor: Actor) -> ToolResponse:
        """Atomically claim an issue for the explicit request actor."""
        return await service.claim(workspace_id, issue_id, actor=actor)

    @server.tool(name="update")
    async def update_tool(
        workspace_id: str,
        issue_id: str,
        actor: Actor,
        title: str | None = None,
        status: IssueStatus | None = None,
        priority: Priority | None = None,
        issue_type: IssueType | None = None,
        assignee: str | None = None,
        if_assignee: str | None = None,
        description: str | None = None,
        acceptance: str | None = None,
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
    ) -> ToolResponse:
        """Update explicitly supported fields on one issue."""
        return await service.update(
            workspace_id,
            issue_id,
            actor=actor,
            title=title,
            status=status,
            priority=priority,
            issue_type=issue_type,
            assignee=assignee,
            if_assignee=if_assignee,
            description=description,
            acceptance=acceptance,
            add_labels=add_labels,
            remove_labels=remove_labels,
        )

    @server.tool(name="close")
    async def close_tool(
        workspace_id: str,
        issue_id: str,
        reason: str,
        actor: Actor,
    ) -> ToolResponse:
        """Close one verified issue with an explicit reason."""
        return await service.close(workspace_id, issue_id, reason=reason, actor=actor)

    @server.tool(name="reopen")
    async def reopen_tool(
        workspace_id: str,
        issue_id: str,
        actor: Actor,
        reason: str | None = None,
    ) -> ToolResponse:
        """Reopen one closed issue."""
        return await service.reopen(workspace_id, issue_id, actor=actor, reason=reason)

    @server.tool(name="dep")
    async def dependency_tool(
        workspace_id: str,
        operation: DependencyOperation,
        issue_id: str,
        depends_on_id: str | None = None,
        actor: Actor | None = None,
    ) -> ToolResponse:
        """Add, remove, or list dependencies for one issue."""
        return await service.dependency(
            workspace_id,
            operation,
            issue_id,
            depends_on_id,
            actor=actor,
        )

    @server.tool(name="comment")
    async def comment_tool(
        workspace_id: str, issue_id: str, text: str, actor: Actor
    ) -> ToolResponse:
        """Add a comment to one issue."""
        return await service.comment(workspace_id, issue_id, text, actor=actor)

    @server.tool(name="comments")
    async def comments_tool(workspace_id: str, issue_id: str) -> ToolResponse:
        """List comments for one issue."""
        return await service.comments(workspace_id, issue_id)

    @server.tool(name="note")
    async def note_tool(workspace_id: str, issue_id: str, text: str, actor: Actor) -> ToolResponse:
        """Append an audit note to one issue."""
        return await service.note(workspace_id, issue_id, text, actor=actor)

    @server.tool(name="heartbeat")
    async def heartbeat_tool(workspace_id: str, issue_id: str, actor: Actor) -> ToolResponse:
        """Refresh the request actor's claim lease."""
        return await service.heartbeat(workspace_id, issue_id, actor=actor)

    @server.tool(name="unclaim")
    async def unclaim_tool(
        workspace_id: str,
        issue_id: str,
        actor: Actor,
        reason: str | None = None,
        if_assignee: str | None = None,
    ) -> ToolResponse:
        """Explicitly release the request actor's claim without force."""
        return await service.unclaim(
            workspace_id,
            issue_id,
            actor=actor,
            reason=reason,
            if_assignee=if_assignee,
        )

    @server.tool(name="reclaim")
    async def reclaim_tool(
        workspace_id: str,
        actor: Actor,
        issue_ids: list[str] | None = None,
        assignees: list[str] | None = None,
        older_than: ReclaimDuration = "10m",
    ) -> ToolResponse:
        """Explicitly reap stale local-replica claims without force."""
        return await service.reclaim(
            workspace_id,
            actor=actor,
            issue_ids=issue_ids,
            assignees=assignees,
            older_than=older_than,
        )

    @server.tool(name="stats")
    async def stats_tool(workspace_id: str) -> ToolResponse:
        """Return issue database statistics for one workspace."""
        return await service.stats(workspace_id)

    @server.tool(name="blocked")
    async def blocked_tool(workspace_id: str, parent: str | None = None) -> ToolResponse:
        """Return blocked issues in one workspace."""
        return await service.blocked(workspace_id, parent)

    @server.custom_route("/health", methods=["GET"], include_in_schema=False)
    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "beads-mcp-server", "version": __version__})

    @server.custom_route("/ready", methods=["GET"], include_in_schema=False)
    async def readiness(_: Request) -> JSONResponse:
        workspace_id = config.readiness_workspace_id
        if workspace_id is None:
            return JSONResponse(
                {"status": "not_ready", "service": "beads-mcp-server"},
                status_code=503,
            )
        try:
            await service.workspace_status(workspace_id)
        except Exception:
            logger.exception("Readiness workspace probe failed")
            return JSONResponse(
                {"status": "not_ready", "service": "beads-mcp-server"},
                status_code=503,
            )
        return JSONResponse(
            {
                "status": "ready",
                "service": "beads-mcp-server",
                "workspace_count": len(config.workspaces.ids()),
            }
        )

    return server


def create_http_app(config: ServerConfig, *, runner: Runner | None = None) -> Starlette:
    """Build the authenticated Streamable HTTP application."""
    server = create_server(config, runner=runner)
    return server.streamable_http_app(
        streamable_http_path=config.mcp_path,
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=list(config.allowed_hosts),
            allowed_origins=list(config.allowed_origins),
        ),
        host=config.host,
    )
