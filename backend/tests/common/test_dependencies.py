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


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_error", [RuntimeError("offline"), ValueError("unexpected connector failure")])
async def test_temporal_reconnects_and_shutdown_cancels_retry(monkeypatch: pytest.MonkeyPatch, initial_error: Exception) -> None:
    import asyncio
    attempts = AsyncMock(side_effect=[initial_error, None])
    client = AsyncMock()

    async def connect() -> None:
        await attempts()
        dependencies.set_temporal_client(client)

    monkeypatch.setattr(dependencies, '_connect_temporal', connect)
    await dependencies.start_temporal_connection(retry_seconds=.01)
    assert dependencies.get_temporal_client() is None
    for _ in range(30):
        if dependencies.get_temporal_client() is client:
            break
        await asyncio.sleep(.01)
    assert dependencies.get_temporal_client() is client
    assert attempts.await_count == 2
    await dependencies.stop_temporal_connection()
    failures = AsyncMock(side_effect=RuntimeError('offline'))
    monkeypatch.setattr(dependencies, '_connect_temporal', failures)
    await dependencies.start_temporal_connection(retry_seconds=60)
    task = dependencies._temporal_reconnect_task
    assert task is not None
    await dependencies.stop_temporal_connection()
    assert task.cancelled() and dependencies._temporal_reconnect_task is None
    dependencies.set_temporal_client(None)
