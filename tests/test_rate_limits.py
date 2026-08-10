"""
Rate-limit test-suite — covers EVERY route that carries @limiter.limit,
including the admin test routes (/admin/settings/test, /admin/suppliers/
pull-test, which got their limits added alongside this suite).

For each limited route:
  * the (limit)-th+1 request returns 429 and nothing before it does
  * tests stay independent: the client fixture calls limiter.reset() at
    setup AND teardown, and the isolation test proves reset() clears the
    window even mid-test.

Introspection test: any route registered in slowapi's limiter._route_limits
must be in this suite, and any entry in this suite must map to a real
limited route — coverage can't silently shrink or grow stale.

Implementation notes:
  * slowapi's limiter runs INSIDE the route wrapper, after FastAPI resolves
    the auth/CSRF dependencies. So admin routes are tested WITH an admin
    session + valid CSRF token (their handler runs, the limiter counts).
  * Every network-touching service (chat Gemini, smart-search chromadb/Gemini,
    image-search downloads, settings/supplier live tests) is stubbed — the
    suite exercises the limiter, never the network.
  * /login rotates emails per attempt so the brute-force guard (5 fails per
    ip+email) never trips before the 10/min rate limit.
"""
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.main import app, chatbot, smart_search
from app.core.config import settings
from app.core.database import get_db
from app.core.models import Base
from app.core.security_middleware import limiter
from app.services import csrf_service

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
ADMIN_PW = "test-admin-pw-123"


def _csrf() -> str:
    return csrf_service.generate_csrf_token()


# (method, path, limit, builder(client, i) -> response)
# "i" lets a builder vary input per request (e.g. unique emails so the
# brute-force guard or unique-email rules never trip before the limiter).
PUBLIC_LIMITED_ROUTES = [
    ("POST", "/admin/login", 10, lambda c, i: c.post("/admin/login", data={"email": "admin@test.local", "password": "wrong"})),
    ("POST", "/signup", 5, lambda c, i: c.post("/signup", data={"email": "rl@test.local", "password": "supersecret1", "csrf_token": _csrf(), "website": ""})),
    ("POST", "/api/resend-verification", 3, lambda c, i: c.post("/api/resend-verification")),
    ("POST", "/login", 10, lambda c, i: c.post("/login", data={"email": f"rl{i}@test.local", "password": "wrongpass1", "csrf_token": _csrf()})),
    ("GET", "/auth/google/login", 15, lambda c, i: c.get("/auth/google/login")),
    ("GET", "/api/smart-search", 30, lambda c, i: c.get("/api/smart-search", params={"q": "phone"})),
    ("POST", "/api/image-search", 10, lambda c, i: c.post("/api/image-search", files={"image": ("i.png", b"fake-png", "image/png")})),
    ("GET", "/api/search-suggest", 30, lambda c, i: c.get("/api/search-suggest", params={"q": "ph"})),
    ("GET", "/go/99999", 30, lambda c, i: c.get("/go/99999")),
    ("GET", "/api/price-war/99999", 20, lambda c, i: c.get("/api/price-war/99999")),
    ("POST", "/api/chat", 15, lambda c, i: c.post("/api/chat", data={"query": "שלום"})),
    ("POST", "/api/track-view/99999", 60, lambda c, i: c.post("/api/track-view/99999")),
    ("GET", "/api/social-proof", 60, lambda c, i: c.get("/api/social-proof")),
    ("POST", "/help/contact", 5, lambda c, i: c.post("/help/contact", data={"name": "test", "email": f"rl{i}@test.local", "subject": "t", "message": "hello", "csrf_token": _csrf()})),
    ("POST", "/api/newsletter", 5, lambda c, i: c.post("/api/newsletter", data={"email": f"rl{i}@test.local", "website": ""})),
    ("GET", "/api/notifications", 60, lambda c, i: c.get("/api/notifications")),
    ("POST", "/api/notifications/99999/read", 60, lambda c, i: c.post("/api/notifications/99999/read")),
    ("GET", "/api/popup", 60, lambda c, i: c.get("/api/popup")),
    ("POST", "/api/popup/99999/dismiss", 30, lambda c, i: c.post("/api/popup/99999/dismiss")),
    ("POST", "/api/ads/99999/click", 60, lambda c, i: c.post("/api/ads/99999/click")),
    ("GET", "/api/site-ads", 60, lambda c, i: c.get("/api/site-ads")),
    ("GET", "/api/instant-search", 60, lambda c, i: c.get("/api/instant-search", params={"q": "ph"})),
    ("GET", "/img/aHR0cHM6Ly9leGFtcGxlLmNvbS9pLnBuZw", 300, lambda c, i: c.get("/img/aHR0cHM6Ly9leGFtcGxlLmNvbS9pLnBuZw")),
    ("POST", "/api/push/subscribe", 30, lambda c, i: c.post("/api/push/subscribe", json={"endpoint": "https://example.com", "keys": {"p256dh": "a", "auth": "b"}})),
    ("POST", "/api/push/unsubscribe", 30, lambda c, i: c.post("/api/push/unsubscribe", json={"endpoint": "https://example.com"})),
    ("POST", "/api/spin-reward", 5, lambda c, i: c.post("/api/spin-reward")),
]

# Admin routes whose handler runs only after the CSRF gate — so the requests
# carry an admin session + valid CSRF token, and the live tests are stubbed.
ADMIN_LIMITED_ROUTES = [
    ("POST", "/admin/settings/test/ai", 10, lambda c, i: c.post("/admin/settings/test/ai", data={"csrf_token": _csrf()})),
    ("POST", "/admin/suppliers/pull-test/aliexpress", 10, lambda c, i: c.post("/admin/suppliers/pull-test/aliexpress", data={"csrf_token": _csrf()})),
    ("POST", "/admin/run-discovery", 10, lambda c, i: c.post("/admin/run-discovery", data={"csrf_token": _csrf()})),
    ("POST", "/admin/reindex-meili", 5, lambda c, i: c.post("/admin/reindex-meili", data={"csrf_token": _csrf()})),
    ("POST", "/admin/reset-meili", 3, lambda c, i: c.post("/admin/reset-meili", data={"csrf_token": _csrf()})),
]

ALL_ROUTES = PUBLIC_LIMITED_ROUTES + ADMIN_LIMITED_ROUTES


@pytest.fixture()
def db_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return engine


@pytest.fixture()
def client(db_engine, monkeypatch):
    Session = sessionmaker(bind=db_engine)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(settings, "admin_secret_key", ADMIN_PW)
    monkeypatch.setattr(settings, "admin_email", "admin@test.local")
    limiter.reset()  # every test starts with a clean rate-limit window

    with TestClient(app, base_url="http://127.0.0.1:8000", follow_redirects=False) as c:
        c.headers.update({"User-Agent": BROWSER_UA})
        yield c

    app.dependency_overrides.clear()
    limiter.reset()  # ...and leaves no counters behind for the next test


def _stub_network(monkeypatch):
    """This suite tests the limiter, never the network: stub every service
    a limited route could call (Gemini chat, semantic/chromadb search,
    image downloads, live supplier/settings key tests)."""
    monkeypatch.setattr(chatbot, "ask", lambda *a, **k: "תשובה לדוגמה")
    monkeypatch.setattr(smart_search, "search", lambda db, q, limit=8: [])
    import app.services.image_search_service as _iss
    monkeypatch.setattr(_iss, "search_by_image", lambda data, db: [])
    import app.services.settings_service as _ss
    monkeypatch.setattr(_ss, "run_test", lambda service, overrides: (True, "mock ok"))
    import app.services.supplier_status_service as _ssvc
    monkeypatch.setattr(_ssvc, "test_supplier_pull", lambda supplier: {"status": "ok"})
    import app.workers.auto_import_worker as _w
    monkeypatch.setattr(_w, "run_full_cycle", lambda: None)  # run-discovery background task
    import app.services.meili_search_service as _ms
    # Stub the singleton so reindex/reset routes don't hit a real Meilisearch
    _svc = _ms._get_service()
    monkeypatch.setattr(_svc, "reindex_all", lambda db: {"status": "ok"})
    monkeypatch.setattr(_svc, "reset_index", lambda: True)


def _exhaust(client, req, limit, path):
    for i in range(limit):
        r = req(client, i)
        assert r.status_code != 429, f"{path}: hit 429 before the limit (req {i + 1}/{limit})"
    r = req(client, limit)
    assert r.status_code == 429, f"{path}: expected 429 after {limit} requests, got {r.status_code}"


# ---------------------------------------------------------------------------
# Introspection — every limited route is covered, and vice versa
# ---------------------------------------------------------------------------

def test_every_rate_limited_route_is_covered():
    """Two-way match between slowapi's registration (limiter._route_limits,
    keyed by 'module.function') and this suite's concrete entries — app
    route patterns ({product_id}) match suite paths (99999) via regex.
    A new @limiter.limit route without a test entry, or a stale suite
    entry, fails here."""
    route_limits = getattr(limiter, "_route_limits", None)
    assert route_limits, "slowapi internals changed — update the introspection test"
    app_limited = []  # (method, path_pattern)
    for route in app.routes:
        ep = getattr(route, "endpoint", None)
        if ep is None:
            continue
        name = f"{ep.__module__}.{ep.__name__}"
        if name in route_limits:
            for m in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                if m in (getattr(route, "methods", set()) or set()):
                    app_limited.append((m, getattr(route, "path", "")))

    assert app_limited, "no rate-limited routes registered — limiter wiring broken?"

    def _pattern(path):
        return re.sub(r"\{[^}]+\}", r"[^/]+", path)

    # Every suite entry must map to a real limited app route.
    for m, p, _, _ in ALL_ROUTES:
        assert any(m2 == m and re.fullmatch(_pattern(p2), p) for m2, p2 in app_limited), \
            f"suite entry {m} {p} matches no rate-limited route"
    # Every limited app route must be exercised by the suite.
    for m, p2 in app_limited:
        assert any(m2 == m and re.fullmatch(_pattern(p2), p) for m2, p, _, _ in ALL_ROUTES), \
            f"rate-limited route {m} {p2} has no test entry"


# ---------------------------------------------------------------------------
# 429-after-limit, parametrized over every limited route
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("method,path,limit,req", PUBLIC_LIMITED_ROUTES,
                         ids=[f"{m} {p}" for m, p, _, _ in PUBLIC_LIMITED_ROUTES])
def test_public_rate_limited_429_after_exhaustion(client, monkeypatch, method, path, limit, req):
    _stub_network(monkeypatch)
    _exhaust(client, req, limit, path)


@pytest.mark.parametrize("method,path,limit,req", ADMIN_LIMITED_ROUTES,
                         ids=[f"{m} {p}" for m, p, _, _ in ADMIN_LIMITED_ROUTES])
def test_admin_rate_limited_429_after_exhaustion(client, monkeypatch, method, path, limit, req):
    """Admin test routes: the handler runs only after the CSRF gate passes,
    so login as admin + valid CSRF token — then exhaust the limit."""
    _stub_network(monkeypatch)
    r = client.post("/admin/login", data={"email": "admin@test.local", "password": ADMIN_PW})
    assert r.status_code == 303
    _exhaust(client, req, limit, path)


@pytest.mark.parametrize("method,path,limit,req", ADMIN_LIMITED_ROUTES,
                         ids=[f"{m} {p}" for m, p, _, _ in ADMIN_LIMITED_ROUTES])
def test_admin_unauth_401s_do_not_consume_limit(client, monkeypatch, method, path, limit, req):
    """Pin the design decision: the limiter runs INSIDE the route wrapper,
    AFTER the CSRF gate. So unauthenticated hammering (401s) never burns
    the admin's window — only real admin requests count. If this behavior
    ever changes (e.g. middleware-based limiting), this test catches it."""
    _stub_network(monkeypatch)
    for _ in range(limit + 5):
        assert client.post(path, data={}).status_code == 401  # CSRF gate rejects
    # The window is untouched: a fresh admin request is NOT rate-limited.
    r = client.post("/admin/login", data={"email": "admin@test.local", "password": ADMIN_PW})
    assert r.status_code == 303
    assert req(client, 0).status_code != 429


# ---------------------------------------------------------------------------
# Isolation — limiter.reset() must fully separate tests
# ---------------------------------------------------------------------------

def test_limiter_reset_isolates_rate_limits(client):
    """Prove reset() clears the window: exhaust /api/newsletter (5/min),
    observe the 429, then reset() and observe the same route works again —
    so a test that burns a limit can never leak into the next test."""
    for i in range(5):
        r = client.post("/api/newsletter", data={"email": f"iso{i}@test.local", "website": ""})
        assert r.status_code == 200
    r = client.post("/api/newsletter", data={"email": "over@test.local", "website": ""})
    assert r.status_code == 429

    limiter.reset()
    r = client.post("/api/newsletter", data={"email": "fresh@test.local", "website": ""})
    assert r.status_code == 200


def test_two_sequential_clients_are_isolated(client, db_engine, monkeypatch):
    """Fixture-level isolation: burn one route's limit in this test's client,
    then prove a brand-new client (fresh limiter window) hits it fine."""
    for i in range(5):
        client.post("/api/newsletter", data={"email": f"seq{i}@test.local", "website": ""})
    assert client.post("/api/newsletter", data={"email": "over@test.local", "website": ""}).status_code == 429

    Session = sessionmaker(bind=db_engine)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    limiter.reset()
    try:
        with TestClient(app, base_url="http://127.0.0.1:8000", follow_redirects=False) as c2:
            c2.headers.update({"User-Agent": BROWSER_UA})
            r = c2.post("/api/newsletter", data={"email": "fresh2@test.local", "website": ""})
            assert r.status_code == 200
    finally:
        app.dependency_overrides.clear()
        limiter.reset()
