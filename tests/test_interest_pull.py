"""
Tests for the interest-driven catalog expansion + automatic cleanup
(app/services/interest_pull_service.py).

The pull itself queries live supplier APIs, so it can't be unit-tested
without network — but its *safety and lifecycle logic* can and must be:

  * cleanup_stale_pulls() deactivates interest-pulled products nobody
    engaged with after the grace period,
  * engagement (clicks / views / favorites) protects a product from
    cleanup,
  * the origin product that sparked a pull is never touched,
  * pull_related_products() degrades to a no-op on throttling and on
    adapters that have no official API (no crash, no fabricated data).
"""
import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.models import (
    Base, Product, InterestPull, AffiliateClick, ProductView, ProductFavorite,
)
from app.services.interest_pull_service import (
    cleanup_stale_pulls, pull_related_products, PULL_STALE_DAYS,
)


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


def _product(db, name="Wireless Charger 15W", price=20.0, active=True, pid=None):
    p = Product(
        sku=f"test-{name}-{pid or name}",
        source_adapter="aliexpress",
        external_id=f"ext-{pid or name}",
        name=name,
        original_name=name,
        price=price,
        is_active=active,
        is_verified=True,
        slug=f"test-slug-{pid or name}".replace(" ", "-"),
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _interest_row(db, product, origin, days_ago, session_id="guest"):
    row = InterestPull(
        product_id=product.id,
        origin_product_id=origin.id,
        session_id=session_id,
        pulled_at=datetime.datetime.utcnow() - datetime.timedelta(days=days_ago),
    )
    db.add(row)
    db.commit()
    return row


# --- cleanup_stale_pulls ---

def test_cleanup_deactivates_unengaged_stale_pull(db):
    origin = _product(db, name="Origin", pid=1)
    pulled = _product(db, name="Related Charger", pid=2)
    _interest_row(db, pulled, origin, days_ago=PULL_STALE_DAYS + 1)

    result = cleanup_stale_pulls(db, max_age_days=PULL_STALE_DAYS)

    assert result["deactivated"] == 1
    db.refresh(pulled)
    assert pulled.is_active is False
    # Origin product is never touched.
    db.refresh(origin)
    assert origin.is_active is True
    # The tracking row is cleaned up too.
    assert db.query(InterestPull).count() == 0


def test_cleanup_keeps_recent_pull_within_grace_period(db):
    origin = _product(db, name="Origin", pid=1)
    pulled = _product(db, name="Fresh Related", pid=2)
    _interest_row(db, pulled, origin, days_ago=1)

    result = cleanup_stale_pulls(db, max_age_days=PULL_STALE_DAYS)

    assert result["deactivated"] == 0
    db.refresh(pulled)
    assert pulled.is_active is True


def test_cleanup_keeps_pulled_product_with_clicks(db):
    origin = _product(db, name="Origin", pid=1)
    pulled = _product(db, name="Clicked Related", pid=2)
    _interest_row(db, pulled, origin, days_ago=PULL_STALE_DAYS + 1)
    db.add(AffiliateClick(product_id=pulled.id, source="aliexpress", session_id="s1"))
    db.commit()

    result = cleanup_stale_pulls(db, max_age_days=PULL_STALE_DAYS)

    assert result["deactivated"] == 0
    db.refresh(pulled)
    assert pulled.is_active is True


def test_cleanup_keeps_pulled_product_with_views(db):
    origin = _product(db, name="Origin", pid=1)
    pulled = _product(db, name="Viewed Related", pid=2)
    _interest_row(db, pulled, origin, days_ago=PULL_STALE_DAYS + 1)
    db.add(ProductView(session_id="s1", product_id=pulled.id))
    db.commit()

    result = cleanup_stale_pulls(db, max_age_days=PULL_STALE_DAYS)

    assert result["deactivated"] == 0
    db.refresh(pulled)
    assert pulled.is_active is True


def test_cleanup_keeps_pulled_product_with_favorites(db):
    origin = _product(db, name="Origin", pid=1)
    pulled = _product(db, name="Favorited Related", pid=2)
    _interest_row(db, pulled, origin, days_ago=PULL_STALE_DAYS + 1)
    db.add(ProductFavorite(user_id=1, product_id=pulled.id))
    db.commit()

    result = cleanup_stale_pulls(db, max_age_days=PULL_STALE_DAYS)

    assert result["deactivated"] == 0
    db.refresh(pulled)
    assert pulled.is_active is True


def test_cleanup_ignores_rows_for_already_deleted_products(db):
    origin = _product(db, name="Origin", pid=1)
    pulled = _product(db, name="Ghost", pid=2)
    _interest_row(db, pulled, origin, days_ago=PULL_STALE_DAYS + 1)
    db.delete(pulled)
    db.commit()

    result = cleanup_stale_pulls(db, max_age_days=PULL_STALE_DAYS)

    # No crash; the orphan row is removed; nothing left behind.
    assert db.query(InterestPull).count() == 0
    assert result["deactivated"] == 0


# --- pull_related_products safety ---

def test_pull_throttles_recent_origin(db):
    origin = _product(db, name="Origin Product", pid=10)
    _interest_row(db, origin, origin, days_ago=0)  # just pulled

    result = pull_related_products(db, origin, session_id="guest")

    # Throttled: no new work, no crash, honest reason.
    assert result["skipped"] == "throttled"
    assert result["pulled"] == 0


def test_pull_noops_for_none(db):
    result = pull_related_products(db, None)
    assert result["skipped"] == "no_product"


def test_cleanup_grace_period_is_three_days(db):
    # The default must match the product requirement ("~3 days").
    assert PULL_STALE_DAYS == 3
