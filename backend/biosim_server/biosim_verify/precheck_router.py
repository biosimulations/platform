"""`GET /verify/runs/precheck` — the server-side eligibility decision (plan §4.4).

Safe and idempotent, so GET; the same repeated query parameter as
`POST /verify/runs`, which stays the authoritative submission. The route
resolves **no** user dependency: the frontend attaches the Platform token to
every Platform request, so resolving one would 401 a lapsed session on a public
check (§4.4). Responses carry `Cache-Control: private, no-store` — run status
changes.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from pydantic import StringConstraints

from biosim_server.biosim_verify.models import MAX_VERIFY_RUN_IDS
from biosim_server.biosim_verify.precheck import (
    RunPrecheckResult,
    _reject_duplicate_selections,
    assess_runs,
    load_run_inputs,
)
from biosim_server.common.ratelimit import precheck_rate_limit

router = APIRouter()

_RUN_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"

# Item-level StringConstraints, not Query(pattern=...): Query(pattern=) on a
# list[str] passes route registration and then raises
# "Unable to apply constraint 'pattern'" on every request, and Python/pydantic
# treat \w as Unicode, so ^[\w-]{1,64}$ would accept `héllo` where the JS rule it
# ports did not. Both details verified in this venv (plan §4.4).
RunId = Annotated[str, StringConstraints(pattern=_RUN_ID_PATTERN)]


@router.get(
    "/verify/runs/precheck",
    response_model=RunPrecheckResult,
    operation_id="precheck-verify-runs",
    tags=["Verification"],
    summary="Precheck biosimulation run IDs for comparability, without starting a verification",
    description=(
        "The same eligibility decision `POST /verify/runs` makes before it starts a workflow, "
        "served on its own: each run's record is classified (not found, not succeeded, upstream "
        "unavailable), dataset overlap uses the shared name-and-SED-ML-label matching, and shared "
        "datasets with different shapes warn instead of blocking. No workflow is started, nothing is "
        "stored, and the caller is never identified: no bearer token is required, an invalid one is "
        "ignored, and responses are `Cache-Control: private, no-store`. At most "
        f"{MAX_VERIFY_RUN_IDS} distinct run IDs per call."
    ),
    dependencies=[Depends(precheck_rate_limit)],
    responses={
        200: {
            "description": "The per-run classification and the batch decision.",
            "headers": {"Cache-Control": {"description": "private, no-store", "schema": {"type": "string"}}},
        },
        400: {"description": "Duplicate run IDs."},
        429: {"description": "The per-client-IP precheck budget is exhausted; Retry-After names the wait."},
        502: {"description": "An upstream service failed or returned an invalid body; runs are classified, not failed."},
        504: {"description": "An upstream service timed out; runs are classified, not failed."},
    },
)
async def precheck_verify_runs(
    response: Response,
    biosimulations_run_ids: Annotated[
        list[RunId],
        Query(
            min_length=1,
            max_length=MAX_VERIFY_RUN_IDS,
            description=f"The biosimulations run IDs to precheck (1 to {MAX_VERIFY_RUN_IDS}, no duplicates).",
        ),
    ],
) -> RunPrecheckResult:
    # Validation first (the count and per-item ASCII rule ran before this handler):
    # a duplicate is rejected before any upstream work, exactly as on POST.
    _reject_duplicate_selections(biosimulations_run_ids, label="run IDs")
    response.headers["Cache-Control"] = "private, no-store"
    inputs = await load_run_inputs(biosimulations_run_ids)
    return assess_runs(biosimulations_run_ids, inputs)
