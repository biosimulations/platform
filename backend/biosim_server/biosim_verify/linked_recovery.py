"""Linked reconstruction and archive re-verification.

Recovery always reserves a new verification id. It does not modify the source
row, its report, or its permalink. Reconstruction compares retained snapshots
and never submits a simulator job.
"""

import errno
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar, cast

from fastapi import HTTPException, Request
from pydantic import BaseModel
from pymongo.errors import DuplicateKeyError
from temporalio.client import Client, WorkflowHandle
from temporalio.service import RPCError, RPCStatusCode

from biosim_server.biosim_omex.models import OmexFile
from biosim_server.biosim_runs.models import BiosimulatorVersion
from biosim_server.biosim_verify.database import IdempotencyConflict, VerificationDatabaseService
from biosim_server.biosim_verify.models import (
    REVERIFY_DEFAULT_ABS_TOL_MIN,
    REVERIFY_DEFAULT_ABS_TOL_SCALE,
    REVERIFY_DEFAULT_CACHE_BUSTER,
    REVERIFY_DEFAULT_DESCRIPTION,
    REVERIFY_DEFAULT_INCLUDE_OUTPUTS,
    REVERIFY_DEFAULT_REL_TOL,
    REVERIFY_DEFAULT_SIMULATORS,
    MAX_VERIFY_RUN_IDS,
    MAX_VERIFY_SNAPSHOTS,
    CompareSettings,
    DerivationKind,
    DerivedVerificationLink,
    LinkedRecoveryAccepted,
    NumericalInputSnapshot,
    ReconstructOffer,
    ReconstructRequest,
    RecoveryReservation,
    ReverifyOffer,
    ReverifyRequest,
    VerificationProvenance,
    VerificationRecord,
    VerificationRecovery,
    VerificationType,
    VerifyWorkflowOutput,
)
from biosim_server.biosim_verify.omex_verify_workflow import OmexVerifyWorkflow, OmexVerifyWorkflowInput
from biosim_server.biosim_verify.reconstruct_workflow import (
    ReconstructComparisonWorkflow,
    ReconstructComparisonWorkflowInput,
)
from biosim_server.biosim_verify.report_codec import encode_report
from biosim_server.biosim_verify.snapshots import SnapshotMismatch, SnapshotMissing, load_verified_snapshots
from biosim_server.common.auth import AuthenticatedUser
from biosim_server.common.ratelimit import _enforce_rate_limit, client_identity
from biosim_server.dependencies import (
    get_file_service,
    get_omex_database_service,
    get_verification_database_service,
    get_verification_report_store,
)

logger = logging.getLogger(__name__)

RECONSTRUCT_MESSAGE = (
    "This starts a new calculation from the retained numerical inputs. "
    "It is not an exact restoration of the original report."
)
REVERIFY_MESSAGE = (
    "This starts a new verification and can submit new simulation jobs. "
    "Omitted fields use the documented defaults listed in this response. "
    "Cached simulator versions are suggestions only: they do not guarantee cache reuse "
    "and are not proof of the original simulator selection."
)
_UNKNOWN_START = "Verification start outcome is unknown; retry this request with the same Idempotency-Key."
_InputT = TypeVar("_InputT", ReconstructComparisonWorkflowInput, OmexVerifyWorkflowInput)


class RecoveryRejected(Exception):
    """A typed refusal. No workflow is started."""

    def __init__(self, status_code: int, detail: str, code: str) -> None:
        self.status_code = status_code
        self.detail = detail
        self.code = code
        super().__init__(detail)


def reconstruction_offer(provenance: VerificationProvenance | None) -> ReconstructOffer:
    """Saved workflow input is not consulted. Missing provenance is an honest refusal."""
    if provenance is None:
        return ReconstructOffer(
            eligible=False, reason="missing_provenance",
            message="Saved request data is not enough to reconstruct a comparison. Provenance for the numerical inputs is missing.",
        )
    try:
        settings = CompareSettings.model_validate(provenance.requested_settings)
    except Exception:
        settings = None
    if (
        settings is None
        or not provenance.ordered_run_ids
        or not provenance.actual_runs
        or not provenance.input_snapshots
    ):
        reason = "missing_numerical_inputs" if provenance.actual_runs and not provenance.input_snapshots else "missing_provenance"
        message = (
            "Retained run metadata has no numerical snapshot."
            if reason == "missing_numerical_inputs"
            else "Saved request data is not enough to reconstruct a comparison. Provenance for the numerical inputs is missing."
        )
        return ReconstructOffer(eligible=False, reason=reason, message=message)
    if any(not snapshot.content_sha256 or not snapshot.artifact_reference for snapshot in provenance.input_snapshots):
        return ReconstructOffer(
            eligible=False, reason="missing_numerical_inputs",
            message="Retained numerical inputs do not include checksums.",
        )
    if len(provenance.ordered_run_ids) > MAX_VERIFY_RUN_IDS or len(provenance.input_snapshots) > MAX_VERIFY_SNAPSHOTS:
        return ReconstructOffer(
            eligible=False, reason="work_limit",
            message="The retained comparison exceeds the verification work limit.",
        )
    return ReconstructOffer(
        eligible=True, reason=None, message=RECONSTRUCT_MESSAGE,
        run_ids=list(provenance.ordered_run_ids),
        snapshot_sha256=[snapshot.content_sha256 for snapshot in provenance.input_snapshots],
        compare_settings=settings,
    )


def _suggested_simulators(provenance: VerificationProvenance | None) -> list[str]:
    if provenance is None:
        return []
    suggested: list[str] = []
    for run in provenance.actual_runs:
        version = run.biosim_sim_run.simulator_version
        label = f"{version.id}:{version.version}"
        if label not in suggested:
            suggested.append(label)
    return suggested


def documented_reverify_settings() -> CompareSettings:
    return CompareSettings(
        user_description=REVERIFY_DEFAULT_DESCRIPTION,
        include_outputs=REVERIFY_DEFAULT_INCLUDE_OUTPUTS,
        rel_tol=REVERIFY_DEFAULT_REL_TOL,
        abs_tol_min=REVERIFY_DEFAULT_ABS_TOL_MIN,
        abs_tol_scale=REVERIFY_DEFAULT_ABS_TOL_SCALE,
        observables=None,
    )


def _reverify_offer(
    provenance: VerificationProvenance | None, *, eligible: bool, reason: str | None, message: str,
) -> ReverifyOffer:
    return ReverifyOffer(
        eligible=eligible, reason=reason, message=message, may_start_simulations=True,
        cache_reuse="not_guaranteed", defaults=documented_reverify_settings(),
        default_simulators=list(REVERIFY_DEFAULT_SIMULATORS),
        default_cache_buster=REVERIFY_DEFAULT_CACHE_BUSTER,
        suggested_simulators=_suggested_simulators(provenance),
    )


def reverify_offer(provenance: VerificationProvenance | None, archive: str) -> ReverifyOffer:
    """``archive`` is ``readable``, ``missing``, ``unreadable``, or ``unavailable``."""
    if archive == "readable":
        return _reverify_offer(provenance, eligible=True, reason=None, message=REVERIFY_MESSAGE)
    if archive == "unavailable":
        return _reverify_offer(
            provenance, eligible=False, reason="dependency_unavailable",
            message="Archive storage could not be checked. Re-verification was not started.",
        )
    if archive == "unreadable":
        return _reverify_offer(
            provenance, eligible=False, reason="archive_unreadable",
            message="The stored archive could not be read. Re-verification was not started and no simulation was submitted.",
        )
    return _reverify_offer(
        provenance, eligible=False, reason="archive_not_stored",
        message=(
            "This verification has no stored archive, so re-verification is unavailable. "
            "Re-verification can start new simulations and cannot run from a saved request alone."
        ),
    )


def apply_reverify_request(body: ReverifyRequest) -> tuple[CompareSettings, list[str], list[str], str]:
    applied: list[str] = []
    simulators = body.simulators
    if simulators is None:
        simulators = list(REVERIFY_DEFAULT_SIMULATORS)
        applied.append("simulators")
    elif len(simulators) == 0:
        raise HTTPException(status_code=400, detail="At least one simulator is required.")
    include_outputs = body.include_outputs
    if include_outputs is None:
        include_outputs = REVERIFY_DEFAULT_INCLUDE_OUTPUTS
        applied.append("include_outputs")
    description = body.user_description
    if description is None:
        description = REVERIFY_DEFAULT_DESCRIPTION
        applied.append("user_description")
    rel_tol = body.rel_tol
    if rel_tol is None:
        rel_tol = REVERIFY_DEFAULT_REL_TOL
        applied.append("rel_tol")
    abs_tol_min = body.abs_tol_min
    if abs_tol_min is None:
        abs_tol_min = REVERIFY_DEFAULT_ABS_TOL_MIN
        applied.append("abs_tol_min")
    abs_tol_scale = body.abs_tol_scale
    if abs_tol_scale is None:
        abs_tol_scale = REVERIFY_DEFAULT_ABS_TOL_SCALE
        applied.append("abs_tol_scale")
    observables = body.observables
    if observables is None:
        applied.append("observables")
    cache_buster = body.cache_buster
    if cache_buster is None:
        cache_buster = REVERIFY_DEFAULT_CACHE_BUSTER
        applied.append("cache_buster")
    settings = CompareSettings(
        user_description=description, include_outputs=include_outputs, rel_tol=rel_tol,
        abs_tol_min=abs_tol_min, abs_tol_scale=abs_tol_scale, observables=observables,
    )
    return settings, simulators, applied, cache_buster


async def _provenance(workflow_id: str) -> VerificationProvenance | None:
    store = get_verification_report_store()
    if store is None:
        raise HTTPException(status_code=503, detail="Verification report service unavailable")
    try:
        return await store.get_provenance(workflow_id)
    except Exception as exc:
        logger.error("verification provenance lookup failed for %s", workflow_id)
        raise HTTPException(status_code=503, detail="Verification report service unavailable") from exc


async def _archive(record: VerificationRecord) -> tuple[str, OmexFile | None]:
    if not record.omex_hash:
        return "missing", None
    omex_db = get_omex_database_service()
    files = get_file_service()
    if omex_db is None or files is None:
        return "unavailable", None
    try:
        omex = await omex_db.get_omex_file_for_caller(record.omex_hash, owner=record.owner_sub)
    except Exception:
        logger.error("archive lookup failed for verification %s", record.workflow_id)
        return "unavailable", None
    if omex is None:
        return "missing", None
    try:
        await files.get_modified_date(omex.omex_gcs_path)
    except FileNotFoundError:
        return "unreadable", None
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            return "unreadable", None
        logger.error("archive readability check failed for verification %s", record.workflow_id)
        return "unavailable", None
    except Exception:
        logger.error("archive readability check failed for verification %s", record.workflow_id)
        return "unavailable", None
    return "readable", omex


async def build_recovery(record: VerificationRecord) -> VerificationRecovery:
    provenance: VerificationProvenance | None = None
    store = get_verification_report_store()
    if store is not None:
        try:
            provenance = await store.get_provenance(record.workflow_id)
        except Exception:
            logger.error("verification provenance lookup failed for %s", record.workflow_id)
            provenance = None
    archive, _omex = await _archive(record)
    return VerificationRecovery(
        reconstruct=reconstruction_offer(provenance),
        reverify_archive=reverify_offer(provenance, archive),
    )


def annotate_output(
    output: VerifyWorkflowOutput,
    record: VerificationRecord | None,
    provenance: VerificationProvenance | None,
    links: list[DerivedVerificationLink],
    recovery: VerificationRecovery | None,
) -> VerifyWorkflowOutput:
    """Overlay recovery metadata onto a response without writing the stored report."""
    update: dict[str, object] = {"derived_verifications": links}
    if recovery is not None:
        update["recovery"] = recovery
    if record is not None and record.source_verification_id:
        update["source_verification_id"] = record.source_verification_id
    if record is not None and record.derivation_kind:
        update["derivation_kind"] = record.derivation_kind
        if record.derivation_kind == DerivationKind.RECONSTRUCTION:
            update["calculation"] = "new_calculation"
    if provenance is not None:
        if provenance.computed_at is not None:
            update["computed_at"] = provenance.computed_at
        if provenance.comparator_version:
            update["comparator_version"] = provenance.comparator_version
        if provenance.source_verification_id and "source_verification_id" not in update:
            update["source_verification_id"] = provenance.source_verification_id
        if provenance.derivation_kind == DerivationKind.RECONSTRUCTION:
            update["calculation"] = "new_calculation"
    return output.model_copy(update=update)


async def derived_links(source_id: str) -> list[DerivedVerificationLink]:
    ledger = get_verification_database_service()
    if ledger is None:
        return []
    try:
        rows = await ledger.list_derived(source_id)
    except Exception:
        logger.error("derived verification lookup failed for %s", source_id)
        return []
    if not isinstance(rows, list):
        return []
    links: list[DerivedVerificationLink] = []
    for row in rows:
        if not isinstance(row, VerificationRecord) or row.derivation_kind not in {
            DerivationKind.RECONSTRUCTION, DerivationKind.REVERIFICATION,
        }:
            continue
        links.append(DerivedVerificationLink(
            workflow_id=row.workflow_id,
            derivation_kind=DerivationKind(row.derivation_kind),
            created=row.created,
        ))
    return links


def _request_hash(body: BaseModel) -> str:
    return encode_report(body.model_dump(mode="json"))[3]


def _reject_duplicate_run_ids(run_ids: list[str]) -> None:
    duplicates = sorted({value for value in run_ids if run_ids.count(value) > 1})
    if duplicates:
        raise HTTPException(status_code=400, detail=f"Duplicate run IDs are not allowed: {', '.join(duplicates)}")


async def _require_ledger() -> VerificationDatabaseService:
    ledger = get_verification_database_service()
    if ledger is None:
        raise HTTPException(status_code=503, detail="Verification database service not available")
    return ledger


async def _run_id_if_started(temporal: Client, workflow_id: str) -> str | None:
    try:
        desc = await temporal.get_workflow_handle(workflow_id).describe(rpc_timeout=timedelta(seconds=10))
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:
            return None
        logger.error("temporal describe failed for reserved verification %s", workflow_id)
        raise HTTPException(status_code=503, detail="Temporal service unavailable") from exc
    except Exception as exc:
        logger.error("temporal describe failed for reserved verification %s", workflow_id)
        raise HTTPException(status_code=503, detail="Temporal service unavailable") from exc
    return desc.run_id


async def _read_started_input(
    temporal: Client, workflow_id: str, run_id: str | None, workflow_type: str, input_type: type[_InputT],
) -> _InputT:
    handle: WorkflowHandle[object, VerifyWorkflowOutput] = temporal.get_workflow_handle(workflow_id, run_id=run_id)
    events = handle.fetch_history_events(page_size=1, rpc_timeout=timedelta(seconds=10))
    event = await anext(events)
    if not event.HasField("workflow_execution_started_event_attributes"):
        raise ValueError("Missing workflow start event")
    attrs = event.workflow_execution_started_event_attributes
    if attrs.workflow_type.name != workflow_type or len(attrs.input.payloads) != 1:
        raise ValueError("Unexpected workflow start input")
    decoded = await temporal.data_converter.decode(attrs.input.payloads, [input_type])
    if len(decoded) != 1 or not isinstance(decoded[0], input_type):
        raise ValueError("Invalid workflow start input")
    return decoded[0]


async def _confirm_same_start(
    temporal: Client, workflow_id: str, expected: _InputT, workflow_type: str, input_type: type[_InputT],
) -> WorkflowHandle[object, VerifyWorkflowOutput]:
    run_id = await _run_id_if_started(temporal, workflow_id)
    if run_id is None:
        raise HTTPException(status_code=503, detail=_UNKNOWN_START)
    try:
        original = await _read_started_input(temporal, workflow_id, run_id, workflow_type, input_type)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("unable to confirm reserved verification %s", workflow_id)
        raise HTTPException(status_code=503, detail=_UNKNOWN_START) from exc
    if original != expected or original.owner_sub != expected.owner_sub:
        raise HTTPException(status_code=503, detail=_UNKNOWN_START)
    return temporal.get_workflow_handle(workflow_id, run_id=run_id)


async def _insert_or_match(record: VerificationRecord) -> None:
    ledger = await _require_ledger()
    try:
        await ledger.insert_verification(record)
    except DuplicateKeyError:
        existing = await ledger.get_verification(record.workflow_id)
        if (
            existing is None
            or existing.source_verification_id != record.source_verification_id
            or existing.owner_sub != record.owner_sub
            or existing.derivation_kind != record.derivation_kind
        ):
            raise HTTPException(status_code=503, detail=_UNKNOWN_START)


async def _reserve_workflow_id(
    *,
    request: Request,
    user: AuthenticatedUser | None,
    source_id: str,
    operation: str,
    idempotency_key: str,
    request_hash: str,
    prefix: str,
) -> tuple[str, bool]:
    """Return ``(workflow_id, already_reserved)``.

    A new reservation is rate-limited on the shared workflow budget. A retry of
    the same key is not, so a lost reply can converge after the quota is spent.
    """
    ledger = await _require_ledger()
    caller_key, _authenticated = client_identity(user, request)
    try:
        existing = await ledger.find_recovery(caller_key, source_id, operation, idempotency_key)
    except Exception as exc:
        logger.error("idempotency lookup failed for %s", source_id)
        raise HTTPException(status_code=503, detail="Verification database service not available") from exc
    if existing is not None:
        if existing.request_hash != request_hash:
            raise RecoveryRejected(
                409, "This idempotency key was already used for a different request.", "idempotency_conflict",
            )
        return existing.workflow_id, True
    # Charge only a reservation that does not already exist. Concurrent identical
    # requests may each pass this check; the unique index then keeps one id.
    _enforce_rate_limit(request, user)
    workflow_id = f"{prefix}{uuid.uuid4()}"
    try:
        stored, _created = await ledger.reserve_recovery(RecoveryReservation(
            caller_key=caller_key, source_verification_id=source_id, operation=operation,
            idempotency_key=idempotency_key, request_hash=request_hash, workflow_id=workflow_id,
            created=datetime.now(UTC),
        ))
    except IdempotencyConflict as exc:
        raise RecoveryRejected(
            409, "This idempotency key was already used for a different request.", "idempotency_conflict",
        ) from exc
    except Exception as exc:
        logger.error("idempotency reservation failed for %s", source_id)
        raise HTTPException(status_code=503, detail="Verification database service not available") from exc
    return stored.workflow_id, stored.workflow_id != workflow_id


async def _start_reserved(
    *,
    temporal: Client,
    workflow_id: str,
    record: VerificationRecord,
    workflow_input: _InputT,
    workflow_type: str,
    input_type: type[_InputT],
    start_workflow: Callable[[], Awaitable[WorkflowHandle[object, VerifyWorkflowOutput]]],
) -> WorkflowHandle[object, VerifyWorkflowOutput]:
    await _insert_or_match(record)
    try:
        handle = await start_workflow()
    except RPCError as exc:
        if exc.status == RPCStatusCode.INVALID_ARGUMENT:
            raise HTTPException(status_code=503, detail="Failed to start verification workflow") from exc
        return await _confirm_same_start(temporal, workflow_id, workflow_input, workflow_type, input_type)
    except Exception as exc:
        logger.warning("verification start outcome is unknown for %s", workflow_id)
        try:
            return await _confirm_same_start(temporal, workflow_id, workflow_input, workflow_type, input_type)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=503, detail=_UNKNOWN_START) from exc
    ledger = await _require_ledger()
    run_id = handle.run_id
    if run_id:
        try:
            await ledger.record_workflow_run_id(workflow_id, run_id)
        except Exception:
            logger.warning("could not record workflow run id for %s", workflow_id)
    return handle


def _record(
    workflow_id: str, source: VerificationRecord, kind: DerivationKind, user: AuthenticatedUser | None,
    workflow_input: BaseModel, omex_hash: str | None,
) -> VerificationRecord:
    dumped = workflow_input.model_dump(mode="json")
    return VerificationRecord(
        workflow_id=workflow_id, verify_type=VerificationType.OMEX if kind == DerivationKind.REVERIFICATION else VerificationType.RUNS,
        owner_sub=source.owner_sub, initiator_sub=None if user is None else user.sub,
        created=datetime.now(UTC), omex_hash=omex_hash, source_verification_id=source.workflow_id,
        derivation_kind=kind.value, workflow_input=dumped, request_hash=encode_report(dumped)[3],
    )


def _accepted(
    *,
    workflow_id: str,
    source_id: str,
    kind: DerivationKind,
    run_id: str | None,
    settings: CompareSettings,
    may_start_simulations: bool,
    defaults_applied: list[str],
    simulators: list[str],
    message: str,
) -> LinkedRecoveryAccepted:
    return LinkedRecoveryAccepted(
        workflow_id=workflow_id, source_verification_id=source_id, derivation_kind=kind,
        workflow_run_id=run_id, may_start_simulations=may_start_simulations,
        defaults_applied=defaults_applied, compare_settings=settings, simulators=simulators,
        cache_reuse="not_guaranteed" if may_start_simulations else None,
        calculation=None if may_start_simulations else "new_calculation",
        message=message,
    )


async def _matching_snapshots(body: ReconstructRequest, provenance: VerificationProvenance) -> ReconstructOffer:
    offer = reconstruction_offer(provenance)
    if not offer.eligible:
        raise RecoveryRejected(409, offer.message, offer.reason or "missing_provenance")
    _reject_duplicate_run_ids(body.run_ids)
    recorded = offer.compare_settings.model_dump() if offer.compare_settings is not None else None
    if (
        body.run_ids != offer.run_ids
        or body.snapshot_sha256 != offer.snapshot_sha256
        or body.compare_settings.model_dump() != recorded
    ):
        raise RecoveryRejected(
            409, "Reconstruction inputs do not match the retained provenance.", "input_mismatch",
        )
    return offer


async def _verify_snapshot_bytes(snapshots: list[NumericalInputSnapshot]) -> None:
    if get_file_service() is None:
        raise HTTPException(status_code=503, detail="Snapshot storage is unavailable")
    try:
        await load_verified_snapshots(snapshots)
    except SnapshotMissing as exc:
        raise RecoveryRejected(
            409, "Retained numerical inputs are missing. No simulator job was submitted.", "missing_numerical_inputs",
        ) from exc
    except SnapshotMismatch as exc:
        raise RecoveryRejected(
            409, "Retained numerical inputs failed checksum verification. No simulator job was submitted.",
            "checksum_mismatch",
        ) from exc
    except RecoveryRejected:
        raise
    except Exception as exc:
        logger.error("snapshot verification could not be completed")
        raise HTTPException(status_code=503, detail="Snapshot storage is unavailable") from exc


async def _peek_reservation(
    *,
    request: Request,
    user: AuthenticatedUser | None,
    source_id: str,
    operation: str,
    idempotency_key: str,
    request_hash: str,
) -> RecoveryReservation | None:
    ledger = await _require_ledger()
    caller_key, _authenticated = client_identity(user, request)
    try:
        existing = await ledger.find_recovery(caller_key, source_id, operation, idempotency_key)
    except Exception as exc:
        logger.error("idempotency lookup failed for %s", source_id)
        raise HTTPException(status_code=503, detail="Verification database service not available") from exc
    if existing is not None and existing.request_hash != request_hash:
        raise RecoveryRejected(
            409, "This idempotency key was already used for a different request.", "idempotency_conflict",
        )
    return existing


async def accept_reconstruction(
    *,
    request: Request,
    user: AuthenticatedUser | None,
    source: VerificationRecord,
    body: ReconstructRequest,
    idempotency_key: str,
    temporal: Client,
) -> LinkedRecoveryAccepted:
    request_hash = _request_hash(body)
    existing = await _peek_reservation(
        request=request, user=user, source_id=source.workflow_id, operation=DerivationKind.RECONSTRUCTION,
        idempotency_key=idempotency_key, request_hash=request_hash,
    )
    if existing is not None:
        run_id = await _run_id_if_started(temporal, existing.workflow_id)
        if run_id is not None:
            return _accepted(
                workflow_id=existing.workflow_id, source_id=source.workflow_id, kind=DerivationKind.RECONSTRUCTION,
                run_id=run_id, settings=body.compare_settings, may_start_simulations=False, defaults_applied=[],
                simulators=[], message=RECONSTRUCT_MESSAGE,
            )
    provenance = await _provenance(source.workflow_id)
    if provenance is None:
        raise RecoveryRejected(
            409,
            "Saved request data is not enough to reconstruct a comparison. Provenance for the numerical inputs is missing.",
            "missing_provenance",
        )
    offer = await _matching_snapshots(body, provenance)
    # Refuse before reserving an id when the bytes are missing or do not match.
    await _verify_snapshot_bytes(provenance.input_snapshots)
    workflow_id = existing.workflow_id if existing is not None else (await _reserve_workflow_id(
        request=request, user=user, source_id=source.workflow_id, operation=DerivationKind.RECONSTRUCTION,
        idempotency_key=idempotency_key, request_hash=request_hash, prefix="reconstruction-",
    ))[0]
    settings = offer.compare_settings if offer.compare_settings is not None else body.compare_settings
    workflow_input = ReconstructComparisonWorkflowInput(
        source_verification_id=source.workflow_id, compare_settings=settings,
        snapshots=list(provenance.input_snapshots), sim_run_info=list(provenance.actual_runs),
        ordered_run_ids=list(provenance.ordered_run_ids), owner_sub=source.owner_sub,
    )
    handle = await _start_reserved(
        temporal=temporal, workflow_id=workflow_id,
        record=_record(workflow_id, source, DerivationKind.RECONSTRUCTION, user, workflow_input, source.omex_hash),
        workflow_input=workflow_input, workflow_type="ReconstructComparisonWorkflow",
        input_type=ReconstructComparisonWorkflowInput,
        start_workflow=lambda: temporal.start_workflow(
            cast(Any, ReconstructComparisonWorkflow.run),
            args=[workflow_input], id=workflow_id, task_queue="verification_tasks",
        ),
    )
    return _accepted(
        workflow_id=workflow_id, source_id=source.workflow_id, kind=DerivationKind.RECONSTRUCTION,
        run_id=handle.run_id, settings=settings, may_start_simulations=False, defaults_applied=[],
        simulators=[], message=RECONSTRUCT_MESSAGE,
    )


async def accept_reverification(
    *,
    request: Request,
    user: AuthenticatedUser | None,
    source: VerificationRecord,
    body: ReverifyRequest,
    idempotency_key: str,
    temporal: Client,
    resolve_simulators: Callable[[list[str]], Awaitable[list[BiosimulatorVersion]]],
) -> LinkedRecoveryAccepted:
    settings, simulator_names, defaults_applied, cache_buster = apply_reverify_request(body)
    request_hash = _request_hash(body)
    existing = await _peek_reservation(
        request=request, user=user, source_id=source.workflow_id, operation=DerivationKind.REVERIFICATION,
        idempotency_key=idempotency_key, request_hash=request_hash,
    )
    if existing is not None:
        run_id = await _run_id_if_started(temporal, existing.workflow_id)
        if run_id is not None:
            return _accepted(
                workflow_id=existing.workflow_id, source_id=source.workflow_id, kind=DerivationKind.REVERIFICATION,
                run_id=run_id, settings=settings, may_start_simulations=True,
                defaults_applied=defaults_applied, simulators=simulator_names, message=REVERIFY_MESSAGE,
            )
    archive, omex = await _archive(source)
    if archive == "unavailable":
        raise HTTPException(status_code=503, detail="Archive storage is unavailable")
    if archive == "unreadable":
        raise RecoveryRejected(
            409,
            "The stored archive could not be read. Re-verification was not started and no simulation was submitted.",
            "archive_unreadable",
        )
    if archive != "readable" or omex is None:
        raise RecoveryRejected(
            409,
            "This verification has no stored archive. Re-verification was not started and no simulation was submitted.",
            "archive_not_stored",
        )
    resolved = await resolve_simulators(simulator_names)
    workflow_id = existing.workflow_id if existing is not None else (await _reserve_workflow_id(
        request=request, user=user, source_id=source.workflow_id, operation=DerivationKind.REVERIFICATION,
        idempotency_key=idempotency_key, request_hash=request_hash, prefix="reverification-",
    ))[0]
    workflow_input = OmexVerifyWorkflowInput(
        omex_file=omex, requested_simulators=resolved, cache_buster=cache_buster,
        compare_settings=settings, owner_sub=source.owner_sub,
    )
    handle = await _start_reserved(
        temporal=temporal, workflow_id=workflow_id,
        record=_record(workflow_id, source, DerivationKind.REVERIFICATION, user, workflow_input, source.omex_hash),
        workflow_input=workflow_input, workflow_type="OmexVerifyWorkflow", input_type=OmexVerifyWorkflowInput,
        start_workflow=lambda: temporal.start_workflow(
            cast(Any, OmexVerifyWorkflow.run),
            args=[workflow_input], id=workflow_id, task_queue="verification_tasks",
        ),
    )
    return _accepted(
        workflow_id=workflow_id, source_id=source.workflow_id, kind=DerivationKind.REVERIFICATION,
        run_id=handle.run_id, settings=settings, may_start_simulations=True,
        defaults_applied=defaults_applied,
        simulators=[f"{simulator.id}:{simulator.version}" for simulator in resolved],
        message=REVERIFY_MESSAGE,
    )
