"""Tests for the BioSim DB project search API: GET /projects and /projects/stats.

Covers three layers:
  * pure query/shape helpers (build_match_stage, _stub_from_agg, _label_values) — no DB,
  * the Mongo-backed ProjectDatabaseService — testcontainers Mongo, exercising the
    Projects -> Metadata join, pagination correctness, search, and facet stats,
  * the FastAPI endpoints — TestClient with a mocked project DB service.
"""

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from motor.motor_asyncio import AsyncIOMotorCollection

from biosim_server.api.main import app
from biosim_server.projects import (
    ProjectDatabaseServiceMongo,
    ProjectSearchFilter,
    ProjectStub,
    build_match_stage,
)
from biosim_server.projects.database import (
    _META,
    _format_from_language_urn,
    _image_url,
    _label_values,
    _stub_from_agg,
)

_API = "https://api.biosimulations.org"


def _project_doc(project_id: str, *, run: str | None = None, updated: datetime | None = None) -> dict[str, Any]:
    when = updated or datetime(2024, 1, 1, tzinfo=timezone.utc)
    return {
        "id": project_id,
        "simulationRun": run or f"run-{project_id}",
        "owner": None,
        "created": when,
        "updated": when,
    }


def _metadata_doc(
    run: str,
    *,
    title: str = "A model",
    abstract: str | None = "an abstract",
    description: str | None = None,
    taxa: list[str] | None = None,
    keywords: list[str] | None = None,
    thumbnails: list[str] | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {"title": title}
    if abstract is not None:
        item["abstract"] = abstract
    if description is not None:
        item["description"] = description
    if taxa is not None:
        item["taxa"] = [{"uri": None, "label": t} for t in taxa]
    if keywords is not None:
        item["keywords"] = keywords
    if thumbnails is not None:
        item["thumbnails"] = thumbnails
    return {"simulationRun": run, "metadata": [item]}


def _spec_doc(run: str, *, languages: list[str]) -> dict[str, Any]:
    return {"simulationRun": run, "models": [{"language": lg} for lg in languages]}


async def _seed(projects_col: AsyncIOMotorCollection, metadata_col: AsyncIOMotorCollection,
                pairs: list[tuple[dict[str, Any], dict[str, Any] | None]],
                specifications_col: AsyncIOMotorCollection | None = None,
                specs: list[dict[str, Any]] | None = None) -> None:
    for project, metadata in pairs:
        await projects_col.insert_one(project)
        if metadata is not None:
            await metadata_col.insert_one(metadata)
    if specifications_col is not None and specs:
        for spec in specs:
            await specifications_col.insert_one(spec)


# --------------------------- pure helpers ---------------------------

def test_match_stage_empty_is_none() -> None:
    assert build_match_stage([], "") is None


def test_match_stage_search_only() -> None:
    stage = build_match_stage([], "yeast")
    assert stage is not None and "$or" in stage
    assert any(f"{_META}.title" in clause for clause in stage["$or"])


def test_match_stage_filter_and_search_are_anded() -> None:
    stage = build_match_stage([ProjectSearchFilter(target="taxa", allowable_values=["X"])], "cell cycle")
    assert stage is not None and "$and" in stage and len(stage["$and"]) == 2


def test_match_stage_unknown_target_ignored() -> None:
    assert build_match_stage([ProjectSearchFilter(target="bogus", allowable_values=["v"])], "") is None


def test_label_values_handles_strings_and_objects() -> None:
    assert _label_values(["a", "b"]) == ["a", "b"]
    assert _label_values([{"label": "x"}, {"label": "y"}]) == ["x", "y"]
    assert _label_values([{"label": "x"}, "y", {"nolabel": 1}]) == ["x", "y"]
    assert _label_values(None) == []


def test_stub_from_agg_maps_joined_metadata() -> None:
    doc = {"id": "proj-1", "simulationRun": "run-1", "created": "2024-01-01T00:00:00Z",
           "updated": "2024-01-01T00:00:00Z",
           _META: {"title": "T", "abstract": "A", "thumbnails": ["Figure2.jpg"]},
           "_models": [{"language": "urn:sedml:language:sbml"}]}
    stub = _stub_from_agg(doc, _API)
    assert isinstance(stub, ProjectStub)
    assert (stub.id, stub.simulation_run, stub.name, stub.summary) == ("proj-1", "run-1", "T", "A")
    assert stub.model_format == "SBML"
    assert stub.image_url == f"{_API}/files/run-1/Figure2.jpg/download/?thumbnail=browse"


def test_stub_from_agg_falls_back_when_metadata_missing() -> None:
    stub = _stub_from_agg({"id": "proj-2", "simulationRun": "run-2", _META: {}}, _API)
    assert stub.name == "proj-2"  # no title -> id
    assert stub.summary == ""
    assert stub.model_format == ""
    assert stub.image_url is None


def test_format_from_language_urn() -> None:
    assert _format_from_language_urn("urn:sedml:language:sbml") == "SBML"
    assert _format_from_language_urn("urn:sedml:language:sbml.level-2.version-3") == "SBML"
    assert _format_from_language_urn("urn:sedml:language:cellml.1_0") == "CELLML"
    assert _format_from_language_urn("urn:sedml:language:vcml") == "VCML"
    assert _format_from_language_urn(None) == ""


def test_image_url_variants() -> None:
    # absolute url passes through
    assert _image_url("run-1", ["https://cdn/x.png"], _API) == "https://cdn/x.png"
    # bare filename -> download endpoint, url-encoded
    assert _image_url("run-1", ["a b.jpg"], _API) == f"{_API}/files/run-1/a%20b.jpg/download/?thumbnail=browse"
    # nothing to resolve
    assert _image_url("run-1", [], _API) is None
    assert _image_url("run-1", None, _API) is None


# --------------------------- Mongo-backed service (Projects x Metadata) ---------------------------

@pytest.mark.asyncio
async def test_db_join_and_pagination(
    project_database_service_mongo: tuple[
        ProjectDatabaseServiceMongo, AsyncIOMotorCollection, AsyncIOMotorCollection, AsyncIOMotorCollection
    ],
) -> None:
    """Assemble from Projects joined to Metadata; assert correct totals and
    non-overlapping, correctly-sized pages (the legacy past-page-1 bug)."""
    svc, projects_col, metadata_col, specifications_col = project_database_service_mongo
    pairs: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    for i in range(25):
        run = f"run-{i:02d}"
        pairs.append((_project_doc(f"p{i:02d}", run=run), _metadata_doc(run, title=f"Model {i:02d}")))
    await _seed(projects_col, metadata_col, pairs)

    page1, total = await svc.query_project_stubs(page=1, per_page=10, filters=[], search_term="")
    assert total == 25 and len(page1) == 10
    # the join populated the display name from Metadata
    assert all(s.name.startswith("Model ") for s in page1)

    page2, _ = await svc.query_project_stubs(page=2, per_page=10, filters=[], search_term="")
    page3, _ = await svc.query_project_stubs(page=3, per_page=10, filters=[], search_term="")
    assert len(page2) == 10 and len(page3) == 5  # remainder only

    ids = {s.id for s in page1} | {s.id for s in page2} | {s.id for s in page3}
    assert len(ids) == 25  # no overlap across pages


@pytest.mark.asyncio
async def test_db_project_without_metadata_still_listed(
    project_database_service_mongo: tuple[
        ProjectDatabaseServiceMongo, AsyncIOMotorCollection, AsyncIOMotorCollection, AsyncIOMotorCollection
    ],
) -> None:
    """A project whose run has no Metadata doc must still appear (name -> id)."""
    svc, projects_col, metadata_col, specifications_col = project_database_service_mongo
    await _seed(projects_col, metadata_col, [(_project_doc("lonely", run="run-x"), None)])
    stubs, total = await svc.query_project_stubs(page=1, per_page=10, filters=[], search_term="")
    assert total == 1
    assert stubs[0].id == "lonely" and stubs[0].name == "lonely" and stubs[0].summary == ""


@pytest.mark.asyncio
async def test_db_search_matches_metadata(
    project_database_service_mongo: tuple[
        ProjectDatabaseServiceMongo, AsyncIOMotorCollection, AsyncIOMotorCollection, AsyncIOMotorCollection
    ],
) -> None:
    svc, projects_col, metadata_col, specifications_col = project_database_service_mongo
    await _seed(projects_col, metadata_col, [
        (_project_doc("a", run="ra"), _metadata_doc("ra", title="Yeast cell cycle", abstract="budding yeast")),
        (_project_doc("b", run="rb"), _metadata_doc("rb", title="Cardiac model", abstract="heart")),
    ])
    stubs, total = await svc.query_project_stubs(page=1, per_page=10, filters=[], search_term="yeast")
    assert total == 1 and [s.id for s in stubs] == ["a"]


@pytest.mark.asyncio
async def test_db_filter_by_taxa(
    project_database_service_mongo: tuple[
        ProjectDatabaseServiceMongo, AsyncIOMotorCollection, AsyncIOMotorCollection, AsyncIOMotorCollection
    ],
) -> None:
    svc, projects_col, metadata_col, specifications_col = project_database_service_mongo
    await _seed(projects_col, metadata_col, [
        (_project_doc("a", run="ra"), _metadata_doc("ra", taxa=["Human"])),
        (_project_doc("b", run="rb"), _metadata_doc("rb", taxa=["Yeast"])),
    ])
    stubs, total = await svc.query_project_stubs(
        page=1, per_page=10, filters=[ProjectSearchFilter(target="taxa", allowable_values=["Human"])], search_term="")
    assert total == 1 and [s.id for s in stubs] == ["a"]


@pytest.mark.asyncio
async def test_db_enriches_model_format_and_image_url(
    project_database_service_mongo: tuple[
        ProjectDatabaseServiceMongo, AsyncIOMotorCollection, AsyncIOMotorCollection, AsyncIOMotorCollection
    ],
) -> None:
    """Page items pick up model_format from Specifications and image_url from the
    metadata thumbnail; a run without a Specifications doc degrades to empty."""
    svc, projects_col, metadata_col, specifications_col = project_database_service_mongo
    await _seed(
        projects_col, metadata_col,
        [
            (_project_doc("a", run="ra"), _metadata_doc("ra", thumbnails=["Figure2.jpg"])),
            (_project_doc("b", run="rb"), _metadata_doc("rb", thumbnails=None)),
        ],
        specifications_col=specifications_col,
        specs=[_spec_doc("ra", languages=["urn:sedml:language:sbml.level-2.version-3"])],
    )
    stubs, _ = await svc.query_project_stubs(page=1, per_page=10, filters=[], search_term="")
    by_id = {s.id: s for s in stubs}
    assert by_id["a"].model_format == "SBML"
    assert by_id["a"].image_url is not None and by_id["a"].image_url.endswith(
        "/files/ra/Figure2.jpg/download/?thumbnail=browse"
    )
    # b has neither a spec nor a thumbnail -> both degrade cleanly
    assert by_id["b"].model_format == ""
    assert by_id["b"].image_url is None


@pytest.mark.asyncio
async def test_db_stats_counts_facets_over_join(
    project_database_service_mongo: tuple[
        ProjectDatabaseServiceMongo, AsyncIOMotorCollection, AsyncIOMotorCollection, AsyncIOMotorCollection
    ],
) -> None:
    svc, projects_col, metadata_col, specifications_col = project_database_service_mongo
    await _seed(projects_col, metadata_col, [
        (_project_doc("a", run="ra"), _metadata_doc("ra", taxa=["Human"], keywords=["cardiac"])),
        (_project_doc("b", run="rb"), _metadata_doc("rb", taxa=["Human"], keywords=["neural"])),
        (_project_doc("c", run="rc"), _metadata_doc("rc", taxa=["Yeast"])),
    ])
    stats = await svc.query_project_stats(filters=[], search_term="")
    by_target = {s.target: s for s in stats}
    taxa_counts = {vf.value: vf.count for vf in by_target["taxa"].value_frequencies}
    assert taxa_counts == {"Human": 2, "Yeast": 1}
    assert by_target["taxa"].value_frequencies[0].value == "Human"  # descending by count


# --------------------------- FastAPI endpoints ---------------------------

@patch("biosim_server.projects.router.get_project_database_service")
def test_endpoint_service_unavailable(mock_get_db: MagicMock) -> None:
    mock_get_db.return_value = None
    client = TestClient(app)
    assert client.get("/projects").status_code == 503


@patch("biosim_server.projects.router.get_project_database_service")
def test_endpoint_bad_filters_json(mock_get_db: MagicMock) -> None:
    mock_get_db.return_value = AsyncMock()
    client = TestClient(app)
    resp = client.get("/projects", params={"filters": "{not json"})
    assert resp.status_code == 400


@patch("biosim_server.projects.router.get_project_database_service")
def test_endpoint_success_shape(mock_get_db: MagicMock) -> None:
    db = AsyncMock()
    db.query_project_stubs.return_value = (
        [ProjectStub(id="a", simulation_run="run-a", created="2024-01-01T00:00:00Z",
                     updated="2024-01-01T00:00:00Z", name="Model A", summary="s")],
        1,
    )
    mock_get_db.return_value = db
    client = TestClient(app)
    resp = client.get("/projects", params={"page": 1, "perPage": 20})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1 and len(body["items"]) == 1
    item = body["items"][0]
    assert item["id"] == "a"
    assert item["simulationRun"] == "run-a"  # camelCase alias
    assert item["name"] == "Model A"


@patch("biosim_server.projects.router.get_project_database_service")
def test_endpoint_stats_shape(mock_get_db: MagicMock) -> None:
    from biosim_server.projects import ProjectQueryStat, ValueFrequency

    db = AsyncMock()
    db.query_project_stats.return_value = [
        ProjectQueryStat(target="taxa", value_frequencies=[ValueFrequency(value="Human", count=2)])
    ]
    mock_get_db.return_value = db
    client = TestClient(app)
    resp = client.get("/projects/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["target"] == "taxa"
    assert body[0]["valueFrequencies"][0] == {"value": "Human", "count": 2}


@patch("biosim_server.projects.router.get_settings")
def test_reindex_disabled_without_token(mock_settings: MagicMock) -> None:
    mock_settings.return_value = MagicMock(project_reindex_token="")
    client = TestClient(app)
    assert client.post("/projects/reindex").status_code == 503


@patch("biosim_server.projects.router.get_project_database_service")
@patch("biosim_server.projects.router.get_settings")
def test_reindex_requires_valid_token(mock_settings: MagicMock, mock_get_db: MagicMock) -> None:
    mock_settings.return_value = MagicMock(project_reindex_token="s3cret")
    mock_get_db.return_value = AsyncMock()
    client = TestClient(app)
    assert client.post("/projects/reindex").status_code == 401  # no header
    assert client.post("/projects/reindex", headers={"Authorization": "Bearer wrong"}).status_code == 401


@patch("biosim_server.projects.router.get_project_database_service")
@patch("biosim_server.projects.router.get_settings")
def test_reindex_valid_token_rebuilds(mock_settings: MagicMock, mock_get_db: MagicMock) -> None:
    mock_settings.return_value = MagicMock(project_reindex_token="s3cret")
    db = AsyncMock()
    db.rebuild_index = AsyncMock(return_value=7)
    mock_get_db.return_value = db
    client = TestClient(app)
    resp = client.post("/projects/reindex", headers={"Authorization": "Bearer s3cret"})
    assert resp.status_code == 200 and resp.json() == {"indexed": 7}
