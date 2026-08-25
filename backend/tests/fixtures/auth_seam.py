"""#24 test seam: Auth0 settings + JWKS cache without mutating process globals."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from biosim_server.common.auth import auth0 as auth0_module
from biosim_server.common.auth.auth0 import JwksCache, get_auth0_settings, get_jwks_cache
from biosim_server.config import Auth0Settings, get_settings
from tests.fixtures.jwks_fixtures import AUDIENCE, ISSUER

DEFAULT_JWKS_URI = "https://idp.invalid/.well-known/jwks.json"


def make_auth0_settings(**overrides: Any) -> Auth0Settings:
    """A copy of process Auth0 settings with test issuer/JWKS/audience filled in.

    Uses ``model_construct`` so env is not re-read. Does not mutate the
    ``get_settings()`` singleton.
    """
    data = get_settings().auth0.model_dump()
    data.update(
        {
            "domain": "",
            "issuer": ISSUER,
            "jwks_uri": DEFAULT_JWKS_URI,
            "audience": AUDIENCE,
        }
    )
    data.update(overrides)
    return Auth0Settings.model_construct(**data)


def install_auth_seam(
    monkeypatch: pytest.MonkeyPatch,
    *,
    settings: Auth0Settings | None = None,
    cache: JwksCache | None = None,
    app: Any | None = None,
) -> tuple[Auth0Settings, JwksCache]:
    """Point auth getters (and optional FastAPI overrides) at a test settings/cache pair."""
    if settings is None:
        settings = make_auth0_settings()
    if cache is None:
        cache = JwksCache()
    monkeypatch.setattr(auth0_module, "get_auth0_settings", lambda: settings)
    monkeypatch.setattr(auth0_module, "get_jwks_cache", lambda: cache)
    if app is not None:
        app.dependency_overrides[get_auth0_settings] = lambda: settings
        app.dependency_overrides[get_jwks_cache] = lambda: cache
    return settings, cache


def clear_auth_overrides(app: Any) -> None:
    app.dependency_overrides.pop(get_auth0_settings, None)
    app.dependency_overrides.pop(get_jwks_cache, None)


@pytest.fixture
def auth_seam(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Auth0Settings, JwksCache]]:
    """Fresh Auth0 settings + JWKS cache for one test; restores overrides on teardown."""
    from biosim_server.api.main import app

    settings, cache = install_auth_seam(monkeypatch, app=app)
    yield settings, cache
    clear_auth_overrides(app)
