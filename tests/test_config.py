from pathlib import Path

import pytest

from beads_mcp_server.config import ConfigError, ServerConfig, WorkspaceRegistry


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
    with pytest.raises(ConfigError, match="BEADS_WORKSPACES_JSON"):
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


def test_complete_environment_builds_fail_closed_transport_configuration() -> None:
    config = ServerConfig.from_environment(
        {
            "BEADS_MCP_TOKEN": "test-token",
            "BEADS_WORKSPACES_JSON": '{"hetzner":"/srv/hetzner"}',
            "MCP_PUBLIC_URL": "https://mcp.test/mcp",
            "MCP_ALLOWED_HOSTS": "mcp.test,mcp.test:*",
        }
    )

    assert config.allowed_hosts == ("mcp.test", "mcp.test:*")
    assert config.allowed_origins == ()
    assert config.public_url == "https://mcp.test/mcp"
