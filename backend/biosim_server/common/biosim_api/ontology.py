"""Mirror of the biosimulations.org ``GET /ontologies/KISAO/{id}`` contract.

An execution log names its algorithm as a bare KISAO id string, so resolving a
term is per-algorithm and repeats across every document, task and output of a
log. That makes it lazy and cacheable, never something to embed in a log
response.

**Two spellings.** The log payload (and therefore the upstream path) uses
``KISAO_0000019``; the vendored ``KISAO_TERMS`` table and the OLS4 permalink use
``KISAO:0000019``. Both conversions live here so no caller on the passthrough
surface has to remember which form it is holding.

Two other private normalizers exist in this repo and are deliberately *not*
shared with these: ``compatibility.simulator_matcher._normalize_kisao_id`` also
prefixes a bare numeric id and rewrites every underscore, which simulator
matching depends on but would be wrong for a passthrough id; and
``projects.search._kisao_name`` is an inline lookup in the Mongo indexing path.
Folding either into this module would change behavior outside this feature.
"""

from biosim_server.common.biosim_api.common import UpstreamModel
from biosim_server.common.kisao_data import KISAO_TERMS

_UNDERSCORE_PREFIX = "KISAO_"
_COLON_PREFIX = "KISAO:"


class KisaoTerm(UpstreamModel):
    """One KISAO algorithm term."""

    id: str | None = None
    name: str | None = None
    url: str | None = None
    description: str | None = None


def normalize_kisao_id(kisao_id: str) -> str:
    """Canonical colon form (``KISAO:0000019``) -- keys KISAO_TERMS and OLS."""
    stripped = kisao_id.strip()
    if stripped.upper().startswith(_UNDERSCORE_PREFIX):
        return _COLON_PREFIX + stripped[len(_UNDERSCORE_PREFIX):]
    return stripped


def upstream_kisao_id(kisao_id: str) -> str:
    """Underscore form (``KISAO_0000019``) -- what the upstream path expects.

    This is the spelling the frontend has always sent (it passes a log's
    ``algorithm`` value straight into the URL), so it is the only form with
    evidence of working against the real API.
    """
    stripped = kisao_id.strip()
    if stripped.upper().startswith(_COLON_PREFIX):
        return _UNDERSCORE_PREFIX + stripped[len(_COLON_PREFIX):]
    return stripped


def kisao_ols_url(kisao_id: str) -> str:
    """Permalink for a term in the EBI OLS4 browser."""
    return (
        "https://www.ebi.ac.uk/ols4/ontologies/kisao/terms"
        f"?obo_id={normalize_kisao_id(kisao_id)}"
    )


def local_kisao_term(kisao_id: str) -> KisaoTerm | None:
    """Best-effort term from the vendored KISAO table, or None if unknown.

    The table is generated from OLS with ``name`` and ancestors only -- it has no
    definitions -- so ``description`` is genuinely unavailable here and is left
    None rather than filled with a placeholder.
    """
    term = KISAO_TERMS.get(normalize_kisao_id(kisao_id))
    if term is None:
        return None
    return KisaoTerm(
        id=normalize_kisao_id(kisao_id),
        name=term["name"],
        url=kisao_ols_url(kisao_id),
        description=None,
    )
