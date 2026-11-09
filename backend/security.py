from urllib.parse import urlsplit
import ipaddress
import math

import anyio
import jwt
from fastapi import Request
from fastapi.responses import JSONResponse
from jwt import PyJWKClient

from .config import Settings


class AccessGuard:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.jwks = None
        # Network key retrieval must not block the ASGI event loop or exhaust
        # the thread pool used for local API requests during an Access outage.
        self.jwt_limiter = anyio.CapacityLimiter(1)
        self.issuer = settings.cf_team_domain.rstrip("/")
        try:
            parsed = urlsplit(self.issuer)
        except ValueError:
            return
        valid = (parsed.scheme == "https" and parsed.hostname is not None
                 and parsed.hostname.endswith(".cloudflareaccess.com")
                 and parsed.netloc == parsed.hostname
                 and not parsed.path and not parsed.query and not parsed.fragment)
        if valid and settings.cf_audience:
            # Cache the JWKS for five minutes, but do not cache individual keys forever:
            # Cloudflare can rotate or revoke a signing key while the service is running.
            self.jwks = PyJWKClient(self.issuer + "/cdn-cgi/access/certs", cache_keys=False, lifespan=300, timeout=5)

    def validate_token(self, token: str):
        key = self.jwks.get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["RS256"], audience=self.settings.cf_audience,
                            issuer=self.issuer, options={"require": ["exp", "iss", "aud"]})
        expiry = claims["exp"]
        if isinstance(expiry, bool) or not isinstance(expiry, (int, float)) or not math.isfinite(expiry):
            raise ValueError("Invalid token expiry")
        return expiry

    async def authenticate(self, request: Request):
        server = request.scope.get("server")
        port = server[1] if server else None
        if port == self.settings.local_port:
            request.state.access_scope = "local"
            host = request.url.hostname or ""
            client = request.client.host if request.client else ""
            try:
                loopback = ipaddress.ip_address(client).is_loopback
            except ValueError:
                loopback = False
            if host not in {"localhost", "127.0.0.1", "::1"} or not loopback:
                return 403, "Local mode is restricted to loopback hosts and clients."
            proxy_headers = {"cf-connecting-ip", "cf-ray", "cf-access-jwt-assertion", "forwarded",
                             "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto"}
            if proxy_headers.intersection(request.headers.keys()):
                return 403, "The local control listener does not accept proxied requests."
        elif (server and tuple(server) == self.settings.viewing_address
              and self.settings.auth_mode == "cloudflare"):
            request.state.access_scope = "remote"
            if not self.jwks:
                return 503, "Cloudflare Access is not configured."
            token = request.headers.get("cf-access-jwt-assertion", "")
            if not token:
                return 401, "Cloudflare Access authentication required."
            if len(token) > 16384:
                return 401, "Invalid or expired Cloudflare Access token."
            try:
                request.state.access_expires = await anyio.to_thread.run_sync(
                    self.validate_token, token, limiter=self.jwt_limiter)
            except Exception:
                return 401, "Invalid or expired Cloudflare Access token."
            allowed_views = {"/view/state", "/view/overview", "/view/events", "/view/bars",
                             "/view/sectors", "/view/attribution", "/view/chart-performance"}
            path = request.url.path
            if request.method not in {"GET", "HEAD"} or not (path in allowed_views or path in {"/", "/scope"} or path.startswith("/assets/")):
                return 403, "The remote listener exposes read-only views only."
            if path.startswith(('/assets/Investing-', '/assets/Data-', '/assets/Settings-')):
                return 403, 'This module is available locally only.'
        else:
            return 403, "Unknown application listener."
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            parsed = urlsplit(origin or "")
            expected_scheme = "http"
            if (not origin or parsed.scheme != expected_scheme or parsed.netloc.lower() != request.headers.get("host", "").lower()
                    or request.headers.get("x-gardifund-request") != "1"):
                return 403, "A same-origin application request is required."
        return None

    async def __call__(self, request: Request, call_next):
        if request.url.path == "/healthz" and request.method == "GET":
            return await call_next(request)
        denial = await self.authenticate(request)
        if denial:
            return JSONResponse({"detail": denial[1]}, status_code=denial[0], headers={"Cache-Control": "no-store"})
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
        return response
