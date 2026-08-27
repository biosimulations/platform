"""#19c: /ready surfaces auth (JWKS cache) health as non-gating information.

Verifies the read-only cache-status accessor across states, that /ready reports
the auth field without gating on it, and -- the load-bearing property -- that
/ready triggers no outbound JWKS fetch.
"""

import httpx
import pytest
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

from biosim_server.api.main import app
from biosim_server.common.auth import auth0 as auth0_module
from biosim_server.common.auth.auth0 import get_current_user, jwks_cache_status
from tests.fixtures.auth_seam import clear_auth_overrides, install_auth_seam
from tests.fixtures.jwks_fixtures import FakeClock, FakeJwksEndpoint, jwks_document, make_key


def test_cold_cache_reports_no_keys_and_is_not_usable(monkeypatch: pytest.MonkeyPatch) -> None:
    install_auth_seam(monkeypatch)
    monkeypatch.setattr(auth0_module, "time", FakeClock())
    status = jwks_cache_status()
    assert status == {"state": "no_keys_cached", "usable": False, "backoff_armed": False}


@pytest.mark.asyncio
async def test_warm_cache_reports_fresh_and_usable(monkeypatch: pytest.MonkeyPatch) -> None:
    key = make_key("ready-key")
    endpoint = FakeJwksEndpoint(responses=[lambda: jwks_document(key)])
    install_auth_seam(monkeypatch)
    clock = FakeClock()
    monkeypatch.setattr(httpx, "AsyncClient", endpoint.client_factory())
    monkeypatch.setattr(auth0_module, "time", clock)

    # Populate the cache through the real validation path (one fetch).
    await get_current_user(HTTPAuthorizationCredentials(scheme="Bearer", credentials=key.token()))
    assert jwks_cache_status() == {"state": "fresh", "usable": True, "backoff_armed": False}

    # Past the TTL but within the staleness bound -> still servable.
    clock.advance(auth0_module._JWKS_TTL_SECONDS + 1)
    assert jwks_cache_status()["state"] == "stale_servable"
    assert jwks_cache_status()["usable"] is True

    # Past the staleness bound -> no longer usable.
    clock.advance(auth0_module._JWKS_STALE_MAX_AGE_SECONDS)
    assert jwks_cache_status()["state"] == "expired"
    assert jwks_cache_status()["usable"] is False


def test_ready_reports_auth_info_without_gating_and_makes_no_jwks_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_auth_seam(monkeypatch, app=app)
    monkeypatch.setattr(auth0_module, "time", FakeClock())

    # Any outbound httpx use during /ready is a bug: /ready must be
    # side-effect-free with respect to the identity provider.
    def _boom(*_a: object, **_k: object) -> object:
        raise AssertionError("/ready must not make an outbound HTTP call")

    monkeypatch.setattr(httpx, "AsyncClient", _boom)

    try:
        resp = TestClient(app).get("/ready")
        body = resp.json()
        # Auth health is reported as information, separate from the gating `checks`.
        assert "auth" in body["info"]
        assert body["info"]["auth"]["state"] == "no_keys_cached"
        assert "auth" not in body["checks"]  # non-gating: never in the gate set
    finally:
        clear_auth_overrides(app)
