"""
Tests for the coupon pipeline (app/services/coupon_service.py):

  * pull_coupons_from_sources() — upserts codes from every adapter,
    updates existing rows instead of duplicating, never crashes on a
    source without coupons
  * active_coupons() — expired coupons are excluded, null expiry = active
  * coupons_for_display() — the unified view used by the coupons page and
    personal area merges standalone Coupon rows + live products carrying
    a coupon_code
  * the discount / valid-until parsers handle real-world feed formats

The pull hits supplier adapters, so tests replace the adapter registry
with fake adapters returning fixed coupon payloads — the interesting logic
(upsert/merge/display) is pure DB work and fully testable offline.
"""
import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.models import Base, Coupon, Product
from app.services import coupon_service


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


# --- fake adapters -------------------------------------------------------

class _FakeAdapter:
    """Simulates one supplier's fetch_coupons() return payloads. The pull
    instantiates adapters via ADAPTERS[source]() so the fake must be a
    CLASS; the payload is passed through a class attribute."""
    name = "fake_source"
    _payload = []

    def __init__(self):
        pass

    def fetch_coupons(self, limit: int = 20):
        return self._payload[:limit]


class _BrokenAdapter:
    """A supplier whose coupon endpoint is down — must not crash the pull."""
    name = "broken_source"

    def __init__(self):
        pass

    def fetch_coupons(self, limit: int = 20):
        raise RuntimeError("network down")


# --- parse helpers -------------------------------------------------------

def test_parse_discount_extracts_number():
    assert coupon_service._parse_discount("10% off") == 10.0
    assert coupon_service._parse_discount("15.5") == 15.5
    assert coupon_service._parse_discount(None) is None
    assert coupon_service._parse_discount("none available") is None


def test_parse_valid_until_handles_iso_and_z():
    parsed = coupon_service._parse_valid_until("2027-01-15T23:59:59Z")
    assert parsed is not None
    assert parsed.year == 2027 and parsed.month == 1
    # naive datetime out — stored consistently with other DB timestamps
    assert parsed.tzinfo is None
    assert coupon_service._parse_valid_until("") is None
    assert coupon_service._parse_valid_until("not-a-date") is None


# --- upsert --------------------------------------------------------------

def test_pull_inserts_new_coupons(db, monkeypatch):
    # Payload set via monkeypatch so it's auto-restored after the test — a
    # class-attribute assignment would leak into the next test.
    monkeypatch.setattr(_FakeAdapter, "_payload", [{"code": "SUMMER10", "discount": "10%", "valid_until": None}])
    monkeypatch.setattr(coupon_service, "ADAPTERS", {"fake": _FakeAdapter})

    report = coupon_service.pull_coupons_from_sources(db)

    assert report["found"] == 1
    assert report["by_source"]["fake"] == 1
    row = db.query(Coupon).filter(Coupon.code == "SUMMER10").first()
    assert row is not None
    assert row.discount_percent == 10.0


def test_pull_upserts_instead_of_duplicating(db, monkeypatch):
    db.add(Coupon(code="SUMMER10", discount_percent=5.0))
    db.commit()
    monkeypatch.setattr(_FakeAdapter, "_payload", [{"code": "summer10", "discount": "20%", "valid_until": None}])
    monkeypatch.setattr(coupon_service, "ADAPTERS", {"fake": _FakeAdapter})

    report = coupon_service.pull_coupons_from_sources(db)

    # Same code (case-insensitive upsert): updated, not duplicated.
    assert report["found"] == 0
    rows = db.query(Coupon).filter(Coupon.code == "SUMMER10").all()
    assert len(rows) == 1
    assert rows[0].discount_percent == 20.0


def test_pull_skips_empty_codes(db, monkeypatch):
    monkeypatch.setattr(_FakeAdapter, "_payload", [
        {"code": "", "discount": None, "valid_until": None},
        {"code": "   ", "discount": None, "valid_until": None},
        {"code": "REAL", "discount": None, "valid_until": None},
    ])
    monkeypatch.setattr(coupon_service, "ADAPTERS", {"fake": _FakeAdapter})

    report = coupon_service.pull_coupons_from_sources(db)

    assert report["found"] == 1
    assert db.query(Coupon).count() == 1


def test_pull_survives_broken_source(db, monkeypatch):
    monkeypatch.setattr(_FakeAdapter, "_payload", [{"code": "OK1", "discount": None, "valid_until": None}])
    monkeypatch.setattr(coupon_service, "ADAPTERS", {"good": _FakeAdapter, "broken": _BrokenAdapter})

    report = coupon_service.pull_coupons_from_sources(db)

    assert report["found"] == 1
    assert report["by_source"]["broken"] == 0
    assert db.query(Coupon).count() == 1


# --- expiry --------------------------------------------------------------

def test_active_coupons_excludes_expired(db):
    past = datetime.datetime.utcnow() - datetime.timedelta(days=5)
    future = datetime.datetime.utcnow() + datetime.timedelta(days=5)
    db.add_all([
        Coupon(code="EXPIRED", discount_percent=10.0, valid_until=past),
        Coupon(code="FUTURE", discount_percent=10.0, valid_until=future),
        Coupon(code="NO_EXPIRY", discount_percent=10.0, valid_until=None),
    ])
    db.commit()

    active = coupon_service.active_coupons(db)
    codes = {c.code for c in active}

    assert "EXPIRED" not in codes
    assert "FUTURE" in codes
    assert "NO_EXPIRY" in codes


# --- unified display -----------------------------------------------------

def _seed_product(db, name="Wireless Charger", coupon_code="SAVE5", price=20.0):
    p = Product(
        sku=f"prod-{name}-{coupon_code}",
        source_adapter="aliexpress",
        external_id=f"ext-{coupon_code}",
        name=name,
        original_name=name,
        price=price,
        image_url="https://example.com/p.jpg",
        supplier_name="AliExpress",
        coupon_code=coupon_code,
        is_active=True,
        is_verified=True,
        slug=f"slug-{name}-{coupon_code}".replace(" ", "-"),
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def test_coupons_for_display_merges_table_and_products(db):
    db.add(Coupon(code="TABLE10", discount_percent=10.0, valid_until=None))
    db.commit()
    prod = _seed_product(db)

    items = coupon_service.coupons_for_display(db, limit=50)
    codes = [it["code"] for it in items]

    assert "TABLE10" in codes
    assert "SAVE5" in codes
    table_item = next(it for it in items if it["code"] == "TABLE10")
    prod_item = next(it for it in items if it["code"] == "SAVE5")
    assert table_item["source"] == "ספקים"
    assert prod_item["source"] == "AliExpress"
    assert prod_item["url"] == f"/product/{prod.id}"
    assert prod_item["image_url"]


def test_coupons_for_display_excludes_expired_table_coupons(db):
    past = datetime.datetime.utcnow() - datetime.timedelta(days=5)
    db.add(Coupon(code="OLD", discount_percent=10.0, valid_until=past))
    db.commit()
    _seed_product(db, coupon_code="FRESH")

    items = coupon_service.coupons_for_display(db, limit=50)
    codes = [it["code"] for it in items]

    assert "OLD" not in codes
    assert "FRESH" in codes


def test_coupons_for_display_respects_limit(db):
    for i in range(5):
        _seed_product(db, name=f"Product {i}", coupon_code=f"CODE{i}")
    items = coupon_service.coupons_for_display(db, limit=3)
    assert len(items) == 3
