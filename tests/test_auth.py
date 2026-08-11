import pytest

from beads_mcp_server.auth import StaticTokenVerifier


@pytest.mark.parametrize("candidate", ["", "wrong", "test-token-extra"])
async def test_static_token_verifier_denies_unknown_tokens(candidate: str) -> None:
    verifier = StaticTokenVerifier("test-token")

    assert await verifier.verify_token(candidate) is None


async def test_static_token_verifier_accepts_only_configured_token() -> None:
    verifier = StaticTokenVerifier("test-token")

    access_token = await verifier.verify_token("test-token")

    assert access_token is not None
    assert access_token.client_id == "beads-agent"
    assert access_token.scopes == ["beads:read", "beads:write"]
