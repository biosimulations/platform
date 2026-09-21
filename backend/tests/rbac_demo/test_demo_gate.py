"""#20: the RBAC demo router is environment-gated (ENABLE_RBAC_DEMO, default off).

These tests exercise the gating helper directly on a fresh FastAPI app, so both
the enabled and disabled states are verified independently of the process-wide
test setting (conftest.py enables the router for the demo/Keycloak suites).
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from biosim_server.api.main import register_demo_router

_DEMO_PATH = "/api/v1/demo/public"


def _app_with_demo(*, enabled: bool) -> FastAPI:
    app = FastAPI()
    register_demo_router(app, enabled=enabled)
    return app


def test_demo_router_absent_when_disabled() -> None:
    app = _app_with_demo(enabled=False)
    # Not mounted -> 404 on its paths.
    assert TestClient(app).get(_DEMO_PATH).status_code == 404
    # Absent from the OpenAPI schema.
    assert not any(p.startswith("/api/v1/demo") for p in app.openapi().get("paths", {}))


def test_demo_router_present_when_enabled() -> None:
    app = _app_with_demo(enabled=True)
    # Public demo endpoint is reachable and needs no bearer token.
    assert TestClient(app).get(_DEMO_PATH).status_code == 200
    # Present in the OpenAPI schema.
    assert any(p.startswith("/api/v1/demo") for p in app.openapi().get("paths", {}))
