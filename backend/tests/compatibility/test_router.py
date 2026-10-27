"""Tests for the compatibility router endpoint."""

import asyncio
import contextlib
import socket
import time
from pathlib import Path
from typing import Any, AsyncIterator
from unittest.mock import patch, AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport

from biosim_server.api.main import app
from biosim_server.biosim_omex.omex_storage import MAX_OMEX_BYTES
from biosim_server.biosim_runs import BiosimulatorVersion


def _sample_omex_file() -> Path:
    """Path to the sample OMEX file in fixtures."""
    return Path(__file__).parent.parent / "fixtures" / "local_data" / "BIOMD0000000010_tellurium_Negative_feedback_and_ultrasen.omex"


@pytest.fixture
def sample_omex_path() -> Path:
    """Path to sample OMEX file in fixtures."""
    return _sample_omex_file()


def sample_omex_bytes() -> bytes:
    """Bytes of the sample OMEX archive (for the fake-download tests)."""
    return _sample_omex_file().read_bytes()


@pytest.fixture
def mock_biosim_service() -> AsyncMock:
    """Mock biosim service with simulator versions."""
    service = AsyncMock()
    service.get_simulator_versions.return_value = [
        BiosimulatorVersion(
            id="tellurium",
            name="tellurium",
            version="2.2.10",
            image_url="ghcr.io/biosimulators/tellurium:2.2.10",
            image_digest="sha256:0c22827b4682273810d48ea606ef50c7163e5f5289740951c00c64c669409eae",
            created="2024-10-10T22:00:50.110Z",
            updated="2024-10-10T22:00:50.110Z"
        ),
    ]
    return service


def test_check_compatibility_no_input() -> None:
    """Test that endpoint requires either file or URL."""
    client = TestClient(app)
    response = client.post("/compatibility/check")
    # Should get 400 because neither uploaded_file nor archive_url provided
    assert response.status_code == 400
    assert "Provide either" in response.json()["detail"]


def test_check_compatibility_invalid_file() -> None:
    """Test with an invalid file."""
    client = TestClient(app)
    response = client.post(
        "/compatibility/check",
        files={"uploaded_file": ("test.omex", b"not a valid zip", "application/octet-stream")}
    )
    assert response.status_code == 400
    assert "Failed to parse OMEX" in response.json()["detail"]


@patch("biosim_server.compatibility.router.get_biosim_service")
def test_check_compatibility_service_unavailable(
    mock_get_service: AsyncMock
) -> None:
    """Test when biosim service is unavailable."""
    mock_get_service.return_value = None

    client = TestClient(app)
    # Create a minimal valid OMEX (just a zip with manifest)
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        manifest = '''<?xml version="1.0" encoding="UTF-8"?>
<omexManifest xmlns="http://identifiers.org/combine.specifications/omex-manifest">
</omexManifest>'''
        zf.writestr("manifest.xml", manifest)
    buf.seek(0)

    response = client.post(
        "/compatibility/check",
        files={"uploaded_file": ("test.omex", buf.getvalue(), "application/octet-stream")}
    )
    # Should fail because no SED-ML files
    assert response.status_code == 400
    assert "No SED-ML files" in response.json()["detail"]


@patch("biosim_server.compatibility.router.get_biosim_service")
@patch("biosim_server.compatibility.simulator_matcher._get_simulator_spec")
def test_check_compatibility_success(
    mock_get_spec: AsyncMock,
    mock_get_service: AsyncMock,
    sample_omex_path: Path,
    mock_biosim_service: AsyncMock
) -> None:
    """Test successful compatibility check."""
    mock_get_service.return_value = mock_biosim_service

    # Mock the simulator spec response
    mock_get_spec.return_value = {
        "id": "tellurium",
        "name": "tellurium",
        "version": "2.2.10",
        "algorithms": [
            {
                "kisaoId": {"id": "KISAO_0000019"},
                "modelFormats": [{"id": "format_2585"}],  # SBML
                "simulationTypes": [{"id": "SedUniformTimeCourseSimulation"}]
            }
        ]
    }

    client = TestClient(app)
    with open(sample_omex_path, "rb") as f:
        response = client.post(
            "/compatibility/check",
            files={"uploaded_file": ("test.omex", f, "application/octet-stream")}
        )

    assert response.status_code == 200
    data = response.json()

    # Check response structure
    assert "omex_id" in data
    assert "omex_content" in data
    assert "eligible_simulators" in data

    # Check omex_id is an MD5 hex string
    assert len(data["omex_id"]) == 32

    # Check OMEX content was parsed
    assert len(data["omex_content"]["sedml_files"]) >= 1
    assert len(data["omex_content"]["simulations"]) >= 1

    # Check simulations have algorithm with id and name
    for sim in data["omex_content"]["simulations"]:
        assert "algorithm" in sim
        assert "id" in sim["algorithm"]
        assert "name" in sim["algorithm"]

    # Check at least tellurium is compatible (it supports CVODE)
    simulator_ids = [s["id"] for s in data["eligible_simulators"]]
    assert "tellurium" in simulator_ids

    # Check simulator has correct shape
    tellurium = next(s for s in data["eligible_simulators"] if s["id"] == "tellurium")
    assert tellurium["exact"] is True
    assert isinstance(tellurium["versions"], list)
    assert "2.2.10" in tellurium["versions"]
    # version_details not populated in default (non-verbose) mode
    assert tellurium["version_details"] is None


@patch("biosim_server.compatibility.router.get_biosim_service")
@patch("biosim_server.compatibility.simulator_matcher._get_simulator_spec")
def test_check_compatibility_dot_slash_manifest_locations(
    mock_get_spec: AsyncMock,
    mock_get_service: AsyncMock,
    mock_biosim_service: AsyncMock
) -> None:
    """An archive whose manifest uses "./" locations must not 400.

    Regression test for BioModels-derived archives (e.g. run
    61fea4893c41b662ca49b3ca), which declare "./x.sedml" in the manifest while
    storing the zip entry as "x.sedml". These previously returned 400
    "No simulations found in the SED-ML files".
    """
    mock_get_service.return_value = mock_biosim_service
    mock_get_spec.return_value = {
        "id": "tellurium",
        "name": "tellurium",
        "version": "2.2.10",
        "algorithms": [
            {
                "kisaoId": {"id": "KISAO_0000496"},
                "modelFormats": [{"id": "format_2585"}],  # SBML
                "simulationTypes": [{"id": "SedUniformTimeCourseSimulation"}]
            }
        ]
    }

    import io
    import zipfile

    sedml = """<?xml version="1.0" encoding="UTF-8"?>
<sedML xmlns="http://sed-ml.org/sed-ml/level1/version3" level="1" version="3">
    <listOfModels>
        <model id="model1" language="urn:sedml:language:sbml" source="Szymanska2009.xml"/>
    </listOfModels>
    <listOfSimulations>
        <uniformTimeCourse id="sim1" initialTime="0" outputStartTime="0" outputEndTime="1000" numberOfPoints="4000">
            <algorithm kisaoID="KISAO:0000496"/>
        </uniformTimeCourse>
    </listOfSimulations>
</sedML>"""

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr("manifest.xml", '''<?xml version="1.0" encoding="UTF-8"?>
<omexManifest xmlns="http://identifiers.org/combine.specifications/omex-manifest">
  <content location="./Szymanska2009.xml" format="http://identifiers.org/combine.specifications/sbml" master="false"/>
  <content location="./BIOMD0000000896_sim.sedml" format="http://identifiers.org/combine.specifications/sed-ml" master="true"/>
  <content location="." format="http://identifiers.org/combine.specifications/omex"/>
</omexManifest>''')
        zf.writestr("Szymanska2009.xml", "<sbml/>")
        zf.writestr("BIOMD0000000896_sim.sedml", sedml)

    client = TestClient(app)
    response = client.post(
        "/compatibility/check",
        files={"uploaded_file": ("test.omex", buf.getvalue(), "application/octet-stream")}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["omex_content"]["sedml_files"] == ["BIOMD0000000896_sim.sedml"]
    assert data["omex_content"]["parse_errors"] == []
    assert [s["algorithm"]["id"] for s in data["omex_content"]["simulations"]] == ["KISAO:0000496"]
    assert "tellurium" in [s["id"] for s in data["eligible_simulators"]]


def test_check_compatibility_reports_unreadable_sedml() -> None:
    """A declared-but-absent SED-ML file yields a specific error, not "no simulations"."""
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr("manifest.xml", '''<?xml version="1.0" encoding="UTF-8"?>
<omexManifest xmlns="http://identifiers.org/combine.specifications/omex-manifest">
  <content location="absent.sedml" format="http://identifiers.org/combine.specifications/sed-ml" master="true"/>
</omexManifest>''')

    client = TestClient(app)
    response = client.post(
        "/compatibility/check",
        files={"uploaded_file": ("test.omex", buf.getvalue(), "application/octet-stream")}
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "absent.sedml" in detail
    assert "No simulations could be read" in detail


def test_check_compatibility_rejects_loopback_archive_url() -> None:
    client = TestClient(app)
    response = client.post("/compatibility/check", params={"archive_url": "http://127.0.0.1/secret.omex"})
    assert response.status_code == 400
    assert "private or reserved" in response.json()["detail"]


def test_check_compatibility_rejects_localhost_archive_url() -> None:
    client = TestClient(app)
    response = client.post("/compatibility/check", params={"archive_url": "http://localhost/secret.omex"})
    assert response.status_code == 400
    assert "private or reserved" in response.json()["detail"]


def test_check_compatibility_rejects_metadata_archive_url() -> None:
    client = TestClient(app)
    response = client.post(
        "/compatibility/check", params={"archive_url": "http://169.254.169.254/latest/meta-data"}
    )
    assert response.status_code == 400
    assert "private or reserved" in response.json()["detail"]


def test_check_compatibility_rejects_non_http_archive_url() -> None:
    client = TestClient(app)
    response = client.post("/compatibility/check", params={"archive_url": "file:///etc/passwd"})
    assert response.status_code == 400
    assert "http or https" in response.json()["detail"]


def test_check_compatibility_rejects_userinfo_archive_url() -> None:
    client = TestClient(app)
    response = client.post(
        "/compatibility/check", params={"archive_url": "https://user:pass@example.com/a.omex"}
    )
    assert response.status_code == 400
    assert "userinfo" in response.json()["detail"]


@patch(
    "biosim_server.compatibility.router.socket.getaddrinfo",
    return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", 80))],
)
def test_check_compatibility_rejects_resolved_private_host(mock_getaddrinfo: object) -> None:
    client = TestClient(app)
    response = client.post(
        "/compatibility/check", params={"archive_url": "http://internal.example/a.omex"}
    )
    assert response.status_code == 400
    assert "private or reserved" in response.json()["detail"]


# --- audit P1 items 6 & 7: non-blocking DNS, capped archive/upload reads --- #

_PUBLIC_ADDR: list[tuple[int, int, int, str, tuple[str, int]]] = [
    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))
]


class _FakeContent:
    """Minimal stand-in for the ``aiohttp`` response body stream."""

    def __init__(self, payload: bytes, *, chunk_size: int = 8) -> None:
        self._payload = payload
        self._chunk_size = chunk_size

    async def iter_chunked(self, size: int) -> AsyncIterator[bytes]:
        del size  # the fake honours its own chunking
        for start in range(0, len(self._payload), self._chunk_size):
            yield self._payload[start : start + self._chunk_size]


class _FakeResponse:
    def __init__(self, payload: bytes, *, content_length: str | None = None, chunk_size: int = 8) -> None:
        self.status = 200
        self.headers = {} if content_length is None else {"Content-Length": content_length}
        self.content = _FakeContent(payload, chunk_size=chunk_size)

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakeSession:
    """``aiohttp.ClientSession`` stand-in that yields one canned response."""

    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def get(self, *args: object, **kwargs: object) -> _FakeResponse:
        return self._response


def _fake_download(response: _FakeResponse) -> Any:
    return patch(
        "biosim_server.compatibility.router.aiohttp.ClientSession",
        return_value=_FakeSession(response),
    )


@patch("biosim_server.compatibility.router.socket.getaddrinfo", return_value=_PUBLIC_ADDR)
def test_check_compatibility_rejects_oversized_declared_content_length(mock_dns: object) -> None:
    """A declared Content-Length over the cap is rejected with 413, never buffered."""
    client = TestClient(app)
    with _fake_download(_FakeResponse(b"", content_length=str(MAX_OMEX_BYTES + 1))):
        response = client.post(
            "/compatibility/check", params={"archive_url": "https://example.com/big.omex"}
        )
    assert response.status_code == 413
    assert "MiB limit" in response.json()["detail"]


@patch("biosim_server.compatibility.router.socket.getaddrinfo", return_value=_PUBLIC_ADDR)
def test_check_compatibility_rejects_oversized_stream(mock_dns: object) -> None:
    """A body over the cap is caught mid-stream when Content-Length is absent."""
    client = TestClient(app)
    response_under_test = _FakeResponse(b"\0" * 4096, chunk_size=64)
    with (
        _fake_download(response_under_test),
        patch("biosim_server.compatibility.router.MAX_OMEX_BYTES", 128),
    ):
        response = client.post(
            "/compatibility/check", params={"archive_url": "https://example.com/lying.omex"}
        )
    assert response.status_code == 413
    assert "MiB limit" in response.json()["detail"]


def test_check_compatibility_accepts_archive_under_the_cap() -> None:
    """Control: a small streamed body still gets past the cap and is parsed."""
    client = TestClient(app)
    payload = sample_omex_bytes()
    response_under_test = _FakeResponse(payload, content_length=str(len(payload)), chunk_size=1024)
    with (
        _fake_download(response_under_test),
        patch("biosim_server.compatibility.router.socket.getaddrinfo", return_value=_PUBLIC_ADDR),
        patch("biosim_server.compatibility.router.get_biosim_service", return_value=None),
    ):
        response = client.post(
            "/compatibility/check", params={"archive_url": "https://example.com/small.omex"}
        )
    # Parsed fine; it fails later at the (deliberately absent) biosim service.
    assert response.status_code == 503
    assert "Failed to parse OMEX" not in response.json()["detail"]


def test_check_compatibility_rejects_oversized_upload() -> None:
    """An upload over the cap is rejected with 413 rather than buffered whole."""
    client = TestClient(app)
    with patch("biosim_server.biosim_omex.omex_storage.MAX_OMEX_BYTES", 4096):
        response = client.post(
            "/compatibility/check",
            files={"uploaded_file": ("big.omex", b"\0" * (64 * 1024), "application/octet-stream")},
        )
    assert response.status_code == 413
    assert "limit" in response.json()["detail"]


@pytest.mark.asyncio
async def test_archive_dns_resolution_does_not_block_event_loop() -> None:
    """A slow resolver must not stall the event loop (P1 item 6).

    ``getaddrinfo`` is a blocking syscall: run inline in the handler it freezes
    every other request for its duration. The ticker below must keep ticking
    while the lookup is in flight.
    """
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    def slow_getaddrinfo(*args: object, **kwargs: object) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        del args, kwargs
        time.sleep(0.5)
        return _PUBLIC_ADDR

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        with (
            patch("biosim_server.compatibility.router.socket.getaddrinfo", slow_getaddrinfo),
            _fake_download(_FakeResponse(b"not-a-zip")),
        ):
            ticker_task = asyncio.create_task(ticker())
            request = asyncio.create_task(
                http.post("/compatibility/check", params={"archive_url": "https://slow.example/a.omex"})
            )
            # Sample the loop while the (blocking) DNS lookup is running.
            await asyncio.sleep(0.2)
            ticks_during_dns = ticks
            await request
            ticker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ticker_task

    # A blocking lookup would show ~0 ticks here.
    assert ticks_during_dns >= 5, f"event loop stalled during DNS resolution (ticks={ticks_during_dns})"
