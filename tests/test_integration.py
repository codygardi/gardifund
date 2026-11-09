import asyncio
import io
import json
import os
import sqlite3
import socket
import subprocess
import sys
import threading
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import jwt
import pytest
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.audit import compare_daily_closes, yahoo_daily_reference
from backend.db import Database
from backend.discovery import MarketDiscovery
from backend.quotes import trade_timestamp
from backend.main import create_app
from backend.market import MarketStore, daily_coverage, intraday_gaps
from backend.services import store_holdings, sync_accounts
from backend.tradier import Tradier
from backend.worker import Worker
from backend import shadow as shadow_module
from backend.setup_lock import hash_password

TEST_PASSWORD_HASH = hash_password("unit-test-pin")


class FakeTradier:
    def accounts(self):
        return [{"account_id": "ACCT1234", "classification": "individual",
                 "balance": {"total_equity": 5500, "total_cash": 1000, "account_number": "ACCT1234"},
                 "positions": [{"symbol": "AAPL", "quantity": 2, "cost_basis": 400}], "orders": []}]

    def quotes(self, symbols):
        return [{"symbol": s, "last": 100.0, "change": 1.0, "change_percentage": 1.0, "volume": 5000} for s in symbols]


def config(tmp_path, mode="local"):
    return Settings(_env_file=None, GARDIFUND_DATA_DIR=tmp_path, GARDIFUND_AUTH_MODE=mode,
                    GARDIFUND_START_WORKER=False, PRODUCTION_API_KEY="unit-test-secret",
                    SETUPS_PASSWORD_HASH=TEST_PASSWORD_HASH,
                    CF_ACCESS_TEAM_DOMAIN="https://example.cloudflareaccess.com", CF_ACCESS_AUD="audience-123")


@pytest.fixture
def setup(tmp_path):
    app = create_app(config(tmp_path), adapter=FakeTradier())
    with TestClient(app, base_url="http://localhost:8765", client=("127.0.0.1", 1234)) as client:
        yield app, client


def mutate(client, method, path, **kwargs):
    headers = {"Origin": "http://localhost:8765", "X-Gardifund-Request": "1", **kwargs.pop("headers", {})}
    return client.request(method, path, headers=headers, **kwargs)


def prepare_shadow(app, amount=1000):
    with app.state.db.connect() as conn:
        for table in ('shadow_positions','shadow_orders','shadow_performance','shadow_daily_performance','shadow_pending','shadow_exit_limits','shadow_settlements'):
            conn.execute(f'DELETE FROM {table}')
        initial=shadow_module.cents(amount)
        conn.execute('UPDATE shadow_state SET cash_cents=?,initial_cents=?,initialized=1 WHERE id=1',(initial,initial))
        shadow_module.record_performance(conn)


def test_live_shadow_isolation_and_transactional_orders(setup):
    app, client = setup
    sync_accounts(app.state.db, FakeTradier())
    live_before = client.get('/api/overview?mode=live').json()
    assert live_before["accounts"][0]["masked_id"] == "••••1234"
    assert "ACCT1234" not in json.dumps(live_before)
    prepare_shadow(app)
    shadow_module.place(app.state.db, 'AAPL', 'buy', 2, 100)
    shadow_module.place(app.state.db, 'AAPL', 'sell', 1, 120)
    data = client.get('/api/overview?mode=shadow').json()
    assert data["state"]["cash_cents"] == 92000
    assert data["positions"][0]["quantity"] == 1
    assert data["positions"][0]["cost_cents"] == 10000
    assert data["realized_cents"] == 2000
    assert data["equity_cents"] is None  # no quote was invented
    assert {(row['symbol'], row['mode']) for row in client.get('/api/positions').json()['rows']} == {('AAPL','live'), ('AAPL','shadow')}
    with pytest.raises(ValueError): shadow_module.place(app.state.db, 'AAPL', 'sell', 2, 120)
    with pytest.raises(ValueError): shadow_module.place(app.state.db, 'AAPL', 'buy', 1, 0.001)
    assert client.get('/api/overview?mode=live').json() == live_before
    assert mutate(client, 'POST', '/api/live/orders', json={"symbol":"AAPL"}).status_code in (404, 405)
    assert mutate(client, 'POST', '/api/shadow/initialize', json={"amount": 500}).status_code in (404,405)


def test_queued_live_sync_reads_filled_broker_positions_without_submitting_orders(tmp_path):
    observed = []
    broker_positions = []

    def respond(request):
        observed.append((request.method, request.url.path))
        if request.url.path.endswith('/user/profile'):
            return httpx.Response(200, json={'profile': {'account': {'account_number': 'ACCT1234'}}})
        if request.url.path.endswith('/balances'):
            return httpx.Response(200, json={'balances': {'total_equity': 1000, 'total_cash': 500}})
        if request.url.path.endswith('/positions'):
            return httpx.Response(200, json={'positions': {'position': list(broker_positions)} if broker_positions else 'null'})
        if request.url.path.endswith('/orders'):
            return httpx.Response(200, json={'orders': 'null'})
        return httpx.Response(404)

    adapter = Tradier('test-key', transport=httpx.MockTransport(respond), market_interval=0)
    app = create_app(config(tmp_path), adapter=adapter)
    with TestClient(app, base_url='http://localhost:8765', client=('127.0.0.1', 1234)) as client:
        with app.state.db.connect() as conn:
            conn.execute('UPDATE jobs SET enabled=0')
        prepare_shadow(app)
        shadow_module.place(app.state.db, 'QQQ', 'buy', 1, 100)
        shadow_before = client.get('/api/overview?mode=shadow').json()

        def run_queued_sync():
            assert mutate(client, 'POST', '/api/jobs/account/run').json() == {'queued': 'account'}
            job, started = app.state.worker.claim_due()
            assert job['name'] == 'account'
            app.state.worker.run_claimed(job, started)
            with app.state.db.connect() as conn:
                assert conn.execute("SELECT status FROM jobs WHERE name='account'").fetchone()[0] == 'complete'

        run_queued_sync()
        assert {(p['symbol'], p['mode']) for p in client.get('/api/positions').json()['rows']} == {('QQQ', 'shadow')}
        broker_positions.append({'symbol':'AAPL', 'quantity':2, 'cost_basis':400})
        run_queued_sync()
        assert {(p['symbol'], p['mode']) for p in client.get('/api/positions').json()['rows']} == {('AAPL', 'live'), ('QQQ', 'shadow')}
        assert client.get('/api/overview?mode=shadow').json() == shadow_before
        assert mutate(client, 'POST', '/api/live/orders', json={'symbol':'AAPL'}).status_code in (404, 405)
        assert observed and all(method == 'GET' for method, _ in observed)


def test_local_origin_host_and_secret_boundaries(setup):
    _, client = setup
    assert client.get('/api/state').status_code == 200
    assert 'unit-test-secret' not in client.get('/api/state').text
    assert client.patch('/api/settings', json={"top_n": 20}).status_code == 403
    assert client.patch('/api/settings', headers={"Origin":"http://evil.example", "X-Gardifund-Request":"1"}, json={"top_n": 20}).status_code == 403
    assert client.get('/api/state', headers={"Host":"attacker.example"}).status_code == 403
    for header in ('Cf-Connecting-Ip', 'Cf-Ray', 'Cf-Access-Jwt-Assertion', 'Forwarded', 'X-Forwarded-For', 'X-Forwarded-Host', 'X-Forwarded-Proto'):
        assert client.get('/api/state', headers={header: 'proxied'}).status_code == 403
    assert mutate(client, 'PATCH', '/api/settings', json={"top_n": 10}).status_code == 200


@pytest.mark.parametrize('share_details', [False, True])
def test_cloudflare_jwt_signature_claims_and_read_only_boundary(tmp_path, monkeypatch, share_details):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    class FakeJWKS:
        def __init__(self, *args, **kwargs): pass
        def get_signing_key_from_jwt(self, token): return SimpleNamespace(key=private_key.public_key())
    monkeypatch.setattr('backend.security.PyJWKClient', FakeJWKS)
    class AccountDetails(FakeTradier):
        def accounts(self):
            account = super().accounts()[0]
            account['positions'][0]['account_number'] = 'PRIVATE-POSITION-ID'
            account['orders'] = [{'symbol':'MSFT', 'side':'buy', 'quantity':1, 'status':'filled',
                                  'avg_fill_price':25, 'account_number':'PRIVATE-ORDER-ID', 'id':'PRIVATE-ORDER-ID'}]
            return [account]
    settings = config(tmp_path, "cloudflare")
    assert settings.share_position_details is False
    settings.share_position_details = share_details
    app = create_app(settings, adapter=AccountDetails())
    sync_accounts(app.state.db, AccountDetails())
    shadow = app.state.db
    from backend import shadow as shadow_module
    prepare_shadow(app)
    shadow_module.place(shadow, 'QQQ', 'buy', 1, 100)
    with shadow.connect() as conn:
        conn.execute("INSERT INTO live_setup_drafts(created,symbol,side,quantity,price_cents,note) VALUES(?,?,?,?,?,?)",
                     (time.time(), 'SPY', 'buy', 1, 10000, 'PRIVATE-DRAFT-NOTE'))
    store_holdings(shadow, '2024-01-02', [{'symbol':'AAPL', 'name':'Apple', 'weight':1, 'sector':'Information Technology'}],
                   'PRIVATE-SOURCE', 'PRIVATE-NOTE')
    app.state.market.write('SPY', '1d', [{'time':'2024-01-02','open':100,'high':102,'low':99,'close':101,'volume':100},
                                         {'time':'2024-01-31','open':103,'high':105,'low':102,'close':104,'volume':100}],
                           'PRIVATE-SOURCE', 'unverified')
    app.state.discovery.scan()
    now = datetime.now(timezone.utc)
    base = {"iss":"https://example.cloudflareaccess.com", "aud":"audience-123", "exp":now + timedelta(minutes=5)}
    def token(**changes): return jwt.encode({**base, **changes}, private_key, algorithm='RS256', headers={"kid":"key-1"})
    host, port = settings.viewing_address
    with TestClient(app, base_url=f'http://{host}:{port}', client=('127.0.0.1', 1234)) as client:
        assert client.get('/healthz').status_code == 200
        assert client.get('/view/state').status_code == 401
        assert client.get('/').status_code == 401
        assert client.get('/assets/example.js').status_code == 401
        assert client.get('/view/state', headers={'Cf-Access-Jwt-Assertion':token()}).status_code == 200
        for bad in (token(aud='wrong'), token(iss='https://wrong.cloudflareaccess.com'), token(exp=now-timedelta(seconds=1))):
            assert client.get('/view/state', headers={'Cf-Access-Jwt-Assertion':bad}).status_code == 401
        assert client.get('/view/state', headers={'Cf-Access-Jwt-Assertion':token(exp=str(int(time.time()) + 300))}).status_code == 401
        assert client.get('/view/state', headers={'Cf-Access-Jwt-Assertion':'x' * 16385}).status_code == 401
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        assert client.get('/view/state', headers={'Cf-Access-Jwt-Assertion':jwt.encode(base, other, algorithm='RS256')}).status_code == 401
        auth = {'Cf-Access-Jwt-Assertion': token()}
        assert client.get('/scope', headers=auth).json()['access_scope'] == 'remote'
        assert client.get('/api/overview', headers=auth).status_code == 403
        assert client.get('/view/backups', headers=auth).status_code == 403
        assert client.get('/view/setup-access', headers=auth).status_code == 403
        assert client.get('/view/setups/live/drafts', headers=auth).status_code == 403
        assert client.get('/view/bars?symbol=QQQ', headers=auth).status_code == 403
        assert client.get('/view/market/quality?symbol=QQQ', headers=auth).status_code == 403
        assert client.get('/view/state', headers={'Host': 'localhost:8765'}).status_code == 401
        assert client.get('/api/state', headers={**auth, 'Host': 'localhost:8765'}).status_code == 403
        live = client.get('/view/overview?mode=live', headers=auth)
        assert live.status_code == 200 and live.json()['summary']['equity'] == 5500
        assert live.json()['summary']['open_positions'] == 1 and live.json()['summary']['order_count'] == 1
        simulated = client.get('/view/overview?mode=shadow', headers=auth)
        assert simulated.json()['summary']['cash'] == 900
        assert simulated.json()['summary']['open_positions'] == 1 and simulated.json()['summary']['order_count'] == 1
        if share_details:
            assert live.json()['positions'][0]['symbol'] == 'AAPL' and live.json()['orders'][0]['symbol'] == 'MSFT'
            assert simulated.json()['positions'][0]['symbol'] == 'QQQ'
        else:
            for response in (live, simulated):
                assert {'positions', 'orders'}.isdisjoint(response.json())
                assert all(symbol not in response.text for symbol in ('AAPL', 'MSFT', 'QQQ'))
        portfolio = client.get('/view/positions', headers=auth)
        assert portfolio.status_code == 403
        for path in ('/view/discovery','/view/database','/view/market/quality?symbol=SPY','/view/settings','/view/setup-access','/view/positions'):
            assert client.get(path, headers=auth).status_code == 403
        ranking = client.get('/view/chart-performance?mode=market&year=2024&month=1&top_n=5', headers=auth)
        assert ranking.json()['rows'][0]['return_pct'] == pytest.approx((104 / 101 - 1) * 100)
        assert client.get('/view/chart-performance?mode=market&year=2024&top_n=6', headers=auth).status_code == 422
        assert client.get('/view/chart-performance?mode=market&year=2024').status_code == 401
        views = (live, simulated, ranking, client.get('/view/state', headers=auth), client.get('/view/bars', headers=auth))
        for response in views:
            assert response.status_code == 200
            assert all(secret not in response.text for secret in ('ACCT1234', 'PRIVATE-POSITION-ID', 'PRIVATE-ORDER-ID',
                                                                   'PRIVATE-SOURCE', 'PRIVATE-NOTE', 'PRIVATE-DRAFT-NOTE',
                                                                   'unit-test-secret', 'unit-test-pin', TEST_PASSWORD_HASH))
        for method, path in [('POST','/api/jobs/account/run'), ('POST','/api/shadow/orders'),
                             ('PATCH','/api/settings'), ('POST','/api/backups'), ('POST','/api/import/bars'),
                             ('DELETE','/api/setups/AAPL'), ('POST','/api/setup-access/unlock'),
                             ('POST','/api/setups/live/drafts'), ('POST','/view/overview'), ('PATCH','/view/state')]:
            assert client.request(method, path, headers={**auth, 'Origin':'https://fund.example', 'X-Gardifund-Request':'1'}).status_code == 403


def test_cloudflare_mode_fails_closed_when_unconfigured(tmp_path):
    settings = Settings(_env_file=None, GARDIFUND_DATA_DIR=tmp_path, GARDIFUND_AUTH_MODE='cloudflare',
                        GARDIFUND_START_WORKER=False, CF_ACCESS_TEAM_DOMAIN='', CF_ACCESS_AUD='')
    app = create_app(settings, adapter=FakeTradier())
    with TestClient(app, base_url='http://127.0.0.1:8766', client=('127.0.0.1',1234)) as client:
        assert client.get('/healthz').status_code == 200
        assert client.get('/view/state').status_code == 503
        assert client.get('/').status_code == 503
    with TestClient(app, base_url='http://localhost:8765', client=('127.0.0.1',1234)) as client:
        assert client.get('/api/state').status_code == 200


def test_actual_listening_sockets_enforce_separate_scopes(tmp_path, monkeypatch):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_lookup_started, release_lookup = threading.Event(), threading.Event()
    class FakeJWKS:
        def __init__(self, *args, **kwargs): pass
        def get_signing_key_from_jwt(self, token):
            if jwt.get_unverified_header(token).get('kid') == 'slow':
                key_lookup_started.set()
                release_lookup.wait(5)
            return SimpleNamespace(key=private_key.public_key())
    monkeypatch.setattr('backend.security.PyJWKClient', FakeJWKS)
    sockets = [socket.create_server(('127.0.0.1', 0)) for _ in range(2)]
    for sock in sockets: sock.setblocking(False)
    local_port, remote_port = (sock.getsockname()[1] for sock in sockets)
    settings = Settings(_env_file=None, GARDIFUND_DATA_DIR=tmp_path, GARDIFUND_AUTH_MODE='cloudflare',
                        GARDIFUND_START_WORKER=False, GARDIFUND_LOCAL_PORT=local_port, GARDIFUND_REMOTE_PORT=remote_port,
                        CF_ACCESS_TEAM_DOMAIN='https://example.cloudflareaccess.com', CF_ACCESS_AUD='audience-123')
    app = create_app(settings, adapter=FakeTradier())
    server = uvicorn.Server(uvicorn.Config(app, workers=1, proxy_headers=False, access_log=False, log_level='warning'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': sockets}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.02)
        assert server.started
        token = jwt.encode({'iss':'https://example.cloudflareaccess.com', 'aud':'audience-123',
                            'exp':datetime.now(timezone.utc) + timedelta(minutes=5)}, private_key, algorithm='RS256')
        with httpx.Client(trust_env=False, timeout=5) as client:
            local = f'http://127.0.0.1:{local_port}'
            remote = f'http://127.0.0.1:{remote_port}'
            assert client.get(local + '/scope').json()['access_scope'] == 'local'
            assert client.get(local + '/api/state').status_code == 200
            assert client.get(local + '/api/state', headers={'Cf-Connecting-Ip':'192.0.2.1'}).status_code == 403
            assert client.get(remote + '/scope').status_code == 401
            auth = {'Cf-Access-Jwt-Assertion':token, 'Host':f'localhost:{local_port}'}
            assert client.get(remote + '/scope', headers=auth).json()['access_scope'] == 'remote'
            assert client.get(remote + '/view/state', headers=auth).status_code == 200
            assert client.get(remote + '/api/state', headers=auth).status_code == 403
            assert client.post(remote + '/view/overview', headers=auth).status_code == 403
            short = jwt.encode({'iss':'https://example.cloudflareaccess.com', 'aud':'audience-123',
                                'exp':int(time.time()) + 3}, private_key, algorithm='RS256')
            started = time.monotonic()
            with client.stream('GET', remote + '/view/events', headers={'Cf-Access-Jwt-Assertion':short}) as response:
                assert response.status_code == 200
                assert ': connected' in list(response.iter_lines())
            assert .5 < time.monotonic() - started < 5
            slow = jwt.encode({'iss':'https://example.cloudflareaccess.com', 'aud':'audience-123',
                               'exp':int(time.time()) + 300}, private_key, algorithm='RS256', headers={'kid':'slow'})
            results = []
            def slow_remote():
                with httpx.Client(trust_env=False, timeout=8) as other:
                    results.append(other.get(remote + '/view/state', headers={'Cf-Access-Jwt-Assertion':slow}).status_code)
            request_thread = threading.Thread(target=slow_remote)
            request_thread.start()
            try:
                assert key_lookup_started.wait(4)
                # A network-bound JWT key lookup must not stall local controls.
                assert client.get(local + '/api/state', timeout=1).status_code == 200
            finally:
                release_lookup.set()
                request_thread.join(timeout=8)
            assert not request_thread.is_alive() and results == [200]
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        for sock in sockets: sock.close()
        assert not thread.is_alive()


def test_tradier_adapter_handles_null_singletons_and_batches():
    observed = []
    def responder(request):
        observed.append((request.method, request.url.path, request.headers.get('Authorization')))
        path = request.url.path
        if path.endswith('/user/profile'):
            return httpx.Response(200, json={'profile':{'account':{'account_number':'ACCT1234'}}})
        if path.endswith('/balances'): return httpx.Response(200, json={'balances':{'total_cash':42}})
        if path.endswith('/positions'): return httpx.Response(200, json={'positions':'null'})
        if path.endswith('/orders'): return httpx.Response(200, json={'orders':'null'})
        if path.endswith('/quotes'): return httpx.Response(200, json={'quotes':{'quote':{'symbol':'SPY','last':100}}})
        return httpx.Response(404)
    adapter = Tradier('secret', transport=httpx.MockTransport(responder), market_interval=0)
    assert adapter.accounts()[0]['positions'] == []
    assert len(adapter.quotes([f'S{i}' for i in range(201)])) == 3
    assert all(m == 'GET' and h == 'Bearer secret' for m, _, h in observed)


def test_adjustment_comparison_excludes_duplicates_and_reports_missing_dates():
    primary = [{'time':'2024-01-02','close':100}, {'time':'2024-01-04','close':105},
               {'time':'2024-01-05','close':200}]
    reference = [('2024-01-02',100,90), ('2024-01-03',42,40), ('2024-01-04',100,100),
                 ('2024-01-05',200,190), ('2024-01-05',201,191), ('bad-date',1,1)]
    result = compare_daily_closes(primary, reference)
    assert result['matched_dates'] == 2 and result['duplicate_reference_dates'] == 1
    assert result['primary_without_reference'] == result['reference_without_primary'] == 1
    assert result['close']['count'] == 2 and result['close']['within_001_pct'] == 1
    assert result['close']['median_difference_pct'] == pytest.approx(2.5)
    assert result['outliers'][0]['date'] == '2024-01-04'
    assert result['outliers'][0]['difference_pct'] == pytest.approx(5)
    assert result['adjusted_close']['median_difference_pct'] == pytest.approx(8.055556)
    assert result['adjusted_close']['within_001_pct'] == 0


def test_adjustment_comparison_reports_dividend_date_association():
    primary = [{'time':'2024-01-02','close':100}, {'time':'2024-01-03','close':103},
               {'time':'2024-01-04','close':104}]
    reference = [('2024-01-02',100,90), ('2024-01-03',100,90), ('2024-01-04',104,94)]
    result = compare_daily_closes(primary, reference, dividend_dates={'2024-01-04'})
    assert result['outliers_before_dividend'] == 1
    assert result['outliers'][0]['date'] == '2024-01-03'


def test_yahoo_reference_excludes_ambiguous_and_invalid_actions(monkeypatch):
    import pandas as pd
    import yfinance as yf
    frame = pd.DataFrame({'Close':[100,101,101,102], 'Adj Close':[90,91,91,92],
                          'Dividends':[.5,.7,.7,float('nan')], 'Stock Splits':[0,0,0,-1]},
                         index=pd.to_datetime(['2024-01-02','2024-01-03','2024-01-03','2024-01-04']))
    class Ticker:
        def history(self, **kwargs): return frame
    monkeypatch.setattr(yf, 'Ticker', lambda symbol: Ticker())
    result = yahoo_daily_reference('2024-01-01', '2024-01-05')
    assert result['actions'] == [{'date':'2024-01-02','action':'dividend','value':.5}]
    assert result['duplicate_action_dates'] == 1 and result['invalid_action_values'] == 2
    frame = frame[['Close']]
    assert yahoo_daily_reference('2024-01-01', '2024-01-05')['actions'] is None


def test_adjustment_audit_is_non_destructive_and_detects_changed_primary(setup, monkeypatch):
    app, client = setup
    market = app.state.market
    bars = [{'time':'2024-01-02','open':100,'high':102,'low':99,'close':101,'volume':100},
            {'time':'2024-01-03','open':101,'high':103,'low':100,'close':102,'volume':200}]
    market.write('SPY', '1d', bars, 'Tradier', 'unverified')
    before = market.read('SPY', '1d')
    with app.state.db.connect() as conn:
        conn.execute("INSERT INTO corporate_actions VALUES('SPY','2024-01-03','dividend',2.0,'Licensed source')")
        conn.execute("INSERT INTO corporate_actions VALUES('SPY','2024-01-02','split',4.0,'Yahoo Finance via yfinance')")
    monkeypatch.setattr('backend.market.yahoo_daily_reference', lambda start,end: {
        'prices':[('2024-01-02',101,90), ('2024-01-03',102,92)],
        'actions':[{'date':'2024-01-02','action':'dividend','value':.5},
                   {'date':'2024-01-03','action':'dividend','value':1}],
        'duplicate_action_dates':0,'invalid_action_values':0})
    result = market.audit_spy_daily()
    assert result['matched_dates'] == 2 and result['close']['within_001_pct'] == 2
    assert result['reference_dividends'] == 2 and result['actions_added'] == 1 and result['action_conflicts'] == 1
    assert result['prior_actions_unconfirmed'] == 1
    with app.state.db.connect() as conn:
        preserved = conn.execute("SELECT value,source FROM corporate_actions WHERE symbol='SPY' AND ex_date='2024-01-03'").fetchone()
        assert preserved['value'] == 2 and preserved['source'] == 'Licensed source'
        assert conn.execute("SELECT COUNT(*) FROM corporate_actions WHERE symbol='SPY' AND action='split'").fetchone()[0] == 1
        assert conn.execute("SELECT status FROM imports WHERE dataset='Corporate actions' ORDER BY id DESC LIMIT 1").fetchone()[0] == 'partial'
    repeated = market.audit_spy_daily()
    assert repeated['actions_added'] == 0 and repeated['actions_existing'] == 2 and repeated['prior_actions_unconfirmed'] == 1
    assert market.read('SPY', '1d') == before
    audit = client.get('/api/database').json()['audit']
    assert audit['reference'] == 'Yahoo Finance via yfinance' and not audit['stale']
    assert next(row for row in client.get('/api/database').json()['catalog'] if row['dataset']=='Corporate actions')['status'] == 'Latest import needs review'
    with app.state.db.connect() as conn:
        conn.execute("UPDATE jobs SET next_run=? WHERE name='adjustments'", (time.time() + 86400,))
    def changing_reference(start, end):
        market.write('SPY', '1d', [{'time':'2024-01-04','open':102,'high':104,'low':101,'close':103,'volume':100}],
                     'Tradier', 'unverified')
        return {'prices':[('2024-01-02',101,90), ('2024-01-03',102,92)],
                'actions':[{'date':'2024-01-04','action':'dividend','value':1}],
                'duplicate_action_dates':0,'invalid_action_values':0}
    monkeypatch.setattr('backend.market.yahoo_daily_reference', changing_reference)
    with pytest.raises(Exception, match='changed during the audit'):
        market.audit_spy_daily()
    stale = client.get('/api/database').json()['audit']
    assert stale['stale'] and stale['audited'] == audit['audited']
    with app.state.db.connect() as conn:
        assert conn.execute("SELECT next_run FROM jobs WHERE name='adjustments'").fetchone()[0] <= time.time() + 6
    assert mutate(client, 'POST', '/api/jobs/adjustments/run').status_code == 200


def test_tradier_share_class_symbols_round_trip():
    seen = []
    def responder(request):
        seen.append(request.url.params.get('symbols'))
        return httpx.Response(200, json={'quotes':{'quote':{'symbol':'BRK/B','last':100}}})
    adapter = Tradier('secret', transport=httpx.MockTransport(responder), market_interval=0)
    assert adapter.quotes(['BRK.B'])[0]['symbol'] == 'BRK.B'
    assert seen == ['BRK/B']


def test_tradier_preferred_share_symbols_round_trip():
    seen = []
    def responder(request):
        seen.append(request.url.params.get('symbols'))
        return httpx.Response(200, json={'quotes':{'quote':{'symbol':"ABR'D",'last':20}}})
    adapter = Tradier('secret', transport=httpx.MockTransport(responder), market_interval=0)
    assert adapter.quotes(['ABR$D'])[0]['symbol'] == 'ABR$D'
    assert seen == ["ABR'D"]
    adapter.close()


def test_last_trade_time_accepts_provider_seconds_and_milliseconds():
    assert trade_timestamp(1762982100011) == 1762982100
    assert trade_timestamp(1762982100) == 1762982100
    assert trade_timestamp(0) is None and trade_timestamp(float('nan')) is None


def test_schedule_persistence_and_recovery_lock(tmp_path):
    app = create_app(config(tmp_path), adapter=FakeTradier())
    with TestClient(app, base_url='http://localhost:8765', client=('127.0.0.1',1234)) as client:
        assert mutate(client, 'PATCH', '/api/settings', json={'discovery_minutes':45,'account_minutes':20}).status_code == 200
    app2 = create_app(config(tmp_path), adapter=FakeTradier())
    assert app2.state.db.settings()['discovery_minutes'] == 45
    with app2.state.db.connect() as conn:
        assert conn.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
        assert conn.execute("SELECT interval_seconds FROM jobs WHERE name='discovery'").fetchone()[0] == 2700
        conn.execute("UPDATE jobs SET enabled=0")
        conn.execute("UPDATE jobs SET status='running' WHERE name='discovery'")
    worker = Worker(app2.state.db, FakeTradier(), app2.state.market)
    worker.start()
    try:
        with app2.state.db.connect() as conn:
            recovered = conn.execute("SELECT status,message FROM jobs WHERE name='discovery'").fetchone()
        assert recovered['status'] == 'idle' and 'Recovered' in recovered['message']
        with pytest.raises(RuntimeError):
            Worker(app2.state.db, FakeTradier(), app2.state.market).start()
    finally:
        worker.stop()


def test_worker_runs_due_job_without_browser(tmp_path):
    db = Database(tmp_path/'data.sqlite3')
    market = MarketStore(db, tmp_path)
    with db.connect() as conn:
        conn.execute("UPDATE jobs SET enabled=0")
        conn.execute("UPDATE jobs SET enabled=1,next_run=? WHERE name='discovery'", (time.time()-1,))
    worker = Worker(db, FakeTradier(), market)
    worker.execute = lambda name: 'background job ran'
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with db.connect() as conn:
                job = conn.execute("SELECT status,message,next_run FROM jobs WHERE name='discovery'").fetchone()
            if job['status'] == 'complete':
                break
            time.sleep(.05)
        assert job['status'] == 'complete' and job['message'] == 'background job ran'
        assert job['next_run'] > time.time()
    finally:
        worker.stop()


def test_sector_intraday_jobs_are_bounded_persisted_and_queueable(setup):
    app, client = setup
    state = client.get('/api/state').json()
    assert state['sector_history']['total'] == 22
    assert {j['name'] for j in state['jobs']} == {'account','discovery','holdings','market','adjustments','portfolio'}
    response = mutate(client, 'POST', '/api/market/sector-history', json={'interval':'1h','symbols':['XLC','XLF']})
    assert response.status_code == 200 and set(response.json()['queued']) == {'XLC','XLF'}
    assert mutate(client, 'POST', '/api/market/sector-history', json={'interval':'1h','symbols':['QQQ']}).status_code == 400
    with app.state.db.connect() as conn:
        queued = conn.execute("SELECT next_run FROM jobs WHERE name='sector_intraday:XLC:1h'").fetchone()[0]
    assert queued <= time.time() + 1

    class MarketRecorder:
        def __init__(self): self.calls = []
        def refresh_yahoo(self, symbol, interval):
            self.calls.append((symbol, interval))
            return {'count': 12}
    market = MarketRecorder()
    worker = Worker(app.state.db, FakeTradier(), market)
    assert '12 bars' in worker.execute('sector_intraday:XLC:1h')
    assert market.calls == [('XLC','1h')]
    with pytest.raises(ValueError):
        worker.execute('sector_intraday:QQQ:1h')


def test_importing_application_module_does_not_create_user_data(tmp_path):
    target = tmp_path/'must-not-be-created'
    environment = {**os.environ, 'GARDIFUND_DATA_DIR': str(target), 'GARDIFUND_START_WORKER':'false'}
    subprocess.run([sys.executable, '-c', 'import backend.main'], check=True, env=environment, capture_output=True, text=True)
    assert not target.exists()


def test_validated_import_and_backup(setup):
    app, client = setup
    payload = 'time,open,high,low,close,volume\n2000-01-03,10,12,9,11,100\n2000-01-04,11,13,10,12,200\n'
    form = {'symbol':'SPY','interval':'1d','source':'Licensed provider','adjustment':'split_dividend_adjusted','replace':'false'}
    result = mutate(client,'POST','/api/import/bars',data=form,files={'file':('bars.csv',payload,'text/csv')})
    assert result.status_code == 200
    assert client.get('/api/bars?symbol=SPY&interval=1d').json()['coverage']['row_count'] == 2
    bad = 'time,open,high,low,close,volume\n2000-01-05,11,10,9,12,200\n'
    assert mutate(client,'POST','/api/import/bars',data=form,files={'file':('bad.csv',bad,'text/csv')}).status_code == 400
    assert client.get('/api/database').json()['imports'][0]['status'] == 'failed'
    assert client.get('/api/bars?symbol=SPY&interval=1d').json()['coverage']['row_count'] == 2
    form['source'] = 'Different source'
    assert mutate(client,'POST','/api/import/bars',data=form,files={'file':('bars.csv',payload,'text/csv')}).status_code == 400
    assert mutate(client,'POST','/api/setup-access/unlock',json={'password':'unit-test-pin'}).status_code == 200
    with app.state.db.connect() as conn:
        conn.execute("INSERT INTO live_setup_drafts(created,symbol,side,quantity,price_cents,note) VALUES(?,?,?,?,?,?)",
                     (time.time(), 'SPY', 'buy', 1, 10000, 'Keep this draft'))
    cookie = client.cookies.get('gardifund_live_setup')
    actions = 'symbol,ex_date,action,value\nSPY,2024-01-02,dividend,0.5\n'
    assert mutate(client,'POST','/api/import/actions',data={'source':'licensed'},files={'file':('actions.csv',actions,'text/csv')}).status_code == 200
    backup = mutate(client,'POST','/api/backups').json()
    with zipfile.ZipFile(app.state.db.path.parent / 'backups' / backup['name']) as archive:
        names = archive.namelist()
        assert 'gardifund.sqlite3' in names and any(n.startswith('market/1d/SPY-') and n.endswith('.parquet') for n in names)
        assert not any('.env' in name for name in names)
        database_bytes = archive.read('gardifund.sqlite3')
        assert all(secret not in database_bytes for secret in (b'unit-test-pin', b'unit-test-secret', cookie.encode()))
        restored = app.state.db.path.parent / 'restore-test'
        archive.extractall(restored)
    restored_store = MarketStore(Database(restored/'gardifund.sqlite3'), restored)
    assert restored_store.read('SPY','1d')['bars'][-1]['close'] == 12
    with restored_store.db.connect() as conn:
        assert conn.execute('SELECT note FROM live_setup_drafts').fetchone()[0] == 'Keep this draft'
        assert conn.execute("SELECT COUNT(*) FROM corporate_actions WHERE symbol='SPY'").fetchone()[0] == 1


def test_corporate_actions_import_validation(setup):
    _, client = setup
    content = 'symbol,ex_date,action,value\nAAA,2024-01-02,split,2\nAAA,2024-02-02,dividend,0.5\n'
    response = mutate(client, 'POST', '/api/import/actions', data={'source':'licensed'}, files={'file':('actions.csv',content,'text/csv')})
    assert response.status_code == 200 and response.json()['count'] == 2
    bad = 'symbol,ex_date,action,value\nAAA,2024-01-02,split,-1\n'
    assert mutate(client, 'POST', '/api/import/actions', data={'source':'licensed'}, files={'file':('actions.csv',bad,'text/csv')}).status_code == 400
    assert client.get('/api/database').json()['counts']['corporate_actions'] == 2


def test_corporate_action_pagination_preserves_same_date_events(setup):
    app, client = setup
    with app.state.db.connect() as conn:
        conn.executemany('INSERT INTO corporate_actions VALUES(?,?,?,?,?)', [
            ('SPY','2024-01-04','split',2,'source'), ('SPY','2024-01-04','dividend',.5,'source'),
            ('SPY','2024-01-03','dividend',.4,'source')])
    path = '/api/database/actions?symbol=SPY&limit=1'
    seen = []
    for _ in range(3):
        result = client.get(path).json()
        seen.extend((row['ex_date'], row['action']) for row in result['rows'])
        cursor = result['next_cursor']
        if cursor:
            path = f"/api/database/actions?symbol=SPY&limit=1&before_date={cursor['date']}&before_action={cursor['action']}"
    assert seen == [('2024-01-04','split'), ('2024-01-04','dividend'), ('2024-01-03','dividend')]
    assert cursor is None
    assert client.get('/api/database/actions?before_date=2024-01-04').status_code == 400


def test_windows_javascript_mime_type(tmp_path, monkeypatch):
    dist = tmp_path/'dist'; dist.mkdir(); (dist/'app.js').write_text('export const a = 1;', encoding='utf-8')
    monkeypatch.setattr('backend.main.ROOT', tmp_path)
    app = create_app(config(tmp_path/'data'), adapter=FakeTradier())
    with TestClient(app, base_url='http://localhost:8765', client=('127.0.0.1',1234)) as client:
        response = client.get('/app.js')
        assert response.status_code == 200
        assert response.headers['content-type'].startswith('text/javascript')
        assert response.headers['x-content-type-options'] == 'nosniff'


def test_attribution_requires_dated_weights_and_adjusted_prices(setup):
    app, client = setup
    rows = [{'symbol':'AAA','name':'A','sector':'Information Technology','weight':.5},
            {'symbol':'BBB','name':'B','sector':'Information Technology','weight':.5}]
    store_holdings(app.state.db, '2024-01-02', rows, 'licensed dated weights')
    def bars(a,b): return [{'time':'2024-01-02','open':a,'high':a,'low':a,'close':a,'volume':1},
                           {'time':'2024-01-03','open':b,'high':b,'low':b,'close':b,'volume':1}]
    app.state.market.write('AAA','1d',bars(100,120),'licensed','split_dividend_adjusted')
    partial = client.get('/api/attribution?start=2024-01-02&end=2024-01-03').json()
    assert partial['complete'] is False and partial['missing'] == 1
    assert partial['rows'][0]['contribution'] == pytest.approx(.1)
    app.state.market.write('BBB','1d',bars(100,90),'licensed','unverified')
    assert client.get('/api/attribution?start=2024-01-02&end=2024-01-03').json()['missing'] == 1
    app.state.market.write('BBB','1d',bars(100,90),'licensed','split_dividend_adjusted',replace=True)
    complete = client.get('/api/attribution?start=2024-01-02&end=2024-01-03').json()
    assert complete['complete'] is True and complete['missing'] == 0
    assert client.get('/api/attribution?start=2024-01-01&end=2024-01-03').json()['complete'] is False


def test_invalid_provider_bars_are_quarantined_and_repaired(tmp_path):
    db = Database(tmp_path/'data.sqlite3'); market = MarketStore(db,tmp_path)
    class Provider:
        def __init__(self): self.bad = True
        def daily(self,*args):
            return [{'date':'2000-01-03','open':10,'high':12,'low':9,'close':11,'volume':100},
                    {'date':'2000-01-04','open':10,'high':10 if self.bad else 12,'low':9,'close':11,'volume':100}]
    provider=Provider()
    result = market.refresh_daily(provider,'SPY')
    assert result['count'] == 1 and result['anomaly_count'] == 1
    assert len(market.read('SPY','1d')['bars']) == 1
    provider.bad=False
    result = market.refresh_daily(provider,'SPY')
    assert result['count'] == 2 and result['anomaly_count'] == 0
    assert len(market.read('SPY','1d')['bars']) == 2  # cached pre-repair data was invalidated


def test_quality_endpoint_exposes_missing_sessions_and_quarantined_source_rows(setup):
    app, client = setup
    class Provider:
        def daily(self, *args):
            return [{'date':'2024-01-02','open':10,'high':12,'low':9,'close':11,'volume':100},
                    {'date':'2024-01-03','open':10,'high':10,'low':9,'close':11,'volume':100},
                    {'date':'2024-01-04','open':11,'high':12,'low':10,'close':11,'volume':120}]
    app.state.market.refresh_daily(Provider(),'SPY')
    response = client.get('/api/market/quality?symbol=SPY&interval=1d')
    assert response.status_code == 200
    quality = response.json()
    assert quality['coverage']['missing_sessions'] == 1
    assert quality['missing_dates'] == ['2024-01-03']
    assert quality['anomalies'][0]['stamp'] == '2024-01-03'
    assert quality['anomalies'][0]['raw']['high'] == 10
    assert 'path' not in quality['coverage']


def test_intraday_history_pages_are_exclusive_and_cover_all_stored_bars(setup):
    app, client = setup
    rows = [{'time':f'2024-01-02T{hour:02d}:30:00Z','open':10,'high':12,'low':9,'close':11,'volume':100}
            for hour in range(14, 20)]
    app.state.market.write('SPY','1h',rows,'licensed history','unverified')
    first = client.get('/api/bars?symbol=SPY&interval=1h&limit=2').json()
    assert [bar['time'] for bar in first['bars']] == [rows[4]['time'], rows[5]['time']]
    assert first['has_more'] and first['next_before'] == rows[4]['time']
    second = client.get('/api/bars', params={'symbol':'SPY','interval':'1h','limit':2,'before':first['next_before']}).json()
    third = client.get('/api/bars', params={'symbol':'SPY','interval':'1h','limit':2,'before':second['next_before']}).json()
    assert [bar['time'] for bar in third['bars'] + second['bars'] + first['bars']] == [row['time'] for row in rows]
    assert not third['has_more'] and third['next_before'] is None


def test_failed_market_commit_preserves_previous_generation(tmp_path):
    db = Database(tmp_path/'data.sqlite3'); market = MarketStore(db,tmp_path)
    old = [{'time':'2024-01-02','open':10,'high':10,'low':10,'close':10,'volume':1}]
    new = [{'time':'2024-01-02','open':20,'high':20,'low':20,'close':20,'volume':1}]
    market.write('SPY','1d',old,'old source','unverified')
    with db.connect() as conn:
        original = conn.execute("SELECT path FROM market_files WHERE symbol='SPY'").fetchone()[0]
        conn.execute("CREATE TRIGGER block_market_update BEFORE UPDATE ON market_files BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        market.write('SPY','1d',new,'new source','split_dividend_adjusted',replace=True)
    with db.connect() as conn:
        row = conn.execute("SELECT path,source,adjustment FROM market_files WHERE symbol='SPY'").fetchone()
    assert row['path'] == original and row['source'] == 'old source' and row['adjustment'] == 'unverified'
    assert market.read('SPY','1d')['bars'][0]['close'] == 10
    assert len(list((tmp_path/'market'/'1d').glob('SPY-*.parquet'))) == 1


def test_live_setup_unlock_is_local_expiring_and_cannot_create_positions(setup):
    app, client = setup
    now = [1000.0]
    app.state.setup_lock.clock = lambda: now[0]
    body = {'symbol':'SPY','side':'buy','quantity':2,'price':100,'note':'Local draft'}
    with app.state.db.connect() as conn:
        conn.execute("INSERT INTO live_setup_drafts(created,symbol,side,quantity,price_cents,note) VALUES(?,?,?,?,?,?)",
                     (time.time(), 'SPY', 'buy', 1, 10000, 'Earlier local draft'))
    assert client.get('/api/setup-access').json()['unlocked'] is False
    assert client.get('/api/setups/live').status_code == 403
    assert mutate(client, 'POST', '/api/setups/live/drafts', json=body).status_code in (404, 405)
    assert client.post('/api/setup-access/unlock', headers={'Origin':'http://evil.example','X-Gardifund-Request':'1'}, json={'password':'unit-test-pin'}).status_code == 403
    assert mutate(client, 'POST', '/api/setup-access/unlock', json={'password':'wrong'}).status_code == 401
    response = mutate(client, 'POST', '/api/setup-access/unlock', json={'password':'unit-test-pin'})
    assert response.status_code == 200 and 'unit-test-pin' not in response.text
    assert 'HttpOnly' in response.headers['set-cookie'] and 'SameSite=strict' in response.headers['set-cookie']
    assert client.get('/api/setup-access').json()['expires_in'] == 900
    before_live = client.get('/api/overview?mode=live').json()
    before_shadow = client.get('/api/overview?mode=shadow').json()
    assert mutate(client, 'POST', '/api/setups/live/drafts', json=body).status_code in (404, 405)
    with app.state.db.connect() as conn:
        assert conn.execute('SELECT note FROM live_setup_drafts').fetchone()[0] == 'Earlier local draft'
    assert client.get('/api/positions').json()['rows'] == []
    assert client.get('/api/overview?mode=live').json() == before_live
    assert client.get('/api/overview?mode=shadow').json() == before_shadow
    assert mutate(client, 'POST', '/api/live/orders', json=body).status_code in (404, 405)
    now[0] += 901
    assert client.get('/api/setup-access').json()['unlocked'] is False
    assert client.get('/api/setups/live').status_code == 403


def test_live_setup_unlock_limits_failed_attempts_and_can_lock(setup):
    app, client = setup
    now = [1000.0]
    app.state.setup_lock.clock = lambda: now[0]
    for _ in range(5):
        assert mutate(client, 'POST', '/api/setup-access/unlock', json={'password':'wrong'}).status_code == 401
    assert mutate(client, 'POST', '/api/setup-access/unlock', json={'password':'unit-test-pin'}).status_code == 429
    now[0] += 61
    assert mutate(client, 'POST', '/api/setup-access/unlock', json={'password':'unit-test-pin'}).status_code == 200
    assert mutate(client, 'POST', '/api/setup-access/lock').status_code == 200
    assert client.get('/api/setups/live').status_code == 403


def test_live_setup_fails_closed_without_password_and_unlock_does_not_survive_restart(tmp_path):
    settings = config(tmp_path)
    settings.setups_password_hash = ''
    app = create_app(settings, adapter=FakeTradier())
    with TestClient(app, base_url='http://localhost:8765', client=('127.0.0.1',1234)) as client:
        assert mutate(client, 'POST', '/api/setup-access/unlock', json={'password':'anything'}).status_code == 503
    settings.setups_password_hash = TEST_PASSWORD_HASH
    first = create_app(settings, adapter=FakeTradier())
    with TestClient(first, base_url='http://localhost:8765', client=('127.0.0.1',1234)) as client:
        assert mutate(client, 'POST', '/api/setup-access/unlock', json={'password':'unit-test-pin'}).status_code == 200
        cookie = client.cookies.get('gardifund_live_setup')
        assert cookie and client.get('/api/setup-access').json()['unlocked']
    restarted = create_app(settings, adapter=FakeTradier())
    with TestClient(restarted, base_url='http://localhost:8765', client=('127.0.0.1',1234)) as client:
        client.cookies.set('gardifund_live_setup', cookie, domain='localhost.local', path='/api')
        assert client.get('/api/setups/live').status_code == 403


def test_daily_coverage_uses_exchange_sessions():
    rows = [{'time':'2024-01-02'}, {'time':'2024-01-04'}]
    assert daily_coverage(rows) == (1, 0)
    # The intervening New Year holiday is not reported as a missing session.
    assert daily_coverage([{'time':'2023-12-29'}, {'time':'2024-01-02'}]) == (0, 0)


def test_intraday_grid_respects_early_closes_daylight_saving_and_partial_edges():
    stamps = [f'2024-11-27T{hour:02d}:30:00Z' for hour in range(14, 21)]
    stamps += [f'2024-11-29T{hour:02d}:30:00Z' for hour in range(14, 17)]
    stamps += ['2024-11-29T17:00:00Z', '2024-12-02T14:30:00Z']
    report = intraday_gaps(stamps, '1h')
    assert report['checked']
    assert report['missing_bars'] == ['2024-11-29T17:30:00Z']
    assert report['unexpected_bars'] == ['2024-11-29T17:00:00Z']
    assert report['partial_sessions'] == ['2024-11-29'] and report['missing_session_count'] == 0
    dst = intraday_gaps(['2024-03-08T20:30:00Z','2024-03-11T13:30:00Z'], '1h')
    assert dst['checked'] and dst['missing_bar_count'] == dst['unexpected_bar_count'] == 0
    assert not intraday_gaps(['2024-01-02T09:30:00'], '1h')['checked']
    assert not intraday_gaps(['2024-01-02T14:30:00Z'] * 100001, '1h')['checked']


def test_intraday_quality_is_bounded_cached_and_source_aware(setup, monkeypatch):
    app, client = setup
    stamps = [f'2024-11-27T{hour:02d}:30:00Z' for hour in range(14, 21)]
    stamps += [f'2024-11-29T{hour:02d}:30:00Z' for hour in range(14, 17)]
    stamps += ['2024-12-02T14:30:00Z']
    bars = [{'time':stamp,'open':100,'high':101,'low':99,'close':100,'volume':1} for stamp in stamps]
    app.state.market.write('SPY','1h',bars,'Yahoo Finance via yfinance','unverified')
    import backend.market as market_module
    original, calls = market_module.intraday_gaps, []
    def counted(values, interval):
        calls.append(len(values))
        return original(values, interval)
    monkeypatch.setattr(market_module, 'intraday_gaps', counted)
    report = client.get('/api/market/quality?symbol=SPY&interval=1h&limit=1').json()['intraday']
    assert report['checked'] and report['missing_bar_count'] == 1
    assert report['missing_bars'] == ['2024-11-29T17:30:00Z']
    assert client.get('/api/market/quality?symbol=SPY&interval=1h').json()['intraday']['missing_bar_count'] == 1
    assert calls == [len(bars)]
    app.state.market.write('SPY','1h',[{'time':'2024-11-29T17:30:00Z','open':100,'high':101,'low':99,'close':100,'volume':1}],
                           'Yahoo Finance via yfinance','unverified')
    assert client.get('/api/market/quality?symbol=SPY&interval=1h').json()['intraday']['missing_bar_count'] == 0
    assert len(calls) == 2
    app.state.market.write('QQQ','1h',bars,'Licensed provider','unverified')
    unknown = client.get('/api/market/quality?symbol=QQQ&interval=1h').json()['intraday']
    assert not unknown['checked'] and 'conventions' in unknown['reason']


def test_intraday_quality_reports_concurrent_series_change(setup, monkeypatch):
    app, client = setup
    bar = {'time':'2024-01-02T14:30:00Z','open':100,'high':101,'low':99,'close':100,'volume':1}
    app.state.market.write('SPY','1h',[bar],'Yahoo Finance via yfinance','unverified')
    import backend.market as market_module
    original = market_module.intraday_gaps
    def changing(values, interval):
        result = original(values, interval)
        app.state.market.write('SPY','1h',[{**bar,'time':'2024-01-02T15:30:00Z'}],
                               'Yahoo Finance via yfinance','unverified')
        return result
    monkeypatch.setattr(market_module, 'intraday_gaps', changing)
    result = client.get('/api/market/quality?symbol=SPY&interval=1h').json()['intraday']
    assert result['stale'] is True
    monkeypatch.setattr(market_module, 'intraday_gaps', original)
    fresh = client.get('/api/market/quality?symbol=SPY&interval=1h').json()['intraday']
    assert fresh['checked'] and not fresh.get('stale') and fresh['observed_bars'] == 2


def test_discovery_clears_missing_quotes(setup):
    app, _ = setup
    store_holdings(app.state.db, '2024-01-02', [{'symbol':'AAA','weight':1,'sector':None}], 'test')
    scan = MarketDiscovery(app.state.db, FakeTradier())
    scan.scan()
    class Missing(FakeTradier):
        def quotes(self, symbols): return []
    scan.adapter = Missing()
    scan.scan()
    with app.state.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM quotes WHERE symbol='AAA'").fetchone()[0] == 0


def test_discovery_refreshes_positions_outside_spy_and_filters_them(setup):
    app, client = setup
    store_holdings(app.state.db, '2024-01-02', [{'symbol':'AAA','weight':1,'sector':None}], 'test')
    sync_accounts(app.state.db, FakeTradier())  # live AAPL is outside this snapshot
    prepare_shadow(app)
    shadow_module.place(app.state.db, 'QQQ', 'buy', 2, 100)

    class PortfolioQuotes(FakeTradier):
        def __init__(self): self.requested = []
        def quotes(self, symbols):
            self.requested = list(symbols)
            return [{'symbol':s,'last':110.0,'change_percentage':1.5,
                     'type':'etf' if s == 'QQQ' else 'stock','description':f'{s} description'} for s in symbols]

    adapter = PortfolioQuotes()
    scan = MarketDiscovery(app.state.db, adapter)
    result = scan.scan()
    assert {'AAA', 'AAPL', 'QQQ'} <= set(adapter.requested)
    shadow = client.get('/api/overview?mode=shadow').json()
    assert shadow['equity_cents'] == 102000
    assert shadow['positions'][0]['price'] == 110.0
    assert shadow['performance'][-1]['equity_cents'] == 102000
    live = client.get('/api/overview?mode=live').json()
    assert live['accounts'][0]['positions'][0]['price'] == 110.0
    assert 'AAPL' not in {r['symbol'] for r in result['lists']['stocks']}
    assert 'QQQ' not in {r['symbol'] for r in result['lists']['etfs']}
    assert [r['symbol'] for r in client.get('/api/positions').json()['rows'] if r['mode'] == 'shadow' and r['kind'] == 'etf'] == ['QQQ']
    assert [r['symbol'] for r in client.get('/api/positions').json()['rows'] if r['mode'] == 'live' and r['kind'] == 'stock'] == ['AAPL']

    class MissingPortfolio(PortfolioQuotes):
        def quotes(self, symbols):
            return [q for q in super().quotes(symbols) if q['symbol'] != 'QQQ']
    scan.adapter = MissingPortfolio()
    scan.scan()
    assert client.get('/api/overview?mode=shadow').json()['equity_cents'] is None


def test_sse_emits_committed_event(setup):
    app, _ = setup
    endpoint = next(route.endpoint for route in app.routes if getattr(route, 'path', '') == '/api/events')
    class RequestStub:
        headers = {}
        query_params = {}
        state = SimpleNamespace(access_scope='local')
        async def is_disconnected(self): return False
    async def exercise():
        response = await endpoint(RequestStub())
        stream = response.body_iterator
        assert 'connected' in await anext(stream)
        app.state.db.event('charts','market_updated',payload={'symbol':'XLC','interval':'1h'})
        event = await asyncio.wait_for(anext(stream), timeout=4)
        assert 'market_updated' in event and '"symbol": "XLC"' in event and 'id:' in event
        await stream.aclose()
    asyncio.run(exercise())


def test_remote_sse_only_forwards_safe_market_identifiers(setup):
    app, _ = setup
    endpoint = next(route.endpoint for route in app.routes if getattr(route, 'path', '') == '/view/events')
    class RequestStub:
        headers = {}
        query_params = {}
        state = SimpleNamespace(access_scope='remote', access_expires=time.time() + 60)
        async def is_disconnected(self): return False
    async def exercise():
        response = await endpoint(RequestStub())
        stream = response.body_iterator
        await anext(stream)
        app.state.db.event('overview', 'account_synced', payload={'account':'ACCT1234'})
        account = await asyncio.wait_for(anext(stream), timeout=4)
        assert 'ACCT1234' not in account and 'account_synced' not in account and 'overview' in account
        app.state.db.event('charts', 'market_updated', payload={'symbol':'XLC', 'interval':'1h', 'private':'SECRET'})
        market = await asyncio.wait_for(anext(stream), timeout=4)
        assert 'market_updated' in market and 'XLC' in market and 'SECRET' not in market
        app.state.db.event('charts', 'market_updated', payload={'symbol':'QQQ', 'interval':'1h'})
        private = await asyncio.wait_for(anext(stream), timeout=4)
        assert 'QQQ' not in private and 'status' in private
        await stream.aclose()
    asyncio.run(exercise())


def test_remote_sse_never_emits_after_token_expiry(setup):
    app, _ = setup
    endpoint = next(route.endpoint for route in app.routes if getattr(route, 'path', '') == '/view/events')
    class RequestStub:
        headers = {}
        query_params = {}
        state = SimpleNamespace(access_scope='remote', access_expires=time.time() + .2)
        async def is_disconnected(self): return False
    async def exercise():
        response = await endpoint(RequestStub())
        stream = response.body_iterator
        assert 'connected' in await anext(stream)
        await asyncio.sleep(.25)
        app.state.db.event('overview', 'account_synced')
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(anext(stream), timeout=1)
    asyncio.run(exercise())


def test_spy_lists_are_scoped_batched_temporary_and_atomic(setup):
    app, client = setup
    holdings = [{'symbol':f'T{i:03d}','name':f'Example {i}','weight':1/220,
                 'sector':'Energy' if i % 2 == 0 else 'Information Technology'} for i in range(220)]
    store_holdings(app.state.db, '2024-01-02', holdings, 'dated test')
    sync_accounts(app.state.db, FakeTradier())
    prepare_shadow(app)
    shadow_module.place(app.state.db, 'QQQ', 'buy', 2, 100)
    class Quotes:
        def __init__(self): self.batches = []; self.fail = False
        def quotes(self, symbols):
            self.batches.append(list(symbols))
            if self.fail and len(self.batches) == 2:
                raise RuntimeError('provider failure')
            return [{'symbol':symbol, 'type':'etf' if symbol in {'SPY','QQQ','XLE'} else 'stock',
                     'last':110.0, 'change_percentage':2.0, 'volume':1000} for symbol in symbols if symbol != 'T000']
    adapter = Quotes()
    scan = app.state.discovery
    scan.adapter = adapter
    result = scan.scan(session='premarket')
    assert scan.progress() is None
    assert len(adapter.batches) >= 3 and all(len(batch) <= 100 for batch in adapter.batches)
    lists = result['lists']
    stocks = {row['symbol']:row for row in lists['stocks']}
    assert len(stocks) == 220 and len(lists['etfs']) == 12 and len(lists['sectors']) == 11
    assert stocks['T000']['price'] is None and stocks['T001']['weight'] == pytest.approx(1/220)
    assert 'AAPL' not in stocks and 'QQQ' not in {r['symbol'] for r in lists['etfs']}
    energy = next(row for row in lists['sectors'] if row['symbol'] == 'XLE')
    assert energy['weight_complete'] and energy['weight'] == pytest.approx(.5) and energy['constituent_count'] == 110
    assert result['requested'] == 232 and result['account_marks_requested'] == 2
    assert client.get('/api/discovery').json()['requested'] == 232
    assert 'lists' not in client.get('/api/state').json()['discovery']
    assert client.get('/api/overview?mode=shadow').json()['equity_cents'] == 102000
    assert client.get('/api/overview?mode=live').json()['accounts'][0]['positions'][0]['price'] == 110.0
    with app.state.db.connect() as conn:
        progress_events = conn.execute("SELECT COUNT(*) FROM events WHERE action='discovery_progress'").fetchone()[0]
    assert 1 <= progress_events <= 11
    adapter.batches = []; adapter.fail = True
    with pytest.raises(RuntimeError, match='provider failure'):
        scan.scan(session='open')
    assert scan.progress() is None
    assert scan.snapshot()['completed'] == result['completed']
    assert client.get('/api/overview?mode=shadow').json()['equity_cents'] == 102000
    adapter.fail = False
    with pytest.raises(Exception, match='session ended'):
        scan.scan(session='open', session_check=lambda: {'state':'postmarket'}, check_every=0)
    assert scan.snapshot()['completed'] == result['completed']
    assert all(not rows for rows in MarketDiscovery(app.state.db, adapter).snapshot()['lists'].values())


def test_spy_sector_weights_report_incomplete_holdings(setup):
    app, client = setup
    store_holdings(app.state.db,'2024-01-02',[{'symbol':'AAA','weight':.4,'sector':'Energy'},
                                             {'symbol':'BBB','weight':.4,'sector':None}], 'partial test')
    result = app.state.discovery.scan()
    energy = next(row for row in result['lists']['sectors'] if row['symbol'] == 'XLE')
    assert energy['weight'] == pytest.approx(.4) and not energy['weight_complete'] and result['unclassified'] == 1


def test_discovery_progress_is_visible_and_cleared(setup):
    app, client = setup
    scan = app.state.discovery
    store_holdings(app.state.db,'2024-01-02',[{'symbol':f'T{i:03d}','weight':1/220,'sector':'Energy'} for i in range(220)],'test')
    waiting, release, failures = threading.Event(), threading.Event(), []
    class SlowQuotes:
        def __init__(self): self.calls = 0
        def quotes(self, symbols):
            self.calls += 1
            if self.calls == 2:
                waiting.set()
                if not release.wait(5): raise RuntimeError('test timeout')
            return [{'symbol':s,'type':'stock','last':10} for s in symbols]
    scan.adapter = SlowQuotes()
    def run():
        try: scan.scan(session='open')
        except Exception as exc: failures.append(exc)
    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert waiting.wait(5)
        progress = client.get('/api/state').json()['discovery']['progress']
        assert progress['phase'] == 'scanning' and progress['processed'] == 100 and progress['total'] > 220
    finally:
        release.set(); thread.join(timeout=5)
    assert not thread.is_alive() and not failures
    assert client.get('/api/state').json()['discovery']['progress'] is None


def test_discovery_worker_obeys_market_clock_and_recovers_cadence(setup):
    app, _ = setup
    class Clock:
        state = 'closed'
        def market_clock(self): return {'state':self.state}
    class Scan:
        def __init__(self): self.sessions = []
        def scan(self, stop_event, session, session_check):
            self.sessions.append(session)
            return {'requested':100,'quoted':90}
    clock, scan = Clock(), Scan()
    worker = Worker(app.state.db, clock, app.state.market, scan)
    assert 'Waiting for premarket' in worker.execute('discovery') and not scan.sessions
    clock.state = 'postmarket'
    assert 'postmarket' in worker.execute('discovery') and not scan.sessions
    for state in ('premarket','open'):
        clock.state = state
        assert '100 SPY-related symbols' in worker.execute('discovery')
    assert scan.sessions == ['premarket','open']
    with app.state.db.connect() as conn:
        conn.execute('UPDATE jobs SET enabled=0')
        conn.execute("UPDATE jobs SET enabled=1,interval_seconds=3,next_run=? WHERE name='discovery'", (time.time()-1,))
    worker.execute = lambda name: (time.sleep(.2) or 'finished')
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with app.state.db.connect() as conn:
                job = conn.execute("SELECT * FROM jobs WHERE name='discovery'").fetchone()
            if job['status'] == 'complete': break
            time.sleep(.05)
        assert job['status'] == 'complete'
        assert job['next_run'] - job['last_started'] == pytest.approx(3, abs=.01)
    finally:
        worker.stop()


def test_due_discovery_interrupts_market_refresh_on_same_worker_thread(setup):
    app, _ = setup
    db, calls, thread_ids = app.state.db, [], []
    with db.connect() as conn:
        conn.execute('UPDATE jobs SET enabled=0')
        conn.execute("UPDATE jobs SET enabled=1,next_run=? WHERE name='market'", (time.time() - 1,))
        conn.execute("UPDATE jobs SET enabled=1,next_run=? WHERE name='discovery'", (time.time() + 3600,))
    class Clock:
        def market_clock(self): return {'state':'open'}
    class Scan:
        def scan(self, stop_event, session, session_check):
            calls.append('discovery')
            thread_ids.append(threading.get_ident())
            with db.connect() as conn:
                assert conn.execute("SELECT status FROM jobs WHERE name='market'").fetchone()[0] == 'running'
                assert conn.execute("SELECT status FROM jobs WHERE name='discovery'").fetchone()[0] == 'running'
            return {'requested':1, 'quoted':1}
    class SlowMarket:
        def refresh_daily(self, adapter, symbol):
            calls.append(symbol)
            thread_ids.append(threading.get_ident())
            if symbol == 'SPY':
                with db.connect() as conn:
                    conn.execute("UPDATE jobs SET next_run=? WHERE name='discovery'", (time.time() - 1,))
            return {'count':1}
        def refresh_yahoo(self, symbol, interval):
            calls.append(interval)
            thread_ids.append(threading.get_ident())
            return {'count':1}
    worker = Worker(db, Clock(), SlowMarket(), Scan())
    worker.start()
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            with db.connect() as conn:
                market = conn.execute("SELECT status FROM jobs WHERE name='market'").fetchone()[0]
                discovery = conn.execute("SELECT status FROM jobs WHERE name='discovery'").fetchone()[0]
            if market == 'complete': break
            time.sleep(.02)
        assert market == discovery == 'complete'
        assert calls[:3] == ['SPY', 'discovery', 'XLC']
        assert len(set(thread_ids)) == 1
    finally:
        worker.stop()


def test_due_discovery_yields_between_holdings_and_account_requests(setup, monkeypatch):
    app, _ = setup
    db, calls = app.state.db, []
    with db.connect() as conn:
        conn.execute('UPDATE jobs SET enabled=0')
        conn.execute("UPDATE jobs SET enabled=1,next_run=? WHERE name='discovery'", (time.time() + 3600,))
    def due():
        with db.connect() as conn:
            conn.execute("UPDATE jobs SET next_run=? WHERE name='discovery'", (time.time() - 1,))
    def workbook(symbol):
        calls.append(symbol)
        if symbol == 'SPY':
            due()
            return '2024-01-02', [{'symbol':'AAA','name':'Example','weight':1}]
        return '2024-01-02', []
    class Scan:
        def scan(self, stop_event, session, session_check):
            calls.append('discovery')
            return {'requested':1, 'quoted':1}
    monkeypatch.setattr('backend.services.workbook', workbook)
    class Clock:
        def market_clock(self): return {'state':'open'}
    worker = Worker(db, Clock(), app.state.market, Scan())
    worker.execute('holdings')
    assert calls[:3] == ['SPY', 'discovery', 'XLC']

    calls.clear()
    def responder(request):
        path = request.url.path
        if path.endswith('/markets/clock'): return httpx.Response(200, json={'clock':{'state':'open'}})
        if path.endswith('/user/profile'):
            calls.append('profile')
            due()
            return httpx.Response(200, json={'profile':{'account':{'account_number':'ACCT1234'}}})
        if path.endswith('/balances'):
            calls.append('balances')
            return httpx.Response(200, json={'balances':{'total_cash':42}})
        if path.endswith('/positions'): return httpx.Response(200, json={'positions':'null'})
        if path.endswith('/orders'): return httpx.Response(200, json={'orders':'null'})
        return httpx.Response(404)
    adapter = Tradier('test-key', transport=httpx.MockTransport(responder), market_interval=0)
    worker = Worker(db, adapter, app.state.market, Scan())
    try:
        worker.execute('account')
        assert calls[:3] == ['profile', 'discovery', 'balances']
    finally:
        adapter.close()


@pytest.mark.parametrize('failure_point', ['claim', 'finish'])
def test_worker_recovers_after_temporary_scheduler_storage_failure(setup, failure_point):
    app, _ = setup
    db, attempts = app.state.db, []
    with db.connect() as conn:
        conn.execute('UPDATE jobs SET enabled=0')
        conn.execute("UPDATE jobs SET enabled=1,next_run=? WHERE name='discovery'", (time.time() - 1,))
    worker = Worker(db, FakeTradier(), app.state.market)
    worker.execute = lambda name: (attempts.append(name) or 'scan finished')
    failed = []
    if failure_point == 'claim':
        original = worker.claim_due
        def flaky_claim(*args, **kwargs):
            if not failed:
                failed.append(True)
                raise sqlite3.OperationalError('temporary lock')
            return original(*args, **kwargs)
        worker.claim_due = flaky_claim
    else:
        original = worker.finish_job
        def flaky_finish(*args, **kwargs):
            if not failed:
                failed.append(True)
                raise sqlite3.OperationalError('temporary lock')
            return original(*args, **kwargs)
        worker.finish_job = flaky_finish
    worker.start()
    try:
        deadline = time.monotonic() + 8
        job = None
        while time.monotonic() < deadline:
            with db.connect() as conn:
                job = conn.execute("SELECT * FROM jobs WHERE name='discovery'").fetchone()
            if failed and job['status'] == 'complete': break
            time.sleep(.03)
        assert failed and job['status'] == 'complete'
        assert job['lease_until'] is None and job['next_run'] > time.time()
        assert worker.thread.is_alive()
        assert len(attempts) == (2 if failure_point == 'finish' else 1)
    finally:
        worker.stop()


def test_old_default_discovery_schedule_migrates_once(tmp_path):
    db = Database(tmp_path/'data.sqlite3')
    with db.connect() as conn:
        conn.execute("DELETE FROM migrations WHERE name='discovery_marketwide_v1'")
        conn.execute("DELETE FROM migrations WHERE name='discovery_marketwide_status_v1'")
        conn.execute("UPDATE settings SET value='30' WHERE key='discovery_minutes'")
        conn.execute("UPDATE jobs SET interval_seconds=1800,status='complete',message='Scanned 503 SPY holdings dated 2024-01-02' WHERE name='discovery'")
    migrated = Database(db.path)
    assert migrated.settings()['discovery_minutes'] == 5
    with migrated.connect() as conn:
        assert conn.execute("SELECT interval_seconds FROM jobs WHERE name='discovery'").fetchone()[0] == 300
        assert conn.execute("SELECT status FROM jobs WHERE name='discovery'").fetchone()[0] == 'idle'
        conn.execute("UPDATE settings SET value='45' WHERE key='discovery_minutes'")
        conn.execute("UPDATE jobs SET interval_seconds=2700 WHERE name='discovery'")
    assert Database(db.path).settings()['discovery_minutes'] == 45


def test_spy_scope_disables_legacy_unrelated_jobs_without_deleting_data(tmp_path):
    db = Database(tmp_path/'data.sqlite3')
    with db.connect() as conn:
        conn.execute("DELETE FROM migrations WHERE name='spy_only_discovery_v1'")
        conn.execute("INSERT INTO jobs(name,interval_seconds,next_run) VALUES('universe_otc',86400,0)")
        conn.execute("INSERT INTO jobs(name,interval_seconds,next_run) VALUES('sector_reference:energy',86400,0)")
        conn.execute('CREATE TABLE directory_listings(symbol TEXT PRIMARY KEY,name TEXT NOT NULL,kind_hint TEXT,exchange TEXT NOT NULL)')
        conn.execute("INSERT INTO directory_listings VALUES('OUTSIDE','Legacy listing',NULL,'Q')")
    upgraded = Database(db.path)
    with upgraded.connect() as conn:
        assert all(row[0] == 0 for row in conn.execute("SELECT enabled FROM jobs WHERE name='universe_otc' OR name LIKE 'sector_reference:%'"))
        assert conn.execute("SELECT COUNT(*) FROM directory_listings WHERE symbol='OUTSIDE'").fetchone()[0] == 1


def test_tradier_reports_market_quota_and_invalid_clock():
    adapter = Tradier('secret', transport=httpx.MockTransport(lambda request: httpx.Response(400, text='Quota Violation: Expires 123')), market_interval=0)
    with pytest.raises(Exception, match='quota'):
        adapter.market_clock()
    adapter.close()
    adapter = Tradier('secret', transport=httpx.MockTransport(lambda request: httpx.Response(200, json={'clock':{'state':'unknown'}})), market_interval=0)
    with pytest.raises(Exception, match='invalid market clock'):
        adapter.market_clock()
    adapter.close()
