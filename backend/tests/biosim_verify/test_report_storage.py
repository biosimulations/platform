from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from biosim_server.biosim_verify.models import CompareSettings, VerificationProvenance, VerifyWorkflowOutput, VerifyWorkflowStatus
from biosim_server.biosim_verify.report_storage import ReportCorrupt, ReportIntegrityConflict, VerificationReportStore
from biosim_server.common.storage.file_service_local import FileServiceLocal


def _output(run_id: str = "run-1", owner: str | None = "owner") -> VerifyWorkflowOutput:
    return VerifyWorkflowOutput(
        workflow_id="wf-1",
        workflow_run_id=run_id,
        owner_sub=owner,
        compare_settings=CompareSettings(
            user_description="test", include_outputs=False, rel_tol=0.1, abs_tol_min=0.1, abs_tol_scale=0.1,
        ),
        workflow_status=VerifyWorkflowStatus.COMPLETED,
        timestamp=datetime.now(UTC).isoformat(),
    )


def _provenance(output: VerifyWorkflowOutput) -> VerificationProvenance:
    return VerificationProvenance(
        verification_id=output.workflow_id,
        workflow_run_id=output.workflow_run_id,
        owner_sub=output.owner_sub,
        workflow_input={"compare_settings": output.compare_settings.model_dump()},
    )


@pytest.mark.asyncio
async def test_report_store_conflicts_on_identity_or_content(
    mongo_test_client: Any,
) -> None:
    store = VerificationReportStore(mongo_test_client)
    await store.ensure_indexes()
    output = _output()
    await store.save(output, _provenance(output))
    with pytest.raises(ReportIntegrityConflict):
        await store.save(output.model_copy(update={"workflow_run_id": "other"}), _provenance(output.model_copy(update={"workflow_run_id": "other"})))
    with pytest.raises(ReportIntegrityConflict):
        await store.save(output.model_copy(update={"workflow_status": VerifyWorkflowStatus.FAILED}), _provenance(output))
    await store.delete_all()


@pytest.mark.asyncio
async def test_report_store_rejects_corrupt_inline_payload(mongo_test_client: Any) -> None:
    store = VerificationReportStore(mongo_test_client)
    await store.ensure_indexes()
    output = _output()
    envelope = await store.save(output, _provenance(output))
    await store._collection.update_one({"report_id": envelope.report_id}, {"$set": {"inline_payload": b"corrupt"}})
    with pytest.raises(ReportCorrupt):
        await store.get(output.workflow_id)
    await store.delete_all()


@pytest.mark.asyncio
async def test_report_store_verifies_external_artifact(mongo_test_client: Any, tmp_path: Path) -> None:
    store = VerificationReportStore(mongo_test_client, FileServiceLocal(tmp_path / "objects"))
    await store.ensure_indexes()
    store._settings.verification_report_inline_max_bytes = 1
    output = _output()
    envelope = await store.save(output, _provenance(output))
    assert envelope.artifact_reference is not None
    restored = await store.get(output.workflow_id)
    assert restored is not None
    assert restored.workflow_id == output.workflow_id
    await store.delete_all()


@pytest.mark.asyncio
async def test_concurrent_publication_and_operational_state_are_idempotent(mongo_test_client: Any) -> None:
    import asyncio
    store = VerificationReportStore(mongo_test_client)
    await store.ensure_indexes()
    output = _output()
    results = await asyncio.gather(*(store.save(output, _provenance(output)) for _ in range(12)))
    assert len({r.content_sha256 for r in results}) == 1
    assert await store._collection.count_documents({"workflow_id": output.workflow_id}) == 1
    restored = await store.get(output.workflow_id)
    assert restored is not None
    await store.save(restored, _provenance(restored))
    other = output.model_copy(update={"owner_sub": "another-owner"})
    with pytest.raises(ReportIntegrityConflict):
        await store.save(other, _provenance(other))
    await store.delete_all()


@pytest.mark.asyncio
async def test_report_roundtrip_preserves_nonfinite_scientific_results(mongo_test_client: Any) -> None:
    import math
    from biosim_server.biosim_verify.models import ComparisonStatistics, GenerateStatisticsActivityOutput
    store = VerificationReportStore(mongo_test_client)
    await store.ensure_indexes()
    output = _output()
    output.workflow_results = GenerateStatisticsActivityOutput(sims_run_info=[], comparison_statistics={
        "model.sedml/report": [[ComparisonStatistics(dataset_name="model.sedml/report", simulator_version_i="a:1",
            simulator_version_j="b:1", var_names=["x", "y", "z"], score=[float('nan'), float('inf'), -float('inf')],
            is_close=[False, False, False])]],
    })
    await store.save(output, _provenance(output))
    restored = await store.get(output.workflow_id)
    assert restored is not None and restored.workflow_results is not None
    scores = restored.workflow_results.comparison_statistics['model.sedml/report'][0][0].score
    assert scores is not None and math.isnan(scores[0]) and scores[1:] == [float('inf'), -float('inf')]
    await store.delete_all()
