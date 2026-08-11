from pathlib import Path

import pytest

from beads_mcp_server.config import (
    ConfigError,
    ServerConfig,
    WorkspaceRegistry,
    WorkspaceUnavailableError,
)


def test_missing_bearer_token_fails_closed() -> None:
    with pytest.raises(ConfigError, match="BEADS_MCP_TOKEN"):
        ServerConfig.from_environment(
            {
                "BEADS_WORKSPACES_JSON": '{"hetzner":"/srv/hetzner"}',
            }
        )


def test_empty_bearer_token_fails_closed() -> None:
    with pytest.raises(ConfigError, match="BEADS_MCP_TOKEN"):
        ServerConfig.from_environment(
            {
                "BEADS_MCP_TOKEN": "",
                "BEADS_WORKSPACES_JSON": '{"hetzner":"/srv/hetzner"}',
            }
        )


def test_missing_workspace_registry_fails_closed() -> None:
    with pytest.raises(ConfigError, match="workspace registry"):
        ServerConfig.from_environment({"BEADS_MCP_TOKEN": "test-token"})


def test_workspace_registry_rejects_unknown_identifiers() -> None:
    registry = WorkspaceRegistry({"hetzner": Path("/srv/hetzner")})

    with pytest.raises(ConfigError, match="Unknown workspace_id"):
        registry.resolve("polaris")


@pytest.mark.parametrize("workspace_id", ["", "../hetzner", "/srv/hetzner", "a b"])
def test_workspace_registry_rejects_malformed_identifiers(workspace_id: str) -> None:
    with pytest.raises(ConfigError, match="workspace"):
        WorkspaceRegistry({workspace_id: Path("/srv/hetzner")})


def test_workspace_registry_rejects_relative_paths() -> None:
    with pytest.raises(ConfigError, match="absolute"):
        WorkspaceRegistry({"hetzner": Path("relative/path")})


def test_workspace_registry_discovers_exact_operator_controlled_ids(tmp_path: Path) -> None:
    for workspace_id in ("library", "cognovis_core", "cognovis-core", "MCN", "repo.v2"):
        metadata = tmp_path / workspace_id / ".beads" / "metadata.json"
        metadata.parent.mkdir(parents=True)
        metadata.write_text("{}")
    (tmp_path / "not-a-workspace").mkdir()

    registry = WorkspaceRegistry.from_root(tmp_path)

    assert registry.ids() == ("MCN", "cognovis-core", "cognovis_core", "library", "repo.v2")
    assert registry.resolve("cognovis_core") == tmp_path / "cognovis_core"
    assert registry.resolve("cognovis-core") == tmp_path / "cognovis-core"


def test_workspace_registry_never_infers_dash_underscore_aliases(tmp_path: Path) -> None:
    metadata = tmp_path / "cognovis_core" / ".beads" / "metadata.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text("{}")
    registry = WorkspaceRegistry.from_root(tmp_path)

    with pytest.raises(ConfigError, match="Unknown workspace_id"):
        registry.resolve("cognovis-core")


def test_workspace_registry_reports_removed_workspace_separately(tmp_path: Path) -> None:
    workspace = tmp_path / "hetzner"
    metadata = workspace / ".beads" / "metadata.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text("{}")
    registry = WorkspaceRegistry.from_root(tmp_path)
    metadata.unlink()

    with pytest.raises(WorkspaceUnavailableError, match="unavailable"):
        registry.resolve("hetzner")


def test_complete_environment_builds_fail_closed_transport_configuration(
    tmp_path: Path,
) -> None:
    metadata = tmp_path / "hetzner" / ".beads" / "metadata.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text("{}")
    config = ServerConfig.from_environment(
        {
            "BEADS_MCP_TOKEN": "test-token",
            "BEADS_WORKSPACE_ROOT": str(tmp_path),
            "BEADS_READINESS_WORKSPACE_ID": "hetzner",
            "MCP_PUBLIC_URL": "https://mcp.test/mcp",
            "MCP_ALLOWED_HOSTS": "mcp.test,mcp.test:*",
        }
    )

    assert config.allowed_hosts == ("mcp.test", "mcp.test:*")
    assert config.allowed_origins == ()
    assert config.public_url == "https://mcp.test/mcp"
    assert config.workspaces.ids() == ("hetzner",)
    assert config.readiness_workspace_id == "hetzner"
