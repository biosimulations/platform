"""SSRF guard for the caller-supplied archive_url on POST /compatibility/check.

That endpoint is unauthenticated and makes the server fetch a URL the caller
chooses, so without these checks it is a request-forgery proxy into the cluster
network and the cloud metadata service.
"""

import socket
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from biosim_server.api.main import app
from biosim_server.compatibility.url_guard import BlockedUrlError, assert_fetchable_url


def _resolves_to(*addresses: str) -> Any:
    """Stub getaddrinfo, so these tests never depend on real DNS."""

    async def _getaddrinfo(host: str, port: int, **kwargs: Any) -> list[Any]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port)) for address in addresses]

    return _getaddrinfo


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://example.com/",
        "ftp://example.com/archive.omex",
        "data:text/plain;base64,aGk=",
    ],
)
async def test_non_http_schemes_are_blocked(url: str) -> None:
    with pytest.raises(BlockedUrlError):
        await assert_fetchable_url(url)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",        # loopback
        "10.1.2.3",         # RFC1918
        "172.16.0.9",       # RFC1918
        "192.168.1.10",     # RFC1918
        "169.254.169.254",  # cloud metadata
        "0.0.0.0",          # unspecified
        "100.64.0.1",       # carrier-grade NAT / shared address space
    ],
)
async def test_internal_destinations_are_blocked(
    address: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = MagicMock()
    loop.getaddrinfo = _resolves_to(address)
    monkeypatch.setattr("asyncio.get_running_loop", lambda: loop)
    with pytest.raises(BlockedUrlError):
        await assert_fetchable_url("http://totally-legit.example.com/archive.omex")


@pytest.mark.asyncio
async def test_ipv4_mapped_ipv6_loopback_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """::ffff:127.0.0.1 is loopback in an IPv6 costume."""
    loop = MagicMock()
    loop.getaddrinfo = _resolves_to("::ffff:127.0.0.1")
    monkeypatch.setattr("asyncio.get_running_loop", lambda: loop)
    with pytest.raises(BlockedUrlError):
        await assert_fetchable_url("http://sneaky.example.com/archive.omex")


@pytest.mark.asyncio
async def test_a_host_with_any_internal_answer_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Split-horizon DNS must not be partially allowed."""
    loop = MagicMock()
    loop.getaddrinfo = _resolves_to("93.184.216.34", "10.0.0.5")
    monkeypatch.setattr("asyncio.get_running_loop", lambda: loop)
    with pytest.raises(BlockedUrlError):
        await assert_fetchable_url("http://mixed.example.com/archive.omex")


@pytest.mark.asyncio
async def test_public_destination_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    loop = MagicMock()
    loop.getaddrinfo = _resolves_to("93.184.216.34")
    monkeypatch.setattr("asyncio.get_running_loop", lambda: loop)
    await assert_fetchable_url("https://example.com/archive.omex")


@pytest.mark.asyncio
async def test_unresolvable_host_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(host: str, port: int, **kwargs: Any) -> list[Any]:
        raise socket.gaierror("no such host")

    loop = MagicMock()
    loop.getaddrinfo = _boom
    monkeypatch.setattr("asyncio.get_running_loop", lambda: loop)
    with pytest.raises(BlockedUrlError):
        await assert_fetchable_url("http://nope.invalid/archive.omex")


def test_check_compatibility_rejects_an_internal_archive_url() -> None:
    """End to end: the route refuses rather than fetching, and says so with a 400."""
    with patch("biosim_server.compatibility.router.aiohttp.ClientSession") as session_cls:
        response = TestClient(app).post(
            "/compatibility/check?archive_url=http://169.254.169.254/latest/meta-data/"
        )
    assert response.status_code == 400
    assert "Refusing to fetch" in response.json()["detail"]
    session_cls.assert_not_called()


def test_check_compatibility_rejects_a_file_url() -> None:
    with patch("biosim_server.compatibility.router.aiohttp.ClientSession") as session_cls:
        response = TestClient(app).post("/compatibility/check?archive_url=file:///etc/passwd")
    assert response.status_code == 400
    session_cls.assert_not_called()


def test_check_compatibility_invalid_token_is_401_not_anonymous() -> None:
    """An invalid bearer must not quietly ingest a *public* OMEX cache entry."""
    with patch("biosim_server.compatibility.router.get_omex_database_service") as omex_db:
        omex_db.return_value = AsyncMock()
        response = TestClient(app).post(
            "/compatibility/check?archive_url=https://example.com/a.omex",
            headers={"Authorization": "Bearer not-a-jwt"},
        )
    assert response.status_code == 401
    omex_db.return_value.upsert_omex_file.assert_not_called()
