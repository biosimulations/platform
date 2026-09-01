"""GET /ontologies/KISAO/{id} — cached term lookup with a local fallback.

A log names its algorithm with an underscore id (`KISAO_0000019`) while the
vendored table and OLS use a colon (`KISAO:0000019`). Normalization happens in
one place; these tests pin that, the cache, and the fallback.
"""

from typing import Any
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from fastapi.testclient import TestClient

from biosim_server.api.main import app
from biosim_server.biosim_runs.biosim_service import BiosimServiceRest
from biosim_server.common.biosim_api import (
    KisaoTerm,
    kisao_ols_url,
    local_kisao_term,
    normalize_kisao_id,
    upstream_kisao_id,
)
from tests.legacy_proxy.upstream_stub import stub_session, upstream_error

# Present in the vendored KISAO_TERMS table (name "CVODE").
_KNOWN_UNDERSCORE = "KISAO_0000019"
_KNOWN_COLON = "KISAO:0000019"
_UNKNOWN = "KISAO_9999999"

_TERM_JSON: dict[str, Any] = {
    "id": _KNOWN_COLON,
    "name": "CVODE",
    "url": "https://www.ebi.ac.uk/ols4/ontologies/kisao/terms?obo_id=KISAO:0000019",
    "description": "A variable-order, variable-step BDF/Adams solver.",
}


async def _clear_cache() -> None:
    """The TTL cache is process-global, so cache-touching tests reset it first.

    Called explicitly rather than via an autouse fixture: an async fixture
    applied to the sync route tests below is a pytest-9 error, and those tests
    mock the service and never reach the cache anyway.
    """
    await BiosimServiceRest._fetch_kisao_term.cache.clear()


# --------------------------------------------------------------------------
# normalization
# --------------------------------------------------------------------------


def test_both_id_spellings_normalize_consistently() -> None:
    assert normalize_kisao_id(_KNOWN_UNDERSCORE) == _KNOWN_COLON
    assert normalize_kisao_id(_KNOWN_COLON) == _KNOWN_COLON
    assert upstream_kisao_id(_KNOWN_COLON) == _KNOWN_UNDERSCORE
    assert upstream_kisao_id(_KNOWN_UNDERSCORE) == _KNOWN_UNDERSCORE
    # Only the prefix separator is rewritten -- an id body is left alone.
    assert normalize_kisao_id("  KISAO_0000019  ") == _KNOWN_COLON
    assert normalize_kisao_id("not-a-kisao-id") == "not-a-kisao-id"


def test_ols_url_uses_the_colon_form() -> None:
    assert kisao_ols_url(_KNOWN_UNDERSCORE).endswith("obo_id=KISAO:0000019")
    assert kisao_ols_url(_KNOWN_COLON) == kisao_ols_url(_KNOWN_UNDERSCORE)


def test_local_term_supplies_name_and_url_but_not_description() -> None:
    """The vendored table stores name + ancestors only -- no definitions."""
    term = local_kisao_term(_KNOWN_UNDERSCORE)
    assert term is not None
    assert term.name == "CVODE"
    assert term.id == _KNOWN_COLON
    assert term.url == kisao_ols_url(_KNOWN_COLON)
    assert term.description is None
    assert local_kisao_term(_KNOWN_COLON) == term
    assert local_kisao_term(_UNKNOWN) is None


# --------------------------------------------------------------------------
# client: upstream, fallback, cache
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upstream_lookup_returns_all_four_fields() -> None:
    await _clear_cache()
    patcher, session = stub_session(_TERM_JSON)
    with patcher:
        term = await BiosimServiceRest().get_kisao_term(_KNOWN_UNDERSCORE)
    session.get.assert_called_once_with(
        f"https://api.biosimulations.org/ontologies/KISAO/{_KNOWN_UNDERSCORE}"
    )
    assert (term.id, term.name) == (_KNOWN_COLON, "CVODE")
    assert term.description == "A variable-order, variable-step BDF/Adams solver."
    assert term.url is not None


@pytest.mark.asyncio
async def test_both_spellings_hit_the_same_upstream_url() -> None:
    await _clear_cache()
    patcher, session = stub_session(_TERM_JSON)
    with patcher:
        await BiosimServiceRest().get_kisao_term(_KNOWN_COLON)
    session.get.assert_called_once_with(
        f"https://api.biosimulations.org/ontologies/KISAO/{_KNOWN_UNDERSCORE}"
    )


@pytest.mark.asyncio
async def test_repeat_lookup_is_served_from_cache() -> None:
    """The same algorithm id repeats across every level of a log."""
    await _clear_cache()
    service = BiosimServiceRest()
    patcher, session = stub_session(_TERM_JSON)
    with patcher:
        first = await service.get_kisao_term(_KNOWN_UNDERSCORE)
        second = await service.get_kisao_term(_KNOWN_UNDERSCORE)
        # The colon spelling normalizes onto the same cache key.
        third = await service.get_kisao_term(_KNOWN_COLON)
    assert first == second == third
    session.get.assert_called_once()


@pytest.mark.asyncio
async def test_falls_back_to_the_local_table_when_upstream_is_down() -> None:
    await _clear_cache()
    with patch.object(
        BiosimServiceRest,
        "_get_biosim_json",
        side_effect=aiohttp.ClientConnectionError("Cannot connect to host"),
    ):
        term = await BiosimServiceRest().get_kisao_term(_KNOWN_UNDERSCORE)
    assert term.name == "CVODE"
    assert term.url == kisao_ols_url(_KNOWN_COLON)
    # No description is available locally; a placeholder would be a fabrication.
    assert term.description is None


@pytest.mark.asyncio
async def test_falls_back_when_upstream_404s_but_the_term_is_known_locally() -> None:
    await _clear_cache()
    with patch.object(BiosimServiceRest, "_get_biosim_json", side_effect=upstream_error(404)):
        term = await BiosimServiceRest().get_kisao_term(_KNOWN_COLON)
    assert term.name == "CVODE"


@pytest.mark.asyncio
async def test_unknown_upstream_and_locally_reraises() -> None:
    await _clear_cache()
    with patch.object(BiosimServiceRest, "_get_biosim_json", side_effect=upstream_error(404)):
        with pytest.raises(aiohttp.ClientResponseError):
            await BiosimServiceRest().get_kisao_term(_UNKNOWN)


@pytest.mark.asyncio
async def test_a_degraded_fallback_is_not_cached() -> None:
    """A local fallback must not pin itself for the cache TTL."""
    await _clear_cache()
    service = BiosimServiceRest()
    with patch.object(
        BiosimServiceRest, "_get_biosim_json", side_effect=aiohttp.ClientConnectionError("down")
    ):
        fallback = await service.get_kisao_term(_KNOWN_UNDERSCORE)
    assert fallback.description is None

    patcher, _ = stub_session(_TERM_JSON)
    with patcher:
        recovered = await service.get_kisao_term(_KNOWN_UNDERSCORE)
    assert recovered.description == "A variable-order, variable-step BDF/Adams solver."


# --------------------------------------------------------------------------
# route
# --------------------------------------------------------------------------


def test_kisao_route_returns_the_term() -> None:
    biosim = AsyncMock()
    biosim.get_kisao_term.return_value = KisaoTerm.model_validate(_TERM_JSON)
    with patch("biosim_server.legacy_proxy.router.get_biosim_service", return_value=biosim):
        response = TestClient(app).get(f"/ontologies/KISAO/{_KNOWN_UNDERSCORE}")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "CVODE"
    assert body["id"] == _KNOWN_COLON
    biosim.get_kisao_term.assert_awaited_once_with(_KNOWN_UNDERSCORE)


def test_kisao_route_404s_for_an_unresolvable_term() -> None:
    biosim = AsyncMock()
    biosim.get_kisao_term.side_effect = upstream_error(404)
    with patch("biosim_server.legacy_proxy.router.get_biosim_service", return_value=biosim):
        response = TestClient(app).get(f"/ontologies/KISAO/{_UNKNOWN}")
    assert response.status_code == 404
    assert _UNKNOWN in response.json()["detail"]


def test_kisao_route_without_biosim_service_is_503() -> None:
    with patch("biosim_server.legacy_proxy.router.get_biosim_service", return_value=None):
        response = TestClient(app).get(f"/ontologies/KISAO/{_KNOWN_UNDERSCORE}")
    assert response.status_code == 503
