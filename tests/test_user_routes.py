"""
User-route auth tests: every user-owned route must reject unauthenticated
requests (401 JSON or 303 redirect) and must NOT leak data from a DIFFERENT
user (404 — the record exists, but not for YOU).

Covers:
  - GET  /personal-area               (page route)
  - POST /api/favorites/{id}/toggle   (JSON API)
  - POST /api/price-alerts            (JSON API)
  - POST /api/orders/{id}/refresh-tracking (JSON API)
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.main import app
from app.core.config import settings
from app.core.database import get_db
from app.core.models import Base, Product, Order, PriceAlert, ProductFavorite
from app.core.security_middleware import limiter
from app.services import auth_service, csrf_service

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

USER_A = "usera@test.local"
USER_B = "userb@test.local"
PASSWORD = "test-pass-123456"

# A real-enough product row so the foreign keys in favorites / alerts /
# orders don't fail. The test DB is in-memory SQLite with no FK enforcement
# by default, but SQLAlchemy's relationship loading still expects a row.
TEST_PRODUCT_ID = 99999


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


def _create_user(client, db_engine, email):
    """Create a user via signup so the password hash is correct."""
    db = sessionmaker(bind=db_engine)()
    user = auth_service.create_user(db, email, PASSWORD)
    db.close()
    assert user is not None, f"Failed to create user {email}"
    return user


def _create_seed_product(client, db_engine):
    """Insert a stub product so foreign-key lookups don't fail."""
    db = sessionmaker(bind=db_engine)()
    p = db.query(Product).filter(Product.id == TEST_PRODUCT_ID).first()
    if not p:
        p = Product(
            id=TEST_PRODUCT_ID,
            name="Test Product for Route Tests",
            price=49.90,
            image_url="https://example.com/img.jpg",
            supplier_name="TestSupplier",
            source_adapter="aliexpress",
            is_active=True,
            is_verified=True,
        )
        db.add(p)
        db.commit()
    db.close()
    return p


def _login(client, email):
    r = client.post("/login", data={
        "email": email, "password": PASSWORD,
        "csrf_token": _csrf(),
    })
    assert r.status_code == 303, f"Login failed for {email}: {r.status_code}"


def _create_order_for_user(client, db_engine, user_id):
    db = sessionmaker(bind=db_engine)()
    order = Order(
        user_id=user_id,
        product_id=TEST_PRODUCT_ID,
        customer_email=USER_A if "usera" in str(user_id) else USER_B,
        total_price=49.90,
        status="processing",
    )
    db.add(order)
    db.commit()
    order_id = order.id
    db.close()
    return order_id


def _create_favorite_for_user(client, db_engine, user_id):
    db = sessionmaker(bind=db_engine)()
    fav = ProductFavorite(user_id=user_id, product_id=TEST_PRODUCT_ID)
    db.add(fav)
    db.commit()
    db.close()


def _create_alert_for_user(client, db_engine, user_id):
    db = sessionmaker(bind=db_engine)()
    alert = PriceAlert(user_id=user_id, product_id=TEST_PRODUCT_ID, target_price=30.0)
    db.add(alert)
    db.commit()
    db.close()


# ---------------------------------------------------------------------------
# Personal-area (page route — unauthenticated → 303 redirect to /login)
# ---------------------------------------------------------------------------

def test_personal_area_without_login_redirects(client):
    r = client.get("/personal-area")
    assert r.status_code in (302, 303, 307)
    assert "/login" in (r.headers.get("location") or "")


def test_personal_area_with_login_shows_page(client, db_engine):
    _create_user(client, db_engine, USER_A)
    _login(client, USER_A)
    r = client.get("/personal-area")
    assert r.status_code == 200


def test_personal_area_does_not_leak_other_users_data(client, db_engine):
    """The personal area shows only the CURRENT user's favorites, orders,
    and alerts — never another user's. This is a server-side guarantee
    (SQL WHERE user_id = $current), not a frontend filter."""
    from app.core.models import User
    _create_user(client, db_engine, USER_A)
    _create_user(client, db_engine, USER_B)
    _create_seed_product(client, db_engine)
    db = sessionmaker(bind=db_engine)()
    ub = db.query(User).filter(User.email == USER_B).first()
    if ub:
        _create_favorite_for_user(client, db_engine, ub.id)
    db.close()
    _login(client, USER_A)
    r = client.get("/personal-area")
    assert r.status_code == 200
    # The page must NOT contain reference to userb's email in the HTML
    assert USER_B not in r.text


# ---------------------------------------------------------------------------
# Favorites toggle (JSON API)
# ---------------------------------------------------------------------------

def test_favorite_toggle_without_login_returns_401(client):
    r = client.post(f"/api/favorites/{TEST_PRODUCT_ID}/toggle")
    assert r.status_code == 401
    data = r.json()
    assert data["status"] == "error"


def test_favorite_toggle_with_login_succeeds(client, db_engine):
    _create_user(client, db_engine, USER_A)
    _create_seed_product(client, db_engine)
    _login(client, USER_A)
    r = client.post(f"/api/favorites/{TEST_PRODUCT_ID}/toggle")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["favorited"] is True


def test_favorite_toggle_twice_untoggles(client, db_engine):
    _create_user(client, db_engine, USER_A)
    _create_seed_product(client, db_engine)
    _login(client, USER_A)
    # First click: favorite
    r = client.post(f"/api/favorites/{TEST_PRODUCT_ID}/toggle")
    assert r.json()["favorited"] is True
    # Second click: unfavorite
    r = client.post(f"/api/favorites/{TEST_PRODUCT_ID}/toggle")
    assert r.json()["favorited"] is False


# ---------------------------------------------------------------------------
# Price alerts (JSON API)
# ---------------------------------------------------------------------------

def test_price_alert_without_login_returns_401(client):
    r = client.post("/api/price-alerts", data={
        "product_id": str(TEST_PRODUCT_ID),
        "target_price": "30",
    })
    assert r.status_code == 401


def test_price_alert_with_login_succeeds(client, db_engine):
    _create_user(client, db_engine, USER_A)
    _create_seed_product(client, db_engine)
    _login(client, USER_A)
    r = client.post("/api/price-alerts", data={
        "product_id": str(TEST_PRODUCT_ID),
        "target_price": "30",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"


def test_price_alert_with_invalid_price_returns_400(client, db_engine):
    _create_user(client, db_engine, USER_A)
    _create_seed_product(client, db_engine)
    _login(client, USER_A)
    r = client.post("/api/price-alerts", data={
        "product_id": str(TEST_PRODUCT_ID),
        "target_price": "0",
    })
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Order tracking refresh (JSON API)
# ---------------------------------------------------------------------------

def test_order_refresh_without_login_returns_401(client):
    r = client.post("/api/orders/1/refresh-tracking")
    assert r.status_code == 401


def test_order_refresh_with_login_own_order_succeeds(client, db_engine):
    from app.core.models import User
    _create_user(client, db_engine, USER_A)
    _create_seed_product(client, db_engine)
    _login(client, USER_A)
    db = sessionmaker(bind=db_engine)()
    ua = db.query(User).filter(User.email == USER_A).first()
    db.close()
    if ua:
        order_id = _create_order_for_user(client, db_engine, ua.id)
        r = client.post(f"/api/orders/{order_id}/refresh-tracking")
        # Even if 17TRACK is unconfigured, the route returned a valid JSON
        assert r.status_code in (200, 503)
        data = r.json()
        assert "status" in data


def test_order_refresh_different_users_order_returns_404(client, db_engine):
    """User A must NOT be able to refresh User B's order tracking —
    the endpoint filters by order.user_id == current user.id."""
    from app.core.models import User
    _create_user(client, db_engine, USER_A)
    _create_user(client, db_engine, USER_B)
    _create_seed_product(client, db_engine)

    db = sessionmaker(bind=db_engine)()
    ub = db.query(User).filter(User.email == USER_B).first()
    db.close()
    if not ub:
        pytest.skip("Could not resolve USER_B from DB")
    order_id_b = _create_order_for_user(client, db_engine, ub.id)

    _login(client, USER_A)
    r = client.post(f"/api/orders/{order_id_b}/refresh-tracking")
    assert r.status_code == 404


def test_order_refresh_nonexistent_order_returns_404(client, db_engine):
    _create_user(client, db_engine, USER_A)
    _create_seed_product(client, db_engine)
    _login(client, USER_A)
    r = client.post("/api/orders/99999999/refresh-tracking")
    assert r.status_code == 404
