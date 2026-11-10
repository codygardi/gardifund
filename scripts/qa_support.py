"""Synthetic local fixtures; never reads .env or reaches a data provider."""
from datetime import datetime
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.config import Settings
from backend.db import SECTORS
from backend.main import create_app
from backend.market import nyse_session_hours
from backend.services import store_holdings, sync_accounts
from backend.setup_lock import hash_password


class SyntheticBroker:
    now = datetime.fromisoformat('2026-11-09T15:00:00+00:00').timestamp()
    def market_clock(self): return {'state':'open'}
    def accounts(self, checkpoint=None):
        return [{'account_id':'QA-ACCOUNT','balance':{'total_equity':12000,'total_cash':11000},
                 'positions':[{'symbol':'Q0001','quantity':3,'cost_basis':300}], 'orders':[]}]
    def quotes(self, symbols):
        result = []
        for index, symbol in enumerate(symbols):
            value = 100 + sum(map(ord,symbol)) % 70
            result.append({'symbol':symbol,'description':f'QA synthetic {symbol}','type':'etf' if symbol in {'SPY',*(s for s,_ in SECTORS)} else 'stock',
                           'last':value,'bid':value-.05,'ask':value+.05,'trade_date':self.now*1000,
                           'bid_date':self.now*1000,'ask_date':self.now*1000,'volume':100000+index*100,
                           'change':1,'change_percentage':1 if symbol.startswith('Q') else 3})
        return result


def fixture(data_dir, local_port, remote_port, start_worker=False, auto_access=False):
    settings = Settings(_env_file=None,GARDIFUND_DATA_DIR=data_dir,GARDIFUND_AUTH_MODE='public',
                        GARDIFUND_LOCAL_PORT=local_port,GARDIFUND_REMOTE_PORT=remote_port,GARDIFUND_START_WORKER=start_worker,
                        PRODUCTION_API_KEY='',SETUPS_PASSWORD_HASH=hash_password('qa-only-password'))
    broker = SyntheticBroker()
    app = create_app(settings,adapter=broker)
    app.state.worker.execution.clock = lambda: broker.now
    members = [{'symbol':f'Q{i:04d}','name':f'QA Example {i:04d}','sector':SECTORS[i%11][1],'weight':1/505} for i in range(505)]
    historical_members = [{**member, 'weight':1/len(SECTORS)} for member in members[:len(SECTORS)]]
    store_holdings(app.state.db,'2026-01-02',historical_members,'QA SYNTHETIC DATA')
    store_holdings(app.state.db,'2026-11-06',members,'QA SYNTHETIC DATA')
    sessions = [day for day,_,_ in nyse_session_hours(2026,2026) if '2026-01-02' <= day <= '2026-11-06']
    for index,symbol in enumerate(['SPY',*(s for s,_ in SECTORS),*(member['symbol'] for member in historical_members)]):
        rows = [{'time':day,'open':100+i*.15+index,'high':102+i*.15+index,'low':99+i*.15+index,
                 'close':101+i*.15+index,'volume':100000} for i,day in enumerate(sessions)]
        app.state.market.write(symbol,'1d',rows,'QA SYNTHETIC DATA','split_dividend_adjusted' if symbol.startswith('Q') else 'unverified')
    sync_accounts(app.state.db,broker)
    app.state.discovery.scan(session='open')
    from uuid import uuid4
    app.state.worker.execution.submit('Q0002','buy',2,'stock',str(uuid4()))
    with app.state.db.connect() as conn:
        conn.execute('UPDATE jobs SET enabled=0')
        if start_worker:
            conn.execute("UPDATE jobs SET enabled=1,next_run=?,interval_seconds=2 WHERE name IN ('portfolio','account','discovery')",(time.time(),))
    if auto_access:
        cookie = app.state.setup_lock.unlock('qa-only-password')
        class FixtureAccess:
            def __init__(self,app): self.app=app
            async def __call__(self,scope,receive,send):
                if scope['type'] == 'http' and scope.get('server',(None,None))[1] == local_port:
                    headers = list(scope.get('headers',[]))
                    headers.append((b'cookie',f'gardifund_live_setup={cookie}'.encode()))
                    scope = {**scope,'headers':headers}
                await self.app(scope,receive,send)
        app.add_middleware(FixtureAccess)
    return app
