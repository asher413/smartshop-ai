"""
Health check & bot endpoints — /healthz, /robots.txt, /sitemap.xml,
/feed, /feed/google-shopping.xml. These are the "plumbing" endpoints that
monitoring tools, search-engine crawlers, and deployment probes hit, and
they must NEVER break silently when a route or middleware changes.

All tests are offline (TestClient + in-memory SQLite); the feed page
renders against an empty catalog (no products yet → still 200).
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.main import app
from app.core.config import settings
from app.core.database import get_db
from app.core.models import Base
from app.core.security_middleware import limiter

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


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
    monkeypatch.setattr(settings, "site_url", "https://smartshop.example.com")
    monkeypatch.setattr(settings, "admin_secret_key", "test-admin-pw-123")
    limiter.reset()

    with TestClient(
        app, base_url="http://127.0.0.1:8000", follow_redirects=False
    ) as c:
        c.headers.update({"User-Agent": BROWSER_UA})
        yield c

    app.dependency_overrides.clear()
    limiter.reset()


# ---------------------------------------------------------------------------
# /healthz
# ---------------------------------------------------------------------------

def test_healthz_returns_200_json(client):
    """Deployment probes must get a stable 200 with the expected JSON shape."""
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.headers.get("content-type", "").startswith("application/json")
    data = r.json()
    assert data["status"] == "ok"


def test_healthz_never_returns_non_200(client):
    """The health check must not redirect or error — probes treat
    non-200 as unhealthy and restart the container."""
    r = client.get("/healthz")
    assert 200 <= r.status_code < 300


def test_healthz_is_not_blocked_by_bot_guard(client):
    """Health probes from platforms like Render/Railway come without a
    User-Agent, Referer, or cookies — the health EP must respond without
    any auth or bot guard getting in the way."""
    r = client.get("/healthz", headers={"User-Agent": ""})
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# /robots.txt
# ---------------------------------------------------------------------------

def test_robots_txt_returns_200_plain_text(client):
    r = client.get("/robots.txt")
    assert r.status_code == 200
    ct = r.headers.get("content-type", "")
    assert "text/plain" in ct or "charset=utf-8" in ct


def test_robots_txt_references_sitemap(client):
    r = client.get("/robots.txt")
    body = r.text
    assert "Allow: /" in body
    assert "Sitemap:" in body
    assert settings.site_url in body


def test_robots_txt_unauthenticated_accessible(client):
    """Crawlers don't carry sessions — robots.txt must be public."""
    r = client.get("/robots.txt")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# /sitemap.xml
# ---------------------------------------------------------------------------

def test_sitemap_returns_200_xml(client):
    r = client.get("/sitemap.xml")
    assert r.status_code == 200
    ct = r.headers.get("content-type", "")
    assert "application/xml" in ct or "text/xml" in ct


def test_sitemap_is_valid_xml(client):
    r = client.get("/sitemap.xml")
    body = r.text
    assert body.strip().startswith("<?xml")
    assert "<urlset" in body
    # Even with zero products, the static pages (/ , /coupons, /about)
    # appear as <url> entries.
    assert "<url>" in body
    assert "/coupons" in body or "/about" in body


def test_sitemap_contains_base_urls(client):
    """The sitemap must always include the static marketing pages
    regardless of how many products are in the catalog."""
    r = client.get("/sitemap.xml")
    assert settings.site_url in r.text


def test_sitemap_unauthenticated_accessible(client):
    r = client.get("/sitemap.xml")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# /feed
# ---------------------------------------------------------------------------

def test_feed_returns_200_html(client):
    r = client.get("/feed")
    assert r.status_code == 200
    ct = r.headers.get("content-type", "")
    assert "text/html" in ct


def test_feed_shows_empty_state_not_500(client):
    """The feed page renders even when the catalog is empty — a missing
    product table row must not cause a 500."""
    r = client.get("/feed")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# /feed/google-shopping.xml
# ---------------------------------------------------------------------------

def test_google_shopping_returns_200_xml(client):
    r = client.get("/feed/google-shopping.xml")
    assert r.status_code == 200
    ct = r.headers.get("content-type", "")
    assert "application/xml" in ct or "text/xml" in ct


def test_google_shopping_empty_catalog_still_valid_xml(client):
    r = client.get("/feed/google-shopping.xml")
    body = r.text
    assert body.strip().startswith("<?xml")
    # The root element is valid even with zero items; Google Shopping
    # XML uses either <feed> (RSS) or <rss> depending on the generator.
    assert ("</feed>" in body or "</rss>" in body)
