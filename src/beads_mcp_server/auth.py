"""Static bearer-token verification for the private MCP endpoint."""

import secrets

from mcp.server.auth.provider import AccessToken


class StaticTokenVerifier:
    """Verify one operator-provided bearer token without persisting it."""

    def __init__(self, token: str) -> None:
        if not token:
            raise ValueError("A non-empty bearer token is required")
        self._token = token

    async def verify_token(self, token: str) -> AccessToken | None:
        """Return access metadata only when the presented token matches."""
        if not token or not secrets.compare_digest(token, self._token):
            return None
        return AccessToken(
            token=token,
            client_id="beads-agent",
            scopes=["beads:read", "beads:write"],
        )
