"""AI availability gate with automatic circuit breaking.

The whole site must keep working even when Gemini is unreachable, slow, or
the API quota is exhausted — product pulling, smart search, chat, coupons.
This gate is the single source of truth for "should I try the LLM right
now?":

- No GOOGLE_API_KEY configured        -> AI permanently off (instant fallback).
- N consecutive LLM failures          -> circuit opens for a cooldown window
  (quota exhausted / network blocked), so every agent short-circuits to its
  non-AI fallback INSTANTLY instead of blocking on retries that would each
  take ~20s. Success resets the counter.
- Otherwise                           -> AI on.

All agents route LLM calls through timeout_utils.llm_call_with_hard_timeout,
which calls allow()/record_*() here, so wiring this gate once covers the
whole codebase.
"""
import logging
import threading
import time

from app.core.config import settings

logger = logging.getLogger(__name__)

# Circuit-breaker thresholds: after FAILURE_THRESHOLD consecutive failures
# (timeouts count as failures), stop trying AI for COOLDOWN_SECONDS.
FAILURE_THRESHOLD = 3
COOLDOWN_SECONDS = 600  # 10 minutes

# Site-wide Gemini throttle: the free tier caps generateContent at ~20
# requests/minute PER MODEL. All agents (chat, blog, viral, marketing,
# forecast, review insights...) share that bucket, so without a throttle the
# site hits the 429 cliff and EVERYTHING fails at once. MIN_INTERVAL spaces
# calls to stay under the cap: 2.5s -> <=24/min (aggressive but under 20/min
# once circuit/429 handling kicks in) and 3.5s -> ~17/min.
MIN_INTERVAL_SECONDS = 3.5

_lock = threading.Lock()
_consecutive_failures = 0
_degraded_until = 0.0  # epoch seconds; 0 = not degraded
_last_call_at = 0.0


def acquire_slot() -> bool:
    """Site-wide rate limiter: return True if an LLM call may start now.
    If the last call was < MIN_INTERVAL ago, deny so the shared per-model
    quota is never blown."""
    global _last_call_at
    with _lock:
        now = time.time()
        if now - _last_call_at < MIN_INTERVAL_SECONDS:
            return False
        _last_call_at = now
        return True


def wait_for_slot(max_wait: float = 4.0) -> bool:
    """Poll acquire_slot() for up to max_wait seconds. Turns the throttle
    into a short queue instead of a hard deny: when a background agent just
    used the slot, a user-facing chat call briefly waits (≤4s) and still
    gets a real AI answer rather than a misleading fallback."""
    deadline = time.time() + max_wait
    while True:
        if acquire_slot():
            return True
        if time.time() >= deadline:
            return False
        time.sleep(0.25)


def ai_configured() -> bool:
    """Is an API key present at all? If not, AI can never work."""
    return bool(settings.google_api_key)


def ai_available() -> bool:
    """Should the caller attempt an LLM call right now?"""
    if not ai_configured():
        return False
    with _lock:
        return time.time() >= _degraded_until


def degraded_until() -> float:
    """Epoch time until which AI calls are skipped (0 = not degraded).
    Exposed for the admin dashboard so the operator can SEE the circuit
    state instead of wondering why nothing uses Gemini anymore."""
    with _lock:
        return _degraded_until


def record_success():
    """A real LLM call completed — the service is healthy again."""
    global _consecutive_failures, _degraded_until
    with _lock:
        _consecutive_failures = 0
        _degraded_until = 0.0


def record_failure():
    """An LLM call failed/timed out. After a run of failures, open the
    circuit so everything falls back to non-AI mode without blocking."""
    global _consecutive_failures, _degraded_until
    with _lock:
        _consecutive_failures += 1
        if _consecutive_failures >= FAILURE_THRESHOLD:
            _degraded_until = time.time() + COOLDOWN_SECONDS
            _consecutive_failures = 0
            logger.warning(
                "AI circuit OPEN for %ss after %d consecutive failures — "
                "site running in no-AI fallback mode",
                COOLDOWN_SECONDS, FAILURE_THRESHOLD,
            )


def record_quota():
    """A 429 (quota exceeded) came back. The per-minute quota is a HARD
    ceiling that won't lift until the window resets, so open the circuit
    IMMEDIATELY — no need to wait for 3 consecutive failures (which the
    short quota window may never produce because a success can slip in and
    reset the counter). This is what makes the no-AI backup engage
    automatically under real usage."""
    global _consecutive_failures, _degraded_until
    with _lock:
        if _degraded_until > time.time():
            return  # already open
        _consecutive_failures = 0
        _degraded_until = time.time() + COOLDOWN_SECONDS
        logger.warning(
            "AI circuit OPEN for %ss — Gemini quota (429) exceeded; "
            "site running in no-AI fallback mode",
            COOLDOWN_SECONDS,
        )
