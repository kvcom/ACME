import httpx

from acme_app.config import settings


class MCPClient:
    def __init__(self) -> None:
        self.base_url = settings.mcp_server_url.rstrip('/')

    async def call_tool(self, tool_name: str, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(f"{self.base_url}/tools/{tool_name}", json=payload)
            response.raise_for_status()
            return response.json()
