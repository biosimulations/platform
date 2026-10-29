"""Linked reconstruction and re-verification.

Recovery creates a new id. It does not rewrite the source row, its report, or
its permalink, and reconstruction never submits a simulator job.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from temporalio.exceptions import ApplicationError
from temporalio.service import RPCError, RPCStatusCode
from temporalio.testing import ActivityEnvironment

from biosim_server.api.main import app
from biosim_server.common.auth import get_optional_user
from biosim_server.biosim_omex.models import OmexFile
from biosim_server.biosim_runs.models import (
    BiosimSimulationRun, BiosimSimulationRunStatus, BiosimulatorVersion, Hdf5DataValues,
)
from biosim_server.biosim_verify.activities import PersistVerificationReportActivityInput, persist_verification_report_activity
from biosim_server.biosim_verify.models import (
    MAX_VERIFY_RUN_IDS, MAX_VERIFY_SIMULATORS, CompareSettings, NumericalInputSnapshot, ReconstructRequest,
    ReverifyRequest, RunData, SimulationRunInfo, VerificationProvenance, VerificationRecord, VerificationType,
    VerifyWorkflowOutput, VerifyWorkflowStatus,
)
from biosim_server.biosim_verify.reconstruct_workflow import CompareRetainedSnapshotsInput, compare_retained_snapshots
from biosim_server.biosim_verify.report_storage import VerificationReportStore
from biosim_server.biosim_verify.snapshots import capture_input_snapshots
from biosim_server.common.auth.auth0 import AuthenticatedUser
from biosim_server.common.storage import gcs_aio
from biosim_server.common.storage.file_service import FileService
from biosim_server.common.storage.file_service_gcs import FileServiceGCS
from biosim_server.dependencies import (
    get_file_service, get_omex_database_service, get_verification_report_store,
    set_file_service, set_omex_database_service, set_verification_report_store,
)
from biosim_server.version import __version__
from tests.biosim_verify.test_compatibility import _file
from tests.common.test_gcs_missing_objects import FakeGcsStorage


_SETTINGS = CompareSettings(
    user_description="kept", include_outputs=False, rel_tol=0.0001, abs_tol_min=0.001, abs_tol_scale=0.00001,
)
_KEY = "same-key-1"
_HASH = "a" * 32


class _MemoryFiles(FileService):
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def download_file(self, gcs_path: str, file_path: Path | None = None) -> tuple[str, str]:
        raise NotImplementedError

    async def upload_file(self, file_path: Path, gcs_path: str) -> str:
        raise NotImplementedError

    async def upload_bytes(self, file_contents: bytes, gcs_path: str) -> str:
        self.objects[gcs_path] = file_contents
        return gcs_path

    async def get_modified_date(self, gcs_path: str) -> datetime:
        if gcs_path not in self.objects:
            raise FileNotFoundError(gcs_path)
        return datetime.now(UTC)

    async def get_listing(self, gcs_path: str) -> list[Any]:
        return []

    async def get_file_contents(self, gcs_path: str) -> bytes | None:
        return self.objects.get(gcs_path)

    async def close(self) -> None:
        return None


class _Temporal:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.fail_next = False
        self._runs: dict[str, str] = {}

    async def start_workflow(
        self, _workflow: object, _arg: object = None, *, args: list[object] | None = None,
        id: str, task_queue: str,
    ) -> SimpleNamespace:
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("lost reply")
        self.started.append(id)
        self._runs[id] = "run-1"
        return SimpleNamespace(id=id, run_id="run-1")

    def get_workflow_handle(self, workflow_id: str, run_id: str | None = None, result_type: object = None) -> Any:
        handle = SimpleNamespace(run_id=self._runs.get(workflow_id))

        async def describe(rpc_timeout: object = None) -> SimpleNamespace:
            if workflow_id not in self._runs:
                raise RPCError("missing", RPCStatusCode.NOT_FOUND, b"")
            return SimpleNamespace(run_id=self._runs[workflow_id], id=workflow_id)

        handle.describe = describe
        return handle


def _version(name: str) -> BiosimulatorVersion:
    return BiosimulatorVersion(
        id=name, name=name, version="1", image_url="image", image_digest="digest",
        created="2026-01-01", updated="2026-01-01",
    )


def _runs() -> list[SimulationRunInfo]:
    return [
        SimulationRunInfo(
            biosim_sim_run=BiosimSimulationRun(
                id=f"run{index}", name=f"run{index}", status=BiosimSimulationRunStatus.SUCCEEDED,
                simulator_version=_version(f"sim{index}"),
            ),
            hdf5_file=_file(f"run{index}", {"ds": ["t", "x"]}),
        )
        for index in range(2)
    ]


def _output(workflow_id: str, owner: str | None = None) -> VerifyWorkflowOutput:
    return VerifyWorkflowOutput(
        workflow_id=workflow_id, compare_settings=_SETTINGS, workflow_status=VerifyWorkflowStatus.COMPLETED,
        timestamp="2026-01-01T00:00:00+00:00", workflow_run_id="run-source", owner_sub=owner,
    )


@pytest_asyncio.fixture
async def recovery_world(
    mongo_test_client: Any, verification_database_service_mongo: Any, monkeypatch: pytest.MonkeyPatch,
) -> Any:
    async with _recovery_world(_MemoryFiles(), mongo_test_client, verification_database_service_mongo, monkeypatch) as world:
        yield world


@pytest_asyncio.fixture
async def gcs_recovery_world(
    mongo_test_client: Any, verification_database_service_mongo: Any, monkeypatch: pytest.MonkeyPatch,
) -> Any:
    """The production storage service, with gcloud's Storage replaced by an in-memory fake."""
    FakeGcsStorage.objects = {}
    FakeGcsStorage.fail_status = None
    monkeypatch.setattr(gcs_aio, "Storage", FakeGcsStorage)
    monkeypatch.setattr("biosim_server.common.storage.file_service_gcs.create_token", lambda: None)
    files = FileServiceGCS()
    async with _recovery_world(files, mongo_test_client, verification_database_service_mongo, monkeypatch) as world:
        world.objects = FakeGcsStorage.objects
        yield world


@asynccontextmanager
async def _recovery_world(
    files: FileService, mongo_test_client: Any, verification_database_service_mongo: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[Any]:
    store = VerificationReportStore(mongo_test_client, files)
    await store.ensure_indexes()
    previous_files = get_file_service()
    previous_store = get_verification_report_store()
    previous_omex = get_omex_database_service()
    set_file_service(files)
    set_verification_report_store(store)
    monkeypatch.setattr("biosim_server.biosim_verify.activities.get_mongo_client", lambda: mongo_test_client)
    monkeypatch.setattr("biosim_server.biosim_verify.activities.get_file_service", lambda: files)
    temporal = _Temporal()
    monkeypatch.setattr("biosim_server.api.main.get_temporal_client", lambda: temporal)
    app.dependency_overrides.pop(get_optional_user, None)
    try:
        yield SimpleNamespace(
            files=files, store=store, ledger=verification_database_service_mongo, temporal=temporal,
            mongo=mongo_test_client,
        )
    finally:
        app.dependency_overrides.pop(get_optional_user, None)
        await store.delete_all()
        set_file_service(previous_files)
        set_verification_report_store(previous_store)
        set_omex_database_service(previous_omex)


def _as_user(user: AuthenticatedUser | None) -> None:
    if user is None:
        app.dependency_overrides.pop(get_optional_user, None)
    else:
        app.dependency_overrides[get_optional_user] = lambda: user


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _archive_source(
    world: Any, workflow_id: str = "source-public", owner: str | None = None, *, with_report: bool = True,
    runs: list[SimulationRunInfo] | None = None,
) -> dict[str, Any]:
    runs = runs or _runs()
    data = [
        RunData(
            run_id=info.biosim_sim_run.id, dataset_name="ds", var_names=["t", "x"],
            data=Hdf5DataValues(shape=[2, 3], values=[0, 1, 2, 3, 4, 5]),
        )
        for info in runs
    ]
    snapshots = await capture_input_snapshots(workflow_id, runs, data)
    assert snapshots is not None
    output = _output(workflow_id, owner)
    provenance = VerificationProvenance(
        verification_id=workflow_id, workflow_run_id=output.workflow_run_id, owner_sub=owner,
        computed_at=datetime(2026, 1, 1, tzinfo=UTC), requested_settings=_SETTINGS.model_dump(),
        actual_runs=runs, ordered_run_ids=[info.biosim_sim_run.id for info in runs], input_snapshots=snapshots,
        comparator_version=__version__,
    )
    if with_report:
        await world.store.save(output, provenance)
    await world.ledger.insert_verification(VerificationRecord(
        workflow_id=workflow_id, verify_type=VerificationType.RUNS, owner_sub=owner,
        created=datetime(2026, 1, 1, tzinfo=UTC), workflow_input={"biosimulations_run_ids": provenance.ordered_run_ids},
        status=VerifyWorkflowStatus.COMPLETED,
    ))
    return {
        "run_ids": provenance.ordered_run_ids,
        "snapshot_sha256": [item.content_sha256 for item in snapshots],
        "compare_settings": _SETTINGS.model_dump(mode="json"),
        "snapshots": snapshots, "runs": runs,
    }


def _body(archived: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_ids": archived["run_ids"], "snapshot_sha256": archived["snapshot_sha256"],
        "compare_settings": archived["compare_settings"],
    }


@pytest.mark.asyncio
async def test_reconstruction_refuses_a_saved_request_without_provenance(recovery_world: Any) -> None:
    await recovery_world.ledger.insert_verification(VerificationRecord(
        workflow_id="request-only", verify_type=VerificationType.RUNS, created=datetime.now(UTC),
        workflow_input={"biosimulations_run_ids": ["run0", "run1"]},
    ))
    async with await _client() as client:
        response = await client.post("/verify/request-only/reconstruct", headers={"Idempotency-Key": _KEY}, json=_body({
            "run_ids": ["run0", "run1"], "snapshot_sha256": ["a" * 64],
            "compare_settings": _SETTINGS.model_dump(mode="json"),
        }))
    assert response.status_code == 409
    assert response.json()["code"] == "missing_provenance"
    assert response.json()["detail"]
    assert recovery_world.temporal.started == []


@pytest.mark.asyncio
async def test_reconstruction_verifies_checksums_and_leaves_the_source_intact(recovery_world: Any) -> None:
    archived = await _archive_source(recovery_world)
    source_before = await recovery_world.ledger._collection.find_one({"workflow_id": "source-public"}, {"_id": 0})
    report_before = await recovery_world.store._collection.find_one({"workflow_id": "source-public"}, {"_id": 0})
    biosim = AsyncMock()
    with patch("biosim_server.api.main.get_biosim_service", return_value=biosim):
        async with await _client() as client:
            created = await client.post(
                "/verify/source-public/reconstruct", headers={"Idempotency-Key": _KEY}, json=_body(archived),
            )
            assert created.status_code == 202
            body = created.json()
            assert body["workflow_id"] != "source-public"
            assert body["source_verification_id"] == "source-public"
            assert body["derivation_kind"] == "reconstruction"
            assert body["may_start_simulations"] is False
            assert body["calculation"] == "new_calculation"
            assert "not an exact restoration" in body["message"]
            source = await client.get("/verify/source-public")
    assert biosim.get_simulator_versions.await_count == 0
    assert recovery_world.temporal.started == [body["workflow_id"]]
    source_after = await recovery_world.ledger._collection.find_one({"workflow_id": "source-public"}, {"_id": 0})
    report_after = await recovery_world.store._collection.find_one({"workflow_id": "source-public"}, {"_id": 0})
    assert source_before == source_after
    assert report_before["content_sha256"] == report_after["content_sha256"]
    assert source.status_code == 200
    loaded = source.json()
    assert loaded["workflow_id"] == "source-public"
    assert loaded["workflow_status"] == "COMPLETED"
    assert body["workflow_id"] in [item["workflow_id"] for item in loaded["derived_verifications"]]
    derived = await recovery_world.ledger.get_verification(body["workflow_id"])
    assert derived is not None
    assert derived.source_verification_id == "source-public"
    assert derived.owner_sub is None
    assert "initiator_sub" not in loaded


@pytest.mark.asyncio
async def test_checksum_mismatch_does_not_start_a_workflow(recovery_world: Any) -> None:
    archived = await _archive_source(recovery_world, "source-checksum")
    reference = archived["snapshots"][0].artifact_reference
    recovery_world.files.objects[reference] = b"tampered"
    async with await _client() as client:
        response = await client.post(
            "/verify/source-checksum/reconstruct", headers={"Idempotency-Key": _KEY}, json=_body(archived),
        )
    assert response.status_code == 409
    assert response.json()["code"] == "checksum_mismatch"
    assert recovery_world.temporal.started == []
    assert await recovery_world.ledger.list_derived("source-checksum") == []


@pytest.mark.asyncio
async def test_identical_requests_converge_and_conflicts_fail(recovery_world: Any) -> None:
    archived = await _archive_source(recovery_world, "source-converge")
    async with await _client() as client:
        first, second = await asyncio.gather(
            client.post("/verify/source-converge/reconstruct", headers={"Idempotency-Key": _KEY}, json=_body(archived)),
            client.post("/verify/source-converge/reconstruct", headers={"Idempotency-Key": _KEY}, json=_body(archived)),
        )
        assert first.status_code == 202 and second.status_code == 202
        assert first.json()["workflow_id"] == second.json()["workflow_id"]
        other = dict(_body(archived))
        other["compare_settings"] = {**other["compare_settings"], "user_description": "different"}
        conflict = await client.post(
            "/verify/source-converge/reconstruct", headers={"Idempotency-Key": _KEY}, json=other,
        )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "idempotency_conflict"
    assert len(set(recovery_world.temporal.started)) == 1
    assert len(await recovery_world.ledger.list_derived("source-converge")) == 1


@pytest.mark.asyncio
async def test_lost_reply_retries_the_reserved_id(recovery_world: Any) -> None:
    archived = await _archive_source(recovery_world, "source-lost")
    recovery_world.temporal.fail_next = True
    async with await _client() as client:
        lost = await client.post(
            "/verify/source-lost/reconstruct", headers={"Idempotency-Key": _KEY}, json=_body(archived),
        )
        assert lost.status_code == 503
        assert "same Idempotency-Key" in lost.json()["detail"]
        recovered = await client.post(
            "/verify/source-lost/reconstruct", headers={"Idempotency-Key": _KEY}, json=_body(archived),
        )
    assert recovered.status_code == 202
    assert recovery_world.temporal.started == [recovered.json()["workflow_id"]]
    assert len(await recovery_world.ledger.list_derived("source-lost")) == 1


@pytest.mark.asyncio
async def test_anonymous_quota_charges_new_reservations_only(recovery_world: Any) -> None:
    archived = await _archive_source(recovery_world, "source-quota")
    async with await _client() as client:
        for index in range(5):
            response = await client.post(
                "/verify/source-quota/reconstruct", headers={"Idempotency-Key": f"quota-key-{index}"}, json=_body(archived),
            )
            assert response.status_code == 202, response.text
        limited = await client.post(
            "/verify/source-quota/reconstruct", headers={"Idempotency-Key": "quota-key-extra"}, json=_body(archived),
        )
        retry = await client.post(
            "/verify/source-quota/reconstruct", headers={"Idempotency-Key": "quota-key-0"}, json=_body(archived),
        )
    assert limited.status_code == 429
    assert retry.status_code == 202
    assert len(set(recovery_world.temporal.started)) == 5


@pytest.mark.asyncio
async def test_privacy_inheritance_and_listing_hide_the_initiator(recovery_world: Any) -> None:
    archived = await _archive_source(recovery_world, "source-private", owner="auth0|owner")
    owner = AuthenticatedUser(sub="auth0|owner", email="owner@example.test")
    other = AuthenticatedUser(sub="auth0|other", email="other@example.test")
    admin = AuthenticatedUser(sub="auth0|admin", email="admin@example.test", roles=["admin"])
    async with await _client() as client:
        _as_user(None)
        hidden = await client.post(
            "/verify/source-private/reconstruct", headers={"Idempotency-Key": _KEY}, json=_body(archived),
        )
        assert hidden.status_code == 404
        assert "recovery" not in hidden.json()
        _as_user(other)
        also_hidden = await client.post(
            "/verify/source-private/reconstruct", headers={"Idempotency-Key": _KEY}, json=_body(archived),
        )
        assert also_hidden.status_code == 404 and "recovery" not in also_hidden.json()
        _as_user(owner)
        owned = await client.post(
            "/verify/source-private/reconstruct", headers={"Idempotency-Key": "owner-key-1"}, json=_body(archived),
        )
        assert owned.status_code == 202
        _as_user(admin)
        administered = await client.post(
            "/verify/source-private/reconstruct", headers={"Idempotency-Key": "admin-key-1"}, json=_body(archived),
        )
        _as_user(owner)
        listing = await client.get("/verification_ids")
    assert administered.status_code == 202
    derived = await recovery_world.ledger.get_verification(administered.json()["workflow_id"])
    assert derived is not None
    assert derived.owner_sub == "auth0|owner"
    assert derived.initiator_sub == "auth0|admin"
    assert all("initiator_sub" not in item for item in listing.json()["verifications"])
    assert administered.json()["workflow_id"] in listing.json()["verification_ids"]
    _as_user(None)
    async with await _client() as client:
        public_listing = await client.get("/verification_ids")
    assert administered.json()["workflow_id"] not in public_listing.json()["verification_ids"]


@pytest.mark.asyncio
async def test_reverify_displays_defaults_and_refuses_a_missing_archive(recovery_world: Any) -> None:
    await _archive_source(recovery_world, "source-reverify")
    omex = OmexFile(
        file_hash_md5=_HASH, uploaded_filename="model.omex", bucket_name="bucket",
        omex_gcs_path="archives/model.omex", file_size=4,
    )
    await recovery_world.files.upload_bytes(b"omex", omex.omex_gcs_path)
    await recovery_world.ledger._collection.update_one(
        {"workflow_id": "source-reverify"}, {"$set": {"verify_type": "omex", "omex_hash": _HASH}},
    )
    omex_db = AsyncMock()
    omex_db.get_omex_file_for_caller = AsyncMock(return_value=omex)
    set_omex_database_service(omex_db)
    biosim = AsyncMock()
    biosim.get_simulator_versions = AsyncMock(return_value=[
        _version(name) for name in ("amici", "copasi", "pysces", "tellurium", "vcell")
    ])
    with patch("biosim_server.api.main.get_biosim_service", return_value=biosim):
        async with await _client() as client:
            accepted = await client.post(
                "/verify/source-reverify/reverify", headers={"Idempotency-Key": _KEY}, json={},
            )
            runs_only = await client.post(
                "/verify/source-public-missing/reverify", headers={"Idempotency-Key": "runs-only-key"}, json={},
            )
    assert accepted.status_code == 202, accepted.text
    body = accepted.json()
    assert body["may_start_simulations"] is True
    assert body["cache_reuse"] == "not_guaranteed"
    assert "simulators" in body["defaults_applied"]
    assert "simulation" in body["message"]
    assert body["compare_settings"]["user_description"] == "reverification"
    assert body["compare_settings"]["rel_tol"] == 0.0001
    assert [item.split(":", 1)[0] for item in body["simulators"]] == [
        "amici", "copasi", "pysces", "tellurium", "vcell",
    ]
    await recovery_world.ledger.insert_verification(VerificationRecord(
        workflow_id="source-public-missing", verify_type=VerificationType.RUNS, created=datetime.now(UTC),
    ))
    started_before = list(recovery_world.temporal.started)
    async with await _client() as client:
        refused = await client.post(
            "/verify/source-public-missing/reverify", headers={"Idempotency-Key": "runs-only-key"}, json={},
        )
    assert refused.status_code == 409
    assert refused.json()["code"] == "archive_not_stored"
    assert recovery_world.temporal.started == started_before
    assert runs_only.status_code == 404


@pytest.mark.asyncio
async def test_unreadable_archive_refuses_reverification_without_a_job(recovery_world: Any) -> None:
    await recovery_world.ledger.insert_verification(VerificationRecord(
        workflow_id="source-unreadable", verify_type=VerificationType.OMEX,
        created=datetime(2026, 1, 1, tzinfo=UTC), status=VerifyWorkflowStatus.COMPLETED, omex_hash=_HASH,
    ))
    omex = OmexFile(
        file_hash_md5=_HASH, uploaded_filename="model.omex", bucket_name="bucket",
        omex_gcs_path="archives/missing.omex", file_size=4,
    )
    omex_db = AsyncMock()
    omex_db.get_omex_file_for_caller = AsyncMock(return_value=omex)
    set_omex_database_service(omex_db)
    async with await _client() as client:
        response = await client.post(
            "/verify/source-unreadable/reverify", headers={"Idempotency-Key": _KEY}, json={},
        )
    assert response.status_code == 409
    assert response.json()["code"] == "archive_unreadable"
    assert recovery_world.temporal.started == []


@pytest.mark.asyncio
async def test_derived_report_shows_source_version_and_time(recovery_world: Any) -> None:
    archived = await _archive_source(recovery_world, "source-derived")
    async with await _client() as client:
        created = await client.post(
            "/verify/source-derived/reconstruct", headers={"Idempotency-Key": _KEY}, json=_body(archived),
        )
    assert created.status_code == 202
    new_id = created.json()["workflow_id"]
    result = await compare_retained_snapshots(CompareRetainedSnapshotsInput(
        snapshots=archived["snapshots"], sim_run_info=archived["runs"], compare_settings=_SETTINGS,
        ordered_run_ids=archived["run_ids"],
    ), new_id)
    output = VerifyWorkflowOutput(
        workflow_id=new_id, compare_settings=_SETTINGS, workflow_status=VerifyWorkflowStatus.COMPLETED,
        timestamp="2026-02-02T00:00:00+00:00", workflow_run_id="run-1",
        source_verification_id="source-derived", derivation_kind="reconstruction", calculation="new_calculation",
        workflow_results_reference=result.reference,
    )
    await ActivityEnvironment().run(
        persist_verification_report_activity,
        PersistVerificationReportActivityInput(output=output, provenance=VerificationProvenance(
            verification_id=new_id, workflow_run_id="run-1", computed_at=datetime(2026, 2, 2, tzinfo=UTC),
            requested_settings=_SETTINGS.model_dump(), source_verification_id="source-derived",
            derivation_kind="reconstruction", ordered_run_ids=archived["run_ids"],
            input_snapshots=archived["snapshots"], actual_runs=archived["runs"],
        )),
    )
    async with await _client() as client:
        derived = await client.get(f"/verify/{new_id}")
    assert derived.status_code == 200
    body = derived.json()
    assert body["source_verification_id"] == "source-derived"
    assert body["derivation_kind"] == "reconstruction"
    assert body["calculation"] == "new_calculation"
    assert body["computed_at"].startswith("2026-02-02")
    assert body["comparator_version"] == __version__
    assert body["workflow_id"] == new_id


@pytest.mark.asyncio
async def test_snapshot_comparison_does_not_call_upstream_services(recovery_world: Any) -> None:
    source = Path("biosim_server/biosim_verify/reconstruct_workflow.py").read_text()
    assert "submit_biosim_run_activity" not in source
    assert "get_hdf5_data" not in source
    assert "get_existing_biosim_simulation_run_activity" not in source
    archived = await _archive_source(recovery_world, "source-offline")
    submit = AsyncMock(side_effect=AssertionError("simulator job"))
    download = AsyncMock(side_effect=AssertionError("upstream download"))
    with (
        patch("biosim_server.biosim_runs.submit_biosim_run_activity", submit),
        patch("biosim_server.biosim_runs.biosim_service.BiosimServiceRest.get_hdf5_data", download),
    ):
        result = await compare_retained_snapshots(CompareRetainedSnapshotsInput(
            snapshots=archived["snapshots"], sim_run_info=archived["runs"], compare_settings=_SETTINGS,
            ordered_run_ids=archived["run_ids"],
        ), "reconstruction-offline")
    submit.assert_not_called()
    download.assert_not_called()
    assert result.reference is not None
    reference = archived["snapshots"][0].artifact_reference
    del recovery_world.files.objects[reference]
    with pytest.raises(ApplicationError):
        await compare_retained_snapshots(CompareRetainedSnapshotsInput(
            snapshots=archived["snapshots"], sim_run_info=archived["runs"], compare_settings=_SETTINGS,
            ordered_run_ids=archived["run_ids"],
        ), "reconstruction-missing")
    submit.assert_not_called()


def test_reconstruction_request_model_requires_explicit_inputs() -> None:
    with pytest.raises(Exception):
        ReconstructRequest.model_validate({"compare_settings": _SETTINGS.model_dump()})
    with pytest.raises(Exception):
        ReverifyRequest.model_validate({"simulators": [f"sim{index}" for index in range(MAX_VERIFY_SIMULATORS + 1)]})


@pytest.mark.asyncio
async def test_over_limit_retained_comparison_is_refused_without_a_job(recovery_world: Any) -> None:
    snapshots = [
        NumericalInputSnapshot(
            run_id=f"run{index}", dataset_name="ds", var_names=["t"], shape=[1, 1], simulator_version="sim:1",
            content_sha256=f"{index:064x}", encoding="verification-json-gzip-v2", uncompressed_bytes=1,
            artifact_reference=f"verification-snapshots/data/{index}.json.gz",
        )
        for index in range(MAX_VERIFY_RUN_IDS + 1)
    ]
    workflow_id = "source-too-big"
    output = _output(workflow_id)
    await recovery_world.store.save(output, VerificationProvenance(
        verification_id=workflow_id, workflow_run_id=output.workflow_run_id,
        computed_at=datetime(2026, 1, 1, tzinfo=UTC), requested_settings=_SETTINGS.model_dump(),
        ordered_run_ids=[item.run_id for item in snapshots], actual_runs=_runs(), input_snapshots=snapshots,
    ))
    await recovery_world.ledger.insert_verification(VerificationRecord(
        workflow_id=workflow_id, verify_type=VerificationType.RUNS, created=datetime(2026, 1, 1, tzinfo=UTC),
        status=VerifyWorkflowStatus.COMPLETED,
    ))
    async with await _client() as client:
        response = await client.post(
            f"/verify/{workflow_id}/reconstruct", headers={"Idempotency-Key": _KEY},
            json={"run_ids": ["run0"], "snapshot_sha256": ["0" * 64], "compare_settings": _SETTINGS.model_dump(mode="json")},
        )
    assert response.status_code == 409
    assert response.json()["code"] == "work_limit"
    assert recovery_world.temporal.started == []


@pytest.mark.asyncio
async def test_gcs_persists_a_failed_report_that_has_no_snapshot_pointer(gcs_recovery_world: Any) -> None:
    """A failed or RUN_ID_NOT_FOUND verification never captures snapshots, so it has no pointer."""
    workflow_id = "failed-no-pointer"
    await gcs_recovery_world.ledger.insert_verification(VerificationRecord(
        workflow_id=workflow_id, verify_type=VerificationType.RUNS, created=datetime(2026, 1, 1, tzinfo=UTC),
    ))
    output = _output(workflow_id).model_copy(update={
        "workflow_status": VerifyWorkflowStatus.RUN_ID_NOT_FOUND, "workflow_error": "run not found",
    })
    stored = await ActivityEnvironment().run(
        persist_verification_report_activity,
        PersistVerificationReportActivityInput(output=output, provenance=VerificationProvenance(
            verification_id=workflow_id, workflow_run_id=output.workflow_run_id,
            requested_settings=_SETTINGS.model_dump(),
        )),
    )
    assert stored.persistence_state == "stored"
    saved = await gcs_recovery_world.store.get(workflow_id)
    assert saved is not None and saved.workflow_status == VerifyWorkflowStatus.RUN_ID_NOT_FOUND
    provenance = await gcs_recovery_world.store.get_provenance(workflow_id)
    assert provenance is not None and provenance.input_snapshots == []


@pytest.mark.asyncio
async def test_gcs_missing_snapshot_is_a_typed_refusal_not_an_outage(gcs_recovery_world: Any) -> None:
    archived = await _archive_source(gcs_recovery_world, "source-gcs-missing")
    del gcs_recovery_world.objects[archived["snapshots"][0].artifact_reference]
    async with await _client() as client:
        response = await client.post(
            "/verify/source-gcs-missing/reconstruct", headers={"Idempotency-Key": _KEY}, json=_body(archived),
        )
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "missing_numerical_inputs"
    assert gcs_recovery_world.temporal.started == []
    # Inside the workflow the same miss is non-retryable, not a retried storage error.
    with pytest.raises(ApplicationError) as raised:
        await compare_retained_snapshots(CompareRetainedSnapshotsInput(
            snapshots=archived["snapshots"], sim_run_info=archived["runs"], compare_settings=_SETTINGS,
            ordered_run_ids=archived["run_ids"],
        ), "reconstruction-gcs-missing")
    assert raised.value.non_retryable


@pytest.mark.asyncio
async def test_gcs_missing_archive_object_is_unreadable_not_an_outage(gcs_recovery_world: Any) -> None:
    await gcs_recovery_world.ledger.insert_verification(VerificationRecord(
        workflow_id="source-gcs-unreadable", verify_type=VerificationType.OMEX,
        created=datetime(2026, 1, 1, tzinfo=UTC), status=VerifyWorkflowStatus.COMPLETED, omex_hash=_HASH,
    ))
    omex_db = AsyncMock()
    omex_db.get_omex_file_for_caller = AsyncMock(return_value=OmexFile(
        file_hash_md5=_HASH, uploaded_filename="model.omex", bucket_name="bucket",
        omex_gcs_path="archives/gone.omex", file_size=4,
    ))
    set_omex_database_service(omex_db)
    async with await _client() as client:
        response = await client.post(
            "/verify/source-gcs-unreadable/reverify", headers={"Idempotency-Key": _KEY}, json={},
        )
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "archive_unreadable"
    assert gcs_recovery_world.temporal.started == []


# --- Real Temporal ---------------------------------------------------------------
# The fake above accepts a duplicate start silently. Real Temporal rejects it, which
# sends the second request through the history-based same-start confirmation.

_REVERIFY_CATALOG = ("amici", "copasi", "pysces", "tellurium", "vcell")


class _LostReply:
    """A real client whose next start reply is lost, after or before the server accepted it."""

    def __init__(self, client: Any, *, after_start: bool) -> None:
        self._client = client
        self.after_start = after_start
        self.armed = True

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    async def start_workflow(self, *args: Any, **kwargs: Any) -> Any:
        if self.armed:
            self.armed = False
            if self.after_start:
                await self._client.start_workflow(*args, **kwargs)
            raise RuntimeError("lost reply")
        return await self._client.start_workflow(*args, **kwargs)


def _catalog() -> AsyncMock:
    biosim = AsyncMock()
    biosim.get_simulator_versions = AsyncMock(return_value=[_version(name) for name in _REVERIFY_CATALOG])
    return biosim


async def _omex_source(world: Any, workflow_id: str, owner: str | None = None) -> None:
    await world.ledger.insert_verification(VerificationRecord(
        workflow_id=workflow_id, verify_type=VerificationType.OMEX, owner_sub=owner,
        created=datetime(2026, 1, 1, tzinfo=UTC), status=VerifyWorkflowStatus.COMPLETED, omex_hash=_HASH,
    ))
    omex = OmexFile(
        file_hash_md5=_HASH, uploaded_filename="model.omex", bucket_name="bucket",
        omex_gcs_path="archives/model.omex", file_size=4,
    )
    await world.files.upload_bytes(b"omex", omex.omex_gcs_path)
    omex_db = AsyncMock()
    omex_db.get_omex_file_for_caller = AsyncMock(return_value=omex)
    set_omex_database_service(omex_db)


async def _terminate_derived(world: Any, temporal: Any, source_id: str) -> None:
    """Started without a worker; a later test's worker must never pick these up."""
    for row in await world.ledger.list_derived(source_id):
        try:
            await temporal.get_workflow_handle(row.workflow_id).terminate(reason="test cleanup")
        except Exception:
            pass


async def _started_on_temporal(temporal: Any, workflow_id: str) -> bool:
    try:
        await temporal.get_workflow_handle(workflow_id).describe()
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:
            return False
        raise
    return True


@pytest.mark.asyncio
async def test_reconstruction_runs_on_a_real_worker_without_upstream(
    recovery_world: Any, temporal_client: Any, temporal_verify_worker: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    archived = await _archive_source(recovery_world, "source-live")
    report_before = await recovery_world.store._collection.find_one({"workflow_id": "source-live"}, {"_id": 0})
    monkeypatch.setattr("biosim_server.api.main.get_temporal_client", lambda: temporal_client)
    download = AsyncMock(side_effect=AssertionError("upstream download"))
    with (
        patch("biosim_server.biosim_runs.biosim_service.BiosimServiceRest.get_hdf5_data", download),
        patch("biosim_server.api.main.get_biosim_service", side_effect=AssertionError("simulator catalog")),
    ):
        async with await _client() as client:
            created = await client.post(
                "/verify/source-live/reconstruct", headers={"Idempotency-Key": _KEY}, json=_body(archived),
            )
            assert created.status_code == 202, created.text
            new_id = created.json()["workflow_id"]
            result = await temporal_client.get_workflow_handle(new_id, result_type=VerifyWorkflowOutput).result()
            derived = await client.get(f"/verify/{new_id}")
            source = await client.get("/verify/source-live")
    download.assert_not_called()
    assert result.workflow_status == VerifyWorkflowStatus.COMPLETED
    assert result.persistence_state == "stored"
    assert derived.status_code == 200, derived.text
    body = derived.json()
    assert body["source_verification_id"] == "source-live"
    assert body["derivation_kind"] == "reconstruction"
    assert body["calculation"] == "new_calculation"
    assert body["computed_at"] and body["comparator_version"] == __version__
    cells = body["workflow_results"]["comparison_statistics"]["ds"]
    assert all(cell["is_close"] == [True, True] for row in cells for cell in row)
    # The new report retains its own inputs, so it is reconstructable in turn.
    provenance = await recovery_world.store.get_provenance(new_id)
    assert provenance is not None
    assert [item.content_sha256 for item in provenance.input_snapshots] == archived["snapshot_sha256"]
    assert body["recovery"]["reconstruct"]["eligible"] is True
    # The source report is unchanged and its permalink links to the new calculation.
    report_after = await recovery_world.store._collection.find_one({"workflow_id": "source-live"}, {"_id": 0})
    assert report_before["content_sha256"] == report_after["content_sha256"]
    assert source.json()["workflow_id"] == "source-live"
    assert [item["workflow_id"] for item in source.json()["derived_verifications"]] == [new_id]


@pytest.mark.asyncio
async def test_reverify_idempotency_converges_on_real_temporal(
    recovery_world: Any, temporal_client: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _omex_source(recovery_world, "source-reverify-live")
    temporal: Any = temporal_client
    monkeypatch.setattr("biosim_server.api.main.get_temporal_client", lambda: temporal)
    url = "/verify/source-reverify-live/reverify"
    try:
        with patch("biosim_server.api.main.get_biosim_service", return_value=_catalog()):
            async with await _client() as client:
                first, second = await asyncio.gather(
                    client.post(url, headers={"Idempotency-Key": "reverify-same"}, json={}),
                    client.post(url, headers={"Idempotency-Key": "reverify-same"}, json={}),
                )
                assert first.status_code == 202, first.text
                assert second.status_code == 202, second.text
                assert first.json()["workflow_id"] == second.json()["workflow_id"]
                assert await _started_on_temporal(temporal_client, first.json()["workflow_id"])

                # Accepted by the server, reply lost: confirmed from the start event, not restarted.
                temporal = _LostReply(temporal_client, after_start=True)
                accepted = await client.post(url, headers={"Idempotency-Key": "reverify-lost-after"}, json={})
                assert accepted.status_code == 202, accepted.text
                temporal = temporal_client
                retried = await client.post(url, headers={"Idempotency-Key": "reverify-lost-after"}, json={})
                assert retried.json()["workflow_id"] == accepted.json()["workflow_id"]

                # Never reached the server: unknown outcome, then the retry starts the reserved id.
                temporal = _LostReply(temporal_client, after_start=False)
                unknown = await client.post(url, headers={"Idempotency-Key": "reverify-lost-before"}, json={})
                assert unknown.status_code == 503
                assert "same Idempotency-Key" in unknown.json()["detail"]
                temporal = temporal_client
                started = await client.post(url, headers={"Idempotency-Key": "reverify-lost-before"}, json={})
                assert started.status_code == 202, started.text
                assert await _started_on_temporal(temporal_client, started.json()["workflow_id"])

                conflict = await client.post(
                    url, headers={"Idempotency-Key": "reverify-same"}, json={"user_description": "other"},
                )
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "idempotency_conflict"
        derived = await recovery_world.ledger.list_derived("source-reverify-live")
        assert sorted(row.workflow_id for row in derived) == sorted({
            first.json()["workflow_id"], accepted.json()["workflow_id"], started.json()["workflow_id"],
        })
        assert all(row.derivation_kind == "reverification" for row in derived)
    finally:
        await _terminate_derived(recovery_world, temporal_client, "source-reverify-live")


@pytest.mark.asyncio
async def test_reverify_access_privacy_quota_and_unavailable_permalink(
    recovery_world: Any, temporal_client: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _omex_source(recovery_world, "source-reverify-private", owner="auth0|owner")
    source_before = await recovery_world.ledger._collection.find_one({"workflow_id": "source-reverify-private"}, {"_id": 0})
    monkeypatch.setattr("biosim_server.api.main.get_temporal_client", lambda: temporal_client)
    owner = AuthenticatedUser(sub="auth0|owner", email="owner@example.test")
    other = AuthenticatedUser(sub="auth0|other", email="other@example.test")
    admin = AuthenticatedUser(sub="auth0|admin", email="admin@example.test", roles=["admin"])
    url = "/verify/source-reverify-private/reverify"
    try:
        with patch("biosim_server.api.main.get_biosim_service", return_value=_catalog()):
            async with await _client() as client:
                for caller in (None, other):
                    _as_user(caller)
                    hidden = await client.post(url, headers={"Idempotency-Key": "hidden-key"}, json={})
                    assert hidden.status_code == 404 and "recovery" not in hidden.json()
                _as_user(owner)
                owned = await client.post(url, headers={"Idempotency-Key": "owner-key"}, json={})
                _as_user(admin)
                administered = await client.post(url, headers={"Idempotency-Key": "admin-key"}, json={})
                _as_user(owner)
                permalink = await client.get("/verify/source-reverify-private")
                listing = await client.get("/verification_ids")
                _as_user(None)
                anonymous_permalink = await client.get("/verify/source-reverify-private")
                public_listing = await client.get("/verification_ids")
        assert owned.status_code == 202 and administered.status_code == 202
        derived = await recovery_world.ledger.get_verification(administered.json()["workflow_id"])
        assert derived is not None
        assert derived.owner_sub == "auth0|owner" and derived.initiator_sub == "auth0|admin"
        assert derived.source_verification_id == "source-reverify-private"
        # The source row is untouched and its permalink still reports the original as unavailable,
        # linking to the derived verifications instead of redirecting to one.
        source_after = await recovery_world.ledger._collection.find_one({"workflow_id": "source-reverify-private"}, {"_id": 0})
        assert source_before == source_after
        assert permalink.status_code == 404
        assert "no longer retained" in permalink.json()["detail"]
        assert {item["workflow_id"] for item in permalink.json()["derived_verifications"]} == {
            owned.json()["workflow_id"], administered.json()["workflow_id"],
        }
        assert permalink.headers["cache-control"] == "private, no-store"
        assert anonymous_permalink.status_code == 404
        assert "derived_verifications" not in anonymous_permalink.json()
        assert "recovery" not in anonymous_permalink.json()
        assert all("initiator_sub" not in item for item in listing.json()["verifications"])
        assert administered.json()["workflow_id"] in listing.json()["verification_ids"]
        assert administered.json()["workflow_id"] not in public_listing.json()["verification_ids"]
    finally:
        _as_user(None)
        await _terminate_derived(recovery_world, temporal_client, "source-reverify-private")

    await _omex_source(recovery_world, "source-reverify-quota")
    try:
        with patch("biosim_server.api.main.get_biosim_service", return_value=_catalog()):
            async with await _client() as client:
                quota_url = "/verify/source-reverify-quota/reverify"
                for index in range(5):
                    response = await client.post(quota_url, headers={"Idempotency-Key": f"quota-key-{index}"}, json={})
                    assert response.status_code == 202, response.text
                limited = await client.post(quota_url, headers={"Idempotency-Key": "quota-key-extra"}, json={})
                retry = await client.post(quota_url, headers={"Idempotency-Key": "quota-key-0"}, json={})
                too_many = await client.post(
                    quota_url, headers={"Idempotency-Key": "quota-key-wide"},
                    json={"simulators": [f"sim{index}" for index in range(MAX_VERIFY_SIMULATORS + 1)]},
                )
        assert limited.status_code == 429
        assert retry.status_code == 202
        assert too_many.status_code == 422
        assert len(await recovery_world.ledger.list_derived("source-reverify-quota")) == 5
    finally:
        await _terminate_derived(recovery_world, temporal_client, "source-reverify-quota")
