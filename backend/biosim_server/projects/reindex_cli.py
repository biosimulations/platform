"""One-shot reindex of the platform project search collection.

Run in-cluster (e.g. the weekly CronJob, or ``kubectl exec ... python -m
biosim_server.projects.reindex_cli`` for an ad-hoc admin reindex). Connects to
Mongo directly and rebuilds ``PlatformProjectSearch`` from Projects + Metadata +
Specifications — no HTTP endpoint or token involved.
"""

import asyncio
import logging

from motor.motor_asyncio import AsyncIOMotorClient

from biosim_server.config import get_settings
from biosim_server.log_config import setup_logging
from biosim_server.projects.search import ProjectSearchServiceMongo

logger = logging.getLogger(__name__)


async def _reindex() -> int:
    client = AsyncIOMotorClient(get_settings().mongodb_uri)
    service = ProjectSearchServiceMongo(db_client=client)
    try:
        count = await service.rebuild_index()
    finally:
        await service.close()
    return count


def main() -> None:
    setup_logging(logger)
    count = asyncio.run(_reindex())
    logger.info(f"project search reindex complete: {count} documents")


if __name__ == "__main__":
    main()
