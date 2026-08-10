"""
Amazon adapter.

Preferred path: Product Advertising API 5.0 (PA-API). IMPORTANT: Amazon
only grants/keeps PA-API access once your Associates account has 3
qualifying sales within its first 180 days — until then, use plain
affiliate links (?tag=yourtag-20) which is what build_affiliate_link()
does regardless of API status. This adapter still needs *some* source of
truth for "what's trending" before you have PA-API, so it leans on the
scraping fallback for discovery until credentials are present.

PA-API requires AWS Signature v4 request signing. That implementation is
kept in _sign_request() below — it's boilerplate you shouldn't need to
touch, but it MUST be tested against Amazon's official PA-API sandbox/docs
before going live; signature schemes are unforgiving of small mistakes.
"""
import datetime
import hashlib
import hmac
import json
import requests

from app.core.config import settings
from app.adapters.base_adapter import BaseSupplierAdapter, RawProduct
from app.adapters.scraping_adapter import ScrapingAdapter

REGION = "us-east-1"
SERVICE = "ProductAdvertisingAPI"


class AmazonAdapter(BaseSupplierAdapter):
    name = "amazon"

    def __init__(self):
        self.access_key = settings.amazon_paapi_access_key
        self.secret_key = settings.amazon_paapi_secret_key
        self.partner_tag = settings.amazon_partner_tag
        self.host = settings.amazon_paapi_host
        self.uses_official_api = bool(self.access_key and self.secret_key and self.partner_tag)
        self._scraping_fallback = ScrapingAdapter(source_name="amazon", supported_domain="amazon.com")

    # --- AWS SigV4 signing (do not modify casually) ---
    def _sign(self, method: str, path: str, payload: dict) -> dict:
        body = json.dumps(payload)
        amz_date = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        date_stamp = amz_date[:8]

        canonical_headers = (
            f"content-encoding:amz-1.0\n"
            f"content-type:application/json; charset=utf-8\n"
            f"host:{self.host}\n"
            f"x-amz-date:{amz_date}\n"
            f"x-amz-target:com.amazon.paapi5.v1.ProductAdvertisingAPIv1.{path.split('/')[-1]}\n"
        )
        signed_headers = "content-encoding;content-type;host;x-amz-date;x-amz-target"
        payload_hash = hashlib.sha256(body.encode()).hexdigest()
        canonical_request = f"{method}\n{path}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"

        credential_scope = f"{date_stamp}/{REGION}/{SERVICE}/aws4_request"
        string_to_sign = (
            f"AWS4-HMAC-SHA256\n{amz_date}\n{credential_scope}\n"
            f"{hashlib.sha256(canonical_request.encode()).hexdigest()}"
        )

        def _hmac(key, msg):
            return hmac.new(key, msg.encode(), hashlib.sha256).digest()

        k_date = _hmac(f"AWS4{self.secret_key}".encode(), date_stamp)
        k_region = _hmac(k_date, REGION)
        k_service = _hmac(k_region, SERVICE)
        k_signing = _hmac(k_service, "aws4_request")
        signature = hmac.new(k_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()

        auth_header = (
            f"AWS4-HMAC-SHA256 Credential={self.access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        return {
            "content-encoding": "amz-1.0",
            "content-type": "application/json; charset=utf-8",
            "host": self.host,
            "x-amz-date": amz_date,
            "x-amz-target": f"com.amazon.paapi5.v1.ProductAdvertisingAPIv1.{path.split('/')[-1]}",
            "Authorization": auth_header,
        }, body

    def _call(self, operation: str, payload: dict) -> dict | None:
        path = f"/paapi5/{operation.lower()}"
        payload = {
            **payload,
            "PartnerTag": self.partner_tag,
            "PartnerType": "Associates",
            "Marketplace": "www.amazon.com",
        }
        headers, body = self._sign("POST", path, payload)
        try:
            resp = requests.post(f"https://{self.host}{path}", headers=headers, data=body, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return None

    def fetch_trending(self, category: str | None = None, limit: int = 20) -> list[RawProduct]:
        # PA-API has no generic "trending" endpoint — real trending signal
        # comes from SearchItems sorted by "Relevance"/"Featured" within a
        # category, cross-referenced against your own click data. Until
        # you're set up for that, discovery uses the scraping fallback.
        if not self.uses_official_api:
            return self._scraping_fallback.fetch_trending(category=category, limit=limit)

        data = self._call("SearchItems", {
            "Keywords": category or "trending",
            "SearchIndex": "All",
            "ItemCount": min(limit, 10),
            "Resources": ["Images.Primary.Large", "ItemInfo.Title", "Offers.Listings.Price"],
        })
        if not data:
            return self._scraping_fallback.fetch_trending(category=category, limit=limit)

        results = []
        try:
            for item in data.get("SearchResult", {}).get("Items", [])[:limit]:
                price_info = (item.get("Offers", {}).get("Listings", [{}])[0].get("Price", {}))
                results.append(RawProduct(
                    source_adapter=self.name,
                    external_id=item.get("ASIN", ""),
                    name=item.get("ItemInfo", {}).get("Title", {}).get("DisplayValue", ""),
                    price=float(price_info.get("Amount", 0) or 0),
                    currency=price_info.get("Currency", "USD"),
                    url=item.get("DetailPageURL", ""),
                    image_url=item.get("Images", {}).get("Primary", {}).get("Large", {}).get("URL", ""),
                ))
        except Exception:
            return self._scraping_fallback.fetch_trending(category=category, limit=limit)
        return results

    def fetch_offer(self, external_id_or_url: str) -> RawProduct | None:
        if not self.uses_official_api:
            return self._scraping_fallback.fetch_offer(external_id_or_url)
        data = self._call("GetItems", {
            "ItemIds": [external_id_or_url],
            "Resources": ["Images.Primary.Large", "ItemInfo.Title", "Offers.Listings.Price"],
        })
        if not data:
            return self._scraping_fallback.fetch_offer(external_id_or_url)
        try:
            item = data["ItemsResult"]["Items"][0]
            price_info = item.get("Offers", {}).get("Listings", [{}])[0].get("Price", {})
            return RawProduct(
                source_adapter=self.name,
                external_id=item.get("ASIN", ""),
                name=item.get("ItemInfo", {}).get("Title", {}).get("DisplayValue", ""),
                price=float(price_info.get("Amount", 0) or 0),
                currency=price_info.get("Currency", "USD"),
                url=item.get("DetailPageURL", ""),
                image_url=item.get("Images", {}).get("Primary", {}).get("Large", {}).get("URL", ""),
            )
        except Exception:
            return self._scraping_fallback.fetch_offer(external_id_or_url)

    def build_affiliate_link(self, raw_url: str) -> str:
        separator = "&" if "?" in raw_url else "?"
        return f"{raw_url}{separator}tag={self.partner_tag or 'yourtag-20'}"
