from datetime import datetime

import pytest

from backend.annual_stocks import AnnualStocks
from backend.db import Database
from backend.market import MarketStore
from backend.services import store_holdings


def test_annual_rankings_require_dated_members_and_declared_adjusted_endpoints(tmp_path):
    db = Database(tmp_path/'db.sqlite3')
    market = MarketStore(db, tmp_path)
    clock = lambda: datetime.fromisoformat('2024-01-04T22:00:00+00:00').timestamp()
    ranking = AnnualStocks(db, market, clock)
    assert ranking.get(2024)['rows'] == []
    members = [{'symbol':'AAA','name':'A','weight':.5,'sector':'Energy'},
               {'symbol':'BBB','name':'B','weight':.5,'sector':'Energy'}]
    store_holdings(db, '2024-01-02', members, 'dated')
    store_holdings(db, '2024-01-03', [{'symbol':'CCC','weight':1,'sector':'Energy'}], 'later snapshot')
    def bars(first, last):
        return [{'time':'2024-01-02','open':first,'high':first,'low':first,'close':first,'volume':1},
                {'time':'2024-01-04','open':last,'high':last,'low':last,'close':last,'volume':1}]
    market.write('AAA','1d',bars(10,12),'declared source','split_dividend_adjusted')
    market.write('BBB','1d',bars(10,15),'unverified source','unverified')
    partial = ranking.get(2024)
    assert partial['complete'] is False and partial['missing'] == 1
    assert [r['symbol'] for r in partial['rows']] == ['AAA']
    assert partial['rows'][0]['return_pct'] == pytest.approx(20)
    assert 'CCC' not in str(partial) and 'declared source' not in str(partial)
    market.write('BBB','1d',bars(10,15),'declared source','split_dividend_adjusted',replace=True)
    full = ranking.get(2024, 1)
    assert full['complete'] is True and full['missing'] == 0
    assert [r['symbol'] for r in full['rows']] == ['BBB']
    assert len(full['series']) == 1 and full['series'][0]['bars'][-1]['close'] == 15
    assert len(ranking.cache) == 2
    with pytest.raises(ValueError): ranking.get(2024,11)
    with pytest.raises(ValueError): ranking.get(2025,1)
