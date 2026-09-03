"""Live-upstream contract tests for the passthrough proxy helper.

These call the real biosimulations.org API, so they carry the ``integration``
marker and are deselected by the default ``-m "not integration"`` run. Their job
is to catch upstream contract drift that the MockTransport suites cannot see:
the mocked tests pin what we do with a response, these pin what we actually get.

Assertions compare the proxied response against a direct upstream fetch rather
than against hard-coded payloads, so a content change upstream does not fail
them -- only a change in passthrough behaviour does.

No Mongo or Temporal is needed: ASGITransport does not run the app's lifespan.
"""

import json
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from biosim_server.api.main import app
from biosim_server.config import get_settings
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
@pytest.mark.parametrize(
    ("label", "path"),
    [
        ("project", f"/projects/{PROJECT_ID}/summary"),
        ("run", f"/runs/{RUN_ID}/summary"),
    ],
)
async def test_summary_is_byte_identical_to_upstream(label: str, path: str) -> None:
    caller, upstream = live_clients()
    async with caller, upstream:
        proxied = await caller.get(path)
        direct = await upstream.get(path)

    assert proxied.status_code == direct.status_code == 200, label
    assert proxied.content == direct.content, f"{label} body diverged from upstream"
    assert proxied.headers["content-type"] == direct.headers["content-type"]
    assert proxied.headers["content-length"] == str(len(direct.content))
    # Upstream sends a weak ETag; it must survive, since we return its exact bytes.
    if "etag" in direct.headers:
        assert proxied.headers["etag"] == direct.headers["etag"]
    for unsafe in ("set-cookie", "x-powered-by", "server", "connection", "content-encoding"):
        assert unsafe not in proxied.headers, f"{label} leaked {unsafe}"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_committed_fixture_still_matches_live_upstream() -> None:
    """Guards tests/fixtures/local_data/run_summary_response.json against drift."""
    caller, upstream = live_clients()
    async with caller, upstream:
        direct = await upstream.get(f"/runs/{RUN_ID}/summary")

    assert direct.status_code == 200
    assert FIXTURE.read_bytes() == direct.content, (
        "The committed run-summary capture no longer matches the live upstream. "
        "Re-capture it rather than loosening the byte-fidelity assertions."
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_caller_credentials_do_not_reach_upstream() -> None:
    caller, upstream = live_clients()
    async with caller, upstream:
        anonymous = await caller.get(f"/runs/{RUN_ID}/summary")
        credentialed = await caller.get(f"/runs/{RUN_ID}/summary", headers=CREDENTIALS)

    # A forwarded bogus token would change the upstream's answer; an identical
    # response is evidence the credentials were dropped before the upstream call.
    assert credentialed.status_code == anonymous.status_code == 200
    assert credentialed.content == anonymous.content
    assert "set-cookie" not in credentialed.headers


@pytest.mark.integration
@pytest.mark.asyncio
async def test_query_string_is_forwarded_opaquely() -> None:
    """includeData is deliberately undeclared on our route and passed through."""
    query = "?includeData=true"
    caller, upstream = live_clients()
    async with caller, upstream:
        proxied = await caller.get(f"/runs/{RUN_ID}/summary{query}")
        direct = await upstream.get(f"/runs/{RUN_ID}/summary{query}")

    assert proxied.status_code == direct.status_code
    assert proxied.content == direct.content


@pytest.mark.integration
@pytest.mark.asyncio
async def test_absent_run_upstream_5xx_becomes_sanitized_502() -> None:
    caller, upstream = live_clients()
    async with caller, upstream:
        direct = await upstream.get(f"/runs/{ABSENT_RUN_ID}/summary")
        proxied = await caller.get(f"/runs/{ABSENT_RUN_ID}/summary")

    assert direct.status_code >= 500, (
        "Upstream now answers an absent run id with "
        f"{direct.status_code}; if it returns 4xx the proxy will mirror it and "
        "this test should pin the new contract instead."
    )
    assert proxied.status_code == 502
    # The upstream echoes the requested path in its error body; ours must not.
    assert proxied.json() == {"detail": "The upstream service failed while loading the run summary."}
    assert ABSENT_RUN_ID not in proxied.text


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upstream_vary_origin_is_not_duplicated() -> None:
    """The upstream sets Vary: Origin for its own CORS; ours must not double it."""
    caller, upstream = live_clients()
    async with caller, upstream:
        direct = await upstream.get(f"/runs/{RUN_ID}/summary")
        cors = await caller.get(
            f"/runs/{RUN_ID}/summary", headers={"Origin": "http://localhost:3000"}
        )
        plain = await caller.get(f"/runs/{RUN_ID}/summary")

    assert "origin" in direct.headers.get("vary", "").lower(), (
        "Upstream no longer varies on Origin, so this regression can no longer occur here."
    )
    assert cors.headers["access-control-allow-origin"] == "http://localhost:3000"
    tokens = [token.strip().lower() for token in cors.headers["vary"].split(",")]
    assert tokens.count("origin") == 1, f"duplicate Vary token: {cors.headers['vary']}"
    assert len(tokens) == len(set(tokens))
    # Without an Origin request header our CORS layer adds nothing, and the
    # upstream's own Origin token is dropped, so no Vary is emitted at all.
    assert "vary" not in plain.headers


@pytest.mark.integration
@pytest.mark.asyncio
async def test_project_summary_embeds_the_run_summary() -> None:
    """Sanity-check the payload shape both endpoints are expected to serve."""
    caller, upstream = live_clients()
    async with caller, upstream:
        project = await caller.get(f"/projects/{PROJECT_ID}/summary")
        run = await caller.get(f"/runs/{RUN_ID}/summary")

    project_body = json.loads(project.content)
    run_body = json.loads(run.content)
    assert project_body["id"] == PROJECT_ID
    assert project_body["simulationRun"]["id"] == RUN_ID
    assert run_body["id"] == RUN_ID
    assert run_body["run"]["status"] == "SUCCEEDED"
