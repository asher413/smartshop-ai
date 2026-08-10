"""Tests for scripts/verify_suppliers.py — the full-registration flow verifier.

Covers the pure logic (affiliate-link validation per supplier) without any
network calls; key-presence checks are covered with monkeypatched settings.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.verify_suppliers import (  # noqa: E402
    AFFILIATE_MARKERS,
    check_affiliate_link,
    missing_keys,
)

# Each supplier must have known tracking markers wired up (a supplier with
# no markers can never be verified — a raw URL would pass silently).
def test_every_adapter_has_markers():
    from app.services.aggregator_service import ADAPTERS
    for name in ADAPTERS:
        assert AFFILIATE_MARKERS.get(name), f"{name} missing affiliate markers"


@pytest.mark.parametrize("supplier,raw,link", [
    # Amazon: tag= is the tracking param
    ("amazon", "https://www.amazon.com/dp/B0ABC", "https://www.amazon.com/dp/B0ABC?tag=my-tag-20"),
    # eBay: campid= from the Partner Network
    ("ebay", "https://www.ebay.com/itm/123", "https://www.ebay.com/itm/123?mkcid=1&campid=123456"),
    # AliExpress: promotion_link style from the official API
    ("aliexpress", "https://www.aliexpress.com/item/100500.html", "https://s.click.aliexpress.com/e/_abcd"),
    # AliExpress fallback: ref= tracking appended by the scraping adapter
    ("aliexpress", "https://www.aliexpress.com/item/100500.html", "https://www.aliexpress.com/item/100500.html?ref=smartshopai"),
    # Temu: aff_id= (affiliate id) OR ref= (click attribution)
    ("temu", "https://www.temu.com/goods.html?goods_id=1", "https://www.temu.com/goods.html?goods_id=1&aff_id=1234"),
    ("temu", "https://www.temu.com/goods.html?goods_id=1", "https://www.temu.com/goods.html?goods_id=1&ref=smartshopai"),
    # Awin: awinaffid= publisher id
    ("awin", "https://shop.example.com/p/1", "https://www.awin1.com/cread.php?awinmid=1&awinaffid=99&p=https%3A%2F%2Fshop.example.com%2Fp%2F1"),
    # B&H: ref= attribution param
    ("bhphoto", "https://www.bhphotovideo.com/c/product/1", "https://www.bhphotovideo.com/c/product/1?ref=smartshopai"),
])
def test_affiliate_link_valid(supplier, raw, link):
    ok, msg = check_affiliate_link(supplier, raw, link)
    assert ok, msg


@pytest.mark.parametrize("supplier,raw,link", [
    # Same URL = no tracking = no commission
    ("amazon", "https://www.amazon.com/dp/B0ABC", "https://www.amazon.com/dp/B0ABC"),
    ("ebay", "https://www.ebay.com/itm/123", "https://www.ebay.com/itm/123"),
    # Tracking param missing
    ("amazon", "https://www.amazon.com/dp/B0ABC", "https://www.amazon.com/dp/B0ABC?tag="),
    ("ebay", "https://www.ebay.com/itm/123", "https://www.ebay.com/itm/123?mkcid=1"),
    # Non-http link
    ("awin", "https://shop.example.com/p/1", "javascript:alert(1)"),
])
def test_affiliate_link_invalid(supplier, raw, link):
    ok, _ = check_affiliate_link(supplier, raw, link)
    assert not ok


def test_cj_pass_through_is_valid():
    # CJ's build_affiliate_link() returns the API buyUrl unchanged because
    # it already carries tracking — that must not be flagged.
    ok, _ = check_affiliate_link("cj", "https://www.example.com/p?aid=123", "https://www.example.com/p?aid=123")
    assert ok


def test_raw_url_with_tracking_passes():
    # Raw URL already carrying tracking (AliExpress promotion_link) passes.
    ok, _ = check_affiliate_link("aliexpress", "https://s.click.aliexpress.com/e/_x", "https://s.click.aliexpress.com/e/_x")
    assert ok


def test_missing_keys_reports_env_names(monkeypatch):
    from app.core import config
    monkeypatch.setattr(config.settings, "ebay_app_id", "", raising=False)
    monkeypatch.setattr(config.settings, "ebay_cert_id", "", raising=False)
    missing = missing_keys("ebay")
    assert "ebay_app_id" in missing and "ebay_cert_id" in missing


def test_scraping_only_suppliers_never_need_keys():
    assert missing_keys("temu") == []
    assert missing_keys("bhphoto") == []
