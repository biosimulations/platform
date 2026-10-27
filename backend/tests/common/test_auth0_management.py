"""Request and response contracts for Auth0 Management helpers.

HTTP is served by an httpx.MockTransport, so no Auth0 tenant or network access
is involved. The success test retains the real token acquisition and
``_auth_headers`` path rather than replacing the missing-deadline boundary with
an AsyncMock.
"""

import asyncio
import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from biosim_server.common.auth import auth0_management as mgmt
from biosim_server.common.auth.auth0_management import (
    Auth0ManagementError,
    ResendOutcome,
)

JOB_URL = "/api/v2/jobs/verification-email"
USER_URL = "/api/v2/users/auth0%7Cabc123"


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        auth0=SimpleNamespace(
            domain="tenant.example.auth0.com",
            management_client_id="mgmt-id",
            management_client_secret="mgmt-secret",
        )
    )


def _user(**overrides: Any) -> dict[str, Any]:
    user: dict[str, Any] = {
        "user_id": "auth0|abc123",
        "email": "person@example.test",
        "email_verified": False,
        "identities": [
            {
                "provider": "auth0",
                "connection": "Username-Password-Authentication",
                "user_id": "abc123",
            }
        ],
    }
    user.update(overrides)
    return user


def _job(**overrides: Any) -> dict[str, Any]:
    job: dict[str, Any] = {
        "id": "job_abc123",
        "type": "verification_email",
        "status": "pending",
    }
    job.update(overrides)
    return job


class ResendEndpoint:
    def __init__(self, *, user: Any = None, job: Any = None) -> None:
        self.user = _user() if user is None else user
        self.job = _job() if job is None else job
        self.requests: list[httpx.Request] = []
        self.token_posts = 0
        self.user_gets = 0
        self.job_posts = 0

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/oauth/token":
            self.token_posts += 1
            return httpx.Response(
                200, json={"access_token": "test-mgmt-token", "expires_in": 3600}
            )
        if request.url.path == "/api/v2/users/auth0|abc123":
            self.user_gets += 1
            if isinstance(self.user, httpx.Response):
                return self.user
            return httpx.Response(200, json=self.user)
        if request.url.path == JOB_URL:
            self.job_posts += 1
            if isinstance(self.job, httpx.Response):
                return self.job
            return httpx.Response(201, json=self.job)
        raise AssertionError(f"unexpected request: {request.method} {request.url}")


InstallResendEndpoint = Callable[[Any], None]


@pytest.fixture
def install_resend_endpoint(monkeypatch: pytest.MonkeyPatch) -> InstallResendEndpoint:
    real_async_client = httpx.AsyncClient

    def install(endpoint: Any) -> None:
        monkeypatch.setattr(mgmt, "get_settings", lambda: _settings())

        def client_factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            kwargs["transport"] = httpx.MockTransport(endpoint)
            return real_async_client(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", client_factory)

    return install


@pytest.mark.asyncio
async def test_resend_uses_real_auth_headers_and_validates_the_live_user_and_job(
    install_resend_endpoint: InstallResendEndpoint,
) -> None:
    endpoint = ResendEndpoint(job=_job(status="completed"))
    install_resend_endpoint(endpoint)

    assert (
        await mgmt.resend_auth0_verification_email("auth0|abc123")
        is ResendOutcome.ACCEPTED
    )
    assert endpoint.token_posts == 1
    assert endpoint.user_gets == 1
    assert endpoint.job_posts == 1
    token, user, job = endpoint.requests
    assert user.headers["Authorization"] == "Bearer test-mgmt-token"
    assert job.headers["Authorization"] == "Bearer test-mgmt-token"
    assert user.url.raw_path.decode() == USER_URL
    assert user.url.query == b""
    assert job.url.path == JOB_URL
    assert json.loads(job.content) == {"user_id": "auth0|abc123"}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "completed"])
async def test_documented_job_states_are_accepted(
    install_resend_endpoint: InstallResendEndpoint, status: str
) -> None:
    install_resend_endpoint(ResendEndpoint(job=_job(status=status)))
    assert (
        await mgmt.resend_auth0_verification_email("auth0|abc123")
        is ResendOutcome.ACCEPTED
    )


@pytest.mark.asyncio
async def test_already_verified_live_user_sends_no_job(
    install_resend_endpoint: InstallResendEndpoint,
) -> None:
    endpoint = ResendEndpoint(user=_user(email_verified=True))
    install_resend_endpoint(endpoint)
    assert (
        await mgmt.resend_auth0_verification_email("auth0|abc123")
        is ResendOutcome.ALREADY_VERIFIED
    )
    assert endpoint.user_gets == 1
    assert endpoint.job_posts == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user",
    [
        _user(user_id="auth0|someone-else"),
        _user(email_verified=None),
        _user(email_verified="false"),
        _user(email=None),
        _user(email=""),
        _user(identities=[]),
        _user(
            identities=[{"provider": "google-oauth2", "connection": "google-oauth2"}]
        ),
        _user(identities=[{"provider": "auth0", "connection": ""}]),
    ],
    ids=[
        "different-user",
        "unknown-verification",
        "non-boolean-verification",
        "no-address",
        "blank-address",
        "no-identity",
        "social-primary",
        "blank-connection",
    ],
)
async def test_ineligible_live_user_sends_no_job(
    install_resend_endpoint: InstallResendEndpoint, user: dict[str, Any]
) -> None:
    endpoint = ResendEndpoint(user=user)
    install_resend_endpoint(endpoint)
    with pytest.raises(Auth0ManagementError):
        await mgmt.resend_auth0_verification_email("auth0|abc123")
    assert endpoint.job_posts == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,body",
    [
        (200, _job()),
        (204, None),
        (201, []),
        (201, "job"),
        (201, {"type": "verification_email", "status": "pending"}),
        (201, _job(id="")),
        (201, _job(id=" ")),
        (201, _job(type="other")),
        (201, _job(status="failed")),
        (201, _job(status="")),
        (201, _job(status=None)),
        (201, _job(status=[])),
        (201, _job(status={})),
    ],
    ids=[
        "http-200",
        "http-204",
        "array",
        "scalar",
        "no-id",
        "empty-id",
        "blank-id",
        "wrong-type",
        "failed",
        "empty-status",
        "missing-status",
        "list-status",
        "object-status",
    ],
)
async def test_invalid_job_acknowledgment_is_an_upstream_error(
    install_resend_endpoint: InstallResendEndpoint,
    status: int,
    body: Any,
) -> None:
    response = (
        httpx.Response(status, json=body)
        if body is not None
        else httpx.Response(status)
    )
    endpoint = ResendEndpoint(job=response)
    install_resend_endpoint(endpoint)
    with pytest.raises(Auth0ManagementError):
        await mgmt.resend_auth0_verification_email("auth0|abc123")
    assert endpoint.token_posts == 1
    assert endpoint.user_gets == 1
    assert endpoint.job_posts == 1


@pytest.mark.asyncio
async def test_invalid_json_job_acknowledgment_is_an_upstream_error(
    install_resend_endpoint: InstallResendEndpoint,
) -> None:
    install_resend_endpoint(
        ResendEndpoint(
            job=httpx.Response(
                201, content=b"not-json", headers={"content-type": "application/json"}
            )
        )
    )
    with pytest.raises(Auth0ManagementError):
        await mgmt.resend_auth0_verification_email("auth0|abc123")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw", ["nan", "NaN", "inf", "-inf", "Infinity", "-1", "soon", ""]
)
async def test_invalid_retry_after_uses_the_fallback(
    install_resend_endpoint: InstallResendEndpoint, raw: str
) -> None:
    install_resend_endpoint(
        ResendEndpoint(job=httpx.Response(429, headers={"Retry-After": raw}))
    )
    with pytest.raises(mgmt.Auth0ManagementRateLimited) as raised:
        await mgmt.resend_auth0_verification_email("auth0|abc123")
    assert raised.value.retry_after == mgmt._MGMT_EXHAUSTED_RETRY_AFTER_SECONDS


@pytest.mark.asyncio
async def test_lookup_retry_after_is_rounded_up_and_sends_no_job(
    install_resend_endpoint: InstallResendEndpoint,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An eligibility-GET 429 uses the same ceiling as token and job hints."""

    class LookupThrottle:
        def __init__(self) -> None:
            self.token_posts = 0
            self.user_gets = 0
            self.job_posts = 0

        async def __call__(self, request: httpx.Request) -> httpx.Response:
            if request.url.path == "/oauth/token":
                self.token_posts += 1
                return httpx.Response(
                    200, json={"access_token": "test-mgmt-token", "expires_in": 3600}
                )
            if request.url.path.startswith("/api/v2/users/"):
                self.user_gets += 1
                return httpx.Response(429, headers={"Retry-After": "7.5"})
            self.job_posts += 1
            raise AssertionError(f"job must not be posted: {request.url}")

    endpoint = LookupThrottle()
    install_resend_endpoint(endpoint)
    monkeypatch.setattr(asyncio, "sleep", lambda _seconds: _async_none())

    with pytest.raises(mgmt.Auth0ManagementRateLimited) as raised:
        await mgmt.resend_auth0_verification_email("auth0|abc123")

    assert raised.value.retry_after == 8
    assert endpoint.token_posts == 1
    assert endpoint.user_gets == mgmt._MGMT_MAX_ATTEMPTS
    assert endpoint.job_posts == 0


async def _async_none() -> None:
    return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user_id,raw_path",
    [
        ("auth0|abc", "/api/v2/users/auth0%7Cabc"),
        ("auth0|a/../../tickets", "/api/v2/users/auth0%7Ca%2F..%2F..%2Ftickets"),
        ("auth0|a?b#c", "/api/v2/users/auth0%7Ca%3Fb%23c"),
    ],
    ids=["plain", "traversal", "query-fragment"],
)
async def test_user_ids_are_one_encoded_path_segment(
    user_id: str, raw_path: str
) -> None:
    assert mgmt._user_url("tenant.example.auth0.com", user_id).endswith(raw_path)
