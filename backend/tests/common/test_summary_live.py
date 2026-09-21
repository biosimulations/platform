"""Live summary contract checks against the public legacy source."""

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from biosim_server.api.main import app
from biosim_server.config import get_settings
from biosim_server.summaries.mapping import map_project_summary, map_run_summary
from biosim_server.dependencies import get_http_client, set_http_client

# A published, archived 2022 record. Its id and payload are stable; the project
# and the run are the same study, so one pair of ids covers both endpoints.
PROJECT_ID = "Yeast-cell-cycle-Irons-J-Theor-Biol-2009"
RUN_ID = "61fea483f499ccf25faafc4d"

# Any absent run id -- malformed or a well-formed but unused ObjectId -- makes
# the upstream answer 500 rather than 404. Verified against the live API; this
# is the case our 5xx -> 502 mapping exists for.
ABSENT_RUN_ID = "000000000000000000000000"

FIXTURE = Path(__file__).parents[1] / "fixtures" / "local_data" / "run_summary_response.json"

CREDENTIALS = {"Authorization": "Bearer not-a-real-token", "Cookie": "session=not-a-real-session"}


@pytest.fixture(autouse=True)
def clear_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.pop(get_http_client, None)


def live_clients() -> tuple[AsyncClient, httpx.AsyncClient]:
    """Caller-facing client over the real app, plus the real upstream client it uses.

    The upstream client is built the way ``get_http_client`` builds it, but owned
    by the test so it is closed deterministically instead of left on the module
    global. ``test_pooled_client_targets_the_configured_upstream`` covers the
    construction itself.
    """
    upstream = httpx.AsyncClient(
        base_url=get_settings().biosimulations_api_base_url.rstrip("/"), timeout=30.0
    )
    app.dependency_overrides[get_http_client] = lambda: upstream
    return (
        AsyncClient(transport=ASGITransport(app=app), base_url="http://platform.test"),
        upstream,
    )


@pytest.mark.asyncio
async def test_pooled_client_targets_the_configured_upstream() -> None:
    """Covers the lazy construction that ``live_clients`` deliberately bypasses.

    Makes no network call. Resets the module global afterwards so the pooled
    client it creates cannot leak into other tests in the same session.
    """
    client = get_http_client()
    try:
        assert str(client.base_url) == get_settings().biosimulations_api_base_url.rstrip("/")
    finally:
        await client.aclose()
        set_http_client(None)


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["project", "run"])
async def test_live_owned_contract_and_fixture_drift(kind: str) -> None:
    import json

    path = f"/projects/{PROJECT_ID}/summary" if kind == "project" else f"/runs/{RUN_ID}/summary"
    mapper = map_project_summary if kind == "project" else map_run_summary
    caller, upstream = live_clients()
    async with caller, upstream:
        owned = await caller.get(path)
        direct = await upstream.get(path)
    assert owned.status_code == direct.status_code == 200
    expected = mapper(direct.json()).model_dump(by_alias=True)
    assert owned.json() == expected
    fixture = FIXTURE.with_name(f"{kind}_summary_response.json")
    # Compare mapped leaves: irrelevant upstream additions are deliberately allowed.
    assert mapper(json.loads(fixture.read_text())).model_dump(by_alias=True) == expected
    run = expected["simulationRun"] if kind == "project" else expected
    assert run["id"] == RUN_ID
    assert run["run"]["simulator"] == {"name": "BoolNet", "version": "2.1.5"}
    assert run["metadata"][0]["thumbnails"] == ["Figure2.jpg"]
    assert set(run) == {"id", "name", "run", "metadata"}
    assert "status" not in run["run"]
    assert "owner" not in expected


@pytest.mark.integration
@pytest.mark.asyncio
async def test_caller_credentials_do_not_reach_upstream() -> None:
    caller, upstream = live_clients()
    seen: list[httpx.Request] = []

    async def record(request: httpx.Request) -> None:
        seen.append(request)

    upstream.event_hooks["request"].append(record)
    async with caller, upstream:
        anonymous = await caller.get(f"/runs/{RUN_ID}/summary")
        credentialed = await caller.get(f"/runs/{RUN_ID}/summary", headers=CREDENTIALS)
    assert credentialed.status_code == anonymous.status_code == 200
    assert credentialed.json() == anonymous.json()
    assert len(seen) == 2
    assert all("authorization" not in request.headers and "cookie" not in request.headers for request in seen)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_absent_run_upstream_5xx_becomes_sanitized_502() -> None:
    caller, upstream = live_clients()
    async with caller, upstream:
        direct = await upstream.get(f"/runs/{ABSENT_RUN_ID}/summary")
        response = await caller.get(f"/runs/{ABSENT_RUN_ID}/summary")
    assert direct.status_code >= 500
    assert response.status_code == 502
    assert response.json() == {"detail": "The upstream service failed while loading the run summary."}
    assert ABSENT_RUN_ID not in response.text
