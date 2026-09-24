"""OIDC discovery resolver tests (P2 #16).

Exercises the three-tier precedence (explicit env -> discovery -> convention),
the explicit timeout, the negative-cache/backoff bound on outbound attempts,
single-flight, and the best-effort startup warm -- all with a fake discovery
endpoint and a fake clock, no container and no network.
"""

import asyncio

import httpx
import pytest

from biosim_server.common.auth import discovery as discovery_module
from biosim_server.common.auth.discovery import resolve_oidc, warm_discovery_cache
from biosim_server.config import Auth0Settings
from tests.fixtures.jwks_fixtures import FakeClock, FakeJwksEndpoint, connect_error

DISCOVERED_ISSUER = "https://tenant.us.auth0.com/"
DISCOVERED_JWKS = "https://tenant.us.auth0.com/.well-known/jwks.json"


def _discovery_doc() -> dict[str, str]:
    return {"issuer": DISCOVERED_ISSUER, "jwks_uri": DISCOVERED_JWKS}


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch) -> None:
    discovery_module._reset_discovery_cache()
    monkeypatch.setattr(discovery_module, "time", FakeClock())


def _domain_settings() -> Auth0Settings:
    # Domain-only config: issuer/jwks_uri explicitly empty (model_validate does
    # not read ambient env/.env, so this is hermetic) so discovery is consulted.
    return Auth0Settings.model_validate(
        {"domain": "tenant.us.auth0.com", "audience": "https://api/", "issuer": "", "jwks_uri": ""}
    )


@pytest.mark.asyncio
async def test_explicit_override_wins_without_any_discovery_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = FakeJwksEndpoint(responses=[_discovery_doc])
    monkeypatch.setattr(httpx, "AsyncClient", endpoint.client_factory())
    settings = Auth0Settings.model_validate(
        {
            "issuer": "https://explicit.example/",
            "jwks_uri": "https://explicit.example/keys",
            "audience": "https://api/",
        }
    )
    issuer, jwks_uri = await resolve_oidc(settings)
    assert (issuer, jwks_uri) == ("https://explicit.example/", "https://explicit.example/keys")
    assert endpoint.call_count == 0  # explicit override never touches the network


@pytest.mark.asyncio
async def test_discovery_used_when_reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    endpoint = FakeJwksEndpoint(responses=[_discovery_doc])
    monkeypatch.setattr(httpx, "AsyncClient", endpoint.client_factory())
    issuer, jwks_uri = await resolve_oidc(_domain_settings())
    assert issuer == DISCOVERED_ISSUER
    assert jwks_uri == DISCOVERED_JWKS
    assert endpoint.call_count == 1
    assert endpoint.calls[0] == "https://tenant.us.auth0.com/.well-known/openid-configuration"


@pytest.mark.asyncio
async def test_discovery_result_is_cached_no_second_request(monkeypatch: pytest.MonkeyPatch) -> None:
    endpoint = FakeJwksEndpoint(responses=[_discovery_doc])
    monkeypatch.setattr(httpx, "AsyncClient", endpoint.client_factory())
    settings = _domain_settings()
    await resolve_oidc(settings)
    await resolve_oidc(settings)
    assert endpoint.call_count == 1  # second call served from cache


@pytest.mark.asyncio
async def test_discovery_unreachable_falls_back_to_convention(monkeypatch: pytest.MonkeyPatch) -> None:
    endpoint = FakeJwksEndpoint(responses=[connect_error])
    monkeypatch.setattr(httpx, "AsyncClient", endpoint.client_factory())
    settings = _domain_settings()
    issuer, jwks_uri = await resolve_oidc(settings)
    # Convention (tier 3) -- exactly what issuer_url()/jwks_url() would return.
    assert issuer == settings.issuer_url()
    assert jwks_uri == settings.jwks_url()


@pytest.mark.asyncio
async def test_discovery_outage_is_bounded_by_the_negative_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    endpoint = FakeJwksEndpoint(responses=[connect_error])
    monkeypatch.setattr(httpx, "AsyncClient", endpoint.client_factory())
    settings = _domain_settings()
    # Many callers during an outage produce at most one outbound attempt per
    # backoff window, not one per call.
    for _ in range(10):
        await resolve_oidc(settings)
    assert endpoint.call_count == 1


@pytest.mark.asyncio
async def test_concurrent_cold_start_is_single_flight(monkeypatch: pytest.MonkeyPatch) -> None:
    endpoint = FakeJwksEndpoint(responses=[_discovery_doc])
    monkeypatch.setattr(httpx, "AsyncClient", endpoint.client_factory())
    settings = _domain_settings()
    results = await asyncio.gather(*(resolve_oidc(settings) for _ in range(8)))
    assert all(r == (DISCOVERED_ISSUER, DISCOVERED_JWKS) for r in results)
    assert endpoint.call_count == 1  # eight concurrent callers, one fetch


@pytest.mark.asyncio
async def test_warm_cache_never_raises_on_outage(monkeypatch: pytest.MonkeyPatch) -> None:
    endpoint = FakeJwksEndpoint(responses=[connect_error])
    monkeypatch.setattr(httpx, "AsyncClient", endpoint.client_factory())
    # Must not raise -- discovery is never a hard startup dependency.
    await warm_discovery_cache(_domain_settings())
