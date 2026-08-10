"""
Tests for the server-side price-histogram + stats helpers
(_build_price_histogram / _build_price_stats) — pure functions,
no DB or network, so they run in milliseconds.

Coverage:
  * Correct bucket count and labels
  * Values above cap fall into the last bucket
  * Empty list returns [] / None (no crash)
  * Single product — avg == median
  * Percentile pct is normalised to the tallest bar
  * price_hist + price_stats appear in the /category and /search
    template contexts (live route test).
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.main import (
    _build_price_histogram,
    _build_price_stats,
    app,
)
from app.core.database import get_db
from app.core.models import Base, Product
from app.core.security_middleware import limiter


# ---------------------------------------------------------------------------
# Fake product — only the .price attr matters
# ---------------------------------------------------------------------------
class FakeProduct:
    def __init__(self, price):
        self.price = price


# ===========================================================================
# _build_price_histogram
# ===========================================================================

class TestBuildPriceHistogram:
    def test_empty_returns_empty_list(self):
        assert _build_price_histogram([]) == []

    def test_returns_correct_number_of_buckets(self):
        # 10 products spread across [0, 20000]
        prods = [FakeProduct(i * 100) for i in range(10)]
        hist = _build_price_histogram(prods, max_price_cap=1000, buckets=4)
        assert len(hist) == 4

    def test_buckets_are_contiguous(self):
        prods = [FakeProduct(50), FakeProduct(250), FakeProduct(750)]
        hist = _build_price_histogram(prods, max_price_cap=1000, buckets=4)
        for i in range(len(hist) - 1):
            assert hist[i]["hi"] == hist[i + 1]["lo"], f"gap at bucket {i}"

    def test_values_above_cap_fall_into_last_bucket(self):
        prods = [FakeProduct(100), FakeProduct(25000), FakeProduct(50000)]
        hist = _build_price_histogram(prods, max_price_cap=20000, buckets=4)
        # Buckets: 0-5000, 5000-10000, 10000-15000, 15000-20000
        # Product at 100    -> bucket 0
        # Product at 25000  -> bucket 3 (clamped)
        # Product at 50000  -> bucket 3 (clamped)
        assert hist[0]["count"] == 1  # 100
        assert hist[1]["count"] == 0
        assert hist[2]["count"] == 0
        assert hist[3]["count"] == 2  # both 25000 and 50000

    def test_last_bucket_hi_is_cap(self):
        prods = [FakeProduct(100)]
        hist = _build_price_histogram(prods, max_price_cap=20000, buckets=8)
        assert hist[-1]["hi"] == 20000

    def test_pct_is_normalised_to_tallest_bucket(self):
        # 10 products in bucket 0, 5 in bucket 1 — bucket 0 = 100%
        prods = [FakeProduct(1000)] * 10 + [FakeProduct(6000)] * 5
        hist = _build_price_histogram(prods, max_price_cap=20000, buckets=4)
        assert hist[0]["pct"] == 100.0
        assert hist[1]["pct"] == 50.0

    def test_count_matches_input(self):
        prods = [FakeProduct(3000), FakeProduct(7000), FakeProduct(3000)]
        hist = _build_price_histogram(prods, max_price_cap=20000, buckets=8)
        total = sum(b["count"] for b in hist)
        assert total == 3

    def test_label_includes_shekel_sign(self):
        prods = [FakeProduct(100)]
        hist = _build_price_histogram(prods)
        for b in hist:
            assert "₪" in b["label"]


# ===========================================================================
# _build_price_stats
# ===========================================================================

class TestBuildPriceStats:
    def test_empty_returns_none(self):
        assert _build_price_stats([]) is None

    def test_single_product_avg_equals_median(self):
        stats = _build_price_stats([FakeProduct(285)])
        assert stats["avg"] == 285
        assert stats["median"] == 285
        assert stats["most_up_to"] == 285
        assert stats["count"] == 1

    def test_even_count_median_is_average_of_middle_two(self):
        prods = [FakeProduct(p) for p in [10, 20, 30, 40]]
        stats = _build_price_stats(prods)
        assert stats["median"] == 25  # (20 + 30) / 2

    def test_odd_count_median_is_middle_element(self):
        prods = [FakeProduct(p) for p in [10, 20, 30]]
        stats = _build_price_stats(prods)
        assert stats["median"] == 20

    def test_most_up_to_is_75th_percentile(self):
        # 8 products at 100, 2 at 1000 → p75 ≈ 100
        prods = [FakeProduct(100)] * 8 + [FakeProduct(1000)] * 2
        stats = _build_price_stats(prods)
        # p75 index = max(0, 10*0.75 - 1) = max(0, 6) = 6 → prices[6] = 100
        assert stats["most_up_to"] == 100

    def test_stats_keys(self):
        stats = _build_price_stats([FakeProduct(50), FakeProduct(150)])
        assert set(stats.keys()) == {"avg", "median", "most_up_to", "count"}


# ===========================================================================
# Live route tests — price_hist + price_stats in template context
# ===========================================================================

@pytest.fixture()
def db_engine_live():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    # Seed a few products so the histogram has data
    db.add(Product(
        name="Cheap Gadget", price=50, category="אלקטרוניקה",
        sku="c1", slug="c1", external_id="e1", is_active=True, is_verified=True,
    ))
    db.add(Product(
        name="Mid Gadget", price=500, category="אלקטרוניקה",
        sku="c2", slug="c2", external_id="e2", is_active=True, is_verified=True,
    ))
    db.add(Product(
        name="Expensive Gadget", price=5000, category="אלקטרוניקה",
        sku="c3", slug="c3", external_id="e3", is_active=True, is_verified=True,
    ))
    db.commit()
    db.close()
    return engine


@pytest.fixture()
def client_live(db_engine_live):
    Session = sessionmaker(bind=db_engine_live)

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    limiter.reset()
    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
        yield c
    app.dependency_overrides.clear()
    limiter.reset()


def test_search_route_includes_price_hist(client_live):
    r = client_live.get("/search?q=gadget")
    assert r.status_code == 200
    body = r.text
    # Replaced histogram with number inputs
    assert 'name="min_price"' in body
    assert 'name="max_price"' in body


def test_search_route_includes_price_stats(client_live):
    r = client_live.get("/search?q=gadget")
    assert r.status_code == 200
    body = r.text
    assert "ממוצע" in body
    assert "חציון" in body


def test_category_route_includes_price_hist(client_live):
    r = client_live.get("/category/אלקטרוניקה")
    assert r.status_code == 200
    body = r.text
    # Price filter inputs (replaced histogram + slider with simple number inputs)
    assert 'name="min_price"' in body
    assert 'name="max_price"' in body


def test_category_route_includes_price_stats(client_live):
    r = client_live.get("/category/אלקטרוניקה")
    assert r.status_code == 200
    body = r.text
    assert "ממוצע" in body
    assert "חציון" in body
