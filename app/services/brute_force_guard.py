"""
Login brute-force protection. Without this, /login is a free password-
guessing endpoint for any bot with a credential-stuffing list — this is
one of the most common real attacks against small sites, far more common
than anything exotic.

Uses cache_service (Redis if configured, in-memory otherwise) to count
failed attempts per (ip, email) pair. After MAX_ATTEMPTS failures within
WINDOW_SECONDS, further attempts are blocked until the window expires —
a standard sliding lockout, not a permanent ban (permanent bans on an IP
just create support tickets when a real user mistypes their password a
few times).
"""
from app.services import cache_service

MAX_ATTEMPTS = 5
WINDOW_SECONDS = 15 * 60


def _key(ip: str, email: str) -> str:
    return f"login_attempts:{ip}:{email.strip().lower()}"


def is_locked_out(ip: str, email: str) -> bool:
    count = cache_service.get(_key(ip, email)) or 0
    return count >= MAX_ATTEMPTS


def record_failed_attempt(ip: str, email: str):
    key = _key(ip, email)
    count = (cache_service.get(key) or 0) + 1
    cache_service.set(key, count, ttl_seconds=WINDOW_SECONDS)


def clear_attempts(ip: str, email: str):
    cache_service.invalidate(_key(ip, email))
