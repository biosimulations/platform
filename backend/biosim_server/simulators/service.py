"""BiosimulatorsService: fetch upstream, validate against the owned contract, trim, cache (plan §4.3).

Every fetch goes through ``common/upstream`` on the pooled biosimulators client,
which buys header isolation, the size cap, no redirects, the 404/502/504 mapping
and the ``upstream_resource``/``upstream_outcome`` logging convention. Validation
follows the plan §4.2 policy: list entries and ontology terms are skipped
individually with a WARNING; a detail, a non-list body, or a non-empty list with
zero valid entries is a 502; upstream status mapping is unchanged.
"""

import logging
from typing import Any, Final

import httpx
from fastapi import HTTPException, status
from pydantic import ValidationError

from biosim_server.common.upstream import fetch_upstream_json_value, upstream_url
from biosim_server.simulators.cache import SimulatorsCache
from biosim_server.simulators.models import (
    OntologyNamespace,
    OntologyTerm,
    OntologyTermList,
    SimulatorList,
    SimulatorSpec,
    SimulatorVersionList,
)

logger = logging.getLogger(__name__)

CACHE_KEY_SIMULATORS_LATEST: Final[str] = "simulators/latest"


def _ontology_cache_key(namespace: OntologyNamespace) -> str:
    return f"ontologies/{namespace.value}"


CACHEABLE_KEYS: Final[frozenset[str]] = frozenset(
    {
        CACHE_KEY_SIMULATORS_LATEST,
        *(_ontology_cache_key(namespace) for namespace in OntologyNamespace),
    }
)

_ENTRY_SKIPPED: Final[str] = "entry_skipped"
_INVALID_BODY: Final[str] = "invalid_body"


def _entry_identity(entry: object) -> str:
    """Best-effort naming for a skip WARNING: the plan asks for the simulator id and version."""
    if isinstance(entry, dict):
        return f"{entry.get('id', '<unnamed>')!s} {entry.get('version', '<no version>')!s}"
    return "<non-object entry>"


def _log_skipped(resource: str, identity: str) -> None:
    logger.warning(
        "Skipped an invalid %s entry (%s); serving the rest of the list",
        resource,
        identity,
        extra={"upstream_resource": resource, "upstream_outcome": _ENTRY_SKIPPED},
    )


def _log_invalid_body(resource: str) -> None:
    logger.warning(
        "The upstream %s did not match the owned contract",
        resource,
        extra={"upstream_resource": resource, "upstream_outcome": _INVALID_BODY},
    )


def _invalid_body(resource: str) -> HTTPException:
    _log_invalid_body(resource)
    return HTTPException(status.HTTP_502_BAD_GATEWAY, f"The upstream service returned an unexpected {resource}.")


def _validate_simulator_entries(payload: dict[str, Any] | list[Any], *, resource: str) -> list[SimulatorSpec]:
    if not isinstance(payload, list):
        raise _invalid_body(resource)
    validated: list[SimulatorSpec] = []
    for entry in payload:
        try:
            validated.append(SimulatorSpec.model_validate(entry))
        except ValidationError:
            _log_skipped(resource, _entry_identity(entry))
    # Upstream drift that invalidates every entry must not become a blank page: a
    # non-empty body with zero valid entries is an invalid body, so the cache can
    # serve the last good list instead (plan §4.2 validation policy).
    if payload and not validated:
        raise _invalid_body(resource)
    return validated


def _validate_ontology_terms(payload: dict[str, Any] | list[Any], *, resource: str) -> list[OntologyTerm]:
    if not isinstance(payload, list):
        raise _invalid_body(resource)
    validated: list[OntologyTerm] = []
    for entry in payload:
        try:
            validated.append(OntologyTerm.model_validate(entry))
        except ValidationError:
            _log_skipped(resource, _entry_identity(entry))
    if payload and not validated:
        raise _invalid_body(resource)
    return validated


def _validate_detail(payload: dict[str, Any] | list[Any], *, resource: str) -> SimulatorSpec:
    try:
        return SimulatorSpec.model_validate(payload)
    except ValidationError as exc:
        raise _invalid_body(resource) from exc


class BiosimulatorsService:
    """The read side of the owned simulator contract.

    The client is injected so tests can swap in an ``httpx.MockTransport``; the
    router (Phase 2) wires the pooled ``get_biosimulators_http_client``. The list
    and the four ontologies are cached; per-simulator detail and version lists
    are never cached (plan §4.3).
    """

    def __init__(self, client: httpx.AsyncClient, cache: SimulatorsCache | None = None) -> None:
        self._client = client
        self._cache = cache if cache is not None else SimulatorsCache(CACHEABLE_KEYS)

    async def get_simulators_latest(self) -> SimulatorList:
        """The cached registry list that backs ``/simulators``, ``/simulators/suggest`` and the table."""

        async def load() -> SimulatorList:
            payload = await fetch_upstream_json_value(
                self._client, upstream_url("simulators", "latest"), resource="simulator list"
            )
            return SimulatorList(simulators=_validate_simulator_entries(payload, resource="simulator list"))

        return await self._cache.get(CACHE_KEY_SIMULATORS_LATEST, load)

    async def get_simulator_versions(self, simulator_id: str) -> SimulatorVersionList:
        """Every version of one simulator, uncached: the detail page's version selector."""

        payload = await fetch_upstream_json_value(
            self._client, upstream_url("simulators", simulator_id), resource="simulator versions"
        )
        return SimulatorVersionList(
            simulator_id=simulator_id,
            versions=_validate_simulator_entries(payload, resource="simulator versions"),
        )

    async def get_simulator_version(self, simulator_id: str, version: str, include_tests: bool) -> SimulatorSpec:
        """One version's spec, uncached; ``version`` accepts upstream's ``latest`` alias."""

        payload = await fetch_upstream_json_value(
            self._client,
            upstream_url("simulators", simulator_id, version),
            resource="simulator detail",
            params={"includeTests": "true"} if include_tests else None,
        )
        return _validate_detail(payload, resource="simulator detail")

    async def get_ontology(self, namespace: OntologyNamespace) -> OntologyTermList:
        """The cached, trimmed term list for one ontology (the frontend's name cache)."""

        async def load() -> OntologyTermList:
            payload = await fetch_upstream_json_value(
                self._client, upstream_url("ontologies", namespace.value), resource="ontology"
            )
            return OntologyTermList(
                namespace=namespace.value,
                terms=_validate_ontology_terms(payload, resource="ontology"),
            )

        return await self._cache.get(_ontology_cache_key(namespace), load)
