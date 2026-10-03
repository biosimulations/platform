"""Standalone lifecycle: every global service handle must be cleared on shutdown.

A handle left set after shutdown points at a closed Motor client, so a restart
or the next test in the same process picks up a dead service.
"""

from unittest.mock import AsyncMock

import pytest

from biosim_server import dependencies
from biosim_server.dependencies import shutdown_standalone


def _install_stub_services() -> tuple[AsyncMock, AsyncMock]:
    """Populate every global handle with a mock service; return the two owned ones."""
    db_service = AsyncMock()
    file_service = AsyncMock()
    dependencies.set_database_service(db_service)
    dependencies.set_omex_database_service(AsyncMock())
    dependencies.set_simulation_run_database_service(AsyncMock())
    dependencies.set_project_database_service(AsyncMock())
    dependencies.set_verification_database_service(AsyncMock())
    dependencies.set_file_service(file_service)
    dependencies.set_biosim_service(AsyncMock())
    dependencies.set_temporal_client(AsyncMock())
    dependencies.set_mongo_client(AsyncMock())
    dependencies.set_http_client(AsyncMock())
    return db_service, file_service


@pytest.mark.asyncio
async def test_shutdown_standalone_clears_every_service_handle() -> None:
    _install_stub_services()

    await shutdown_standalone()

    # The OMEX handle used to be the one omission: get_omex_database_service()
    # kept returning a service bound to the Motor client closed below.
    assert dependencies.get_omex_database_service() is None
    assert dependencies.get_database_service() is None
    assert dependencies.get_simulation_run_database_service() is None
    assert dependencies.get_project_database_service() is None
    assert dependencies.get_verification_database_service() is None
    assert dependencies.get_file_service() is None
    assert dependencies.get_biosim_service() is None
    assert dependencies.get_temporal_client() is None
    assert dependencies.get_mongo_client() is None
    # get_http_client() lazily re-creates a client, so check the global itself.
    assert dependencies.global_http_client is None


@pytest.mark.asyncio
async def test_shutdown_standalone_closes_shared_services() -> None:
    """The db/file services that own the shared client are closed, not just unset."""
    db_service, file_service = _install_stub_services()

    await shutdown_standalone()

    db_service.close.assert_awaited_once()
    file_service.close.assert_awaited_once()
