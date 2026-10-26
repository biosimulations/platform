"""Give unhandled-exception 500s the same CORS headers as every other response.

Starlette renders an unhandled exception in ``ServerErrorMiddleware``, which always
wraps the whole middleware stack -- including CORSMiddleware -- and an
``@app.exception_handler(Exception)`` is installed there too. So a 500 left the app
without ``Access-Control-Allow-Origin``, and browsers reported it as a CORS error
that hid both the status and the body. This middleware sits *inside* CORS and
renders the 500 there instead.

The exception is re-raised once the 500 has been sent, exactly as
``ServerErrorMiddleware`` does: the server still logs the traceback, and test
clients that raise app exceptions still raise.
"""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class ServerErrorCorsMiddleware:
    """Render an unhandled exception as a JSON 500 inside CORSMiddleware, then re-raise it."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception:
            # Once headers are out a status can no longer be sent; leave the
            # connection to the server, as ServerErrorMiddleware would.
            if not response_started:
                response = JSONResponse(status_code=500, content={"detail": "Internal Server Error"})
                await response(scope, receive, send)
            raise
