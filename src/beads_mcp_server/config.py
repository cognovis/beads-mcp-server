"""Fail-closed configuration and workspace resolution."""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """Raised when server configuration is missing or unsafe."""


class WorkspaceUnavailableError(ConfigError):
    """Raised when a registered workspace no longer exists or is incomplete."""


_WORKSPACE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


@dataclass(frozen=True, slots=True)
class WorkspaceRegistry:
    """Resolve stable workspace identifiers to explicitly configured paths."""

    workspaces: Mapping[str, Path]
    validate_availability: bool = False

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
            path = self.workspaces[workspace_id]
        except KeyError as error:
            raise ConfigError(f"Unknown workspace_id: {workspace_id!r}") from error
        if self.validate_availability and not _is_beads_workspace(path):
            raise WorkspaceUnavailableError(
                f"Registered workspace_id {workspace_id!r} is unavailable"
            )
        return path

    def ids(self) -> tuple[str, ...]:
        """Return deterministic exact workspace identifiers."""
        return tuple(sorted(self.workspaces))

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
        return cls(
            {key: Path(value) for key, value in parsed.items()},
            validate_availability=True,
        )

    @classmethod
    def from_root(cls, root: Path) -> WorkspaceRegistry:
        """Discover exact IDs from immediate operator-controlled workspace children."""
        if not root.is_absolute():
            raise ConfigError("BEADS_WORKSPACE_ROOT must be absolute")
        if not root.is_dir():
            raise ConfigError(f"BEADS_WORKSPACE_ROOT does not exist: {root}")
        workspaces = {
            child.name: child
            for child in root.iterdir()
            if child.is_dir() and _is_beads_workspace(child)
        }
        return cls(workspaces, validate_availability=True)


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
    readiness_workspace_id: str | None = None

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> ServerConfig:
        """Load configuration from an explicit environment mapping."""
        token = environment.get("BEADS_MCP_TOKEN", "")
        if not token.strip():
            raise ConfigError("BEADS_MCP_TOKEN is required and must not be empty")
        workspace_json = environment.get("BEADS_WORKSPACES_JSON", "").strip()
        workspace_root = environment.get("BEADS_WORKSPACE_ROOT", "").strip()
        if bool(workspace_json) == bool(workspace_root):
            raise ConfigError(
                "Exactly one workspace registry source is required: "
                "BEADS_WORKSPACES_JSON or BEADS_WORKSPACE_ROOT"
            )
        workspaces = (
            WorkspaceRegistry.from_json(workspace_json)
            if workspace_json
            else WorkspaceRegistry.from_root(Path(workspace_root))
        )
        readiness_workspace_id = environment.get("BEADS_READINESS_WORKSPACE_ID", "").strip()
        if not readiness_workspace_id:
            raise ConfigError("BEADS_READINESS_WORKSPACE_ID is required")
        workspaces.resolve(readiness_workspace_id)
        public_url = environment.get("MCP_PUBLIC_URL", "")
        if not public_url.startswith("https://"):
            raise ConfigError("MCP_PUBLIC_URL is required and must use https://")

        allowed_hosts = _csv(environment.get("MCP_ALLOWED_HOSTS", ""))
        if not allowed_hosts:
            raise ConfigError("MCP_ALLOWED_HOSTS is required and must not be empty")

        return cls(
            bearer_token=token,
            workspaces=workspaces,
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
            readiness_workspace_id=readiness_workspace_id,
        )


def _is_beads_workspace(path: Path) -> bool:
    beads_dir = path / ".beads"
    return (beads_dir / "metadata.json").is_file() or (beads_dir / "config.yaml").is_file()


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
