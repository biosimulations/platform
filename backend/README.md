![backend-ci](https://github.com/biosimulations/platform/actions/workflows/ci.yaml/badge.svg)

# **Platform Backend**

Biosimulations backend services. Uses separate containers for REST API management, job processing (Temporal workers), and MongoDB for state.

_The REST API can be accessed via Swagger UI here:_ **[https://biosim.biosimulations.org/docs](https://biosim.biosimulations.org/docs)**

### For Developers

The backend is a microservices Python application built with FastAPI and Temporal. Key packages under `biosim_server/`:

- `api`: REST endpoints (file uploads, job creation, result retrieval)
- `common`: shared utilities (storage, temporal, ssh, hpc)
- `biosim_runs`, `biosim_verify`, `biosim_omex`, `compatibility`, `simulations`: domain workflows and activities
- `worker`: worker process entry point

**Container management** is handled by Kubernetes (config in `../kustomize/`). **Dependency management** uses `poetry`.

### Quickstart

```bash
cd backend
poetry install
poetry run uvicorn biosim_server.api.main:app --host 0.0.0.0 --port 8000
poetry run python -m biosim_server.worker.worker_main
poetry run pytest -m "not integration"
```

### Notes
- Uses Temporal for distributed, fault-tolerant workflow tasks. A Temporal server must be reachable at `TEMPORAL_SERVICE_URL` (default `localhost:7233`).
- If running Temporal workflows in the PyCharm debugger fails, see https://youtrack.jetbrains.com/issue/PY-62467/TypeError-Task-object-is-not-callable-debugging-uvloop-with-asyncio-support-enabled
