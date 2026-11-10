"""The owned simulator contract: round trips, wire names, null preservation (plan §4.2)."""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from biosim_server.simulators.models import (
    OntologyTerm,
    OntologyTermList,
    SimulatorList,
    SimulatorSpec,
    SimulatorVersionList,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _round_trips_to_identical_wire_names(model_class: type[BaseModel], payload: Any) -> None:
    """Validate, serialize back by alias, and require the fixture byte-for-byte (nulls included)."""
    model = model_class.model_validate(payload)
    assert model.model_dump(by_alias=True, exclude_unset=True) == payload


def test_every_list_fixture_entry_round_trips_to_identical_wire_names() -> None:
    for entry in _load("simulators_latest.json"):
        _round_trips_to_identical_wire_names(SimulatorSpec, entry)


def test_detail_fixture_round_trips_to_identical_wire_names() -> None:
    _round_trips_to_identical_wire_names(SimulatorSpec, _load("simulator_detail.json"))


def test_ontology_fixtures_round_trip_to_identical_wire_names() -> None:
    for namespace, terms in _load("ontology_terms.json").items():
        assert len(terms) == 5
        for term in terms:
            _round_trips_to_identical_wire_names(OntologyTerm, term)
        envelope = OntologyTermList(namespace=namespace, terms=[OntologyTerm.model_validate(t) for t in terms])
        assert envelope.model_dump(by_alias=True, exclude_unset=True) == {"namespace": namespace, "terms": terms}


def test_list_envelope_round_trips() -> None:
    raw = _load("simulators_latest.json")
    envelope = SimulatorList(simulators=[SimulatorSpec.model_validate(entry) for entry in raw])
    assert envelope.model_dump(by_alias=True, exclude_unset=True) == {"simulators": raw}


def test_version_envelope_uses_the_camel_case_wire_name() -> None:
    envelope = SimulatorVersionList(simulator_id="copasi", versions=[])
    assert envelope.model_dump(by_alias=True) == {"simulatorId": "copasi", "versions": []}


def test_unknown_upstream_fields_are_dropped() -> None:
    spec = SimulatorSpec.model_validate({"id": "copasi", "name": "COPASI", "version": "1", "tasks": [], "owner": "x"})
    dumped = spec.model_dump(by_alias=True)
    assert "tasks" not in dumped and "owner" not in dumped


def test_algorithm_parameters_null_is_served_as_null_never_an_empty_list() -> None:
    """``parameters == null`` drives the "Parameters curated" star; a default [] would raise ratings."""
    algorithms = _load("simulators_latest.json")[0]["algorithms"]
    null_entry = next(algorithm for algorithm in algorithms if algorithm["parameters"] is None)
    spec = SimulatorSpec.model_validate({**_load("simulators_latest.json")[0], "algorithms": [null_entry]})
    algorithm = spec.algorithms[0] if spec.algorithms else None
    assert algorithm is not None and algorithm.parameters is None
    assert spec.model_dump(by_alias=True)["algorithms"][0]["parameters"] is None


def test_algorithm_parameters_empty_list_is_served_as_an_empty_list() -> None:
    algorithms = _load("simulators_latest.json")[0]["algorithms"]
    empty_entry = next(algorithm for algorithm in algorithms if algorithm["parameters"] == [])
    spec = SimulatorSpec.model_validate({**_load("simulators_latest.json")[0], "algorithms": [empty_entry]})
    assert spec.algorithms is not None and spec.algorithms[0].parameters == []
    assert spec.model_dump(by_alias=True)["algorithms"][0]["parameters"] == []


def test_absent_image_serializes_as_null_never_an_empty_object() -> None:
    """The status check is a JS truthiness test on ``image``; ``{}`` would count as present."""
    entry = _load("simulators_latest.json")[1]
    assert "image" not in entry
    spec = SimulatorSpec.model_validate(entry)
    assert spec.image is None
    assert spec.model_dump(by_alias=True)["image"] is None


def test_wire_names_are_the_camel_case_names_the_frontend_reads() -> None:
    detail = SimulatorSpec.model_validate(_load("simulator_detail.json"))
    dumped = detail.model_dump(by_alias=True)
    algorithm = dumped["algorithms"][0]
    assert set(dumped) >= {
        "supportedOperatingSystemTypes",
        "supportedProgrammingLanguages",
        "interfaceTypes",
        "pythonApi",
        "biosimulators",
    }
    assert set(algorithm) >= {
        "kisaoId",
        "modelingFrameworks",
        "modelFormats",
        "simulationFormats",
        "archiveFormats",
        "simulationTypes",
        "modelChangePatterns",
        "outputDimensions",
        "outputVariablePatterns",
        "availableSoftwareInterfaceTypes",
    }
    parameter = algorithm["parameters"][0]
    assert set(parameter) >= {"kisaoId", "recommendedRange", "availableSoftwareInterfaceTypes"}
    validation_tests = dumped["biosimulators"]["validationTests"]
    assert set(validation_tests) >= {"testSuiteVersion", "ghIssue", "ghActionRun"}
    result = validation_tests["results"][0]
    assert set(result) >= {"case", "resultType", "duration", "exception", "warnings", "skipReason", "log"}
