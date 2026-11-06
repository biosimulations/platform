"""The shared run-precheck decision: one loader, one overlap verdict (plan §4.4).

`GET /verify/runs/precheck` and `POST /verify/runs` both load run inputs through
`load_run_inputs` and both decide dataset overlap through the same
`find_common_datasets` matching (name **and** `sedmlDataSetLabels`), so the
precheck can never be stricter or looser than the submission:

* the precheck asks for run records too and classifies every outcome;
* the submission passes ``include_run_records=False`` -- its 400 conditions are
  unchanged -- and a metadata fetch failure is still a skip.

`_reject_duplicate_selections` moved here so both routes (and the
simulator-selection routes in `api/main.py`) import one copy.
"""

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, ValidationError
from pydantic.alias_generators import to_camel

from biosim_server.biosim_runs.models import HDF5File
from biosim_server.biosim_verify.compatibility import find_common_datasets
from biosim_server.common.upstream import fetch_upstream_json, upstream_url
from biosim_server.dependencies import get_database_service, get_http_client, get_simdata_http_client

logger = logging.getLogger(__name__)

# One 30 s upstream hop (the per-phase httpx timeout) plus 10 s of slack: the run
# page's budget formula. Runs still unresolved when it runs out are classified
# ``upstream_unavailable``, never silently dropped.
PRECHECK_TIME_BUDGET_SECONDS: Final[float] = 40.0


def _reject_duplicate_selections(values: list[str], *, label: str) -> None:
    """400 when a selection repeats: a duplicate has no meaning in a pairwise
    comparison and would only multiply workflow work for one quota unit."""
    duplicates = sorted({value for value in values if values.count(value) > 1})
    if duplicates:
        raise HTTPException(
            status_code=400, detail=f"Duplicate {label} are not allowed: {', '.join(duplicates)}"
        )


class _Outcome(StrEnum):
    """How one upstream fetch ended. Classified, never swallowed (plan §4.4)."""

    OK = "ok"
    NOT_FOUND = "not_found"
    UNAVAILABLE = "unavailable"


class _UpstreamRunRecord(BaseModel):
    """The three fields the precheck reads from `api.biosimulations.org/runs/{id}`.

    ``email`` and every other upstream field are dropped at parse time and are
    never read, logged, or echoed (plan §3.3, §4.4).
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")

    status: str | None = None
    simulator: str | None = None
    simulator_version: str | None = None


@dataclass
class RunInputs:
    """What one run contributed to the decision.

    ``record_outcome`` is ``None`` when run records were not requested (the
    submission path). A ``None`` ``hdf5_file`` means the metadata was unavailable
    or invalid: the precheck serves a warning, the submission skips the run.
    """

    hdf5_file: HDF5File | None = None
    record: _UpstreamRunRecord | None = None
    record_outcome: _Outcome | None = None
    metadata_outcome: _Outcome | None = None


class PrecheckExclusion(StrEnum):
    NOT_FOUND = "not_found"
    NOT_SUCCEEDED = "not_succeeded"
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"


class PrecheckIneligible(StrEnum):
    FEWER_THAN_TWO_ELIGIBLE_RUNS = "fewer_than_two_eligible_runs"
    NO_COMMON_DATASETS = "no_common_datasets"


class PrecheckDataset(BaseModel):
    name: str
    shape: list[int]
    sedml_data_set_ids: list[str] = []      # feeds the UI's dataset_var_names (today's source)
    sedml_data_set_labels: list[str] = []   # what find_common_datasets matches on


class PrecheckRun(BaseModel):
    run_id: str
    eligible: bool
    simulator: str | None = None            # "vcell:7.7.0.13" when known (the UI parses id:version)
    run_status: str | None = None           # upstream status, upper-cased, when known
    exclusion: PrecheckExclusion | None = None
    reason: str | None = None               # UI copy for an exclusion (signed off in 0.3)
    datasets: list[PrecheckDataset] | None = None   # None = metadata unavailable


class PrecheckWarning(BaseModel):
    code: Literal["metadata_unavailable", "shape_mismatch"]
    run_ids: list[str]
    dataset: str | None = None


class RunPrecheckResult(BaseModel):
    runs: list[PrecheckRun]                 # request order
    eligible: bool
    ineligible_reason: PrecheckIneligible | None = None
    common_datasets: dict[str, list[str]]   # find_common_datasets over eligible runs with metadata
    warnings: list[PrecheckWarning] = []


# The per-exclusion UI copy signed off in Phase 0 decision 0.3.
_NOT_FOUND_REASON: Final[str] = "Run not found in BioSimulations."
_UPSTREAM_UNAVAILABLE_REASON: Final[str] = "Could not check this run. Please retry."


def _not_succeeded_reason(status: str) -> str:
    return f"Run status is {status}; only successful runs can be compared."


def _precheck_datasets(hdf5_file: HDF5File) -> list[PrecheckDataset]:
    """Flatten one HDF5File into the wire datasets, in upstream (group) order."""
    datasets: list[PrecheckDataset] = []
    for group in hdf5_file.groups:
        for dataset in group.datasets:
            raw_ids = next((attr.value for attr in dataset.attributes if attr.key == "sedmlDataSetIds"), [])
            data_set_ids = [str(value) for value in raw_ids] if isinstance(raw_ids, list) else []
            datasets.append(
                PrecheckDataset(
                    name=dataset.name,
                    shape=dataset.shape,
                    sedml_data_set_ids=data_set_ids,
                    sedml_data_set_labels=dataset.sedml_labels,
                )
            )
    return datasets


async def _load_run_record(run_id: str) -> tuple[_Outcome, _UpstreamRunRecord | None]:
    try:
        payload = await fetch_upstream_json(
            get_http_client(), upstream_url("runs", run_id), resource="run record"
        )
    except HTTPException as exc:
        return (_Outcome.NOT_FOUND, None) if exc.status_code == 404 else (_Outcome.UNAVAILABLE, None)
    try:
        return _Outcome.OK, _UpstreamRunRecord.model_validate(payload)
    except ValidationError:
        logger.warning("The upstream run record for %s did not match the precheck contract", run_id, exc_info=True)
        return _Outcome.UNAVAILABLE, None


async def _load_run_metadata(run_id: str, db_service: object) -> tuple[_Outcome, HDF5File | None]:
    hdf5: HDF5File | None = None
    # The Mongo cache first, as today; a cache failure falls through to the live fetch.
    get_biosim_run_rows = getattr(db_service, "get_biosimulator_workflow_runs_by_biosim_runid", None)
    if callable(get_biosim_run_rows):
        try:
            for cached in await get_biosim_run_rows(run_id):
                if cached.hdf5_file is not None:
                    hdf5 = cached.hdf5_file
                    break
        except Exception:
            logger.warning("Cached verification metadata unavailable for %s", run_id, exc_info=True)
    if hdf5 is not None:
        return _Outcome.OK, hdf5
    # The live fallback is the capped httpx path on the simdata client (plan §4.4),
    # replacing the uncapped aiohttp fetch. A failure here is "skip" for the
    # submission and a warning for the precheck -- never a swallowed error.
    try:
        payload = await fetch_upstream_json(
            get_simdata_http_client(), upstream_url("datasets", run_id, "metadata"), resource="run metadata"
        )
        return _Outcome.OK, HDF5File.model_validate(payload)
    except (HTTPException, ValidationError, ValueError):
        logger.warning("Verification metadata unavailable for %s", run_id, exc_info=True)
        return _Outcome.UNAVAILABLE, None


async def _load_one_run(run_id: str, db_service: object, include_run_records: bool) -> RunInputs:
    if not include_run_records:
        metadata_outcome, hdf5_file = await _load_run_metadata(run_id, db_service)
        return RunInputs(hdf5_file=hdf5_file, metadata_outcome=metadata_outcome)
    # The record and the metadata are independent, so they load side by side: one run is
    # one upstream hop deep, which is what PRECHECK_TIME_BUDGET_SECONDS is sized for.
    async with asyncio.TaskGroup() as group:
        record_task = group.create_task(_load_run_record(run_id))
        metadata_task = group.create_task(_load_run_metadata(run_id, db_service))
    record_outcome, record = record_task.result()
    metadata_outcome, hdf5_file = metadata_task.result()
    return RunInputs(
        hdf5_file=hdf5_file, record=record, record_outcome=record_outcome, metadata_outcome=metadata_outcome
    )


async def load_run_inputs(run_ids: list[str], *, include_run_records: bool = True) -> dict[str, RunInputs]:
    """Load one entry per requested run, concurrently, inside the precheck time budget.

    Every run ends classified: a run still unresolved when the budget runs out is
    ``upstream_unavailable`` (plan §4.4). The submission calls this with
    ``include_run_records=False`` -- it needs only the metadata, and its 400
    conditions are unchanged.
    """
    ordered_ids = list(dict.fromkeys(run_ids))
    db_service = get_database_service()
    tasks = {
        run_id: asyncio.create_task(_load_one_run(run_id, db_service, include_run_records))
        for run_id in ordered_ids
    }
    done, pending = await asyncio.wait(tasks.values(), timeout=PRECHECK_TIME_BUDGET_SECONDS)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    results: dict[str, RunInputs] = {}
    for run_id in ordered_ids:
        task = tasks[run_id]
        if task in done and not task.cancelled() and task.exception() is None:
            results[run_id] = task.result()
        else:
            if task in done and task.exception() is not None:
                logger.error("The precheck loader raised for %s", run_id, exc_info=task.exception())
            results[run_id] = RunInputs(
                record_outcome=_Outcome.UNAVAILABLE if include_run_records else None,
                metadata_outcome=_Outcome.UNAVAILABLE,
            )
    return results


def assess_runs(run_ids: list[str], inputs: Mapping[str, RunInputs]) -> RunPrecheckResult:
    """The pure decision over loaded inputs (plan §4.4): per-run exclusions, the
    shared overlap verdict, and the shape/metadata warnings. Request order kept."""
    runs: list[PrecheckRun] = []
    metadata_unavailable: list[str] = []

    for run_id in run_ids:
        item = inputs.get(run_id) or RunInputs()
        if item.record_outcome is None:
            raise ValueError(
                "assess_runs requires classified run records; "
                "call load_run_inputs with include_run_records=True"
            )
        record = item.record
        simulator = (
            f"{record.simulator}:{record.simulator_version}"
            if record is not None and record.simulator and record.simulator_version
            else None
        )
        run_status = record.status.upper() if record is not None and record.status else None
        if item.record_outcome == _Outcome.NOT_FOUND:
            runs.append(
                PrecheckRun(
                    run_id=run_id, eligible=False, simulator=simulator, run_status=run_status,
                    exclusion=PrecheckExclusion.NOT_FOUND, reason=_NOT_FOUND_REASON,
                )
            )
            continue
        if item.record_outcome == _Outcome.UNAVAILABLE or record is None:
            runs.append(
                PrecheckRun(
                    run_id=run_id, eligible=False, simulator=simulator, run_status=run_status,
                    exclusion=PrecheckExclusion.UPSTREAM_UNAVAILABLE, reason=_UPSTREAM_UNAVAILABLE_REASON,
                )
            )
            continue
        if run_status != "SUCCEEDED":
            status = run_status or "UNKNOWN"
            runs.append(
                PrecheckRun(
                    run_id=run_id, eligible=False, simulator=simulator, run_status=status,
                    exclusion=PrecheckExclusion.NOT_SUCCEEDED, reason=_not_succeeded_reason(status),
                )
            )
            continue
        runs.append(
            PrecheckRun(
                run_id=run_id, eligible=True, simulator=simulator, run_status=run_status,
                datasets=_precheck_datasets(item.hdf5_file) if item.hdf5_file is not None else None,
            )
        )
        if item.hdf5_file is None:
            metadata_unavailable.append(run_id)

    eligible_runs = [run for run in runs if run.eligible]
    metadata_by_run = {
        run.run_id: loaded.hdf5_file
        for run in eligible_runs
        if (loaded := inputs.get(run.run_id)) is not None and loaded.hdf5_file is not None
    }
    # The shared overlap verdict. It only applies once two runs carry metadata:
    # with fewer, the submission skips the check (unchanged 400 conditions) and
    # the precheck reports the metadata warning instead (Phase 0 decision 0.3).
    common = find_common_datasets(metadata_by_run) if len(metadata_by_run) >= 2 else {}

    if len(eligible_runs) < 2:
        batch_eligible, ineligible_reason = False, PrecheckIneligible.FEWER_THAN_TWO_ELIGIBLE_RUNS
    elif len(metadata_by_run) >= 2 and not common:
        batch_eligible, ineligible_reason = False, PrecheckIneligible.NO_COMMON_DATASETS
    else:
        batch_eligible, ineligible_reason = True, None

    warnings: list[PrecheckWarning] = []
    if common:
        for dataset_name in common:
            shapes: dict[tuple[int, ...], list[str]] = {}
            for run in eligible_runs:
                loaded = inputs.get(run.run_id)
                if loaded is None or loaded.hdf5_file is None:
                    continue
                dataset = loaded.hdf5_file.datasets.get(dataset_name)
                if dataset is not None:
                    shapes.setdefault(tuple(dataset.shape), []).append(run.run_id)
            if len(shapes) > 1:
                warnings.append(
                    PrecheckWarning(
                        code="shape_mismatch",
                        run_ids=[run_id for ids in shapes.values() for run_id in ids],
                        dataset=dataset_name,
                    )
                )
    if metadata_unavailable:
        warnings.append(PrecheckWarning(code="metadata_unavailable", run_ids=metadata_unavailable))

    return RunPrecheckResult(
        runs=runs,
        eligible=batch_eligible,
        ineligible_reason=ineligible_reason,
        common_datasets=common,
        warnings=warnings,
    )
