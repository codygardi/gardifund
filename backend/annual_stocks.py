"""Bounded annual SPY constituent rankings from dated, declared-adjusted data."""
from collections import OrderedDict
from datetime import datetime, timezone
from threading import RLock
import time

from .market import LOCK, nyse_session_hours


class AnnualStocks:
    def __init__(self, db, market, clock=time.time):
        self.db, self.market, self.clock = db, market, clock
        self.cache = OrderedDict()
        self.lock = RLock()

    def get(self, year, top_n=10):
        now = self.clock()
        if not 2000 <= year <= datetime.fromtimestamp(now, timezone.utc).year or not 1 <= top_n <= 10:
            raise ValueError('Choose an available year from 2000 onward and a Top N from 1–10')
        sessions = [(day, closing) for day, _, closing in nyse_session_hours(year, year)
                    if day.startswith(f'{year}-') and closing.timestamp() <= now]
        if not sessions:
            return {'rows':[], 'series':[], 'complete':False, 'year':year, 'message':'No completed regular sessions are available in this year.'}
        start, end = sessions[0][0], sessions[-1][0]
        with self.lock, LOCK:
            with self.db.connect() as conn:
                snapshot = conn.execute('SELECT * FROM holdings_snapshots WHERE as_of=?', (start,)).fetchone()
                if not snapshot:
                    return {'rows':[], 'series':[], 'complete':False, 'year':year, 'start':start, 'end':end,
                            'message':f'A dated SPY constituent snapshot for {start} is required. Current constituents are never substituted for past years.'}
                members = [dict(r) for r in conn.execute('SELECT symbol,name,sector FROM holdings WHERE as_of=?', (start,))]
                generations = tuple(tuple(r) for r in conn.execute("SELECT symbol,updated,adjustment FROM market_files WHERE interval='1d' ORDER BY symbol"))
            if len(members) > 1000:
                raise ValueError('The annual constituent universe exceeds the 1,000-symbol calculation limit')
            key = (year, start, end, snapshot['imported'], generations)
            result = self.cache.get(key)
            if result is None:
                prices = self.market.adjusted_closes({m['symbol'] for m in members}, start, end)
                rows = []
                for member in members:
                    values = prices.get(member['symbol'], {})
                    if start not in values or end not in values:
                        continue
                    rows.append({**member, 'return_pct':(values[end] / values[start] - 1) * 100})
                rows.sort(key=lambda row: (-row['return_pct'], row['symbol']))
                series = []
                for row in rows[:10]:
                    data = self.market.read(row['symbol'], '1d', start=start, end=end, limit=400)
                    series.append({'symbol':row['symbol'], 'bars':data['bars']})
                missing = len(members) - len(rows)
                complete = bool(members) and missing == 0 and snapshot['weight_sum'] >= .995
                result = {'year':year, 'start':start, 'end':end, 'rows':rows[:10], 'series':series, 'complete':complete,
                          'eligible':len(members), 'missing':missing,
                          'message':'Ranked by first-session close to last completed-session close using the start-date SPY universe and provider-declared dividend/split-adjusted prices. '
                                    + ('Coverage is complete for ranking endpoints.' if complete else 'The ranking is incomplete; missing constituents are excluded, not estimated.')
                                    + ' Historical winners use hindsight and are not a backtest or recommendation.'}
                self.cache[key] = result
                while len(self.cache) > 8:
                    self.cache.popitem(last=False)
            else:
                self.cache.move_to_end(key)
            return {**result, 'rows':result['rows'][:top_n], 'series':result['series'][:top_n]}
