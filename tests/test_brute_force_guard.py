"""
Unit tests for the brute-force login guard ITSELF — not the /login route
(rate-limited + CSRF-gated login is covered in test_security.py). These pin
the low-level contract so future changes (TTL value, cache backend swap,
MAX_ATTEMPTS tuning) can't silently break the outer boundary:

  * record_failed_attempt() — increments the counter and sets expiry
  * is_locked_out()        — True when at/above MAX_ATTEMPTS, False otherwise
  * clear_attempts()       — resets the counter (called after a successful login)
  * key namespace           — different IPs/emails don't collide
  * TTL expiry              — the lockout naturally lifts after WINDOW_SECONDS

All tests run offline — no app instance, no DB, no server. They exercise
cache_service's in-memory store (Redis is optional) and steer clear of
any real Redis connection.
"""
import time

import pytest

from app.services import brute_force_guard, cache_service


# ---------------------------------------------------------------------------
# Helpers — every test starts with a clean in-memory cache
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_cache():
    """Purge the in-memory cache between tests so no leftover counter
    leaks into the next test. Each test uses a unique email, but this
    gives us belt-and-suspenders isolation."""
    # Nuke the entire in-memory fallback so no key from a previous
    # test survives. Tests that need real TTL behaviour monkeypatch
    # time anyway, so clearing here has no side effects.
    cache_service._memory_cache.clear()
    yield
    cache_service._memory_cache.clear()


# ---------------------------------------------------------------------------
# Initial state — clean slate
# ---------------------------------------------------------------------------

def test_fresh_ip_not_locked_out():
    """A brand-new IP+email pair starts unlocked."""
    assert brute_force_guard.is_locked_out("127.0.0.1", "fresh@test.local") is False


def test_fresh_key_reports_zero_not_blocked():
    """is_locked_out must return False when the counter is 0 (not None
    interpreted as False) — the 'or 0' in cache_service.get(key) handles
    both None and 0 safely."""
    assert brute_force_guard.is_locked_out("10.0.0.1", "fresh@test.local") is False


# ---------------------------------------------------------------------------
# record_failed_attempt — counting
# ---------------------------------------------------------------------------

def test_record_failed_attempt_increments_counter():
    """Each failed attempt pushes the counter closer to the limit."""
    ip, email = "127.0.0.1", "inc@test.local"
    assert brute_force_guard.is_locked_out(ip, email) is False
    brute_force_guard.record_failed_attempt(ip, email)
    assert brute_force_guard.is_locked_out(ip, email) is False  # 1 < 5
    brute_force_guard.record_failed_attempt(ip, email)
    brute_force_guard.record_failed_attempt(ip, email)
    brute_force_guard.record_failed_attempt(ip, email)
    assert brute_force_guard.is_locked_out(ip, email) is False  # 4 < 5


def test_record_max_attempts_triggers_lockout():
    """At exactly MAX_ATTEMPTS, the IP+email must be locked out."""
    ip, email = "192.168.1.1", "locked@test.local"
    for _ in range(brute_force_guard.MAX_ATTEMPTS):
        brute_force_guard.record_failed_attempt(ip, email)
    assert brute_force_guard.is_locked_out(ip, email) is True


def test_record_beyond_max_stays_locked():
    """Spamming failed attempts past the limit must NOT reset/overflow the
    counter — the lockout must hold until clear_attempts() or TTL expiry."""
    ip, email = "192.168.1.1", "overflow@test.local"
    for _ in range(brute_force_guard.MAX_ATTEMPTS + 3):
        brute_force_guard.record_failed_attempt(ip, email)
    assert brute_force_guard.is_locked_out(ip, email) is True


# ---------------------------------------------------------------------------
# clear_attempts — reset after successful login
# ---------------------------------------------------------------------------

def test_clear_removes_lockout():
    """After a successful login, clear_attempts must immediately lift the
    lockout for that IP+email pair."""
    ip, email = "10.0.0.1", "clear@test.local"
    for _ in range(brute_force_guard.MAX_ATTEMPTS):
        brute_force_guard.record_failed_attempt(ip, email)
    assert brute_force_guard.is_locked_out(ip, email) is True
    brute_force_guard.clear_attempts(ip, email)
    assert brute_force_guard.is_locked_out(ip, email) is False


def test_clear_then_record_starts_fresh():
    """After clearing, the counter resets — a fresh series of failed
    attempts starts counting from 1 again, not from the old value."""
    ip, email = "10.0.0.1", "fresh-restart@test.local"
    for _ in range(brute_force_guard.MAX_ATTEMPTS):
        brute_force_guard.record_failed_attempt(ip, email)
    brute_force_guard.clear_attempts(ip, email)
    # Now record just 1 — lockout must NOT trigger yet
    brute_force_guard.record_failed_attempt(ip, email)
    assert brute_force_guard.is_locked_out(ip, email) is False


def test_clear_on_unlocked_key_is_noop():
    """Clearing a key that was never set must not raise — the cache layer
    handles missing keys gracefully."""
    brute_force_guard.clear_attempts("127.0.0.1", "no-such@test.local")
    assert brute_force_guard.is_locked_out("127.0.0.1", "no-such@test.local") is False


# ---------------------------------------------------------------------------
# Isolation — different IPs / emails don't share counters
# ---------------------------------------------------------------------------

def test_different_ips_are_isolated():
    """Locking out one IP must NOT affect another — each IP+email key is
    independent."""
    ip_a = "127.0.0.1"
    ip_b = "192.168.1.1"
    email = "shared@test.local"
    for _ in range(brute_force_guard.MAX_ATTEMPTS):
        brute_force_guard.record_failed_attempt(ip_a, email)
    assert brute_force_guard.is_locked_out(ip_a, email) is True
    assert brute_force_guard.is_locked_out(ip_b, email) is False


def test_different_emails_are_isolated():
    """Locking out one email on an IP must NOT lock out another email on
    the same IP — credential-stuffing bots try many emails; each pair is
    tracked independently."""
    ip = "127.0.0.1"
    for _ in range(brute_force_guard.MAX_ATTEMPTS):
        brute_force_guard.record_failed_attempt(ip, "a@test.local")
    assert brute_force_guard.is_locked_out(ip, "a@test.local") is True
    assert brute_force_guard.is_locked_out(ip, "b@test.local") is False


def test_email_case_insensitive():
    """The key normalizes email to lowercase — a@Test.Local and a@test.local
    must share the same counter (RFC-agnostic for login UI)."""
    ip = "127.0.0.1"
    for _ in range(brute_force_guard.MAX_ATTEMPTS):
        brute_force_guard.record_failed_attempt(ip, "A@Test.Local")
    assert brute_force_guard.is_locked_out(ip, "a@test.local") is True


# ---------------------------------------------------------------------------
# TTL / time window — lockout lifts after WINDOW_SECONDS
# ---------------------------------------------------------------------------

def test_ttl_expiry_lifts_lockout(monkeypatch):
    """After WINDOW_SECONDS have elapsed, the counter should expire and the
    IP+email should no longer be locked out. This is a sliding window, not
    a permanent ban — a real user who mistypes their password a few times
    shouldn't need to email support."""
    ip, email = "10.0.0.1", "ttl@test.local"

    # Lock out by hitting MAX_ATTEMPTS.
    for _ in range(brute_force_guard.MAX_ATTEMPTS):
        brute_force_guard.record_failed_attempt(ip, email)
    assert brute_force_guard.is_locked_out(ip, email) is True

    # Fast-forward past the window. cache_service uses in-memory expiry
    # (time.time() compared at read time in the _memory_cache fallback),
    # so we monkeypatch time.time() inside cache_service to simulate expiry.
    real_time = time.time
    future = real_time() + brute_force_guard.WINDOW_SECONDS + 10
    monkeypatch.setattr(cache_service, "time", type(
        "fake_time", (), {"time": staticmethod(lambda: future)}
    ))
    assert brute_force_guard.is_locked_out(ip, email) is False


def test_ttl_range_still_locked_before_expiry(monkeypatch):
    """Before the TTL expires, the lockout must hold — the window is
    the FULL WINDOW_SECONDS, not shorter."""
    ip, email = "10.0.0.1", "ttl2@test.local"

    for _ in range(brute_force_guard.MAX_ATTEMPTS):
        brute_force_guard.record_failed_attempt(ip, email)
    assert brute_force_guard.is_locked_out(ip, email) is True

    # Advance only partially — the lockout must still be active.
    real_time = time.time
    partial = real_time() + brute_force_guard.WINDOW_SECONDS - 5
    monkeypatch.setattr(cache_service, "time", type(
        "fake_time", (), {"time": staticmethod(lambda: partial)}
    ))
    # The cached entry still hasn't expired — it was set to expire at
    # original_time + WINDOW_SECONDS, and we're still 5s short.
    assert brute_force_guard.is_locked_out(ip, email) is True


# ---------------------------------------------------------------------------
# Constants — verify sensible defaults
# ---------------------------------------------------------------------------

def test_max_attempts_reasonable():
    """MAX_ATTEMPTS must be at least 3 (to allow for honest typos) and at
    most 20 (to keep lockout meaningful)."""
    assert 3 <= brute_force_guard.MAX_ATTEMPTS <= 20


def test_window_seconds_reasonable():
    """WINDOW_SECONDS must be between 5min and 1h — too short is useless
    against bots, too long punishes real users."""
    assert 5 * 60 <= brute_force_guard.WINDOW_SECONDS <= 60 * 60


# ---------------------------------------------------------------------------
# _key — namespace hygiene
# ---------------------------------------------------------------------------

def test_key_contains_ip_and_normalized_email():
    """The key must embed both IP and normalized email so different pairs
    are independent."""
    k = brute_force_guard._key("1.2.3.4", "User@Example.com")
    assert "1.2.3.4" in k
    assert "user@example.com" in k
    assert "login_attempts" in k


def test_key_differs_for_different_ips():
    assert brute_force_guard._key("1.1.1.1", "a@b.com") != \
           brute_force_guard._key("2.2.2.2", "a@b.com")


def test_key_differs_for_different_emails():
    assert brute_force_guard._key("1.1.1.1", "a@b.com") != \
           brute_force_guard._key("1.1.1.1", "x@y.com")
