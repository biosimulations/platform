"""Tests for OMEX archive parsing."""

import io
import zipfile

import pytest
from pathlib import Path

from biosim_server.compatibility.omex_parser import _normalize_location, parse_omex_content


@pytest.fixture
def sample_omex_path() -> Path:
    """Path to sample OMEX file in fixtures."""
    return Path(__file__).parent.parent / "fixtures" / "local_data" / "BIOMD0000000010_tellurium_Negative_feedback_and_ultrasen.omex"


def test_parse_omex_content(sample_omex_path: Path) -> None:
    """Test parsing a real OMEX file."""
    with open(sample_omex_path, "rb") as f:
        file_content = f.read()

    omex_content = parse_omex_content(file_content)

    # Check SED-ML files found
    assert len(omex_content.sedml_files) == 1
    assert "BIOMD0000000010_url.sedml" in omex_content.sedml_files[0]

    # Check model formats found
    assert len(omex_content.model_formats) >= 1
    sbml_formats = [mf for mf in omex_content.model_formats
                    if "sbml" in mf.format_uri.lower()]
    assert len(sbml_formats) >= 1

    # Check simulations found
    assert len(omex_content.simulations) >= 1
    # The sample OMEX uses CVODE (KISAO:0000019)
    kisao_ids = [sim.algorithm.id for sim in omex_content.simulations]
    assert "KISAO:0000019" in kisao_ids
    # Check that algorithm name is populated
    cvode_sims = [sim for sim in omex_content.simulations if sim.algorithm.id == "KISAO:0000019"]
    assert cvode_sims[0].algorithm.name == "CVODE"

    # Check simulation types
    sim_types = [sim.simulation_type for sim in omex_content.simulations]
    assert "uniformTimeCourse" in sim_types


def test_parse_omex_model_language(sample_omex_path: Path) -> None:
    """Test that model language is extracted from SED-ML."""
    with open(sample_omex_path, "rb") as f:
        file_content = f.read()

    omex_content = parse_omex_content(file_content)

    # Check that at least one model has language info
    models_with_language = [mf for mf in omex_content.model_formats if mf.language]
    assert len(models_with_language) >= 1

    # The sample uses SBML level 2 version 4
    sbml_models = [mf for mf in models_with_language if mf.language and "sbml" in mf.language.lower()]
    assert len(sbml_models) >= 1


def test_parse_omex_deduplicates_simulations(sample_omex_path: Path) -> None:
    """Test that duplicate simulations are deduplicated."""
    with open(sample_omex_path, "rb") as f:
        file_content = f.read()

    omex_content = parse_omex_content(file_content)

    # Check no duplicate algorithm+type combinations
    seen = set()
    for sim in omex_content.simulations:
        key = (sim.algorithm.id, sim.simulation_type)
        assert key not in seen, f"Duplicate simulation: {key}"
        seen.add(key)


def test_parse_invalid_omex() -> None:
    """Test handling of invalid OMEX content."""
    with pytest.raises(Exception):
        parse_omex_content(b"not a valid zip file")


SEDML_L1V3 = """<?xml version="1.0" encoding="UTF-8"?>
<sedML xmlns="http://sed-ml.org/sed-ml/level1/version3" level="1" version="3">
    <listOfModels>
        <model id="model1" language="urn:sedml:language:sbml" source="Szymanska2009.xml"/>
    </listOfModels>
    <listOfSimulations>
        <uniformTimeCourse id="sim1" initialTime="0" outputStartTime="0" outputEndTime="1000" numberOfPoints="4000">
            <algorithm kisaoID="KISAO:0000496"/>
        </uniformTimeCourse>
    </listOfSimulations>
</sedML>"""


def _build_omex(manifest_locations: dict[str, str], entries: dict[str, str]) -> bytes:
    """Build an in-memory OMEX archive.

    Args:
        manifest_locations: manifest `location` -> `format` URI, written verbatim
            so tests can exercise spellings that differ from the zip entry names.
        entries: zip entry name -> file contents.
    """
    content_elements = "\n".join(
        f'  <content location="{location}" format="{format_uri}"/>'
        for location, format_uri in manifest_locations.items()
    )
    manifest = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<omexManifest xmlns="http://identifiers.org/combine.specifications/omex-manifest">\n'
        f"{content_elements}\n"
        "</omexManifest>"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.xml", manifest)
        for name, contents in entries.items():
            zf.writestr(name, contents)
    return buf.getvalue()


def test_parse_omex_with_dot_slash_manifest_locations() -> None:
    """Manifest locations written as "./file" must resolve to bare zip entries.

    Regression test: BioModels-derived archives declare "./x.sedml" in the
    manifest while storing the entry as "x.sedml". Zip lookups are exact-string,
    so the SED-ML was never read and the endpoint reported that the archive
    contained no simulations.
    """
    file_content = _build_omex(
        manifest_locations={
            ".": "http://identifiers.org/combine.specifications/omex",
            "./Szymanska2009.xml": "http://identifiers.org/combine.specifications/sbml",
            "./BIOMD0000000896_sim.sedml": "http://identifiers.org/combine.specifications/sed-ml",
        },
        entries={
            "Szymanska2009.xml": "<sbml/>",
            "BIOMD0000000896_sim.sedml": SEDML_L1V3,
        },
    )

    omex_content = parse_omex_content(file_content)

    assert omex_content.parse_errors == []
    assert omex_content.sedml_files == ["BIOMD0000000896_sim.sedml"]
    assert [sim.algorithm.id for sim in omex_content.simulations] == ["KISAO:0000496"]
    assert [sim.simulation_type for sim in omex_content.simulations] == ["uniformTimeCourse"]

    # The "./"-prefixed model location must still match the SED-ML model source
    assert len(omex_content.model_formats) == 1
    assert omex_content.model_formats[0].location == "Szymanska2009.xml"
    assert omex_content.model_formats[0].language == "urn:sedml:language:sbml"


def test_parse_omex_with_leading_slash_manifest_locations() -> None:
    """Locations written as "/file" resolve the same way as "./file"."""
    file_content = _build_omex(
        manifest_locations={"/sim.sedml": "http://identifiers.org/combine.specifications/sed-ml"},
        entries={"sim.sedml": SEDML_L1V3},
    )

    omex_content = parse_omex_content(file_content)

    assert omex_content.parse_errors == []
    assert omex_content.sedml_files == ["sim.sedml"]
    assert len(omex_content.simulations) == 1


def test_parse_omex_reports_missing_sedml_instead_of_skipping() -> None:
    """A declared-but-absent SED-ML file is reported rather than silently ignored."""
    file_content = _build_omex(
        manifest_locations={"absent.sedml": "http://identifiers.org/combine.specifications/sed-ml"},
        entries={},
    )

    omex_content = parse_omex_content(file_content)

    assert omex_content.simulations == []
    assert len(omex_content.parse_errors) == 1
    assert "absent.sedml" in omex_content.parse_errors[0]


def test_parse_omex_reports_malformed_sedml() -> None:
    """Unparseable SED-ML is surfaced as a parse error, not an empty result."""
    file_content = _build_omex(
        manifest_locations={"broken.sedml": "http://identifiers.org/combine.specifications/sed-ml"},
        entries={"broken.sedml": "<sedML><unclosed>"},
    )

    omex_content = parse_omex_content(file_content)

    assert omex_content.simulations == []
    assert len(omex_content.parse_errors) == 1
    assert "broken.sedml" in omex_content.parse_errors[0]


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        ("./sim.sedml", "sim.sedml"),
        ("/sim.sedml", "sim.sedml"),
        ("sim.sedml", "sim.sedml"),
        ("./nested/sim.sedml", "nested/sim.sedml"),
        ("././sim.sedml", "sim.sedml"),
        ("  ./sim.sedml  ", "sim.sedml"),
        (".", "."),
    ],
)
def test_normalize_location(location: str, expected: str) -> None:
    """Manifest location spellings normalize to zip entry names."""
    assert _normalize_location(location) == expected
