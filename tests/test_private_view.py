"""Test the Access assertion and live view over real HTTP sockets."""
import json
import socket
import threading
import time
from types import SimpleNamespace

from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
import jwt
import pytest
from pydantic import ValidationError
import uvicorn

from backend.config import Settings
from backend.main import create_app
from backend.services import sync_accounts


def test_view_ip_accepts_a_single_private_ipv4():
    settings = Settings(_env_file=None, GARDIFUND_PRIVATE_VIEW_IP='192.168.60.20')
    assert settings.viewing_address == ('192.168.60.20', 80)
    for value in ('0.0.0.0', '127.0.0.1', '8.8.8.8', '100.64.1.2',
                  '192.168.60.0/24', 'fd00::1', 'host.local'):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, GARDIFUND_PRIVATE_VIEW_IP=value)


def test_access_view_receives_sanitized_live_updates(tmp_path, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    class Keys:
        def __init__(self, *args, **kwargs):
            pass

        def get_signing_key_from_jwt(self, token):
            return SimpleNamespace(key=key.public_key())

    class Broker:
        def accounts(self):
            return [{'account_id': 'SYNTHETIC-PRIVATE-ID',
                     'balance': {'total_equity': 1000, 'total_cash': 800},
                     'positions': [{'symbol': 'AAA', 'quantity': 2, 'cost_basis': 180,
                                    'account_number': 'SYNTHETIC-PRIVATE-ID', 'note': 'SYNTHETIC-PRIVATE-NOTE'}],
                     'orders': []}]

    monkeypatch.setattr('backend.security.PyJWKClient', Keys)
    issuer, audience = 'https://view-test.cloudflareaccess.com', 'view-test-audience'
    with socket.create_server(('127.0.0.1', 0)) as local_socket, socket.create_server(('127.0.0.1', 0)) as view_socket:
        sockets = [local_socket, view_socket]
        for sock in sockets:
            sock.setblocking(False)
        local_port, view_port = (sock.getsockname()[1] for sock in sockets)
        settings = Settings(
            _env_file=None, GARDIFUND_DATA_DIR=tmp_path, GARDIFUND_AUTH_MODE='cloudflare',
            GARDIFUND_START_WORKER=False, GARDIFUND_PRIVATE_VIEW_IP='',
            GARDIFUND_LOCAL_PORT=local_port, GARDIFUND_REMOTE_PORT=view_port,
            PRODUCTION_API_KEY='', SETUPS_PASSWORD_HASH='', CF_ACCESS_TEAM_DOMAIN=issuer, CF_ACCESS_AUD=audience,
        )
        app = create_app(settings, adapter=Broker())
        sync_accounts(app.state.db, Broker())
        server = uvicorn.Server(uvicorn.Config(app, workers=1, proxy_headers=False,
                                              access_log=False, log_level='warning'))
        thread = threading.Thread(target=server.run, kwargs={'sockets': sockets}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(.02)
            assert server.started
            auth = {'Cf-Access-Jwt-Assertion': jwt.encode(
                {'iss': issuer, 'aud': audience, 'exp': int(time.time()) + 120}, key, algorithm='RS256')}
            local, view = (f'http://127.0.0.1:{port}' for port in (local_port, view_port))
            with httpx.Client(timeout=6, trust_env=False) as client:
                assert client.get(view + '/scope').status_code == 401
                assert client.get(view + '/scope', headers=auth).json()['access_scope'] == 'remote'
                assert client.get(local + '/api/state', headers=auth).status_code == 403
                assert client.get(view + '/api/state', headers=auth).status_code == 403
                overview = view + '/view/overview?mode=live'
                assert client.get(overview, headers=auth).json()['summary']['equity'] == 1000
                with client.stream('GET', view + '/view/events', headers=auth) as response:
                    assert response.status_code == 200
                    assert response.headers['content-type'].startswith('text/event-stream')
                    lines = response.iter_lines()
                    assert next(lines) == ': connected'
                    with app.state.db.connect() as conn:
                        row = conn.execute('SELECT payload FROM live_accounts').fetchone()
                        account = json.loads(row[0])
                        account['balance']['total_equity'] = 1234
                        conn.execute('UPDATE live_accounts SET payload=?', (json.dumps(account),))
                        app.state.db.event('overview', 'account_synced', conn,
                                           {'account_id': 'SYNTHETIC-PRIVATE-ID', 'note': 'SYNTHETIC-PRIVATE-NOTE'})
                    event = next(line for line in lines if line.startswith('data: '))
                    message = json.loads(event.removeprefix('data: '))
                    assert message['module'] == 'overview'
                    assert message['payload'] is None
                updated = client.get(overview, headers=auth)
                assert updated.json()['summary']['equity'] == 1234
                assert 'SYNTHETIC-PRIVATE' not in updated.text
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive()
