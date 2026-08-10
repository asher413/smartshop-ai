"""
Two defensive layers that sit in front of every request:

1. SecurityHeadersMiddleware — sets the standard response headers that
   protect against clickjacking (X-Frame-Options), MIME-sniffing attacks
   (X-Content-Type-Options), and enables HSTS once you're on HTTPS (which
   Render/most free hosts give you by default). These cost nothing and
   most security scanners/browsers flag their absence immediately.

2. Rate limiting (slowapi, wrapping the same algorithm as Flask-Limiter)
   — applied per-route in main.py. Keyed by user_id (session) or admin
   session flag when available, falling back to IP address. Per-user
   keying means real users behind the same proxy/NAT don't share a single
   quota — an admin's dashboard rate-limiter never blocks another admin,
   and a signed-in user's chat calls don't consume some anonymous
   visitor's budget.
"""
import secrets
import time

from starlette.middleware.base import BaseHTTPMiddleware
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.responses import Response

from app.core.config import settings
from app.services.fraud_service import FraudService

# The rest of the app reads a "session_id" cookie for personalization
# (recently-viewed products, click history). Nothing ever SET that cookie,
# so every anonymous visitor fell back to "guest" and all users shared one
# history bucket — the personalization features never actually worked.
# This middleware hands out a per-browser session_id on first visit.
SESSION_ID_COOKIE = "session_id"

# Bot-guard state: per-IP request counts used by FraudService.risk_score
# (request velocity + burst timing). Kept in-process like the limiter's
# fallback; with multiple workers point it at Redis for exactness.
_bot_requests: dict[str, list[float]] = {}
_fraud = FraudService()
BOT_HARD_BLOCK_SCORE = 90
BOT_SOFT_BLOCK_SCORE = 70

# storage_uri points the limiter at Redis when configured so rate-limit
# counts are shared across multiple worker processes/containers. Without
# this, each Gunicorn/Uvicorn worker keeps its own independent in-memory
# counter — meaning a 10/minute limit effectively becomes "10 x number of
# workers" per minute, which quietly defeats the point under real
# concurrent-user load. Falls back to in-memory (single-process only,
# fine for local dev / a single free-tier instance) when Redis isn't set.
def _get_rate_limit_key(request):
    """Rate-limit key function: prefer the user's identity over IP.

    When a session carries a user_id, the key becomes "user:<id>" so
    every authenticated user gets their own independent quota. When the
    admin session is active, the key is "admin:<email>" so different
    admins don't share a limit.

    Anonymous routes (/signup, /login, /api/newsletter) stay IP-keyed so
    a burst of signup attempts from one device can't just spin up new
    accounts to reset the counter.
    """
    try:
        # Anonymous-first routes: always use IP so a single device can't
        # dodge the limit by creating new sessions/users. If you add a new
        # anonymous-first route, add it to this tuple.
        if str(request.url.path) in ("/signup", "/login", "/admin/login", "/api/newsletter"):
            return get_remote_address(request)
        s = getattr(request, "session", None)
        if s:
            if s.get("is_admin"):
                # Admin login stores the submitted email in the session so
                # different admins get independent rate-limit quotas.
                email = s.get("user_email") or s.get("admin_email") or "admin"
                return f"admin:{email}"
            uid = s.get("user_id")
            if uid:
                return f"user:{uid}"
    except Exception:
        pass
    return get_remote_address(request)


limiter = Limiter(
    key_func=_get_rate_limit_key,
    storage_uri=settings.redis_url if settings.redis_url else None,
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        # --- Bot guard: score this request and soft/hard-block obvious bots
        # before they burn Gemini quota on /api/chat or spam signup. Uses the
        # same FraudService the storefront ships; soft-block (score 70-89)
        # serves an empty 204 so bots see "nothing here", hard block (90+)
        # returns 403. Real browsers never trip this.
        client_ip = request.client.host if request.client else ""
        ua = request.headers.get("user-agent", "")
        accept_language = request.headers.get("accept-language", "")
        now = time.time()
        hits = [t for t in _bot_requests.get(client_ip, []) if now - t < 60]
        hits.append(now)
        _bot_requests[client_ip] = hits[-60:]

        ua_l = ua.lower()
        # Only bypass the guard for /admin requests carrying a VALID Basic
        # admin credential (cron jobs / curl -u). We deliberately do NOT
        # trust an arbitrary Authorization header on non-admin routes — that
        # would let a scraper add "Authorization: junk" and dodge the guard
        # on /api/chat, which is exactly the quota-burn vector this exists
        # to stop. require_admin still enforces auth on the route itself.
        is_admin_api = request.url.path.startswith("/admin")
        auth_header = request.headers.get("authorization", "")
        has_valid_admin_auth = False
        if is_admin_api and auth_header.startswith("Basic "):
            import base64
            try:
                decoded = base64.b64decode(auth_header[6:]).decode()
                user, _, pw = decoded.partition(":")
                from app.core.config import settings as _settings
                if secrets.compare_digest(user, "admin") and secrets.compare_digest(pw, _settings.admin_secret_key):
                    has_valid_admin_auth = True
            except Exception:
                pass
        is_real_browser = any(k in ua_l for k in (
            "mozilla", "chrome", "safari", "firefox", "edge", "opera",
            "postman", "dart", "axios", "okhttp",
        ))
        is_documented_crawler = any(k in ua_l for k in (
            "googlebot", "bingbot", "duckduckbot", "yandex", "slurp", "semrush", "ia_archiver",
        ))
        # /healthz is exempt from the bot guard: Render/Docker/LB health
        # probes send no browser UA, and the endpoint has zero side effects
        # — blocking it would make healthCheckPath never see a 200 and
        # trigger a restart loop.
        is_health_probe = request.url.path == "/healthz"
        # Image proxy is a public CDN-like endpoint — any client should be
        # able to fetch product images (browsers, curl, image-pipeline jobs).
        is_image_proxy = request.url.path.startswith("/img/")
        if is_health_probe or is_image_proxy or has_valid_admin_auth or is_real_browser or is_documented_crawler:
            risk = 0
        else:
            # Bare bot tooling (curl, wget, python-requests, headless scrapers):
            # score it — UA signature alone is enough to hard-block.
            risk = _fraud.risk_score(
                user_agent=ua,
                accept_language=accept_language,
                request_count_last_minute=len(hits),
                last_request_time=hits[-2] if len(hits) > 1 else 0,
            )
        if risk >= BOT_HARD_BLOCK_SCORE:
            return Response(status_code=403)
        if risk >= BOT_SOFT_BLOCK_SCORE:
            return Response(status_code=204)

        # Anonymous personalization: hand out a stable session_id cookie on
        # first visit so recently-viewed/click-history features have a real
        # per-browser bucket instead of everyone collapsing into "guest".
        # Safe to set on every response — browsers only store it when missing
        # or updated, and the value is regenerated per browser (opaque token).
        if SESSION_ID_COOKIE not in request.cookies:
            session_id_value = secrets.token_hex(16)
            # BaseHTTPMiddleware rebuilds the downstream Request from the
            # original scope headers, so mutating request.cookies alone would
            # NOT reach the route handler on this first request (it would log
            # under "guest" once). Patch the scope's Cookie header so the
            # handler sees the same id it just issued. Merge with any existing
            # cookie header rather than replacing it (other cookies may exist).
            headers = request.scope.get("headers") or []
            merged = False
            new_headers = []
            for name, value in headers:
                if name.lower() == b"cookie":
                    new_headers.append((name, value + f"; {SESSION_ID_COOKIE}={session_id_value}".encode()))
                    merged = True
                else:
                    new_headers.append((name, value))
            if not merged:
                new_headers.append((b"cookie", f"{SESSION_ID_COOKIE}={session_id_value}".encode()))
            request.scope["headers"] = new_headers

            response = await call_next(request)
            response.set_cookie(
                SESSION_ID_COOKIE,
                session_id_value,
                max_age=60 * 60 * 24 * 365,  # 1 year
                httponly=True,
                samesite="lax",
            )
        else:
            response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        # Cross-origin isolation hardening: COOP prevents a cross-origin
        # page from being able to interact with ours (popup/sandbox
        # attacks); CORP stops other origins from embedding our resources
        # (MIME-based side-channel reads). Both are free wins recommended by
        # security scanners and close the main remaining browser-level
        # side channels after CSP/frame-ancestors.
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        # Only meaningful over HTTPS (which is the default on Render/most
        # free hosts) — harmless to send even before HTTPS is confirmed.
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        # Basic Content-Security-Policy: block third-party scripts/frames by
        # default, then explicitly allow the CDNs the templates actually use
        # (Tailwind CDN, FontAwesome, Google Fonts, Chart.js). Inline scripts
        # are needed by the admin dashboard's inline <script> blocks, so
        # 'unsafe-inline' stays for now — the pragmatic baseline is
        # default-src 'self' + the known CDNs, which already kills most
        # injected third-party content vectors.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://cdnjs.cloudflare.com; "
            "style-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://cdnjs.cloudflare.com https://fonts.googleapis.com; "
            "font-src 'self' https://cdnjs.cloudflare.com https://fonts.gstatic.com; "
            "img-src 'self' data: https:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'"
        )
        return response
