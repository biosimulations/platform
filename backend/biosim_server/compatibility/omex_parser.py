"""Parse OMEX archives to extract model formats and simulation requirements."""

import logging
import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO

from biosim_server.compatibility.models import KisaoTerm, ModelFormat, OmexContent, SimulationRequirement
from biosim_server.compatibility.simulator_matcher import _normalize_kisao_id, get_kisao_term_name_sync

logger = logging.getLogger(__name__)


def _create_kisao_term(kisao_id: str) -> KisaoTerm:
    """Create a KisaoTerm with ID and name from static KISAO data."""
    normalized_id = _normalize_kisao_id(kisao_id)
    name = get_kisao_term_name_sync(normalized_id)
    return KisaoTerm(id=normalized_id, name=name)

# Namespaces used in OMEX/SED-ML files
OMEX_MANIFEST_NS = "http://identifiers.org/combine.specifications/omex-manifest"
SEDML_NS = "http://sed-ml.org/sed-ml/level1/version4"
SEDML_NS_V3 = "http://sed-ml.org/sed-ml/level1/version3"
SEDML_NS_V2 = "http://sed-ml.org/sed-ml/level1/version2"
SEDML_NS_V1 = "http://sed-ml.org/"

# Format URIs for SED-ML files
SEDML_FORMAT_URIS = {
    "http://identifiers.org/combine.specifications/sed-ml",
    "http://identifiers.org/combine.specifications/sedml",
}

# Format URIs for model files
MODEL_FORMAT_URIS = {
    "http://identifiers.org/combine.specifications/sbml": "SBML",
    "http://identifiers.org/combine.specifications/cellml": "CellML",
    "http://identifiers.org/combine.specifications/cellml.1.0": "CellML",
    "http://identifiers.org/combine.specifications/cellml.1.1": "CellML",
    "http://identifiers.org/combine.specifications/cellml.2.0": "CellML",
    "http://identifiers.org/combine.specifications/neuroml": "NeuroML",
    "http://purl.org/NET/mediatypes/application/sbml+xml": "SBML",
}

# Map simulation element names to types
SIMULATION_TYPE_MAP = {
    "uniformTimeCourse": "uniformTimeCourse",
    "steadyState": "steadyState",
    "oneStep": "oneStep",
    "analysis": "analysis",
}


def _normalize_location(location: str) -> str:
    """Normalize a manifest location to the spelling used for zip entry names.

    COMBINE manifests routinely write archive-root-relative paths as
    "./file.sedml" (and occasionally "/file.sedml"), while the zip entries are
    stored as "file.sedml". Zip lookups are exact-string, so the two spellings
    have to be reconciled before reading.
    """
    normalized = location.strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.lstrip("/")


def _resolve_zip_entry(zf: zipfile.ZipFile, location: str) -> str | None:
    """Map a manifest location onto an actual entry name in the archive.

    Returns None if no entry can be identified, in which case the caller is
    responsible for reporting the failure rather than skipping silently.
    """
    names = zf.namelist()
    name_set = set(names)

    normalized = _normalize_location(location)
    if normalized in name_set:
        return normalized
    if location in name_set:
        return location

    # Last resort: an unambiguous basename match, which covers manifests that
    # disagree with the archive about directory nesting. Ambiguous matches are
    # deliberately left unresolved.
    basename = normalized.rsplit("/", 1)[-1]
    matches = [name for name in names if name.rsplit("/", 1)[-1] == basename]
    if len(matches) == 1:
        return matches[0]

    return None


def parse_omex_content(file_content: bytes) -> OmexContent:
    """Parse an OMEX archive and extract model formats and simulation requirements.

    Args:
        file_content: Raw bytes of the OMEX file

    Returns:
        OmexContent with parsed model formats and simulation requirements
    """
    model_formats: list[ModelFormat] = []
    simulations: list[SimulationRequirement] = []
    sedml_files: list[str] = []
    parse_errors: list[str] = []

    with zipfile.ZipFile(BytesIO(file_content), 'r') as zf:
        # Parse manifest.xml to find files
        manifest_content = zf.read("manifest.xml")
        manifest_root = ET.fromstring(manifest_content)

        # Find model files and SED-ML files from manifest
        for content in manifest_root.findall(f"{{{OMEX_MANIFEST_NS}}}content"):
            location = content.get("location", "")
            format_uri = content.get("format", "")

            # Skip the manifest itself
            if _normalize_location(location) in ("", ".", "manifest.xml"):
                continue

            # Check if it's a model file
            if format_uri in MODEL_FORMAT_URIS:
                model_formats.append(ModelFormat(
                    format_uri=format_uri,
                    location=_normalize_location(location)
                ))

            # Check if it's a SED-ML file
            if format_uri in SEDML_FORMAT_URIS:
                sedml_files.append(_normalize_location(location))

        # Parse each SED-ML file
        for sedml_location in sedml_files:
            entry_name = _resolve_zip_entry(zf, sedml_location)
            if entry_name is None:
                message = f"SED-ML file declared in manifest but not found in archive: {sedml_location}"
                logger.warning(message)
                parse_errors.append(message)
                continue
            try:
                sedml_content = zf.read(entry_name)
                sedml_simulations = _parse_sedml(sedml_content, model_formats)
                simulations.extend(sedml_simulations)
            except (KeyError, ET.ParseError) as e:
                message = f"Failed to parse SED-ML file {sedml_location}: {e}"
                logger.warning(message)
                parse_errors.append(message)

    # Deduplicate simulations by algorithm
    seen_algorithms: set[tuple[str, str]] = set()
    unique_simulations: list[SimulationRequirement] = []
    for sim in simulations:
        key = (sim.algorithm.id, sim.simulation_type)
        if key not in seen_algorithms:
            seen_algorithms.add(key)
            unique_simulations.append(sim)

    return OmexContent(
        model_formats=model_formats,
        simulations=unique_simulations,
        sedml_files=sedml_files,
        parse_errors=parse_errors
    )


def _parse_sedml(sedml_content: bytes, model_formats: list[ModelFormat]) -> list[SimulationRequirement]:
    """Parse a SED-ML file to extract simulation requirements.

    Args:
        sedml_content: Raw bytes of the SED-ML file
        model_formats: List of model formats to update with language info

    Returns:
        List of SimulationRequirement objects
    """
    simulations: list[SimulationRequirement] = []

    root = ET.fromstring(sedml_content)

    # Detect SED-ML namespace
    ns = _detect_sedml_namespace(root)
    if not ns:
        return simulations

    # Extract model languages and update model_formats
    for model in root.findall(f".//{{{ns}}}model"):
        language = model.get("language")
        source = model.get("source", "")

        if language and source and not source.startswith("#"):
            # Find matching model format and update language
            for mf in model_formats:
                if mf.location.endswith(source) or source.endswith(mf.location):
                    mf.language = language
                    break

    # Extract simulations and their algorithms
    for sim_type_name in SIMULATION_TYPE_MAP:
        for sim_elem in root.findall(f".//{{{ns}}}{sim_type_name}"):
            algorithm = sim_elem.find(f"{{{ns}}}algorithm")
            if algorithm is not None:
                kisao_id = algorithm.get("kisaoID")
                if kisao_id:
                    simulations.append(SimulationRequirement(
                        algorithm=_create_kisao_term(kisao_id),
                        simulation_type=SIMULATION_TYPE_MAP[sim_type_name]
                    ))

    return simulations


def _detect_sedml_namespace(root: ET.Element) -> str | None:
    """Detect the SED-ML namespace from the root element."""
    tag = root.tag
    if tag.startswith("{"):
        ns = tag[1:tag.index("}")]
        if "sed-ml" in ns or "sedml" in ns.lower():
            return ns

    # Check known namespaces
    for ns in [SEDML_NS, SEDML_NS_V3, SEDML_NS_V2, SEDML_NS_V1]:
        if root.find(f".//{{{ns}}}listOfSimulations") is not None:
            return ns

    return None
