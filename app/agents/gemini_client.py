"""
Direct REST client for Google Gemini.

Why this exists: the project used langchain's ChatGoogleGenerativeAI (grpc
transport), which on some networks hangs forever — the chat widget answered
"סליחה, אני מתקשה לענות" even with a valid GOOGLE_API_KEY, because the
invoke() call never returned within the hard timeout (the underlying grpc
connect/retry loop doesn't honor the langchain timeout kwarg). The plain
REST API (generativelanguage.googleapis.com) works reliably in the same
environment. So every LLM call in the app goes through this module now:

- requests.post with a wall-clock timeout (enforced by requests, and the
  caller can additionally wrap it in llm_call_with_hard_timeout).
- Consults the ai_gate BEFORE calling (no key / circuit open -> return None
  instantly, no network attempt) and records success/failure, so the whole
  fallback machinery keeps working.
- Returns the generated text string, or None on any failure/timeout.
"""

import logging
import json

import requests

from app.core.config import settings
from app.agents import ai_gate

logger = logging.getLogger(__name__)

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def gemini_generate_text(
    prompt: str,
    *,
    system: str = "",
    timeout_seconds: float = 8.0,
    bypass_gate: bool = False,
    json_mode: bool = False,
    model: str | None = None,
    temperature: float = 0.0,
    max_output_tokens: int | None = None,
) -> str | None:
    """Call Gemini's generateContent REST endpoint and return the text.

    Returns None on: no API key, circuit open, network error, timeout, or
    an error status — never raises. bypass_gate=True skips the gate checks
    (used by the admin settings "test connection" so a freshly-typed key is
    really tested) and skips gate bookkeeping.
    """
    if not bypass_gate:
        if not ai_gate.ai_available():
            return None
        # Site-wide throttle: never exceed the shared per-model quota so a
        # background agent can't exhaust it for the user-facing chat. Wait
        # briefly for a slot (short queue) instead of hard-denying — a chat
        # request moments after a background call still gets its answer.
        if not ai_gate.wait_for_slot():
            return None
    key = (settings.google_api_key or "").strip()
    if not key:
        return None

    model = model or settings.gemini_model
    url = f"{GEMINI_BASE}/{model}:generateContent"

    parts = [{"text": prompt}]
    body: dict = {"contents": [{"role": "user", "parts": parts}]}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    generation: dict = {"temperature": temperature}
    if json_mode:
        generation["responseMimeType"] = "application/json"
    if max_output_tokens:
        generation["maxOutputTokens"] = max_output_tokens
    body["generationConfig"] = generation

    try:
        resp = requests.post(
            url,
            params={"key": key},
            json=body,
            timeout=timeout_seconds,
        )
    except requests.RequestException as e:
        if not bypass_gate:
            ai_gate.record_failure()
        logger.warning("Gemini REST request failed: %s", e)
        return None

    if resp.status_code != 200:
        if not bypass_gate:
            if resp.status_code == 429:
                # Quota exhausted — open the circuit NOW so the whole site
                # falls back to no-AI mode instead of hammering a hard 429.
                ai_gate.record_quota()
            else:
                ai_gate.record_failure()
        try:
            err = resp.json().get("error", {}).get("message", resp.text[:200])
        except Exception:
            err = resp.text[:200]
        logger.warning("Gemini REST error %s: %s", resp.status_code, err)
        return None

    try:
        data = resp.json()
        text = (
            data.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
        )
    except Exception as e:
        if not bypass_gate:
            ai_gate.record_failure()
        logger.warning("Gemini REST parse failed: %s", e)
        return None

    if not text or not text.strip():
        if not bypass_gate:
            ai_gate.record_failure()
        return None

    if not bypass_gate:
        ai_gate.record_success()
    return text.strip()


def gemini_generate_json(
    prompt: str,
    *,
    system: str = "",
    timeout_seconds: float = 8.0,
    bypass_gate: bool = False,
    model: str | None = None,
    temperature: float = 0.0,
) -> dict | None:
    """Like gemini_generate_text but asks for JSON and parses it. Returns
    the parsed dict, or None on failure/parse error (caller falls back)."""
    text = gemini_generate_text(
        prompt,
        system=system,
        timeout_seconds=timeout_seconds,
        bypass_gate=bypass_gate,
        json_mode=True,
        model=model,
        temperature=temperature,
    )
    if not text:
        return None
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except Exception as e:
        logger.warning("Gemini JSON parse failed: %s (text: %.100s)", e, text)
        return None
