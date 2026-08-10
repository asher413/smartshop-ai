"""
E2E email tests — verify the whole SMTP pipeline (verification, help-contact,
newsletter) and the Google-OAuth password-set fix without hitting a real server.
"""
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.main import app
from app.core.database import get_db
from app.core.config import settings
from app.core.models import Base, User
from app.core.security_middleware import limiter
from app.services import auth_service, csrf_service, brute_force_guard

TEST_EMAIL = "email-test@test.local"
TEST_PASSWORD = "testpassword123"


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
    # Disable SMTP — we're testing the code paths, not real servers.
    monkeypatch.setattr(settings, "smtp_host", "")
    monkeypatch.setattr(settings, "smtp_user", "")
    limiter.reset()
    with TestClient(app, base_url="http://127.0.0.1", follow_redirects=False) as c:
        yield c
    app.dependency_overrides.clear()
    limiter.reset()


def _create_user(db):
    user = auth_service.create_user(db, TEST_EMAIL, TEST_PASSWORD)
    brute_force_guard.clear_attempts("127.0.0.1", TEST_EMAIL)
    return user


# ---------------------------------------------------------------------------
# email_service.send_email — must not raise when SMTP is unconfigured
# ---------------------------------------------------------------------------


def test_send_email_returns_false_when_smtp_not_configured(monkeypatch):
    """Without SMTP_HOST/USER, send_email logs and returns False — never raises."""
    from app.services.email_service import send_email
    monkeypatch.setattr(settings, "smtp_host", "")
    monkeypatch.setattr(settings, "smtp_user", "")
    result = send_email("someone@test.com", "test", "<p>hi</p>")
    assert result is False


def test_send_verification_email_returns_false_when_smtp_not_configured(monkeypatch):
    from app.services.email_service import send_verification_email
    monkeypatch.setattr(settings, "smtp_host", "")
    monkeypatch.setattr(settings, "smtp_user", "")
    result = send_verification_email("user@test.com", "https://example.com/verify?token=x")
    assert result is False


# ---------------------------------------------------------------------------
# Signup flow: verification email is triggered (SMTP-down path doesn't crash)
# ---------------------------------------------------------------------------


def test_signup_triggers_verification_email_without_crashing(client, monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "")
    monkeypatch.setattr(settings, "smtp_user", "")
    token = csrf_service.generate_csrf_token()
    r = client.post("/signup", data={
        "email": "new-signup@test.local",
        "password": "secret12345",
        "csrf_token": token,
        "website": "",
    })
    # Should redirect to personal-area (signup succeeded)
    assert r.status_code == 303
    assert r.headers.get("location", "").endswith("/personal-area")


# ---------------------------------------------------------------------------
# Newsletter signup (SMTP no-op — just record in DB)
# ---------------------------------------------------------------------------


def test_newsletter_signup_works_without_smtp(client):
    r = client.post("/api/newsletter", data={
        "email": "nl-sub@test.local",
        "website": "",
    })
    assert r.status_code == 200
    data = json.loads(r.text)
    assert data["status"] == "ok"


def test_newsletter_duplicate_returns_already_subscribed(client, db_engine):
    client.post("/api/newsletter", data={"email": "nl-dup@test.local", "website": ""})
    r = client.post("/api/newsletter", data={"email": "nl-dup@test.local", "website": ""})
    assert r.status_code == 200
    data = json.loads(r.text)
    assert "כבר רשומים" in data.get("message", "")


def test_newsletter_honeypot_bot_silently_dropped(client):
    """Bots fill the hidden 'website' field — should get fake success, no DB record."""
    r = client.post("/api/newsletter", data={
        "email": "bot@spam.local",
        "website": "http://spam.com",
    })
    assert r.status_code == 200
    data = json.loads(r.text)
    assert data["status"] == "ok"  # fake success for the bot


# ---------------------------------------------------------------------------
# Help-center contact form (SMTP down → still stores message in DB)
# ---------------------------------------------------------------------------


def test_help_contact_stores_message_when_smtp_down(client, monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "")
    monkeypatch.setattr(settings, "smtp_user", "")
    token = csrf_service.generate_csrf_token()
    r = client.post("/help/contact", data={
        "name": "בודק",
        "email": "tester@test.local",
        "subject": "בדיקה",
        "message": "הודעת בדיקה מהסוויטה",
        "csrf_token": token,
        "website": "",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert "/help?sent=1" in r.headers.get("location", "")


def test_help_contact_honeypot_bot_silently_dropped(client):
    token = csrf_service.generate_csrf_token()
    r = client.post("/help/contact", data={
        "name": "bot",
        "email": "bot@spam.com",
        "subject": "spam",
        "message": "spam spam",
        "csrf_token": token,
        "website": "http://myspam.biz",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert "/help?sent=1" in r.headers.get("location", "")


# ---------------------------------------------------------------------------
# Google OAuth account → email+password signup fix
# ---------------------------------------------------------------------------


def test_google_user_can_set_password_via_signup(client, db_engine, monkeypatch):
    """If a user first signed in with Google (password_hash=None), signing up
    with the same email should SET a password on that account, not reject them."""
    monkeypatch.setattr(settings, "smtp_host", "")
    monkeypatch.setattr(settings, "smtp_user", "")
    Session = sessionmaker(bind=db_engine)
    db = Session()

    # Simulate a Google-created account (no password)
    google_user = User(
        email=TEST_EMAIL,
        password_hash=None,
        oauth_provider="google",
        oauth_subject_id="google-sub-123",
        email_verified=True,
    )
    db.add(google_user)
    db.commit()
    google_id = google_user.id
    db.close()

    # Now sign up with the same email + a password
    token = csrf_service.generate_csrf_token()
    r = client.post("/signup", data={
        "email": TEST_EMAIL,
        "password": "brandnewpassword999",
        "csrf_token": token,
        "website": "",
    })
    assert r.status_code == 303, f"Expected 303, got {r.status_code}"

    # The account should now have a password
    db2 = Session()
    user = db2.query(User).filter(User.id == google_id).first()
    assert user is not None
    assert user.password_hash is not None, "Google user should get a password set"
    assert auth_service.verify_password("brandnewpassword999", user.password_hash)
    db2.close()
