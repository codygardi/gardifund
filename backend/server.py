"""Run one app and worker with separate control and viewing sockets."""
import logging
import socket

import uvicorn

from .config import Settings
from .main import create_app


def main():
    settings = Settings()
    if settings.local_port == settings.remote_port:
        raise ValueError("Local and remote listener ports must differ")
    app = create_app(settings)
    addresses = [("127.0.0.1", settings.local_port)]
    if settings.auth_mode == "cloudflare":
        addresses.append(settings.viewing_address)
    sockets = []
    try:
        for address in addresses:
            try:
                sock = socket.create_server(address, backlog=2048)
            except OSError as exc:
                if address != settings.viewing_address:
                    raise
                logging.error("Read-only viewer could not bind %s:%s: %s", *address, exc)
                continue
            sock.setblocking(False)
            sockets.append(sock)
        config = uvicorn.Config(app, workers=1, proxy_headers=False, access_log=False)
        uvicorn.Server(config).run(sockets=sockets)
    finally:
        for sock in sockets:
            sock.close()


if __name__ == "__main__":
    main()
