"""GET /verify/runs/precheck: the §4.4 decision matrix, the run-ID matrix, and POST parity (plan §4.4)."""

import asyncio
import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from biosim_server.api.main import app
from biosim_server.biosim_runs.models import HDF5Attribute, HDF5Dataset, HDF5File, HDF5Group
from biosim_server.common.upstream import _PUBLIC_FETCH_HEADERS
from tests.api.test_main import _make_temporal_for_runs

DEFAULT_DATASET = {"shared": ["t", "x"]}

# Headers an upstream might send that must never reach our caller (§4.5 response hygiene)
LEAKY_UPSTREAM_HEADERS = {
    "Set-Cookie": "upstream-session=1",
    "Access-Control-Allow-Origin": "*",
    "X-Upstream-Internal": "1",
}


def _run_record(status: str = "SUCCEEDED", simulator: str = "copasi", version: str = "7.7.0") -> dict[str, Any]:
    # `email` is deliberately present upstream: the precheck must never echo it.
    return {
        "id": "upstream-id", "status": status, "simulator": simulator, "simulatorVersion": version,
        "simulatorDigest": "sha256:abc", "email": "secret@example.test", "submitted": "2026-11-05T00:00:00Z",
    }


def _metadata(run_id: str, datasets: dict[str, list[str]], shape: tuple[int, int] = (2, 10)) -> dict[str, Any]:
    groups = [
        HDF5Group(
            name="g",
            attributes=[],
            datasets=[
                HDF5Dataset(
                    name=name,
                    shape=list(shape),
                    attributes=[HDF5Attribute(key="sedmlDataSetLabels", value=list(labels))],
                )
            ],
        )
        for name, labels in datasets.items()
    ]
    return HDF5File(filename="f.h5", id=run_id, uri=f"uri/{run_id}", groups=groups).model_dump(mode="json")


@pytest.fixture
def upstreams(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub the two upstreams the loader reads: run records and simdata metadata.

    ``state``: "records" maps run_id -> status (default SUCCEEDED) or an httpx
    status to fail with; "no_metadata" lists run ids whose metadata is 404;
    "metadata" maps run_id -> datasets/shape overrides.
    """
    state: dict[str, Any] = {"records": {}, "no_metadata": set(), "metadata": {}}
    calls: dict[str, list[str]] = {"runs": [], "simdata": []}
    seen_headers: list[dict[str, str]] = []

    def runs_handler(request: httpx.Request) -> httpx.Response:
        run_id = request.url.path.split("/")[2]
        calls["runs"].append(run_id)
        seen_headers.append(dict(request.headers))
        configured = state["records"].get(run_id, "SUCCEEDED")
        if isinstance(configured, int):
            return httpx.Response(configured, json={"detail": "upstream failure"})
        return httpx.Response(200, json=_run_record(status=configured), headers=LEAKY_UPSTREAM_HEADERS)

    def simdata_handler(request: httpx.Request) -> httpx.Response:
        run_id = request.url.path.split("/")[2]
        calls["simdata"].append(run_id)
        seen_headers.append(dict(request.headers))
        if run_id in state["no_metadata"]:
            return httpx.Response(404, json={"detail": "not found"})
        override = state["metadata"].get(run_id)
        datasets, shape = override if override else (DEFAULT_DATASET, (2, 10))
        return httpx.Response(200, json=_metadata(run_id, datasets, shape), headers=LEAKY_UPSTREAM_HEADERS)

    monkeypatch.setattr(
        "biosim_server.biosim_verify.precheck.get_http_client",
        lambda: httpx.AsyncClient(base_url="https://runs.test", transport=httpx.MockTransport(runs_handler)),
    )
    monkeypatch.setattr(
        "biosim_server.biosim_verify.precheck.get_simdata_http_client",
        lambda: httpx.AsyncClient(base_url="https://simdata.test", transport=httpx.MockTransport(simdata_handler)),
    )
    monkeypatch.setattr("biosim_server.biosim_verify.precheck.get_database_service", lambda: None)
    state["calls"] = calls
    state["seen_headers"] = seen_headers
    return state


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
        yield test_client


@pytest.mark.asyncio
async def test_two_succeeded_runs_sharing_a_dataset_are_eligible(
    client: AsyncClient, upstreams: dict[str, Any]
) -> None:
    response = await client.get(
        "/verify/runs/precheck", params=[("biosimulations_run_ids", "r1"), ("biosimulations_run_ids", "r2")]
    )

    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "private, no-store"
    body = response.json()
    assert body["eligible"] is True
    assert body["ineligible_reason"] is None
    assert body["common_datasets"] == {"shared": ["t", "x"]}
    assert [run["run_id"] for run in body["runs"]] == ["r1", "r2"]  # request order
    assert all(run["eligible"] and run["run_status"] == "SUCCEEDED" for run in body["runs"])
    assert all(run["simulator"] == "copasi:7.7.0" for run in body["runs"])
    assert body["warnings"] == []


@pytest.mark.asyncio
async def test_same_dataset_names_but_different_labels_are_blocked_here_and_by_post(
    client: AsyncClient, upstreams: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    upstreams["metadata"]["r2"] = ({"shared": ["y", "z"]}, (2, 10))
    response = await client.get(
        "/verify/runs/precheck", params=[("biosimulations_run_ids", "r1"), ("biosimulations_run_ids", "r2")]
    )

    assert response.status_code == 200
    body = response.json()
    assert body["eligible"] is False
    assert body["ineligible_reason"] == "no_common_datasets"
    assert body["common_datasets"] == {}

    # Parity: the same input is rejected by POST /verify/runs with its unchanged 400.
    monkeypatch.setattr("biosim_server.api.main.get_temporal_client", lambda: MagicMock())
    monkeypatch.setattr("biosim_server.api.main.get_verification_database_service", lambda: AsyncMock())
    monkeypatch.setattr("biosim_server.api.main.get_database_service", lambda: None)
    submitted = await client.post("/verify/runs", params={"biosimulations_run_ids": ["r1", "r2"]})
    assert submitted.status_code == 400
    assert "no datasets in common" in submitted.json()["detail"]


@pytest.mark.asyncio
async def test_agreeing_runs_pass_both_the_precheck_and_post(
    client: AsyncClient, upstreams: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("biosim_server.api.main.get_temporal_client", lambda: _make_temporal_for_runs())
    monkeypatch.setattr("biosim_server.api.main.get_verification_database_service", lambda: AsyncMock())
    monkeypatch.setattr("biosim_server.api.main.get_database_service", lambda: None)
    submitted = await client.post("/verify/runs", params={"biosimulations_run_ids": ["r1", "r2"]})
    precheck = await client.get(
        "/verify/runs/precheck", params=[("biosimulations_run_ids", "r1"), ("biosimulations_run_ids", "r2")]
    )

    assert submitted.status_code == 200, submitted.text
    assert precheck.status_code == 200
    assert precheck.json()["eligible"] is True


@pytest.mark.asyncio
async def test_shared_dataset_with_different_shapes_warns_without_blocking(
    client: AsyncClient, upstreams: dict[str, Any]
) -> None:
    upstreams["metadata"]["r2"] = ({"shared": ["t", "x"]}, (3, 10))
    response = await client.get(
        "/verify/runs/precheck", params=[("biosimulations_run_ids", "r1"), ("biosimulations_run_ids", "r2")]
    )

    assert response.status_code == 200
    body = response.json()
    assert body["eligible"] is True
    assert body["ineligible_reason"] is None
    assert body["warnings"] == [{"code": "shape_mismatch", "run_ids": ["r1", "r2"], "dataset": "shared"}]


@pytest.mark.asyncio
async def test_unknown_run_id_is_excluded_not_found_and_others_are_unaffected(
    client: AsyncClient, upstreams: dict[str, Any]
) -> None:
    upstreams["records"]["r2"] = 404
    response = await client.get(
        "/verify/runs/precheck", params=[("biosimulations_run_ids", "r1"), ("biosimulations_run_ids", "r2")]
    )

    assert response.status_code == 200
    body = response.json()
    by_id = {run["run_id"]: run for run in body["runs"]}
    assert by_id["r2"]["eligible"] is False
    assert by_id["r2"]["exclusion"] == "not_found"
    assert by_id["r2"]["reason"] == "Run not found in BioSimulations."
    assert by_id["r1"]["eligible"] is True
    assert body["eligible"] is False
    assert body["ineligible_reason"] == "fewer_than_two_eligible_runs"


@pytest.mark.asyncio
async def test_a_running_run_is_excluded_not_succeeded(client: AsyncClient, upstreams: dict[str, Any]) -> None:
    upstreams["records"]["r2"] = "RUNNING"
    response = await client.get(
        "/verify/runs/precheck", params=[("biosimulations_run_ids", "r1"), ("biosimulations_run_ids", "r2")]
    )

    assert response.status_code == 200
    body = response.json()
    excluded = next(run for run in body["runs"] if run["run_id"] == "r2")
    assert excluded["eligible"] is False
    assert excluded["exclusion"] == "not_succeeded"
    assert excluded["run_status"] == "RUNNING"
    assert excluded["reason"] == "Run status is RUNNING; only successful runs can be compared."


@pytest.mark.asyncio
async def test_simdata_down_for_one_run_leaves_it_eligible_with_a_warning(
    client: AsyncClient, upstreams: dict[str, Any]
) -> None:
    upstreams["no_metadata"].add("r2")
    response = await client.get(
        "/verify/runs/precheck", params=[("biosimulations_run_ids", "r1"), ("biosimulations_run_ids", "r2")]
    )

    assert response.status_code == 200
    body = response.json()
    assert body["eligible"] is True  # the metadata warning never blocks (Phase 0 decision 0.3)
    by_id = {run["run_id"]: run for run in body["runs"]}
    assert by_id["r2"]["datasets"] is None
    assert body["warnings"] == [{"code": "metadata_unavailable", "run_ids": ["r2"], "dataset": None}]


@pytest.mark.asyncio
async def test_a_dashed_run_id_is_accepted(client: AsyncClient, upstreams: dict[str, Any]) -> None:
    upstreams["records"]["run-a"] = "SUCCEEDED"
    upstreams["records"]["run-b"] = "SUCCEEDED"
    response = await client.get(
        "/verify/runs/precheck", params=[("biosimulations_run_ids", "run-a"), ("biosimulations_run_ids", "run-b")]
    )

    assert response.status_code == 200, response.text
    assert response.json()["eligible"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [
        [("biosimulations_run_ids", "a/b")],
        [("biosimulations_run_ids", "héllo")],
        [("biosimulations_run_ids", "a" * 65)],
        [("biosimulations_run_ids", f"r{i}") for i in range(11)],
    ],
)
async def test_the_run_id_matrix_rejects_before_any_upstream_work(
    client: AsyncClient, upstreams: dict[str, Any], params: list[tuple[str, Any]]
) -> None:
    response = await client.get("/verify/runs/precheck", params=params)
    assert response.status_code == 422
    assert upstreams["calls"]["runs"] == [] and upstreams["calls"]["simdata"] == []


@pytest.mark.asyncio
async def test_duplicate_run_ids_are_a_400(client: AsyncClient, upstreams: dict[str, Any]) -> None:
    response = await client.get(
        "/verify/runs/precheck", params=[("biosimulations_run_ids", "r1"), ("biosimulations_run_ids", "r1")]
    )

    assert response.status_code == 400
    assert "Duplicate run IDs" in response.json()["detail"]
    assert upstreams["calls"]["runs"] == [] and upstreams["calls"]["simdata"] == []


@pytest.mark.asyncio
async def test_precheck_response_never_carries_the_upstream_email(
    client: AsyncClient, upstreams: dict[str, Any]
) -> None:
    response = await client.get(
        "/verify/runs/precheck", params=[("biosimulations_run_ids", "r1"), ("biosimulations_run_ids", "r2")]
    )

    assert response.status_code == 200
    assert "email" not in response.text
    assert json.dumps(response.json()).count("secret") == 0


@pytest.mark.asyncio
async def test_an_invalid_bearer_token_still_gets_a_200(
    client: AsyncClient, upstreams: dict[str, Any]
) -> None:
    response = await client.get(
        "/verify/runs/precheck",
        params=[("biosimulations_run_ids", "r1"), ("biosimulations_run_ids", "r2")],
        headers={"Authorization": "Bearer definitely-invalid"},
    )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_the_precheck_budget_returns_429_with_retry_after(
    client: AsyncClient, upstreams: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "biosim_server.common.ratelimit.get_settings",
        lambda: SimpleNamespace(ratelimit=SimpleNamespace(enabled=True, precheck_per_window=2, precheck_window_seconds=60)),
    )
    first = await client.get("/verify/runs/precheck", params={"biosimulations_run_ids": "r1"})
    second = await client.get("/verify/runs/precheck", params={"biosimulations_run_ids": "r1"})
    admitted_calls = {name: len(made) for name, made in upstreams["calls"].items()}
    exhausted = await client.get("/verify/runs/precheck", params={"biosimulations_run_ids": "r1"})

    assert first.status_code == second.status_code == 200
    assert exhausted.status_code == 429
    assert exhausted.headers["Retry-After"]
    # A denied precheck never reaches either upstream
    assert {name: len(made) for name, made in upstreams["calls"].items()} == admitted_calls


@pytest.mark.asyncio
async def test_caller_credentials_never_reach_either_upstream_and_no_upstream_header_is_relayed(
    client: AsyncClient, upstreams: dict[str, Any]
) -> None:
    """§4.5 header isolation and response hygiene for the run-record and simdata fetches."""
    response = await client.get(
        "/verify/runs/precheck",
        params=[("biosimulations_run_ids", "r1"), ("biosimulations_run_ids", "r2")],
        headers={
            "Authorization": "Bearer caller-token",
            "Cookie": "session=caller",
            # Not an allowed origin, so CORSMiddleware adds nothing and any CORS header would be relayed
            "Origin": "https://not-allowed.test",
        },
    )

    assert response.status_code == 200
    # A run record and the metadata for each of the two runs
    assert len(upstreams["seen_headers"]) == 4
    for headers in upstreams["seen_headers"]:
        assert {name: value for name, value in headers.items() if name != "host"} == _PUBLIC_FETCH_HEADERS
    for name in LEAKY_UPSTREAM_HEADERS:
        assert name.lower() not in response.headers


@pytest.mark.asyncio
async def test_a_runs_record_and_metadata_load_concurrently(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One run is one upstream hop deep, which the 40 s budget (one 30 s hop plus slack) assumes (§4.4).

    Each upstream answers only once the other has been asked, so loading them one after
    the other would leave the run unclassifiable and exclude it as upstream_unavailable.
    """
    started: set[str] = set()
    both_started = asyncio.Event()

    async def wait_for_the_other(kind: str) -> None:
        started.add(kind)
        if len(started) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=2)

    async def runs_handler(request: httpx.Request) -> httpx.Response:
        await wait_for_the_other("record")
        return httpx.Response(200, json=_run_record())

    async def simdata_handler(request: httpx.Request) -> httpx.Response:
        await wait_for_the_other("metadata")
        return httpx.Response(200, json=_metadata(request.url.path.split("/")[2], DEFAULT_DATASET))

    monkeypatch.setattr(
        "biosim_server.biosim_verify.precheck.get_http_client",
        lambda: httpx.AsyncClient(base_url="https://runs.test", transport=httpx.MockTransport(runs_handler)),
    )
    monkeypatch.setattr(
        "biosim_server.biosim_verify.precheck.get_simdata_http_client",
        lambda: httpx.AsyncClient(base_url="https://simdata.test", transport=httpx.MockTransport(simdata_handler)),
    )
    monkeypatch.setattr("biosim_server.biosim_verify.precheck.get_database_service", lambda: None)

    response = await client.get("/verify/runs/precheck", params={"biosimulations_run_ids": "r1"})

    assert response.status_code == 200
    run = response.json()["runs"][0]
    assert run["exclusion"] is None
    assert run["run_status"] == "SUCCEEDED"
    assert [dataset["name"] for dataset in run["datasets"]] == ["shared"]
