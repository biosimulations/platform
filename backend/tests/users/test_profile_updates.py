"""Strict profile PATCH and real-helper integration. No live Auth0 writes."""

import asyncio
import json
import logging
from urllib.parse import quote
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from biosim_server.api.main import app
from biosim_server.common.auth import auth0_management as mgmt
from biosim_server.common.auth.auth0 import (
    AuthenticatedUser,
    JwksCache,
    get_current_user,
)
from biosim_server.config import get_settings
from tests.fixtures.auth_seam import (
    clear_auth_overrides,
    install_auth_seam,
    make_auth0_settings,
)
from tests.fixtures.jwks_fixtures import jwks_document, make_key

PATH = "/api/v1/me"
ISSUER = "https://tenant.auth0.com/"
AUDIENCE = "https://api.example.test/"
SUB = "auth0|profile"


class Provider:
    def __init__(self) -> None:
        self.record: dict[str, Any] = {
            "user_id": SUB,
            "email": "old@example.com",
            "email_verified": True,
            "name": "Old Name",
        }
        self.calls: list[httpx.Request] = []
        self.token_posts = 0
        self.patches: list[dict[str, Any]] = []
        self.failure: str | int | None = None
        self.failures_remaining = 10
        self.key: Any = None

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if request.url.path == "/.well-known/jwks.json":
            return httpx.Response(200, json=jwks_document(self.key))
        if request.url.path == "/oauth/token":
            self.token_posts += 1
            return httpx.Response(
                200, json={"access_token": "test-management-token", "expires_in": 3600}
            )
        assert (
            request.url.raw_path
            == ("/api/v2/users/" + quote(self.record["user_id"], safe="")).encode()
        )
        assert request.headers["authorization"] == "Bearer test-management-token"
        if request.method == "GET":
            return httpx.Response(200, json=self.record)
        assert request.method == "PATCH"
        body = json.loads(request.content)
        self.patches.append(body)
        if self.failure is not None and self.failures_remaining:
            self.failures_remaining -= 1
            if self.failure == "transport":
                raise httpx.ReadError(
                    "private exception with old@example.com", request=request
                )
            if self.failure == "timeout":
                raise httpx.ReadTimeout("private timeout", request=request)
            if self.failure == "hang":
                await asyncio.Event().wait()
            if self.failure == "invalid-json":
                return httpx.Response(200, content=b"private invalid response")
            if self.failure == "invalid-object":
                return httpx.Response(200, json=[])
            assert isinstance(self.failure, int)
            return httpx.Response(
                self.failure,
                headers={"Retry-After": "7.5"},
                json={"message": "private provider body", "errorCode": "private-error"},
            )
        self.record.update(
            {key: value for key, value in body.items() if key != "verify_email"}
        )
        return httpx.Response(200, json=self.record)


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> Iterator[Provider]:
    settings = get_settings().auth0
    for key, value in {
        "domain": "tenant.auth0.com",
        "issuer": ISSUER,
        "management_client_id": "test-id",
        "management_client_secret": "test-secret",
    }.items():
        monkeypatch.setattr(settings, key, value)
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        sub=SUB, issuer=ISSUER, email="old@example.com"
    )
    endpoint = Provider()
    real_client = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(endpoint)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    yield endpoint
    app.dependency_overrides.pop(get_current_user, None)
    clear_auth_overrides(app)


@pytest.mark.parametrize(
    "payload,wire",
    [
        ({"name": "New Name"}, {"name": "New Name"}),
        (
            {"email": "New.Local@EXAMPLE.COM"},
            {
                "email": "New.Local@example.com",
                "email_verified": False,
                "verify_email": True,
            },
        ),
        (
            {"name": "New Name", "email": "new@example.com"},
            {
                "name": "New Name",
                "email": "new@example.com",
                "email_verified": False,
                "verify_email": True,
            },
        ),
    ],
)
def test_profile_payload_uses_real_helper(
    provider: Provider, payload: dict[str, Any], wire: dict[str, Any]
) -> None:
    response = TestClient(app).patch(PATH, json=payload)
    assert response.status_code == 200, response.text
    assert provider.patches == [wire]
    assert provider.token_posts == 1
    assert response.json()["email"] == wire.get("email", "old@example.com")
    if "email" in wire:
        assert response.json()["emailVerified"] is False


@pytest.mark.parametrize(
    "email",
    [
        None,
        "",
        " ",
        "not-an-address",
        "name@",
        "@example.com",
        "x@@example.com",
        "x@example",
        "Name <x@example.com>",
        123,
        False,
        [],
        {},
        "a" * 250 + "@example.com",
    ],
)
@pytest.mark.parametrize("mixed", [False, True])
def test_bad_email_never_reaches_auth0(
    provider: Provider, email: Any, mixed: bool
) -> None:
    payload = {"email": email, **({"name": "New Name"} if mixed else {})}
    assert TestClient(app).patch(PATH, json=payload).status_code == 422
    assert provider.calls == []


@pytest.mark.parametrize(
    "field",
    [
        "roles",
        "app_metadata",
        "blocked",
        "password",
        "connection",
        "user_id",
        "verify_email",
        "email_verified",
        "emailVerified",
    ],
)
def test_extra_fields_are_rejected_atomically(provider: Provider, field: str) -> None:
    assert (
        TestClient(app).patch(PATH, json={"name": "New Name", field: True}).status_code
        == 422
    )
    assert provider.calls == []


def test_empty_patch_only_reads_profile(provider: Provider) -> None:
    response = TestClient(app).patch(PATH, json={})
    assert response.status_code == 200
    assert not provider.patches
    assert [r.method for r in provider.calls] == ["POST", "GET"]


def test_name_null_remains_allowed(
    provider: Provider,
) -> None:
    # Preserve the pre-existing null-name behavior (the helper omits None).
    assert TestClient(app).patch(PATH, json={"name": None}).status_code == 200
    assert provider.patches == [{}]


def test_name_only_social_update_uses_real_helper(provider: Provider) -> None:
    sub = "google-oauth2|social-profile"
    provider.record["user_id"] = sub
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        sub=sub, issuer=ISSUER
    )
    response = TestClient(app).patch(PATH, json={"name": "New Name"})
    assert response.status_code == 200
    assert response.json()["provider"] == "google-oauth2"
    assert provider.patches == [{"name": "New Name"}]


@pytest.mark.parametrize(
    "status,expected",
    [
        (409, 409),
        (400, 422),
        (422, 422),
        (401, 502),
        (403, 502),
        (404, 502),
        (500, 502),
        (503, 502),
        (429, 503),
        (302, 502),
    ],
)
def test_provider_errors_are_classified_without_body_leaks(
    provider: Provider, status: int, expected: int, caplog: pytest.LogCaptureFixture
) -> None:
    provider.failure = status
    with caplog.at_level(logging.WARNING):
        response = TestClient(app).patch(PATH, json={"email": "new@example.com"})
    assert response.status_code == expected, response.text
    assert len(provider.patches) == 1
    assert "private provider body" not in response.text + caplog.text
    assert "private-error" not in response.text + caplog.text
    assert SUB not in caplog.text
    if status == 429:
        assert response.headers["retry-after"] == "8"
    if status == 409:
        assert response.json()["detail"] == "Email address is already in use"


@pytest.mark.parametrize(
    "failure", ["transport", "timeout", "invalid-json", "invalid-object", 500, 502, 503]
)
def test_uncertain_email_update_is_never_replayed(
    provider: Provider, failure: str | int
) -> None:
    provider.failure = failure
    assert (
        TestClient(app).patch(PATH, json={"email": "new@example.com"}).status_code
        == 502
    )
    assert len(provider.patches) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [500, "transport", "timeout", 429])
async def test_name_only_retries_but_email_does_not(
    provider: Provider, monkeypatch: pytest.MonkeyPatch, failure: str | int
) -> None:
    async def no_wait(_seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_wait)
    provider.failure = failure
    provider.failures_remaining = 1
    assert (await mgmt.update_auth0_user(SUB, name="New Name"))["name"] == "New Name"
    assert len(provider.patches) == 2
    provider.patches.clear()
    provider.failures_remaining = 1
    with pytest.raises(mgmt.Auth0ManagementError):
        await mgmt.update_auth0_user(SUB, email="new@example.com", verify_email=True)
    assert len(provider.patches) == 1
    assert provider.token_posts == 1


@pytest.mark.asyncio
async def test_hanging_email_patch_stays_in_shared_deadline(
    provider: Provider, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider.failure = "hang"
    monkeypatch.setattr(mgmt, "_MGMT_OPERATION_DEADLINE_SECONDS", 0.05)
    with pytest.raises(mgmt.Auth0ManagementUnavailable):
        await asyncio.wait_for(
            mgmt.update_auth0_user(SUB, email="new@example.com"), timeout=1
        )
    assert len(provider.patches) == 1
    provider.failure = None
    assert await mgmt.update_auth0_user(SUB, name="Recovered")
    assert provider.token_posts == 1


@pytest.mark.asyncio
async def test_token_budget_exhaustion_starts_no_email_patch(
    provider: Provider, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [0.0]
    original = provider.__class__.__call__

    async def timed(self: Provider, request: httpx.Request) -> httpx.Response:
        response = await original(self, request)
        if request.url.path == "/oauth/token":
            now[0] = 26.0
        return response

    monkeypatch.setattr(Provider, "__call__", timed)
    monkeypatch.setattr(mgmt, "_monotonic", lambda: now[0])
    with pytest.raises(mgmt.Auth0ManagementUnavailable, match="budget exhausted"):
        await mgmt.update_auth0_user(SUB, email="new@example.com")
    assert provider.token_posts == 1
    assert not provider.patches


@pytest.mark.asyncio
async def test_cancelling_email_update_never_replays(
    provider: Provider, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = asyncio.Event()
    original = Provider.__call__

    async def hanging(self: Provider, request: httpx.Request) -> httpx.Response:
        if request.method == "PATCH":
            entered.set()
        return await original(self, request)

    monkeypatch.setattr(Provider, "__call__", hanging)
    provider.failure = "hang"
    task = asyncio.create_task(mgmt.update_auth0_user(SUB, email="new@example.com"))
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(provider.patches) == 1
    provider.failure = None
    assert await mgmt.update_auth0_user(SUB, name="After cancel")
    assert provider.token_posts == 1


def test_same_signed_token_reads_new_email_through_real_helpers(
    provider: Provider, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = make_key("profile-key")
    provider.key = key
    settings = make_auth0_settings(
        domain="tenant.auth0.com",
        issuer=ISSUER,
        audience=AUDIENCE,
        jwks_uri=ISSUER + ".well-known/jwks.json",
    )
    app.dependency_overrides.pop(get_current_user)
    install_auth_seam(monkeypatch, settings=settings, cache=JwksCache(), app=app)
    token = key.token(
        sub=SUB,
        issuer=ISSUER,
        audience=AUDIENCE,
        extra_claims={settings.email_claim: "old@example.com"},
    )
    headers = {"Authorization": f"Bearer {token}"}
    client = TestClient(app)
    patched = client.patch(
        PATH, headers=headers, json={"email": "New.Local@example.com"}
    )
    read = client.get(PATH, headers=headers)
    assert patched.status_code == read.status_code == 200
    for response in [patched, read]:
        assert response.json()["email"] == "New.Local@example.com"
        assert response.json()["emailVerified"] is False
    from jose import jwt  # type: ignore[import-untyped]

    assert jwt.get_unverified_claims(token)[settings.email_claim] == "old@example.com"
    assert provider.token_posts == 1
    assert provider.patches == [
        {
            "email": "New.Local@example.com",
            "email_verified": False,
            "verify_email": True,
        }
    ]
    assert any(
        request.method == "GET" and "/api/v2/users/" in request.url.path
        for request in provider.calls
    )


def test_openapi_strict_patch_contract() -> None:
    schema = app.openapi()["components"]["schemas"]["UpdateUserProfileRequest"]
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"name", "email"}
    assert schema["properties"]["email"]["format"] == "email"
    assert schema["properties"]["email"]["maxLength"] == 255
    assert "anyOf" not in schema["properties"]["email"]
    assert "default" not in schema["properties"]["email"]
    assert "email" not in schema.get("required", [])


@pytest.mark.parametrize(
    "sub,issuer,status",
    [
        (SUB, None, 403),
        (SUB, "https://foreign.auth0.com/", 403),
        ("google-oauth2|profile", ISSUER, 400),
        ("auth0|", ISSUER, 400),
        ("auth0|a|b", ISSUER, 400),
        ("auth0|a@clients", ISSUER, 400),
    ],
)
def test_email_guards_precede_all_management_work(
    provider: Provider, sub: str, issuer: str | None, status: int
) -> None:
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        sub=sub, issuer=issuer
    )
    response = TestClient(app).patch(
        PATH, json={"name": "New Name", "email": "new@example.com"}
    )
    assert response.status_code == status
    assert provider.calls == []


@pytest.mark.asyncio
async def test_helper_keeps_verification_controls_server_owned(
    provider: Provider,
) -> None:
    await mgmt.update_auth0_user(SUB, email="new@example.com", verify_email=False)
    assert provider.patches == [
        {"email": "new@example.com", "email_verified": False, "verify_email": True}
    ]
