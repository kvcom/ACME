"""Async MCP HTTP client.

Catches transport errors so the orchestrator can record a tool_call_log with
status=error rather than crashing the whole request.
"""
from __future__ import annotations

from typing import Any

import httpx

from acme_app.config import settings


class MCPClientError(Exception):
    """Raised when the MCP server returns a non-2xx or is unreachable."""


class MCPClient:
    def __init__(self, base_url: str | None = None, timeout: float = 20.0) -> None:
        self.base_url = (base_url or settings.mcp_server_url).rstrip('/')
        self.timeout = timeout

    async def call_tool(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(f'{self.base_url}/tools/{tool_name}', json=payload)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            raise MCPClientError(f'{tool_name} returned {exc.response.status_code}: {exc.response.text[:200]}') from exc
        except httpx.HTTPError as exc:
            raise MCPClientError(f'{tool_name} transport error: {exc}') from exc

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f'{self.base_url}/health')
                return response.status_code == 200
        except httpx.HTTPError:
            return False
