"""Recovery admission: no storage work before the caller is charged (I-4, WP-5)."""
import asyncio
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from temporalio.exceptions import ApplicationError

from biosim_server.biosim_verify import linked_recovery
from biosim_server.biosim_verify.models import VerifyWorkflowOutput, VerifyWorkflowStatus
from biosim_server.biosim_verify.reconstruct_workflow import (
    CompareRetainedSnapshotsInput,
    compare_retained_snapshots,
)
from biosim_server.dependencies import get_omex_database_service
from tests.biosim_verify.test_linked_recovery import (  # noqa: F401
    _KEY,
    _SETTINGS,
    _archive_source,
    _body,
    _catalog,
    _client,
    _omex_source,
    _terminate_derived,
    recovery_world,
)

pytestmark = pytest.mark.filterwarnings("error::UserWarning")

_STORAGE_METHODS = ("download_file", "upload_file", "upload_bytes", "put_if_absent", "exists", "delete_file",
                    "get_modified_date", "get_listing", "get_file_contents", "iter_objects")


def _spy_storage(files: Any, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every FileService call made from now on."""
    calls: list[str] = []
    for name in _STORAGE_METHODS:
        original = getattr(files, name)

        def recorder(*args: Any, _name: str = name, _original: Callable[..., Any] = original, **kwargs: Any) -> Any:
            calls.append(_name)
            return _original(*args, **kwargs)

        monkeypatch.setattr(files, name, recorder)
    return calls


def _charge_spy(monkeypatch: pytest.MonkeyPatch, *, throttled: bool = False) -> AsyncMock:
    """Replace the shared-budget charge; a throttled caller gets the limiter's 429."""
    charge = AsyncMock(side_effect=HTTPException(status_code=429, detail="Too Many Requests") if throttled else None)
    monkeypatch.setattr(linked_recovery, "_charge_new_reservation", charge)
    return charge


@pytest.mark.asyncio
async def test_a_throttled_reconstruction_does_no_storage_work(
    recovery_world: Any, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    archived = await _archive_source(recovery_world, "source-throttled")
    calls = _spy_storage(recovery_world.files, monkeypatch)
    _charge_spy(monkeypatch, throttled=True)
    async with await _client() as client:
        response = await client.post("/verify/source-throttled/reconstruct", headers={"Idempotency-Key": _KEY},
                                     json=_body(archived))
    assert response.status_code == 429
    assert calls == []
    assert recovery_world.temporal.started == []


@pytest.mark.asyncio
async def test_a_throttled_reverification_reads_no_archive(
    recovery_world: Any, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    await _omex_source(recovery_world, "source-throttled-reverify")
    omex_db = get_omex_database_service()
    assert omex_db is not None
    calls = _spy_storage(recovery_world.files, monkeypatch)
    _charge_spy(monkeypatch, throttled=True)
    with patch("biosim_server.api.main.get_biosim_service", return_value=_catalog()):
        async with await _client() as client:
            response = await client.post("/verify/source-throttled-reverify/reverify",
                                         headers={"Idempotency-Key": _KEY}, json={})
    assert response.status_code == 429
    assert calls == []
    omex_db.get_omex_file_for_caller.assert_not_awaited()  # type: ignore[attr-defined]
    assert recovery_world.temporal.started == []


@pytest.mark.asyncio
async def test_an_eligible_reconstruction_only_checks_existence(
    recovery_world: Any, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    archived = await _archive_source(recovery_world, "source-exists-only")
    references = {snapshot.artifact_reference for snapshot in archived["snapshots"]}
    calls = _spy_storage(recovery_world.files, monkeypatch)
    charge = _charge_spy(monkeypatch)
    async with await _client() as client:
        response = await client.post("/verify/source-exists-only/reconstruct", headers={"Idempotency-Key": _KEY},
                                     json=_body(archived))
        replay = await client.post("/verify/source-exists-only/reconstruct", headers={"Idempotency-Key": _KEY},
                                   json=_body(archived))
    assert response.status_code == 202, response.text
    assert replay.json()["workflow_id"] == response.json()["workflow_id"]
    # One existence check per distinct reference for the new request; the replay of a
    # started reservation returns before any storage call and is never charged.
    assert calls == ["exists"] * len(references)
    charge.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_missing_snapshot_is_refused_and_stays_charged(
    recovery_world: Any, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    archived = await _archive_source(recovery_world, "source-missing")
    del recovery_world.files.objects[archived["snapshots"][0].artifact_reference]
    charge = _charge_spy(monkeypatch)
    async with await _client() as client:
        response = await client.post("/verify/source-missing/reconstruct", headers={"Idempotency-Key": _KEY},
                                     json=_body(archived))
    assert response.status_code == 409
    assert response.json()["code"] == "missing_numerical_inputs"
    charge.assert_awaited_once()  # storage was read, so the charge stands
    assert recovery_world.temporal.started == []
    assert await recovery_world.ledger._idempotency.count_documents(  # no id was reserved
        {"source_verification_id": "source-missing"}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["error", "timeout"])
async def test_a_storage_failure_during_the_existence_check_is_503(
    recovery_world: Any, monkeypatch: pytest.MonkeyPatch, failure: str,  # noqa: F811
) -> None:
    archived = await _archive_source(recovery_world, f"source-storage-{failure}")

    async def broken(_: str) -> bool:
        if failure == "timeout":
            await asyncio.sleep(1)
        raise OSError("storage unreachable")

    monkeypatch.setattr(recovery_world.files, "exists", broken)
    monkeypatch.setattr(linked_recovery, "_EXISTS_BUDGET_SECONDS", 0.05)
    _charge_spy(monkeypatch)
    async with await _client() as client:
        response = await client.post(f"/verify/source-storage-{failure}/reconstruct",
                                     headers={"Idempotency-Key": _KEY}, json=_body(archived))
    assert response.status_code == 503
    assert recovery_world.temporal.started == []


@pytest.mark.asyncio
async def test_existence_checks_are_bounded_in_concurrency(
    recovery_world: Any, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    template = (await _archive_source(recovery_world, "source-bounded"))["snapshots"][0]
    snapshots = [template.model_copy(update={"artifact_reference": f"verification-snapshots/data/{index}.json.gz"})
                 for index in range(20)]
    active = peak = 0

    async def slow_exists(_: str) -> bool:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return True

    monkeypatch.setattr(recovery_world.files, "exists", slow_exists)
    await linked_recovery._require_snapshots_exist(snapshots)
    assert peak == linked_recovery._EXISTS_CONCURRENCY


# --- The comparison reports which check failed (4.2) ---------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("damage, code, message", [
    ("tamper", "checksum_mismatch", "Retained numerical input checksum mismatch"),
    ("delete", "missing_numerical_inputs", "Retained numerical input is missing"),
])
async def test_the_comparison_raises_a_typed_failure(
    recovery_world: Any, damage: str, code: str, message: str,  # noqa: F811
) -> None:
    archived = await _archive_source(recovery_world, f"source-{damage}")
    reference = archived["snapshots"][0].artifact_reference
    if damage == "tamper":
        recovery_world.files.objects[reference] = b"tampered"
    else:
        del recovery_world.files.objects[reference]
    with pytest.raises(ApplicationError) as raised:
        await compare_retained_snapshots(CompareRetainedSnapshotsInput(
            snapshots=archived["snapshots"], sim_run_info=archived["runs"], compare_settings=_SETTINGS,
            ordered_run_ids=archived["run_ids"],
        ), f"reconstruction-{damage}")
    assert raised.value.type == code and raised.value.message == message and raised.value.non_retryable


@pytest.mark.asyncio
async def test_a_checksum_mismatch_fails_the_derived_verification(
    recovery_world: Any, temporal_client: Any, temporal_verify_worker: Any,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Admission checks existence only; the comparison detects the mismatch and says so."""
    archived = await _archive_source(recovery_world, "source-mismatch-live")
    recovery_world.files.objects[archived["snapshots"][0].artifact_reference] = b"tampered"
    monkeypatch.setattr("biosim_server.api.main.get_temporal_client", lambda: temporal_client)
    try:
        async with await _client() as client:
            created = await client.post("/verify/source-mismatch-live/reconstruct",
                                        headers={"Idempotency-Key": _KEY}, json=_body(archived))
            assert created.status_code == 202, created.text
            new_id = created.json()["workflow_id"]
            result = await temporal_client.get_workflow_handle(new_id, result_type=VerifyWorkflowOutput).result()
            derived = await client.get(f"/verify/{new_id}")
    finally:
        await _terminate_derived(recovery_world, temporal_client, "source-mismatch-live")
    assert result.workflow_status == VerifyWorkflowStatus.FAILED
    assert result.failure_code == "checksum_mismatch"
    assert result.workflow_error == "Retained numerical input checksum mismatch"
    assert result.persistence_state == "stored"
    body = derived.json()
    assert derived.status_code == 200
    assert body["workflow_status"] == "FAILED" and body["failure_code"] == "checksum_mismatch"
    assert body["source_verification_id"] == "source-mismatch-live"
