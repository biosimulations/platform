"""The live-upstream drift test (plan 1.5).

Run by hand and before a release; excluded from the normal run with
``-m "not integration"`` (note: backend CI currently runs plain pytest, so it
does execute this file -- see Phase 2.7):

    uv run pytest -m integration tests/simulators/test_live_contract.py

It pins the owned contract (plan §4.2) against the live registry: zero skipped
entries, the ``includeTests`` query actually reaching upstream, and every
contract field populated somewhere in the list (plus one detail for the
validation-test block). ``POPULATED_SOMEWHERE`` was computed against the live
registry on 2026-11-05; a missing path means upstream removed or stopped
populating a contract field -- real drift.

Fields the live registry never populates (documented here so the assertion above
stays exact): ``algorithms[].description``, ``algorithms[].dependencies[].version``,
``algorithms[].modelChangePatterns[].symbol``, the three ``*Formats[].supportedFeatures``
on archive/simulation formats, ``biosimulators.validationTests[].exception``,
and ``urls[].title``.
"""

import logging
from typing import Any

import httpx
import pytest

from biosim_server.config import get_settings
from biosim_server.simulators.models import OntologyNamespace
from biosim_server.simulators.service import BiosimulatorsService

pytestmark = pytest.mark.integration

POPULATED_SOMEWHERE: frozenset[str] = frozenset({
    "algorithms.archiveFormats.id",
    "algorithms.archiveFormats.namespace",
    "algorithms.archiveFormats.version",
    "algorithms.availableSoftwareInterfaceTypes",
    "algorithms.citations.authors",
    "algorithms.citations.identifiers.id",
    "algorithms.citations.identifiers.namespace",
    "algorithms.citations.identifiers.url",
    "algorithms.citations.issue",
    "algorithms.citations.journal",
    "algorithms.citations.pages",
    "algorithms.citations.title",
    "algorithms.citations.volume",
    "algorithms.citations.year",
    "algorithms.dependencies.freeNonCommercialLicense",
    "algorithms.dependencies.name",
    "algorithms.dependencies.required",
    "algorithms.dependencies.url",
    "algorithms.id",
    "algorithms.kisaoId.id",
    "algorithms.kisaoId.namespace",
    "algorithms.modelChangePatterns.name",
    "algorithms.modelChangePatterns.target.grammar",
    "algorithms.modelChangePatterns.target.value",
    "algorithms.modelChangePatterns.types",
    "algorithms.modelFormats.id",
    "algorithms.modelFormats.namespace",
    "algorithms.modelFormats.version",
    "algorithms.modelingFrameworks.id",
    "algorithms.modelingFrameworks.namespace",
    "algorithms.name",
    "algorithms.outputDimensions.id",
    "algorithms.outputDimensions.namespace",
    "algorithms.outputVariablePatterns.name",
    "algorithms.outputVariablePatterns.symbol.namespace",
    "algorithms.outputVariablePatterns.symbol.value",
    "algorithms.outputVariablePatterns.target.grammar",
    "algorithms.outputVariablePatterns.target.value",
    "algorithms.parameters.availableSoftwareInterfaceTypes",
    "algorithms.parameters.id",
    "algorithms.parameters.kisaoId.id",
    "algorithms.parameters.kisaoId.namespace",
    "algorithms.parameters.name",
    "algorithms.parameters.recommendedRange",
    "algorithms.parameters.type",
    "algorithms.parameters.value",
    "algorithms.simulationFormats.id",
    "algorithms.simulationFormats.namespace",
    "algorithms.simulationFormats.version",
    "algorithms.simulationTypes",
    "authors.firstName",
    "authors.identifiers.id",
    "authors.identifiers.namespace",
    "authors.identifiers.url",
    "authors.lastName",
    "authors.middleName",
    "biosimulators.created",
    "biosimulators.imageVersion",
    "biosimulators.specificationVersion",
    "biosimulators.updated",
    "biosimulators.validated",
    "biosimulators.validationTests.ghActionRun",
    "biosimulators.validationTests.ghIssue",
    "biosimulators.validationTests.results.case.description",
    "biosimulators.validationTests.results.case.id",
    "biosimulators.validationTests.results.duration",
    "biosimulators.validationTests.results.log",
    "biosimulators.validationTests.results.resultType",
    "biosimulators.validationTests.results.skipReason.category",
    "biosimulators.validationTests.results.skipReason.message",
    "biosimulators.validationTests.results.warnings.category",
    "biosimulators.validationTests.results.warnings.message",
    "biosimulators.validationTests.testSuiteVersion",
    "cli.command",
    "cli.installationInstructions",
    "cli.package",
    "cli.packageRepository",
    "description",
    "funding.funder.id",
    "funding.funder.namespace",
    "funding.grant",
    "funding.url",
    "id",
    "image.digest",
    "image.format.id",
    "image.format.namespace",
    "image.format.version",
    "image.operatingSystemType",
    "image.url",
    "interfaceTypes",
    "license.id",
    "license.namespace",
    "name",
    "pythonApi.installationInstructions",
    "pythonApi.module",
    "pythonApi.package",
    "references.citations.authors",
    "references.citations.identifiers.id",
    "references.citations.identifiers.namespace",
    "references.citations.identifiers.url",
    "references.citations.issue",
    "references.citations.journal",
    "references.citations.pages",
    "references.citations.title",
    "references.citations.volume",
    "references.citations.year",
    "references.identifiers.id",
    "references.identifiers.namespace",
    "references.identifiers.url",
    "supportedOperatingSystemTypes",
    "supportedProgrammingLanguages.id",
    "supportedProgrammingLanguages.namespace",
    "urls.type",
    "urls.url",
    "version",
})


def _walk(node: Any, prefix: str, populated: set[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            _visit(value, f"{prefix}.{key}" if prefix else key, populated)
    elif isinstance(node, list):
        for item in node:
            _visit(item, prefix, populated)


def _visit(value: Any, path: str, populated: set[str]) -> None:
    if isinstance(value, (dict, list)):
        _walk(value, path, populated)
    elif value is not None and value != "":
        populated.add(path)


@pytest.mark.asyncio
async def test_live_registry_matches_the_owned_contract(caplog: pytest.LogCaptureFixture) -> None:
    async with httpx.AsyncClient(base_url=get_settings().biosimulators_api_base_url.rstrip("/")) as client:
        service = BiosimulatorsService(client)
        with caplog.at_level(logging.WARNING):
            registry = await service.get_simulators_latest()
            detail = await service.get_simulator_version("copasi", "latest", include_tests=True)
            ontologies = {namespace: await service.get_ontology(namespace) for namespace in OntologyNamespace}

    # Zero skipped entries: every live simulator validates against the owned contract.
    skipped = [record for record in caplog.records if record.getMessage().startswith("Skipped an invalid")]
    assert skipped == [], [record.getMessage() for record in skipped]

    # The includeTests query reached upstream: the detail carries its validation tests.
    assert detail.biosimulators is not None
    assert detail.biosimulators.validation_tests is not None
    assert detail.biosimulators.validation_tests.results

    # Every ontology serves its trimmed terms.
    for namespace, terms in ontologies.items():
        assert terms.namespace == namespace.value
        assert terms.terms

    # Every contract field is populated somewhere in the live list (plus one detail).
    populated: set[str] = set()
    for spec in [*registry.simulators, detail]:
        _walk(spec.model_dump(by_alias=True), "", populated)
    missing = POPULATED_SOMEWHERE - populated
    assert not missing, f"contract fields the live upstream no longer populates: {sorted(missing)}"
