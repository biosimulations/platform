"""Shared aiohttp stub for the biosimulations.org passthrough client tests.

Every ``BiosimServiceRest`` method opens its own ``aiohttp.ClientSession``, so
these tests patch the session factory and inspect the URL that was actually
requested. Centralised here because six endpoints assert the same shape.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import ClientResponseError


def upstream_error(status: int) -> ClientResponseError:
    return ClientResponseError(request_info=MagicMock(), history=(), status=status)


def stub_session(json_body: Any) -> tuple[Any, Any]:
    """Return ``(patcher, session)``; the session records ``.get`` calls.

    Use as::

        patcher, session = stub_session({...})
        with patcher:
            ...
        session.get.assert_called_once_with(url)
    """
    resp = AsyncMock()
    resp.json.return_value = json_body
    resp.raise_for_status = MagicMock()

    get_cm = MagicMock()
    get_cm.__aenter__ = AsyncMock(return_value=resp)
    get_cm.__aexit__ = AsyncMock(return_value=False)

    session = MagicMock()
    session.get = MagicMock(return_value=get_cm)

    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)

    return patch("aiohttp.ClientSession", return_value=session_cm), session
