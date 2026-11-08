"""Cross-runtime gateway/ASGI integration; no Cloudflare account or provider calls."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time
from types import SimpleNamespace

from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
import uvicorn

from backend.config import Settings
from backend.main import create_app
from backend.services import sync_accounts


def test_javascript_gateway_authenticates_real_backend_and_forwards_live_sse(tmp_path, monkeypatch):
    node = shutil.which('node')
    assert node, 'Node.js is required for the cross-runtime gateway integration test'
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
    sockets = [socket.create_server(('127.0.0.1', 0)) for _ in range(2)]
    for sock in sockets:
        sock.setblocking(False)
    local_port, remote_port = [sock.getsockname()[1] for sock in sockets]
    settings = Settings(_env_file=None, GARDIFUND_DATA_DIR=tmp_path,
                        GARDIFUND_AUTH_MODE='cloudflare', GARDIFUND_START_WORKER=False,
                        GARDIFUND_LOCAL_PORT=local_port, GARDIFUND_REMOTE_PORT=remote_port,
                        PRODUCTION_API_KEY='', SETUPS_PASSWORD_HASH='',
                        CF_ACCESS_TEAM_DOMAIN='https://gateway-test.cloudflareaccess.com',
                        CF_ACCESS_AUD='gateway-test-audience')
    app = create_app(settings, adapter=Broker())
    sync_accounts(app.state.db, Broker())
    with app.state.db.connect() as conn:
        cursor = conn.execute('SELECT MAX(id) FROM events').fetchone()[0]
    connected = threading.Event()
    update_errors = []

    async def observed_app(scope, receive, send):
        async def observed_send(message):
            if (scope.get('path') == '/view/events' and message['type'] == 'http.response.body'
                    and b': connected' in message.get('body', b'')):
                connected.set()
            await send(message)
        await app(scope, receive, observed_send)

    def update_after_connection():
        if not connected.wait(15):
            return
        try:
            with app.state.db.connect() as conn:
                row = conn.execute('SELECT payload FROM live_accounts').fetchone()
                payload = json.loads(row[0])
                payload['balance']['total_equity'] = 1234
                conn.execute('UPDATE live_accounts SET payload=?', (json.dumps(payload),))
                app.state.db.event('overview', 'account_synced', conn,
                                   {'account_id': 'SYNTHETIC-PRIVATE-ID', 'note': 'SYNTHETIC-PRIVATE-NOTE'})
        except Exception as exc:
            update_errors.append(type(exc).__name__)

    server = uvicorn.Server(uvicorn.Config(observed_app, workers=1, proxy_headers=False,
                                          access_log=False, log_level='warning'))
    server_thread = threading.Thread(target=server.run, kwargs={'sockets': sockets}, daemon=True)
    update_thread = threading.Thread(target=update_after_connection, daemon=True)
    server_thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and server_thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.02)
        assert server.started
        update_thread.start()
        claims = {'iss': 'https://gateway-test.cloudflareaccess.com',
                  'aud': 'gateway-test-audience', 'exp': int(time.time()) + 120}
        env = {**os.environ, 'QA_LOCAL_ORIGIN': f'http://127.0.0.1:{local_port}',
               'QA_REMOTE_ORIGIN': f'http://127.0.0.1:{remote_port}', 'QA_EVENT_CURSOR': str(cursor),
               'QA_ACCESS_JWT': jwt.encode(claims, key, algorithm='RS256'),
               'QA_EXPIRED_JWT': jwt.encode({**claims, 'exp': int(time.time()) - 60}, key, algorithm='RS256'),
               'QA_WRONG_AUDIENCE_JWT': jwt.encode({**claims, 'aud': 'wrong'}, key, algorithm='RS256')}
        result = subprocess.run([node, str(Path(__file__).with_name('gateway_roundtrip.mjs'))],
                                env=env, capture_output=True, text=True, timeout=25)
        assert result.returncode == 0, result.stdout + result.stderr
        report = json.loads(result.stdout)
        assert report['authenticated_gateway_roundtrip'] and report['live_sse_update']
        update_thread.join(timeout=2)
        assert not update_thread.is_alive() and not update_errors
    finally:
        connected.set()
        if update_thread.ident is not None:
            update_thread.join(timeout=3)
        server.should_exit = True
        server_thread.join(timeout=10)
        for sock in sockets:
            sock.close()
        assert not server_thread.is_alive()
