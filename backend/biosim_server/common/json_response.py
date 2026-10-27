"""JSON responses that render non-finite floats (NaN, +/-Infinity) as ``null``.

Simulator output legitimately contains NaN -- a variable a simulator could not
compute -- and it flows into verification reports both as raw data
(``Hdf5DataValues.values``) and as comparison scores (``calc_stats`` yields NaN
for an all-NaN variable). Temporal's JSON converter accepts NaN, so such a report
is stored without complaint; FastAPI's response path then hands the floats to
Starlette's ``JSONResponse``, which renders with ``allow_nan=False`` and raises.
That ValueError escaped as an unhandled 500 *outside* CORSMiddleware, so browsers
saw a CORS error instead of the report (compose-api-prod-comparison-212, Oct 2026).

``null`` is what Pydantic's own ``model_dump_json`` emits for these values and what
the frontend already handles (``formatScore`` shows a dash; chart gaps).
"""

import math
from typing import Any

from starlette.responses import JSONResponse


def _finite_or_none(value: Any) -> Any:
    """Return ``value`` with every non-finite float replaced by ``None``.

    A self-referential container raises ``ValueError`` -- ``json.dumps``'s own
    "Circular reference detected" -- rather than recursing until ``RecursionError``:
    the strict encoder already rejects such content, and the fallback walk must not
    replace that clean rejection with a crash.
    """
    return _finite_or_none_walk(value, set())


def _finite_or_none_walk(value: Any, ancestors: set[int]) -> Any:
    """``_finite_or_none``'s recursion, tracking the containers on the current path.

    Only ancestors count as cycles, so a value shared by two siblings (a DAG, which
    ``json.dumps`` serializes by duplication) is walked twice and kept.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if not isinstance(value, (dict, list, tuple)):
        return value
    marker = id(value)
    if marker in ancestors:
        raise ValueError("Circular reference detected")
    ancestors.add(marker)
    try:
        if isinstance(value, dict):
            return {k: _finite_or_none_walk(v, ancestors) for k, v in value.items()}
        return [_finite_or_none_walk(v, ancestors) for v in value]
    finally:
        ancestors.remove(marker)


class NonFiniteSafeJSONResponse(JSONResponse):
    """Starlette's ``JSONResponse``, with NaN and +/-Infinity rendered as ``null``.

    Content is rendered unchanged first; only content the strict encoder rejects
    is walked and re-rendered, so ordinary responses pay nothing extra. Content the
    strict encoder rejects for any other reason -- a circular reference, say -- still
    raises ``ValueError``, exactly as it does without this class.
    """

    def render(self, content: Any) -> bytes:
        try:
            return super().render(content)
        except ValueError:
            return super().render(_finite_or_none(content))
