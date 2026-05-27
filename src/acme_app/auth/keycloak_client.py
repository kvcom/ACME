import httpx

from acme_app.config import settings


async def login(username: str, password: str) -> dict:
    url = f"{settings.keycloak_url}/realms/{settings.keycloak_realm}/protocol/openid-connect/token"
    payload = {
        'client_id': settings.keycloak_client_id,
        'grant_type': 'password',
        'username': username,
        'password': password,
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(url, data=payload)
        response.raise_for_status()
        return response.json()
