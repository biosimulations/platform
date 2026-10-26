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
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _finite_or_none(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite_or_none(v) for v in value]
    return value


class NonFiniteSafeJSONResponse(JSONResponse):
    """Starlette's ``JSONResponse``, with NaN and +/-Infinity rendered as ``null``.

    Content is rendered unchanged first; only content the strict encoder rejects
    is walked and re-rendered, so ordinary responses pay nothing extra.
    """

    def render(self, content: Any) -> bytes:
        try:
            return super().render(content)
        except ValueError:
            return super().render(_finite_or_none(content))
