"""Request-shape tests for the Auth0 Management API client.

HTTP is served by an httpx.MockTransport, so no Auth0 tenant or network access
is involved.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from biosim_server.common.auth import auth0_management


@pytest.mark.asyncio
async def test_resend_verification_email_targets_primary_database_identity() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(201, json={"type": "verification_email", "status": "pending", "id": "job_abc123"})

    real_async_client = httpx.AsyncClient
    settings = MagicMock()
    settings.auth0.domain = "tenant.example.auth0.com"
    with (
        patch.object(auth0_management, "get_settings", return_value=settings),
        patch.object(
            auth0_management, "_auth_headers", AsyncMock(return_value={"Authorization": "Bearer test-mgmt-token"})
        ),
        patch.object(httpx, "AsyncClient", lambda: real_async_client(transport=httpx.MockTransport(handler))),
    ):
        job = await auth0_management.resend_auth0_verification_email("auth0|abc123")

    assert job["status"] == "pending"
    assert len(captured) == 1
    request = captured[0]
    assert request.method == "POST"
    assert str(request.url) == "https://tenant.example.auth0.com/api/v2/jobs/verification-email"
    assert request.headers["Authorization"] == "Bearer test-mgmt-token"
    # No `identity` object: without one Auth0 only verifies the user's primary
    # database identity, which is why the router refuses non-database accounts.
    assert json.loads(request.content) == {"user_id": "auth0|abc123"}
