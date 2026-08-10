"""
Permanent security test-suite — runs in CI with plain pytest, no live
server needed. Covers the OWASP baseline for a storefront:

  * Authentication   — signup/login/admin flows, session gating
  * CSRF             — missing/invalid tokens rejected, admin POSTs
                       without origin/token blocked (403)
  * Security headers — CSP, X-Frame-Options, nosniff, HSTS, referrer
  * Rate limiting    — slowapi 429s after the per-route limit
  * SQL injection    — injection payloads can't break queries or leak
  * XSS              — stored product names are HTML-escaped everywhere

Implementation notes:
  * The real app is exercised via FastAPI TestClient with an in-memory
    SQLite DB wired in through dependency_overrides[get_db] — no network,
    no chromadb, no real catalog needed.
  * The bot-guard middleware would score TestClient's bare UA as a bot,
    so every request carries a browser-like User-Agent (risk -> 0).
  * slowapi keeps in-memory counters; limiter.reset() in the fixture keeps
    each test independent even though routes are rate-limited.
"""
import re
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.main import app
from app.core.config import settings
from app.core.database import get_db
from app.core.models import Base, Product, User
from app.core.security_middleware import limiter
from app.services import auth_service, csrf_service

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


@pytest.fixture()
def db_engine():
    # StaticPool: every connection (incl. TestClient's own worker thread)
    # shares the SAME in-memory database — otherwise each connection would
    # get a fresh empty DB and queries would fail with "no such table".
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return engine


@pytest.fixture()
def client(db_engine, monkeypatch):
    """TestClient against the real app with an isolated in-memory DB."""
    Session = sessionmaker(bind=db_engine)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    # Deterministic admin creds regardless of the dev .env.
    monkeypatch.setattr(settings, "admin_secret_key", "test-admin-pw-123")
    monkeypatch.setattr(settings, "admin_email", "admin@test.local")
    limiter.reset()

    with TestClient(app, base_url="http://127.0.0.1:8000", follow_redirects=False) as c:
        c.headers.update({"User-Agent": BROWSER_UA})
        yield c

    app.dependency_overrides.clear()
    limiter.reset()


def _csrf() -> str:
    return csrf_service.generate_csrf_token()


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def test_signup_creates_user_and_session(client, db_engine):
    r = client.post("/signup", data={
        "email": "new@test.local", "password": "supersecret1",
        "csrf_token": _csrf(), "website": "",
    })
    assert r.status_code == 303
    assert r.headers["location"] == "/personal-area"
    # Session now identifies the user.
    r2 = client.get("/personal-area")
    assert r2.status_code == 200
    assert "new@test.local".split("@")[0] in r2.text


def test_signup_rejects_short_password(client):
    r = client.post("/signup", data={
        "email": "short@test.local", "password": "123",
        "csrf_token": _csrf(), "website": "",
    })
    assert r.status_code == 200
    assert "8" in r.text  # error mentions the 8-char requirement


def test_signup_rejects_duplicate_email(client, db_engine):
    Session = sessionmaker(bind=db_engine)
    db = Session()
    auth_service.create_user(db, "dup@test.local", "supersecret1")
    db.close()

    r = client.post("/signup", data={
        "email": "dup@test.local", "password": "supersecret1",
        "csrf_token": _csrf(), "website": "",
    })
    assert r.status_code == 200
    assert "כבר רשומה" in r.text


def test_login_with_valid_credentials(client, db_engine):
    Session = sessionmaker(bind=db_engine)
    db = Session()
    auth_service.create_user(db, "login@test.local", "supersecret1")
    db.close()

    r = client.post("/login", data={
        "email": "login@test.local", "password": "supersecret1",
        "csrf_token": _csrf(),
    })
    assert r.status_code == 303
    assert r.headers["location"] == "/personal-area"


def test_login_wrong_password_denied(client, db_engine):
    Session = sessionmaker(bind=db_engine)
    db = Session()
    auth_service.create_user(db, "login2@test.local", "supersecret1")
    db.close()

    r = client.post("/login", data={
        "email": "login2@test.local", "password": "wrongpass1",
        "csrf_token": _csrf(),
    })
    assert r.status_code == 200  # error page, NOT a redirect
    assert "שגויים" in r.text
    # Not authenticated: personal area still redirects to login.
    r2 = client.get("/personal-area")
    assert r2.status_code in (303, 307)


def test_admin_dashboard_requires_login(client):
    r = client.get("/admin")
    assert r.status_code == 303
    assert r.headers["location"].endswith("/admin/login")


def test_admin_api_requires_auth(client):
    r = client.get("/admin/settings")
    assert r.status_code == 401


def test_admin_login_wrong_password(client):
    r = client.post("/admin/login", data={"email": "admin@test.local", "password": "nope"})
    assert r.status_code == 401


def test_admin_login_correct_password(client):
    r = client.post("/admin/login", data={"email": "admin@test.local", "password": "test-admin-pw-123"})
    assert r.status_code == 303
    assert r.headers["location"] == "/admin"
    # Now the dashboard is reachable with the session cookie.
    r2 = client.get("/admin")
    assert r2.status_code == 200


def test_admin_login_requires_password_not_just_admin_email(client):
    # The system email alone is NOT a password — wrong password still 401.
    r = client.post("/admin/login", data={"email": "admin@test.local", "password": ""})
    assert r.status_code in (401, 422)


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------

def test_login_without_csrf_token_rejected(client):
    r = client.post("/login", data={"email": "x@test.local", "password": "supersecret1"})
    assert r.status_code == 400


def test_signup_with_invalid_csrf_token_rejected(client):
    r = client.post("/signup", data={
        "email": "csrf@test.local", "password": "supersecret1",
        "csrf_token": "forged-token", "website": "",
    })
    assert r.status_code == 400


def test_signup_honeypot_silently_swallows_bots(client):
    # A bot that fills the invisible honeypot field gets a fake success:
    # no account is created, no error leaks that the trap exists.
    r = client.post("/signup", data={
        "email": "bot@test.local", "password": "supersecret1",
        "csrf_token": _csrf(), "website": "http://spam.example",
    })
    assert r.status_code == 303
    r2 = client.get("/personal-area")
    assert r2.status_code in (303, 307)  # not logged in


def test_admin_post_without_csrf_blocked(client):
    # Login as admin (session flag set).
    client.post("/admin/login", data={"email": "admin@test.local", "password": "test-admin-pw-123"})
    # A POST with no Origin, no Sec-Fetch-Site and no token must be 403.
    r = client.post("/admin/marketing/popup", data={"title": "x"})
    assert r.status_code == 403


def test_admin_post_with_valid_csrf_token_allowed(client):
    client.post("/admin/login", data={"email": "admin@test.local", "password": "test-admin-pw-123"})
    r = client.post(
        "/admin/marketing/popup",
        data={"title": "מבצע", "message": "היי", "link": ""},
        headers={"X-CSRF-Token": _csrf()},
    )
    # Not 403 anymore — reaches the handler (200) or form validation (422).
    assert r.status_code in (200, 422)


def test_admin_post_with_same_origin_allowed(client):
    # Browsers attach an Origin header on same-origin POSTs — that alone
    # must satisfy the CSRF gate without a token field.
    client.post("/admin/login", data={"email": "admin@test.local", "password": "test-admin-pw-123"})
    r = client.post(
        "/admin/marketing/popup",
        data={"title": "x", "message": "y", "link": ""},
        headers={"Origin": "http://127.0.0.1:8000"},
    )
    assert r.status_code in (200, 422)


def test_cross_origin_post_blocked(client):
    client.post("/admin/login", data={"email": "admin@test.local", "password": "test-admin-pw-123"})
    r = client.post(
        "/admin/marketing/popup",
        data={"title": "x"},
        headers={"Origin": "https://evil.example"},
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# CSRF — exhaustive coverage of EVERY admin POST route
# ---------------------------------------------------------------------------
# Every state-changing admin POST route that MUST be gated by
# require_admin_csrf. /admin/login and /admin/logout are intentionally
# excluded: login can't carry an authenticated-session CSRF proof (it is the
# very first request), and logout is already POST-only — which IS the
# logout-CSRF mitigation (a GET link can no longer be triggered by an
# attacker's <img>).
#
# The form payloads are minimal: the CSRF gate short-circuits BEFORE the
# handler for all blocked cases, and the allowed cases never hit the network
# because _stub_admin_side_effects() patches every service/agent they call.
ADMIN_CSRF_POST_ROUTES = [
    ("/admin/settings/save", {}),
    ("/admin/settings/test/ai", {}),
    ("/admin/suppliers/pull-test/aliexpress", {}),
    ("/admin/suppliers/pull/aliexpress", {}),
    ("/admin/users/99999/toggle-active", {}),
    ("/admin/run-price-monitor", {}),
    ("/admin/run-discovery", {}),
    ("/admin/coupons/pull", {}),
    ("/admin/marketing/popup", {"title": "t", "message": "m", "link": ""}),
    ("/admin/marketing/popup/99999/update", {"title": "t", "message": "m", "link": ""}),
    ("/admin/marketing/popup/99999/delete", {}),
    ("/admin/marketing/ad", {"name": "n", "position": "bogus"}),
    ("/admin/marketing/ad/99999/update", {"name": "n", "position": "bogus"}),
    ("/admin/marketing/ad/99999/toggle", {}),
    ("/admin/marketing/ad/99999/delete", {}),
    ("/admin/newsletter/send", {}),
    ("/admin/instagram/post", {"product_id": "99999"}),
    ("/admin/viral/script", {"product_id": "99999"}),
    ("/admin/blog/guide", {"category": "אלקטרוניקה"}),
    ("/admin/messages/99999/status", {"status": "read"}),
    ("/admin/reindex-meili", {}),
    ("/admin/reset-meili", {}),
    ("/admin/image-cache/clear", {}),
    ("/admin/warm-image-cache", {}),
]


def _login_admin(client):
    r = client.post("/admin/login", data={
        "email": "admin@test.local", "password": "test-admin-pw-123",
    })
    assert r.status_code == 303, r.status_code


def _stub_admin_side_effects(monkeypatch):
    """Stop the *allowed* CSRF tests from doing real work: supplier pulls,
    newsletter emails, Instagram posts, AI calls, price-monitor runs and
    .env writes. These tests exercise the CSRF gate — never side effects.
    Handlers that `import ... inside the function` are patched on the module,
    which the call-time import then picks up."""
    import app.workers.auto_import_worker as _w
    monkeypatch.setattr(_w, "run_full_cycle", lambda *a, **k: None)

    from app.api.main import email_campaign, instagram_agent, viral_engine, blog_agent
    monkeypatch.setattr(email_campaign, "send_newsletter", lambda db, limit=6: {"sent": 0})
    monkeypatch.setattr(instagram_agent, "post_deal", lambda **kw: {"status": "ok"})
    monkeypatch.setattr(viral_engine, "generate_short_script", lambda *a, **k: "script")
    monkeypatch.setattr(viral_engine, "build_social_caption", lambda *a, **k: "caption")
    monkeypatch.setattr(blog_agent, "write_buying_guide", lambda *a, **k: "guide")

    import app.services.settings_service as _ss
    monkeypatch.setattr(_ss, "save", lambda payload: [])
    monkeypatch.setattr(_ss, "run_test", lambda service, overrides: (True, "mock ok"))

    import app.services.coupon_service as _cs
    monkeypatch.setattr(_cs, "pull_coupons_from_sources", lambda db: {})

    import app.services.supplier_status_service as _ssvc
    monkeypatch.setattr(_ssvc, "test_supplier_pull", lambda supplier: {"status": "skipped", "supplier": supplier})
    # The per-supplier "pull now" endpoint runs a REAL discovery in the
    # background (network + real DB) — stub it for the CSRF gate tests.
    monkeypatch.setattr(_ssvc, "pull_supplier_products", lambda supplier, db=None: {"status": "ok", "message": "stubbed", "summary": {}})

    import app.api.main as _m
    # The run-price-monitor background task still instantiates SessionLocal()
    # (the REAL dev DB), but SQLAlchemy sessions connect lazily: the patched
    # record/check functions never query, so no connection or write happens.
    monkeypatch.setattr(_m, "record_daily_prices", lambda db: (0, []))
    monkeypatch.setattr(_m, "check_price_alerts", lambda db: [])


def test_every_admin_post_route_has_csrf_protection():
    """Introspection: any POST route under /admin (except login/logout) MUST
    depend on require_admin_csrf. If a future admin endpoint is added without
    the guard, this fails — no route can be silently forgotten."""
    protected = []
    unprotected = []
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        if not (path.startswith("/admin") and "POST" in methods):
            continue
        if path in ("/admin/login", "/admin/logout"):
            continue
        deps = getattr(route, "dependant", None)
        names = []
        if deps is not None:
            for d in getattr(deps, "dependencies", []):
                fn = getattr(d, "call", None)
                if fn is not None:
                    names.append(getattr(fn, "__name__", "") or getattr(fn, "__qualname__", ""))
        (protected if any("require_admin_csrf" in n for n in names) else unprotected).append(path)
    assert not unprotected, f"Admin POST routes missing require_admin_csrf: {unprotected}"
    # The parametrized suite below must stay complete: app.routes exposes the
    # route patterns ({ad_id}), while ADMIN_CSRF_POST_ROUTES uses concrete
    # paths (99999), so compare patterns-by-regex. A new protected route that
    # isn't added to the list — or an extra entry that matches no real route
    # — fails here, so coverage can't silently shrink.
    pattern_regexes = [
        re.compile("^" + re.sub(r"\{[^}]+\}", r"[^/]+", p) + "$") for p in protected
    ]
    assert len(pattern_regexes) == len(ADMIN_CSRF_POST_ROUTES)
    for concrete, _ in ADMIN_CSRF_POST_ROUTES:
        assert any(rx.match(concrete) for rx in pattern_regexes), f"no protected route matches {concrete}"


@pytest.mark.parametrize("route,data", ADMIN_CSRF_POST_ROUTES)
def test_admin_post_requires_auth(client, route, data):
    r = client.post(route, data=data)
    assert r.status_code == 401


@pytest.mark.parametrize("route,data", ADMIN_CSRF_POST_ROUTES)
def test_admin_post_without_csrf_proof_blocked(client, route, data):
    _login_admin(client)
    # No Origin, no Sec-Fetch-Site, no token -> the gate must fail closed.
    r = client.post(route, data=data)
    assert r.status_code == 403


@pytest.mark.parametrize("route,data", ADMIN_CSRF_POST_ROUTES)
def test_admin_post_cross_origin_blocked(client, route, data):
    _login_admin(client)
    r = client.post(route, data=data, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


@pytest.mark.parametrize("route,data", ADMIN_CSRF_POST_ROUTES)
def test_admin_post_valid_csrf_token_allowed(client, monkeypatch, route, data):
    _stub_admin_side_effects(monkeypatch)
    _login_admin(client)
    r = client.post(route, data={**data, "csrf_token": _csrf()}, headers={"X-CSRF-Token": _csrf()})
    # Passed the gate -> reached the handler (200/400/404 are handler outcomes).
    assert r.status_code not in (401, 403)


@pytest.mark.parametrize("route,data", ADMIN_CSRF_POST_ROUTES)
def test_admin_post_same_origin_allowed(client, monkeypatch, route, data):
    _stub_admin_side_effects(monkeypatch)
    _login_admin(client)
    r = client.post(route, data=data, headers={"Origin": "http://127.0.0.1:8000"})
    assert r.status_code not in (401, 403)


@pytest.mark.parametrize("route,data", ADMIN_CSRF_POST_ROUTES)
def test_admin_post_sec_fetch_site_allowed(client, monkeypatch, route, data):
    """Browsers also attach Sec-Fetch-Site on fetch() POSTs — the gate's
    second same-origin proof, independent of Origin."""
    _stub_admin_side_effects(monkeypatch)
    _login_admin(client)
    r = client.post(route, data=data, headers={"Sec-Fetch-Site": "same-origin"})
    assert r.status_code not in (401, 403)


@pytest.mark.parametrize("route,data", ADMIN_CSRF_POST_ROUTES)
def test_admin_post_basic_auth_cron_allowed(client, monkeypatch, route, data):
    """Cron jobs / curl -u use HTTP Basic and carry no ambient session cookie
    — no CSRF to defend against, so they must pass the gate for every route."""
    import base64
    _stub_admin_side_effects(monkeypatch)
    creds = base64.b64encode(b"admin:test-admin-pw-123").decode()
    r = client.post(route, data=data, headers={"Authorization": f"Basic {creds}"})
    assert r.status_code not in (401, 403)


def test_logout_requires_post(client, db_engine):
    Session = sessionmaker(bind=db_engine)
    db = Session()
    auth_service.create_user(db, "lo@test.local", "supersecret1")
    db.close()
    client.post("/login", data={
        "email": "lo@test.local", "password": "supersecret1", "csrf_token": _csrf(),
    })
    r = client.get("/logout")
    assert r.status_code in (405, 307)  # GET must not log out
    r2 = client.post("/logout")
    assert r2.status_code in (303, 307)


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------

def test_security_headers_present(client):
    r = client.get("/")
    assert r.status_code == 200
    h = r.headers
    assert "content-security-policy" in h
    assert h["x-frame-options"] == "DENY"
    assert h["x-content-type-options"] == "nosniff"
    assert h["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "strict-transport-security" in h
    assert "permissions-policy" in h


def test_csp_defaults_to_self(client):
    r = client.get("/")
    csp = r.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp


def test_trusted_host_rejects_evil_host(client):
    r = client.get("/", headers={"Host": "evil.example"})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

def test_newsletter_rate_limited(client):
    for i in range(5):
        r = client.post("/api/newsletter", data={"email": f"rl{i}@test.local", "website": ""})
        assert r.status_code == 200
    r = client.post("/api/newsletter", data={"email": "over@test.local", "website": ""})
    assert r.status_code == 429


def test_admin_login_rate_limited(client):
    for _ in range(10):
        client.post("/admin/login", data={"email": "admin@test.local", "password": "wrong"})
    r = client.post("/admin/login", data={"email": "admin@test.local", "password": "wrong"})
    assert r.status_code == 429


# ---------------------------------------------------------------------------
# SQL injection
# ---------------------------------------------------------------------------

SQLI_PAYLOADS = [
    "' OR 1=1 --",
    "'; DROP TABLE products; --",
    "\" OR \"\"=\"",
    "' UNION SELECT password_hash FROM users --",
    "1 OR 1=1",
    "%'; SELECT * FROM users; --",
]


@pytest.mark.parametrize("payload", SQLI_PAYLOADS)
def test_search_immune_to_sql_injection(client, payload):
    import urllib.parse
    r = client.get(f"/search?q={urllib.parse.quote(payload)}")
    # No 500, no traceback, no leaked data — just a normal results page.
    # (The query IS echoed back into the search box / <title>, which is
    # expected and safe — Jinja escapes it. What must NOT happen is a
    # crash, a traceback, or the injection actually executing.)
    assert r.status_code == 200
    assert "Traceback" not in r.text


@pytest.mark.parametrize("payload", SQLI_PAYLOADS)
def test_login_immune_to_sql_injection(client, payload):
    r = client.post("/login", data={
        "email": payload, "password": "supersecret1", "csrf_token": _csrf(),
    })
    # Never authenticates, never crashes.
    assert r.status_code in (200, 400, 429)
    assert "Traceback" not in r.text


def test_admin_login_immune_to_sql_injection(client):
    r = client.post("/admin/login", data={"email": "' OR 1=1 --", "password": "x"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# XSS
# ---------------------------------------------------------------------------

@pytest.fixture()
def xss_product(db_engine):
    Session = sessionmaker(bind=db_engine)
    db = Session()
    p = Product(
        sku="xss-1",
        source_adapter="test",
        external_id="xss1",
        name='<script>alert("xss")</script> Wireless Charger',
        original_name='<script>alert("xss")</script> Wireless Charger',
        price=19.9,
        image_url="https://example.com/i.jpg",
        is_active=True,
        is_verified=True,
        slug="xss-script-wireless-charger-test-xss1",
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    pid = p.id
    db.close()
    return pid


def test_product_page_escapes_stored_xss(client, xss_product):
    r = client.get(f"/product/{xss_product}")
    assert r.status_code == 200
    # The raw <script> must never appear; Jinja auto-escapes it.
    assert "<script>alert" not in r.text
    assert "&lt;script&gt;" in r.text


def test_search_page_escapes_stored_xss(client, xss_product):
    r = client.get("/search?q=Wireless+Charger")
    assert r.status_code == 200
    assert "<script>alert" not in r.text
    assert "&lt;script&gt;" in r.text


def test_home_page_escapes_stored_xss(client, xss_product):
    r = client.get("/")
    assert r.status_code == 200
    assert "<script>alert" not in r.text
