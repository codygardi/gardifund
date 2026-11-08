"""Serve isolated synthetic UI fixtures on loopback for visual verification."""
from pathlib import Path
import socket
import tempfile
import uvicorn
from qa_support import fixture, ROOT

if __name__=='__main__':
    (ROOT/'data').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='qa-ui-',dir=ROOT/'data') as directory:
        app,_=fixture(Path(directory),18765,18766,auto_access=True)
        sockets=[socket.create_server(('127.0.0.1',port)) for port in (18765,18766)]
        print('Disposable QA data:',directory,flush=True)
        try:
            uvicorn.Server(uvicorn.Config(app,proxy_headers=False,access_log=False)).run(sockets=sockets)
        finally:
            for sock in sockets: sock.close()
