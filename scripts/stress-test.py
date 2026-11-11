"""Bounded HTTP load against disposable synthetic data and public viewing routes."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import statistics
import tempfile
import threading
import time

import httpx
import uvicorn
from qa_support import fixture, ROOT


def main():
    (ROOT/'data').mkdir(exist_ok=True)
    results=[]
    started=time.perf_counter()
    with tempfile.TemporaryDirectory(prefix='qa-stress-',dir=ROOT/'data') as directory:
        sockets=[socket.create_server(('127.0.0.1',0)) for _ in range(2)]
        ports=[sock.getsockname()[1] for sock in sockets]
        app=fixture(Path(directory),*ports,start_worker=True)
        server=uvicorn.Server(uvicorn.Config(app,proxy_headers=False,access_log=False,log_level='error'))
        thread=threading.Thread(target=lambda:server.run(sockets=sockets),daemon=True)
        thread.start()
        deadline=time.monotonic()+15
        try:
            while not server.started:
                if not thread.is_alive() or time.monotonic()>deadline: raise RuntimeError('QA server failed to start')
                time.sleep(.02)
            local=f'http://127.0.0.1:{ports[0]}'
            remote=f'http://127.0.0.1:{ports[1]}'
            cases=[(local,'GET','/api/state',200),(local,'GET','/api/overview?mode=shadow',200),
                   (local,'GET','/api/discovery',200),(local,'GET','/api/positions',200),
                   (local,'GET','/api/bars?symbol=SPY&interval=1d',200),(local,'GET','/api/database',200),
                   (remote,'GET','/view/overview?mode=live',200),(remote,'GET','/view/overview?mode=shadow',200),
                   (remote,'GET','/api/state',403),(remote,'GET','/view/database',403),
                   (remote,'POST','/api/shadow/reset',403),(remote,'GET','/view/state',200)]
            with httpx.Client(timeout=20,limits=httpx.Limits(max_connections=16)) as client:
                def request(index):
                    base,method,path,expected=cases[index%len(cases)]
                    before=time.perf_counter()
                    try:
                        response=client.request(method,base+path)
                        return response.status_code == expected, (time.perf_counter()-before)*1000
                    except httpx.HTTPError:
                        return False, (time.perf_counter()-before)*1000
                with ThreadPoolExecutor(max_workers=16) as pool:
                    results=list(pool.map(request,range(330)))
            with app.state.db.connect() as conn:
                integrity=conn.execute('PRAGMA quick_check').fetchone()[0]
                journal=conn.execute('PRAGMA journal_mode').fetchone()[0]
            worker_count=sum(t.name=='gardifund-worker' for t in threading.enumerate())
        finally:
            server.should_exit=True
            thread.join(timeout=15)
            for sock in sockets: sock.close()
            if thread.is_alive(): raise RuntimeError('QA server did not stop cleanly')
    durations=sorted(duration for _, duration in results)
    report={'created':datetime.now(timezone.utc).isoformat(),'requests':len(results),'concurrency':16,
            'failures':sum(not okay for okay, _ in results),'p50_ms':round(statistics.median(durations),2),
            'p95_ms':round(durations[int(len(durations)*.95)-1],2),'max_ms':round(max(durations),2),
            'elapsed_seconds':round(time.perf_counter()-started,2),'sqlite_integrity':integrity,'journal_mode':journal,
            'background_workers':worker_count,'temporary_data_removed':not Path(directory).exists()}
    (ROOT/'data'/'verification-stress.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report))
    if report['failures'] or integrity!='ok' or journal!='wal' or worker_count!=1:
        raise SystemExit(1)


if __name__=='__main__': main()
