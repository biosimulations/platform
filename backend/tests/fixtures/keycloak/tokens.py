"""Fetch real access tokens from a Keycloak realm via the Resource Owner
Password Credentials ("direct access") grant -- the realm's client is public
and direct-grant-enabled specifically so tests can get a token for a given
user without a browser redirect.
"""

import httpx

_TOKEN_TIMEOUT_SECONDS = 10.0


async def fetch_keycloak_token(issuer: str, client_id: str, username: str, password: str) -> str:
    """Logs in as `username`/`password` via Keycloak's direct-grant token endpoint and returns the access token.

    Raises `httpx.HTTPStatusError` if the request fails (e.g. bad
    credentials, client not direct-grant-enabled).
    """
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{issuer}/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": client_id,
                "username": username,
                "password": password,
                "scope": "openid",
            },
            timeout=_TOKEN_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        access_token: str = resp.json()["access_token"]
        return access_token
