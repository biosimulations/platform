"""Keep the supported image identity and persistent test setup aligned with Compose."""
from pathlib import Path

import yaml


def test_temporal_volume_is_initialized_for_the_pinned_nonroot_image() -> None:
    compose = yaml.safe_load((Path(__file__).resolve().parents[2] / 'compose.yaml').read_text())
    temporal = compose['services']['temporal']
    # Changing this pin requires checking the image's actual uid/gid again.
    assert temporal['image'] == 'temporalio/temporal:1.7.0'
    assert '--db-filename' in temporal['command']
    assert temporal['command'][temporal['command'].index('--db-filename') + 1] == '/data/temporal.db'
    assert 'temporal_data:/data' in temporal['volumes']
    assert temporal['depends_on']['temporal-data-init']['condition'] == 'service_completed_successfully'
    init = compose['services']['temporal-data-init']
    assert init['image'] == 'busybox:1.37'
    assert init['command'] == ['chown', '-R', '1000:1000', '/data']
    assert 'temporal_data:/data' in init['volumes']
    assert init['restart'] == 'no'
    assert 'temporal_data' in compose['volumes']
    assert temporal.get('user') not in ('root', '0', '0:0')
    assert temporal['healthcheck']['test'] == [
        'CMD', 'temporal', 'operator', 'cluster', 'health', '--address', 'localhost:7233',
    ]
    assert 'mongosh' in compose['services']['mongo']['healthcheck']['test']
