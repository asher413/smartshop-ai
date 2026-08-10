"""
Unit tests for the CSRF protection layer ITSELF — not the routes that use it
(the route-level gate is covered exhaustively in test_security.py). These pin
the low-level contract so future changes to the defense layer (serializer
swaps, different signing salt, max-age tuning) can't silently break the
outer boundary:

  * generate_csrf_token() -> a fresh, unique, verifiable token
  * verify_csrf_token()   -> True only for a valid, unexpired token signed
                             with the current secret; False for empty,
                             forged, tampered or expired input.

All tests run offline — no app instance, no DB, no server.
"""
import time

from app.services import csrf_service


def test_generate_returns_nonempty_unique_tokens():
    t1 = csrf_service.generate_csrf_token()
    t2 = csrf_service.generate_csrf_token()
    assert t1 and t2
    assert t1 != t2  # tokens rotate — no replay of the same value


def test_valid_token_verifies():
    token = csrf_service.generate_csrf_token()
    assert csrf_service.verify_csrf_token(token) is True


def test_empty_and_none_tokens_rejected():
    assert csrf_service.verify_csrf_token("") is False
    assert csrf_service.verify_csrf_token(None) is False


def test_whitespace_only_token_rejected():
    assert csrf_service.verify_csrf_token("   ") is False


def test_forged_token_rejected():
    # Random strings and half-formed itsdangerous payloads must fail closed.
    assert csrf_service.verify_csrf_token("forged-token") is False
    assert csrf_service.verify_csrf_token("abc.def.ghi") is False


def test_tampered_token_rejected():
    """Flipping any part of a valid signature must invalidate it."""
    token = csrf_service.generate_csrf_token()
    replacement = "abc" if not token.endswith("abc") else "xyz"
    tampered = token[:-3] + replacement
    assert tampered != token
    assert csrf_service.verify_csrf_token(tampered) is False


def test_expired_token_rejected(monkeypatch):
    """A token issued beyond the max age must be rejected — even though it
    carries a perfectly valid signature. Freezing time freezes itsdangerous'
    SIGNATURE timestamp (the shared time module drives both), which is what
    max_age checks — the payload's own time_ns() is not what expires."""
    real_now = time.time  # capture BEFORE patching the module attribute
    stale = real_now() - 2 * csrf_service.TOKEN_MAX_AGE_SECONDS
    monkeypatch.setattr(csrf_service.time, "time", lambda: stale)
    old_token = csrf_service.generate_csrf_token()
    monkeypatch.setattr(csrf_service.time, "time", real_now)
    assert csrf_service.verify_csrf_token(old_token) is False


def test_fresh_token_still_valid_after_issue(monkeypatch):
    """Regression: the expiry check must not reject brand-new tokens (the
    signature's embedded timestamp is the only time reference)."""
    real_now = time.time  # capture BEFORE patching the module attribute
    token = csrf_service.generate_csrf_token()
    monkeypatch.setattr(csrf_service.time, "time", lambda: real_now() + 10)
    assert csrf_service.verify_csrf_token(token) is True


def test_token_is_not_the_plain_timestamp():
    """The token must not leak the raw timestamp — it's a signed payload."""
    token = csrf_service.generate_csrf_token()
    assert not token.startswith(str(int(time.time()))[:5])
    assert "time.time" not in token
