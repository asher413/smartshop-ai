"""
Regression guard for the "everything is configurable from the admin
panel" requirement.

If someone adds a new key to settings_service.EDITABLE (the whitelist of
env vars the admin panel can save) but forgets to add the matching
<input name="..."> to the settings template, the key silently becomes
unfillable from the UI — exactly the bug the user asked to prevent.

This test parses the template's rendered HTML (it renders fine with
defaults) and asserts every EDITABLE env name has a form field, and every
field in the form maps back to an EDITABLE key (no typos / dead inputs).
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
from app.core.models import Base
from app.core.security_middleware import limiter
from app.services import settings_service

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
ADMIN_PW = "test-admin-pw-123"


@pytest.fixture()
def client(db_engine, monkeypatch):
    def override_get_db():
        db = db_engine()
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
        # Admin session so /admin/settings renders.
        c.post("/admin/login", data={"email": "admin@test.local", "password": ADMIN_PW})
        yield c

    app.dependency_overrides.clear()
    limiter.reset()


@pytest.fixture()
def db_engine():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    return lambda: Session()


def test_settings_page_renders_for_admin(client):
    r = client.get("/admin/settings")
    assert r.status_code == 200


def test_every_editable_key_has_a_form_field(client):
    """The admin panel must be able to fill EVERY editable setting —
    otherwise the key is dead config that can only be set by hand-editing
    .env, which the user explicitly asked to avoid."""
    html = client.get("/admin/settings").text
    # name="GOOGLE_API_KEY" (and name="GOOGLE_API_KEY__clear" checkboxes)
    field_names = set(re.findall(r'name="([A-Z][A-Z0-9_]+)"', html))

    missing = [
        env for env in settings_service.EDITABLE.values()
        if env not in field_names
    ]
    assert missing == [], f"מפתחות חסרים בדף ההגדרות (לא ניתנים למילוי מהפאנל): {missing}"


def test_every_form_field_maps_to_editable_key(client):
    """No stray/typo'd inputs in the template: every fillable field must be
    a known editable setting (clear-checkboxes excluded)."""
    html = client.get("/admin/settings").text
    field_names = set(re.findall(r'name="([A-Z][A-Z0-9_]+)"', html))
    known = set(settings_service.EDITABLE.values())

    # __clear checkboxes are part of the save protocol, not settings.
    real_fields = {f for f in field_names if not f.endswith("__clear")}
    unknown = real_fields - known
    assert unknown == set(), f"שדות לא מוכרים בתבנית ההגדרות: {unknown}"


def test_secrets_are_masked_in_settings_page(client, monkeypatch):
    """The rendered settings page must not leak raw secret values — masked
    placeholders only (the real values stay in the DB/env). This actually
    sets a secret first, so the assertion is meaningful rather than a
    tautology."""
    raw_secret = "SOME-LONG-REAL-SECRET-12345-abcdef"
    monkeypatch.setattr(settings, "google_api_key", raw_secret)
    html = client.get("/admin/settings").text
    # The raw value must never appear in the page source.
    assert raw_secret not in html
    # The field is still present (so the key is fillable/manageable), just
    # not with its real value.
    assert 'name="GOOGLE_API_KEY"' in html


def test_env_example_covers_every_editable_key_and_settings_field():
    """.env.example must document EVERY env var the app reads — otherwise a
    new key added to settings_service/config silently becomes undiscoverable
    for anyone deploying from the template."""
    import re
    from app.core.config import Settings as AppSettings

    example = open(".env.example", encoding="utf-8").read()
    documented = set(re.findall(r"^([A-Z][A-Z0-9_]+)=", example, re.M))

    expected = set(settings_service.EDITABLE.values())
    expected |= {f.upper() for f in AppSettings.model_fields}

    missing = sorted(expected - documented)
    assert missing == [], f"משתנים חסרים ב-.env.example: {missing}"
