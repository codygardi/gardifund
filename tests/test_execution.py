from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from backend.config import Settings
from backend.db import Database
from backend.main import create_app
from backend.services import store_holdings
from backend import shadow
from backend.setup_lock import hash_password, LiveSetupLock
from backend.execution import settlement_day


def epoch(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


class Broker:
    now = epoch('2024-01-02T15:00:00Z')
    state = 'open'
    bid, ask, last = 99.0, 101.0, 100.0
    stale = False
    calls = 0

    def market_clock(self):
        return {'state': self.state}

    def quotes(self, symbols):
        self.calls += 1
        stamp = int((self.now - (300 if self.stale else 0)) * 1000)
        return [{'symbol':s, 'type':'stock', 'last':self.last, 'bid':self.bid, 'ask':self.ask,
                 'bid_date':stamp, 'ask_date':stamp, 'trade_date':stamp, 'volume':10000} for s in symbols]


@pytest.fixture
def account(tmp_path):
    broker = Broker()
    settings = Settings(_env_file=None, GARDIFUND_DATA_DIR=tmp_path, GARDIFUND_START_WORKER=False,
                        GARDIFUND_AUTH_MODE='local', SETUPS_PASSWORD_HASH=hash_password('test-only'))
    app = create_app(settings, adapter=broker)
    app.state.worker.execution.clock = lambda: broker.now
    store_holdings(app.state.db, '2024-01-02', [{'symbol':'AAA','name':'Example','sector':'Energy','weight':1}], 'test snapshot')
    with app.state.db.connect() as conn:
        conn.execute('UPDATE jobs SET enabled=0')
    with TestClient(app, base_url='http://localhost:8765', client=('127.0.0.1',1)) as client:
        yield app, client, broker


def post(client, path, body):
    return client.post('/api'+path, json=body, headers={'Origin':'http://localhost:8765','X-Gardifund-Request':'1'})


def order(client, side='buy', quantity=2, symbol='AAA', category='stock', request_id=None, **extra):
    return post(client, '/shadow/orders', {'symbol':symbol,'side':side,'quantity':quantity,'category':category,
                                         'request_id':request_id or str(uuid4()), **extra})


def test_market_fills_spread_settled_cash_and_live_isolation(account):
    app, client, broker = account
    assert client.get('/api/overview?mode=shadow').json()['state']['cash_cents'] == 1000000
    before_live = client.get('/api/overview?mode=live').json()
    bought = order(client)
    assert bought.status_code == 200 and bought.json()['price'] == 101
    row = client.get('/api/positions').json()['rows'][0]
    assert row['mode'] == 'shadow' and row['entry_price'] == 101 and row['pl'] == -2
    sold = order(client, side='sell')
    assert sold.status_code == 200 and sold.json()['price'] == 99 and sold.json()['settles_on'] == '2024-01-03'
    with app.state.db.connect() as conn:
        assert shadow.cash_available(conn, broker.now) == (979800, 19800)
        assert shadow.cash_available(conn, epoch('2024-01-03T15:00:00Z')) == (999600, 0)
    assert order(client, quantity=98).status_code == 400  # proceeds are not settled yet
    assert client.get('/api/overview?mode=live').json() == before_live
    assert post(client,'/live/orders',{}).status_code in (404,405)


def test_client_cannot_choose_fill_price_or_trade_unrelated_instruments(account):
    _, client, _ = account
    assert order(client, price=1).status_code == 422
    assert order(client, quantity=0).status_code == 422
    assert order(client, quantity=1.5).status_code == 422
    assert order(client, symbol='QQQ', category='etf').status_code == 400
    assert order(client, symbol='AAA', category='sector').status_code == 400
    assert order(client, symbol='SPY', category='etf', quantity=1).status_code == 200
    assert order(client, symbol='XLK', category='sector', quantity=1).status_code == 200


def test_missing_stale_or_crossed_quotes_never_create_a_fill(account):
    app, client, broker = account
    broker.stale = True
    assert order(client).status_code == 503
    broker.stale = False; broker.bid = 102
    assert order(client).status_code == 503
    broker.bid = None
    assert order(client).status_code == 503
    with app.state.db.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM shadow_orders').fetchone()[0] == 0
        assert conn.execute('SELECT cash_cents FROM shadow_state').fetchone()[0] == 1000000


def test_quote_that_expires_during_final_session_check_never_fills(account):
    app, client, broker = account
    checks = [0]
    def delayed_clock():
        checks[0] += 1
        if checks[0] == 2:
            broker.now += 121
        return {'state':'open'}
    broker.market_clock = delayed_clock
    assert order(client).status_code == 503
    with app.state.db.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM shadow_orders').fetchone()[0] == 0
        assert conn.execute('SELECT cash_cents FROM shadow_state').fetchone()[0] == 1000000


def test_retries_and_concurrent_requests_do_not_duplicate_or_overdraw(account):
    app, client, broker = account
    request_id = str(uuid4())
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: app.state.worker.execution.submit('AAA','buy',2,'stock',request_id), range(8)))
    assert all(result == results[0] for result in results) and broker.calls == 1
    assert order(client, quantity=3, request_id=request_id).status_code == 400
    def buy():
        try:
            return app.state.worker.execution.submit('AAA','buy',90,'stock',str(uuid4()))['status']
        except ValueError:
            return 'rejected'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: buy(), range(2))) == ['filled','rejected']
    with app.state.db.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM shadow_orders').fetchone()[0] == 2
        assert conn.execute('SELECT cash_cents FROM shadow_state').fetchone()[0] == 70800


def test_off_hours_entries_queue_and_recover_while_exits_fail_closed(account):
    app, client, broker = account
    broker.state = 'closed'
    broker.now = epoch('2024-01-01T15:00:00Z')
    request_id = str(uuid4())
    result = order(client, request_id=request_id)
    assert result.status_code == 200 and result.json()['status'] == 'queued' and broker.calls == 0
    assert client.get('/api/positions').json()['rows'] == []
    activity = client.get('/api/overview?mode=shadow').json()['activity']
    assert activity[0]['status'] == 'queued' and activity[0].get('fill_price') is None
    assert order(client, request_id=request_id).json() == result.json()
    broker.state = 'open'; broker.now = epoch('2024-01-02T15:00:00Z')
    app.state.worker.execute('portfolio')
    assert client.get('/api/positions').json()['rows'][0]['quantity'] == 2
    assert order(client, request_id=request_id).json()['status'] == 'filled'
    for stamp in ('2024-07-04T15:00:00Z','2024-11-29T18:00:00Z','2024-01-02T21:00:00Z'):
        broker.now = epoch(stamp)
        assert order(client, side='sell').status_code == 400


def test_reset_archives_history_cancels_pending_and_preserves_request_ids(account):
    app, client, broker = account
    buy_id = str(uuid4())
    assert order(client, request_id=buy_id).status_code == 200
    broker.state = 'closed'
    pending_id = str(uuid4())
    assert order(client, request_id=pending_id).json()['status'] == 'queued'
    reset_id = str(uuid4())
    body = {'confirmation':'RESET SHADOW','request_id':reset_id}
    assert post(client,'/shadow/reset',{**body,'confirmation':'no'}).status_code == 422
    result = post(client,'/shadow/reset',body)
    assert result.status_code == 200 and result.json()['cash'] == 10000
    assert post(client,'/shadow/reset',body).json() == result.json()
    with app.state.db.connect() as conn:
        archive = json.loads(conn.execute('SELECT payload FROM shadow_archives').fetchone()[0])
        assert len(archive['shadow_positions']) == 1 and len(archive['shadow_orders']) == 1
        assert conn.execute('SELECT COUNT(*) FROM shadow_archives').fetchone()[0] == 1
        assert conn.execute('SELECT cash_cents FROM shadow_state').fetchone()[0] == 1000000
    assert order(client, request_id=buy_id).json()['status'] == 'filled'
    assert order(client, request_id=pending_id).json()['status'] == 'cancelled'
    assert client.get('/api/positions').json()['rows'] == []


def test_portfolio_marks_update_pnl_without_changing_cost_or_quantity(account):
    app, client, broker = account
    assert order(client).status_code == 200
    broker.last = 110
    app.state.worker.execute('portfolio')
    row = client.get('/api/positions').json()['rows'][0]
    assert row['quantity'] == 2 and row['entry_price'] == 101 and row['pl'] == 18
    assert row['pl_pct'] == pytest.approx(18 / 202 * 100)
    snapshot = client.get('/api/overview?mode=shadow').json()
    assert snapshot['equity_cents'] == 1001800
    assert snapshot['daily_performance']


def test_settlement_skips_exchange_and_bank_holidays():
    assert settlement_day(epoch('2024-05-24T15:00:00Z')) == '2024-05-28'
    assert settlement_day(epoch('2024-11-08T15:00:00Z')) == '2024-11-12'


def test_limit_exits_reserve_shares_respect_price_and_fill_once(account):
    app, client, broker = account
    assert order(client).status_code == 200
    request_id = str(uuid4())
    working = order(client, side='sell', quantity=1, request_id=request_id, order_type='limit',limit_price=105)
    assert working.status_code == 200 and working.json()['status'] == 'working'
    activity = client.get('/api/overview?mode=shadow').json()['activity']
    assert activity[0]['status'] == 'working' and activity[0]['limit_price'] == 105
    assert order(client,side='sell',quantity=2).status_code == 400
    row = client.get('/api/positions').json()['rows'][0]
    assert row['reserved_quantity'] == 1 and row['available_quantity'] == 1
    broker.bid, broker.ask, broker.last = 105.5, 106, 105.5
    app.state.worker.execute('portfolio')
    result = order(client,side='sell',quantity=1,request_id=request_id,order_type='limit',limit_price=105)
    assert result.json()['status'] == 'filled' and result.json()['price'] == 105.5
    app.state.worker.execute('portfolio')
    assert client.get('/api/positions').json()['rows'][0]['quantity'] == 1
    with app.state.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM shadow_orders WHERE side='sell'").fetchone()[0] == 1


def test_day_limits_cancel_and_expire_on_early_close_without_filling(account):
    app, client, broker = account
    broker.now = epoch('2024-11-29T17:00:00Z')
    assert order(client).status_code == 200
    request_id = str(uuid4())
    assert order(client,side='sell',quantity=1,request_id=request_id,order_type='limit',limit_price=110).json()['status'] == 'working'
    assert post(client,f'/shadow/pending/{request_id}/cancel',{}).json()['status'] == 'cancelled'
    second_id = str(uuid4())
    assert order(client,side='sell',request_id=second_id,order_type='limit',limit_price=110).json()['status'] == 'working'
    broker.now = epoch('2024-11-29T18:00:00Z')
    app.state.worker.execute('portfolio')
    assert order(client,side='sell',request_id=second_id,order_type='limit',limit_price=110).json()['status'] == 'expired'
    assert client.get('/api/positions').json()['rows'][0]['quantity'] == 2
    assert order(client,side='sell',order_type='limit',limit_price=90).status_code == 400


def test_limit_batches_rotate_past_unmet_orders_and_persist_progress(account):
    app, client, broker = account
    assert order(client,quantity=25).status_code == 200
    broker.bid, broker.ask = 80, 81
    for index in range(25):
        result = order(client,side='sell',quantity=1,order_type='limit',limit_price=200 if index<20 else 90)
        assert result.json()['status'] == 'working'
        broker.now += 1
    broker.bid, broker.ask = 99, 101
    app.state.worker.execute('portfolio')
    # Continue with a newly constructed worker, just as after a restart.
    from backend.worker import Worker
    restarted = Worker(app.state.db,broker,app.state.market)
    restarted.execution.clock = lambda: broker.now
    restarted.execute('portfolio')
    with app.state.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM shadow_exit_limits WHERE status='filled'").fetchone()[0] == 5
        assert conn.execute("SELECT quantity FROM shadow_positions WHERE symbol='AAA'").fetchone()[0] == 20


def test_entry_batches_rotate_past_symbols_without_fresh_quotes(account):
    app, client, broker = account
    symbols = [f'S{index:03d}' for index in range(25)]
    store_holdings(app.state.db,'2024-01-02',[{'symbol':symbol,'sector':'Energy','weight':1/25} for symbol in symbols],'test')
    broker.state = 'closed'
    for symbol in symbols:
        assert order(client,quantity=1,symbol=symbol).json()['status'] == 'queued'
        broker.now += 1
    original = broker.quotes
    def mixed_quotes(requested):
        result = original(requested)
        for quote in result:
            if quote['symbol'] in symbols[:20]:
                quote['bid_date'] = quote['ask_date'] = (broker.now-300)*1000
        return result
    broker.quotes = mixed_quotes
    broker.state = 'open'
    app.state.worker.execute('portfolio')
    app.state.worker.execute('portfolio')
    with app.state.db.connect() as conn:
        assert {row[0] for row in conn.execute('SELECT symbol FROM shadow_positions')} == set(symbols[20:])


def test_queue_schema_upgrade_preserves_cash_positions_and_orders(account):
    app, client, broker = account
    assert order(client).status_code == 200
    assert order(client,side='sell',quantity=1,order_type='limit',limit_price=110).json()['status'] == 'working'
    broker.state = 'closed'
    assert order(client,quantity=1).json()['status'] == 'queued'
    selections = {
        'shadow_state':'cash_cents,initial_cents', 'shadow_positions':'symbol,quantity,cost_cents',
        'shadow_pending':'request_id,symbol,quantity,status',
        'shadow_exit_limits':'request_id,symbol,quantity,limit_price,status',
        'shadow_requests':'request_id,fingerprint,result',
    }
    with app.state.db.connect() as conn:
        before = {table:[tuple(row) for row in conn.execute(f'SELECT {fields} FROM {table}')]
                  for table,fields in selections.items()}
        # Recreate the queue schema from the previously deployed build.
        for table in ('shadow_pending','shadow_exit_limits'):
            conn.execute(f'DROP INDEX {table}_queue')
            conn.execute(f'ALTER TABLE {table} DROP COLUMN check_order')
    upgraded = Database(app.state.db.path)
    with upgraded.connect() as conn:
        after = {table:[tuple(row) for row in conn.execute(f'SELECT {fields} FROM {table}')]
                 for table,fields in selections.items()}
        assert before == after
        assert conn.execute('PRAGMA quick_check').fetchone()[0] == 'ok'


def test_positions_and_working_limits_survive_restart_and_constituent_turnover(account):
    app, client, broker = account
    assert order(client).status_code == 200
    request_id = str(uuid4())
    assert order(client,side='sell',request_id=request_id,order_type='limit',limit_price=105).json()['status'] == 'working'
    store_holdings(app.state.db,'2024-01-03',[{'symbol':'BBB','weight':1,'sector':'Energy'}],'turnover')
    settings = Settings(_env_file=None,GARDIFUND_DATA_DIR=app.state.db.path.parent,GARDIFUND_START_WORKER=False,GARDIFUND_AUTH_MODE='local')
    restarted = create_app(settings,adapter=broker)
    restarted.state.worker.execution.clock = lambda: broker.now
    broker.bid, broker.ask = 106, 107
    restarted.state.worker.execute('portfolio')
    with app.state.db.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM shadow_positions').fetchone()[0] == 0
        assert conn.execute("SELECT status FROM shadow_exit_limits WHERE request_id=?",(request_id,)).fetchone()[0] == 'filled'


def test_password_hash_is_salted_and_malformed_hashes_fail_closed():
    first, second = hash_password('test-only'), hash_password('test-only')
    assert first != second and 'test-only' not in first
    lock = LiveSetupLock(first)
    assert lock.unlock('test-only')
    assert LiveSetupLock('scrypt$999999999$8$1$bad$bad').password_hash is None
