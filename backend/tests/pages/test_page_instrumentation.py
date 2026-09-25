"""Page assembly and upstream fetches emit bounded, structured phase records.

SHARED-MIN-001: before this, `pages/service.py` and `common/upstream.py` logged
failures only, with no phase durations and no decoded byte counts, so an
operator could see that a page failed but not where the time or the bytes went
(and `scripts/bench_pages.py` reports end-to-end latency only).

These tests assert the *emitted* records rather than the `LogRecord` attributes,
because `log_config.JsonFormatter` keeps an explicit allowlist -- a field that is
not on it never reaches an operator. They also assert the bound itself: the
records carry a page name, an outcome from a fixed vocabulary, an HTTP status,
millisecond durations and a byte count, and never a resource id, an upstream URL,
or payload content.
"""

import asyncio
import json
import logging
from io import StringIO
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import HTTPException

from scripts.bench_pages import _read_phase_samples

from biosim_server.api.main import app
from biosim_server.common.upstream import fetch_upstream_json_value
from biosim_server.config import get_settings
from biosim_server.dependencies import get_http_client
from biosim_server.log_config import JsonFormatter
from biosim_server.pages import service
from tests.pages.test_mapping import satellite
from tests.pages.test_run_page import hang_until_cancelled
from tests.summaries.test_mapping import payload

pytestmark = pytest.mark.asyncio
SECRET = "synthetic-secret-marker"
PAGE = f"/projects/{SECRET}/page"
RESOURCES = ["files", "specifications"]
_LOGGERS = ("biosim_server.pages.service", "biosim_server.common.upstream")


def _capture() -> tuple[StringIO, list[logging.Handler]]:
    stream = StringIO()
    handlers: list[logging.Handler] = []
    for name in _LOGGERS:
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonFormatter())
        logging.getLogger(name).addHandler(handler)
        handlers.append(handler)
    return stream, handlers


def _release(handlers: list[logging.Handler]) -> None:
    for name, handler in zip(_LOGGERS, handlers, strict=True):
        logging.getLogger(name).removeHandler(handler)


def _events(stream: StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def _page_events(stream: StringIO) -> list[dict[str, Any]]:
    return [event for event in _events(stream) if "page_outcome" in event]


def _fetch_events(stream: StringIO) -> list[dict[str, Any]]:
    return [event for event in _events(stream) if "upstream_outcome" in event]


def _project_identity() -> dict[str, Any]:
    """A project summary carrying the marker, so a leak would be observable."""
    raw = payload("project")
    raw["name"] = SECRET
    return raw


async def test_success_emits_one_page_record_with_durations_and_byte_counts() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/summary"):
            return httpx.Response(200, json=_project_identity())
        return httpx.Response(200, json=satellite(request.url.path.split("/")[1]))

    stream, handlers = _capture()
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://upstream.test") as upstream:
            app.dependency_overrides[get_http_client] = lambda: upstream
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://platform.test") as caller:
                assert (await caller.get(PAGE)).status_code == 200
    finally:
        _release(handlers)

    pages = _page_events(stream)
    assert len(pages) == 1
    event = pages[0]
    assert event["page"] == "project"
    assert event["page_outcome"] == "ok"
    assert event["page_status"] == 200
    assert event["page_duration_ms"] >= 0
    assert event["page_identity_duration_ms"] >= 0
    assert event["page_satellites_duration_ms"] >= 0
    assert set(event) >= {"page", "page_outcome", "page_status", "page_duration_ms",
                          "page_identity_duration_ms", "page_satellites_duration_ms"}

    fetches = _fetch_events(stream)
    assert [fetch["upstream_resource"] for fetch in fetches] == ["project summary", *RESOURCES]
    for fetch in fetches:
        assert fetch["upstream_outcome"] == "ok"
        assert fetch["page"] == "project"
        assert fetch["upstream_duration_ms"] >= 0
        assert fetch["upstream_bytes"] > 0
    assert SECRET not in stream.getvalue()


async def test_missing_satellite_is_recorded_without_failing_the_page() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/summary"):
            return httpx.Response(200, json=_project_identity())
        return httpx.Response(404, text=SECRET)

    stream, handlers = _capture()
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://upstream.test") as upstream:
            app.dependency_overrides[get_http_client] = lambda: upstream
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://platform.test") as caller:
                assert (await caller.get(PAGE)).status_code == 200
    finally:
        _release(handlers)

    assert [event["page_outcome"] for event in _page_events(stream)] == ["ok"]
    # The 404 fallback is recorded per resource so an upstream contract change is
    # visible in the logs even though the page itself still succeeds.
    assert [event["upstream_outcome"] for event in _fetch_events(stream)] == [
        "ok", "not_found", "not_found",
    ]
    assert SECRET not in stream.getvalue().replace(PAGE, "")


@pytest.mark.parametrize(
    "failure, page_status, upstream_outcome",
    [
        (500, 502, "upstream_error"),
        ("read_timeout", 504, "timeout"),
        ("invalid", 502, "invalid_body"),
        (404, 404, "not_found"),
    ],
)
async def test_failures_record_a_bounded_outcome_and_the_caller_visible_status(
    failure: int | str, page_status: int, upstream_outcome: str
) -> None:
    """An upstream-side failure is a page `error` carrying the status the caller got.

    The page's own `timeout` outcome is reserved for the page budget expiring
    (test_page_budget_expiry_is_recorded_as_a_timeout); a per-phase httpx timeout
    that the fetch converts into a 504 is still a page error.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/summary"):
            if failure == "read_timeout":
                raise httpx.ReadTimeout(SECRET, request=request)
            if failure == "invalid":
                return httpx.Response(200, text=SECRET)
            assert isinstance(failure, int)
            return httpx.Response(failure, text=SECRET)
        return httpx.Response(200, json=satellite(request.url.path.split("/")[1]))

    stream, handlers = _capture()
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://upstream.test") as upstream:
            app.dependency_overrides[get_http_client] = lambda: upstream
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://platform.test") as caller:
                response = await caller.get(PAGE)
    finally:
        _release(handlers)

    assert response.status_code == page_status
    pages = _page_events(stream)
    assert len(pages) == 1
    assert pages[0]["page_outcome"] == "error"
    assert pages[0]["page_status"] == page_status
    assert pages[0]["page_satellites_duration_ms"] >= 0
    assert upstream_outcome in [event["upstream_outcome"] for event in _fetch_events(stream)]
    assert SECRET not in stream.getvalue()


async def test_page_budget_expiry_is_recorded_as_a_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The page budget firing is the one outcome the caller sees as 504 `timeout`."""
    monkeypatch.setitem(service._PAGE_TIMEOUTS, "project", 0.05)
    cancelled: set[str] = set()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/summary"):
            return httpx.Response(200, json=_project_identity())
        await hang_until_cancelled(request.url.path.split("/")[1], cancelled)

    stream, handlers = _capture()
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://upstream.test") as upstream:
            app.dependency_overrides[get_http_client] = lambda: upstream
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://platform.test") as caller:
                response = await caller.get(PAGE)
    finally:
        _release(handlers)

    assert response.status_code == 504
    pages = _page_events(stream)
    assert len(pages) == 1
    assert pages[0]["page_outcome"] == "timeout"
    assert pages[0]["page_status"] == 504
    # The identity phase finished even though the page did not; the stalls are
    # exactly what the durations are for.
    assert pages[0]["page_identity_duration_ms"] >= 0
    assert cancelled == set(RESOURCES)


async def test_caller_cancellation_is_recorded_and_still_propagates() -> None:
    cancelled: set[str] = set()
    both_started = asyncio.Event()
    started: set[str] = set()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/summary"):
            return httpx.Response(200, json=_project_identity())
        resource = request.url.path.split("/")[1]
        started.add(resource)
        if started == set(RESOURCES):
            both_started.set()
        await hang_until_cancelled(resource, cancelled)

    stream, handlers = _capture()
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://upstream.test") as upstream:
            task = asyncio.create_task(service.assemble_project_page(upstream, SECRET))
            await asyncio.wait_for(both_started.wait(), timeout=2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    finally:
        _release(handlers)

    assert cancelled == set(RESOURCES)
    pages = _page_events(stream)
    assert len(pages) == 1
    assert pages[0]["page_outcome"] == "cancelled"
    # Nothing was answered, so there is no status to report -- and the phase
    # durations that did complete are still there, which is the point.
    assert "page_status" not in pages[0]
    assert pages[0]["page_identity_duration_ms"] >= 0
    assert SECRET not in stream.getvalue()


async def test_oversize_body_is_recorded_without_the_body_or_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The byte-bound breach is observable as an outcome, not as a dumped body."""
    monkeypatch.setattr(get_settings(), "upstream_max_response_bytes", 64)
    oversize = json.dumps({"files": [SECRET] * 50})
    assert len(oversize) > 64, "the fixture must exceed the cap"

    stream, handlers = _capture()
    try:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, text=oversize)),
            base_url="https://upstream.test",
        ) as upstream:
            with pytest.raises(HTTPException) as error:
                await fetch_upstream_json_value(upstream, "/files/x", resource="files", page="project")
    finally:
        _release(handlers)

    assert error.value.status_code == 502

    events = _fetch_events(stream)
    assert [event["upstream_outcome"] for event in events] == ["too_large"]
    assert events[0]["upstream_resource"] == "files"
    assert events[0]["page"] == "project"
    assert SECRET not in stream.getvalue()


def test_benchmark_phase_summary_reads_the_servers_own_records(tmp_path: Path) -> None:
    """`bench_pages.py` turns the records into the phase/byte breakdown it lacked.

    uvicorn's access lines are not JSON, a baseline without the instrumentation
    logs nothing, and a different page's records must not be mixed in -- all three
    are handled here so the report cannot silently show the wrong numbers.
    """
    log = tmp_path / "branch.log"
    log.write_text(
        "\n".join(
            [
                'INFO:     127.0.0.1:55240 - "GET /runs/x/page HTTP/1.1" 200 OK',
                json.dumps({
                    "page": "run", "page_outcome": "ok", "page_status": 200,
                    "page_duration_ms": 120, "page_identity_duration_ms": 30,
                    "page_satellites_duration_ms": 90,
                }),
                json.dumps({"page": "run", "upstream_resource": "logs", "upstream_bytes": 2048}),
                json.dumps({"page": "run", "upstream_resource": "files", "upstream_bytes": 4096}),
                json.dumps({"page": "project", "page_outcome": "ok", "page_duration_ms": 999}),
                json.dumps({"page": "run", "page_outcome": "timeout", "page_duration_ms": 40000,
                            "page_identity_duration_ms": 12}),
                '{not valid json',
            ]
        )
    )

    samples = _read_phase_samples(log, "run")
    assert samples.requests == 2
    assert samples.outcomes == {"ok": 1, "timeout": 1}
    assert samples.page_millis == [120.0, 40000.0]
    assert samples.identity_millis == [30.0, 12.0]
    assert samples.satellites_millis == [90.0]
    assert samples.bytes_read == [2048, 4096]

    # A missing log (or a ref that predates the instrumentation) reports nothing.
    assert _read_phase_samples(tmp_path / "absent.log", "run").requests == 0


def test_formatter_drops_extras_that_are_not_on_the_allowlist() -> None:
    record = logging.LogRecord(
        "biosim_server.pages.service", logging.INFO, __file__, 1, "Page assembly finished", (), None
    )
    record.page = "run"
    record.page_outcome = "ok"
    record.payload = SECRET  # a future `extra=` that must not become a log field

    rendered = JsonFormatter().format(record)
    event = json.loads(rendered)
    assert event["page"] == "run"
    assert event["page_outcome"] == "ok"
    assert "payload" not in event
    assert SECRET not in rendered
