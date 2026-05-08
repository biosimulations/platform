from pathlib import Path
from typing import AsyncGenerator

import pytest_asyncio
from gcloud.aio.auth import Token
from testcontainers.mongodb import MongoDbContainer  # type: ignore

from biosim_server.common.storage import FileServiceGCS
from biosim_server.common.storage import create_token
from biosim_server.dependencies import get_file_service, set_file_service
from tests.fixtures.file_service_local import FileServiceLocal


@pytest_asyncio.fixture(scope="function")
async def file_service_local() -> AsyncGenerator[FileServiceLocal, None]:
    file_service_local = FileServiceLocal()
    file_service_local.init()
    saved_file_service = get_file_service()
    set_file_service(file_service_local)

    yield file_service_local

    await file_service_local.close()
    set_file_service(saved_file_service)


@pytest_asyncio.fixture(scope="function")
async def file_service_gcs() -> AsyncGenerator[FileServiceGCS, None]:
    file_service_gcs: FileServiceGCS = FileServiceGCS()
    saved_file_service = get_file_service()
    set_file_service(file_service_gcs)

    yield file_service_gcs

    await file_service_gcs.close()
    set_file_service(saved_file_service)


@pytest_asyncio.fixture(scope="function")
async def file_service_gcs_test_base_path() -> Path:
    return Path("verify_test")


@pytest_asyncio.fixture(scope="module")
async def gcs_token() -> AsyncGenerator[Token, None]:
    token: Token = create_token()

    yield token

    await token.close()
