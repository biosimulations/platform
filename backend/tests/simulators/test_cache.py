"""The five-key, single-flight, stale-if-error cache (plan §4.3; Phase 0 decision 0.6).

The stale window is measured from freshness expiry: a value is fresh for 10
minutes, then served stale for 24 more hours after a failed refresh, and the
boundary tests below use an injected clock rather than sleeping.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from biosim_server.simulators.cache import (
    CACHE_FRESH_SECONDS,
    CACHE_STALE_SECONDS,
    FAILURE_BACKOFF_SECONDS,
    SimulatorsCache,
)

KEY = "simulators/latest"


class _FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _cache(clock: _FakeClock) -> SimulatorsCache:
    return SimulatorsCache([KEY], clock=clock)


def _loader(state: dict[str, Any], calls: list[int]) -> Callable[[], Awaitable[str]]:
    async def loader() -> str:
        calls.append(len(calls) + 1)
        if state["fails"]:
            raise RuntimeError("upstream down")
        return f"value-{len(calls)}"

    return loader


def test_default_windows_are_ten_minutes_and_twenty_four_hours() -> None:
    assert CACHE_FRESH_SECONDS == 600.0
    assert CACHE_STALE_SECONDS == 86400.0


@pytest.mark.asyncio
async def test_rejects_keys_outside_the_declared_set() -> None:
    cache = SimulatorsCache([KEY])

    async def loader() -> str:
        raise AssertionError("must not run")

    with pytest.raises(ValueError, match="not one of the fixed cacheable keys"):
        await cache.get("simulators/copasi", loader)


@pytest.mark.asyncio
async def test_hit_within_the_fresh_window_calls_upstream_once() -> None:
    clock = _FakeClock()
    cache = _cache(clock)
    state: dict[str, Any] = {"fails": False}
    calls: list[int] = []

    first = await cache.get(KEY, _loader(state, calls))
    second = await cache.get(KEY, _loader(state, calls))

    assert first == second
    assert calls == [1]


@pytest.mark.asyncio
async def test_single_flight_makes_one_call_for_n_concurrent_cold_callers() -> None:
    clock = _FakeClock()
    cache = _cache(clock)
    calls: list[int] = []
    gate = asyncio.Event()

    async def loader() -> str:
        calls.append(len(calls) + 1)
        await gate.wait()
        return "value"

    task = asyncio.gather(*(cache.get(KEY, loader) for _ in range(8)))
    for _ in range(10):
        await asyncio.sleep(0)
    gate.set()
    results = await task

    assert calls == [1]
    assert results == ["value"] * 8


@pytest.mark.asyncio
async def test_failed_refresh_serves_stale_within_24h_of_freshness_expiry(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = _FakeClock()
    cache = _cache(clock)
    state: dict[str, Any] = {"fails": False}
    calls: list[int] = []
    first = await cache.get(KEY, _loader(state, calls))

    # One second past freshness expiry the refresh fails and the last good value is served.
    clock.advance(CACHE_FRESH_SECONDS + 1)
    state["fails"] = True
    with caplog.at_level(logging.WARNING):
        stale = await cache.get(KEY, _loader(state, calls))

    assert stale == first
    assert calls == [1, 2]  # the failed refresh ran; the stale value is what comes back
    served = [record for record in caplog.records if getattr(record, "upstream_outcome", None) == "stale_served"]
    assert len(served) == 1
    assert "upstream down" not in served[0].getMessage()


@pytest.mark.asyncio
async def test_failed_refresh_after_the_stale_window_raises() -> None:
    """Maximum age is 24 h 10 min after the successful fetch (Phase 0 decision 0.6)."""
    clock = _FakeClock()
    cache = _cache(clock)
    state: dict[str, Any] = {"fails": False}
    calls: list[int] = []
    await cache.get(KEY, _loader(state, calls))

    state["fails"] = True
    clock.now = 1000.0 + CACHE_FRESH_SECONDS + CACHE_STALE_SECONDS  # exactly the stale boundary
    with pytest.raises(RuntimeError, match="upstream down"):
        await cache.get(KEY, _loader(state, calls))
    assert calls == [1, 2]  # one last refresh was attempted; nothing stale remained to serve


@pytest.mark.asyncio
async def test_errors_are_never_cached() -> None:
    clock = _FakeClock()
    cache = _cache(clock)
    state: dict[str, Any] = {"fails": True}
    calls: list[int] = []
    loader = _loader(state, calls)

    with pytest.raises(RuntimeError):
        await cache.get(KEY, loader)
    state["fails"] = False
    assert await cache.get(KEY, loader) == "value-2"
    assert calls == [1, 2]


@pytest.mark.asyncio
async def test_success_after_stale_serving_restarts_the_fresh_window() -> None:
    clock = _FakeClock()
    cache = _cache(clock)
    state: dict[str, Any] = {"fails": False}
    calls: list[int] = []
    loader = _loader(state, calls)

    first = await cache.get(KEY, loader)
    clock.advance(CACHE_FRESH_SECONDS + 1)
    state["fails"] = True
    assert await cache.get(KEY, loader) == first  # stale served, timestamp untouched
    state["fails"] = False
    clock.advance(FAILURE_BACKOFF_SECONDS)  # past the back-off, so the next call refreshes
    refreshed_at = clock.now
    refreshed = await cache.get(KEY, loader)
    assert refreshed == "value-3"  # a successful fetch replaces the stale value
    state["fails"] = True
    clock.now = refreshed_at + CACHE_FRESH_SECONDS + CACHE_STALE_SECONDS
    with pytest.raises(RuntimeError):
        await cache.get(KEY, loader)  # the new fetch's own stale window has since expired


# ---------------------------------------------------------------------------
# Single-flight under failure: an outage is one upstream attempt, not one per caller
# ---------------------------------------------------------------------------


async def _expired_cache(clock: _FakeClock) -> SimulatorsCache:
    """A cache holding one good value that has just gone stale."""
    cache = _cache(clock)

    async def good() -> str:
        return "last-good"

    await cache.get(KEY, good)
    clock.advance(CACHE_FRESH_SECONDS + 1)
    return cache


def _failing_loader(calls: list[int], gate: asyncio.Event) -> Callable[[], Awaitable[str]]:
    """A loader that fails only once ``gate`` opens, so callers pile up behind it."""

    async def loader() -> str:
        calls.append(len(calls) + 1)
        await gate.wait()
        raise RuntimeError("upstream down")

    return loader


async def _let_callers_queue() -> None:
    for _ in range(10):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_concurrent_callers_share_one_failed_refresh_and_all_get_the_stale_value() -> None:
    """N callers during an outage make one upstream attempt and are all served stale.

    Before the shared refresh task, each waiter took the lock after the previous
    one failed and called upstream again, so caller k waited k attempts.
    """
    clock = _FakeClock()
    cache = await _expired_cache(clock)
    calls: list[int] = []
    gate = asyncio.Event()
    loader = _failing_loader(calls, gate)

    results = asyncio.gather(*(cache.get(KEY, loader) for _ in range(5)))
    await _let_callers_queue()
    gate.set()

    assert await results == ["last-good"] * 5
    assert calls == [1]


@pytest.mark.asyncio
async def test_concurrent_cold_callers_share_one_failed_attempt() -> None:
    """With nothing stale to serve, every waiter gets the one attempt's error."""
    clock = _FakeClock()
    cache = _cache(clock)
    calls: list[int] = []
    gate = asyncio.Event()
    loader = _failing_loader(calls, gate)

    results = asyncio.gather(*(cache.get(KEY, loader) for _ in range(5)), return_exceptions=True)
    await _let_callers_queue()
    gate.set()

    outcomes = await results
    assert all(isinstance(outcome, RuntimeError) for outcome in outcomes)
    assert calls == [1]


@pytest.mark.asyncio
async def test_a_call_inside_the_back_off_makes_no_upstream_attempt() -> None:
    """After a failed refresh, the stale value is served without calling a down upstream.

    Once the back-off has passed, the next call tries upstream again.
    """
    clock = _FakeClock()
    cache = await _expired_cache(clock)
    state: dict[str, Any] = {"fails": True}
    calls: list[int] = []
    loader = _loader(state, calls)

    assert await cache.get(KEY, loader) == "last-good"
    assert calls == [1]

    clock.advance(FAILURE_BACKOFF_SECONDS - 1)
    assert await cache.get(KEY, loader) == "last-good"
    assert calls == [1]  # inside the back-off: no attempt

    clock.advance(1)
    assert await cache.get(KEY, loader) == "last-good"
    assert calls == [1, 2]  # back-off over: one new attempt, which failed again


@pytest.mark.asyncio
async def test_back_off_does_not_apply_without_a_stale_value() -> None:
    """A cold failure is never cached, back-off included: the next caller retries."""
    clock = _FakeClock()
    cache = _cache(clock)
    state: dict[str, Any] = {"fails": True}
    calls: list[int] = []
    loader = _loader(state, calls)

    with pytest.raises(RuntimeError):
        await cache.get(KEY, loader)
    with pytest.raises(RuntimeError):
        await cache.get(KEY, loader)
    assert calls == [1, 2]


@pytest.mark.asyncio
async def test_a_cancelled_first_caller_does_not_cancel_the_shared_refresh() -> None:
    """The caller that started the refresh disconnects; the others still get the value.

    The refresh runs in its own task, shielded from any one caller, so a
    disconnecting client cannot make the remaining waiters redo the fetch.
    """
    clock = _FakeClock()
    cache = _cache(clock)
    calls: list[int] = []
    gate = asyncio.Event()

    async def loader() -> str:
        calls.append(len(calls) + 1)
        await gate.wait()
        return "value"

    first = asyncio.create_task(cache.get(KEY, loader))
    await _let_callers_queue()
    others = asyncio.gather(*(cache.get(KEY, loader) for _ in range(3)))
    await _let_callers_queue()
    first.cancel()
    await _let_callers_queue()
    gate.set()

    assert await others == ["value"] * 3
    assert first.cancelled()
    assert calls == [1]
    # The refresh also filled the cache: a later call needs no upstream attempt.
    assert await cache.get(KEY, loader) == "value"
    assert calls == [1]
