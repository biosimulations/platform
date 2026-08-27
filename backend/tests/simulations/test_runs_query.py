"""Tests for the simulation runs listing: POST /simulations/runs.

Covers three layers:
  * pure query translation (build_mongo_query / resolve_sort) — no DB,
  * the Mongo-backed SimulationRunDatabaseService — testcontainers Mongo,
  * the FastAPI endpoint — TestClient with a mocked runs DB service.
"""

from datetime import datetime, timezone
from typing import Any, Literal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pymongo import ASCENDING, DESCENDING

from biosim_server.api.main import app
from biosim_server.common.auth import AuthenticatedUser, get_optional_user
from biosim_server.simulations import SimulationRunDatabaseServiceMongo, SimulationRunRecord
from biosim_server.simulations.database import InvalidDateFilterError, build_mongo_query, resolve_sort
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
    owner: str | None = None,
    visibility: Literal["public", "private"] | None = None,
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
        owner=owner if owner is not None else owner_sub,
        visibility=visibility,
    )


_PUBLIC_VISIBILITY: dict[str, Any] = {"visibility": {"$in": [None, "public"]}}


def _with_visibility(
    query: dict[str, Any], *, caller_sub: str | None = None
) -> dict[str, Any]:
    visibility: dict[str, Any] = (
        _PUBLIC_VISIBILITY
        if caller_sub is None
        else {
            "$or": [
                _PUBLIC_VISIBILITY,
                {"visibility": "private", "owner_sub": caller_sub},
            ]
        }
    )
    if not query:
        return visibility
    return {"$and": [query, visibility]}


# --------------------------- query translation ---------------------------

def test_build_query_all_no_filters() -> None:
    req = ListSimulationRunsRequest(type="all")
    assert build_mongo_query(req) == _PUBLIC_VISIBILITY


def test_build_query_all_includes_caller_private_rows() -> None:
    req = ListSimulationRunsRequest(type="all")
    assert build_mongo_query(req, caller_sub="auth0|me") == _with_visibility(
        {}, caller_sub="auth0|me"
    )


def test_simulation_run_owner_is_optional_for_legacy_records() -> None:
    common = {
        "run_id": "legacy",
        "processing_id": "sim-run-legacy",
        "name": "Legacy run",
        "simulator": "copasi",
        "simulator_version": "1.0.0",
        "submitted": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "updated": datetime(2024, 1, 1, tzinfo=timezone.utc),
    }
    omitted = SimulationRunRecord.model_validate(common)
    assert omitted.owner is None
    assert omitted.owner_sub is None
    assert omitted.visibility is None
    null_owner = SimulationRunRecord.model_validate({**common, "owner": None, "owner_sub": None})
    assert null_owner.owner is None
    assert null_owner.owner_sub is None
    assert SimulationRunRecord.model_validate({**common, "visibility": None}).visibility is None
    assert SimulationRunRecord.model_validate({**common, "visibility": "public"}).visibility == "public"
    assert SimulationRunRecord.model_validate({**common, "visibility": "private"}).visibility == "private"


def test_simulation_run_new_write_stamps_owner_fields_together() -> None:
    record = SimulationRunRecord(
        run_id="new",
        processing_id="sim-run-new",
        name="Owned run",
        simulator="copasi",
        simulator_version="1.0.0",
        owner_sub="auth0|alice",
        owner="auth0|alice",
        visibility="private",
    )
    assert record.owner == record.owner_sub == "auth0|alice"
    assert record.visibility == "private"


def test_build_query_user_scope() -> None:
    req = ListSimulationRunsRequest(type="user", user="a@b.com")
    assert build_mongo_query(req) == _with_visibility({"email": "a@b.com"})


def test_build_query_user_scope_owner_sub() -> None:
    req = ListSimulationRunsRequest(type="user", owner_sub="auth0|abc")
    assert build_mongo_query(req) == _with_visibility({"owner_sub": "auth0|abc"})


def test_build_query_user_scope_owner_sub_and_legacy_email() -> None:
    # The verified-email fallback is restricted to genuinely legacy rows:
    # owner_sub null AND visibility null/missing. A newly written anonymous
    # row (explicit visibility "public") must not be claimable as "mine"
    # through a matching email.
    req = ListSimulationRunsRequest(type="user", owner_sub="auth0|abc", user="a@b.com")
    assert build_mongo_query(req) == _with_visibility(
        {
            "$or": [
                {"owner_sub": "auth0|abc"},
                {"owner_sub": None, "email": "a@b.com", "visibility": None},
            ]
        }
    )


def test_build_query_user_scope_lowercases_email() -> None:
    req = ListSimulationRunsRequest(type="user", user="Foo@Bar.COM")
    assert build_mongo_query(req) == _with_visibility({"email": "foo@bar.com"})


def test_build_query_email_filter_lowercases_value() -> None:
    req = ListSimulationRunsRequest(
        filters=[TableFilter(id="email", operator="equal", value="Foo@Bar.COM")]
    )
    assert build_mongo_query(req) == _with_visibility({"email": "foo@bar.com"})


def test_build_query_contains_filter() -> None:
    req = ListSimulationRunsRequest(
        filters=[TableFilter(id="simulator", operator="contains", value="cop")]
    )
    query = build_mongo_query(req)
    assert query == _with_visibility({"simulator": {"$regex": "cop", "$options": "i"}})


def test_build_query_field_alias_and_allowlist() -> None:
    # 'createdAt' maps onto the persisted 'submitted' field; unknown ids are dropped.
    req = ListSimulationRunsRequest(
        filters=[
            TableFilter(id="createdAt", operator="after", value="2024-01-01T00:00:00Z"),
            TableFilter(id="not_a_field", operator="equal", value="x"),
        ]
    )
    query = build_mongo_query(req)
    inner = query["$and"][0]
    assert "submitted" in inner
    assert inner["submitted"]["$gt"] == datetime(2024, 1, 1, tzinfo=timezone.utc)
    assert "not_a_field" not in inner


def test_build_query_is_any() -> None:
    req = ListSimulationRunsRequest(
        filters=[TableFilter(id="status", operator="is_any", value=["SUCCEEDED", "FAILED"])]
    )
    assert build_mongo_query(req) == _with_visibility({"status": {"$in": ["SUCCEEDED", "FAILED"]}})


def test_resolve_sort_default_is_newest_first() -> None:
    assert resolve_sort(None) == ("submitted", DESCENDING)


def test_resolve_sort_explicit() -> None:
    assert resolve_sort(TableSort(id="name", direction="asc")) == ("name", ASCENDING)
    # createdAt aliases onto submitted
    assert resolve_sort(TableSort(id="createdAt", direction="desc")) == ("submitted", DESCENDING)
    assert resolve_sort(TableSort(id="biosimulationsRunId", direction="asc")) == (
        "biosimulations_run_id",
        ASCENDING,
    )
    # unknown field falls back to default
    assert resolve_sort(TableSort(id="bogus", direction="asc")) == ("submitted", DESCENDING)


def test_build_query_biosimulations_run_id() -> None:
    req = ListSimulationRunsRequest(
        filters=[TableFilter(id="biosimulationsRunId", operator="equal", value="6a3d5603015a4d8b0bf24b74")]
    )
    assert build_mongo_query(req) == _with_visibility(
        {"biosimulations_run_id": "6a3d5603015a4d8b0bf24b74"}
    )


def test_build_query_invalid_date_raises() -> None:
    req = ListSimulationRunsRequest(
        filters=[TableFilter(id="createdAt", operator="after", value={"year": 2024, "month": 1, "day": 1})]
    )
    with pytest.raises(InvalidDateFilterError):
        build_mongo_query(req)


# --------------------------- Mongo-backed service ---------------------------

@pytest.mark.asyncio
async def test_db_insert_and_query_all(
    simulation_run_database_service_mongo: SimulationRunDatabaseServiceMongo,
) -> None:
    svc = simulation_run_database_service_mongo
    await svc.insert_simulation_run(_record("a", cache_buster="salt-123"))
    await svc.insert_simulation_run(_record("b"))

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
    await svc.insert_simulation_run(_record("a", status="CREATED"))
    await svc.update_simulation_run("a", status="SUCCEEDED", biosimulations_run_id="mock_1")

    records, _ = await svc.query_simulation_runs(ListSimulationRunsRequest(type="all"))
    assert records[0].status == "SUCCEEDED"
    assert records[0].biosimulations_run_id == "mock_1"


@pytest.mark.asyncio
async def test_db_user_scope_filter(
    simulation_run_database_service_mongo: SimulationRunDatabaseServiceMongo,
) -> None:
    svc = simulation_run_database_service_mongo
    await svc.insert_simulation_run(_record("a", email="me@x.com"))
    await svc.insert_simulation_run(_record("b", email="other@x.com"))

    records, total = await svc.query_simulation_runs(
        ListSimulationRunsRequest(type="user", user="me@x.com")
    )
    assert total == 1
    assert records[0].run_id == "a"


@pytest.mark.asyncio
async def test_db_owner_sub_scope_includes_legacy_email_rows(
    simulation_run_database_service_mongo: SimulationRunDatabaseServiceMongo,
) -> None:
    svc = simulation_run_database_service_mongo
    await svc.insert_simulation_run(_record("owned", email="me@x.com", owner_sub="auth0|me"))
    await svc.insert_simulation_run(_record("legacy", email="me@x.com", owner_sub=None))
    await svc.insert_simulation_run(_record("other", email="other@x.com", owner_sub="auth0|other"))

    records, total = await svc.query_simulation_runs(
        ListSimulationRunsRequest(type="user", owner_sub="auth0|me", user="me@x.com")
    )
    assert total == 2
    assert {r.run_id for r in records} == {"owned", "legacy"}


@pytest.mark.asyncio
async def test_db_query_hides_others_private_runs(
    simulation_run_database_service_mongo: SimulationRunDatabaseServiceMongo,
) -> None:
    svc = simulation_run_database_service_mongo
    await svc.insert_simulation_run(_record("pub", visibility="public", owner_sub=None))
    await svc.insert_simulation_run(
        _record("mine", visibility="private", owner_sub="auth0|me")
    )
    await svc.insert_simulation_run(
        _record("theirs", visibility="private", owner_sub="auth0|other")
    )
    await svc.insert_simulation_run(_record("legacy", visibility=None, owner_sub=None))

    anon, anon_total = await svc.query_simulation_runs(
        ListSimulationRunsRequest(type="all"), caller_sub=None
    )
    assert anon_total == 2
    assert {r.run_id for r in anon} == {"pub", "legacy"}

    mine, mine_total = await svc.query_simulation_runs(
        ListSimulationRunsRequest(type="all"), caller_sub="auth0|me"
    )
    assert mine_total == 3
    assert {r.run_id for r in mine} == {"pub", "legacy", "mine"}

    admin, admin_total = await svc.query_simulation_runs(
        ListSimulationRunsRequest(type="all"), caller_sub="auth0|admin"
    )
    assert admin_total == 2
    assert {r.run_id for r in admin} == {"pub", "legacy"}


@pytest.mark.asyncio
async def test_db_contains_filter(
    simulation_run_database_service_mongo: SimulationRunDatabaseServiceMongo,
) -> None:
    svc = simulation_run_database_service_mongo
    await svc.insert_simulation_run(_record("a", simulator="copasi"))
    await svc.insert_simulation_run(_record("b", simulator="tellurium"))

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
            _record(f"r{i}", name=f"Run {i}",
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

    user = AuthenticatedUser(
        sub="auth0|test-user-id", email="user@example.com", email_verified=True
    )
    app.dependency_overrides[get_optional_user] = lambda: user
    try:
        client = TestClient(app)
        resp = client.post("/simulations/runs", json={"type": "user", "user": "someone-else@example.com",
                                                       "filters": [], "pagination": {"page": 1, "perPage": 20}})
    finally:
        app.dependency_overrides.pop(get_optional_user, None)

    assert resp.status_code == 200
    sent_request = runs_db.query_simulation_runs.call_args[0][0]
    assert sent_request.owner_sub == user.sub
    assert sent_request.user == user.email
    assert sent_request.user != "someone-else@example.com"


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
    # Public listing redacts email even when the persisted record has one.
    assert run["email"] is None
