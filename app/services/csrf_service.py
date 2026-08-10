"""
Lightweight CSRF protection for the state-changing forms (login, signup,
newsletter) — a signed, time-limited token embedded as a hidden field and
verified on submit. Deliberately not a full framework: this covers the
actual attack (a malicious page auto-submitting a form to your site using
the victim's cookies) without adding a dependency for something this
simple.
"""
import secrets
import time
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from app.core.config import settings

_serializer = URLSafeTimedSerializer(settings.session_secret_key, salt="csrf-token")

TOKEN_MAX_AGE_SECONDS = 3600


def generate_csrf_token() -> str:
    # Time + random nonce: every token is unique (no same-tick collisions on
    # coarse timers) and unpredictable. Expiry still works — itsdangerous
    # checks the SIGNATURE's timestamp against max_age, not the payload.
    # The payload is OPAQUE: callers must treat it as an opaque blob and
    # never parse it (it is not a float).
    return _serializer.dumps(f"{time.time_ns()}.{secrets.token_hex(6)}")


def verify_csrf_token(token: str) -> bool:
    if not token:
        return False
    try:
        _serializer.loads(token, max_age=TOKEN_MAX_AGE_SECONDS)
        return True
    except (BadSignature, SignatureExpired):
        return False
