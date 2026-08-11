"""Curated application service for supported bd operations."""

import re
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from beads_mcp_server.config import WorkspaceRegistry
from beads_mcp_server.runner import BdResult

type IssueType = Literal["bug", "feature", "task", "epic", "chore", "decision"]
type IssueStatus = Literal["open", "in_progress", "blocked", "deferred", "closed"]
type DependencyOperation = Literal["add", "remove", "list"]

_ISSUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class Runner(Protocol):
    """Command runner interface consumed by the application service."""

    async def run(self, workspace: Path, command: str, *arguments: str) -> BdResult:
        """Run one bd command in the selected workspace."""


class ToolResponse(BaseModel):
    """Stable response envelope returned by every MCP tool."""

    workspace_id: str
    elapsed_ms: int
    data: Any


class WorkspaceListResponse(BaseModel):
    """Public MCP representation of configured workspace identifiers."""

    workspace_ids: list[str]


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
        return await self._execute(workspace_id, "create", *arguments)

    async def claim(self, workspace_id: str, issue_id: str) -> ToolResponse:
        return await self._execute(workspace_id, "update", _issue_id(issue_id), "--claim")

    async def update(
        self,
        workspace_id: str,
        issue_id: str,
        *,
        title: str | None = None,
        status: IssueStatus | None = None,
        priority: int | None = None,
        issue_type: IssueType | None = None,
        assignee: str | None = None,
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
        _option(arguments, "--description", description)
        _option(arguments, "--acceptance", acceptance)
        for label in add_labels or []:
            _option(arguments, "--add-label", label)
        for label in remove_labels or []:
            _option(arguments, "--remove-label", label)
        if len(arguments) == 1:
            raise ValueError("update requires at least one field")
        return await self._execute(workspace_id, "update", *arguments)

    async def close(self, workspace_id: str, issue_id: str, *, reason: str) -> ToolResponse:
        return await self._execute(
            workspace_id,
            "close",
            _issue_id(issue_id),
            "--reason",
            _text(reason, "reason"),
        )

    async def reopen(
        self, workspace_id: str, issue_id: str, *, reason: str | None = None
    ) -> ToolResponse:
        arguments = [_issue_id(issue_id)]
        _option(arguments, "--reason", reason)
        return await self._execute(workspace_id, "reopen", *arguments)

    async def dependency(
        self,
        workspace_id: str,
        operation: DependencyOperation,
        issue_id: str,
        depends_on_id: str | None = None,
    ) -> ToolResponse:
        issue_id = _issue_id(issue_id)
        if operation == "list":
            if depends_on_id is not None:
                raise ValueError("depends_on_id is not valid for the list operation")
            return await self._execute(workspace_id, "dep", "list", issue_id)
        if depends_on_id is None:
            raise ValueError(f"depends_on_id is required for the {operation} operation")
        return await self._execute(
            workspace_id,
            "dep",
            operation,
            issue_id,
            _issue_id(depends_on_id),
        )

    async def comment(self, workspace_id: str, issue_id: str, text: str) -> ToolResponse:
        return await self._execute(
            workspace_id,
            "comment",
            _issue_id(issue_id),
            "--",
            _text(text, "text"),
        )

    async def comments(self, workspace_id: str, issue_id: str) -> ToolResponse:
        return await self._execute(workspace_id, "comments", _issue_id(issue_id))

    async def note(self, workspace_id: str, issue_id: str, text: str) -> ToolResponse:
        return await self._execute(
            workspace_id,
            "update",
            _issue_id(issue_id),
            "--append-notes",
            _text(text, "text"),
        )

    async def stats(self, workspace_id: str) -> ToolResponse:
        return await self._execute(workspace_id, "stats")

    async def blocked(self, workspace_id: str, parent: str | None = None) -> ToolResponse:
        arguments: list[str] = []
        _option(arguments, "--parent", _optional_issue_id(parent))
        return await self._execute(workspace_id, "blocked", *arguments)

    async def _execute(self, workspace_id: str, command: str, *arguments: str) -> ToolResponse:
        workspace = self._registry.resolve(workspace_id)
        result = await self._runner.run(workspace, command, *arguments)
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


def _texts(values: list[str], name: str) -> list[str]:
    return [_text(value, name) for value in values]


def _option(arguments: list[str], flag: str, value: object | None) -> None:
    if value is not None:
        arguments.extend((flag, str(value)))
