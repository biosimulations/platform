"""The CI entry point must fail each infra gate, even with a degraded-ready API."""
import os
from pathlib import Path
import subprocess

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/smoke-infrastructure.sh'


@pytest.mark.parametrize('failure,stage,expected', [
    ('mongo', 'infra', 'mongo did not become ready'),
    ('temporal', 'infra', 'temporal did not become SERVING'),
    ('probe', 'infra', 'describe'),
    ('backend', 'backend', 'backend did not connect to Temporal'),
])
def test_smoke_fails_each_unavailable_dependency(tmp_path: Path, failure: str, stage: str, expected: str) -> None:
    binaries = {
        'sleep': '#!/bin/sh\nexit 0\n',
        'curl': '#!/bin/sh\nprintf \'{"checks":{"temporal":false}}\\n\'\n',
        'docker': '''#!/usr/bin/env python3
import os,sys
args = sys.argv[1:]
failure = os.environ['SMOKE_FAILURE']
if 'mongosh' in args: sys.exit(1 if failure == 'mongo' else 0)
if 'health' in args:
    print('NOT_SERVING' if failure == 'temporal' else 'SERVING')
    sys.exit(0)
if 'describe' in args and failure == 'probe':
    print('describe: workflow not found', file=sys.stderr)
    sys.exit(1)
print('diagnostics: ' + ' '.join(args))
''',
    }
    for name, content in binaries.items():
        path = tmp_path / name
        path.write_text(content)
        path.chmod(0o755)
    result = subprocess.run(['bash', str(SCRIPT), stage], env={**os.environ, 'SMOKE_FAILURE': failure,
        'SMOKE_MONGO_WAIT_SECONDS': '1', 'SMOKE_TEMPORAL_WAIT_SECONDS': '1', 'SMOKE_BACKEND_WAIT_SECONDS': '1',
        'PATH': str(tmp_path) + os.pathsep + os.environ['PATH']}, text=True, capture_output=True, timeout=15)
    assert result.returncode != 0
    assert expected in result.stderr
    assert 'compose ps -a' in result.stderr
    assert 'logs --tail=200 mongo temporal' in result.stderr
