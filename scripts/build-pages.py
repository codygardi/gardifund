"""Build only the public launch page; never include app bundles or local data."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ('index.html', 'site.css', 'site.js')
OUTPUT_FILES = {*ASSETS, 'config.json', '.nojekyll'}


def dashboard_address(value):
    if not value:
        return ''
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ''
        if (value != value.strip() or any(ord(char) < 33 for char in value)
                or parsed.scheme != 'https' or parsed.username or parsed.password
                or parsed.port not in (None, 443) or parsed.path not in ('', '/')
                or parsed.query or parsed.fragment
                or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+', host)
                or host.endswith(('.localhost', '.local', '.internal', '.test', '.example'))):
            raise ValueError()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError()
        return f'https://{host.lower()}/'
    except ValueError:
        raise ValueError('GARDIFUND_DASHBOARD_URL must be a public HTTPS hostname with no path, credentials, query, or fragment') from None


def build(output, address=''):
    address = dashboard_address(address)
    output = Path(output).resolve()
    if output == ROOT or output == ROOT / 'pages':
        raise ValueError('Choose a separate Pages output directory')
    output.mkdir(parents=True, exist_ok=True)
    if any(path.name not in OUTPUT_FILES or path.is_symlink() or not path.is_file() for path in output.iterdir()):
        raise ValueError('Pages output contains unexpected files; refusing to publish them')
    for name in ASSETS:
        shutil.copyfile(ROOT / 'pages' / name, output / name)
    (output / 'config.json').write_text(json.dumps({'schema_version': 1, 'dashboard_url': address}, indent=2) + '\n', encoding='utf-8')
    (output / '.nojekyll').write_text('', encoding='utf-8')
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'pages-dist')
    args = parser.parse_args()
    destination = build(args.output, os.environ.get('GARDIFUND_DASHBOARD_URL', ''))
    print(f'Public launch page built: {destination.name} (five static files; no account data)')
