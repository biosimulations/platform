"""Tests for the simulation runs listing: POST /simulations/runs.

Covers three layers:
  * pure query translation (build_mongo_query / resolve_sort) — no DB,
  * the Mongo-backed SimulationRunDatabaseService — testcontainers Mongo,
  * the FastAPI endpoint — TestClient with a mocked runs DB service.
"""

from datetime import datetime, timezone
from typing import Literal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pymongo import ASCENDING, DESCENDING

from biosim_server.api.main import app
from biosim_server.simulations.models import SimulationRun
from biosim_server.common.auth import AuthenticatedUser, can_view_simulation_run, get_optional_user
from biosim_server.simulations import SimulationRunDatabaseServiceMongo, SimulationRunRecord
from biosim_server.simulations.database import build_mongo_query, resolve_sort
from biosim_server.simulations.models import (
    ListSimulationRunsRequest,
    TableFilter,
    TablePagination,
    TableSort,
)


def _record(
    run_id: str,
    *,
    name: str = "Run",
    simulator: str = "copasi",
    email: str = "user@example.com",
    status: str = "CREATED",
    cache_buster: str = "0",
    submitted: datetime | None = None,
    biosimulations_run_id: str | None = None,
    owner_sub: str | None = None,
    visibility: Literal["public", "private"] = "private",
    omex_id: str | None = None,
) -> SimulationRunRecord:
    when = submitted or datetime(2024, 1, 1, tzinfo=timezone.utc)
    return SimulationRunRecord(
        run_id=run_id,
        processing_id=f"sim-run-{run_id}",
        name=name,
        simulator=simulator,
        simulator_version="1.0.0",
        simulator_digest="sha256:abc",
        cache_buster=cache_buster,
        email=email,
        status=status,  # type: ignore[arg-type]
        submitted=when,
        updated=when,
        biosimulations_run_id=biosimulations_run_id,
        owner_sub=owner_sub,
        visibility=visibility,
        omex_id=omex_id,
    )


# The visibility half of the ACL clause: explicitly public, or legacy (field absent).
_PUBLIC = {"visibility": {"$in": ["public", None]}}


# --------------------------- query translation ---------------------------

def test_missing_visibility_is_legacy_public() -> None:
    """Legacy Mongo docs predate the field; they were world-readable and stay public.

    (Audit plan section 7.3 / 4.3 migration strategy: missing visibility -> public.
    Reading them as private would make every run written before this feature
    unreachable, including to the caller who submitted it.)
    """
    record = SimulationRunRecord.model_validate({
        "run_id": "job-1",
        "processing_id": "sim-run-1",
        "name": "Run",
        "simulator": "copasi",
        "simulator_version": "1.0.0",
    })
    assert record.visibility is None
    assert can_view_simulation_run(None, record) is True


def test_explicitly_private_without_owner_is_denied_to_everyone() -> None:
    """A *missing* visibility is public; an explicit "private" with no owner is not.

    Visibility is never inferred from the owner, so an ownerless private record
    fails closed rather than falling back to public.
    """
    record = SimulationRunRecord(
        run_id="job-1", processing_id="sim-run-1", name="Run",
        simulator="copasi", simulator_version="1.0.0",
        owner_sub=None, visibility="private",
    )
    assert can_view_simulation_run(None, record) is False
    assert can_view_simulation_run(AuthenticatedUser(sub="auth0|abc", email=None), record) is False


def test_run_simulation_request_rejects_extra_owner_sub() -> None:
    """Client-supplied owner identity is not a request field; extra="forbid" → 422."""
    response = TestClient(app).post(
        "/simulations/run",
        json={
            "omex_id": "abc123def456",
            "name": "Test Run",
            "simulators": [{"id": "copasi", "version": "4.34.251"}],
            "owner_sub": "attacker-controlled",
        },
    )
    assert response.status_code == 422
    assert any(err.get("type") == "extra_forbidden" for err in response.json()["detail"])


def test_from_record_does_not_expose_owner_sub_or_visibility() -> None:
    """SimulationRun is the public listing DTO; internal ownership fields stay on record"""

    record = SimulationRunRecord(
        run_id="job-1",
        processing_id="sim-run-1",
        name="Run",
        simulator="copasi",
        simulator_version="1.0.0",
        owner_sub="auth0|abc",
        visibility="private",
        omex_id="abc123def456"
    )

    api_run = SimulationRun.from_record(record)

    dumped = api_run.model_dump()
    assert "owner_sub" not in dumped
    assert "visibility" not in dumped
    assert "omex_id" not in dumped

    # Same shape FastAPI would emit (camelCase aliases)
    aliased = api_run.model_dump(by_alias=True)
    assert "ownerSub" not in aliased
    assert "visibility" not in aliased
    assert "omexId" not in aliased

    json_payload = api_run.model_dump_json()
    assert "auth0|abc" not in json_payload
    assert "private" not in json_payload



def test_build_query_all_anonymous_is_public_only() -> None:
    req = ListSimulationRunsRequest(type="all")
    # `$in [..., None]` so legacy documents with no visibility field are public too.
    assert build_mongo_query(req) == _PUBLIC
    assert build_mongo_query(req, viewer=None) == _PUBLIC


def test_build_query_all_authenticated_includes_own_private() -> None:
    viewer = AuthenticatedUser(sub="auth0|me", email="a@b.com")
    req = ListSimulationRunsRequest(type="all")
    assert build_mongo_query(req, viewer=viewer) == {
        "$or": [_PUBLIC, {"owner_sub": "auth0|me"}]
    }


def test_build_query_all_admin_unrestricted() -> None:
    viewer = AuthenticatedUser(sub="auth0|admin", email="a@b.com", roles=["admin"])
    req = ListSimulationRunsRequest(type="all")
    assert build_mongo_query(req, viewer=viewer) == {}


def test_build_query_user_scope_uses_owner_sub_not_email() -> None:
    viewer = AuthenticatedUser(sub="auth0|me", email="a@b.com")
    req = ListSimulationRunsRequest(type="user", user="a@b.com")
    assert build_mongo_query(req, viewer=viewer) == {"owner_sub": "auth0|me"}
    # Client-supplied user email is ignored for ACL
    assert "email" not in build_mongo_query(req, viewer=viewer)


def test_build_query_user_scope_unauthenticated_matches_nothing() -> None:
    req = ListSimulationRunsRequest(type="user", user="a@b.com")
    assert build_mongo_query(req, viewer=None) == {"run_id": {"$in": []}}


def test_build_query_never_uses_request_user_as_acl() -> None:
    viewer = AuthenticatedUser(sub="auth0|me", email="a@b.com")
    req = ListSimulationRunsRequest(type="all", user="victim@example.com")
    q = build_mongo_query(req, viewer=viewer)
    assert q == {"$or": [_PUBLIC, {"owner_sub": "auth0|me"}]}
    assert "email" not in q


def test_build_query_empty_sub_is_public_only() -> None:
    viewer = AuthenticatedUser(sub="", email="a@b.com")
    req = ListSimulationRunsRequest(type="all")
    assert build_mongo_query(req, viewer=viewer) == _PUBLIC


def test_build_query_email_filter_lowercases_value() -> None:
    req = ListSimulationRunsRequest(
        filters=[TableFilter(id="email", operator="equal", value="Foo@Bar.COM")]
    )
    query = build_mongo_query(req)
    assert query["$and"][0]["email"] == "foo@bar.com"
    assert query["$and"][1] == _PUBLIC


def test_build_query_contains_filter() -> None:
    req = ListSimulationRunsRequest(
        filters=[TableFilter(id="simulator", operator="contains", value="cop")]
    )
    query = build_mongo_query(req)
    assert query["$and"][0]["simulator"] == {"$regex": "cop", "$options": "i"}
    assert query["$and"][1] == _PUBLIC


def test_build_query_field_alias_and_allowlist() -> None:
    # 'createdAt' maps onto the persisted 'submitted' field; unknown ids are dropped.
    req = ListSimulationRunsRequest(
        filters=[
            TableFilter(id="createdAt", operator="after", value="2024-01-01T00:00:00Z"),
            TableFilter(id="not_a_field", operator="equal", value="x"),
        ]
    )
    query = build_mongo_query(req)
    assert "submitted" in query["$and"][0]
    assert query["$and"][0]["submitted"]["$gt"] == datetime(2024, 1, 1, tzinfo=timezone.utc)
    assert "not_a_field" not in query["$and"][0]


def test_build_query_is_any() -> None:
    req = ListSimulationRunsRequest(
        filters=[TableFilter(id="status", operator="is_any", value=["SUCCEEDED", "FAILED"])]
    )
    query = build_mongo_query(req)
    assert query["$and"][0]["status"] == {"$in": ["SUCCEEDED", "FAILED"]}


def test_resolve_sort_default_is_newest_first() -> None:
    assert resolve_sort(None) == ("submitted", DESCENDING)


def test_resolve_sort_explicit() -> None:
    assert resolve_sort(TableSort(id="name", direction="asc")) == ("name", ASCENDING)
    # createdAt aliases onto submitted
    assert resolve_sort(TableSort(id="createdAt", direction="desc")) == ("submitted", DESCENDING)
    # unknown field falls back to default
    assert resolve_sort(TableSort(id="bogus", direction="asc")) == ("submitted", DESCENDING)


# --------------------------- Mongo-backed service ---------------------------

@pytest.mark.asyncio
async def test_db_insert_and_query_all(
    simulation_run_database_service_mongo: SimulationRunDatabaseServiceMongo,
) -> None:
    svc = simulation_run_database_service_mongo
    await svc.insert_simulation_run(_record("a", cache_buster="salt-123", visibility="public"))
    await svc.insert_simulation_run(_record("b", visibility="public"))

    records, total = await svc.query_simulation_runs(ListSimulationRunsRequest(type="all"))
    assert total == 2
    assert {r.run_id for r in records} == {"a", "b"}
    # cache_buster round-trips through Mongo
    by_id = {r.run_id: r for r in records}
    assert by_id["a"].cache_buster == "salt-123"
    assert by_id["b"].cache_buster == "0"


@pytest.mark.asyncio
async def test_db_update_status(
    simulation_run_database_service_mongo: SimulationRunDatabaseServiceMongo,
) -> None:
    svc = simulation_run_database_service_mongo
    await svc.insert_simulation_run(_record("a", status="CREATED", visibility="public"))
    await svc.update_simulation_run("a", status="SUCCEEDED", biosimulations_run_id="mock_1")

    records, _ = await svc.query_simulation_runs(ListSimulationRunsRequest(type="all"))
    assert records[0].status == "SUCCEEDED"
    assert records[0].biosimulations_run_id == "mock_1"


@pytest.mark.asyncio
async def test_db_user_scope_filter(
    simulation_run_database_service_mongo: SimulationRunDatabaseServiceMongo,
) -> None:
    svc = simulation_run_database_service_mongo
    await svc.insert_simulation_run(_record("a", email="me@x.com", owner_sub="auth0|me", visibility="private"))
    await svc.insert_simulation_run(_record("b", email="other@x.com", owner_sub="auth0|other", visibility="private"))

    viewer = AuthenticatedUser(sub="auth0|me", email="me@x.com")
    records, total = await svc.query_simulation_runs(
        ListSimulationRunsRequest(type="user", user="ignored@x.com"),
        viewer=viewer,
    )
    assert total == 1
    assert records[0].run_id == "a"


@pytest.mark.asyncio
async def test_db_anonymous_hides_private(
    simulation_run_database_service_mongo: SimulationRunDatabaseServiceMongo,
) -> None:
    svc = simulation_run_database_service_mongo
    await svc.insert_simulation_run(_record("pub", visibility="public", owner_sub=None))
    await svc.insert_simulation_run(_record("priv", visibility="private", owner_sub="auth0|me"))
    records, total = await svc.query_simulation_runs(ListSimulationRunsRequest(type="all"))
    assert total == 1
    assert records[0].run_id == "pub"


@pytest.mark.asyncio
async def test_db_authenticated_sees_own_private_not_others(
    simulation_run_database_service_mongo: SimulationRunDatabaseServiceMongo,
) -> None:
    svc = simulation_run_database_service_mongo
    await svc.insert_simulation_run(_record("pub", visibility="public"))
    await svc.insert_simulation_run(_record("mine", visibility="private", owner_sub="auth0|me"))
    await svc.insert_simulation_run(_record("theirs", visibility="private", owner_sub="auth0|other"))
    viewer = AuthenticatedUser(sub="auth0|me", email="me@x.com")
    records, total = await svc.query_simulation_runs(
        ListSimulationRunsRequest(type="all"), viewer=viewer
    )
    assert total == 2
    assert {r.run_id for r in records} == {"pub", "mine"}


@pytest.mark.asyncio
async def test_db_missing_visibility_is_legacy_public(
    simulation_run_database_service_mongo: SimulationRunDatabaseServiceMongo,
) -> None:
    svc = simulation_run_database_service_mongo
    await svc._runs_col.insert_one({
        "run_id": "legacy",
        "processing_id": "sim-run-legacy",
        "name": "Run",
        "simulator": "copasi",
        "simulator_version": "1.0.0",
        "status": "CREATED",
        "submitted": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "updated": datetime(2024, 1, 1, tzinfo=timezone.utc),
        # no visibility, no owner_sub
    })
    records, total = await svc.query_simulation_runs(ListSimulationRunsRequest(type="all"))
    assert total == 1
    assert [r.run_id for r in records] == ["legacy"]

    # ... but an explicitly private legacy-owner-less row stays hidden.
    await svc._runs_col.insert_one({
        "run_id": "sealed",
        "processing_id": "sim-run-sealed",
        "name": "Run",
        "simulator": "copasi",
        "simulator_version": "1.0.0",
        "status": "CREATED",
        "visibility": "private",
        "submitted": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "updated": datetime(2024, 1, 1, tzinfo=timezone.utc),
    })
    records, total = await svc.query_simulation_runs(ListSimulationRunsRequest(type="all"))
    assert total == 1
    assert [r.run_id for r in records] == ["legacy"]


@pytest.mark.asyncio
async def test_db_contains_filter(
    simulation_run_database_service_mongo: SimulationRunDatabaseServiceMongo,
) -> None:
    svc = simulation_run_database_service_mongo
    await svc.insert_simulation_run(_record("a", simulator="copasi", visibility="public"))
    await svc.insert_simulation_run(_record("b", simulator="tellurium", visibility="public"))

    records, total = await svc.query_simulation_runs(
        ListSimulationRunsRequest(filters=[TableFilter(id="simulator", operator="contains", value="cop")])
    )
    assert total == 1
    assert records[0].simulator == "copasi"


@pytest.mark.asyncio
async def test_db_sort_and_paginate(
    simulation_run_database_service_mongo: SimulationRunDatabaseServiceMongo,
) -> None:
    svc = simulation_run_database_service_mongo
    for i in range(5):
        await svc.insert_simulation_run(
            _record(f"r{i}", name=f"Run {i}", visibility="public",
                    submitted=datetime(2024, 1, i + 1, tzinfo=timezone.utc))
        )

    # Newest first (default sort), first page of 2.
    page1, total = await svc.query_simulation_runs(
        ListSimulationRunsRequest(pagination=TablePagination(page=1, perPage=2))
    )
    assert total == 5
    assert [r.run_id for r in page1] == ["r4", "r3"]

    page3, _ = await svc.query_simulation_runs(
        ListSimulationRunsRequest(pagination=TablePagination(page=3, perPage=2))
    )
    assert [r.run_id for r in page3] == ["r0"]


# --------------------------- FastAPI endpoint ---------------------------

@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_endpoint_all_works_anonymously(mock_get_runs_db: MagicMock) -> None:
    # No Authorization header, type "all": list_simulation_runs depends on
    # get_optional_user (not get_current_user), specifically so the public runs
    # listing (e.g. the frontend's un-authenticated "Browse Simulation Runs" page)
    # keeps working with no token at all.
    runs_db = AsyncMock()
    runs_db.query_simulation_runs.return_value = ([], 0)
    mock_get_runs_db.return_value = runs_db

    client = TestClient(app)
    resp = client.post("/simulations/runs", json={"type": "all", "filters": [],
                                                   "pagination": {"page": 1, "perPage": 20}})
    assert resp.status_code == 200


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_endpoint_user_type_scoped_to_caller_ignores_body_user(mock_get_runs_db: MagicMock) -> None:
    # A client-supplied `user` in the body must not be trusted when the caller IS
    # authenticated -- the query is scoped to the verified token's own email.
    # Overrides get_optional_user directly (not the `authenticated_user` fixture,
    # which only overrides get_current_user and has no effect on this endpoint).
    runs_db = AsyncMock()
    runs_db.query_simulation_runs.return_value = ([], 0)
    mock_get_runs_db.return_value = runs_db

    user = AuthenticatedUser(sub="auth0|test-user-id", email="user@example.com")
    app.dependency_overrides[get_optional_user] = lambda: user
    try:
        client = TestClient(app)
        resp = client.post("/simulations/runs", json={"type": "user", "user": "someone-else@example.com",
                                                       "filters": [], "pagination": {"page": 1, "perPage": 20}})
    finally:
        app.dependency_overrides.pop(get_optional_user, None)

    assert resp.status_code == 200
    sent_request = runs_db.query_simulation_runs.call_args.args[0]
    assert sent_request.user is None
    assert runs_db.query_simulation_runs.call_args.kwargs["viewer"] is user


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_endpoint_service_unavailable(mock_get_runs_db: MagicMock, authenticated_user: AuthenticatedUser) -> None:
    mock_get_runs_db.return_value = None
    client = TestClient(app)
    resp = client.post("/simulations/runs", json={"type": "all", "filters": [],
                                                   "pagination": {"page": 1, "perPage": 20}})
    assert resp.status_code == 503


@patch("biosim_server.simulations.router.get_simulation_run_database_service")
def test_endpoint_success_shape(mock_get_runs_db: MagicMock, authenticated_user: AuthenticatedUser) -> None:
    runs_db = AsyncMock()
    runs_db.query_simulation_runs.return_value = (
        [_record("a", status="SUCCEEDED", biosimulations_run_id="6a3d5603015a4d8b0bf24b74")], 1)
    mock_get_runs_db.return_value = runs_db

    client = TestClient(app)
    resp = client.post("/simulations/runs", json={"type": "all", "filters": [],
                                                   "pagination": {"page": 1, "perPage": 20}})
    assert resp.status_code == 200
    body = resp.json()
    # camelCase aliases and pagination echo with _total filled in.
    assert body["pagination"] == {"page": 1, "perPage": 20, "_total": 1}
    assert len(body["runs"]) == 1
    run = body["runs"][0]
    assert run["id"] == "a"  # internal run_id (per-simulator job)
    # the biosimulations ObjectId detail pages must key off, exposed separately
    assert run["biosimulationsRunId"] == "6a3d5603015a4d8b0bf24b74"
    assert run["status"] == "SUCCEEDED"
    assert run["simulatorVersion"] == "1.0.0"
    assert run["simulatorDigest"] == "sha256:abc"
    assert run["envVars"] == []
    assert run["submitted"].endswith("Z")


def test_run_simulation_request_rejects_client_supplied_visibility() -> None:
    """Visibility is server-authoritative at create; the body cannot set it."""
    response = TestClient(app).post(
        "/simulations/run",
        json={
            "omex_id": "abc123def456",
            "name": "Test Run",
            "simulators": [{"id": "copasi", "version": "4.34.251"}],
            "visibility": "public",
        },
    )
    assert response.status_code == 422
    assert any(err.get("type") == "extra_forbidden" for err in response.json()["detail"])


@pytest.mark.asyncio
async def test_db_totals_and_pagination_are_computed_after_the_acl_filter(
    simulation_run_database_service_mongo: SimulationRunDatabaseServiceMongo,
) -> None:
    """A private run must not show up in someone else's totals or shift their pages.

    Counting before filtering would leak the existence of hidden runs through
    `_total` even when none of their rows are ever returned.
    """
    svc = simulation_run_database_service_mongo
    for i in range(2):
        await svc.insert_simulation_run(_record(f"pub-{i}", visibility="public"))
    for i in range(5):
        await svc.insert_simulation_run(
            _record(f"priv-{i}", owner_sub="auth0|someone-else", visibility="private")
        )

    request = ListSimulationRunsRequest(type="all", pagination=TablePagination(perPage=2))
    records, total = await svc.query_simulation_runs(request, viewer=None)
    assert total == 2                      # not 7
    assert {r.run_id for r in records} == {"pub-0", "pub-1"}

    # Page 2 of the authorized set is empty -- it is not the private overspill.
    request = ListSimulationRunsRequest(type="all", pagination=TablePagination(page=2, perPage=2))
    records, total = await svc.query_simulation_runs(request, viewer=None)
    assert total == 2
    assert records == []

    # The owner of the private runs sees their own, plus the public ones.
    owner = AuthenticatedUser(sub="auth0|someone-else", email=None)
    request = ListSimulationRunsRequest(type="all", pagination=TablePagination(perPage=20))
    _, owner_total = await svc.query_simulation_runs(request, viewer=owner)
    assert owner_total == 7
