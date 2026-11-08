from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime
import json
import time
from zoneinfo import ZoneInfo

from .market import valid_symbol

INITIAL_CENTS = 1_000_000


def trading_day(now=None):
    return datetime.fromtimestamp(now or time.time(), ZoneInfo('America/New_York')).date().isoformat()


def cash_available(conn, now=None):
    cash = conn.execute('SELECT cash_cents FROM shadow_state WHERE id=1').fetchone()[0]
    unsettled = conn.execute('SELECT COALESCE(SUM(amount_cents),0) FROM shadow_settlements WHERE settles_on>?',
                             (trading_day(now),)).fetchone()[0]
    return cash - unsettled, unsettled


def prior_request(conn, request_id, fingerprint):
    row = conn.execute('SELECT fingerprint,result FROM shadow_requests WHERE request_id=?', (request_id,)).fetchone()
    if row:
        if row['fingerprint'] != fingerprint:
            raise ValueError('This request ID has already been used for a different action')
        return json.loads(row['result'])
    return None


def save_request(conn, request_id, fingerprint, result):
    conn.execute('INSERT OR REPLACE INTO shadow_requests VALUES(?,?,?)', (request_id, fingerprint, json.dumps(result)))


def cents(amount):
    try:
        value = Decimal(str(amount))
        if not value.is_finite() or value <= 0 or value > Decimal("1000000000"):
            raise ValueError()
        result = int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        if result < 1:
            raise ValueError()
        return result
    except (ValueError, InvalidOperation):
        raise ValueError("Amount must be positive and finite") from None


def record_performance(conn, now=None):
    state = conn.execute("SELECT * FROM shadow_state WHERE id=1").fetchone()
    if not state["initialized"]:
        return
    total_realized = conn.execute("SELECT COALESCE(SUM(realized_cents),0) FROM shadow_orders").fetchone()[0]
    positions = conn.execute("SELECT p.quantity,q.price FROM shadow_positions p LEFT JOIN quotes q ON q.symbol=p.symbol").fetchall()
    equity = state["cash_cents"] + sum(round(p["quantity"] * p["price"] * 100) for p in positions) if all(p["price"] is not None for p in positions) else None
    conn.execute("INSERT INTO shadow_performance(created,cash_cents,equity_cents,realized_cents) VALUES(?,?,?,?)",
                 (now or time.time(), state["cash_cents"], equity, total_realized))
    conn.execute('INSERT OR REPLACE INTO shadow_daily_performance VALUES(?,?,?,?,?)',
                 (trading_day(now), now or time.time(), state['cash_cents'], equity, total_realized))
    conn.execute('DELETE FROM shadow_performance WHERE id NOT IN (SELECT id FROM shadow_performance ORDER BY id DESC LIMIT 10000)')


def place(db, symbol, side, quantity, price, *, category='stock', request_id=None, fingerprint='', settles_on=None, now=None, order_type='market', limit_price=None):
    symbol = valid_symbol(symbol)
    if side not in {"buy", "sell"} or isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= 1000000:
        raise ValueError("Enter a valid side and whole-share quantity")
    price_cents, now = cents(price), now or time.time()
    cost = cents(Decimal(str(price)) * quantity)
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        previous = prior_request(conn, request_id, fingerprint) if request_id else None
        if previous and previous.get('status') not in {'queued', 'working'}:
            return previous
        state = conn.execute("SELECT * FROM shadow_state WHERE id=1").fetchone()
        if not state["initialized"]:
            raise ValueError("Shadow account is unavailable")
        position = conn.execute("SELECT * FROM shadow_positions WHERE symbol=?", (symbol,)).fetchone()
        realized = 0
        if side == "buy":
            available, _ = cash_available(conn, now)
            if cost > available:
                raise ValueError("Insufficient settled Shadow cash")
            if position and position['category'] != category:
                raise ValueError('This symbol is already held in another position tab')
            new_quantity = quantity + (position["quantity"] if position else 0)
            new_cost = cost + (position["cost_cents"] if position else 0)
            conn.execute("INSERT OR REPLACE INTO shadow_positions(symbol,quantity,cost_cents,category,opened) VALUES(?,?,?,?,?)",
                         (symbol, new_quantity, new_cost, category, position['opened'] if position else now))
            cash = state["cash_cents"] - cost
        else:
            if not position or quantity > position["quantity"]:
                raise ValueError("Insufficient shadow shares")
            allocated_cost = (position["cost_cents"] * quantity + position["quantity"] // 2) // position["quantity"]
            realized = cost - allocated_cost
            remaining = position["quantity"] - quantity
            if remaining:
                conn.execute("UPDATE shadow_positions SET quantity=?,cost_cents=? WHERE symbol=?", (remaining, position["cost_cents"] - allocated_cost, symbol))
            else:
                conn.execute("DELETE FROM shadow_positions WHERE symbol=?", (symbol,))
            cash = state["cash_cents"] + cost
        conn.execute("UPDATE shadow_state SET cash_cents=? WHERE id=1", (cash,))
        cursor = conn.execute("INSERT INTO shadow_orders(created,symbol,side,quantity,price_cents,realized_cents,category,fill_price,request_id,order_type,limit_price) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                              (now, symbol, side, quantity, price_cents, realized, category, price, request_id, order_type, limit_price))
        if side == 'sell' and settles_on:
            conn.execute('INSERT INTO shadow_settlements(order_id,amount_cents,settles_on) VALUES(?,?,?)', (cursor.lastrowid, cost, settles_on))
        result = {"status": "filled", "symbol": symbol, "side": side, "quantity": quantity, "price": price,
                  "category": category, "settles_on": settles_on, "simulated": True, 'order_type':order_type, 'limit_price':limit_price}
        if request_id:
            save_request(conn, request_id, fingerprint, result)
            conn.execute("UPDATE shadow_pending SET status='filled',message='Simulated market fill' WHERE request_id=?", (request_id,))
            conn.execute("UPDATE shadow_exit_limits SET status='filled',message='Simulated limit fill' WHERE request_id=?", (request_id,))
        record_performance(conn, now)
        db.event("overview", "shadow_order_filled", conn)
    return result


def reset(db, request_id, now=None):
    with db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        previous = prior_request(conn, request_id, 'reset')
        if previous:
            return previous
        archive = {table: [dict(r) for r in conn.execute(f'SELECT * FROM {table}')] for table in
                   ('shadow_state', 'shadow_positions', 'shadow_orders', 'shadow_settlements', 'shadow_performance', 'shadow_daily_performance', 'shadow_pending', 'shadow_exit_limits')}
        cursor = conn.execute('INSERT INTO shadow_archives(created,payload) VALUES(?,?)', (now or time.time(), json.dumps(archive)))
        for pending in archive['shadow_pending'] + archive['shadow_exit_limits']:
            if pending['status'] in {'pending', 'working'}:
                conn.execute("UPDATE shadow_requests SET result=? WHERE request_id=?", (json.dumps({'status':'cancelled','message':'Shadow account reset'}), pending['request_id']))
        for table in ('shadow_positions', 'shadow_orders', 'shadow_settlements', 'shadow_performance', 'shadow_daily_performance', 'shadow_pending', 'shadow_exit_limits'):
            conn.execute(f'DELETE FROM {table}')
        conn.execute('UPDATE shadow_state SET initialized=1,cash_cents=?,initial_cents=? WHERE id=1', (INITIAL_CENTS, INITIAL_CENTS))
        record_performance(conn, now)
        result = {'reset': True, 'cash': INITIAL_CENTS / 100, 'archive_id': cursor.lastrowid}
        save_request(conn, request_id, 'reset', result)
        db.event('overview', 'shadow_reset', conn)
        return result


def snapshot(db, now=None):
    with db.connect() as conn:
        state = dict(conn.execute("SELECT * FROM shadow_state WHERE id=1").fetchone())
        state['settled_cash_cents'], state['unsettled_cash_cents'] = cash_available(conn, now)
        positions = [dict(r) for r in conn.execute("""SELECT p.*,q.price,q.fetched,i.sector,i.name,i.kind
                       FROM shadow_positions p LEFT JOIN quotes q ON q.symbol=p.symbol
                       LEFT JOIN instruments i ON i.symbol=p.symbol ORDER BY p.symbol""")]
        orders = [dict(r) for r in conn.execute("SELECT * FROM shadow_orders ORDER BY id DESC LIMIT 200")]
        realized = conn.execute("SELECT COALESCE(SUM(realized_cents),0) FROM shadow_orders").fetchone()[0]
        perf = [dict(r) for r in conn.execute("SELECT * FROM shadow_performance ORDER BY id DESC LIMIT 500")]
        pending = [dict(r) for r in conn.execute("SELECT * FROM shadow_pending WHERE status='pending' ORDER BY created LIMIT 100")]
        entry_updates = [dict(r) for r in conn.execute("SELECT * FROM shadow_pending WHERE status='rejected' ORDER BY created DESC LIMIT 10")]
        exit_orders = [dict(r) for r in conn.execute("SELECT * FROM shadow_exit_limits WHERE status='working' ORDER BY created LIMIT 100")]
        exit_updates = [dict(r) for r in conn.execute("SELECT * FROM shadow_exit_limits WHERE status IN ('expired','rejected') ORDER BY created DESC LIMIT 10")]
        queued_activity = [dict(r) for r in conn.execute("SELECT * FROM shadow_pending WHERE status!='filled' ORDER BY created DESC LIMIT 200")]
        limit_activity = [dict(r) for r in conn.execute("SELECT * FROM shadow_exit_limits WHERE status!='filled' ORDER BY created DESC LIMIT 200")]
        activity_count = (conn.execute('SELECT COUNT(*) FROM shadow_orders').fetchone()[0]
                          + conn.execute("SELECT COUNT(*) FROM shadow_pending WHERE status!='filled'").fetchone()[0]
                          + conn.execute("SELECT COUNT(*) FROM shadow_exit_limits WHERE status!='filled'").fetchone()[0])
        daily = [dict(r) for r in conn.execute('SELECT * FROM shadow_daily_performance ORDER BY day DESC LIMIT 3650')]
    equity = state["cash_cents"] + sum(round(p["quantity"] * p["price"] * 100) for p in positions) if all(p["price"] is not None for p in positions) else None
    activity = [{**order,'status':'filled'} for order in orders]
    for order in queued_activity + limit_activity:
        is_limit = 'limit_price' in order
        activity.append({'created':order['created'],'symbol':order['symbol'],'quantity':order['quantity'],
                         'side':'sell' if is_limit else 'buy','order_type':'limit' if is_limit else 'market',
                         'status':'queued' if order['status']=='pending' else order['status'], 'limit_price':order.get('limit_price')})
    activity.sort(key=lambda order:(order['created'],order['status'] in {'working','queued'}),reverse=True)
    return {"state": state, "positions": positions, "orders": orders, "pending": pending, 'entry_updates':entry_updates,
            'exit_orders':exit_orders, 'exit_updates':exit_updates, "performance": list(reversed(perf)), 'daily_performance': list(reversed(daily)),
            'activity':activity[:200], 'activity_count':activity_count,
            "equity_cents": equity, "realized_cents": realized,
            "valuation_note": "Last cached quote; may be stale. Equity is unavailable if any position lacks a quote."}
