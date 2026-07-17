"""Tests for Phase-1 $text search: the platform-owned project_search collection.

Covers the indexer (rebuild_index materializes from source collections) and the
$text-ranked read path, via testcontainers Mongo.
"""

from datetime import datetime, timezone
from typing import Any

import pytest
from motor.motor_asyncio import AsyncIOMotorCollection

from biosim_server.projects import ProjectSearchServiceMongo
from biosim_server.projects.models import ProjectSearchFilter

Cols = tuple[
    ProjectSearchServiceMongo,
    AsyncIOMotorCollection,
    AsyncIOMotorCollection,
    AsyncIOMotorCollection,
    AsyncIOMotorCollection,
]


def _project(pid: str, run: str, updated: datetime | None = None) -> dict[str, Any]:
    when = updated or datetime(2024, 1, 1, tzinfo=timezone.utc)
    return {"id": pid, "simulationRun": run, "owner": None, "created": when, "updated": when}


def _metadata(run: str, *, title: str, abstract: str = "", description: str = "",
              taxa: list[str] | None = None, keywords: list[str] | None = None,
              thumbnails: list[str] | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"title": title, "abstract": abstract, "description": description}
    if taxa is not None:
        item["taxa"] = [{"uri": None, "label": t} for t in taxa]
    if keywords is not None:
        item["keywords"] = keywords
    if thumbnails is not None:
        item["thumbnails"] = thumbnails
    return {"simulationRun": run, "metadata": [item]}


def _spec(run: str, languages: list[str]) -> dict[str, Any]:
    return {"simulationRun": run, "models": [{"language": lg} for lg in languages]}


async def _seed_and_build(
    cols: Cols,
    projects: list[dict[str, Any]],
    metadatas: list[dict[str, Any]],
    specs: list[dict[str, Any]],
) -> int:
    svc, projects_col, metadata_col, specifications_col, _search = cols
    if projects:
        await projects_col.insert_many(projects)
    if metadatas:
        await metadata_col.insert_many(metadatas)
    if specs:
        await specifications_col.insert_many(specs)
    return await svc.rebuild_index()


@pytest.mark.asyncio
async def test_rebuild_materializes_enriched_docs(project_search_service_mongo: Cols) -> None:
    svc, _p, _m, _s, search_col = project_search_service_mongo
    n = await _seed_and_build(
        project_search_service_mongo,
        [_project("p1", "r1")],
        [_metadata("r1", title="Yeast cell cycle", abstract="budding yeast",
                   taxa=["Yeast"], thumbnails=["Fig.png"])],
        [_spec("r1", ["urn:sedml:language:sbml"])],
    )
    assert n == 1
    doc = await search_col.find_one({"id": "p1"})
    assert doc is not None
    # enriched at index time: model_format from Specifications, image_url built,
    # raw text + facets carried for the index
    assert doc["name"] == "Yeast cell cycle"
    assert doc["model_format"] == "SBML"
    assert doc["image_url"].endswith("/files/r1/Fig.png/download/?thumbnail=browse")
    assert doc["taxa"] == ["Yeast"]
    assert doc["title"] == "Yeast cell cycle"


@pytest.mark.asyncio
async def test_summary_and_text_are_html_stripped(project_search_service_mongo: Cols) -> None:
    svc, _p, _m, _s, search_col = project_search_service_mongo
    html_abstract = (
        '<body xmlns="http://www.w3.org/1999/xhtml">'
        '<div class="dc:title">Edelstein1996 - EPSP</div> A model of &alpha;7 receptors.</body>'
    )
    await _seed_and_build(
        project_search_service_mongo,
        [_project("p1", "r1")],
        [_metadata("r1", title="<i>Yeast</i> model", abstract=html_abstract)],
        [],
    )
    doc = await search_col.find_one({"id": "p1"})
    assert doc is not None
    assert "<" not in doc["summary"] and "<" not in doc["title"] and "<" not in doc["abstract"]
    assert doc["name"] == "Yeast model"          # tags stripped from title
    assert "α7" in doc["abstract"]          # &alpha; entity unescaped -> α7
    # still searchable by the plain-text content (whole-token match, not substring)
    stubs, _ = await svc.query_project_stubs(page=1, per_page=10, filters=[], search_term="receptors")
    assert [s.id for s in stubs] == ["p1"]


@pytest.mark.asyncio
async def test_summary_is_truncated(project_search_service_mongo: Cols) -> None:
    svc, _p, _m, _s, search_col = project_search_service_mongo
    long_abstract = "word " * 200  # ~1000 chars
    await _seed_and_build(
        project_search_service_mongo,
        [_project("p1", "r1")],
        [_metadata("r1", title="T", abstract=long_abstract)],
        [],
    )
    doc = await search_col.find_one({"id": "p1"})
    assert doc is not None
    assert len(doc["summary"]) <= 301 and doc["summary"].endswith("…")  # truncated + ellipsis
    assert len(doc["abstract"]) > 301  # full text kept for search


@pytest.mark.asyncio
async def test_text_search_ranks_by_relevance(project_search_service_mongo: Cols) -> None:
    """A title hit should outrank an abstract-only hit (title weighted higher)."""
    svc = project_search_service_mongo[0]
    await _seed_and_build(
        project_search_service_mongo,
        [_project("title_hit", "r1"), _project("abstract_hit", "r2"), _project("miss", "r3")],
        [
            _metadata("r1", title="Calcium signaling model", abstract="unrelated"),
            _metadata("r2", title="Some model", abstract="calcium dynamics in the cell"),
            _metadata("r3", title="Cardiac model", abstract="heart electrophysiology"),
        ],
        [],
    )
    stubs, total = await svc.query_project_stubs(page=1, per_page=10, filters=[], search_term="calcium")
    assert total == 2
    assert [s.id for s in stubs] == ["title_hit", "abstract_hit"]  # title hit ranks first


@pytest.mark.asyncio
async def test_no_search_sorts_by_recency(project_search_service_mongo: Cols) -> None:
    svc = project_search_service_mongo[0]
    await _seed_and_build(
        project_search_service_mongo,
        [
            _project("old", "r1", updated=datetime(2022, 1, 1, tzinfo=timezone.utc)),
            _project("new", "r2", updated=datetime(2025, 1, 1, tzinfo=timezone.utc)),
        ],
        [_metadata("r1", title="Old"), _metadata("r2", title="New")],
        [],
    )
    stubs, total = await svc.query_project_stubs(page=1, per_page=10, filters=[], search_term="")
    assert total == 2
    assert [s.id for s in stubs] == ["new", "old"]  # newest first


@pytest.mark.asyncio
async def test_filter_and_pagination(project_search_service_mongo: Cols) -> None:
    svc = project_search_service_mongo[0]
    projects = [_project(f"p{i:02d}", f"r{i:02d}") for i in range(15)]
    metadatas = [_metadata(f"r{i:02d}", title=f"Model {i:02d}", taxa=["Human" if i % 2 else "Yeast"])
                 for i in range(15)]
    await _seed_and_build(project_search_service_mongo, projects, metadatas, [])

    human = [ProjectSearchFilter(target="taxa", allowable_values=["Human"])]
    page1, total = await svc.query_project_stubs(page=1, per_page=5, filters=human, search_term="")
    assert total == 7  # odd indices 1,3,5,7,9,11,13
    assert len(page1) == 5
    page2, _ = await svc.query_project_stubs(page=2, per_page=5, filters=human, search_term="")
    assert len(page2) == 2  # remainder
    assert not ({s.id for s in page1} & {s.id for s in page2})  # no overlap


@pytest.mark.asyncio
async def test_stats_ignore_structured_filters(project_search_service_mongo: Cols) -> None:
    """Facet counts reflect search but not the active filters (stable menu)."""
    svc = project_search_service_mongo[0]
    await _seed_and_build(
        project_search_service_mongo,
        [_project("a", "r1"), _project("b", "r2"), _project("c", "r3")],
        [
            _metadata("r1", title="A", taxa=["Human"], keywords=["cardiac"]),
            _metadata("r2", title="B", taxa=["Human"], keywords=["neural"]),
            _metadata("r3", title="C", taxa=["Yeast"]),
        ],
        [],
    )
    # even with a taxa filter active, the taxa facet still shows the full counts
    stats = await svc.query_project_stats(
        filters=[ProjectSearchFilter(target="taxa", allowable_values=["Human"])], search_term="")
    taxa = {vf.value: vf.count for s in stats if s.target == "taxa" for vf in s.value_frequencies}
    assert taxa == {"Human": 2, "Yeast": 1}


@pytest.mark.asyncio
async def test_keyword_and_taxa_values_are_searchable(project_search_service_mongo: Cols) -> None:
    """keywords + taxa labels are in the configured searchable set, so a term that
    only appears in them still matches."""
    svc = project_search_service_mongo[0]
    await _seed_and_build(
        project_search_service_mongo,
        [_project("kw", "r1"), _project("tx", "r2")],
        [
            _metadata("r1", title="Model One", abstract="nothing relevant", keywords=["arrhythmia"]),
            _metadata("r2", title="Model Two", abstract="nothing relevant", taxa=["Danio rerio"]),
        ],
        [],
    )
    by_keyword, _ = await svc.query_project_stubs(page=1, per_page=10, filters=[], search_term="arrhythmia")
    assert [s.id for s in by_keyword] == ["kw"]
    by_taxon, _ = await svc.query_project_stubs(page=1, per_page=10, filters=[], search_term="Danio")
    assert [s.id for s in by_taxon] == ["tx"]


@pytest.mark.asyncio
async def test_ensure_indexes_rebuilds_text_index_on_change(project_search_service_mongo: Cols) -> None:
    """A pre-existing text index with a different field set is dropped and
    recreated from the configured weights."""
    svc, _p, _m, _s, search_col = project_search_service_mongo
    # stand up an old-style index (title only)
    await search_col.create_index([("title", "text")], weights={"title": 1}, name="project_text")
    await svc.ensure_indexes()
    weights = (await search_col.index_information())["project_text"]["weights"]
    assert set(weights.keys()) == {"title", "abstract", "description", "keywords", "taxa"}
    assert weights["title"] == 10 and weights["keywords"] == 4 and weights["taxa"] == 3


@pytest.mark.asyncio
async def test_rebuild_if_empty_then_noop(project_search_service_mongo: Cols) -> None:
    svc, projects_col, metadata_col, _s, _search = project_search_service_mongo
    await projects_col.insert_one(_project("p1", "r1"))
    await metadata_col.insert_one(_metadata("r1", title="One"))
    assert await svc.rebuild_index_if_empty() == 1   # first run populates
    assert await svc.rebuild_index_if_empty() == 0   # already populated -> no-op
