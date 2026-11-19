"""The owned simulator registry endpoints (plan §4.1, §4.2, §4.5, §4.6).

Explicit routes only — no catch-all: anything else gets FastAPI's native 404,
and the ontology namespace is an enum path parameter, so an unknown namespace is
422. `/simulators/latest` must be registered before `/simulators/{simulator_id}`
or the literal path would be captured as an id.

The routes resolve no user dependency: they are public reads, the auth0 plugin
attaches the Platform token to them anyway, and resolving one would 401 a lapsed
session (§4.6). Response headers are ours alone — nothing is relayed from
upstream — with `Cache-Control: public, max-age=300` on the cached list and
ontology responses and nothing on per-simulator detail and version lists.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from biosim_server.common.ratelimit import simulators_rate_limit
from biosim_server.dependencies import get_biosimulators_http_client
from biosim_server.simulators.models import (
    OntologyNamespace,
    OntologyTermList,
    SimulatorList,
    SimulatorSpec,
    SimulatorVersionList,
)
from biosim_server.simulators.service import BiosimulatorsService

router = APIRouter(dependencies=[Depends(simulators_rate_limit)])

_simulators_service: BiosimulatorsService | None = None


def set_simulators_service(service: BiosimulatorsService | None) -> None:
    """Swap the process-wide service; tests inject a MockTransport-backed client."""
    global _simulators_service
    _simulators_service = service


def get_simulators_service() -> BiosimulatorsService:
    """The pooled service, built lazily on the biosimulators client (dependencies.py pattern)."""
    global _simulators_service
    if _simulators_service is None:
        _simulators_service = BiosimulatorsService(get_biosimulators_http_client())
    return _simulators_service


_UPSTREAM_FAILURE_RESPONSES: dict[int | str, dict[str, str]] = {
    404: {"description": "Unknown simulator or version."},
    429: {"description": "The per-client-IP simulators budget is exhausted; Retry-After names the wait."},
    502: {"description": "The upstream registry failed or returned an invalid or oversized body."},
    504: {"description": "The upstream registry timed out."},
}


@router.get(
    "/simulators/latest",
    response_model=SimulatorList,
    operation_id="get-latest-simulators",
    tags=["Simulators"],
    summary="The latest version of every simulator in the BioSimulators registry",
    description=(
        "The owned, validated registry list that backs the simulators table and the suggest page. "
        "Cached in-process for 10 minutes (stale-served up to 24 hours longer during an upstream "
        "outage) and identical for every caller, so the response is `Cache-Control: public, "
        "max-age=300`. No authentication: an invalid bearer token is ignored, never a 401."
    ),
    responses={
        200: {
            "description": "The registry list.",
            "headers": {"Cache-Control": {"description": "public, max-age=300", "schema": {"type": "string"}}},
        },
        **_UPSTREAM_FAILURE_RESPONSES,
    },
)
async def get_latest_simulators(
    response: Response, service: Annotated[BiosimulatorsService, Depends(get_simulators_service)]
) -> SimulatorList:
    response.headers["Cache-Control"] = "public, max-age=300"
    return await service.get_simulators_latest()


@router.get(
    "/simulators/{simulator_id}",
    response_model=SimulatorVersionList,
    operation_id="list-simulator-versions",
    tags=["Simulators"],
    summary="Every version of one simulator",
    description=(
        "An array of the simulator's full version specs wrapped in the owned envelope; the detail "
        "page's version selector reads it. Never cached: the key space grows with the catalogue."
    ),
    responses=_UPSTREAM_FAILURE_RESPONSES,
)
async def list_simulator_versions(
    simulator_id: str, service: Annotated[BiosimulatorsService, Depends(get_simulators_service)]
) -> SimulatorVersionList:
    return await service.get_simulator_versions(simulator_id)


@router.get(
    "/simulators/{simulator_id}/{version}",
    response_model=SimulatorSpec,
    operation_id="get-simulator-version",
    tags=["Simulators"],
    summary="One simulator version, optionally with its test-suite results",
    description=(
        "A bare owned spec for one version; `version` accepts upstream's `latest` alias. "
        "`include_tests` is sent upstream as `includeTests` and adds the validation-test results. "
        "Never cached."
    ),
    responses=_UPSTREAM_FAILURE_RESPONSES,
)
async def get_simulator_version(
    simulator_id: str,
    version: str,
    service: Annotated[BiosimulatorsService, Depends(get_simulators_service)],
    include_tests: Annotated[
        bool, Query(description="Include the BioSimulators test-suite results (upstream `includeTests`).")
    ] = False,
) -> SimulatorSpec:
    return await service.get_simulator_version(simulator_id, version, include_tests)


@router.get(
    "/ontologies/{namespace}",
    response_model=OntologyTermList,
    operation_id="get-ontology-terms",
    tags=["Simulators"],
    summary="The trimmed term list for one ontology (EDAM, SBO, KISAO or SPDX)",
    description=(
        "Only `id` and `name` per term: the UI reads nothing else, and EDAM shrinks from 342 KB to "
        "33 KB. Cached in-process for 10 minutes like the registry list, so the response is "
        "`Cache-Control: public, max-age=300`. An unknown namespace is a 422."
    ),
    responses={
        200: {
            "description": "The trimmed term list.",
            "headers": {"Cache-Control": {"description": "public, max-age=300", "schema": {"type": "string"}}},
        },
        422: {"description": "Unknown ontology namespace."},
        429: {"description": "The per-client-IP simulators budget is exhausted; Retry-After names the wait."},
        502: {"description": "The upstream registry failed or returned an invalid or oversized body."},
        504: {"description": "The upstream registry timed out."},
    },
)
async def get_ontology_terms(
    namespace: OntologyNamespace,
    response: Response,
    service: Annotated[BiosimulatorsService, Depends(get_simulators_service)],
) -> OntologyTermList:
    response.headers["Cache-Control"] = "public, max-age=300"
    return await service.get_ontology(namespace)
