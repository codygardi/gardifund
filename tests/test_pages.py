import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('pages_builder', Path(__file__).resolve().parents[1] / 'scripts' / 'build-pages.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def test_pages_artifact_contains_only_public_assets_and_explicit_address(tmp_path):
    result = builder.build(tmp_path / 'public', 'https://gardifund-view.personal.workers.dev/')
    assert {p.name for p in result.iterdir()} == {'index.html', 'site.css', 'site.js', 'config.json', '.nojekyll'}
    config = json.loads((result / 'config.json').read_text())
    assert config == {'schema_version': 1, 'dashboard_url': 'https://gardifund-view.personal.workers.dev/'}
    html = (result / 'index.html').read_text(encoding='utf-8')
    assert './site.css' in html and './site.js' in html
    assert 'aria-disabled="true"' in html
    assert 'PRODUCTION_API_KEY' not in html
    assert '127.0.0.1' not in html
    assert not (result / '.env').exists()


def test_pages_without_a_dashboard_stays_pending_and_does_not_invent_connectivity(tmp_path):
    result = builder.build(tmp_path / 'public')
    assert json.loads((result / 'config.json').read_text())['dashboard_url'] == ''
    assert 'connection pending' in (result / 'index.html').read_text(encoding='utf-8')
    (result / 'account.json').write_text('{"private":true}')
    with pytest.raises(ValueError, match='unexpected files'):
        builder.build(result)


@pytest.mark.parametrize('value', [
    'http://gardifund.workers.dev', 'https://127.0.0.1:8765', 'https://[::1]',
    'https://localhost', 'https://app.local', 'https://user:secret@gardifund.workers.dev',
    'https://gardifund.workers.dev/api/state', 'https://gardifund.workers.dev?token=secret',
    'https://gardifund.workers.dev/#token', ' https://gardifund.workers.dev',
    'https://gardifund.workers.dev:8765', 'javascript:alert(1)',
])
def test_pages_rejects_unsafe_dashboard_destinations(value):
    with pytest.raises(ValueError, match='public HTTPS hostname'):
        builder.dashboard_address(value)
