"""Privacy boundaries through real authentication, pagination and Mongo queries."""

from collections.abc import Iterator
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from biosim_server.api.main import app
from biosim_server.biosim_verify.database import (
    VerificationCursor, VerificationDatabaseServiceMongo, decode_verification_cursor,
    encode_verification_cursor,
)
from biosim_server.common.auth import get_optional_user
from tests.fixtures.auth_seam import install_auth_seam
from tests.fixtures.jwks_fixtures import FakeJwksEndpoint, connect_error, jwks_document, make_key

pytestmark = pytest.mark.asyncio
KEY = make_key("listing")
WRONG_KEY = make_key("listing")


@pytest.fixture(autouse=True)
def auth(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeJwksEndpoint]:
    previous = app.dependency_overrides.copy()
    app.dependency_overrides.pop(get_optional_user, None)
    settings, _ = install_auth_seam(monkeypatch, app=app)
    settings.roles_claim = "roles"
    endpoint = FakeJwksEndpoint(responses=[lambda: jwks_document(KEY)])
    monkeypatch.setattr("biosim_server.common.auth.auth0.httpx.AsyncClient", endpoint.client_factory())
    yield endpoint
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)


@pytest.mark.parametrize("owner", [None, "alice", "bob", "admin"])
async def test_scoped_pages_hide_other_prefixes_and_cursor_anchors(
    verification_database_service_mongo: VerificationDatabaseServiceMongo,
    owner: str | None, auth: FakeJwksEndpoint,
) -> None:
    svc = verification_database_service_mongo
    rows = [
        ("a-public-null", None), ("b-Acme-secret-alice", "alice"),
        ("c-public-missing", None), ("d-secret-bob", "bob"),
        ("e-secret-admin", "admin"), ("f-public-null", None),
        ("g-secret-alice", "alice"), ("h-empty-owner", ""),
    ]
    docs = [{"workflow_id": name, "created": datetime(2025, 1, 1), "owner_sub": sub} for name, sub in rows]
    docs[2].pop("owner_sub")
    await svc._collection.insert_many(docs)
    expected = [name for name, sub in rows if sub is None or sub == owner]
    headers = {} if owner is None else {"Authorization": "Bearer " + KEY.token(sub=owner, extra_claims={"roles": ["admin"]} if owner == "admin" else {})}
    seen: list[str] = []
    params = {"limit": "2", "owner_sub": "bob"}  # untrusted selector cannot widen access
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        while True:
            response = await client.get("/verification_ids", params=params, headers=headers)
            assert response.status_code == 200, response.text
            assert response.headers["Cache-Control"] == "private, no-store"
            body = response.json()
            page = body["verification_ids"]
            seen.extend(page)
            token = body["next_cursor"]
            if token is None:
                break
            assert len(page) == 2
            anchor = decode_verification_cursor(token).workflow_id
            assert anchor == page[-1] and anchor in expected
            params["cursor"] = token
    assert seen == expected
    assert len(seen) == len(set(seen))
    assert auth.call_count == (0 if owner is None else 1)


@pytest.mark.parametrize("owner", [None, "bob"])
async def test_foreign_and_forged_cursor_does_not_grant_access(
    verification_database_service_mongo: VerificationDatabaseServiceMongo, owner: str | None,
) -> None:
    svc = verification_database_service_mongo
    await svc._collection.insert_many([
        {"workflow_id": name, "created": datetime(2025, 1, 1), "owner_sub": sub}
        for name, sub in [("a-private", "alice"), ("b-private", "alice"), ("c-public", None), ("d-bob", "bob")]
    ])
    headers = {} if owner is None else {"Authorization": "Bearer " + KEY.token(sub=owner)}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        alice_page = await client.get("/verification_ids", params={"limit": 1}, headers={"Authorization": "Bearer " + KEY.token(sub="alice")})
        cursor = alice_page.json()["next_cursor"]
        forged = encode_verification_cursor(VerificationCursor(datetime(2025, 1, 1), "a-nonexistent"))
        for token in (cursor, forged):
            response = await client.get("/verification_ids", params={"cursor": token}, headers=headers)
            assert response.status_code == 200
            assert response.json() == {"verification_ids": ["c-public"] + (["d-bob"] if owner else []), "next_cursor": None}


@pytest.mark.parametrize("public_count", [0, 1, 2])
async def test_hidden_rows_do_not_create_lookahead_or_underfill_pages(
    verification_database_service_mongo: VerificationDatabaseServiceMongo, public_count: int,
) -> None:
    svc = verification_database_service_mongo
    await svc._collection.insert_many([
        {"workflow_id": f"private-{i}", "owner_sub": "alice", "created": datetime(2025, 1, 2)} for i in range(10)
    ] + [
        {"workflow_id": f"public-{i}", "owner_sub": None, "created": datetime(2025, 1, 1)} for i in range(public_count)
    ])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/verification_ids", params={"limit": 2})
    assert response.status_code == 200
    assert response.json() == {"verification_ids": [f"public-{i}" for i in range(public_count)], "next_cursor": None}


@pytest.mark.parametrize("kind", ["malformed", "empty", "basic", "expired", "signature", "outage"])
async def test_credentials_fail_closed_before_listing(kind: str, auth: FakeJwksEndpoint) -> None:
    authorization = {
        "malformed": "Bearer not-a-jwt", "empty": "Bearer ", "basic": "Basic abc",
        "expired": "Bearer " + KEY.token(expires_in=-3600),
        "signature": "Bearer " + WRONG_KEY.token(), "outage": "Bearer " + KEY.token(),
    }[kind]
    if kind == "outage":
        auth.responses = [connect_error]
    ledger = AsyncMock()
    with patch("biosim_server.api.main.get_verification_database_service", return_value=ledger):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/verification_ids", headers={"Authorization": authorization})
    assert response.status_code == (503 if kind == "outage" else 401), response.text
    ledger.list_verification_ids.assert_not_awaited()
    if kind != "outage":
        assert "WWW-Authenticate" in response.headers
