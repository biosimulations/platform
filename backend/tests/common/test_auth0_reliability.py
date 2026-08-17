"""
Multi-phase reliability scenarios for the JWKS cache.

tests/common/test_auth0_jwks.py pins individual behaviours -- backoff, stale
service, cooldown -- one at a time. This module runs them as timelines: a
sustained outage with recovery, a rotation during an outage, a cold start
during an outage. Those interactions are where cache state machines actually
break, and they are exactly what happens during a real incident.

Hermetic: local RSA keys, a fake JWKS endpoint, a fake clock. No container, no
network, no sleeping.
"""


import asyncio
from typing import Any, Iterator

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from biosim_server.common.auth import auth0 as auth0_module
from biosim_server.common.auth.auth0 import get_current_user
from biosim_server.config import get_settings
from tests.fixtures.jwks_fixtures import (
    AUDIENCE,
    ISSUER,
    FakeClock,
    FakeJwksEndpoint,
    connect_error,
    jwks_document,
    make_key,
)

OLD_KEY = make_key("kid-old")
NEW_KEY = make_key("kid-new")

@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    auth0_module._reset_jwks_cache()
    yield
    auth0_module._reset_jwks_cache()

@pytest.fixture
def auth_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings().auth0
    monkeypatch.setattr(settings, "domain", "")
    monkeypatch.setattr(settings, "issuer", ISSUER)
    monkeypatch.setattr(settings, "jwks_uri", "https://idp.invalid/.well-known/jwks.json")
    monkeypatch.setattr(settings, "audience", AUDIENCE)

def _install(
    monkeypatch: pytest.MonkeyPatch, endpoint: FakeJwksEndpoint, clock: FakeClock
) -> None:
    monkeypatch.setattr(
        auth0_module.httpx,  # type: ignore[attr-defined]
        "AsyncClient",
        endpoint.client_factory(),
    )
    monkeypatch.setattr(auth0_module, "time", clock)

def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

class Scripted:
    """
    A JWKS endpoint whose behaviour is flipped mid-test.

    Models a real incident better than a fixed response queue: the endpoint is
    up, then down, then up again, while requests keep arriving throughout.
    """

    def __init__(self, document: dict[str, Any]) -> None:
        self.document = document
        self.up = True

    def __call__(self) -> dict[str, Any]:
        if not self.up:
            return connect_error()
        return self.document

@pytest.mark.asyncio
async def test_sustained_outage_then_recovery(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A 20-minute Auth0 outage, minute by minute.

    Phase 1  warm cache, IdP up          -> 200, 1 fetch
    Phase 2  IdP down, cache still fresh -> 200, 0 fetches (never even asked)
    Phase 3  IdP down, TTL expired       -> 200 from stale keys, 1 fetch per 10 s
    Phase 4  IdP back up                 -> 200, cache refreshed, backoff cleared
    """

    script = Scripted(jwks_document(OLD_KEY))

    endpoint = FakeJwksEndpoint(responses=[script])
    clock = FakeClock()
    _install(monkeypatch, endpoint, clock)

    # Phase 1: warm cache, IdP up -> 200, 1 fetch
    await get_current_user(_creds(OLD_KEY.token()))

    assert endpoint.call_count == 1

    # Phase 2: IdP down, cache still fresh -> 200, 0 fetches (never even asked)
    script.up = False
    for _ in range(30):
        await get_current_user(_creds(OLD_KEY.token()))

        clock.advance(60)

    fetches_after_phase_2 = endpoint.call_count

    # Phase 3: IdP down, TTL expired -> 200 from stale keys, 1 fetch per 10 s
    for _ in range(20):
        user = await get_current_user(_creds(OLD_KEY.token()))

        assert user.sub == "auth0|test-user"

        # Stale keys still verify
        clock.advance(1)

    assert endpoint.call_count <= fetches_after_phase_2 + 3

    # Phase 4: IdP back up -> 200, cache refreshed, backoff cleared
    script.up = True
    clock.advance(11)
    await get_current_user(_creds(OLD_KEY.token()))

    assert auth0_module._jwks_cache["last_failure_at"] == 0.0

    # Backoff cleared and the cache is fresh again: no further fetches for a while

    before = endpoint.call_count

    for _ in range(10):
        await get_current_user(_creds(OLD_KEY.token()))

        clock.advance(60)

    assert endpoint.call_count == before

@pytest.mark.asyncio
async def test_outbound_rate_is_bounded_regardless_of_inbound_load(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    1000 requests over a simulated 60 s outage must not produce 1000 fetches.

    The ceiling is 60 s / 10 s backoff = 6 attempts, plus the initial one.
    """
    endpoint = FakeJwksEndpoint(responses=[connect_error])
    clock = FakeClock()
    _install(monkeypatch, endpoint, clock)

    for _ in range(1000):
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(_creds(OLD_KEY.token()))
        assert exc_info.value.status_code == 503
        clock.advance(0.06)

    assert endpoint.call_count <= 7

@pytest.mark.asyncio
async def test_rotation_during_an_outage_recovers_when_the_idp_returns(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The worst realistic case: Auth0 rotates keys *while* it is unreachable.

    Old-key tokens keep working from the stale cache. New-key tokens 401 --
    correctly, since the backend has no way to learn the new key -- and start
    working as soon as the IdP is reachable again.
    """

    script = Scripted(jwks_document(OLD_KEY))
    endpoint = FakeJwksEndpoint(responses=[script])
    clock = FakeClock()
    _install(monkeypatch, endpoint, clock)

    await get_current_user(_creds(OLD_KEY.token()))

    # auth0 goes down and rotates while down
    script.up = False
    script.document = jwks_document(NEW_KEY)
    clock.advance(3601)

    # Old tokens still verify from the stale cache
    assert (await get_current_user(_creds(OLD_KEY.token()))).sub == "auth0|test-user"

    # New-key tokens cannot verify -- 401, never 500, never a hang
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(_creds(NEW_KEY.token()))
    assert exc_info.value.status_code == 401

    # Auth0 returns. Past both the backoff and the kid cooldown
    script.up = True
    clock.advance(61)
    user = await get_current_user(_creds(NEW_KEY.token(sub="auth0|new")))
    assert user.sub == "auth0|new"

@pytest.mark.asyncio
async def test_cold_start_during_an_outage_fails_closed_then_recovers(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A pod that restarts mid-outage has no stale keys to fall back on.

    503 is the correct answer, and recovery must be automatic -- no restart, no
    intervention -- once the IdP is reachable.
    """

    script = Scripted(jwks_document(OLD_KEY))
    script.up = False
    endpoint = FakeJwksEndpoint(responses=[script])
    clock = FakeClock()
    _install(monkeypatch, endpoint, clock)

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(_creds(OLD_KEY.token()))
    assert exc_info.value.status_code == 503

    script.up = True
    clock.advance(11)

    assert (await get_current_user(_creds(OLD_KEY.token()))).sub == "auth0|test-user"

@pytest.mark.asyncio
async def test_concurrent_requests_during_recovery_produce_one_fetch(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The recovery moment is the thundering-herd moment.

    Every waiting request wakes at once when the backoff expires; the lock plus
    the double check must collapse them into a single fetch.
    """

    script = Scripted(jwks_document(OLD_KEY))
    script.up = False
    endpoint = FakeJwksEndpoint(responses=[script])
    clock = FakeClock()
    _install(monkeypatch, endpoint, clock)

    with pytest.raises(HTTPException):
        await get_current_user(_creds(OLD_KEY.token()))
    fetches_during_outrage = endpoint.call_count

    script.up = True
    clock.advance(11)

    results = await asyncio.gather(
        *(get_current_user(_creds(OLD_KEY.token())) for _ in range(50))
    )

    assert len(results) == 50
    assert endpoint.call_count == fetches_during_outrage + 1

@pytest.mark.asyncio
async def test_staleness_bound_is_enforced_and_recovery_still_works_after_it(
    auth_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Past 24 h the cache stops being served -- but the system is not wedged."""

    script = Scripted(jwks_document(OLD_KEY))
    endpoint = FakeJwksEndpoint(responses=[script])
    clock = FakeClock()
    _install(monkeypatch, endpoint, clock)

    await get_current_user(_creds(OLD_KEY.token()))
    script.up = False
    clock.advance(86_401)

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(_creds(OLD_KEY.token()))
    assert exc_info.value.status_code == 503

    script.up = True
    clock.advance(11)

    assert (await get_current_user(_creds(OLD_KEY.token()))).sub == "auth0|test-user"
