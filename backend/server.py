"""Run one app and worker with separate loopback control and viewing sockets."""
import socket

import uvicorn

from .config import Settings
from .main import create_app


def main():
    settings = Settings()
    if settings.local_port == settings.remote_port:
        raise ValueError("Local and remote listener ports must differ")
    ports = [settings.local_port]
    if settings.auth_mode == "cloudflare":
        ports.append(settings.remote_port)
    sockets = []
    try:
        for port in ports:
            sock = socket.create_server(("127.0.0.1", port), backlog=2048)
            sock.setblocking(False)
            sockets.append(sock)
        config = uvicorn.Config(create_app(settings), workers=1, proxy_headers=False, access_log=False)
        uvicorn.Server(config).run(sockets=sockets)
    finally:
        for sock in sockets:
            sock.close()


if __name__ == "__main__":
    main()
