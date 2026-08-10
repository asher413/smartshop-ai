"""
Rakuten Advertising adapter (formerly LinkShare).

Why this matters: Rakuten Advertising is the affiliate network that Etsy
and a growing number of big brands moved their affiliate programs to.
One set of credentials unlocks tens of thousands of merchants (Etsy,
Sephora, Home Depot, Macy's, Walmart Marketplace, ...) through Rakuten's
Product Search API — same "one integration, many merchants" leverage as
Awin/CJ, but with a real-time product search endpoint instead of bulk
feeds.

Auth is OAuth2 client-credentials (two steps):
  1. POST https://api.linksynergy.com/token
     Authorization: Bearer base64(client_id:client_secret)
     body: scope=<account_id>          (form-encoded)
     -> {"access_token": "..."}
  2. Every API call carries  Authorization: Bearer <access_token>

Endpoints used:
  - GET /productsearch/1.0    real-time product search (XML response)
  - POST /v1/links/deep_links  turns a product URL into a tracked deep link

Credentials (from the Rakuten Advertising dashboard -> Developer Portal):
  - RAKUTEN_CLIENT_ID       the app key / client id
  - RAKUTEN_CLIENT_SECRET   the app secret
  - RAKUTEN_ACCOUNT_ID      your network/site id (the OAuth2 "scope")

Without credentials this adapter contributes nothing (no scraping
fallback — Rakuten has no useful public listing pages to scrape), so the
pipeline simply skips it until you're approved.
"""
import base64
import time
import xml.etree.ElementTree as ET

import requests

from app.core.config import settings
from app.adapters.base_adapter import BaseSupplierAdapter, RawProduct

BASE_URL = "https://api.linksynergy.com"


class RakutenAdapter(BaseSupplierAdapter):
    name = "rakuten"

    def __init__(self):
        self.client_id = settings.rakuten_client_id
        self.client_secret = settings.rakuten_client_secret
        self.account_id = settings.rakuten_account_id
        self.uses_official_api = bool(self.client_id and self.client_secret and self.account_id)
        self._token = None
        self._token_expiry = 0.0
        # URL -> merchant mid map filled during fetch_trending so
        # build_affiliate_link() can pass the MERCHANT id (not our network
        # id) to the deep-links API — Rakuten's advertiser_id means the
        # merchant's mid, and it is not derivable from a bare product URL.
        self._mid_by_url: dict[str, str] = {}

    # --- OAuth2 client-credentials -------------------------------------
    def _get_token(self) -> str | None:
        if self._token and time.time() < self._token_expiry:
            return self._token
        if not self.uses_official_api:
            return None
        try:
            basic = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
            resp = requests.post(
                f"{BASE_URL}/token",
                headers={
                    "Authorization": f"Bearer {basic}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={"scope": self.account_id},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            self._token = data["access_token"]
            self._token_expiry = time.time() + int(data.get("expires_in", 3600)) - 60
            return self._token
        except Exception:
            return None

    def _headers(self) -> dict:
        token = self._get_token()
        return {"Authorization": f"Bearer {token}"} if token else {}

    # --- Product search -------------------------------------------------
    @staticmethod
    def _parse_price(price_elem) -> tuple[float, str]:
        """<price><amount>19.99</amount><currency>USD</currency></price>.
        Missing element -> (0.0, 'USD'), never crashes."""
        if price_elem is None:
            return 0.0, "USD"
        try:
            amount = float((price_elem.findtext("amount") or "0").replace(",", ""))
        except (TypeError, ValueError):
            amount = 0.0
        currency = (price_elem.findtext("currency") or "USD").strip()
        return amount, currency

    def fetch_trending(self, category: str | None = None, limit: int = 20) -> list[RawProduct]:
        if not self.uses_official_api:
            return []  # no credentials — contributes nothing, no crash
        # category arrives as a Hebrew taxonomy word; Rakuten expects a
        # merchant name or English keyword. Best-effort: use the category
        # directly (the API is lenient) or a generic trending keyword.
        keyword = (category or "").strip() or "best sellers"
        try:
            resp = requests.get(
                f"{BASE_URL}/productsearch/1.0",
                headers=self._headers(),
                params={
                    "keyword": keyword,
                    "max": min(limit, 50),
                    "pagenumber": 1,
                    "language": "en_US",
                },
                timeout=15,
            )
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
        except Exception:
            return []

        results = []
        for item in root.findall(".//item")[:limit]:
            product_id = item.findtext("linkid") or item.findtext("sku") or ""
            name = item.findtext("productname") or ""
            if not product_id or not name:
                continue
            price, currency = self._parse_price(item.find("price") or item.find("saleprice"))
            sale_price, _ = self._parse_price(item.find("saleprice"))
            if sale_price and (price == 0.0 or sale_price < price):
                price = sale_price
            raw_url = item.findtext("linkurl") or ""
            mid = item.findtext("mid") or ""
            if raw_url:
                self._mid_by_url[raw_url] = mid
            results.append(RawProduct(
                source_adapter=self.name,
                external_id=product_id,
                name=name,
                price=price,
                currency=currency,
                url=raw_url,
                image_url=item.findtext("imageurl") or "",
                demand_score=0.0,
                extra={
                    "advertiser_id": mid,
                    "advertiser_name": item.findtext("merchantname") or "",
                },
            ))
        return results

    def fetch_offer(self, external_id_or_url: str) -> RawProduct | None:
        # No per-item refresh endpoint in the search API — re-search by the
        # id as a keyword, matching how CJ/Awin handle this.
        results = self.fetch_trending(category=external_id_or_url, limit=1)
        return results[0] if results else None

    def build_affiliate_link(self, raw_url: str) -> str:
        if not self.uses_official_api:
            return raw_url
        # Product-search linkurls are sometimes ALREADY tracked deep links
        # (linksynergy.com) — pass those through unchanged.
        if raw_url and "linksynergy.com" in raw_url:
            return raw_url
        # The deep-links API expects the MERCHANT's mid as advertiser_id,
        # not our network id. We know it from the last fetch_trending()
        # that returned this URL; if unknown, return the raw URL and let
        # the verifier flag it as untracked (honest failure).
        mid = self._mid_by_url.get(raw_url, "")
        if not mid:
            return raw_url
        try:
            resp = requests.post(
                f"{BASE_URL}/v1/links/deep_links",
                headers={**self._headers(), "Content-Type": "application/json"},
                json={"url": raw_url, "advertiser_id": mid},
                timeout=10,
            )
            resp.raise_for_status()
            deep_link = resp.json().get("advertiser", {}).get("deep_link", "")
            if deep_link:
                return deep_link
        except Exception:
            pass
        return raw_url

    def fetch_coupons(self, limit: int = 20) -> list[dict]:
        """Coupon feed from the Rakuten coupon API (best-effort; requires
        credentials — returns [] without them)."""
        if not self.uses_official_api:
            return []
        try:
            resp = requests.get(
                f"{BASE_URL}/coupon/1.0",
                headers=self._headers(),
                params={"network": self.account_id},
                timeout=15,
            )
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
        except Exception:
            return []

        items = []
        for c in root.findall(".//coupon")[:limit]:
            code = c.findtext("code") or c.findtext("couponcode") or ""
            if not code:
                continue
            items.append({
                "code": code,
                "discount": c.findtext("description") or c.findtext("type") or None,
                "source": "rakuten",
                "valid_until": c.findtext("enddate") or None,
            })
        return items
