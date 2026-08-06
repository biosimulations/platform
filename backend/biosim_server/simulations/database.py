"""Persistence for user-facing simulation run records.

Backs ``POST /simulations/runs`` (the table listing). One record is written per
``(submission x simulator)`` when a run is submitted; the workflow updates each
record's status as the run reaches a terminal state.
"""

import logging
import re
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection
from pymongo import ASCENDING, DESCENDING
from pymongo.results import InsertOneResult
from typing_extensions import override

from biosim_server.config import get_settings
from biosim_server.simulations.models import (
    ListSimulationRunsRequest,
    SimulationRunRecord,
    TableSort,
)

logger = logging.getLogger(__name__)

# Maps API/table field ids onto persisted document fields. Anything not listed
# is ignored, so callers cannot filter or sort on arbitrary internal fields.
_FILTERABLE_FIELDS: dict[str, str] = {
    "id": "run_id",
    "run_id": "run_id",
    "name": "name",
    "simulator": "simulator",
    "simulatorVersion": "simulator_version",
    "simulator_version": "simulator_version",
    "status": "status",
    "email": "email",
    "purpose": "purpose",
    "createdAt": "submitted",
    "submitted": "submitted",
    "updated": "updated",
    "runtime": "runtime",
}

_DATE_FIELDS = {"submitted", "updated"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_date(value: Any) -> Any:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    return value


def _filter_clause(db_field: str, operator: str | None, value: Any) -> Any | None:
    """Translate one TableFilter into a Mongo match clause for ``db_field``.

    Returns ``None`` when the filter is incomplete or unsupported (skip it)."""
    is_date = db_field in _DATE_FIELDS
    op = operator or "contains"
    if value is None and op != "is_any":
        return None
    if op in ("equal", "is"):
        return value
    if op == "on" and is_date:
        start = _coerce_date(value)
        if isinstance(start, datetime):
            start = start.replace(hour=0, minute=0, second=0, microsecond=0)
            return {"$gte": start, "$lt": start + timedelta(days=1)}
        return value
    if op == "is_any":
        values = value if isinstance(value, list) else [value]
        return {"$in": values}
    if op == "contains":
        return {"$regex": re.escape(str(value)), "$options": "i"}
    if op == "starts_with":
        return {"$regex": "^" + re.escape(str(value)), "$options": "i"}
    if op == "ends_with":
        return {"$regex": re.escape(str(value)) + "$", "$options": "i"}
    if op in ("less_than", "before"):
        return {"$lt": _coerce_date(value) if is_date else value}
    if op in ("greater_than", "after"):
        return {"$gt": _coerce_date(value) if is_date else value}
    return None


def build_mongo_query(request: ListSimulationRunsRequest) -> dict[str, Any]:
    """Build the Mongo filter for a runs listing request (owner scope + filters)."""
    query: dict[str, Any] = {}
    if request.type == "user" and request.user:
        query["email"] = request.user
    for table_filter in request.filters:
        if not table_filter.id:
            continue
        db_field = _FILTERABLE_FIELDS.get(table_filter.id)
        if db_field is None:
            continue
        clause = _filter_clause(db_field, table_filter.operator, table_filter.value)
        if clause is None:
            continue
        existing = query.get(db_field)
        if isinstance(existing, dict) and isinstance(clause, dict):
            existing.update(clause)
        else:
            query[db_field] = clause
    return query


def resolve_sort(sort: TableSort | None) -> tuple[str, int]:
    """Resolve a TableSort to a (db_field, direction) pair. Defaults to newest first."""
    if sort and sort.id:
        db_field = _FILTERABLE_FIELDS.get(sort.id)
        if db_field is not None:
            return db_field, DESCENDING if sort.direction == "desc" else ASCENDING
    return "submitted", DESCENDING


class SimulationRunDatabaseService(ABC):
    @abstractmethod
    async def insert_simulation_run(self, record: SimulationRunRecord) -> SimulationRunRecord:
        pass

    @abstractmethod
    async def update_simulation_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        biosimulations_run_id: str | None = None,
        runtime: int | None = None,
        results_size: int | None = None,
    ) -> None:
        pass

    @abstractmethod
    async def query_simulation_runs(
        self, request: ListSimulationRunsRequest
    ) -> tuple[list[SimulationRunRecord], int]:
        """Return a page of matching records and the total match count."""
        pass

    @abstractmethod
    async def get_simulation_runs_by_processing_id(self, processing_id: str) -> list[SimulationRunRecord]:
        """Return all SimulationRunRecords for a parent processing_id (workflow id)."""
        pass

    @abstractmethod
    async def delete_simulation_runs_by_processing_id(self, processing_id: str) -> None:
        """Delete all SimulationRunRecords for a parent processing_id (workflow id)."""
        pass

    @abstractmethod
    async def delete_all_simulation_runs(self) -> None:
        pass

    async def ensure_indexes(self) -> None:
        """Create or refresh indexes for the underlying store. Default: no-op."""
        return

    @abstractmethod
    async def close(self) -> None:
        pass


class SimulationRunDatabaseServiceMongo(SimulationRunDatabaseService):
    _db_client: AsyncIOMotorClient
    _runs_col: AsyncIOMotorCollection

    def __init__(self, db_client: AsyncIOMotorClient) -> None:
        self._db_client = db_client
        database = self._db_client.get_database(get_settings().mongodb_database)
        self._runs_col = database.get_collection(get_settings().mongodb_collection_simulation_runs)

    @override
    async def insert_simulation_run(self, record: SimulationRunRecord) -> SimulationRunRecord:
        if record.database_id is not None:
            raise Exception("Cannot insert document that already has a database id")
        logger.info(f"Inserting simulation run record {record.run_id} ({record.simulator})")
        result: InsertOneResult = await self._runs_col.insert_one(record.model_dump())
        if result.acknowledged:
            inserted = record.model_copy(deep=True)
            inserted.database_id = str(result.inserted_id)
            return inserted
        raise Exception("Insert failed")

    @override
    async def update_simulation_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        biosimulations_run_id: str | None = None,
        runtime: int | None = None,
        results_size: int | None = None,
    ) -> None:
        updates: dict[str, Any] = {"updated": _utcnow()}
        if status is not None:
            updates["status"] = status
        if biosimulations_run_id is not None:
            updates["biosimulations_run_id"] = biosimulations_run_id
        if runtime is not None:
            updates["runtime"] = runtime
        if results_size is not None:
            updates["results_size"] = results_size
        logger.info(f"Updating simulation run record {run_id}: {sorted(updates.keys())}")
        await self._runs_col.update_one({"run_id": run_id}, {"$set": updates})

    @override
    async def query_simulation_runs(
        self, request: ListSimulationRunsRequest
    ) -> tuple[list[SimulationRunRecord], int]:
        query = build_mongo_query(request)
        sort_field, direction = resolve_sort(request.sort)
        total: int = await self._runs_col.count_documents(query)

        page = max(request.pagination.page, 1)
        per_page = max(request.pagination.per_page, 1)
        skip = (page - 1) * per_page

        cursor = self._runs_col.find(query).sort(sort_field, direction).skip(skip).limit(per_page)
        documents = await cursor.to_list(length=per_page)

        records: list[SimulationRunRecord] = []
        for doc in documents:
            doc_dict = dict(doc)
            doc_dict["database_id"] = str(doc["_id"])
            del doc_dict["_id"]
            records.append(SimulationRunRecord.model_validate(doc_dict))
        return records, total

    @override
    async def get_simulation_runs_by_processing_id(self, processing_id: str) -> list[SimulationRunRecord]:
        documents = await self._runs_col.find({"processing_id": processing_id}).to_list(length=100)
        records: list[SimulationRunRecord] = []
        for doc in documents:
            doc_dict = dict(doc)
            doc_dict["database_id"] = str(doc["_id"])
            del doc_dict["_id"]
            records.append(SimulationRunRecord.model_validate(doc_dict))
        return records

    @override
    async def delete_simulation_runs_by_processing_id(self, processing_id: str) -> None:
        logger.info(f"Deleting simulation run records for processing_id {processing_id}")
        result = await self._runs_col.delete_many({"processing_id": processing_id})
        if not result.acknowledged:
            raise Exception("Delete failed")

    @override
    async def delete_all_simulation_runs(self) -> None:
        logger.info("Deleting all simulation run records")
        result = await self._runs_col.delete_many({})
        if not result.acknowledged:
            raise Exception("Delete failed")

    @override
    async def ensure_indexes(self) -> None:
        # processing_id: hot read on every GET /simulations/{id} hybrid-read.
        # run_id: hot lookup on every early/terminal update_simulation_run call;
        # also the natural primary key for a SimulationRunRecord.
        await self._runs_col.create_index("processing_id")
        await self._runs_col.create_index("run_id", unique=True)

    @override
    async def close(self) -> None:
        self._db_client.close()
