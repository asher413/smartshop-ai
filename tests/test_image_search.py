"""
Tests for the visual search service (app/services/image_search_service.py).
The catalog-image hash fetcher is stubbed so no network calls happen.
"""
import datetime
import io

import pytest
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.models import Base, Product
from app.services import image_search_service


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _product(db, name="Gadget", price=10.0, image_url="https://example.com/i.jpg"):
    p = Product(
        sku=f"img-{name}",
        source_adapter="ebay",
        external_id=f"img-ext-{name}",
        name=name,
        original_name=name,
        price=price,
        image_url=image_url,
        is_active=True,
        is_verified=True,
        slug=f"img-slug-{name}".replace(" ", "-"),
        last_updated=datetime.datetime.utcnow(),
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _png_bytes() -> bytes:
    img = Image.new("RGB", (32, 32), (120, 60, 60))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_image_search_returns_matching_products(db, monkeypatch):
    from app.services.image_search_service import _dhash
    _product(db, name="Red Gadget")
    _product(db, name="Blue Gadget", image_url="https://example.com/blue.jpg")

    target = _dhash(Image.open(io.BytesIO(_png_bytes())))
    monkeypatch.setattr(image_search_service, "_fetch_hash", lambda url: target)

    results = image_search_service.search_by_image(_png_bytes(), db)
    assert len(results) == 2  # both catalog images "match" the uploaded one
    assert all("id" in r and "name" in r and "price" in r for r in results)


def test_image_search_skips_dissimilar_products(db, monkeypatch):
    _product(db, name="Far Gadget")
    monkeypatch.setattr(image_search_service, "_fetch_hash", lambda url: 0xFFFFFFFFFFFFFFFF)
    results = image_search_service.search_by_image(_png_bytes(), db)
    assert results == []


def test_image_search_ignores_unfetchable_images(db, monkeypatch):
    _product(db, name="Broken Img")
    monkeypatch.setattr(image_search_service, "_fetch_hash", lambda url: None)
    results = image_search_service.search_by_image(_png_bytes(), db)
    assert results == []


def test_image_search_invalid_upload_returns_empty(db):
    assert image_search_service.search_by_image(b"not an image", db) == []


def test_image_search_respects_limit(db, monkeypatch):
    from app.services.image_search_service import _dhash
    for i in range(5):
        _product(db, name=f"Gadget {i}")
    target = _dhash(Image.open(io.BytesIO(_png_bytes())))
    monkeypatch.setattr(image_search_service, "_fetch_hash", lambda url: target)
    results = image_search_service.search_by_image(_png_bytes(), db, limit=3)
    assert len(results) == 3
