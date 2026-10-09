import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, UTC, timedelta
from typing import Any, AsyncGenerator, Awaitable, Callable, NoReturn, Optional

import dotenv
import uvicorn
from temporalio.client import (
    Client,
    WorkflowExecutionDescription,
    WorkflowExecutionStatus,
    WorkflowHandle,
    WorkflowQueryFailedError,
    WorkflowQueryRejectedError,
)
from temporalio.service import RPCError, RPCStatusCode
from fastapi import FastAPI, File, UploadFile, Query, APIRouter, Depends, HTTPException, Response
from fastapi.openapi.docs import get_swagger_ui_html
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import HTMLResponse

from biosim_server.biosim_omex import OmexFile, get_cached_omex_file_from_upload
from biosim_server.biosim_runs import BiosimulatorVersion, HDF5File
from biosim_server.biosim_verify import CompareSettings
from biosim_server.biosim_verify.compatibility import find_common_datasets
from biosim_server.biosim_verify.database import (
    InvalidVerificationCursor,
    VerificationCursor,
    VerificationDatabaseService,
    VERIFICATION_CURSOR_MAX_LENGTH,
    decode_verification_cursor,
    encode_verification_cursor,
)
from biosim_server.compatibility import compatibility_router
from biosim_server.simulations import run_summary_router, simulations_router
from biosim_server.projects.router import router as projects_router
from biosim_server.common.auth import AuthenticatedUser, get_optional_user
from biosim_server.common.auth.auth0 import JwksCache, get_jwks_cache
from biosim_server.common.auth.discovery import warm_discovery_cache
from biosim_server.common.auth.roles import require_owner_or_admin, ADMIN_ROLE
from biosim_server.common.ratelimit import workflow_rate_limit
from biosim_server.common.upload_limit import MultipartBodyLimitMiddleware
from biosim_server.rbac_demo.router import router as rbac_demo_router
from biosim_server.users.router import router as users_router
from biosim_server.validation import validation_router
from biosim_server.biosim_verify.models import (
    MAX_VERIFY_RUN_IDS,
    MAX_VERIFY_SIMULATORS,
    VERIFICATION_IDS_DEFAULT_PAGE_SIZE,
    VERIFICATION_IDS_MAX_PAGE_SIZE,
    WORKFLOW_ID_PREFIX_MAX_LENGTH,
    VerificationIdsResponse,
    VerificationLedgerRecord,
    VerificationRecord,
    VerificationRun,
    VerificationType,
    VerifyWorkflowOutput,
    VerifyWorkflowStatus,
)
from biosim_server.biosim_verify.omex_verify_workflow import OmexVerifyWorkflow, OmexVerifyWorkflowInput
from biosim_server.biosim_verify.runs_verify_workflow import RunsVerifyWorkflowInput, RunsVerifyWorkflow
from biosim_server.config import get_local_cache_dir, get_settings
from biosim_server.dependencies import (
    get_file_service,
    get_temporal_client,
    init_standalone,
    shutdown_standalone,
    get_biosim_service,
    get_omex_database_service,
    get_mongo_client,
    get_database_service,
    get_verification_database_service,
)
from biosim_server.log_config import setup_logging
from biosim_server.version import __version__

logger = logging.getLogger(__name__)
setup_logging(logger)

# -- load dev env -- #
REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
DEV_ENV_PATH = os.path.join(REPO_ROOT, 'assets', 'dev', 'config', '.dev_env')
dotenv.load_dotenv(DEV_ENV_PATH)  # NOTE: create an env config at this filepath if dev

# -- constraints -- #
APP_VERSION = __version__
APP_TITLE = "biosim-server"
# Built-in CORS origins. Two groups, both stable and not deploy-specific:
#  - local-dev loopbacks (127.0.0.1 / localhost on common dev ports)
#  - cross-org trusted services that call this API (biosimulators.*,
#    run.biosimulations.*, etc.)
# Deployment-specific frontend origins (e.g. https://biosim.biosimulations.org,
# https://biosim-dev.biosimulations.org) belong in CORS_EXTRA_ORIGINS instead
# of this list — keeps overlay/deploy URLs explicit and reviewable per-env.
APP_ORIGINS = [
    'http://127.0.0.1:8000',
    'http://127.0.0.1:4200',
    'http://127.0.0.1:4201',
    'http://127.0.0.1:4202',
    'http://localhost:4200',
    'http://localhost:4201',
    'http://localhost:4202',
    'http://localhost:8000',
    'http://localhost:3000',
    'http://localhost:3001',
    'https://biosimulators.org',
    'https://www.biosimulators.org',
    'https://biosimulators.dev',
    'https://www.biosimulators.dev',
    'https://run.biosimulations.dev',
    'https://run.biosimulations.org',
    'https://biosimulations.dev',
    'https://biosimulations.org',
    'https://bio.libretexts.org',
    'https://biochecknet.biosimulations.org'
]

# Deployment-specific origins from env (comma-separated). Empty by default;
# overlay ConfigMaps supply this per cluster/host.
_extra = os.environ.get('CORS_EXTRA_ORIGINS', '').strip()
if _extra:
    APP_ORIGINS = APP_ORIGINS + [o.strip() for o in _extra.split(',') if o.strip()]
APP_SERVERS: list[dict[str, str]] = [
    # {
    #     "url": "https://biochecknet.biosimulations.org",
    #     "description": "Production server"
    # },
    # {
    #     "url": "http://localhost:3001",
    #     "description": "Main Development server"
    # },
    # {
    #     "url": "http://localhost:8000",
    #     "description": "Alternate Development server"
    # }
]

# -- app components -- #

router = APIRouter()


def _validate_auth0_configuration() -> None:
    """
    Startup gate: refuse to serve traffic with an unusable Auth0 configuration.

    Replaces _warn_if_auth0_misconfigured(), which both under-reacted (the pod
    started anyway, reported healthy, and failed every authenticated request)
    and misdescribed the failure -- it promised a 401, which has never been
    what happens.

    Raising here propagates out of `lifespan`, so uvicorn exits non-zero and
    Kubernetes shows CrashLoopBackOff with the reason in `kubectl logs`. That
    is the loudest signal available, and a cluster that cannot authenticate
    anyone should be using it.
    """
    auth0 = get_settings().auth0
    errors = auth0.configuration_errors()

    if not auth0.required:
        if errors:
            logger.warning(
                "AUTH_REQUIRED=false: starting with an incomplete Auth0 configuration "
                "(%s). Every endpoint behind get_current_user/get_optional_user will "
                "return 503 (Authentication temporarily unavailable); no request will "
                "be authenticated in this deployment.",
                "; ".join(errors),
            )
        else:
            logger.warning(
                "AUTH_REQUIRED=false: Auth0 startup validation skipped, though the "
                "configuration looks complete."
            )
        return
    if errors:
        raise RuntimeError(
            "Auth0 configuration is incomplete or invalid; refusing to start. "
            + "; ".join(errors)
            + ". Fix this cluster's kustomize/config/<cluster>/api.env, or set "
            "AUTH_REQUIRED=false to run this deployment without authentication."
        )
    logger.info(
        "Auth0 configuration validated: issuer=%s audience=%s trusted_issuers=%d",
        auth0.issuer_url() if not auth0.has_explicit_trusted_issuers() else "(explicit map)",
        auth0.audience if not auth0.has_explicit_trusted_issuers() else "(per-issuer)",
        len(auth0.trusted_issuer_map()),
    )
    # AUTH-MAJ-004: the hosted password-change route converts ordinary token
    # possession into an account-changing capability, so a cluster that enables
    # it should also say, loudly, whether the recent-authentication gate is on.
    # Non-fatal by design -- the gate needs tenant work (a Post-Login Action that
    # stamps `auth_time`) that may not have landed -- but it must not be enabled
    # silently, which is exactly what an unremarked default would do.
    if auth0.password_reset_client_id and not auth0.password_reset_require_recent_auth:
        logger.warning(
            "Password reset is configured (AUTH0_PASSWORD_RESET_CLIENT_ID set) without recent-"
            "authentication enforcement (AUTH0_PASSWORD_RESET_REQUIRE_RECENT_AUTH is false): any "
            "valid eligible access token can mint a hosted password-change ticket. Enable the "
            "gate before exposing the reset UI; see docs/auth0-p2-decisions.md (D-12)."
        )

@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    _validate_auth0_configuration()
    # #16: best-effort OIDC discovery warm. Deliberately after the (local,
    # side-effect-free) configuration gate and wrapped so it can never fail
    # startup -- a pod that cannot reach the discovery endpoint at boot still
    # starts and serves tokens via the convention-derived URLs.
    await warm_discovery_cache(get_settings().auth0)
    await init_standalone()
    yield
    await shutdown_standalone()


app = FastAPI(openapi_url="/openapi.json", docs_url=None, redoc_url=None, title=APP_TITLE, version=APP_VERSION, servers=APP_SERVERS, lifespan=lifespan)

# Bound multipart uploads while they are received. Registered *before* CORS:
# the last-added middleware is outermost, so CORS stays outside and its headers
# still reach the 413 this one returns.
app.add_middleware(MultipartBodyLimitMiddleware)

# add origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=APP_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"])

# include routers
app.include_router(compatibility_router)
app.include_router(validation_router)
app.include_router(simulations_router)
app.include_router(run_summary_router)
app.include_router(projects_router)
app.include_router(users_router)


def register_demo_router(app: FastAPI, *, enabled: bool) -> None:
    """Mount the /api/v1/demo/* RBAC teaching router only when explicitly enabled.

    #20: the demo router is a worked example, not production API surface. Left
    unmounted (the default), its paths return 404 and it is absent from
    /openapi.json. The Keycloak integration suite enables it via
    ENABLE_RBAC_DEMO in the test environment.
    """
    if enabled:
        app.include_router(rbac_demo_router)


register_demo_router(app, enabled=get_settings().enable_rbac_demo)


# -- endpoint logic -- #

@app.get("/")
def root() -> dict[str, str]:
    return {
        'docs': 'https://biosim.biosimulations.org/docs',
        'version': APP_VERSION
    }


@app.get("/version")
def get_version() -> str:
    return APP_VERSION


@app.get("/health", include_in_schema=False)
def health() -> dict[str, str]:
    # Liveness: deliberately dependency-free so a transient Mongo/Temporal
    # blip doesn't get the pod killed by a liveness probe.
    return {"status": "ok"}


@app.get("/ready", include_in_schema=False)
async def ready(
    response: Response,
    jwks_cache: JwksCache = Depends(get_jwks_cache),
) -> dict[str, object]:
    checks: dict[str, bool] = {}

    mongo_client = get_mongo_client()
    if mongo_client is None:
        checks["mongodb"] = False
    else:
        try:
            await mongo_client.admin.command("ping")
            checks["mongodb"] = True
        except Exception as e:
            logger.warning(f"/ready: mongodb ping failed: {e}")
            checks["mongodb"] = False

    checks["temporal"] = get_temporal_client() is not None

    ok = all(checks.values())
    response.status_code = 200 if ok else 503
    # #19c: auth (JWKS cache) health is reported as NON-GATING information --
    # it is deliberately kept out of `checks`, so `ok` stays computed from
    # MongoDB + Temporal only. A warm-cache Auth0 outage therefore never makes a
    # pod unready (it is still validating tokens), and this call makes no
    # outbound Auth0 request. Decision D-5: inform, do not gate.
    info: dict[str, object] = {"auth": jwks_cache.status()}
    return {"status": "ready" if ok else "not ready", "checks": checks, "info": info}


@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html() -> HTMLResponse:
    if app.openapi_url is None:
        raise HTTPException(status_code=404, detail="OpenAPI schema not available")
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=app.title + " - Swagger UI",
        swagger_js_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js",
        swagger_css_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css",
    )

@app.post(
    "/verify/omex",
    response_model=VerifyWorkflowOutput,
    operation_id="verify-omex",
    tags=["Verification"],
    dependencies=[Depends(get_temporal_client), Depends(get_file_service), Depends(get_local_cache_dir), Depends(get_omex_database_service), Depends(get_verification_database_service), Depends(workflow_rate_limit)],
    summary="Request verification report for OMEX/COMBINE archive across simulators",
    responses={400: {"description": "Unknown simulator, duplicate simulators, or simulators that resolve to the same version."}, 401: {"description": "Invalid bearer token (omit the header to call anonymously)."}, 503: {"description": "Verification database or Temporal unavailable."}},
)
async def verify_omex(
        uploaded_file: UploadFile = File(..., description="OMEX/COMBINE archive containing a deterministic SBML model"),
        user: AuthenticatedUser | None = Depends(get_optional_user),
        workflow_id_prefix: str = Query(default="omex-verification-", pattern=r"^[^/]*$",
                                         max_length=WORKFLOW_ID_PREFIX_MAX_LENGTH,
                                         description=f"Prefix for the workflow id; must not contain / "
                                                     f"(at most {WORKFLOW_ID_PREFIX_MAX_LENGTH} characters)."),
        simulators: list[str] = Query(default=["amici", "copasi", "pysces", "tellurium", "vcell"],
                                      max_length=MAX_VERIFY_SIMULATORS,
                                      description=f"List of simulators 'name' or 'name:version' to compare "
                                                  f"(at most {MAX_VERIFY_SIMULATORS}, no duplicates)."),
        include_outputs: bool = Query(default=False,
                                      description="Whether to include the output data on which the comparison is based."),
        user_description: str = Query(default="my-omex-compare", description="User description of the verification run."),
        rel_tol: float = Query(default=0.0001, description="Relative tolerance for proximity comparison."),
        abs_tol_min: float = Query(default=0.001, description="Min absolute tolerance, where atol = max(atol_min, max(arr1,arr2)*atol_scale."),
        abs_tol_scale: float = Query(default=0.00001, description="Scale for absolute tolerance, where atol = max(atol_min, max(arr1,arr2)*atol_scale."),
        cache_buster: str = Query(default="0", description="Optional unique id for cache busting (unique string to force new simulation runs)."),
        observables: Optional[list[str]] = Query(default=None,
                                                 description="List of observables to include in the return data.")
) -> VerifyWorkflowOutput:
    # Validation first: a duplicate selection is rejected before any service work.
    _reject_duplicate_selections(simulators, label="simulators")
    temporal_client = get_temporal_client()
    if temporal_client is None:
        raise HTTPException(status_code=503, detail="Temporal service not available")
    # Checked before the upload so an unavailable ledger costs no storage writes.
    ledger = _require_verification_ledger()
    # Anonymous callers (the legacy API) create ownerless, publicly readable verifications.
    owner_sub = user.sub if user is not None else None

    # ---- resolve simulators before the upload is stored ---- #
    # An unknown or colliding simulator is a 400 that must not cost a storage write.
    biosim_service = get_biosim_service()
    assert biosim_service is not None
    simulator_versions = _resolve_requested_simulators(
        simulators, await biosim_service.get_simulator_versions()
    )

    # ---- using hash to avoid saving multiple copies, upload to cloud storage if needed ---- #
    file_service = get_file_service()
    assert file_service is not None
    omex_database = get_omex_database_service()
    assert omex_database is not None
    omex_file: OmexFile = await get_cached_omex_file_from_upload(file_service=file_service, omex_database=omex_database,
                                                                 uploaded_file=uploaded_file, owner=owner_sub)

    # ---- create workflow input ---- #

    workflow_id = f"{workflow_id_prefix}{uuid.uuid4()}"
    compare_settings = CompareSettings(user_description=user_description, include_outputs=include_outputs,
                                       rel_tol=rel_tol, abs_tol_min=abs_tol_min, abs_tol_scale=abs_tol_scale,
                                       observables=observables)
    omex_verify_workflow_input = OmexVerifyWorkflowInput(omex_file=omex_file, requested_simulators=simulator_versions,
                                                         compare_settings=compare_settings, cache_buster=cache_buster,
                                                         owner_sub=owner_sub)

    # ---- persist ledger row, then invoke workflow ---- #
    # Log only non-sensitive correlation fields -- never the whole OmexFile
    # (its repr would carry the owner's raw Auth0 subject).
    logger.info(
        "starting verify workflow %s for OMEX hash %s (visibility=%s)",
        workflow_id,
        omex_file.file_hash_md5,
        omex_file.visibility,
    )
    workflow_handle = await _persist_and_start_verify(
        ledger=ledger,
        workflow_id=workflow_id,
        verify_type=VerificationType.OMEX,
        owner_sub=owner_sub,
        temporal_client=temporal_client,
        workflow_input=omex_verify_workflow_input,
        omex_hash=omex_file.file_hash_md5,
        start_workflow=lambda: temporal_client.start_workflow(
            OmexVerifyWorkflow.run,
            args=[omex_verify_workflow_input],
            task_queue="verification_tasks",
            id=workflow_id,
        ),
    )

    # ---- return initial workflow output ---- #
    omex_verify_workflow_output = VerifyWorkflowOutput(
        compare_settings=compare_settings,
        workflow_status=VerifyWorkflowStatus.PENDING,
        timestamp=str(datetime.now(UTC)),
        workflow_id=workflow_id,
        workflow_run_id=workflow_handle.run_id,
        owner_sub=owner_sub
    )
    return omex_verify_workflow_output


def _reject_duplicate_selections(values: list[str], *, label: str) -> None:
    """400 when a selection repeats: a duplicate has no meaning in a pairwise
    comparison and would only multiply workflow work for one quota unit."""
    duplicates = sorted({value for value in values if values.count(value) > 1})
    if duplicates:
        raise HTTPException(
            status_code=400, detail=f"Duplicate {label} are not allowed: {', '.join(duplicates)}"
        )


def _resolve_requested_simulators(
    simulators: list[str], all_simulator_versions: list[BiosimulatorVersion]
) -> list[BiosimulatorVersion]:
    """Map each requested 'name' or 'name:version' to one registry version, in order.

    400 for an unknown simulator, and for two requests that resolve to the same
    version (e.g. 'copasi' and 'copasi:<latest>'), which would run it twice.
    """
    resolved: list[BiosimulatorVersion] = []
    requested_by_version: dict[tuple[str, str], str] = {}
    for simulator in simulators:
        simulator_version: Optional[BiosimulatorVersion] = None
        if ":" in simulator:
            name, version = simulator.split(":", 1)
            for sv in all_simulator_versions:
                if sv.id == name and sv.version == version:
                    simulator_version = sv
                    break
        else:
            for sv in all_simulator_versions:
                if sv.id == simulator:
                    simulator_version = sv  # don't break, we want the last one in the list
        if simulator_version is None:
            raise HTTPException(status_code=400, detail=f"Simulator {simulator} not found.")
        key = (simulator_version.id, simulator_version.version)
        if key in requested_by_version:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Simulators {requested_by_version[key]} and {simulator} resolve to the same "
                    f"simulator version {simulator_version.id}:{simulator_version.version}."
                ),
            )
        requested_by_version[key] = simulator
        resolved.append(simulator_version)
    return resolved


class _VerifyOwnership:
    """Adapter so ``require_owner_or_admin`` can check a verify workflow.

    Email is never persisted on the verify payload; ownership is ``owner_sub``
    only.
    """

    def __init__(self, owner_sub: str | None) -> None:
        self.owner_sub = owner_sub
        self.email: str | None = None


def _authorize_verification_read(user: AuthenticatedUser | None, owner_sub: str | None) -> None:
    """Ownerless verifications (anonymous/legacy callers) are public.

    One started with a token stays owner-or-admin: anonymous callers get 401
    and other users 403, matching ``authorize_resource_access``.
    """
    if owner_sub is None:
        return
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required to read this verification",
            headers={"WWW-Authenticate": 'Bearer realm="api", error="invalid_request"'},
        )
    require_owner_or_admin(user, [_VerifyOwnership(owner_sub)], action="read")


def _require_verification_ledger() -> VerificationDatabaseService:
    """Return the verification ledger, or 503 if it is not initialized."""
    ledger = get_verification_database_service()
    if ledger is None:
        raise HTTPException(status_code=503, detail="Verification database service not available")
    return ledger


async def _persist_and_start_verify(
    *,
    ledger: VerificationDatabaseService,
    workflow_id: str,
    verify_type: VerificationType,
    owner_sub: str | None,
    temporal_client: Client,
    workflow_input: OmexVerifyWorkflowInput | RunsVerifyWorkflowInput,
    start_workflow: Callable[[], Awaitable[WorkflowHandle[Any, VerifyWorkflowOutput]]],
    omex_hash: str | None = None,
) -> WorkflowHandle[Any, VerifyWorkflowOutput]:
    """Write the ledger row, then start the workflow (mirrors simulations/router.py).

    Insert-before-start makes every started verification listable by
    GET /verification_ids. Only a definitive invalid-request rejection removes
    the row. Transport failures can happen after acceptance; reconcile the same
    ID against history and retain the row if the outcome remains uncertain.
    """
    try:
        await ledger.insert_verification(
            VerificationLedgerRecord(
                workflow_id=workflow_id,
                verify_type=verify_type,
                owner_sub=owner_sub,
                created=datetime.now(UTC),
                omex_hash=omex_hash,
                status="PENDING",
            )
        )
    except Exception as e:
        logger.error("Failed to persist verification record for %s: %s", workflow_id, e, exc_info=e)
        raise HTTPException(status_code=503, detail="Failed to persist verification record")

    try:
        workflow_handle = await start_workflow()
    except Exception as e:
        logger.error("Failed to start %s verification workflow %s: %s", verify_type, workflow_id, e, exc_info=e)
        if isinstance(e, RPCError) and e.status == RPCStatusCode.INVALID_ARGUMENT:
            try:
                await ledger.delete_verification(workflow_id)
            except Exception as cleanup_e:
                logger.warning("Cleanup of rejected verification %s failed", workflow_id, exc_info=cleanup_e)
            raise HTTPException(status_code=503, detail="Failed to start verification workflow")

        # NOT_FOUND on this follow-up is not proof of rejection: a delayed start
        # may still commit. Never delete a record after an ambiguous failure.
        try:
            desc = await temporal_client.get_workflow_handle(workflow_id).describe(
                rpc_timeout=timedelta(seconds=10)
            )
            recovered = temporal_client.get_workflow_handle(
                workflow_id, run_id=desc.run_id, result_type=VerifyWorkflowOutput
            )
            original = await _read_verification_input(temporal_client, recovered, desc.workflow_type)
            if original != workflow_input or original.owner_sub != owner_sub:
                raise ValueError("Existing workflow input does not match submission")
            logger.info("Recovered accepted verification start for %s", workflow_id)
            return recovered
        except Exception as recovery_error:
            logger.warning("Unable to confirm verification start for %s", workflow_id, exc_info=recovery_error)
            raise HTTPException(
                status_code=503,
                detail=f"Verification start outcome is unknown; check /verify/{workflow_id} before resubmitting",
            )

    logger.info("started workflow with id %s", workflow_id)
    assert workflow_handle.id == workflow_id
    return workflow_handle


# Workflow type names for the two verification workflow kinds.
# Any other type (e.g. OmexSimWorkflow, SimulationRunWorkflow) is rejected.
_VERIFY_WORKFLOW_TYPES = frozenset({"OmexVerifyWorkflow", "RunsVerifyWorkflow"})
_FAILED_EXECUTION_STATUSES = frozenset({
    WorkflowExecutionStatus.FAILED,
    WorkflowExecutionStatus.TERMINATED,
    WorkflowExecutionStatus.TIMED_OUT,
    WorkflowExecutionStatus.CANCELED,
})


async def _read_verification_input(
    client: Client,
    handle: WorkflowHandle[Any, VerifyWorkflowOutput],
    workflow_type: str,
) -> OmexVerifyWorkflowInput | RunsVerifyWorkflowInput:
    """Read only the first history page, using the configured payload converter."""
    if workflow_type not in _VERIFY_WORKFLOW_TYPES:
        raise ValueError("Not a verification workflow")
    events = handle.fetch_history_events(page_size=1, rpc_timeout=timedelta(seconds=10))
    event = await anext(events)
    if not event.HasField("workflow_execution_started_event_attributes"):
        raise ValueError("Missing workflow start event")
    attrs = event.workflow_execution_started_event_attributes
    if attrs.workflow_type.name != workflow_type or len(attrs.input.payloads) != 1:
        raise ValueError("Unexpected workflow start input")
    input_type = OmexVerifyWorkflowInput if workflow_type == "OmexVerifyWorkflow" else RunsVerifyWorkflowInput
    decoded = await client.data_converter.decode(attrs.input.payloads, [input_type])
    if len(decoded) != 1 or not isinstance(decoded[0], input_type):
        raise ValueError("Invalid workflow start input")
    return decoded[0]


async def _recover_terminal_verification(
    client: Client, desc: WorkflowExecutionDescription, user: AuthenticatedUser | None,
) -> VerifyWorkflowOutput | None:
    """Reconstruct an unqueryable terminal output without bypassing ownership."""
    if desc.status not in _FAILED_EXECUTION_STATUSES:
        return None
    try:
        handle = client.get_workflow_handle(desc.id, run_id=desc.run_id, result_type=VerifyWorkflowOutput)
        original = await _read_verification_input(client, handle, desc.workflow_type)
    except RPCError as error:
        if error.status == RPCStatusCode.NOT_FOUND:
            await _ledger_fallback_404(desc.id, user)
        logger.warning("Unable to recover terminal verification %s", desc.id, exc_info=error)
        raise HTTPException(status_code=503, detail="Temporal service unavailable")
    except Exception as error:
        logger.warning("Unable to recover terminal verification %s", desc.id, exc_info=error)
        raise HTTPException(status_code=503, detail="Unable to recover verification output")
    _authorize_verification_read(user, original.owner_sub)
    output = VerifyWorkflowOutput(
        workflow_id=desc.id, workflow_run_id=desc.run_id,
        compare_settings=original.compare_settings, owner_sub=original.owner_sub,
        timestamp=(desc.close_time or desc.start_time).isoformat(),
        workflow_status=VerifyWorkflowStatus.IN_PROGRESS,
    )
    return _reconcile_terminal_status(output, desc.status)


def _reconcile_terminal_status(
    output: VerifyWorkflowOutput,
    execution_status: WorkflowExecutionStatus | None,
) -> VerifyWorkflowOutput:
    """Override a stuck IN_PROGRESS status when Temporal says the workflow is done.

    Fixes D1: a workflow that failed/was terminated never updated verify_output,
    so callers would poll forever.  Only overrides when the output still reports
    a non-terminal status; completed/failed/run-not-found outputs are left alone.
    """
    if output.workflow_status.is_done:
        return output
    if execution_status in _FAILED_EXECUTION_STATUSES:
        status_name = getattr(execution_status, "name", str(execution_status))
        return output.model_copy(
            update={
                "workflow_status": VerifyWorkflowStatus.FAILED,
                "workflow_error": f"Verification workflow ended with status {status_name}",
            }
        )
    return output


@app.get(
    "/verify/{workflow_id}",
    response_model=VerifyWorkflowOutput,
    operation_id='get-verify-output',
    name="Retrieve verification report",
    tags=["Verification"],
    dependencies=[Depends(get_temporal_client)],
    summary='Retrieve verification report for OMEX/COMBINE archive',
    responses={
        401: {"description": "Invalid bearer token, or no token for a verification started with one."},
        403: {"description": "Caller is not the workflow owner or an admin."},
        404: {"description": "Verification not found, or not a verification workflow ID."},
        503: {"description": "Temporal is unreachable or the client is not initialized."},
    },
)
async def get_verify_output(
        workflow_id: str,
        user: AuthenticatedUser | None = Depends(get_optional_user),
) -> VerifyWorkflowOutput:
    logger.info("in get /verify/%s", workflow_id)

    # D2 fix: check for None *before* entering the try so AssertionError → 503, not 404.
    temporal_client = get_temporal_client()
    if temporal_client is None:
        raise HTTPException(status_code=503, detail="Temporal service not available")

    handle = temporal_client.get_workflow_handle(
        workflow_id=workflow_id, result_type=VerifyWorkflowOutput
    )

    # Step 1: describe — distinguish "not found" from "service error", and reject
    # non-verification workflow types early.
    try:
        desc = await handle.describe(rpc_timeout=timedelta(seconds=60))
    except RPCError as rpc_err:
        if rpc_err.status == RPCStatusCode.NOT_FOUND:
            await _ledger_fallback_404(workflow_id, user)
        logger.error("Temporal describe failed for %s: %s", workflow_id, rpc_err, exc_info=rpc_err)
        raise HTTPException(status_code=503, detail="Temporal service unavailable")
    except Exception as e:
        logger.error("Unexpected error describing workflow %s: %s", workflow_id, e, exc_info=e)
        raise HTTPException(status_code=503, detail="Temporal service unavailable")

    if desc.workflow_type not in _VERIFY_WORKFLOW_TYPES:
        raise HTTPException(status_code=404, detail=f"Verification not found: {workflow_id}")

    # Step 2: query the workflow for its output.
    try:
        workflow_output: VerifyWorkflowOutput = await handle.query(
            "get_output",
            result_type=VerifyWorkflowOutput,
            rpc_timeout=timedelta(seconds=60),
        )
    except HTTPException:
        raise
    except RPCError as rpc_err:
        if rpc_err.status == RPCStatusCode.NOT_FOUND:
            # History purged between describe and query: same answer as a describe miss.
            await _ledger_fallback_404(workflow_id, user)
        recovered = await _recover_terminal_verification(temporal_client, desc, user)
        if recovered is not None:
            return recovered
        logger.error("Temporal query failed for %s: %s", workflow_id, rpc_err, exc_info=rpc_err)
        raise HTTPException(status_code=503, detail="Temporal service unavailable")
    except (WorkflowQueryFailedError, WorkflowQueryRejectedError) as e:
        recovered = await _recover_terminal_verification(temporal_client, desc, user)
        if recovered is not None:
            return recovered
        logger.warning("Workflow query failed for %s: %s", workflow_id, e, exc_info=e)
        raise HTTPException(status_code=404, detail=f"Verification not found: {workflow_id}")
    except Exception as e:
        recovered = await _recover_terminal_verification(temporal_client, desc, user)
        if recovered is not None:
            return recovered
        logger.error("Unexpected error querying workflow %s", workflow_id, exc_info=e)
        raise HTTPException(status_code=503, detail="Temporal service unavailable")

    # Step 3: ownership check -- ownerless is public; owned is owner-or-admin.
    _authorize_verification_read(user, workflow_output.owner_sub)

    # Step 4: D1 fix — reconcile a stuck IN_PROGRESS against Temporal's execution status.
    workflow_output = _reconcile_terminal_status(workflow_output, desc.status)

    ledger = get_verification_database_service()
    if ledger is not None:
        try:
            status_str = (
                workflow_output.workflow_status.value
                if hasattr(workflow_output.workflow_status, "value")
                else str(workflow_output.workflow_status)
            )
            await ledger.update_verification_status(workflow_id, status_str)
        except Exception as e:
            logger.warning("Could not update verification status in ledger for %s: %s", workflow_id, e)

    return workflow_output


async def _ledger_fallback_404(workflow_id: str, user: AuthenticatedUser | None) -> NoReturn:
    """Temporal returned NOT_FOUND.  Check the ledger for an expired-but-known ID.

    Raises HTTPException(404) with a detail that distinguishes:
    - "no longer retained" — ledger row exists and caller may see it
    - generic "not found" — no row, or belongs to someone else (no existence leak)
    """
    ledger = get_verification_database_service()
    if ledger is not None:
        try:
            row = await ledger.get_verification(workflow_id)
            if row is not None:
                caller_may_know = row.owner_sub is None or (
                    user is not None
                    and (ADMIN_ROLE in user.roles or row.owner_sub == user.sub)
                )
                if caller_may_know:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Verification {workflow_id} exists but its results are no longer retained.",
                    )
        except HTTPException:
            raise
        except Exception as ledger_err:
            logger.warning(
                "Ledger lookup failed during NOT_FOUND fallback for %s: %s",
                workflow_id,
                ledger_err,
            )
    raise HTTPException(status_code=404, detail=f"Verification not found: {workflow_id}")


@app.post(
    "/verify/runs",
    response_model=VerifyWorkflowOutput,
    operation_id="verify-runs",
    tags=["Verification"],
    dependencies=[Depends(get_temporal_client), Depends(get_verification_database_service), Depends(get_biosim_service), Depends(workflow_rate_limit)],
    summary="Request verification report for biosimulation runs by run IDs",
    responses={400: {"description": "Duplicate run IDs, or runs lack common datasets or requested observables."}, 401: {"description": "Invalid bearer token (omit the header to call anonymously)."}, 503: {"description": "Verification database or Temporal unavailable."}},
)
async def verify_runs(
        user: AuthenticatedUser | None = Depends(get_optional_user),
        workflow_id_prefix: str = Query(default="runs-verification-", pattern=r"^[^/]*$",
                                         max_length=WORKFLOW_ID_PREFIX_MAX_LENGTH,
                                         description=f"Prefix for the workflow id; must not contain / "
                                                     f"(at most {WORKFLOW_ID_PREFIX_MAX_LENGTH} characters)."),
        biosimulations_run_ids: list[str] = Query(default=["67817a2e1f52f47f628af971","67817a2eba5a3f02b9f2938d"],
                                                  max_length=MAX_VERIFY_RUN_IDS,
                                                  description=f"List of biosimulations run IDs to compare "
                                                              f"(at most {MAX_VERIFY_RUN_IDS}, no duplicates)."),
        include_outputs: bool = Query(default=False,
                                      description="Whether to include the output data on which the comparison is based."),
        user_description: str = Query(default="my-verify-job", description="User description of the verification run."),
        rel_tol: float = Query(default=0.0001, description="Relative tolerance for proximity comparison."),
        abs_tol_min: float = Query(default=0.001, description="Min absolute tolerance, where atol = max(atol_min, max(arr1,arr2)*atol_scale."),
        abs_tol_scale: float = Query(default=0.00001, description="Scale for absolute tolerance, where atol = max(atol_min, max(arr1,arr2)*atol_scale."),
        observables: Optional[list[str]] = Query(default=None,
                                                 description="SED-ML data set labels of interest. Validated only: "
                                                             "the request is rejected with 400 if none appear in the "
                                                             "runs' common dataset labels. Not yet used to filter the "
                                                             "returned comparison."),
        omex_hash: Optional[str] = Query(default=None,
                                         description="Optional OMEX archive MD5 hash associated with these simulation runs.")
) -> VerifyWorkflowOutput:
    # Validation first: a duplicate run id is rejected before any service work.
    _reject_duplicate_selections(biosimulations_run_ids, label="run IDs")
    temporal_client = get_temporal_client()
    if temporal_client is None:
        raise HTTPException(status_code=503, detail="Temporal service not available")
    # Checked before the preflight so an unavailable ledger costs no upstream metadata calls.
    ledger = _require_verification_ledger()
    # Anonymous callers (the legacy API) create ownerless, publicly readable verifications.
    owner_sub = user.sub if user is not None else None

    # ---- model-compatibility preflight (Step 9) ---- #
    if len(biosimulations_run_ids) >= 2:
        hdf5_files = await _load_hdf5_metadata_for_preflight(biosimulations_run_ids)
        if len(hdf5_files) >= 2:
            common = find_common_datasets(hdf5_files)
            if not common:
                per_run = "; ".join(
                    f"{rid}: [{', '.join(hdf5_files[rid].datasets.keys())}]"
                    for rid in sorted(hdf5_files)
                )
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Runs have no datasets in common; comparison requires the same "
                        f"model/outputs. {per_run}"
                    ),
                )

            if observables and not set(observables).intersection(
                label for labels in common.values() for label in labels
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Requested observables are absent from the common dataset labels.",
                )

    # ---- create workflow input ---- #
    workflow_id = f"{workflow_id_prefix}{uuid.uuid4()}"
    compare_settings = CompareSettings(user_description=user_description, include_outputs=include_outputs,
                                       rel_tol=rel_tol, abs_tol_min=abs_tol_min, abs_tol_scale=abs_tol_scale,
                                       observables=observables)
    runs_verify_workflow_input = RunsVerifyWorkflowInput(biosimulations_run_ids=biosimulations_run_ids,
                                                         compare_settings=compare_settings, owner_sub=owner_sub)

    # ---- persist ledger row, then invoke workflow ---- #
    logger.info("starting verify workflow %s for biosim run IDs %s", workflow_id, biosimulations_run_ids)
    workflow_handle = await _persist_and_start_verify(
        ledger=ledger,
        workflow_id=workflow_id,
        verify_type=VerificationType.RUNS,
        owner_sub=owner_sub,
        temporal_client=temporal_client,
        workflow_input=runs_verify_workflow_input,
        omex_hash=omex_hash,
        start_workflow=lambda: temporal_client.start_workflow(
            RunsVerifyWorkflow.run,
            args=[runs_verify_workflow_input],
            task_queue="verification_tasks",
            id=workflow_id,
        ),
    )

    # ---- return initial workflow output ---- #
    runs_verify_workflow_output = VerifyWorkflowOutput(
        compare_settings=compare_settings,
        workflow_status=VerifyWorkflowStatus.PENDING,
        timestamp=str(datetime.now(UTC)),
        workflow_id=workflow_id,
        workflow_run_id=workflow_handle.run_id,
        owner_sub=owner_sub
    )
    return runs_verify_workflow_output


async def _load_hdf5_metadata_for_preflight(
    run_ids: list[str],
) -> dict[str, HDF5File]:
    """Fetch HDF5 metadata for preflight overlap check.

    Tries Mongo cache first; falls back to live BiosimService.  Runs whose
    metadata can't be fetched are logged and skipped — the workflow already
    handles those via RUN_ID_NOT_FOUND / FAILED.
    Returns a dict of run_id -> HDF5File for runs where metadata is available.
    """
    results: dict[str, HDF5File] = {}
    db_service = get_database_service()
    biosim_svc = get_biosim_service()

    for run_id in dict.fromkeys(run_ids):
        hdf5: HDF5File | None = None
        # Try Mongo cache first
        if db_service is not None:
            try:
                cached_list = await db_service.get_biosimulator_workflow_runs_by_biosim_runid(run_id)
                for cached in cached_list:
                    if cached.hdf5_file is not None:
                        hdf5 = cached.hdf5_file
                        break
            except Exception:
                logger.warning("Cached verification metadata unavailable for %s", run_id, exc_info=True)
        # Fall back to live fetch
        if hdf5 is None and biosim_svc is not None:
            try:
                hdf5 = await biosim_svc.get_hdf5_metadata(run_id)
            except Exception:
                logger.warning("Verification metadata unavailable for %s", run_id, exc_info=True)
        if hdf5 is not None:
            results[run_id] = hdf5
    return results


@app.get(
    "/verification_ids",
    response_model=VerificationIdsResponse,
    operation_id="list-verification-ids",
    tags=["Verification"],
    dependencies=[Depends(get_verification_database_service)],
    summary="List verification workflow IDs usable with GET /verify/{workflow_id}",
    description=(
        "Caller-scoped, newest-first listing: anonymous callers see ownerless verifications; "
        "authenticated callers see ownerless plus their own, with no administrator bypass. "
        "Invalid credentials are rejected. Responses use Cache-Control: private, no-store. "
        "Keep the same identity while paginating; restart after login/logout. One bounded page "
        "at a time. Follow `next_cursor` (pass it back as `cursor`) for older IDs; it is "
        "null on the last page."
    ),
    responses={
        200: {"description": "One page visible to the current caller.", "headers": {
            "Cache-Control": {"description": "private, no-store", "schema": {"type": "string"}},
        }},
        400: {"description": "Malformed cursor."},
        401: {"description": "Invalid credentials; no anonymous fallback.", "headers": {
            "WWW-Authenticate": {"schema": {"type": "string"}},
        }},
        503: {"description": "Verification database or authentication service unavailable."},
    },
)
async def list_verification_ids(
        response: Response,
        user: AuthenticatedUser | None = Depends(get_optional_user),
        limit: int = Query(default=VERIFICATION_IDS_DEFAULT_PAGE_SIZE, ge=1, le=VERIFICATION_IDS_MAX_PAGE_SIZE,
                           description=f"Page size (1-{VERIFICATION_IDS_MAX_PAGE_SIZE})."),
        cursor: Optional[str] = Query(default=None, max_length=VERIFICATION_CURSOR_MAX_LENGTH,
                                      description="Opaque `next_cursor` from a previous page."),
        omex_hash: Optional[str] = Query(default=None,
                                         description="Optional OMEX archive MD5 hash to filter verification records."),
) -> VerificationIdsResponse:
    # IDs can contain private caller text. Always scope the database read,
    # including anonymous requests and continuation pages.
    after: VerificationCursor | None = None
    if cursor is not None:
        try:
            after = decode_verification_cursor(cursor)
        except InvalidVerificationCursor:
            raise HTTPException(status_code=400, detail="Invalid cursor") from None
    ledger = get_verification_database_service()
    if ledger is None:
        raise HTTPException(status_code=503, detail="Verification database service not available")

    try:
        page = await ledger.list_verification_ids(
            user.sub if user is not None else None, limit=limit, after=after, omex_hash=omex_hash
        )
    except Exception:
        # Driver errors can include the scoped query (subjects/cursor IDs).
        logger.error("Failed to list verification IDs")
        raise HTTPException(status_code=503, detail="Failed to list verification IDs") from None

    logger.info(
        "listing verification ids (count=%d, has_more=%s)",
        len(page.verification_ids), page.next_cursor is not None,
    )
    response.headers["Cache-Control"] = "private, no-store"
    return VerificationIdsResponse(
        verification_ids=page.verification_ids,
        records=page.records,
        next_cursor=encode_verification_cursor(page.next_cursor) if page.next_cursor else None,
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
    logger.info("Server started")
