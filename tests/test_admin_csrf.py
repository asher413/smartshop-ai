"""
Dedicated tests for the `require_admin_csrf` dependency — the gate in
front of EVERY state-changing admin POST endpoint.

The gate must:
  * 401 when the caller isn't an admin at all (no session, no Basic)
  * 403 when an admin-authenticated browser POST can't prove same-origin
    AND carries no valid CSRF token (fails closed)
  * 403 on a forged cross-origin POST (evil Origin header)
  * pass with a valid signed CSRF token (header or form field)
  * pass on Sec-Fetch-Site: same-origin / same-site (browser proof)
  * pass for Basic-auth cron clients (no ambient cookie => no CSRF to
    forge), with the correct password, and 401 with a wrong one

These tests use the real app via TestClient against an in-memory DB
(StaticPool so the TestClient worker thread sees the same tables), with
deterministic admin creds and a browser UA so the bot guard scores 0.
"""
import base64

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.main import app
from app.core.config import settings
from app.core.database import get_db
from app.core.models import Base, User
from app.core.security_middleware import limiter
from app.services import auth_service, csrf_service

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
ADMIN_PW = "test-admin-pw-123"


@pytest.fixture()
def db_engine():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
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
    limiter.reset()

    with TestClient(app, base_url="http://127.0.0.1:8000", follow_redirects=False) as c:
        c.headers.update({"User-Agent": BROWSER_UA})
        yield c

    app.dependency_overrides.clear()
    limiter.reset()


def _basic_auth(user: str = "admin", password: str = ADMIN_PW) -> str:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return f"Basic {token}"


def _admin_session(client) -> None:
    """Log in as admin via the form so the client holds an admin session
    cookie (no Authorization header — the CSRF tests must exercise the
    session path, not the Basic-exemption path)."""
    r = client.post("/admin/login", data={"email": "admin@test.local", "password": ADMIN_PW})
    assert r.status_code == 303


def _make_user(db_engine) -> int:
    Session = sessionmaker(bind=db_engine)
    db = Session()
    user = auth_service.create_user(db, f"u{id(db)}@test.local", "supersecret1")
    uid = user.id
    db.close()
    return uid


# --- 401: not an admin at all -------------------------------------------

def test_post_without_any_auth_returns_401(client, db_engine):
    uid = _make_user(db_engine)
    r = client.post(f"/admin/users/{uid}/toggle-active")
    assert r.status_code == 401


def test_post_with_wrong_basic_password_returns_401(client, db_engine):
    uid = _make_user(db_engine)
    r = client.post(
        f"/admin/users/{uid}/toggle-active",
        headers={"Authorization": _basic_auth(password="wrong-password")},
    )
    assert r.status_code == 401


def test_get_admin_settings_without_auth_returns_401(client):
    r = client.get("/admin/settings")
    assert r.status_code == 401


# --- 403: admin session but no origin proof and no token ---------------

def test_admin_post_without_origin_or_token_is_403(client, db_engine):
    _admin_session(client)
    uid = _make_user(db_engine)
    # No Origin, no Sec-Fetch-Site, no token -> must fail closed.
    r = client.post(f"/admin/users/{uid}/toggle-active")
    assert r.status_code == 403


def test_admin_post_with_invalid_token_is_403(client, db_engine):
    _admin_session(client)
    uid = _make_user(db_engine)
    r = client.post(
        f"/admin/users/{uid}/toggle-active",
        headers={"X-CSRF-Token": "forged-or-expired"},
    )
    assert r.status_code == 403


def test_admin_post_with_expired_token_is_403(client, db_engine, monkeypatch):
    _admin_session(client)
    uid = _make_user(db_engine)
    # Genuine expiry: mint a VALID token, then force the validator to treat
    # every token as expired (max_age=-1) so loads() raises SignatureExpired
    # — exactly the real-world "token was valid but is old now" case.
    token = csrf_service.generate_csrf_token()
    import app.services.csrf_service as csrf_mod
    monkeypatch.setattr(csrf_mod, "TOKEN_MAX_AGE_SECONDS", -1)
    r = client.post(
        f"/admin/users/{uid}/toggle-active",
        headers={"X-CSRF-Token": token},
    )
    assert r.status_code == 403


def test_admin_post_with_wrong_secret_token_is_403(client, db_engine):
    _admin_session(client)
    uid = _make_user(db_engine)
    # A token signed by an attacker's key must be rejected (BadSignature).
    from itsdangerous import URLSafeTimedSerializer
    forged = URLSafeTimedSerializer("attacker-secret", salt="csrf-token").dumps("0")
    r = client.post(
        f"/admin/users/{uid}/toggle-active",
        headers={"X-CSRF-Token": forged},
    )
    assert r.status_code == 403


# --- 403: forged cross-origin -------------------------------------------

def test_admin_post_with_evil_origin_is_403(client, db_engine):
    _admin_session(client)
    uid = _make_user(db_engine)
    r = client.post(
        f"/admin/users/{uid}/toggle-active",
        headers={"Origin": "https://evil.example"},
    )
    assert r.status_code == 403


def test_admin_post_with_evil_origin_even_with_plain_cookie(client, db_engine):
    # Cross-site request with the victim's session cookie and an evil
    # Origin header: the gate must reject it even though the cookie is
    # "legit" — the Origin mismatch is the attack signal.
    _admin_session(client)
    uid = _make_user(db_engine)
    r = client.post(
        f"/admin/users/{uid}/toggle-active",
        headers={
            "Origin": "https://evil.example",
            "Sec-Fetch-Site": "cross-site",
        },
    )
    assert r.status_code == 403


# --- 200: valid CSRF token (header / form field) ------------------------

def test_admin_post_with_valid_token_header_is_200(client, db_engine):
    _admin_session(client)
    uid = _make_user(db_engine)
    r = client.post(
        f"/admin/users/{uid}/toggle-active",
        headers={"X-CSRF-Token": csrf_service.generate_csrf_token()},
    )
    assert r.status_code == 200


def test_admin_post_with_valid_token_in_form_is_200(client, db_engine):
    _admin_session(client)
    uid = _make_user(db_engine)
    r = client.post(
        f"/admin/users/{uid}/toggle-active",
        data={"csrf_token": csrf_service.generate_csrf_token()},
    )
    assert r.status_code == 200


def test_admin_post_with_matching_origin_is_200(client, db_engine):
    _admin_session(client)
    uid = _make_user(db_engine)
    r = client.post(
        f"/admin/users/{uid}/toggle-active",
        headers={"Origin": "http://127.0.0.1:8000"},
    )
    assert r.status_code == 200


def test_admin_post_with_sec_fetch_site_same_origin_is_200(client, db_engine):
    _admin_session(client)
    uid = _make_user(db_engine)
    r = client.post(
        f"/admin/users/{uid}/toggle-active",
        headers={"Sec-Fetch-Site": "same-origin"},
    )
    assert r.status_code == 200


def test_admin_post_with_sec_fetch_site_same_site_is_200(client, db_engine):
    _admin_session(client)
    uid = _make_user(db_engine)
    r = client.post(
        f"/admin/users/{uid}/toggle-active",
        headers={"Sec-Fetch-Site": "same-site"},
    )
    assert r.status_code == 200


# --- 200: Basic-auth cron clients (no ambient cookie) -------------------

def test_basic_cron_client_is_exempt_from_csrf(client, db_engine):
    # Cron jobs (cron-job.org / curl -u) have no browser session cookie,
    # so there is no ambient credential for an attacker to reuse — the
    # gate lets a valid Basic credential through without a token.
    uid = _make_user(db_engine)
    r = client.post(
        f"/admin/users/{uid}/toggle-active",
        headers={"Authorization": _basic_auth()},
    )
    assert r.status_code == 200


def test_basic_cron_with_system_email_user_works(client, db_engine):
    uid = _make_user(db_engine)
    r = client.post(
        f"/admin/users/{uid}/toggle-active",
        headers={"Authorization": _basic_auth(user="admin@test.local")},
    )
    assert r.status_code == 200


def test_basic_cron_wrong_password_still_401(client, db_engine):
    uid = _make_user(db_engine)
    r = client.post(
        f"/admin/users/{uid}/toggle-active",
        headers={"Authorization": _basic_auth(password="nope")},
    )
    assert r.status_code == 401


# --- The gate must not block the actual action --------------------------

def test_toggle_user_actually_flips_active(client, db_engine):
    _admin_session(client)
    Session = sessionmaker(bind=db_engine)
    db = Session()
    user = auth_service.create_user(db, "flip@test.local", "supersecret1")
    uid = user.id
    db.close()

    r = client.post(
        f"/admin/users/{uid}/toggle-active",
        headers={"X-CSRF-Token": csrf_service.generate_csrf_token()},
    )
    assert r.status_code == 200
    assert r.json()["is_active"] is False

    db = Session()
    db_user = db.query(User).filter(User.id == uid).first()
    assert db_user.is_active is False
    db.close()
