"""Thin async client for the Auth0 Management API (api/v2/users/*).

Used by PATCH/DELETE /api/v1/me to actually mutate the Auth0 user record --
Auth0 is the single source of truth for identity (no local password storage),
so profile writes have to go through it rather than a local shadow table.

Requires a Machine-to-Machine Auth0 application authorized for the Management
API with `update:users` / `delete:users` scopes (AUTH0_MANAGEMENT_CLIENT_ID /
AUTH0_MANAGEMENT_CLIENT_SECRET). Callers should check `management_api_configured()`
first and surface a 503 when it's false, rather than let these raise.
"""

import asyncio
import time
from typing import Any

import httpx

from biosim_server.config import get_settings

_token_cache: dict[str, Any] = {"access_token": None, "expires_at": 0.0}
# Refresh a little before actual expiry to avoid racing a request against the
# token dying mid-flight.
_EXPIRY_SAFETY_MARGIN_SECONDS = 60
_token_refresh_lock = asyncio.Lock()


def management_api_configured() -> bool:
    settings = get_settings().auth0
    return bool(settings.management_client_id and settings.management_client_secret)


async def _get_management_token() -> str:
    settings = get_settings().auth0
    now = time.time()
    if _token_cache["access_token"] is not None and now < _token_cache["expires_at"]:
        access_token: str = _token_cache["access_token"]
        return access_token

    async with _token_refresh_lock:
        # Re-check after acquiring the lock -- another concurrent caller may have
        # already refreshed the token while we were waiting on it.
        now = time.time()
        if _token_cache["access_token"] is None or now >= _token_cache["expires_at"]:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"https://{settings.domain}/oauth/token",
                    json={
                        "client_id": settings.management_client_id,
                        "client_secret": settings.management_client_secret,
                        "audience": f"https://{settings.domain}/api/v2/",
                        "grant_type": "client_credentials",
                    },
                    timeout=10.0,
                )
                resp.raise_for_status()
                payload = resp.json()
                _token_cache["access_token"] = payload["access_token"]
                _token_cache["expires_at"] = now + payload["expires_in"] - _EXPIRY_SAFETY_MARGIN_SECONDS
        access_token = _token_cache["access_token"]
        return access_token


async def _auth_headers() -> dict[str, str]:
    token = await _get_management_token()
    return {"Authorization": f"Bearer {token}"}


async def get_auth0_user(user_id: str) -> dict[str, Any]:
    settings = get_settings().auth0
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://{settings.domain}/api/v2/users/{user_id}",
            headers=await _auth_headers(),
            timeout=10.0,
        )
        resp.raise_for_status()
        return dict(resp.json())


async def update_auth0_user(user_id: str, **fields: Any) -> dict[str, Any]:
    settings = get_settings().auth0
    async with httpx.AsyncClient() as client:
        resp = await client.patch(
            f"https://{settings.domain}/api/v2/users/{user_id}",
            headers=await _auth_headers(),
            json=fields,
            timeout=10.0,
        )
        resp.raise_for_status()
        return dict(resp.json())


async def delete_auth0_user(user_id: str) -> None:
    settings = get_settings().auth0
    async with httpx.AsyncClient() as client:
        resp = await client.delete(
            f"https://{settings.domain}/api/v2/users/{user_id}",
            headers=await _auth_headers(),
            timeout=10.0,
        )
        resp.raise_for_status()
