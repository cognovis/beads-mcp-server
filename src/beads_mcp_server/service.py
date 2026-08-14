"""Curated application service for supported bd operations."""

import re
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from beads_mcp_server.config import WorkspaceRegistry
from beads_mcp_server.runner import BdCommandError, BdResult

type IssueType = Literal["bug", "feature", "task", "epic", "chore", "decision"]
type IssueStatus = Literal["open", "in_progress", "blocked", "deferred", "closed"]
type DependencyOperation = Literal["add", "remove", "list"]

_ISSUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SENSITIVE_VALUE = re.compile(
    r"(?i)\b(token|password|authorization|secret)\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|\S+)"
)
_URI_CREDENTIAL = re.compile(r"(://[^:/\s]+:)[^@\s]+@")
_FORCE_OVERRIDE_HINT = re.compile(
    r";?\s*reclaim or use --force to override\.?",
    re.IGNORECASE,
)
_MAX_ERROR_DETAIL_CHARS = 2048


class Runner(Protocol):
    """Command runner interface consumed by the application service."""

    async def run(
        self,
        workspace: Path,
        command: str,
        *arguments: str,
        actor: str | None = None,
    ) -> BdResult:
        """Run one bd command in the selected workspace."""


class ToolResponse(BaseModel):
    """Stable response envelope returned by every MCP tool."""

    workspace_id: str
    elapsed_ms: int
    data: Any


class WorkspaceListResponse(BaseModel):
    """Public MCP representation of configured workspace identifiers."""

    workspace_ids: list[str]


class ToolExecutionError(RuntimeError):
    """Safe, coded error detail suitable for an MCP tool response."""


class BeadsService:
    """Expose only the bd operations supported by this server."""

    def __init__(self, *, registry: WorkspaceRegistry, runner: Runner) -> None:
        self._registry = registry
        self._runner = runner

    def workspaces(self) -> WorkspaceListResponse:
        """List exact IDs without exposing server filesystem paths."""
        return WorkspaceListResponse(workspace_ids=list(self._registry.ids()))

    async def workspace_status(self, workspace_id: str) -> ToolResponse:
        """Probe one workspace through a read-only bd statistics call."""
        return await self._execute(workspace_id, "stats", "--no-activity")

    async def ready(
        self,
        workspace_id: str,
        *,
        limit: int = 100,
        assignee: str | None = None,
        priority: int | None = None,
        issue_type: IssueType | None = None,
        labels: list[str] | None = None,
    ) -> ToolResponse:
        arguments = ["--limit", str(limit)]
        _option(arguments, "--assignee", assignee)
        _option(arguments, "--priority", priority)
        _option(arguments, "--type", issue_type)
        for label in labels or []:
            _option(arguments, "--label", label)
        return await self._execute(workspace_id, "ready", *arguments)

    async def list_issues(
        self,
        workspace_id: str,
        *,
        status: str | None = None,
        assignee: str | None = None,
        priority: int | None = None,
        issue_type: IssueType | None = None,
        labels: list[str] | None = None,
        parent: str | None = None,
        limit: int = 50,
        include_closed: bool = False,
    ) -> ToolResponse:
        arguments = ["--limit", str(limit)]
        _option(arguments, "--status", status)
        _option(arguments, "--assignee", assignee)
        _option(arguments, "--priority", priority)
        _option(arguments, "--type", issue_type)
        _option(arguments, "--parent", _optional_issue_id(parent))
        for label in labels or []:
            _option(arguments, "--label", label)
        if include_closed:
            arguments.append("--all")
        return await self._execute(workspace_id, "list", *arguments)

    async def show(
        self,
        workspace_id: str,
        issue_id: str,
        *,
        include_comments: bool = False,
        include_dependents: bool = False,
    ) -> ToolResponse:
        arguments = [f"--id={_issue_id(issue_id)}"]
        if include_comments:
            arguments.append("--include-comments")
        if include_dependents:
            arguments.append("--include-dependents")
        return await self._execute(workspace_id, "show", *arguments)

    async def create(
        self,
        workspace_id: str,
        *,
        actor: str,
        title: str,
        issue_type: IssueType = "task",
        priority: int = 2,
        description: str | None = None,
        acceptance: str | None = None,
        assignee: str | None = None,
        labels: list[str] | None = None,
        dependencies: list[str] | None = None,
        parent: str | None = None,
    ) -> ToolResponse:
        arguments = [
            "--title",
            _text(title, "title"),
            "--type",
            issue_type,
            "--priority",
            str(priority),
        ]
        _option(arguments, "--description", description)
        _option(arguments, "--acceptance", acceptance)
        _option(arguments, "--assignee", assignee)
        if labels:
            _option(arguments, "--labels", ",".join(_texts(labels, "labels")))
        if dependencies:
            _option(arguments, "--deps", ",".join(_texts(dependencies, "dependencies")))
        _option(arguments, "--parent", _optional_issue_id(parent))
        return await self._execute(workspace_id, "create", *arguments, actor=_actor(actor))

    async def claim(self, workspace_id: str, issue_id: str, *, actor: str) -> ToolResponse:
        return await self._execute(
            workspace_id,
            "update",
            _issue_id(issue_id),
            "--claim",
            actor=_actor(actor),
        )

    async def update(
        self,
        workspace_id: str,
        issue_id: str,
        *,
        actor: str,
        title: str | None = None,
        status: IssueStatus | None = None,
        priority: int | None = None,
        issue_type: IssueType | None = None,
        assignee: str | None = None,
        if_assignee: str | None = None,
        description: str | None = None,
        acceptance: str | None = None,
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
    ) -> ToolResponse:
        arguments = [_issue_id(issue_id)]
        _option(arguments, "--title", title)
        _option(arguments, "--status", status)
        _option(arguments, "--priority", priority)
        _option(arguments, "--type", issue_type)
        _option(arguments, "--assignee", assignee)
        if assignee is not None and if_assignee is None:
            raise ValueError("assignee update requires if_assignee")
        _option(arguments, "--if-assignee", if_assignee)
        _option(arguments, "--description", description)
        _option(arguments, "--acceptance", acceptance)
        for label in add_labels or []:
            _option(arguments, "--add-label", label)
        for label in remove_labels or []:
            _option(arguments, "--remove-label", label)
        if len(arguments) == 1:
            raise ValueError("update requires at least one field")
        return await self._execute(workspace_id, "update", *arguments, actor=_actor(actor))

    async def close(
        self,
        workspace_id: str,
        issue_id: str,
        *,
        reason: str,
        actor: str,
    ) -> ToolResponse:
        return await self._execute(
            workspace_id,
            "close",
            _issue_id(issue_id),
            "--reason",
            _text(reason, "reason"),
            actor=_actor(actor),
        )

    async def reopen(
        self,
        workspace_id: str,
        issue_id: str,
        *,
        actor: str,
        reason: str | None = None,
    ) -> ToolResponse:
        arguments = [_issue_id(issue_id)]
        _option(arguments, "--reason", reason)
        return await self._execute(workspace_id, "reopen", *arguments, actor=_actor(actor))

    async def dependency(
        self,
        workspace_id: str,
        operation: DependencyOperation,
        issue_id: str,
        depends_on_id: str | None = None,
        *,
        actor: str | None = None,
    ) -> ToolResponse:
        issue_id = _issue_id(issue_id)
        if operation == "list":
            if depends_on_id is not None:
                raise ValueError("depends_on_id is not valid for the list operation")
            return await self._execute(workspace_id, "dep", "list", issue_id)
        mutation_actor = _actor(actor) if actor is not None else None
        if mutation_actor is None:
            raise ValueError(f"actor is required for the {operation} operation")
        if depends_on_id is None:
            raise ValueError(f"depends_on_id is required for the {operation} operation")
        return await self._execute(
            workspace_id,
            "dep",
            operation,
            issue_id,
            _issue_id(depends_on_id),
            actor=mutation_actor,
        )

    async def comment(
        self, workspace_id: str, issue_id: str, text: str, *, actor: str
    ) -> ToolResponse:
        return await self._execute(
            workspace_id,
            "comment",
            _issue_id(issue_id),
            "--",
            _text(text, "text"),
            actor=_actor(actor),
        )

    async def comments(self, workspace_id: str, issue_id: str) -> ToolResponse:
        return await self._execute(workspace_id, "comments", _issue_id(issue_id))

    async def note(
        self, workspace_id: str, issue_id: str, text: str, *, actor: str
    ) -> ToolResponse:
        return await self._execute(
            workspace_id,
            "update",
            _issue_id(issue_id),
            "--append-notes",
            _text(text, "text"),
            actor=_actor(actor),
        )

    async def heartbeat(self, workspace_id: str, issue_id: str, *, actor: str) -> ToolResponse:
        return await self._execute(
            workspace_id,
            "heartbeat",
            _issue_id(issue_id),
            actor=_actor(actor),
        )

    async def unclaim(
        self,
        workspace_id: str,
        issue_id: str,
        *,
        actor: str,
        reason: str | None = None,
        if_assignee: str | None = None,
    ) -> ToolResponse:
        arguments = [_issue_id(issue_id)]
        _option(arguments, "--reason", reason)
        _option(arguments, "--if-assignee", if_assignee)
        return await self._execute(
            workspace_id,
            "unclaim",
            *arguments,
            actor=_actor(actor),
        )

    async def reclaim(
        self,
        workspace_id: str,
        *,
        actor: str,
        issue_ids: list[str] | None = None,
        assignees: list[str] | None = None,
        older_than: str = "10m",
    ) -> ToolResponse:
        arguments = ["--older-than", _duration(older_than)]
        for issue_id in issue_ids or []:
            _option(arguments, "--id", _issue_id(issue_id))
        for assignee in assignees or []:
            _option(arguments, "--assignee", _actor(assignee))
        return await self._execute(
            workspace_id,
            "reclaim",
            *arguments,
            actor=_actor(actor),
        )

    async def stats(self, workspace_id: str) -> ToolResponse:
        return await self._execute(workspace_id, "stats")

    async def blocked(self, workspace_id: str, parent: str | None = None) -> ToolResponse:
        arguments: list[str] = []
        _option(arguments, "--parent", _optional_issue_id(parent))
        return await self._execute(workspace_id, "blocked", *arguments)

    async def _execute(
        self,
        workspace_id: str,
        command: str,
        *arguments: str,
        actor: str | None = None,
    ) -> ToolResponse:
        workspace = self._registry.resolve(workspace_id)
        try:
            result = await self._runner.run(workspace, command, *arguments, actor=actor)
        except BdCommandError as error:
            raise ToolExecutionError(_tool_error_message(command, error)) from error
        return ToolResponse(
            workspace_id=workspace_id,
            elapsed_ms=result.elapsed_ms,
            data=result.data,
        )


def _issue_id(value: str) -> str:
    if not _ISSUE_ID.fullmatch(value):
        raise ValueError(f"Invalid issue_id: {value!r}")
    return value


def _optional_issue_id(value: str | None) -> str | None:
    return _issue_id(value) if value is not None else None


def _text(value: str, name: str) -> str:
    if not value.strip() or "\x00" in value:
        raise ValueError(f"{name} must be non-empty and contain no null bytes")
    return value


def _actor(value: str) -> str:
    if len(value) > 128 or value != value.strip() or not value or "\x00" in value:
        raise ValueError("actor must be 1-128 characters without surrounding whitespace")
    if "\r" in value or "\n" in value:
        raise ValueError("actor must not contain line breaks")
    return value


def _duration(value: str) -> str:
    if not re.fullmatch(r"[0-9][0-9A-Za-z.]{0,31}", value):
        raise ValueError("older_than must be a bounded duration such as 10m or 1h")
    return value


def _tool_error_message(command: str, error: BdCommandError) -> str:
    detail = _SENSITIVE_VALUE.sub(lambda match: f"{match.group(1)}=[REDACTED]", error.stderr)
    detail = _URI_CREDENTIAL.sub(r"\1[REDACTED]@", detail)
    detail = _FORCE_OVERRIDE_HINT.sub("", detail).strip()
    detail = detail[:_MAX_ERROR_DETAIL_CHARS]
    normalized = detail.lower()
    if "assignee is" in normalized and "actor is" in normalized:
        return (
            "BD_ASSIGNEE_MISMATCH: "
            f"bd {command} rejected the request actor because it does not own the claim. "
            f"{detail} Use the claim actor, a compare-and-swap assignee update, or explicit "
            "reclaim after lease expiry; no automatic reassignment was attempted."
        )
    if detail:
        return f"BD_COMMAND_FAILED: {error}. Detail: {detail}"
    return f"BD_COMMAND_FAILED: {error}"


def _texts(values: list[str], name: str) -> list[str]:
    return [_text(value, name) for value in values]


def _option(arguments: list[str], flag: str, value: object | None) -> None:
    if value is not None:
        arguments.extend((flag, str(value)))
