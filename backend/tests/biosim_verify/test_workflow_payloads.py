"""Statistics stay out of Temporal payloads once a report is stored (WP-4, I-3)."""
import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from temporalio.api.common.v1 import Payload
from temporalio.api.history.v1 import HistoryEvent

from biosim_server.biosim_verify.activities import externalize_statistics
from biosim_server.biosim_verify.models import (
    GenerateStatisticsActivityOutput,
    GenerateStatisticsActivityResult,
    ReportPersistenceState,
    VerifyWorkflowOutput,
)
from biosim_server.biosim_verify.report_codec import encode_report
from biosim_server.common.storage.file_service_local import FileServiceLocal
from biosim_server.common.temporal import pydantic_data_converter
from biosim_server.config import get_settings
from tests.biosim_verify.test_persistence_failures import World, world  # noqa: F401

pytestmark = pytest.mark.filterwarnings("error::UserWarning")


def _completed_results(events: list[HistoryEvent], activity_type: str) -> list[Payload]:
    """Result payloads of every completed activity of *activity_type*."""
    scheduled = {event.event_id for event in events
                 if event.HasField("activity_task_scheduled_event_attributes")
                 and event.activity_task_scheduled_event_attributes.activity_type.name == activity_type}
    return [event.activity_task_completed_event_attributes.result.payloads[0] for event in events
            if event.HasField("activity_task_completed_event_attributes")
            and event.activity_task_completed_event_attributes.scheduled_event_id in scheduled]


def _workflow_result(events: list[HistoryEvent]) -> Payload:
    [completed] = [event for event in events if event.HasField("workflow_execution_completed_event_attributes")]
    return completed.workflow_execution_completed_event_attributes.result.payloads[0]


async def _decode(payload: Payload, type_hint: type[Any]) -> Any:
    [value] = await pydantic_data_converter.decode([payload], [type_hint])
    return value


def _activity_objects(objects: FileServiceLocal) -> list[Path]:
    return list((objects.base_dir / "verification-activity").glob("*"))


@pytest.mark.integration_local
@pytest.mark.asyncio
@pytest.mark.parametrize("statistics", ["inline", "externalized"])
async def test_no_statistics_cross_temporal_after_the_report_is_stored(
    world: World, monkeypatch: pytest.MonkeyPatch, statistics: str,  # noqa: F811
) -> None:
    if statistics == "externalized":
        monkeypatch.setattr(get_settings(), "verification_activity_inline_max_bytes", 1)
    handle = await world.start()
    await asyncio.wait_for(handle.result(), 30)
    events = list((await handle.fetch_history()).events)

    # The statistics activity: inline under the threshold, an object reference over it.
    [statistics_payload] = _completed_results(events, "generate_statistics_activity_v2")
    computed = await _decode(statistics_payload, GenerateStatisticsActivityResult)
    if statistics == "inline":
        assert computed.output is not None and computed.reference is None
        assert _activity_objects(world.objects) == []  # no transient object was written
    else:
        assert computed.output is None and computed.reference is not None
        assert await world.objects.get_file_contents(computed.reference.artifact_reference) is not None

    # After persistence: the activity result, the workflow result and the final query.
    [persisted_payload] = _completed_results(events, "persist_verification_report_activity")
    final_query = await handle.query("get_output", result_type=VerifyWorkflowOutput)
    slim = [await _decode(persisted_payload, VerifyWorkflowOutput),
            await _decode(_workflow_result(events), VerifyWorkflowOutput), final_query]
    for output in slim:
        assert output.persistence_state == ReportPersistenceState.STORED and output.report_available
        assert output.workflow_results is None and output.workflow_results_reference is None
    for payload in (persisted_payload, _workflow_result(events)):
        assert b"comparison_statistics" not in payload.data

    # The stored report is the authoritative copy, and it has the statistics.
    saved = await world.store.get(handle.id)
    assert saved is not None and saved.workflow_results is not None
    assert saved.workflow_results.comparison_statistics


def _statistics() -> GenerateStatisticsActivityOutput:
    return GenerateStatisticsActivityOutput(sims_run_info=[], comparison_statistics={"ds": []})


@pytest.mark.asyncio
@pytest.mark.parametrize("headroom, inline", [(0, True), (-1, False)])
async def test_inline_threshold_is_inclusive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, headroom: int, inline: bool,
) -> None:
    output = _statistics()
    size = encode_report(output.model_dump(mode="json"))[2]
    monkeypatch.setattr(get_settings(), "verification_activity_inline_max_bytes", size + headroom)
    objects = FileServiceLocal(tmp_path)
    with patch("biosim_server.biosim_verify.activities.get_file_service", return_value=objects):
        result = await externalize_statistics(output)
    assert (result.output is not None) is inline
    assert (result.reference is not None) is not inline
    assert bool(list(tmp_path.glob("verification-activity/*"))) is not inline
