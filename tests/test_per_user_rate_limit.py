"""Per-user rate-limit isolation tests.

Verifies that `_get_rate_limit_key` correctly scopes quotas to
authenticated users (key = "user:<id>"), so user1 exhausting their
15/minute /api/chat quota does NOT block user2 from chatting.
"""
import pytest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.main import app, smart_search
from app.core.database import get_db
from app.core.config import settings
from app.core.models import Base
from app.core.security_middleware import limiter, _get_rate_limit_key
from app.services import auth_service, brute_force_guard, csrf_service

TEST_EMAIL_1 = "user1-limit@test.local"
TEST_EMAIL_2 = "user2-limit@test.local"
TEST_PASSWORD = "test12345678"
CHAT_LIMIT = 15  # @limiter.limit("15/minute") on /api/chat
SMART_SEARCH_LIMIT = 30  # @limiter.limit("30/minute") on /api/smart-search
IMAGE_SEARCH_LIMIT = 10  # @limiter.limit("10/minute") on /api/image-search

# Admin identities: admin1 uses the configured system email, admin2 uses the
# legacy literal username "admin" — both accepted by /admin/login. The rate
# limiter keys on the session's admin email, so they must get separate quotas.
ADMIN_EMAIL_1 = "admin@test.local"
ADMIN_EMAIL_2 = "admin"
ADMIN_PASSWORD = "test-admin-pw-123"  # monkeypatched in the client fixture
RUN_DISCOVERY_LIMIT = 10  # @limiter.limit("10/minute") on /admin/run-discovery
SAME_ORIGIN = {"Origin": "http://127.0.0.1"}  # passes require_admin_csrf


@pytest.fixture(autouse=True)
def _reset_limiter():
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture()
def db_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine


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
    monkeypatch.setattr(settings, "admin_secret_key", "test-admin-pw-123")
    monkeypatch.setattr(settings, "admin_email", "admin@test.local")
    limiter.reset()

    with TestClient(app, base_url="http://127.0.0.1", follow_redirects=False) as c:
        yield c

    app.dependency_overrides.clear()
    limiter.reset()


def _create_user(db, email: str):
    user = auth_service.create_user(db, email, TEST_PASSWORD)
    if user is None:
        user = auth_service.authenticate_user(db, email, TEST_PASSWORD)
    brute_force_guard.clear_attempts("127.0.0.1", email)
    return user


class TestRateLimitKeyFunction:

    def test_different_users_get_different_keys(self):
        """The key function must return distinct keys for different user IDs."""
        key1 = _get_rate_limit_key_with_session(user_id=1)
        key2 = _get_rate_limit_key_with_session(user_id=2)
        assert key1 != key2, (
            f"user:1 key ({key1!r}) must differ from user:2 key ({key2!r})"
        )
        assert key1 == "user:1", f"Expected 'user:1', got {key1!r}"
        assert key2 == "user:2", f"Expected 'user:2', got {key2!r}"

    def test_anonymous_uses_ip_key(self):
        """Without session, the key must be the IP address."""
        key = _get_rate_limit_key_with_session()
        assert key == "127.0.0.1", f"Anonymous key should be IP, got {key!r}"

    def test_admin_uses_admin_key(self):
        """Admin session must return 'admin:<email>' key."""
        key = _get_rate_limit_key_with_session(is_admin=True, admin_email="ops@site.com")
        assert key == "admin:ops@site.com", f"Expected admin key, got {key!r}"

    def test_signup_route_stays_ip_keyed_even_with_session(self):
        """POST /signup must always use IP key regardless of session."""
        key = _get_rate_limit_key_with_session(
            user_id=42, path="/signup"
        )
        assert key == "127.0.0.1", (
            f"Signup must always be IP-keyed, got {key!r}"
        )

    def test_every_limited_route_gets_per_user_key(self):
        """Contract: every @limiter.limit route — EXCEPT the anonymous-first
        endpoints (signup/login/admin-login/newsletter) that stay IP-keyed —
        must scope its quota to the session's user_id. So on ANY rate-limited
        route, user1's exhaustion never bleeds into user2's quota. If a new
        limited route falls back to an IP-wide key, this test fails."""
        route_limits = getattr(limiter, "_route_limits", None)
        assert route_limits, "slowapi internals changed — update the introspection test"
        IP_KEYED = {"/signup", "/login", "/admin/login", "/api/newsletter"}
        limited_paths = []
        for route in app.routes:
            ep = getattr(route, "endpoint", None)
            if ep is None:
                continue
            name = f"{ep.__module__}.{ep.__name__}"
            if name in route_limits:
                limited_paths.append(route.path)
        assert limited_paths, "no rate-limited routes registered — wiring broken?"
        for path in limited_paths:
            if path in IP_KEYED:
                continue
            key = _get_rate_limit_key_with_session(user_id=77, path=path)
            assert key == "user:77", (
                f"{path}: rate-limit key must be per-user, got {key!r} "
                "(a shared/IP key would let one user drain another's quota)"
            )

    def test_two_admins_get_distinct_keys(self):
        """Two different admin identities must map to different keys, so one
        admin's exhausted quota never blocks the other."""
        key1 = _get_rate_limit_key_with_session(is_admin=True, admin_email=ADMIN_EMAIL_1)
        key2 = _get_rate_limit_key_with_session(is_admin=True, admin_email=ADMIN_EMAIL_2)
        assert key1 != key2, (
            f"admin keys must differ: {key1!r} vs {key2!r}"
        )
        assert key1 == "admin:admin@test.local", f"Expected admin key, got {key1!r}"
        assert key2 == "admin:admin", f"Expected admin key, got {key2!r}"


# ---------------------------------------------------------------------------
# Helpers: simulate a Starlette request with a session attached
# ---------------------------------------------------------------------------

def _get_rate_limit_key_with_session(
    user_id: int | None = None,
    is_admin: bool = False,
    admin_email: str = "",
    path: str = "/api/chat",
) -> str:
    """Call _get_rate_limit_key with a fake request that carries a session."""
    from starlette.requests import Request
    from starlette.datastructures import MutableHeaders

    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [(b"host", b"127.0.0.1")],
        "client": ("127.0.0.1", 54321),
        "server": ("127.0.0.1", 8000),
    }
    sess = {}
    if is_admin:
        sess["is_admin"] = True
        sess["admin_email"] = admin_email or "admin@local"
    if user_id:
        sess["user_id"] = user_id
        sess["user_email"] = f"user{user_id}@test.local"
    scope["session"] = sess
    req = Request(scope)
    return _get_rate_limit_key(req)


class TestPerUserRateLimitIsolationE2E:

    def test_user1_exhaustion_does_not_block_user2(self, client, db_engine):
        """End-to-end: user1 burns 15 /api/chat, user2 still gets 200."""
        Session = sessionmaker(bind=db_engine)
        db = Session()

        user1 = _create_user(db, TEST_EMAIL_1)
        user2 = _create_user(db, TEST_EMAIL_2)
        assert user1.id != user2.id

        # Log in both users via the real login flow (with CSRF token)
        token = csrf_service.generate_csrf_token()

        # Login user1
        resp1 = client.post("/login", data={
            "email": TEST_EMAIL_1,
            "password": TEST_PASSWORD,
            "csrf_token": token,
        })
        # Follow redirect or check cookies set
        cookies1 = dict(client.cookies)

        # Login user2 (need fresh client to avoid session cross-contamination)
        client.cookies.clear()
        resp2 = client.post("/login", data={
            "email": TEST_EMAIL_2,
            "password": TEST_PASSWORD,
            "csrf_token": token,
        })
        cookies2 = dict(client.cookies)

        db.close()

        # Phase 1: User1 — exhaust all 15 chat requests
        client.cookies.update(cookies1)
        for i in range(CHAT_LIMIT):
            r = client.post("/api/chat", data={"query": f"u1 q{i}"})
            assert r.status_code in (200, 429), f"u1 req {i+1}: {r.status_code}"

        r_over = client.post("/api/chat", data={"query": "u1 over"})
        assert r_over.status_code == 429, (
            f"Expected 429 after {CHAT_LIMIT} requests, got {r_over.status_code}"
        )

        # Phase 2: User2 — must have fresh quota
        client.cookies.clear()
        client.cookies.update(cookies2)
        r = client.post("/api/chat", data={"query": "u2 first msg"})
        assert r.status_code == 200, (
            f"User2 should get 200. Got {r.status_code}. "
            f"Rate-limit key is NOT per-user."
        )

    def test_anonymous_users_share_ip_quota(self, client):
        """Without login, same IP = shared quota."""
        client.cookies.clear()
        for i in range(CHAT_LIMIT):
            r = client.post("/api/chat", data={"query": f"anon {i}"})
            assert r.status_code in (200, 429)

        r = client.post("/api/chat", data={"query": "anon over"})
        assert r.status_code == 429, f"Expected 429, got {r.status_code}"

    @pytest.mark.parametrize(
        "path,limit,req",
        [
            ("/api/smart-search", SMART_SEARCH_LIMIT, lambda c, i: c.get("/api/smart-search", params={"q": f"phone {i}"})),
            ("/api/image-search", IMAGE_SEARCH_LIMIT, lambda c, i: c.post("/api/image-search", files={"image": ("i.png", b"fake-png", "image/png")})),
        ],
        ids=["smart-search", "image-search"],
    )
    def test_user1_exhaustion_does_not_block_user2_on_route(self, client, db_engine, monkeypatch, path, limit, req):
        """End-to-end per route: user1 burns the route's whole minute quota,
        then user2 (same IP, different account) still gets 200 — proving the
        key is per-user and not per-IP. The services behind these routes are
        stubbed: only the limiter is under test."""
        import app.services.image_search_service as image_search_service_mod
        monkeypatch.setattr(smart_search, "search", lambda db, q, limit=8: [])
        monkeypatch.setattr(image_search_service_mod, "search_by_image", lambda data, db: [])

        Session = sessionmaker(bind=db_engine)
        db = Session()
        user1 = _create_user(db, TEST_EMAIL_1)
        user2 = _create_user(db, TEST_EMAIL_2)
        assert user1.id != user2.id
        token = csrf_service.generate_csrf_token()

        r1 = client.post("/login", data={"email": TEST_EMAIL_1, "password": TEST_PASSWORD, "csrf_token": token})
        assert r1.status_code == 303
        cookies1 = dict(client.cookies)
        client.cookies.clear()
        r2 = client.post("/login", data={"email": TEST_EMAIL_2, "password": TEST_PASSWORD, "csrf_token": token})
        assert r2.status_code == 303
        cookies2 = dict(client.cookies)
        db.close()

        # Phase 1: user1 — exhaust the route's whole quota
        client.cookies.clear()
        client.cookies.update(cookies1)
        for i in range(limit):
            r = req(client, i)
            assert r.status_code in (200, 429), f"u1 {path} req {i + 1}: {r.status_code}"

        r_over = req(client, 999)
        assert r_over.status_code == 429, (
            f"{path}: expected 429 for user1 after {limit} requests, got {r_over.status_code}"
        )

        # Phase 2: user2 — must have an independent quota
        client.cookies.clear()
        client.cookies.update(cookies2)
        r = req(client, 0)
        assert r.status_code == 200, (
            f"{path}: user2 blocked after user1's exhaustion — got {r.status_code}. "
            f"Rate-limit key is NOT per-user."
        )


def _login_admin(client, email: str) -> dict:
    """Log in through the real /admin/login flow; returns the session cookies."""
    client.cookies.clear()
    r = client.post("/admin/login", data={"email": email, "password": ADMIN_PASSWORD})
    assert r.status_code == 303, f"Admin login for {email!r} failed: {r.status_code}"
    return dict(client.cookies)


class TestPerAdminRateLimitIsolationE2E:
    """Same isolation guarantee as users, but for admins: the limiter keys on
    the session's admin email ("admin:<email>"), so admin1 exhausting the
    /admin/run-discovery quota must NOT block admin2."""

    def test_admin1_exhaustion_does_not_block_admin2(self, client, monkeypatch):
        """End-to-end: admin1 burns 10 run-discovery triggers, admin2 still 200."""
        # run-discovery enqueues a full import cycle as a background task;
        # TestClient executes background tasks, so no-op it for a fast, fully
        # offline test (the route/limiter/CSRF are what's under test).
        import app.workers.auto_import_worker as worker_mod
        monkeypatch.setattr(worker_mod, "run_full_cycle", lambda: None)

        cookies1 = _login_admin(client, ADMIN_EMAIL_1)
        assert cookies1, "admin1 login should set a session cookie"

        # Phase 1: admin1 — exhaust all 10 triggers/minute
        for i in range(RUN_DISCOVERY_LIMIT):
            r = client.post("/admin/run-discovery", headers=SAME_ORIGIN)
            assert r.status_code in (200, 429), f"admin1 req {i+1}: {r.status_code}"

        r_over = client.post("/admin/run-discovery", headers=SAME_ORIGIN)
        assert r_over.status_code == 429, (
            f"Expected 429 after {RUN_DISCOVERY_LIMIT} triggers, got {r_over.status_code}"
        )

        # Phase 2: admin2 — must have an independent quota
        cookies2 = _login_admin(client, ADMIN_EMAIL_2)
        assert cookies2 and cookies2 != cookies1, "admin2 must get its own session"

        r = client.post("/admin/run-discovery", headers=SAME_ORIGIN)
        assert r.status_code == 200, (
            f"Admin2 should still trigger discovery. Got {r.status_code}. "
            f"Rate-limit key is NOT per-admin."
        )

    def test_unauthenticated_triggers_do_not_consume_admin_quota(self, client, monkeypatch):
        """Requests rejected by the CSRF/auth gate (401) must not decrement
        the admin's quota — otherwise a flood of bot hits would lock the
        panel out before the real admin even logs in."""
        import app.workers.auto_import_worker as worker_mod
        monkeypatch.setattr(worker_mod, "run_full_cycle", lambda: None)

        # 25 unauthenticated attempts (no session, no Origin) — all 401
        for i in range(25):
            r = client.post("/admin/run-discovery")
            assert r.status_code == 401, f"unauth req {i+1}: {r.status_code}"

        # Admin1 logs in and still has the full 10/min quota
        cookies1 = _login_admin(client, ADMIN_EMAIL_1)
        client.cookies.update(cookies1)
        r = client.post("/admin/run-discovery", headers=SAME_ORIGIN)
        assert r.status_code == 200, (
            f"Unauthenticated 401s consumed the admin quota! Got {r.status_code}"
        )
