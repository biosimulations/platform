"""Unhandled-exception 500s carry CORS headers.

Starlette renders an unhandled exception in ServerErrorMiddleware, outside
CORSMiddleware, so the browser saw a CORS error and never the 500 behind it.
"""

from collections.abc import Iterator
from unittest.mock import patch

import httpx
import pytest
from starlette.types import Message, Receive, Scope, Send

from biosim_server.api.main import app
from biosim_server.common.server_errors import ServerErrorCorsMiddleware
from tests.api.test_main import _make_verify_output, _temporal_with_describe

pytestmark = pytest.mark.asyncio

ORIGIN = "http://localhost:3000"


@pytest.fixture
def failing_get_verify() -> Iterator[None]:
    """GET /verify raises an unhandled RuntimeError after the Temporal query succeeds."""
    temporal = _temporal_with_describe(query_result=_make_verify_output(owner_sub=None))
    with patch("biosim_server.api.main.get_temporal_client", return_value=temporal), \
         patch("biosim_server.api.main._reconcile_terminal_status", side_effect=RuntimeError("private failure")):
        yield


@pytest.mark.usefixtures("failing_get_verify")
async def test_unhandled_500_carries_cors_headers() -> None:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/verify/wf", headers={"origin": ORIGIN})

    assert response.status_code == 500
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert response.json() == {"detail": "Internal Server Error"}
    assert "private failure" not in response.text


@pytest.mark.usefixtures("failing_get_verify")
async def test_unhandled_exception_is_still_raised_to_the_server() -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        with pytest.raises(RuntimeError, match="private failure"):
            await client.get("/verify/wf", headers={"origin": ORIGIN})


async def test_no_second_response_once_headers_are_sent() -> None:
    async def fails_mid_body(_scope: Scope, _receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        raise RuntimeError("mid-body")

    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(message)

    with pytest.raises(RuntimeError, match="mid-body"):
        await ServerErrorCorsMiddleware(fails_mid_body)({"type": "http"}, receive, send)

    assert [m["type"] for m in sent] == ["http.response.start"]
    assert sent[0]["status"] == 200
