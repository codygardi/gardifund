"""Read-only broker data drives a separate, idempotent Shadow cash account."""
from datetime import date, datetime, timedelta, timezone
from threading import RLock
import json
import time

from . import shadow
from .db import SECTORS
from .market import calendar_dates, nyse_session_hours, valid_symbol
from .quotes import account_symbols, number, store_quotes, trade_timestamp
from .tradier import ProviderError

QUOTE_MAX_AGE = 120


class QuoteUnavailable(ProviderError):
    pass


def regular_session(now):
    stamp = datetime.fromtimestamp(now, timezone.utc)
    return any(opening.timestamp() <= now < closing.timestamp()
               for day, opening, closing in nyse_session_hours(stamp.year, stamp.year)
               if day == shadow.trading_day(now))


def settlement_day(now):
    # Conservative T+1 simulation: skip exchange closures and US federal holidays.
    # Actual broker settlement calendars can differ; this is not a broker ledger.
    from pandas.tseries.holiday import USFederalHolidayCalendar
    current = date.fromisoformat(shadow.trading_day(now))
    federal = {day.date().isoformat() for day in USFederalHolidayCalendar().holidays(current, current + timedelta(days=14))}
    sessions = calendar_dates(current.year, current.year + 1)
    for offset in range(1, 15):
        candidate = (current + timedelta(days=offset)).isoformat()
        if candidate in sessions and candidate not in federal:
            return candidate
    raise ValueError('Could not establish a Shadow settlement date')


class ShadowExecution:
    def __init__(self, db, adapter, clock=time.time):
        self.db, self.adapter, self.clock = db, adapter, clock
        self.lock = RLock()

    def market_state(self):
        clock = self.adapter.market_clock()
        state = clock.get('state')
        if state not in {'premarket', 'open', 'postmarket', 'closed'}:
            raise ProviderError('Market session could not be verified')
        now = self.clock()
        with self.db.connect() as conn:
            previous = conn.execute('SELECT state FROM market_session WHERE id=1').fetchone()
            conn.execute('INSERT OR REPLACE INTO market_session VALUES(1,?,?)', (state, now))
            if not previous or previous[0] != state:
                self.db.event('investing', 'session_changed', conn)
        return state, state == 'open' and regular_session(now)

    def category(self, symbol, requested):
        sector_symbols = {s for s, _ in SECTORS}
        with self.db.connect() as conn:
            constituent = conn.execute('SELECT 1 FROM holdings WHERE symbol=? AND as_of=(SELECT MAX(as_of) FROM holdings_snapshots)', (symbol,)).fetchone()
        if requested == 'sector' and symbol in sector_symbols:
            return requested
        if requested == 'etf' and symbol in {'SPY', *sector_symbols}:
            return requested
        if requested == 'stock' and constituent and symbol not in {'SPY', *sector_symbols}:
            return requested
        raise ValueError('Choose SPY, a current SPY constituent, or a tradable sector ETF in the matching category')

    def fresh_quote(self, symbol, side):
        quotes = self.adapter.quotes([symbol])
        quote = next((q for q in quotes if isinstance(q, dict) and q.get('symbol') == symbol), None)
        if quote is None:
            raise QuoteUnavailable('No current quote is available; no simulated fill was recorded')
        now = self.clock()
        bid, ask = number(quote.get('bid'), True), number(quote.get('ask'), True)
        stamps = [trade_timestamp(quote.get(key)) for key in ('bid_date', 'ask_date')]
        if (bid is None or ask is None or bid > ask or any(stamp is None or not -5 <= now - stamp <= QUOTE_MAX_AGE for stamp in stamps)):
            raise QuoteUnavailable('A fresh, valid bid and ask are required; no simulated fill was recorded')
        with self.db.connect() as conn:
            store_quotes(conn, [quote], [symbol], now)
        return (ask if side == 'buy' else bid), min(stamps) + QUOTE_MAX_AGE

    def submit(self, symbol, side, quantity, category, request_id, *, order_type='market', limit_price=None, process_pending=False, process_limit=False):
        symbol = valid_symbol(symbol)
        if side not in {'buy','sell'} or isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= 1000000:
            raise ValueError('Enter a valid side and whole-share quantity')
        if order_type not in {'market','limit'} or (order_type == 'market' and limit_price is not None):
            raise ValueError('Choose a valid market or limit order')
        if order_type == 'limit':
            limit_price = number(limit_price, True)
            if side != 'sell' or limit_price is None or limit_price > 1_000_000_000:
                raise ValueError('Limit orders are available for Shadow exits with a positive limit price')
            limit_price = float(limit_price)
        fingerprint = json.dumps([symbol, side, quantity, category, order_type, limit_price], separators=(',', ':'))
        with self.lock:
            with self.db.connect() as conn:
                previous = shadow.prior_request(conn, request_id, fingerprint)
                if previous and (previous.get('status') not in {'queued','working'} or not (process_pending or process_limit)):
                    return previous
            if side == 'buy':
                category = self.category(symbol, category)
            else:
                with self.db.connect() as conn:
                    held = conn.execute('SELECT quantity,category FROM shadow_positions WHERE symbol=?', (symbol,)).fetchone()
                    reserved = conn.execute("SELECT COALESCE(SUM(quantity),0) FROM shadow_exit_limits WHERE symbol=? AND status='working' AND request_id!=?", (symbol, request_id)).fetchone()[0]
                if not held or quantity > held['quantity'] - reserved or held['category'] != category:
                    raise ValueError('Insufficient available Shadow shares; cancel working limit exits before selling their reserved shares')
            _, is_open = self.market_state()
            if not is_open:
                if side == 'sell':
                    if process_limit:
                        return previous
                    raise ValueError('Positions can only be exited during the normal trading session')
                if process_pending:
                    return previous
                with self.db.connect() as conn:
                    conn.execute('BEGIN IMMEDIATE')
                    previous = shadow.prior_request(conn, request_id, fingerprint)
                    if previous:
                        return previous
                    if conn.execute("SELECT COUNT(*) FROM shadow_pending WHERE status='pending'").fetchone()[0] >= 100:
                        raise ValueError('The Shadow account already has 100 pending entries')
                    sequence = conn.execute("SELECT COALESCE(MAX(check_order),0)+1 FROM shadow_pending WHERE status='pending'").fetchone()[0]
                    conn.execute('INSERT INTO shadow_pending(request_id,created,symbol,quantity,category,check_order) VALUES(?,?,?,?,?,?)',
                                 (request_id, self.clock(), symbol, quantity, category, sequence))
                    result = {'status':'queued', 'symbol':symbol, 'quantity':quantity, 'simulated':True,
                              'message':'Queued for the next regular session; fresh quotes and settled cash are checked before a simulated fill.'}
                    shadow.save_request(conn, request_id, fingerprint, result)
                    self.db.event('investing', 'shadow_entry_queued', conn)
                    return result
            if order_type == 'limit' and not process_limit:
                with self.db.connect() as conn:
                    conn.execute('BEGIN IMMEDIATE')
                    if conn.execute("SELECT COUNT(*) FROM shadow_exit_limits WHERE status='working'").fetchone()[0] >= 100:
                        raise ValueError('The Shadow account already has 100 working limit exits')
                    sequence = conn.execute("SELECT COALESCE(MAX(check_order),0)+1 FROM shadow_exit_limits WHERE status='working'").fetchone()[0]
                    conn.execute('INSERT INTO shadow_exit_limits(request_id,created,symbol,quantity,category,limit_price,expires_on,check_order) VALUES(?,?,?,?,?,?,?,?)',
                                 (request_id, self.clock(), symbol, quantity, category, limit_price, shadow.trading_day(self.clock()), sequence))
                    result = {'status':'working','symbol':symbol,'side':'sell','quantity':quantity,'limit_price':limit_price,'order_type':'limit','simulated':True,
                              'message':'Day limit exit is working. It can fill at a fresh bid at or above your limit during the normal session.'}
                    shadow.save_request(conn, request_id, fingerprint, result)
                    self.db.event('investing','shadow_limit_placed',conn)
                try:
                    return self.submit(symbol, side, quantity, category, request_id, order_type='limit', limit_price=limit_price, process_limit=True)
                except QuoteUnavailable:
                    return result
            price, valid_until = self.fresh_quote(symbol, side)
            if order_type == 'limit' and price < limit_price:
                return previous
            # Recheck the clock and session after the network call so a late response
            # cannot execute after the close (including early-close sessions).
            if not self.market_state()[1]:
                if process_pending or process_limit:
                    return previous
                raise ValueError('The normal trading session ended before the simulated fill')
            now = self.clock()
            if now > valid_until:
                raise QuoteUnavailable('The quote expired while verifying the session; no simulated fill was recorded')
            return shadow.place(self.db, symbol, side, quantity, price, category=category, request_id=request_id,
                                fingerprint=fingerprint, settles_on=settlement_day(now) if side == 'sell' else None, now=now,
                                order_type=order_type, limit_price=limit_price)

    def cancel(self, request_id):
        with self.lock, self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute("SELECT * FROM shadow_pending WHERE request_id=? AND status='pending'", (request_id,)).fetchone()
            table = 'shadow_pending'
            if not row:
                row = conn.execute("SELECT * FROM shadow_exit_limits WHERE request_id=? AND status='working'", (request_id,)).fetchone()
                table = 'shadow_exit_limits'
            if not row:
                raise ValueError('This entry is no longer pending')
            result = {'status':'cancelled', 'symbol':row['symbol'], 'simulated':True}
            conn.execute(f"UPDATE {table} SET status='cancelled',message='Cancelled locally' WHERE request_id=?", (request_id,))
            conn.execute('UPDATE shadow_requests SET result=? WHERE request_id=?', (json.dumps(result), request_id))
            self.db.event('investing', 'shadow_entry_cancelled', conn)
            return result

    def expire_limits(self):
        now = self.clock()
        today = shadow.trading_day(now)
        closing = next((end.timestamp() for day, _, end in nyse_session_hours(int(today[:4]), int(today[:4])) if day == today), None)
        with self.lock, self.db.connect() as conn:
            rows = conn.execute("SELECT request_id,symbol FROM shadow_exit_limits WHERE status='working' AND (expires_on<? OR (expires_on=? AND ?=1))",
                                (today, today, int(closing is not None and now >= closing))).fetchall()
            for row in rows:
                result = {'status':'expired','symbol':row['symbol'],'message':'Day limit expired at the regular-session close.'}
                conn.execute("UPDATE shadow_exit_limits SET status='expired',message=? WHERE request_id=?", (result['message'],row['request_id']))
                conn.execute('UPDATE shadow_requests SET result=? WHERE request_id=?',(json.dumps(result),row['request_id']))
            if rows:
                self.db.event('investing','shadow_limits_expired',conn)

    def reset(self, request_id):
        with self.lock:
            return shadow.reset(self.db, request_id, self.clock())

    def next_batch(self, table, status):
        if (table,status) not in {('shadow_pending','pending'),('shadow_exit_limits','working')}:
            raise ValueError('Unknown Shadow queue')
        with self.lock, self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            rows = [dict(row) for row in conn.execute(f'SELECT * FROM {table} WHERE status=? ORDER BY check_order,created,request_id LIMIT 20',(status,))]
            if rows:
                sequence = conn.execute(f'SELECT COALESCE(MAX(check_order),0)+1 FROM {table} WHERE status=?',(status,)).fetchone()[0]
                conn.executemany(f'UPDATE {table} SET check_order=? WHERE request_id=? AND status=?',
                                 [(sequence,row['request_id'],status) for row in rows])
            return rows

    def refresh(self):
        self.expire_limits()
        state, is_open = self.market_state()
        symbols = account_symbols(self.db)
        if state in {'premarket', 'open'} and symbols:
            if len(symbols) > 1000:
                raise ValueError('Portfolio marks exceed the 1,000-symbol refresh limit')
            quotes = self.adapter.quotes(symbols)
            with self.db.connect() as conn:
                store_quotes(conn, quotes, symbols, self.clock())
                shadow.record_performance(conn, self.clock())
                self.db.event('overview', 'portfolio_marked', conn)
        if is_open:
            pending = self.next_batch('shadow_pending','pending')
            for order in pending:
                try:
                    self.submit(order['symbol'], 'buy', order['quantity'], order['category'], order['request_id'], process_pending=True)
                except QuoteUnavailable as exc:
                    with self.db.connect() as conn:
                        conn.execute("UPDATE shadow_pending SET message=? WHERE request_id=? AND status='pending'", (str(exc)[:300], order['request_id']))
                except ProviderError:
                    raise  # A temporary provider outage leaves entries pending.
                except ValueError as exc:
                    with self.lock, self.db.connect() as conn:
                        # Preserve a concurrent cancel/reset instead of restoring an order.
                        changed = conn.execute("UPDATE shadow_pending SET status='rejected',message=? WHERE request_id=? AND status='pending'",
                                               (str(exc)[:300], order['request_id'])).rowcount
                        if changed:
                            conn.execute('UPDATE shadow_requests SET result=? WHERE request_id=?',
                                         (json.dumps({'status':'rejected','message':str(exc)[:300], 'symbol':order['symbol']}), order['request_id']))
                            self.db.event('investing', 'shadow_entry_rejected', conn)
            exits = self.next_batch('shadow_exit_limits','working')
            for order in exits:
                try:
                    self.submit(order['symbol'], 'sell', order['quantity'], order['category'], order['request_id'],
                                order_type='limit', limit_price=order['limit_price'], process_limit=True)
                except QuoteUnavailable:
                    continue
                except ProviderError:
                    raise
                except ValueError as exc:
                    with self.lock, self.db.connect() as conn:
                        changed = conn.execute("UPDATE shadow_exit_limits SET status='rejected',message=? WHERE request_id=? AND status='working'", (str(exc)[:300],order['request_id'])).rowcount
                        if changed:
                            conn.execute('UPDATE shadow_requests SET result=? WHERE request_id=?',
                                         (json.dumps({'status':'rejected','symbol':order['symbol'],'message':str(exc)[:300]}),order['request_id']))
                            self.db.event('investing','shadow_limit_rejected',conn)
        return f'Portfolio quotes checked; market is {state}'
