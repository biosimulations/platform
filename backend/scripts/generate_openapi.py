"""Regenerate the committed OpenAPI artifact.

The spec is a checked-in contract artifact, so it must not depend on the
environment it was generated in. ENABLE_RBAC_DEMO is forced on here because the
demo router is gated (config.py, default false) and the committed baseline
includes its paths -- regenerating with the default silently deletes them.
Must be set before biosim_server.api.main is imported, since the router is
mounted at import time.

    uv run python -m scripts.generate_openapi
"""

import os
from pathlib import Path

os.environ["ENABLE_RBAC_DEMO"] = "true"

import yaml  # noqa: E402

from biosim_server.api.main import app  # noqa: E402

SPEC = Path(__file__).resolve().parent.parent / "biosim_server/api/spec/openapi_3_1_0_generated.yaml"


def main() -> None:
    # sort_keys=False keeps route/schema declaration order; the default (ASCII)
    # escaping matches the committed file, so diffs stay minimal.
    SPEC.write_text(yaml.dump(app.openapi(), sort_keys=False, default_flow_style=False))
    print(f"wrote {SPEC}")


if __name__ == "__main__":
    main()
