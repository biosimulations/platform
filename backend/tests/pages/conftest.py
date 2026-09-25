from collections.abc import Iterator

import pytest

from biosim_server.api.main import app
from biosim_server.common import ratelimit as ratelimit_module
from biosim_server.config import get_settings
from biosim_server.dependencies import get_http_client


@pytest.fixture(autouse=True)
def clear_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.pop(get_http_client, None)


@pytest.fixture(autouse=True)
def unmetered_page_budget() -> Iterator[None]:
    """Keep page-contract tests independent of the page-aggregation bucket.

    Both page routes are metered per client IP (``ratelimit.page_rate_limit``),
    and every mounted request in this directory arrives from the same test IP --
    so once the suite grew past the ceiling these tests would start failing on
    429 rather than on the behavior under test. Raising the ceiling here makes
    them order-independent; metering itself is proven in test_page_rate_limit.py,
    which sets its own ceiling (and its own window) per test.
    """
    settings = get_settings().ratelimit
    original_page_per_window = settings.page_per_window
    ratelimit_module._reset_rate_limit_state()
    settings.page_per_window = 10_000
    yield
    settings.page_per_window = original_page_per_window
    ratelimit_module._reset_rate_limit_state()
