"""Local, short-lived access to Live screening, using a salted password hash."""
from collections import deque
import hashlib
import hmac
import secrets
import threading
import time

from fastapi import HTTPException, Request


def hash_password(password):
    if not isinstance(password, str) or not 1 <= len(password) <= 128:
        raise ValueError('Use a password of 1–128 characters')
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode('utf-8'), salt=salt, n=16384, r=8, p=1, dklen=32)
    return f'scrypt$16384$8$1${salt.hex()}${digest.hex()}'


def parse_hash(value):
    try:
        algorithm, n, r, p, salt, digest = value.split('$')
        if (algorithm, n, r, p) != ('scrypt', '16384', '8', '1'):
            return None
        salt, digest = bytes.fromhex(salt), bytes.fromhex(digest)
        return (salt, digest) if len(salt) == 16 and len(digest) == 32 else None
    except (AttributeError, ValueError):
        return None


class LiveSetupLock:
    cookie_name = "gardifund_live_setup"
    lifetime = 900

    def __init__(self, password_hash: str):
        self.password_hash = parse_hash(password_hash)
        self.lock = threading.Lock()
        self.sessions = {}
        self.failures = deque()
        self.blocked_until = 0.0
        self.clock = time.monotonic

    def unlock(self, candidate: str):
        now = self.clock()
        with self.lock:
            if not self.password_hash:
                raise HTTPException(status_code=503, detail="Live setup password is not configured locally.")
            if now < self.blocked_until:
                raise HTTPException(status_code=429, detail="Too many attempts. Try again later.")
            while self.failures and self.failures[0] <= now - 300:
                self.failures.popleft()
            salt, expected = self.password_hash
            actual = hashlib.scrypt(candidate.encode('utf-8'), salt=salt, n=16384, r=8, p=1, dklen=32)
            if not hmac.compare_digest(actual, expected):
                self.failures.append(now)
                if len(self.failures) >= 5:
                    self.failures.clear()
                    self.blocked_until = now + 60
                raise HTTPException(status_code=401, detail="Incorrect password.")
            self.failures.clear()
            self.sessions = {digest: expiry for digest, expiry in self.sessions.items() if expiry > now}
            if len(self.sessions) >= 32:
                oldest = min(self.sessions, key=self.sessions.get)
                del self.sessions[oldest]
            token = secrets.token_urlsafe(32)
            self.sessions[hashlib.sha256(token.encode()).digest()] = now + self.lifetime
            return token

    def remaining(self, request: Request):
        if getattr(request.state, "access_scope", None) != "local":
            return 0.0
        token = request.cookies.get(self.cookie_name, "")
        if not token or len(token) > 128:
            return 0.0
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        now = self.clock()
        with self.lock:
            expiry = self.sessions.get(digest, 0)
            if expiry <= now:
                self.sessions.pop(digest, None)
                return 0.0
            return expiry - now

    def authorized(self, request: Request):
        return self.remaining(request) > 0

    def require(self, request: Request):
        if not self.authorized(request):
            raise HTTPException(status_code=403, detail="Unlock Live setups on this machine first.")

    def revoke(self, request: Request):
        token = request.cookies.get(self.cookie_name, "")
        if token and len(token) <= 128:
            with self.lock:
                self.sessions.pop(hashlib.sha256(token.encode("utf-8")).digest(), None)
