"""The owned simulator contract and its list envelopes (plan §4.2).

Wire names are the upstream-compatible camelCase names the frontend already
reads; Python attributes are snake_case behind ``to_camel`` aliases, and unknown
upstream fields are dropped (``extra="ignore"``).

Null versus empty is contract, not decoration:

* ``algorithms[].parameters`` is ``null`` on 61 of 231 live algorithms and
  ``getSimulatorCurationStatus`` tests ``parameters == null`` to award the
  "Parameters curated" star, so it is ``list[...] | None = None`` -- never a
  default ``[]``.
* an absent ``image`` serializes as ``null``, never ``{}``, because the status
  check is a JS truthiness test.

A top-level JSON array cannot gain a field without breaking clients, so list
responses are wrapped in the envelopes at the bottom of this module. Field-level
consumer and nullability evidence lives in the plan's Phase 0 matrix.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class OntologyNamespace(StrEnum):
    """The ontology namespaces the simulators pages read (plan §4.5 path allowlist)."""

    EDAM = "EDAM"
    SBO = "SBO"
    KISAO = "KISAO"
    SPDX = "SPDX"


class _ContractModel(BaseModel):
    """Shared contract base: camelCase wire names, snake_case attributes, unknown upstream fields dropped."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")


class OntologyRef(_ContractModel):
    """A term in a named ontology: ``{id, namespace}``."""

    id: str | None = None
    namespace: str | None = None


class FormatRef(OntologyRef):
    """An EDAM-format reference: an ``OntologyRef`` plus version and supported features."""

    version: str | None = None
    supported_features: list[str] | None = None


class IdentifierRef(_ContractModel):
    """An external identifier (``{namespace, id, url}``): author identities, citations, cross references."""

    namespace: str | None = None
    id: str | None = None
    url: str | None = None


class SimulatorAuthor(_ContractModel):
    first_name: str | None = None
    middle_name: str | None = None
    last_name: str | None = None
    identifiers: list[IdentifierRef] | None = None


class Citation(_ContractModel):
    title: str | None = None
    authors: str | None = None
    journal: str | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    year: int | None = None
    identifiers: list[IdentifierRef] | None = None


class SimulatorFunding(_ContractModel):
    """One funding record: ``{funder: OntologyRef, grant, url}`` (the frontend reads ``funder.id``)."""

    funder: OntologyRef | None = None
    grant: str | None = None
    url: str | None = None


class SimulatorUrl(_ContractModel):
    type: str | None = None
    title: str | None = None
    url: str | None = None


class PatternTarget(_ContractModel):
    """The target of a model-change or output-variable pattern: ``{value, grammar}``."""

    value: str | None = None
    grammar: str | None = None


class PatternSymbol(_ContractModel):
    """The symbol of a pattern: ``{namespace, value}``."""

    value: str | None = None
    namespace: str | None = None


class ModelChangePattern(_ContractModel):
    name: str | None = None
    types: list[str] | None = None
    target: PatternTarget | None = None
    symbol: PatternSymbol | None = None


class OutputVariablePattern(_ContractModel):
    name: str | None = None
    target: PatternTarget | None = None
    symbol: PatternSymbol | None = None


class SimulatorParameter(_ContractModel):
    id: str | None = None
    name: str | None = None
    kisao_id: OntologyRef | None = None
    type: str | None = None
    value: str | None = None
    recommended_range: list[str] | None = None
    available_software_interface_types: list[str] | None = None


class Dependency(_ContractModel):
    name: str | None = None
    version: str | None = None
    required: bool | None = None
    url: str | None = None
    free_non_commercial_license: bool | None = None


class SimulatorAlgorithm(_ContractModel):
    # Upstream sends null ids for some algorithms and parameters (the frontend
    # falls back to the KISAO id); only the top-level spec identity is required.
    id: str | None = None
    name: str | None = None
    kisao_id: OntologyRef | None = None
    description: str | None = None
    modeling_frameworks: list[OntologyRef] | None = None
    model_formats: list[FormatRef] | None = None
    simulation_formats: list[FormatRef] | None = None
    archive_formats: list[FormatRef] | None = None
    simulation_types: list[str] | None = None
    model_change_patterns: list[ModelChangePattern] | None = None
    parameters: list[SimulatorParameter] | None = None
    output_dimensions: list[OntologyRef] | None = None
    output_variable_patterns: list[OutputVariablePattern] | None = None
    dependencies: list[Dependency] | None = None
    available_software_interface_types: list[str] | None = None
    citations: list[Citation] | None = None


class ValidationMessage(_ContractModel):
    """A ``{category, message}`` note on one validation-test result."""

    category: str | None = None
    message: str | None = None


class ValidationTestCase(_ContractModel):
    id: str | None = None
    description: str | None = None


class ValidationTestCaseResult(_ContractModel):
    case: ValidationTestCase | None = None
    result_type: str | None = None
    duration: float | None = None
    exception: ValidationMessage | None = None
    warnings: list[ValidationMessage] | None = None
    skip_reason: ValidationMessage | None = None
    log: str | None = None


class ValidationTests(_ContractModel):
    """The test-suite block; present only when ``includeTests`` was requested upstream."""

    test_suite_version: str | None = None
    gh_issue: int | None = None
    gh_action_run: int | None = None
    results: list[ValidationTestCaseResult] | None = None


class CurationBlock(_ContractModel):
    """The ``biosimulators`` curation block the curation stars and detail header read."""

    validated: bool | None = None
    image_version: str | None = None
    specification_version: str | None = None
    created: str | None = None
    updated: str | None = None
    validation_tests: ValidationTests | None = None


class ImageBlock(_ContractModel):
    url: str | None = None
    digest: str | None = None
    format: FormatRef | None = None
    operating_system_type: str | None = None


class SimulatorCli(_ContractModel):
    package: str | None = None
    command: str | None = None
    installation_instructions: str | None = None
    package_repository: str | None = None


class SimulatorPythonApi(_ContractModel):
    package: str | None = None
    module: str | None = None
    installation_instructions: str | None = None


class SimulatorReferences(_ContractModel):
    citations: list[Citation] | None = None
    identifiers: list[IdentifierRef] | None = None


class SimulatorSpec(_ContractModel):
    """One simulator version: the union of the table, suggest and detail consumers (plan §4.2)."""

    id: str
    name: str
    version: str
    description: str | None = None
    urls: list[SimulatorUrl] | None = None
    authors: list[SimulatorAuthor] | None = None
    references: SimulatorReferences | None = None
    license: OntologyRef | None = None
    algorithms: list[SimulatorAlgorithm] | None = None
    interface_types: list[str] | None = None
    supported_operating_system_types: list[str] | None = None
    supported_programming_languages: list[OntologyRef] | None = None
    funding: list[SimulatorFunding] | None = None
    biosimulators: CurationBlock | None = None
    image: ImageBlock | None = None
    cli: SimulatorCli | None = None
    python_api: SimulatorPythonApi | None = None


class SimulatorList(_ContractModel):
    """Envelope for ``GET /simulators/latest``: a bare array cannot gain a field without breaking clients."""

    simulators: list[SimulatorSpec]


class SimulatorVersionList(_ContractModel):
    """Envelope for ``GET /simulators/{simulator_id}`` (upstream returns an array of every version)."""

    simulator_id: str
    versions: list[SimulatorSpec]


class OntologyTerm(_ContractModel):
    """Only ``id`` and ``name``: the UI reads nothing else, and EDAM shrinks from 342 KB to 33 KB.

    ``name`` is nullable: EDAM carries two relationship terms without one
    (``isImplementedIn``, ``isOrganizational``), and the frontend's cache ignores
    terms it cannot name anyway.
    """

    id: str
    name: str | None = None


class OntologyTermList(_ContractModel):
    """Envelope for ``GET /ontologies/{namespace}``."""

    namespace: str
    terms: list[OntologyTerm]
