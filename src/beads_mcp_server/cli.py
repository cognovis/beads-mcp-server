"""Process entry point for the Streamable HTTP MCP server."""

import logging
import os
import sys

import uvicorn

from beads_mcp_server.config import ConfigError, ServerConfig
from beads_mcp_server.server import create_http_app


def main() -> None:
    """Load fail-closed configuration and run the HTTP application."""
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        config = ServerConfig.from_environment(os.environ)
    except ConfigError:
        logging.getLogger(__name__).exception("Server configuration is invalid")
        raise SystemExit(2) from None

    app = create_http_app(config)
    uvicorn.run(app, host=config.host, port=config.port, log_level="info")


if __name__ == "__main__":
    main()
