"""Integration test for POST /compatibility/check against the local stack.

Uses testcontainers MongoDB + FileServiceLocal + BiosimServiceMock — no
external network calls, no creds required. The mock provides a realistic
list of simulator versions so the endpoint's compatibility-matching logic
exercises real code, not stubs.
"""

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from biosim_server.api.main import app
from biosim_server.biosim_omex import OmexDatabaseServiceMongo
from biosim_server.common.storage import FileServiceLocal
from tests.fixtures.biosim_service_mock import BiosimServiceMock


@pytest.mark.integration_local
@pytest.mark.asyncio
async def test_compatibility_check(
    omex_test_file: Path,
    omex_database_service_mongo: OmexDatabaseServiceMongo,
    file_service_local: FileServiceLocal,
    biosim_service_mock: BiosimServiceMock,
) -> None:
    """POST a real OMEX file through /compatibility/check; assert the
    response shape and that the file gets cached in Mongo + local storage."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
        with open(omex_test_file, "rb") as f:
            response = await test_client.post(
                "/compatibility/check",
                files={"uploaded_file": (omex_test_file.name, f, "application/zip")},
            )

    assert response.status_code == 200, response.text
    body = response.json()

    # Response shape: CompatibilityResponse
    assert "omex_id" in body
    assert isinstance(body["omex_id"], str) and len(body["omex_id"]) == 32  # md5 hash
    assert "omex_content" in body
    assert "eligible_simulators" in body

    omex_content = body["omex_content"]
    assert isinstance(omex_content["sedml_files"], list) and len(omex_content["sedml_files"]) > 0
    assert isinstance(omex_content["simulations"], list)
    assert isinstance(omex_content["model_formats"], list)

    # The test OMEX is a Tellurium SBML model — Tellurium should match.
    eligible = body["eligible_simulators"]
    assert isinstance(eligible, list)
    eligible_ids = {sim["id"] for sim in eligible}
    assert "tellurium" in eligible_ids, f"expected tellurium in eligible, got {eligible_ids}"

    # The OMEX file should now be cached in the Mongo + local storage.
    cached = await omex_database_service_mongo.get_omex_file(file_hash_md5=body["omex_id"])
    assert cached is not None
    assert cached.file_hash_md5 == body["omex_id"]
