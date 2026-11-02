"""Derived links on a permalink: bounded, ranked, attributed by relation only (S-2, D4, D5)."""
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest

from biosim_server.biosim_verify import linked_recovery
from biosim_server.biosim_verify.models import (
    MAX_DERIVED_PER_SOURCE_PER_DAY_ANONYMOUS,
    MAX_DERIVED_PER_SOURCE_PER_DAY_AUTHENTICATED,
    InitiatorRelation,
    VerificationRecord,
    VerificationType,
)
from biosim_server.common.auth import AuthenticatedUser
from tests.biosim_verify.test_linked_recovery import (  # noqa: F401
    _KEY,
    _archive_source,
    _as_user,
    _body,
    _catalog,
    _client,
    _omex_source,
    recovery_world,
)

pytestmark = pytest.mark.filterwarnings("error::UserWarning")

OWNER = AuthenticatedUser(sub="auth0|owner", email="owner@example.test")
OTHER = AuthenticatedUser(sub="auth0|other", email="other@example.test")
ADMIN = AuthenticatedUser(sub="auth0|admin", email="admin@example.test", roles=["admin"])
ME = AuthenticatedUser(sub="auth0|me", email="me@example.test")
_BASE = datetime(2026, 11, 1, tzinfo=UTC)


async def _derived(world: Any, source: str, index: int, relation: InitiatorRelation, initiator: str | None,
                   *, minutes: int, owner: str | None = None) -> str:
    workflow_id = f"derived-{relation}-{index}"
    await world.ledger.insert_verification(VerificationRecord(
        workflow_id=workflow_id, verify_type=VerificationType.RUNS, owner_sub=owner,
        created=_BASE + timedelta(minutes=minutes), source_verification_id=source,
        derivation_kind="reconstruction", initiator_sub=initiator, initiator_relation=relation,
    ))
    return workflow_id


async def _permalink(caller: AuthenticatedUser | None, workflow_id: str) -> dict[str, Any]:
    _as_user(caller)
    try:
        async with await _client() as client:
            response = await client.get(f"/verify/{workflow_id}")
    finally:
        _as_user(None)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


@pytest.mark.asyncio
async def test_many_links_from_others_never_displace_owner_admin_or_your_own(recovery_world: Any) -> None:  # noqa: F811
    await _archive_source(recovery_world, "source-busy")
    priority = [await _derived(recovery_world, "source-busy", 0, InitiatorRelation.OWNER, None, minutes=0),
                await _derived(recovery_world, "source-busy", 1, InitiatorRelation.ADMIN, "auth0|admin", minutes=1)]
    mine = [await _derived(recovery_world, "source-busy", index, InitiatorRelation.OTHER, ME.sub, minutes=2 + index)
            for index in range(2)]
    others = [await _derived(recovery_world, "source-busy", 100 + index, InitiatorRelation.ANONYMOUS, None,
                             minutes=10 + index) for index in range(105)]

    anonymous = await _permalink(None, "source-busy")
    listed = [link["workflow_id"] for link in anonymous["derived_verifications"]]
    assert listed[:2] == [priority[1], priority[0]]  # newest first within the group
    # The 5 newest others: an anonymous caller has no own group, so ME's links are just others here.
    assert listed[2:] == list(reversed(others))[:5]
    assert anonymous["derived_verifications_total"] == 109

    signed_in = await _permalink(ME, "source-busy")
    links = signed_in["derived_verifications"]
    assert [link["workflow_id"] for link in links] == (
        [priority[1], priority[0]] + list(reversed(mine)) + list(reversed(others))[:5]
    )
    assert [link["is_yours"] for link in links] == [False, False, True, True] + [False] * 5
    assert {link["initiator_relation"] for link in links} == {"owner", "admin", "other", "anonymous"}
    assert signed_in["derived_verifications_total"] == 109


@pytest.mark.asyncio
async def test_owner_and_admin_links_are_capped_at_ten(recovery_world: Any) -> None:  # noqa: F811
    await _archive_source(recovery_world, "source-admins")
    for index in range(12):
        await _derived(recovery_world, "source-admins", index, InitiatorRelation.ADMIN, "auth0|admin", minutes=index)
    body = await _permalink(None, "source-admins")
    assert len(body["derived_verifications"]) == 10
    assert body["derived_verifications"][0]["workflow_id"] == "derived-admin-11"
    assert body["derived_verifications_total"] == 12


@pytest.mark.asyncio
async def test_the_unavailable_original_lists_links_the_same_way(recovery_world: Any) -> None:  # noqa: F811
    await recovery_world.ledger.insert_verification(VerificationRecord(
        workflow_id="source-gone", verify_type=VerificationType.RUNS, created=_BASE,
    ))
    for index in range(8):
        await _derived(recovery_world, "source-gone", index, InitiatorRelation.OTHER, f"auth0|u{index}", minutes=index)
    async with await _client() as client:
        response = await client.get("/verify/source-gone")
    assert response.status_code == 404
    body = response.json()
    assert len(body["derived_verifications"]) == 5 and body["derived_verifications_total"] == 8
    assert all("auth0|" not in json.dumps(link) for link in body["derived_verifications"])


# --- Who started it, and the per-source daily cap --------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("caller, relation", [
    (None, "anonymous"), (OTHER, "other"), (ADMIN, "admin"),
])
async def test_the_initiator_relation_is_recorded_at_reservation(
    recovery_world: Any, caller: AuthenticatedUser | None, relation: str,  # noqa: F811
) -> None:
    archived = await _archive_source(recovery_world, "source-public-relation")
    _as_user(caller)
    try:
        async with await _client() as client:
            response = await client.post("/verify/source-public-relation/reconstruct",
                                         headers={"Idempotency-Key": _KEY}, json=_body(archived))
    finally:
        _as_user(None)
    assert response.status_code == 202, response.text
    row = await recovery_world.ledger.get_verification(response.json()["workflow_id"])
    assert row is not None and row.initiator_relation == relation and row.custom_settings is False


@pytest.mark.asyncio
async def test_the_owner_is_recorded_as_owner(recovery_world: Any) -> None:  # noqa: F811
    archived = await _archive_source(recovery_world, "source-owned-relation", owner=OWNER.sub)
    _as_user(OWNER)
    try:
        async with await _client() as client:
            response = await client.post("/verify/source-owned-relation/reconstruct",
                                         headers={"Idempotency-Key": _KEY}, json=_body(archived))
    finally:
        _as_user(None)
    row = await recovery_world.ledger.get_verification(response.json()["workflow_id"])
    assert row is not None and row.initiator_relation == "owner"


@pytest.mark.asyncio
@pytest.mark.parametrize("caller, cap", [
    (None, MAX_DERIVED_PER_SOURCE_PER_DAY_ANONYMOUS), (OTHER, MAX_DERIVED_PER_SOURCE_PER_DAY_AUTHENTICATED),
])
async def test_the_daily_cap_per_caller_and_source_is_checked_before_the_shared_charge(
    recovery_world: Any, monkeypatch: pytest.MonkeyPatch, caller: AuthenticatedUser | None, cap: int,  # noqa: F811
) -> None:
    archived = await _archive_source(recovery_world, "source-capped")
    other_source = await _archive_source(recovery_world, "source-uncapped")
    charges: list[str] = []
    monkeypatch.setattr(linked_recovery, "_enforce_rate_limit", lambda request, user: charges.append("charged"))
    _as_user(caller)
    try:
        async with await _client() as client:
            for index in range(cap):
                accepted = await client.post("/verify/source-capped/reconstruct",
                                             headers={"Idempotency-Key": f"cap-key-{index}"}, json=_body(archived))
                assert accepted.status_code == 202, accepted.text
            capped = await client.post("/verify/source-capped/reconstruct",
                                       headers={"Idempotency-Key": "cap-key-extra"}, json=_body(archived))
            replay = await client.post("/verify/source-capped/reconstruct",
                                       headers={"Idempotency-Key": "cap-key-0"}, json=_body(archived))
            elsewhere = await client.post("/verify/source-uncapped/reconstruct",
                                          headers={"Idempotency-Key": "cap-key-extra"}, json=_body(other_source))
    finally:
        _as_user(None)
    assert capped.status_code == 429
    assert capped.json()["code"] == "derived_limit"
    assert replay.status_code == 202  # an existing reservation is neither counted nor charged
    assert elsewhere.status_code == 202  # the cap is per source
    assert len(charges) == cap + 1  # the capped request was never charged


@pytest.mark.asyncio
async def test_yesterdays_requests_do_not_count(recovery_world: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    archived = await _archive_source(recovery_world, "source-yesterday")
    monkeypatch.setattr(linked_recovery, "_enforce_rate_limit", lambda request, user: None)
    async with await _client() as client:
        for index in range(MAX_DERIVED_PER_SOURCE_PER_DAY_ANONYMOUS):
            response = await client.post("/verify/source-yesterday/reconstruct",
                                         headers={"Idempotency-Key": f"old-key-{index}"}, json=_body(archived))
            assert response.status_code == 202
        await recovery_world.ledger._idempotency.update_many(
            {"source_verification_id": "source-yesterday"},
            {"$set": {"created": datetime.now(UTC) - timedelta(days=1, minutes=1)}},
        )
        fresh = await client.post("/verify/source-yesterday/reconstruct",
                                  headers={"Idempotency-Key": "fresh-key-1"}, json=_body(archived))
    assert fresh.status_code == 202


# --- Custom settings and identities ----------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("body, custom", [({}, False), ({"rel_tol": 0.5}, True)])
async def test_custom_settings_are_flagged_on_links_and_the_derived_report(
    recovery_world: Any, body: dict[str, Any], custom: bool,  # noqa: F811
) -> None:
    await _omex_source(recovery_world, "source-custom")
    with patch("biosim_server.api.main.get_biosim_service", return_value=_catalog()):
        async with await _client() as client:
            response = await client.post("/verify/source-custom/reverify", headers={"Idempotency-Key": _KEY},
                                         json=body)
    assert response.status_code == 202, response.text
    row = await recovery_world.ledger.get_verification(response.json()["workflow_id"])
    assert row is not None and row.custom_settings is custom
    links, total = await linked_recovery.derived_links("source-custom", None)
    assert total == 1 and links[0].custom_settings is custom


@pytest.mark.asyncio
async def test_no_initiator_identity_appears_in_any_response(recovery_world: Any) -> None:  # noqa: F811
    archived = await _archive_source(recovery_world, "source-identities")
    responses: list[str] = []
    async with await _client() as client:
        for index, caller in enumerate((ADMIN, OTHER, None)):
            _as_user(caller)
            accepted = await client.post("/verify/source-identities/reconstruct",
                                         headers={"Idempotency-Key": f"identity-key-{index}"}, json=_body(archived))
            assert accepted.status_code == 202, accepted.text
            responses.append(accepted.text)
        for caller in (ADMIN, OTHER, None):
            _as_user(caller)
            for path in ("/verify/source-identities", "/verify/source-identities/recovery", "/verification_ids"):
                responses.append((await client.get(path)).text)
    _as_user(None)
    text = "\n".join(responses)
    links = json.loads(responses[3])["derived_verifications"]
    assert {link["initiator_relation"] for link in links} == {"admin", "other", "anonymous"}
    for secret in ("auth0|admin", "auth0|other", "admin@example.test", "other@example.test", "initiator_sub"):
        assert secret not in text
