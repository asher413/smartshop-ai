"""Shared helper for LLM calls with a hard wall-clock timeout.

The langchain 'timeout' kwarg doesn't reliably cut off grpc connect/retry
loops (observed: the product page hung for minutes on a blocked network),
so we enforce the cap at the thread level. A SINGLE module-level bounded
ThreadPoolExecutor is shared by all agents so a permanently blocked network
can't accumulate one lingering thread per request — workers are capped and
reused, and the request path always proceeds even if Gemini is unreachable.

The AI availability gate (app.agents.ai_gate) is consulted BEFORE any call:
when no key is configured or the circuit is open (repeated failures / quota
exhausted), we return None instantly so every agent falls back to its
non-AI path without blocking. Successful calls report success (resets the
circuit); timeouts/failures count toward opening it.
"""
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

from app.agents import ai_gate

logger = logging.getLogger(__name__)

_LLM_EXECUTOR = ThreadPoolExecutor(max_workers=4)


def llm_call_with_hard_timeout(fn, timeout_seconds: float = 6.0, bypass_gate: bool = False):
    """Run fn on a worker thread and return its result, or None on timeout.

    Returns None immediately (no network attempt) when the AI gate says AI
    is unavailable — no key, or the circuit is open after repeated failures
    (unless bypass_gate=True, used by the admin settings "test connection"
    so a freshly-typed key is REALLY tested regardless of saved state).

    Gate bookkeeping (record_success/record_failure) is skipped when
    bypass_gate=True — an admin's manual test must not trip the production
    circuit breaker or reset a healthy circuit.

    The abandoned call keeps running on the shared worker until grpc gives
    up, but the caller never waits longer than timeout_seconds. The pool is
    bounded (4 workers), so this can't grow threads unboundedly.
    """
    if not bypass_gate and not ai_gate.ai_available():
        return None
    future = _LLM_EXECUTOR.submit(fn)
    try:
        result = future.result(timeout=timeout_seconds)
        if not bypass_gate:
            ai_gate.record_success()
        return result
    except FutureTimeout:
        future.cancel()
        if not bypass_gate:
            ai_gate.record_failure()
        return None
    except Exception:
        if not bypass_gate:
            ai_gate.record_failure()
        logger.warning("LLM call failed", exc_info=True)
        return None
