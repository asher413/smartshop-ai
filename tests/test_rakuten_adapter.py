"""
Tests for the Rakuten Advertising adapter (app/adapters/rakuten_adapter.py).

Covers the OAuth2 client-credentials token flow, XML product-search
parsing, deep-link building, and the no-credentials safety path. HTTP is
fully mocked — no live network calls.
"""
import pytest

from app.adapters.rakuten_adapter import RakutenAdapter, BASE_URL

SAMPLE_SEARCH_XML = """<?xml version="1.0" encoding="UTF-8"?>
<result>
  <item>
    <linkid>12345</linkid>
    <mid>789</mid>
    <merchantname>Etsy</merchantname>
    <sku>SKU-1</sku>
    <productname>Handmade Ceramic Mug</productname>
    <price><amount>19.99</amount><currency>USD</currency></price>
    <saleprice><amount>14.99</amount><currency>USD</currency></saleprice>
    <linkurl>https://www.etsy.com/listing/12345</linkurl>
    <imageurl>https://img.etsystatic.com/1.jpg</imageurl>
  </item>
  <item>
    <linkid>67890</linkid>
    <mid>456</mid>
    <merchantname>Sephora</merchantname>
    <sku>SKU-2</sku>
    <productname>Skincare Set</productname>
    <price><amount>49.00</amount><currency>USD</currency></price>
    <linkurl>https://www.sephora.com/p/skincare</linkurl>
    <imageurl>https://img.sephora.com/2.jpg</imageurl>
  </item>
</result>
"""


def _adapter(monkeypatch, client_id="cid", secret="csec", account="999"):
    from app.core import config
    monkeypatch.setattr(config.settings, "rakuten_client_id", client_id, raising=False)
    monkeypatch.setattr(config.settings, "rakuten_client_secret", secret, raising=False)
    monkeypatch.setattr(config.settings, "rakuten_account_id", account, raising=False)
    return RakutenAdapter()


def _mock_token(monkeypatch, status=200, body=None):
    import requests
    body = body or {"access_token": "tok123", "expires_in": 3600}

    def fake_post(url, headers=None, data=None, json=None, timeout=None):
        assert url == f"{BASE_URL}/token"
        assert headers["Authorization"].startswith("Bearer ")
        class R:
            status_code = status
            def raise_for_status(self):
                if status >= 400:
                    raise requests.HTTPError(f"HTTP {status}")
            def json(self):
                return body
            text = str(body)
        return R()
    monkeypatch.setattr("app.adapters.rakuten_adapter.requests.post", fake_post)
    return fake_post


def _mock_search(monkeypatch, xml=SAMPLE_SEARCH_XML, status=200):
    import requests
    def fake_get(url, headers=None, params=None, timeout=None):
        assert url == f"{BASE_URL}/productsearch/1.0"
        class R:
            status_code = status
            content = xml.encode()
            def raise_for_status(self):
                if status >= 400:
                    raise requests.HTTPError(f"HTTP {status}")
        return R()
    monkeypatch.setattr("app.adapters.rakuten_adapter.requests.get", fake_get)
    return fake_get


def test_token_flow(monkeypatch):
    import base64
    adapter = _adapter(monkeypatch)
    recorded = {}

    def fake_post(url, headers=None, data=None, json=None, timeout=None):
        recorded["headers"] = headers
        recorded["data"] = data
        class R:
            status_code = 200
            def raise_for_status(self):
                pass
            def json(self):
                return {"access_token": "abc", "expires_in": 3600}
            text = ""
        return R()
    monkeypatch.setattr("app.adapters.rakuten_adapter.requests.post", fake_post)

    token = adapter._get_token()
    assert token == "abc"
    # Basic auth must be base64(client_id:client_secret) in a Bearer header.
    expected = base64.b64encode(b"cid:csec").decode()
    assert recorded["headers"]["Authorization"] == f"Bearer {expected}"
    assert recorded["data"] == {"scope": "999"}
    # Token is cached.
    assert adapter._token == "abc"


def test_no_credentials_returns_empty(monkeypatch):
    adapter = _adapter(monkeypatch, client_id="", secret="", account="")
    assert adapter.uses_official_api is False
    assert adapter.fetch_trending() == []
    assert adapter.fetch_offer("x") is None


def test_fetch_trending_parses_xml(monkeypatch):
    _mock_token(monkeypatch)
    _mock_search(monkeypatch)
    adapter = _adapter(monkeypatch)

    items = adapter.fetch_trending(category="gifts", limit=10)
    assert len(items) == 2
    first = items[0]
    assert first.source_adapter == "rakuten"
    assert first.external_id == "12345"
    assert first.name == "Handmade Ceramic Mug"
    # saleprice wins over price (14.99 < 19.99)
    assert first.price == 14.99
    assert first.currency == "USD"
    assert first.url == "https://www.etsy.com/listing/12345"
    assert first.image_url == "https://img.etsystatic.com/1.jpg"
    assert first.extra["advertiser_name"] == "Etsy"


def test_fetch_trending_without_category_uses_default_keyword(monkeypatch):
    _mock_token(monkeypatch)
    captured = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        captured["params"] = params
        class R:
            status_code = 200
            content = SAMPLE_SEARCH_XML.encode()
            def raise_for_status(self):
                pass
        return R()
    monkeypatch.setattr("app.adapters.rakuten_adapter.requests.get", fake_get)

    adapter = _adapter(monkeypatch)
    adapter.fetch_trending()
    assert captured["params"]["keyword"] == "best sellers"


def test_fetch_trending_bad_response_is_empty(monkeypatch):
    _mock_token(monkeypatch)
    _mock_search(monkeypatch, xml="<result></result>")
    adapter = _adapter(monkeypatch)
    assert adapter.fetch_trending() == []


def test_build_affiliate_link_uses_deep_links_api(monkeypatch):
    _mock_token(monkeypatch)
    captured = {}

    def fake_post(url, headers=None, data=None, json=None, timeout=None):
        if url.endswith("/token"):
            class R:
                status_code = 200
                def raise_for_status(self):
                    pass
                def json(self):
                    return {"access_token": "tok", "expires_in": 3600}
                text = ""
            return R()
        captured["json"] = json
        class R:
            status_code = 200
            def raise_for_status(self):
                pass
            def json(self):
                return {"advertiser": {"deep_link": "https://click.linksynergy.com/deeplink?id=999&mid=789&murl=https%3A%2F%2Fetsy.com"}}
            text = ""
        return R()
    monkeypatch.setattr("app.adapters.rakuten_adapter.requests.post", fake_post)

    adapter = _adapter(monkeypatch)
    # build_affiliate_link needs the merchant mid, learned during a prior
    # fetch_trending() — simulate that lookup state.
    adapter._mid_by_url = {"https://www.etsy.com/listing/12345": "789"}
    link = adapter.build_affiliate_link("https://www.etsy.com/listing/12345")
    assert link.startswith("https://click.linksynergy.com")
    assert captured["json"]["url"] == "https://www.etsy.com/listing/12345"
    assert captured["json"]["advertiser_id"] == "789"  # merchant mid, not network id


def test_build_affiliate_link_returns_raw_when_mid_unknown(monkeypatch):
    _mock_token(monkeypatch)
    adapter = _adapter(monkeypatch)
    # No prior fetch_trending => no mid => honest raw fallback (verifier flags it).
    assert adapter.build_affiliate_link("https://www.etsy.com/listing/999") == "https://www.etsy.com/listing/999"


def test_build_affiliate_link_passes_through_already_tracked(monkeypatch):
    adapter = _adapter(monkeypatch)
    tracked = "https://click.linksynergy.com/deeplink?id=999&mid=789&murl=etsy"
    assert adapter.build_affiliate_link(tracked) == tracked


def test_fetch_trending_records_mid_for_links(monkeypatch):
    _mock_token(monkeypatch)
    _mock_search(monkeypatch)
    adapter = _adapter(monkeypatch)
    adapter.fetch_trending()
    assert adapter._mid_by_url.get("https://www.etsy.com/listing/12345") == "789"


def test_build_affiliate_link_without_creds_returns_raw(monkeypatch):
    adapter = _adapter(monkeypatch, client_id="", secret="", account="")
    assert adapter.build_affiliate_link("https://etsy.com/x") == "https://etsy.com/x"


def test_fetch_coupons_parses_xml(monkeypatch):
    _mock_token(monkeypatch)
    coupon_xml = """<?xml version="1.0"?><coupons>
      <coupon><code>SAVE10</code><description>10% off</description><enddate>2026-12-31</enddate></coupon>
    </coupons>"""
    captured = {}
    import requests
    def fake_get(url, headers=None, params=None, timeout=None):
        captured["url"] = url
        class R:
            status_code = 200
            content = coupon_xml.encode()
            def raise_for_status(self):
                pass
        return R()
    monkeypatch.setattr("app.adapters.rakuten_adapter.requests.get", fake_get)
    adapter = _adapter(monkeypatch)
    coupons = adapter.fetch_coupons()
    assert captured["url"] == f"{BASE_URL}/coupon/1.0"
    assert coupons[0]["code"] == "SAVE10"
    assert coupons[0]["source"] == "rakuten"


def test_registered_in_aggregator():
    from app.services.aggregator_service import ADAPTERS
    assert "rakuten" in ADAPTERS
    from app.adapters.rakuten_adapter import RakutenAdapter
    assert ADAPTERS["rakuten"] is RakutenAdapter


def test_verification_marks_rakuten_links(monkeypatch):
    from app.core import config
    monkeypatch.setattr(config.settings, "rakuten_client_id", "", raising=False)
    monkeypatch.setattr(config.settings, "rakuten_client_secret", "", raising=False)
    monkeypatch.setattr(config.settings, "rakuten_account_id", "", raising=False)
    from app.services.supplier_verification import check_affiliate_link, missing_keys

    ok, _ = check_affiliate_link("rakuten", "https://etsy.com/x", "https://click.linksynergy.com/deeplink?id=1&mid=2")
    assert ok
    not_ok, _ = check_affiliate_link("rakuten", "https://etsy.com/x", "https://etsy.com/x")
    assert not not_ok
    missing = missing_keys("rakuten")
    assert "rakuten_client_id" in missing
    assert "rakuten_client_secret" in missing
    assert "rakuten_account_id" in missing
