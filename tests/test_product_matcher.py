"""
Tests for the cross-vendor product matcher (app/services/product_matcher.py).

This is the "same product, different store" detector behind both the import
pipeline dedup and the price-war widget — getting it wrong means either
duplicate products splitting clicks or unrelated listings shown as price
comparisons, so the core scoring logic is locked down with pure-function
tests plus one in-memory SQLite check of the DB query.
"""
import pytest

from app.core.models import Base, Product
from app.services.product_matcher import (
    normalize_name,
    name_similarity,
    price_compatible,
    find_existing_product_match,
    merge_offer_into_product,
    MATCH_SIMILARITY_THRESHOLD,
)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


# --- normalize_name ---

def test_normalize_lowercases_and_strips_punctuation():
    assert normalize_name("Wireless Charger 15W, Fast!") == {"wireless", "charger", "15w"}


def test_normalize_drops_marketplace_filler_words():
    tokens = normalize_name("Hot NEW Original Wireless Charger 15W Sale")
    assert "hot" not in tokens
    assert "new" not in tokens
    assert "original" not in tokens
    assert "sale" not in tokens
    assert "wireless" in tokens  # real identity token survives


def test_normalize_empty_name():
    assert normalize_name(None) == set()
    assert normalize_name("") == set()


# --- name_similarity ---

def test_identical_names_score_1():
    assert name_similarity("Wireless Charger 15W", "Wireless Charger 15W") == pytest.approx(1.0)


def test_subset_name_scores_high():
    # One store abbreviates the title; containment keeps this a match.
    assert name_similarity("Wireless Charger 15W", "Wireless Charger 15W Fast Charging") > MATCH_SIMILARITY_THRESHOLD


def test_unrelated_names_score_low():
    assert name_similarity("Wireless Charger 15W", "USB C Cable 1m") < MATCH_SIMILARITY_THRESHOLD


def test_shared_brand_alone_is_not_enough():
    # Same brand word but genuinely different products.
    assert name_similarity("Anker PowerCore 10000", "Anker Wireless Charger Pad") < MATCH_SIMILARITY_THRESHOLD


def test_short_generic_name_never_matches():
    # A bare "Charger" would containment-match every "X Charger" listing —
    # the identity-token guard must block it outright.
    assert name_similarity("Charger", "Wireless Charger 15W") == 0.0
    assert name_similarity("Charger", "Charger") == 0.0


# --- price_compatible ---

def test_equal_prices_compatible():
    assert price_compatible(29.99, 29.99)


def test_moderate_markup_compatible():
    # A 25% price gap across stores is normal (currency/promos/stock).
    assert price_compatible(20.0, 25.0)


def test_huge_price_gap_not_compatible():
    assert not price_compatible(20.0, 60.0)


def test_missing_or_zero_prices_not_compatible():
    assert not price_compatible(None, 20.0)
    assert not price_compatible(0, 20.0)
    assert not price_compatible(20.0, None)


# --- DB-backed: find_existing_product_match / merge_offer_into_product ---

@pytest.fixture()
def db_session():
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=test_engine)
    Session = sessionmaker(bind=test_engine)
    session = Session()
    yield session
    session.close()


def _seed(db, source, name, price, external_id="x1", active=True):
    p = Product(
        source_adapter=source,
        external_id=external_id,
        name=name,
        price=price,
        is_active=active,
        is_verified=True,
        slug=f"{source}-{external_id}",
    )
    db.add(p)
    db.commit()
    return p


def test_finds_existing_product_from_another_source(db_session):
    _seed(db_session, "aliexpress", "Wireless Charger 15W Fast Charging", 20.0)
    match = find_existing_product_match(db_session, "temu", "Wireless Charger 15W", 21.0)
    assert match is not None
    assert match.source_adapter == "aliexpress"


def test_ignores_products_from_same_source(db_session):
    _seed(db_session, "temu", "Wireless Charger 15W Fast Charging", 20.0)
    match = find_existing_product_match(db_session, "temu", "Wireless Charger 15W", 21.0)
    assert match is None


def test_no_match_when_price_too_far_apart(db_session):
    _seed(db_session, "aliexpress", "Wireless Charger 15W Fast Charging", 20.0)
    match = find_existing_product_match(db_session, "temu", "Wireless Charger 15W", 60.0)
    assert match is None


def test_no_match_for_unrelated_product(db_session):
    _seed(db_session, "aliexpress", "USB C Cable 1m", 5.0)
    match = find_existing_product_match(db_session, "temu", "Wireless Charger 15W", 20.0)
    assert match is None


def test_merge_adds_offer_and_affiliate_link(db_session):
    product = _seed(db_session, "aliexpress", "Wireless Charger 15W Fast Charging", 20.0)
    merge_offer_into_product(
        db_session,
        product,
        source_adapter="temu",
        offer_price=18.5,
        offer_url="https://temu.example/item/42",
        affiliate_link="https://temu.example/item/42?aff=site",
    )
    offers = product.offers
    assert len(offers) == 1
    assert offers[0]["source"] == "temu"
    assert offers[0]["price"] == 18.5
    assert offers[0]["approximate_match"] is True
    assert product.affiliate_links["temu"] == "https://temu.example/item/42?aff=site"


def test_merge_replaces_stale_offer_for_same_source(db_session):
    product = _seed(db_session, "aliexpress", "Wireless Charger 15W Fast Charging", 20.0)
    product.offers = [{"source": "temu", "price": 99.0, "approximate_match": True}]
    db_session.commit()
    merge_offer_into_product(
        db_session,
        product,
        source_adapter="temu",
        offer_price=18.5,
        offer_url="https://temu.example/item/42",
        affiliate_link=None,
    )
    offers = product.offers
    assert len(offers) == 1
    assert offers[0]["price"] == 18.5
    # No affiliate link supplied -> falls back to the offer URL.
    assert product.affiliate_links["temu"] == "https://temu.example/item/42"
