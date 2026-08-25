"""
Startup-gate behaviour: a pod may not start with a half-configured Auth0 setup.

Two layers:
  * Auth0Settings.configuration_errors() -- pure, exhaustive, fast.
  * _validate_auth0_configuration() and lifespan -- the actual gate.

Note that httpx's ASGITransport does not run the ASGI lifespan protocol, so
the rest of the suite never executes `lifespan`; these tests drive it directly.
"""

from unittest.mock import AsyncMock, patch

import pytest

from biosim_server.api.main import _validate_auth0_configuration, app, lifespan
from biosim_server.config import Auth0Settings, get_settings


# --------------------------------------------------------------------------
# Auth0Settings.configuration_errors()
# --------------------------------------------------------------------------

def _settings(**overrides: object) -> Auth0Settings:
    """
    Build an Auth0Settings without reading the ambient environment.

    _env_file=None keeps a developer's backend/.env (which does set
    AUTH0_DOMAIN) from making these tests pass for the wrong reason.
    """

    base: dict[str, object] = {
        "AUTH0_DOMAIN": "tenant.us.auth0.com",
        "AUTH0_AUDIENCE": "https://api.example.test",
        "AUTH0_ISSUER": "",
        "AUTH0_JWKS_URI": "",
    }
    base.update(overrides)
    return Auth0Settings(_env_file=None, **base) # type: ignore[call-arg,arg-type]

def test_complete_configuration_has_no_errors() -> None:
    assert _settings().configuration_errors() == []

def test_explicit_issuer_jwks_overrides_are_accepted() -> None:
    """The shape tests/fixtures/keycloak uses: no domain, both overrides set."""
    errors = _settings(
        AUTH0_DOMAIN="",
        AUTH0_ISSUER="http://localhost:8080/realms/biosim-test",
        AUTH0_JWKS_URI="http://localhost:8080/realms/biosim-test/protocol/openid-connect/certs",
    ).configuration_errors()
    assert errors == []

def test_missing_domain_is_reported() -> None:
    errors = _settings(AUTH0_DOMAIN="").configuration_errors()
    assert any("AUTH0_DOMAIN" in e for e in errors)

def test_missing_audience_is_reported() -> None:
    errors = _settings(AUTH0_AUDIENCE="").configuration_errors()
    assert errors == ["AUTH0_AUDIENCE is not set"]

def test_empty_configuration_reports_every_problem_at_once() -> None:
    """
    An operator must be able to fix everything in one edit, not one per restart.
    """
    errors = _settings(AUTH0_DOMAIN="", AUTH0_AUDIENCE="").configuration_errors()
    assert len(errors) >= 3
    assert any("AUTH0_AUDIENCE" in e for e in errors)
    assert any("AUTH0_ISSUER" in e for e in errors)
    assert any("AUTH0_JWKS_URI" in e for e in errors)

def test_half_configured_override_is_reported() -> None:
    """AUTH0_ISSUER without AUTH0_JWKS_URI is not a valid configuration."""
    errors = _settings(
        AUTH0_DOMAIN="", AUTH0_ISSUER="https://issuer.example.test/"
    ).configuration_errors()
    assert any("AUTH0_JWKS_URI" in e for e in errors)
    assert not any("AUTH0_ISSUER" in e for e in errors)

@pytest.mark.parametrize(
    "bad_domain",
    [
        "https://tenant.us.auth0.com",
        "tenant.us.auth0.com/",
        "https://tenant.us.auth0.com/"
    ],
)
def test_url_shaped_domain_is_rejected(bad_domain: str) -> None:
    """The silent killer: a URL here yields an issuer no token can ever match."""
    errors = _settings(AUTH0_DOMAIN=bad_domain).configuration_errors()
    assert any("bare hostname" in e for e in errors)

@pytest.mark.parametrize("bad_domain", ["tenant", " tenant.us.auth0.com"])
def test_malformed_hostname_is_rejected(bad_domain: str) -> None:
    errors = _settings(AUTH0_DOMAIN=bad_domain).configuration_errors()
    assert any("does not look like a hostname" in e for e in errors)

# --------------------------------------------------------------------------
# The gate itself
# --------------------------------------------------------------------------

def test_gate_passes_with_valid_configuration(
        monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A complete configuration starts and records what it validated."""
    settings = get_settings().auth0
    monkeypatch.setattr(settings, "required", True)
    monkeypatch.setattr(settings, "domain", "tenant.us.auth0.com")
    monkeypatch.setattr(settings, "audience", "https://api.example.test")
    monkeypatch.setattr(settings, "issuer", "")
    monkeypatch.setattr(settings, "jwks_uri", "")

    with caplog.at_level("INFO"):
        _validate_auth0_configuration()
    assert "Auth0 configuration validated" in caplog.text

def test_gate_raises_when_required_and_misconfigured(
        monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The P0 #5 core assertion: no half-configured cluster may start."""
    settings = get_settings().auth0
    monkeypatch.setattr(settings, "required", True)
    monkeypatch.setattr(settings, "domain", "")
    monkeypatch.setattr(settings, "audience", "")
    monkeypatch.setattr(settings, "issuer", "")
    monkeypatch.setattr(settings, "jwks_uri", "")

    with pytest.raises(RuntimeError) as exc_info:
        _validate_auth0_configuration()

    message = str(exc_info.value)
    assert "refusing to start" in message
    assert "AUTH0_AUDIENCE" in message
    assert "AUTH0_DOMAIN" in message
    assert "AUTH_REQUIRED=false" in message


def test_gate_warns_but_starts_when_auth_is_deliberately_disabled(
        monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """AUTH_REQUIRED=false starts, and the warning states the real failure mode."""

    settings = get_settings().auth0
    monkeypatch.setattr(settings, "required", False)
    monkeypatch.setattr(settings, "domain", "")
    monkeypatch.setattr(settings, "audience", "")
    monkeypatch.setattr(settings, "issuer", "")
    monkeypatch.setattr(settings, "jwks_uri", "")

    with caplog.at_level("WARNING"):
        _validate_auth0_configuration()     # must not raise

    assert "AUTH_REQUIRED=false" in caplog.text
    assert "503" in caplog.text

    assert  "401" not in caplog.text


@pytest.mark.asyncio
async def test_lifespan_validates_before_opening_connections(
        monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A configuration error aborts startup before Mongo/Temporal are touched."""
    settings = get_settings().auth0
    monkeypatch.setattr(settings, "required", True)
    monkeypatch.setattr(settings, "domain", "")
    monkeypatch.setattr(settings, "audience", "")
    monkeypatch.setattr(settings, "issuer", "")
    monkeypatch.setattr(settings, "jwks_uri", "")

    with patch("biosim_server.api.main.init_standalone", new=AsyncMock()) as init_mock:
        with pytest.raises(RuntimeError):
            async with lifespan(app):
                pass # pragma: no cover -- startup must abort before this runs

    init_mock.assert_not_awaited()

@pytest.mark.asyncio
async def test_lifespan_proceeds_with_valid_configuration(
        monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The happy path still initialises and shuts down the service singletons."""
    settings = get_settings().auth0
    monkeypatch.setattr(settings, "required", True)
    monkeypatch.setattr(settings, "domain", "tenant.us.auth0.com")
    monkeypatch.setattr(settings, "audience", "https://api.example.test")
    monkeypatch.setattr(settings, "issuer", "")
    monkeypatch.setattr(settings, "jwks_uri", "")

    with patch("biosim_server.api.main.init_standalone", new=AsyncMock()) as init_mock:
        with patch(
            "biosim_server.api.main.shutdown_standalone", new=AsyncMock()
        ) as shutdown_mock:
            async with lifespan(app):
                init_mock.assert_awaited_once()
    shutdown_mock.assert_awaited_once()



