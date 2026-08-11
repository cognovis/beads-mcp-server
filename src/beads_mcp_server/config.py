"""Fail-closed configuration and workspace resolution."""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """Raised when server configuration is missing or unsafe."""


_WORKSPACE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@dataclass(frozen=True, slots=True)
class WorkspaceRegistry:
    """Resolve stable workspace identifiers to explicitly configured paths."""

    workspaces: Mapping[str, Path]

    def __post_init__(self) -> None:
        if not self.workspaces:
            raise ConfigError("The workspace registry must not be empty")
        for workspace_id, path in self.workspaces.items():
            if not _WORKSPACE_ID.fullmatch(workspace_id):
                raise ConfigError(f"Invalid workspace identifier: {workspace_id!r}")
            if not path.is_absolute():
                raise ConfigError(f"Workspace path must be absolute: {path}")

    def resolve(self, workspace_id: str) -> Path:
        """Return the configured path for a workspace identifier."""
        try:
            return self.workspaces[workspace_id]
        except KeyError as error:
            raise ConfigError(f"Unknown workspace_id: {workspace_id!r}") from error

    @classmethod
    def from_json(cls, raw_value: str) -> WorkspaceRegistry:
        """Parse a JSON object containing workspace-to-path mappings."""
        try:
            parsed: Any = json.loads(raw_value)
        except json.JSONDecodeError as error:
            raise ConfigError("BEADS_WORKSPACES_JSON must contain valid JSON") from error
        if not isinstance(parsed, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in parsed.items()
        ):
            raise ConfigError("BEADS_WORKSPACES_JSON must be a JSON object of string paths")
        return cls({key: Path(value) for key, value in parsed.items()})


@dataclass(frozen=True, slots=True, kw_only=True)
class ServerConfig:
    """Runtime configuration for the HTTP MCP service."""

    bearer_token: str
    workspaces: WorkspaceRegistry
    bd_path: Path = Path("/usr/bin/bd")
    host: str = "127.0.0.1"
    port: int = 8092
    mcp_path: str = "/mcp"
    timeout_seconds: float = 30.0
    max_output_bytes: int = 4 * 1024 * 1024
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost")
    allowed_origins: tuple[str, ...] = ()
    public_url: str = "https://beads-mcp.invalid/mcp"

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> ServerConfig:
        """Load configuration from an explicit environment mapping."""
        token = environment.get("BEADS_MCP_TOKEN", "")
        if not token.strip():
            raise ConfigError("BEADS_MCP_TOKEN is required and must not be empty")
        workspace_json = environment.get("BEADS_WORKSPACES_JSON", "")
        if not workspace_json.strip():
            raise ConfigError("BEADS_WORKSPACES_JSON is required and must not be empty")
        public_url = environment.get("MCP_PUBLIC_URL", "")
        if not public_url.startswith("https://"):
            raise ConfigError("MCP_PUBLIC_URL is required and must use https://")

        allowed_hosts = _csv(environment.get("MCP_ALLOWED_HOSTS", ""))
        if not allowed_hosts:
            raise ConfigError("MCP_ALLOWED_HOSTS is required and must not be empty")

        return cls(
            bearer_token=token,
            workspaces=WorkspaceRegistry.from_json(workspace_json),
            bd_path=Path(environment.get("BEADS_PATH", "/usr/bin/bd")),
            host=environment.get("HOST", "127.0.0.1"),
            port=_positive_int(environment.get("PORT", "8092"), "PORT"),
            mcp_path=_absolute_url_path(environment.get("MCP_PATH", "/mcp")),
            timeout_seconds=_positive_float(
                environment.get("BEADS_COMMAND_TIMEOUT_SECONDS", "30"),
                "BEADS_COMMAND_TIMEOUT_SECONDS",
            ),
            max_output_bytes=_positive_int(
                environment.get("BEADS_MAX_OUTPUT_BYTES", str(4 * 1024 * 1024)),
                "BEADS_MAX_OUTPUT_BYTES",
            ),
            allowed_hosts=allowed_hosts,
            allowed_origins=_csv(environment.get("MCP_ALLOWED_ORIGINS", "")),
            public_url=public_url,
        )


def _positive_int(raw_value: str, name: str) -> int:
    try:
        value = int(raw_value)
    except ValueError as error:
        raise ConfigError(f"{name} must be an integer") from error
    if value <= 0:
        raise ConfigError(f"{name} must be positive")
    return value


def _positive_float(raw_value: str, name: str) -> float:
    try:
        value = float(raw_value)
    except ValueError as error:
        raise ConfigError(f"{name} must be a number") from error
    if value <= 0:
        raise ConfigError(f"{name} must be positive")
    return value


def _absolute_url_path(value: str) -> str:
    if not value.startswith("/") or value.startswith("//"):
        raise ConfigError("MCP_PATH must be an absolute URL path")
    return value.rstrip("/") or "/"


def _csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())
