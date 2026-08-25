import json
import logging
from io import StringIO

import pytest
import httpx
from fastapi.security import HTTPAuthorizationCredentials

from biosim_server.common.auth import auth0 as auth0_module
from biosim_server.common.auth.auth0 import get_current_user, get_optional_user
from tests.fixtures.auth_seam import install_auth_seam
from biosim_server.log_config import JsonFormatter
from tests.fixtures.jwks_fixtures import FakeClock, FakeJwksEndpoint, jwks_document, make_key


def _install_seam(monkeypatch: pytest.MonkeyPatch) -> None:
    install_auth_seam(monkeypatch)


def _capture_auth_logs() -> tuple[StringIO, logging.Handler]:
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    # Both the auth0 outcome events and roles.py denial events must be caught.
    for name in ("biosim_server.common.auth.auth0", "biosim_server.common.auth.roles"):
        logging.getLogger(name).addHandler(handler)
    return stream, handler


def _remove_handler(handler: logging.Handler) -> None:
    for name in ("biosim_server.common.auth.auth0", "biosim_server.common.auth.roles"):
        logging.getLogger(name).removeHandler(handler)


def test_auth_log_formatter_preserves_bounded_fields_without_sensitive_values() -> None:
    token = "header.payload.signature"
    email = "person@example.test"
    raw_sub = "auth0|private-subject"
    record = logging.LogRecord(
        "biosim_server.common.auth.auth0",
        logging.INFO,
        __file__,
        1,
        "Authentication outcome",
        (),
        None,
    )
    record.auth_outcome = "denied"
    record.auth_reason = "invalid_token"
    record.auth_subject_hash = "b7c4cf3b2f8d"

    rendered = JsonFormatter().format(record)
    event = json.loads(rendered)
    assert event["auth_outcome"] == "denied"
    assert event["auth_reason"] == "invalid_token"
    assert event["auth_subject_hash"] == "b7c4cf3b2f8d"
    for secret in (token, email, raw_sub):
        assert secret not in rendered


@pytest.mark.asyncio
async def test_emitted_auth_log_never_contains_token_email_or_raw_subject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = make_key("observability-key")
    raw_sub = "auth0|private-subject"
    email = "person@example.test"
    token = key.token(sub=raw_sub, extra_claims={"email": email})
    endpoint = FakeJwksEndpoint(responses=[lambda: jwks_document(key)])
    _install_seam(monkeypatch)
    monkeypatch.setattr(httpx, "AsyncClient", endpoint.client_factory())
    monkeypatch.setattr(auth0_module, "time", FakeClock())

    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("biosim_server.common.auth.auth0")
    logger.addHandler(handler)
    try:
        await get_current_user(HTTPAuthorizationCredentials(scheme="Bearer", credentials=token))
    finally:
        logger.removeHandler(handler)

    rendered = stream.getvalue()
    assert '"auth_outcome":"success"' in rendered
    for secret in (token, email, raw_sub):
        assert secret not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case, expected_reason",
    [
        ("expired", "expired"),
        ("wrong_aud", "invalid_claims"),
        ("wrong_iss", "invalid_claims"),
        ("unknown_kid", "unknown_kid"),
        ("missing_sub", "missing_sub"),
        ("malformed", "malformed"),
    ],
)
async def test_each_denial_emits_its_bounded_reason_and_leaks_nothing(
    monkeypatch: pytest.MonkeyPatch, case: str, expected_reason: str
) -> None:
    """Every 401 path emits exactly one bounded `denied` reason, and no token,
    email, or raw subject appears in the emitted, formatted log output."""
    served = make_key("served-key")
    raw_sub = "auth0|private-subject"
    email = "person@example.test"
    claims = {"email": email}

    if case == "expired":
        token = served.token(sub=raw_sub, expires_in=-120, extra_claims=claims)
    elif case == "wrong_aud":
        token = served.token(sub=raw_sub, audience="https://wrong.example/", extra_claims=claims)
    elif case == "wrong_iss":
        token = served.token(sub=raw_sub, issuer="https://wrong.example/", extra_claims=claims)
    elif case == "unknown_kid":
        token = make_key("rotated-away-key").token(sub=raw_sub, extra_claims=claims)
    elif case == "missing_sub":
        token = served.token(sub=None, extra_claims=claims)
    else:  # malformed
        token = "not-a-jwt"

    endpoint = FakeJwksEndpoint(responses=[lambda: jwks_document(served)])
    _install_seam(monkeypatch)
    monkeypatch.setattr(httpx, "AsyncClient", endpoint.client_factory())
    monkeypatch.setattr(auth0_module, "time", FakeClock())

    stream, handler = _capture_auth_logs()
    try:
        with pytest.raises(Exception):
            await get_current_user(HTTPAuthorizationCredentials(scheme="Bearer", credentials=token))
    finally:
        _remove_handler(handler)

    rendered = stream.getvalue()
    assert '"auth_outcome":"denied"' in rendered
    assert f'"auth_reason":"{expected_reason}"' in rendered
    # Exactly one denial event for this outcome (no duplicate emission).
    assert rendered.count('"auth_outcome":"denied"') == 1
    for secret in (token, email, raw_sub):
        assert secret not in rendered


@pytest.mark.asyncio
async def test_optional_auth_downgrade_preserves_specific_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#19b: an invalid token on an optional-auth endpoint records the specific
    bounded reason (`expired`), not a generic `invalid_token`, and downgrades to
    anonymous without leaking the token."""
    served = make_key("served-key")
    raw_sub = "auth0|private-subject"
    token = served.token(sub=raw_sub, expires_in=-120)
    endpoint = FakeJwksEndpoint(responses=[lambda: jwks_document(served)])
    _install_seam(monkeypatch)
    monkeypatch.setattr(httpx, "AsyncClient", endpoint.client_factory())
    monkeypatch.setattr(auth0_module, "time", FakeClock())

    stream, handler = _capture_auth_logs()
    try:
        result = await get_optional_user(
            HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        )
    finally:
        _remove_handler(handler)

    assert result is None  # behaviour unchanged: caller treated as anonymous
    rendered = stream.getvalue()
    assert '"auth_outcome":"anonymous_downgrade"' in rendered
    assert '"auth_reason":"expired"' in rendered
    assert token not in rendered


@pytest.mark.asyncio
async def test_no_token_optional_auth_emits_no_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """A genuinely anonymous request (no bearer token) produces no auth event --
    silence distinguishes it from the invalid-token downgrade above."""
    stream, handler = _capture_auth_logs()
    try:
        result = await get_optional_user(None)
    finally:
        _remove_handler(handler)
    assert result is None
    assert stream.getvalue() == ""
