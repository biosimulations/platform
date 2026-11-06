"""Single-flight, stale-if-error in-process cache for the five cacheable keys (plan §4.3).

Exactly five keys are cacheable: the simulator list and the four ontology dumps.
A value is fresh for ``CACHE_FRESH_SECONDS``; when a refresh fails, the last good
value is served until ``CACHE_STALE_SECONDS`` have passed **since freshness
expiry** (Phase 0 decision 0.6), so a value is at most 24 hours 10 minutes old
after a successful fetch. Only validated results are stored: an error is never
cached, and a failed refresh never resets the successful-fetch timestamp.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Final, TypeVar, cast

logger = logging.getLogger(__name__)

CACHE_FRESH_SECONDS: Final[float] = 10 * 60
CACHE_STALE_SECONDS: Final[float] = 24 * 60 * 60

ValueT = TypeVar("ValueT")


@dataclass
class _Entry:
    value: object
    fetched_at: float
    fresh_until: float
    stale_until: float


class SimulatorsCache:
    """One process, five keys, one refresh in flight per key.

    ``clock`` is injectable so the 10-minute and 24-hour boundaries are tested
    without sleeping. The key set is closed: any other key is a programming
    error, not a cache miss.
    """

    def __init__(
        self,
        cacheable_keys: Iterable[str],
        *,
        fresh_seconds: float = CACHE_FRESH_SECONDS,
        stale_seconds: float = CACHE_STALE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._cacheable_keys = frozenset(cacheable_keys)
        self._fresh_seconds = fresh_seconds
        self._stale_seconds = stale_seconds
        self._now = clock
        self._locks: dict[str, asyncio.Lock] = {key: asyncio.Lock() for key in self._cacheable_keys}
        self._entries: dict[str, _Entry] = {}

    async def get(self, key: str, loader: Callable[[], Awaitable[ValueT]]) -> ValueT:
        """Return the cached value under ``key``, refreshing it at most once.

        Inside the fresh window the cached value is returned untouched. When it
        has expired, one caller runs ``loader`` under the key's lock while the
        rest await it (single-flight); a loader result replaces the entry, and a
        loader failure serves the last good value while the stale window is open
        and re-raises otherwise. Neither failure leaves a trace in the cache.
        """
        if key not in self._cacheable_keys:
            raise ValueError(f"{key!r} is not one of the fixed cacheable keys")
        entry = self._entries.get(key)
        if entry is not None and self._now() < entry.fresh_until:
            return cast(ValueT, entry.value)
        async with self._locks[key]:
            entry = self._entries.get(key)
            # Another caller refreshed while this one waited on the lock.
            if entry is not None and self._now() < entry.fresh_until:
                return cast(ValueT, entry.value)
            try:
                value = await loader()
            except Exception:
                if entry is not None and self._now() < entry.stale_until:
                    logger.warning(
                        "Serving a stale %s after a failed refresh",
                        key,
                        extra={"upstream_resource": key, "upstream_outcome": "stale_served"},
                    )
                    return cast(ValueT, entry.value)
                raise
            now = self._now()
            self._entries[key] = _Entry(
                value=value,
                fetched_at=now,
                fresh_until=now + self._fresh_seconds,
                stale_until=now + self._fresh_seconds + self._stale_seconds,
            )
            return value
