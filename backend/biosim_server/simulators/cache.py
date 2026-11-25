"""Single-flight, stale-if-error in-process cache for the five cacheable keys (plan §4.3).

Exactly five keys are cacheable: the simulator list and the four ontology dumps.
A value is fresh for ``CACHE_FRESH_SECONDS``; when a refresh fails, the last good
value is served until ``CACHE_STALE_SECONDS`` have passed **since freshness
expiry** (Phase 0 decision 0.6), so a value is at most 24 hours 10 minutes old
after a successful fetch. Only validated results are stored: an error is never
cached, and a failed refresh never resets the successful-fetch timestamp.

Single-flight holds when the upstream is down, too. All callers of an expired
key share one refresh task, so a failure is one upstream attempt answered to
every waiter, not one attempt per waiter in turn. After a failed refresh that
served a stale value, further callers get that value without a new attempt for
``FAILURE_BACKOFF_SECONDS``. The back-off limits attempts, not results: nothing
about the failure is stored, and a cold key (no stale value) is retried by the
next caller as before.
"""

import asyncio
import functools
import logging
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Final, TypeVar, cast

logger = logging.getLogger(__name__)

CACHE_FRESH_SECONDS: Final[float] = 10 * 60
CACHE_STALE_SECONDS: Final[float] = 24 * 60 * 60
# How long a stale value is served without a new upstream attempt after a refresh
# fails. One attempt against a hanging upstream can take the 30 s httpx timeout
# (common/upstream.UPSTREAM_TIMEOUT_SECONDS), so this keeps a down upstream at no
# more than about one attempt per minute per key per process.
FAILURE_BACKOFF_SECONDS: Final[float] = 30.0

ValueT = TypeVar("ValueT")


@dataclass
class _Entry:
    value: object
    fetched_at: float
    fresh_until: float
    stale_until: float


class SimulatorsCache:
    """One process, five keys, one refresh in flight per key.

    ``clock`` is injectable so the 10-minute, 24-hour and back-off boundaries are
    tested without sleeping. The key set is closed: any other key is a
    programming error, not a cache miss.
    """

    def __init__(
        self,
        cacheable_keys: Iterable[str],
        *,
        fresh_seconds: float = CACHE_FRESH_SECONDS,
        stale_seconds: float = CACHE_STALE_SECONDS,
        failure_backoff_seconds: float = FAILURE_BACKOFF_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._cacheable_keys = frozenset(cacheable_keys)
        self._fresh_seconds = fresh_seconds
        self._stale_seconds = stale_seconds
        self._failure_backoff_seconds = failure_backoff_seconds
        self._now = clock
        self._entries: dict[str, _Entry] = {}
        self._inflight: dict[str, asyncio.Task[object]] = {}
        self._retry_not_before: dict[str, float] = {}

    async def get(self, key: str, loader: Callable[[], Awaitable[ValueT]]) -> ValueT:
        """Return the cached value under ``key``, refreshing it at most once.

        Inside the fresh window the cached value is returned untouched. When it
        has expired, the first caller starts one refresh task and every caller,
        that one included, awaits the same task (single-flight). A loader result
        replaces the entry; a loader failure answers every waiter with the last
        good value while the stale window is open, and with the error otherwise.
        Neither failure leaves a value in the cache.
        """
        if key not in self._cacheable_keys:
            raise ValueError(f"{key!r} is not one of the fixed cacheable keys")
        entry = self._entries.get(key)
        now = self._now()
        if entry is not None and now < entry.fresh_until:
            return cast(ValueT, entry.value)
        # A refresh just failed and a stale value is still servable: do not send
        # another request to an upstream that is down.
        if entry is not None and now < entry.stale_until and now < self._retry_not_before.get(key, 0.0):
            return cast(ValueT, entry.value)
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.create_task(self._refresh(key, loader))
            self._inflight[key] = task
            task.add_done_callback(functools.partial(self._finish, key))
        # shield: a caller that disconnects must not cancel the refresh the other
        # waiters are sharing. The task runs on and still fills the cache.
        return cast(ValueT, await asyncio.shield(task))

    def _finish(self, key: str, task: "asyncio.Task[object]") -> None:
        """Free the key's refresh slot once its task is done."""
        if self._inflight.get(key) is task:
            del self._inflight[key]
        # Mark a failure as retrieved. If every caller was cancelled, nobody
        # awaits the task, and asyncio would log "exception was never retrieved".
        if not task.cancelled():
            task.exception()

    async def _refresh(self, key: str, loader: Callable[[], Awaitable[object]]) -> object:
        entry = self._entries.get(key)
        try:
            value = await loader()
        except Exception:
            now = self._now()
            if entry is not None and now < entry.stale_until:
                self._retry_not_before[key] = now + self._failure_backoff_seconds
                logger.warning(
                    "Serving a stale %s after a failed refresh",
                    key,
                    extra={"upstream_resource": key, "upstream_outcome": "stale_served"},
                )
                return entry.value
            raise
        now = self._now()
        self._entries[key] = _Entry(
            value=value,
            fetched_at=now,
            fresh_until=now + self._fresh_seconds,
            stale_until=now + self._fresh_seconds + self._stale_seconds,
        )
        self._retry_not_before.pop(key, None)
        return value
