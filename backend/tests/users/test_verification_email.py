"""Resend route contract through the real pooled Management helper; no live mail."""

import asyncio
import logging
from collections.abc import Iterator
from typing import Any
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException, Request, Response
from fastapi.testclient import TestClient

from biosim_server.api.main import app
from biosim_server.common.auth.auth0 import AuthenticatedUser, get_current_user
from biosim_server.common.auth import auth0_management as mgmt
from biosim_server.common import ratelimit
from biosim_server.config import RateLimitSettings, get_settings
from biosim_server.users import router

PATH = "/api/v1/me/resend-verification"
ISSUER = "https://tenant.auth0.com/"
SUB = "auth0|private-subject"


def authorize(sub: str = SUB, issuer: str | None = ISSUER) -> None:
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        sub=sub, issuer=issuer
    )


@pytest.fixture(autouse=True)
def setup(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    settings = get_settings()
    for key, value in {
        "domain": "tenant.auth0.com",
        "issuer": ISSUER,
        "management_client_id": "client",
        "management_client_secret": "private-secret",
    }.items():
        monkeypatch.setattr(settings.auth0, key, value)
    monkeypatch.setattr(settings.ratelimit, "enabled", True)
    monkeypatch.setattr(settings.ratelimit, "verification_email_per_window", 3)
    monkeypatch.setattr(settings.ratelimit, "verification_email_window_seconds", 600)
    authorize()
    yield
    app.dependency_overrides.pop(get_current_user, None)


class Endpoint:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.user: dict[str, Any] = {
            "user_id": SUB,
            "email": "private@example.test",
            "email_verified": False,
            "identities": [{"provider": "auth0", "connection": "database"}],
        }
        self.fail_at: str | None = None
        self.status = 429

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        stage = (
            "token"
            if path == "/oauth/token"
            else "lookup"
            if "/users/" in path
            else "job"
        )
        self.calls.append(stage)
        if stage == self.fail_at:
            return httpx.Response(
                self.status,
                headers={"Retry-After": "7.5"},
                json={"error": "private upstream material"},
            )
        if stage == "token":
            return httpx.Response(
                200, json={"access_token": "private-token", "expires_in": 3600}
            )
        if stage == "lookup":
            return httpx.Response(200, json=self.user)
        assert path == "/api/v2/jobs/verification-email"
        return httpx.Response(
            201,
            json={
                "id": "private-job",
                "type": "verification_email",
                "status": "pending",
            },
        )


@pytest.fixture
def endpoint(monkeypatch: pytest.MonkeyPatch) -> Endpoint:
    endpoint = Endpoint()
    original = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(endpoint)
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    monkeypatch.setattr(mgmt, "_MGMT_MAX_ATTEMPTS", 1)
    return endpoint


def check(response: httpx.Response, status: int) -> None:
    assert response.status_code == status, response.text
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "verified,status,outcome",
    [(False, 202, "accepted"), (True, 200, "already_verified")],
)
def test_real_helper_results(
    endpoint: Endpoint, verified: bool, status: int, outcome: str
) -> None:
    endpoint.user["email_verified"] = verified
    response = TestClient(app).post(PATH)
    check(response, status)
    assert response.json()["outcome"] == outcome
    assert set(response.json()) == {"outcome", "message"}
    assert endpoint.calls == (
        ["token", "lookup"] if verified else ["token", "lookup", "job"]
    )


@pytest.mark.parametrize(
    "field,value,status",
    [
        ("user_id", "auth0|other", 502),
        ("email_verified", None, 502),
        ("email", 42, 502),
        ("email", None, 400),
        ("email", "", 400),
        ("identities", [], 502),
        ("identities", [{"provider": "google-oauth2", "connection": "google"}], 400),
    ],
)
def test_live_eligibility(
    endpoint: Endpoint, field: str, value: Any, status: int
) -> None:
    endpoint.user[field] = value
    check(TestClient(app).post(PATH), status)
    assert endpoint.calls == ["token", "lookup"]


@pytest.mark.parametrize("stage", ["token", "lookup", "job"])
def test_upstream_throttling(endpoint: Endpoint, stage: str) -> None:
    endpoint.fail_at = stage
    response = TestClient(app).post(PATH)
    check(response, 503)
    assert response.headers["retry-after"] == "8"
    assert endpoint.calls.count("job") <= 1
    if stage == "token":
        again = TestClient(app).post(PATH)
        check(again, 503)
        assert int(again.headers["retry-after"]) > 0
        assert endpoint.calls == ["token"]


@pytest.mark.parametrize(
    "body", [b"{}", b"null", b" ", b"invalid", b'{"email":"private@example.test"}']
)
def test_body_rejected_before_quota(endpoint: Endpoint, body: bytes) -> None:
    check(TestClient(app).post(PATH, content=body), 422)
    assert endpoint.calls == []
    assert not ratelimit._rate_limit_buckets


def test_query_rejected_before_quota(endpoint: Endpoint) -> None:
    check(TestClient(app).post(PATH + "?email=private"), 422)
    assert endpoint.calls == []
    assert not ratelimit._rate_limit_buckets


@pytest.mark.parametrize(
    "sub",
    [
        "auth0|",
        "auth0|a|b",
        "auth0|a@clients",
        "auth0|a b",
        "google-oauth2|a",
        "github|a",
        "samlp|a",
        "email|a",
    ],
)
def test_bad_subject_charges_quota_without_outbound(
    endpoint: Endpoint, sub: str
) -> None:
    authorize(sub)
    client = TestClient(app)
    for _ in range(3):
        check(client.post(PATH), 400)
    check(client.post(PATH), 429)
    assert endpoint.calls == []


@pytest.mark.parametrize("issuer", [None, "https://foreign.auth0.com/"])
def test_foreign_issuer_is_free(endpoint: Endpoint, issuer: str | None) -> None:
    authorize(issuer=issuer)
    check(TestClient(app).post(PATH), 403)
    assert not endpoint.calls
    assert not ratelimit._rate_limit_buckets


def test_unconfigured_is_free(
    endpoint: Endpoint, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings().auth0, "management_client_secret", "")
    check(TestClient(app).post(PATH), 503)
    assert not endpoint.calls
    assert not ratelimit._rate_limit_buckets


@pytest.mark.parametrize("failure", [False, True])
def test_quota_is_not_refunded(endpoint: Endpoint, failure: bool) -> None:
    endpoint.user["email_verified"] = True
    if failure:
        endpoint.fail_at, endpoint.status = "lookup", 500
    client = TestClient(app)
    for _ in range(3):
        authorize()  # A refreshed principal with the same issuer/subject shares the quota.
        check(client.post(PATH), 502 if failure else 200)
    calls = endpoint.calls.copy()
    response = client.post(PATH)
    check(response, 429)
    assert int(response.headers["retry-after"]) > 0
    assert endpoint.calls == calls


def test_authentication_errors_keep_privacy_and_headers(endpoint: Endpoint) -> None:
    app.dependency_overrides.pop(get_current_user)
    response = TestClient(app).post(PATH)
    check(response, 401)
    assert "Bearer" in response.headers["www-authenticate"]

    def unavailable() -> None:
        raise HTTPException(
            503, "Authentication unavailable", headers={"Retry-After": "4"}
        )

    app.dependency_overrides[get_current_user] = unavailable
    response = TestClient(app).post(PATH)
    check(response, 503)
    assert response.headers["retry-after"] == "4"
    assert not endpoint.calls


def test_logs_and_response_do_not_leak(
    endpoint: Endpoint, caplog: pytest.LogCaptureFixture
) -> None:
    endpoint.fail_at, endpoint.status = "job", 500
    with caplog.at_level(logging.DEBUG):
        response = TestClient(app).post(PATH)
    check(response, 502)
    for secret in [
        SUB,
        "private@example.test",
        "private-secret",
        "private-token",
        "private upstream material",
        "private-job",
        "/api/v2/users/",
    ]:
        assert secret not in caplog.text + response.text
    assert endpoint.calls == ["token", "lookup", "job"]


@pytest.mark.asyncio
async def test_stream_stops_at_first_chunk() -> None:
    calls = 0

    async def receive() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        assert calls == 1
        return {"type": "http.request", "body": b"x", "more_body": True}

    request = Request({"type": "http", "headers": [], "query_string": b""}, receive)
    with pytest.raises(HTTPException) as exc:
        await router.resend_verification(
            request, Response(), AuthenticatedUser(sub=SUB, issuer=ISSUER)
        )
    assert exc.value.status_code == 422
    assert calls == 1


@pytest.mark.asyncio
async def test_cancellation_and_logging_are_context_local(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()

    async def submit(sub: str) -> mgmt.ResendOutcome:
        logging.getLogger("httpx").info("private inside resend")
        entered.set()
        await release.wait()
        return mgmt.ResendOutcome.ACCEPTED

    monkeypatch.setattr(router, "resend_auth0_verification_email", submit)

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    request = Request({"type": "http", "headers": [], "query_string": b""}, receive)
    with caplog.at_level(logging.INFO, logger="httpx"):
        task = asyncio.create_task(
            router.resend_verification(
                request, Response(), AuthenticatedUser(sub=SUB, issuer=ISSUER)
            )
        )
        await entered.wait()
        logging.getLogger("httpx").info("unrelated concurrent request")
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        logging.getLogger("httpx").info("after cancellation")
    assert "private inside resend" not in caplog.text
    assert "unrelated concurrent request" in caplog.text
    assert "after cancellation" in caplog.text


def test_quota_isolation_rollover_and_disable(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings().ratelimit
    monkeypatch.setattr(settings, "verification_email_per_window", 1)
    now = [1800000000.0]
    monkeypatch.setattr(ratelimit, "time", SimpleNamespace(time=lambda: now[0]))
    request = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1)})
    user = AuthenticatedUser(sub=SUB, issuer=ISSUER)
    ratelimit.verification_email_rate_limit(request, user)
    with pytest.raises(HTTPException):
        ratelimit.verification_email_rate_limit(request, user)
    for fn in [
        ratelimit.workflow_rate_limit,
        ratelimit.compatibility_rate_limit,
        ratelimit.validation_rate_limit,
        ratelimit.password_reset_rate_limit,
    ]:
        fn(request, user)
    ratelimit.page_rate_limit(request)
    ratelimit.legacy_rate_limit(request)
    ratelimit.verification_email_rate_limit(
        request, AuthenticatedUser(sub=SUB, issuer="https://other/")
    )
    now[0] += 600
    ratelimit.verification_email_rate_limit(request, user)
    monkeypatch.setattr(settings, "enabled", False)
    ratelimit.verification_email_rate_limit(request, user)


def test_quota_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import ValidationError

    settings = RateLimitSettings()
    assert settings.verification_email_per_window == 3
    assert settings.verification_email_window_seconds == 600
    for key in [
        "RATE_LIMIT_VERIFICATION_EMAIL_PER_WINDOW",
        "RATE_LIMIT_VERIFICATION_EMAIL_WINDOW_SECONDS",
    ]:
        with pytest.raises(ValidationError):
            RateLimitSettings.model_validate({key: 0})
        monkeypatch.setenv(key, "7")
    settings = RateLimitSettings()
    assert (
        settings.verification_email_per_window
        == settings.verification_email_window_seconds
        == 7
    )


def test_openapi_contract() -> None:
    operation = app.openapi()["paths"][PATH]["post"]
    assert operation["operationId"] == "resend-verification-email"
    assert "requestBody" not in operation
    assert not operation.get("parameters")
    for status in [200, 202, 400, 401, 403, 422, 429, 502, 503]:
        assert "Cache-Control" in operation["responses"][str(status)]["headers"]


@pytest.mark.parametrize(
    "case,expected",
    [
        ("local", 202),
        ("foreign", 403),
        ("wrong-audience", 401),
        ("bad-signature", 401),
        ("expired", 401),
        ("missing-exp", 401),
        ("malformed-bearer", 401),
    ],
)
def test_signed_token_composition(
    monkeypatch: pytest.MonkeyPatch, case: str, expected: int
) -> None:
    import json
    from biosim_server.common.auth.auth0 import JwksCache
    from tests.fixtures.auth_seam import (
        clear_auth_overrides,
        install_auth_seam,
        make_auth0_settings,
    )
    from tests.fixtures.jwks_fixtures import jwks_document, make_key

    local_key, foreign_key = make_key("local"), make_key("foreign")
    foreign_issuer = "https://foreign.auth0.com/"
    audience = "https://api.example.test/"
    config = {
        "domain": "tenant.auth0.com",
        "issuer": "",
        "audience": "",
        "jwks_uri": "",
        "trusted_issuers_json": json.dumps(
            {
                ISSUER: {
                    "audiences": [audience],
                    "jwks_uri": ISSUER + ".well-known/jwks.json",
                },
                foreign_issuer: {
                    "audiences": [audience],
                    "jwks_uri": foreign_issuer + ".well-known/jwks.json",
                },
            }
        ),
    }
    app.dependency_overrides.pop(get_current_user, None)
    install_auth_seam(
        monkeypatch, settings=make_auth0_settings(**config), cache=JwksCache(), app=app
    )
    endpoint = Endpoint()
    original = httpx.AsyncClient

    async def transport(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/jwks.json":
            return httpx.Response(
                200,
                json=jwks_document(
                    foreign_key
                    if request.url.host == "foreign.auth0.com"
                    else local_key
                ),
            )
        return await endpoint(request)

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(transport)
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    key = foreign_key if case in {"foreign", "bad-signature"} else local_key
    claims: dict[str, Any] = {
        "sub": SUB,
        "issuer": foreign_issuer if case == "foreign" else ISSUER,
        "audience": "wrong" if case == "wrong-audience" else audience,
    }
    if case == "expired":
        claims["expires_in"] = -120
    token = key.token(**claims)
    if case == "missing-exp":
        from jose import jwt  # type: ignore[import-untyped]

        payload = jwt.get_unverified_claims(token)
        payload.pop("exp")
        token = jwt.encode(
            payload, key.private_pem, algorithm="RS256", headers={"kid": key.kid}
        )
    header = "Bearer" if case == "malformed-bearer" else f"Bearer {token}"
    try:
        response = TestClient(app).post(PATH, headers={"Authorization": header})
        check(response, expected)
        assert endpoint.calls == (["token", "lookup", "job"] if expected == 202 else [])
        if expected != 202:
            assert not ratelimit._rate_limit_buckets
    finally:
        clear_auth_overrides(app)
