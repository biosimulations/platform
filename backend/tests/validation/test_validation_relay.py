"""Contract for the COMBINE validation relay: what we send, what we return."""

from collections.abc import AsyncIterator, Callable

import httpx
import pytest

from biosim_server.api.main import app
from biosim_server.config import get_settings
from biosim_server.dependencies import get_combine_http_client

pytestmark = pytest.mark.asyncio

Handler = Callable[[httpx.Request], httpx.Response]

MULTIPART = (
    b"--b0undary\r\n"
    b'Content-Disposition: form-data; name="language"\r\n\r\n'
    b"SBML\r\n"
    b"--b0undary\r\n"
    b'Content-Disposition: form-data; name="file"; filename="m.xml"\r\n'
    b"Content-Type: application/xml\r\n\r\n"
    b"<sbml/>\r\n"
    b"--b0undary--\r\n"
)
CONTENT_TYPE = "multipart/form-data; boundary=b0undary"
ROUTES = [("/validation/model", "/model/validate"), ("/validation/sed-ml", "/sed-ml/validate")]


async def relay(handler: Handler) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://combine.test"
    ) as upstream:
        app.dependency_overrides[get_combine_http_client] = lambda: upstream
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://platform.test"
        ) as caller:
            yield caller


@pytest.mark.parametrize(("route", "upstream_path"), ROUTES)
async def test_body_and_content_type_reach_the_right_upstream_path(
    route: str, upstream_path: str
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"status": "valid"})

    async for caller in relay(handler):
        response = await caller.post(
            route, content=MULTIPART, headers={"Content-Type": CONTENT_TYPE}
        )

    assert response.status_code == 200
    assert response.json() == {"status": "valid"}
    assert len(seen) == 1
    # The boundary lives in Content-Type, so a rewritten header would make the
    # body unparseable upstream even though the bytes survived.
    assert seen[0].url.path == upstream_path
    assert seen[0].content == MULTIPART
    assert seen[0].headers["content-type"] == CONTENT_TYPE


async def test_caller_credentials_are_never_forwarded() -> None:
    """COMBINE is a third-party validator with no notion of a Platform principal.

    A bearer token is sent here deliberately: it must reach the handler (so the
    relay has something to strip) rather than being rejected up front.
    """
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    async for caller in relay(handler):
        response = await caller.post(
            "/validation/model",
            content=MULTIPART,
            headers={
                "Content-Type": CONTENT_TYPE,
                "Authorization": "Bearer platform-token",
                "Cookie": "session=secret",
                "X-Caller": "secret",
            },
        )

    assert response.status_code == 200
    assert len(seen) == 1
    forwarded = seen[0].headers
    assert "authorization" not in forwarded
    assert "cookie" not in forwarded
    assert "x-caller" not in forwarded
    # The body still has to arrive intact with the credentials stripped.
    assert seen[0].content == MULTIPART


@pytest.mark.parametrize(
    "authorization",
    ["Bearer not-a-real-token", "Bearer ", "Basic dXNlcjpwYXNz", "garbage"],
)
async def test_these_endpoints_work_with_or_without_authentication(
    authorization: str,
) -> None:
    """Validation needs no identity, so no token state may turn into a failure.

    ``get_optional_user`` would 401 every one of these. These routes resolve
    with ``get_advisory_user`` instead, because the frontend attaches the
    Platform token to every Platform-bound request -- so a lapsed session would
    otherwise break a public utility page for an action that never needed a
    login. A token that cannot be validated is simply treated as no token.
    """
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"status": "valid"})

    async for caller in relay(handler):
        response = await caller.post(
            "/validation/model",
            content=MULTIPART,
            headers={"Content-Type": CONTENT_TYPE, "Authorization": authorization},
        )

    assert response.status_code == 200
    assert response.json() == {"status": "valid"}
    assert len(seen) == 1
    assert "authorization" not in seen[0].headers


async def test_no_authorization_header_at_all_also_works() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "valid"})

    async for caller in relay(handler):
        response = await caller.post(
            "/validation/model", content=MULTIPART, headers={"Content-Type": CONTENT_TYPE}
        )

    assert response.status_code == 200


async def test_upstream_4xx_body_is_preserved() -> None:
    """The validation report *is* the 400 body; the frontend reads title/detail."""
    report = {"title": "Invalid model", "detail": "line 3: unknown element"}

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json=report)

    async for caller in relay(handler):
        response = await caller.post(
            "/validation/model", content=MULTIPART, headers={"Content-Type": CONTENT_TYPE}
        )

    assert response.status_code == 400
    assert response.json() == report


async def test_upstream_5xx_becomes_sanitized_502() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="combine stack trace with internals")

    async for caller in relay(handler):
        response = await caller.post(
            "/validation/model", content=MULTIPART, headers={"Content-Type": CONTENT_TYPE}
        )

    assert response.status_code == 502
    assert "combine" not in response.text.lower()
    assert "stack" not in response.text.lower()


@pytest.mark.parametrize(
    ("error", "expected"),
    [(httpx.ReadTimeout("slow"), 504), (httpx.ConnectError("refused"), 502)],
)
async def test_transport_failures_are_mapped(error: Exception, expected: int) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise error

    async for caller in relay(handler):
        response = await caller.post(
            "/validation/model", content=MULTIPART, headers={"Content-Type": CONTENT_TYPE}
        )

    assert response.status_code == expected


async def test_set_cookie_is_not_relayed_back() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={},
            headers={"Set-Cookie": "upstream=session", "X-Internal": "leak"},
        )

    async for caller in relay(handler):
        response = await caller.post(
            "/validation/model", content=MULTIPART, headers={"Content-Type": CONTENT_TYPE}
        )

    assert "set-cookie" not in response.headers
    assert "x-internal" not in response.headers
    assert response.headers["content-type"].startswith("application/json")


async def test_declared_oversize_is_refused_without_an_upstream_call() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    limit = get_settings().combine_max_request_bytes
    async for caller in relay(handler):
        response = await caller.post(
            "/validation/model",
            content=b"x",
            headers={"Content-Type": CONTENT_TYPE, "Content-Length": str(limit + 1)},
        )

    assert response.status_code == 413
    assert seen == []


async def test_understated_content_length_cannot_bypass_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The running count, not the declared length, is what refuses the body."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    settings = get_settings()
    monkeypatch.setattr(settings, "combine_max_request_bytes", 16, raising=False)
    async for caller in relay(handler):
        response = await caller.post(
            "/validation/model",
            content=b"y" * 1024,
            headers={"Content-Type": CONTENT_TYPE, "Content-Length": "4"},
        )

    assert response.status_code == 413
    assert seen == []


async def test_oversized_upstream_report_becomes_502(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"z" * 4096)

    settings = get_settings()
    monkeypatch.setattr(settings, "combine_max_response_bytes", 64, raising=False)
    async for caller in relay(handler):
        response = await caller.post(
            "/validation/model", content=MULTIPART, headers={"Content-Type": CONTENT_TYPE}
        )

    assert response.status_code == 502
    assert b"zzzz" not in response.content


async def test_relay_is_rate_limited() -> None:
    """An unmetered relay would spend the Platform's upstream reputation.

    Bucket isolation comes from the autouse reset in tests/conftest.py.
    """
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    settings = get_settings().ratelimit
    if not settings.enabled:
        pytest.skip("rate limiting disabled in this configuration")
    budget = settings.anonymous_per_window

    statuses = []
    async for caller in relay(handler):
        for _ in range(budget + 1):
            r = await caller.post(
                "/validation/model", content=MULTIPART, headers={"Content-Type": CONTENT_TYPE}
            )
            statuses.append(r.status_code)

    assert all(s == 200 for s in statuses[:budget])
    assert statuses[-1] == 429
    assert "retry-after" in {k.lower() for k in r.headers}


async def test_routes_are_published_in_openapi() -> None:
    paths = app.openapi()["paths"]
    assert "/validation/model" in paths
    assert "/validation/sed-ml" in paths
