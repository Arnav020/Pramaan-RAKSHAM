"""Access controls shared by the web routes.

- Rate limiting per client IP for the public endpoints (oracle abuse: nobody gets unlimited free
  attempts to tune a scam against Pramaan).
- Desk sign-in by cookie (never a token in the URL) with two roles: analyst and lead.
- Issuer and partner keys for institutions.
"""

from __future__ import annotations

import os
import secrets
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

SLOW_EN = "Please wait a few minutes before checking more. This limit stops people from testing scams against Pramaan."


class RateLimiter:
    def __init__(self, limit: int, window_s: int) -> None:
        self.limit, self.window = limit, window_s
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True


def _limits(var: str = "PRAMAAN_WEB_RATE", default: str = "20/600") -> tuple[int, int]:
    raw = os.getenv(var, default)                           # e.g. 20 checks per 10 minutes per IP
    n, _, w = raw.partition("/")
    return int(n), int(w or 600)


WEB = RateLimiter(*_limits())
# The /kit page runs a whole folder in one go (the jury kit has 49 files); it gets its own, larger allowance.
KIT = RateLimiter(*_limits("PRAMAAN_KIT_RATE", "60/600"))


def client_ip(request: Request) -> str:
    """The real client address. Only trust X-Forwarded-For when the request came through our own proxy (Caddy on localhost)."""
    peer = request.client.host if request.client else "unknown"
    if peer in ("127.0.0.1", "::1"):
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return peer


def enforce_rate(request: Request, bucket: str = "check") -> None:
    if partner_for_key(request.headers.get("x-pramaan-key", "")):
        return                     # a bank's own servers calling with their partner key
    limiter = KIT if bucket == "kit" else WEB
    if not limiter.allow(f"{bucket}:{client_ip(request)}"):
        raise HTTPException(429, SLOW_EN)


# ------------------------------------------------------------------ desk roles
ROLES = ("analyst", "lead")


def _tokens() -> dict[str, str]:
    t = {"analyst": os.getenv("DESK_TOKEN", "").strip(), "lead": os.getenv("DESK_LEAD_TOKEN", "").strip()}
    return {k: v for k, v in t.items() if v}


def role_for_token(given: str) -> str | None:
    """Constant-time check of a presented token. The lead token also works as an analyst."""
    found = None
    for role, tok in _tokens().items():
        if given and secrets.compare_digest(given, tok):
            found = role if found != "lead" else found
    return found


def desk_role(request: Request) -> str | None:
    return role_for_token(request.cookies.get("desk", ""))


def require_role(request: Request, need: str = "analyst") -> str:
    role = desk_role(request)
    if role is None:
        raise HTTPException(403, "Pramaan Desk is for bank analysts. Sign in at /desk/login.")
    if need == "lead" and role != "lead":
        raise HTTPException(403, "This action needs the lead analyst role.")
    return role


def issuer_for_token(given: str) -> str | None:
    """Institutions that may issue Pramaan Seals: ISSUER_TOKENS="demobank:<token>,otherbank:<token>"."""
    for pair in os.getenv("ISSUER_TOKENS", "").split(","):
        name, _, tok = pair.strip().partition(":")
        if name and tok and given and secrets.compare_digest(given, tok):
            return name
    return None


def partner_for_key(given: str) -> str | None:
    """Banks and platforms using the API: PARTNER_KEYS="demobank:<key>"."""
    for pair in os.getenv("PARTNER_KEYS", "").split(","):
        name, _, key = pair.strip().partition(":")
        if name and key and given and secrets.compare_digest(given, key):
            return name
    return None
