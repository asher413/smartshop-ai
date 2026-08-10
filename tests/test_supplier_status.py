"""
Tests for the live supplier-status page (app/services/supplier_status_service.py
and the /admin/suppliers* routes in main.py).

Covers: per-supplier status rows (mode, counts, last pull, pending), the
relative-time helper, and the full pull-test flow with a stubbed adapter.
No real network calls.
"""
import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.models import Base, Product, TrendingCandidate
from app.services import supplier_status_service


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


_seq = [0]

def _product(db, source="ebay", name="Gadget", price=25.0, days_ago=1):
    _seq[0] += 1
    n = _seq[0]
    p = Product(
        sku=f"test-{source}-{name}-{n}",
        source_adapter=source,
        external_id=f"ext-{name}-{n}",
        name=name,
        original_name=name,
        price=price,
        is_active=True,
        is_verified=True,
        slug=f"slug-{source}-{name}-{n}".replace(" ", "-"),
        last_updated=datetime.datetime.utcnow() - datetime.timedelta(days=days_ago),
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _candidate(db, source="ebay", status="pending", days_ago=2):
    _seq[0] += 1
    c = TrendingCandidate(
        source_adapter=source,
        external_id=f"cand-{source}-{_seq[0]}",
        raw_name="Candidate",
        raw_price=10.0,
        status=status,
        discovered_at=datetime.datetime.utcnow() - datetime.timedelta(days=days_ago),
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


# --- get_supplier_status ---

def test_status_includes_every_registered_adapter(db):
    rows = supplier_status_service.get_supplier_status(db)
    from app.services.aggregator_service import ADAPTERS
    names = {r["name"] for r in rows}
    assert names == set(ADAPTERS.keys())


def test_status_counts_products_per_source(db):
    _product(db, source="ebay")
    _product(db, source="ebay")
    _product(db, source="aliexpress")
    _product(db, source="aliexpress")
    _product(db, source="aliexpress")

    by_name = {r["name"]: r for r in supplier_status_service.get_supplier_status(db)}
    assert by_name["ebay"]["product_count"] == 2
    assert by_name["aliexpress"]["product_count"] == 3
    assert by_name["amazon"]["product_count"] == 0


def test_status_pending_candidates(db):
    _candidate(db, source="ebay", status="pending")
    _candidate(db, source="ebay", status="pending")
    _candidate(db, source="ebay", status="promoted")  # not pending

    by_name = {r["name"]: r for r in supplier_status_service.get_supplier_status(db)}
    assert by_name["ebay"]["pending_count"] == 2


def test_status_last_pull_prefers_most_recent_signal(db):
    old_product = _product(db, source="ebay", days_ago=10)
    fresh_candidate = _candidate(db, source="ebay", days_ago=2)

    by_name = {r["name"]: r for r in supplier_status_service.get_supplier_status(db)}
    # The most recent signal wins — candidate discovered 2 days ago beats
    # the product update from 10 days ago.
    assert by_name["ebay"]["last_pull_ago"] == "לפני 2 ימים"


def test_status_last_pull_with_no_signals_is_never(db):
    by_name = {r["name"]: r for r in supplier_status_service.get_supplier_status(db)}
    assert by_name["amazon"]["last_pull"] is None
    assert by_name["amazon"]["last_pull_ago"] == "אף פעם"


def _fake_adapter(source, items):
    class FakeAdapter:
        name = source
        uses_official_api = False

        def __init__(self):
            self.calls = 0

        def fetch_trending(self, category=None, limit=15):
            return items

        def build_affiliate_link(self, url):
            return f"{url}?tracking=1"

    return FakeAdapter


# --- per-supplier pull (discover one source only) ---

def test_discover_trending_sources_filter_only_pulls_selected(db, monkeypatch):
    from app.adapters.base_adapter import RawProduct
    from app.services import aggregator_service

    ebay_items = [RawProduct(
        source_adapter="ebay", external_id="1", name="eBay Only",
        price=25.0, currency="USD", url="https://www.ebay.com/itm/1",
        image_url="", in_stock=True,
    )]
    aliexpress_items = [RawProduct(
        source_adapter="aliexpress", external_id="1", name="Ali Only",
        price=10.0, currency="USD", url="https://www.aliexpress.com/item/1",
        image_url="", in_stock=True,
    )]
    monkeypatch.setattr(aggregator_service, "ADAPTERS", {
        "ebay": _fake_adapter("ebay", ebay_items),
        "aliexpress": _fake_adapter("aliexpress", aliexpress_items),
    })

    summary = aggregator_service.discover_trending(db, sources=["ebay"])
    # Only ebay ran: one candidate staged, aliexpress untouched.
    assert summary["discovered"] == 1
    assert set(summary["by_source"].keys()) == {"ebay"}
    assert db.query(TrendingCandidate).filter_by(source_adapter="ebay").count() == 1
    assert db.query(TrendingCandidate).filter_by(source_adapter="aliexpress").count() == 0


def test_pull_supplier_products_stages_and_promotes(db, monkeypatch):
    from app.adapters.base_adapter import RawProduct
    from app.services import aggregator_service, supplier_status_service

    items = [RawProduct(
        source_adapter="ebay", external_id="hot-1", name="Hot eBay Item",
        price=25.0, currency="USD", url="https://www.ebay.com/itm/999",
        image_url="https://example.com/i.jpg", in_stock=True,
        demand_score=99.0, rating=4.9, review_count=500,
    )]
    monkeypatch.setattr(aggregator_service, "ADAPTERS", {"ebay": _fake_adapter("ebay", items)})
    monkeypatch.setattr(supplier_status_service, "ADAPTERS", {"ebay": _fake_adapter("ebay", items)})

    result = supplier_status_service.pull_supplier_products("ebay", db=db)
    assert result["status"] == "ok"
    assert "מועמדים" in result["message"]
    # High score auto-promoted it straight to a live Product.
    assert db.query(TrendingCandidate).filter_by(source_adapter="ebay").count() == 1
    assert db.query(Product).filter_by(source_adapter="ebay").count() == 1


def test_pull_supplier_products_unknown_supplier():
    result = supplier_status_service.pull_supplier_products("nope")
    assert result["status"] == "error"


def test_status_mode_api_vs_scraping(db, monkeypatch):
    from app.core import config
    # ebay configured -> api mode; aliexpress not -> scraping.
    monkeypatch.setattr(config.settings, "ebay_app_id", "appid", raising=False)
    monkeypatch.setattr(config.settings, "ebay_cert_id", "cert", raising=False)
    monkeypatch.setattr(config.settings, "aliexpress_app_key", "", raising=False)
    monkeypatch.setattr(config.settings, "aliexpress_app_secret", "", raising=False)

    by_name = {r["name"]: r for r in supplier_status_service.get_supplier_status(db)}
    assert by_name["ebay"]["mode"] == "api"
    assert by_name["ebay"]["mode_label"] == "API רשמי"
    assert by_name["aliexpress"]["mode"] == "scraping"


# --- _time_ago ---

def test_time_ago_relative():
    now = datetime.datetime.utcnow()
    assert supplier_status_service._time_ago(None) == "אף פעם"
    assert supplier_status_service._time_ago(now - datetime.timedelta(minutes=5)) == "לפני 5 דקות"
    assert supplier_status_service._time_ago(now - datetime.timedelta(hours=3)) == "לפני 3 שעות"
    assert supplier_status_service._time_ago(now - datetime.timedelta(days=1)) == "אתמול"
    assert supplier_status_service._time_ago(now - datetime.timedelta(days=4)) == "לפני 4 ימים"


# --- test_supplier_pull (stubbed adapters, no network) ---

def test_pull_test_unknown_supplier():
    result = supplier_status_service.test_supplier_pull("nope")
    assert result["status"] == "error"


def test_pull_test_missing_keys_reports_env_names(monkeypatch):
    from app.core import config
    monkeypatch.setattr(config.settings, "ebay_app_id", "", raising=False)
    monkeypatch.setattr(config.settings, "ebay_cert_id", "", raising=False)
    result = supplier_status_service.test_supplier_pull("ebay")
    assert result["status"] == "skip"
    assert "EBAY_APP_ID" in result["message"]


def test_pull_test_success_flow(monkeypatch):
    from app.adapters.base_adapter import RawProduct
    from app.services import aggregator_service

    class FakeAdapter:
        name = "ebay"
        uses_official_api = False

        def fetch_trending(self, category=None, limit=1):
            return [RawProduct(
                source_adapter="ebay", external_id="1", name="Real eBay Item",
                price=19.9, currency="USD", url="https://www.ebay.com/itm/1",
                image_url="", in_stock=True,
            )]

        def build_affiliate_link(self, url):
            return f"{url}?mkcid=1&campid=9999"

    monkeypatch.setattr(aggregator_service, "ADAPTERS", {"ebay": FakeAdapter})
    monkeypatch.setattr(supplier_status_service, "ADAPTERS", {"ebay": FakeAdapter})
    # ebay run_test hits the network — stub it as passing.
    monkeypatch.setattr("app.services.settings_service.run_test", lambda service, overrides: (True, "ok"))
    monkeypatch.setattr("app.services.supplier_verification.missing_keys", lambda supplier: [])

    result = supplier_status_service.test_supplier_pull("ebay")
    assert result["status"] == "ok"
    assert "Real eBay Item" in result["message"]


def test_pull_test_rejects_link_without_tracking(monkeypatch):
    from app.adapters.base_adapter import RawProduct
    from app.services import aggregator_service

    class FakeAdapter:
        name = "amazon"
        uses_official_api = False

        def fetch_trending(self, category=None, limit=1):
            return [RawProduct(
                source_adapter="amazon", external_id="1", name="No Tracking",
                price=10.0, currency="USD", url="https://www.amazon.com/dp/B0X",
                image_url="", in_stock=True,
            )]

        def build_affiliate_link(self, url):
            return url  # broken: no tag= param

    monkeypatch.setattr(aggregator_service, "ADAPTERS", {"amazon": FakeAdapter})
    monkeypatch.setattr(supplier_status_service, "ADAPTERS", {"amazon": FakeAdapter})
    monkeypatch.setattr("app.services.settings_service.run_test", lambda service, overrides: (True, "ok"))
    monkeypatch.setattr("app.services.supplier_verification.missing_keys", lambda supplier: [])

    result = supplier_status_service.test_supplier_pull("amazon")
    assert result["status"] == "fail"
    assert "עמלה" in result["message"]


# --- routes ---

def test_suppliers_routes_registered():
    # Route protection is already covered by require_admin; here we just
    # assert the page routes exist on the app without import errors.
    from app.api.main import app
    routes = {getattr(r, "path", "") for r in app.routes}
    assert "/admin/suppliers" in routes
    assert "/admin/suppliers/api" in routes
    assert "/admin/suppliers/pull-test/{supplier}" in routes
    assert "/admin/suppliers/pull/{supplier}" in routes
