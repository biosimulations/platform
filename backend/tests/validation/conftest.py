from collections.abc import Iterator

import pytest

from biosim_server.api.main import app
from biosim_server.dependencies import get_combine_http_client


@pytest.fixture(autouse=True)
def clear_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.pop(get_combine_http_client, None)
