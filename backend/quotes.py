"""Quote validation and persistence shared by scans, account marks and simulation."""
import json
import math
import time

from .market import valid_symbol


def number(value, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return None if positive and value <= 0 else value


def trade_timestamp(value):
    value = number(value, positive=True)
    if value is None:
        return None
    if 946684800000 <= value <= 4102444800000:
        return int(value / 1000)
    if 946684800 <= value <= 4102444800:
        return int(value)
    return None


def quote_fields(quote):
    volume = number(quote.get('volume'))
    bid, ask = number(quote.get('bid'), True), number(quote.get('ask'), True)
    return {'price': number(quote.get('last'), positive=True),
            'trade_time': trade_timestamp(quote.get('trade_date')),
            'change_pct': number(quote.get('change_percentage')),
            'volume': int(volume) if volume is not None and volume >= 0 else None,
            'bid':bid, 'ask':ask, 'spread_pct':(ask - bid) / ((ask + bid) / 2) * 100 if bid and ask and bid <= ask else None}


def store_quotes(conn, quotes, symbols, now=None):
    now = now or time.time()
    requested = set(symbols)
    by_symbol = {q.get('symbol'): q for q in quotes if isinstance(q, dict) and q.get('symbol') in requested}
    conn.executemany('DELETE FROM quotes WHERE symbol=?', [(s,) for s in requested])
    conn.executemany('DELETE FROM quote_details WHERE symbol=?', [(s,) for s in requested])
    for symbol, quote in by_symbol.items():
        fields = quote_fields(quote)
        if fields['price'] is not None:
            kind = quote.get('type')
            if kind in {'stock', 'etf'}:
                conn.execute('INSERT OR IGNORE INTO instruments VALUES(?,?,?,?,?,?)',
                             (symbol, str(quote.get('description') or symbol)[:160], kind, None, 'Tradier quote', now))
            conn.execute('INSERT OR REPLACE INTO quotes VALUES(?,?,?,?,?,?,?)',
                         (symbol, fields['price'], number(quote.get('change')), fields['change_pct'], fields['volume'],
                          str(quote.get('trade_date') or ''), now))
        conn.execute('INSERT OR REPLACE INTO quote_details VALUES(?,?,?,?,?,?,?)',
                     (symbol, number(quote.get('bid'), True), number(quote.get('ask'), True),
                      trade_timestamp(quote.get('bid_date')), trade_timestamp(quote.get('ask_date')),
                      fields['trade_time'], now))


def account_symbols(db):
    with db.connect() as conn:
        symbols = {r[0] for r in conn.execute('SELECT symbol FROM shadow_positions')}
        for account in conn.execute('SELECT payload FROM live_accounts'):
            for p in json.loads(account[0]).get('positions', []):
                if isinstance(p, dict) and isinstance(p.get('symbol'), str):
                    try:
                        symbols.add(valid_symbol(p['symbol']))
                    except ValueError:
                        pass
    return sorted(symbols)
