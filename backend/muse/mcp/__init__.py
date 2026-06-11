"""muse's MCP server: exposes session query + markup tools to the user's own
Claude Code over Streamable HTTP, mounted on the same uvicorn process so tool
calls and the web UI share live state."""

from .server import build_mcp, set_service

__all__ = ["build_mcp", "set_service"]
