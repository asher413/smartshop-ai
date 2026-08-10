"""
eBay adapter — this is the easiest official API to get approved for, so
if you only set up one supplier API on day one, make it this one.

Uses eBay's Browse API (OAuth2 client-credentials flow) to search listings,
then wraps result URLs with the Partner Network campaign id for commission
tracking via build_affiliate_link().
"""
import time
import requests

from app.core.config import settings
from app.adapters.base_adapter import BaseSupplierAdapter, RawProduct
from app.adapters.scraping_adapter import ScrapingAdapter

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"


class EbayAdapter(BaseSupplierAdapter):
    name = "ebay"

    def __init__(self):
        self.app_id = settings.ebay_app_id
        self.cert_id = settings.ebay_cert_id
        self.campaign_id = settings.ebay_campaign_id
        self.uses_official_api = bool(self.app_id and self.cert_id)
        self._scraping_fallback = ScrapingAdapter(source_name="ebay", supported_domain="ebay.com")
        self._token = None
        self._token_expiry = 0

    def _get_token(self) -> str | None:
        if self._token and time.time() < self._token_expiry:
            return self._token
        try:
            resp = requests.post(
                TOKEN_URL,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "grant_type": "client_credentials",
                    "scope": "https://api.ebay.com/oauth/api_scope",
                },
                auth=(self.app_id, self.cert_id),  # HTTP Basic: client_id / client_secret
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            self._token = data["access_token"]
            self._token_expiry = time.time() + int(data.get("expires_in", 7200)) - 60
            return self._token
        except Exception:
            return None

    def fetch_trending(self, category: str | None = None, limit: int = 20) -> list[RawProduct]:
        if not self.uses_official_api:
            return self._scraping_fallback.fetch_trending(category=category, limit=limit)
        token = self._get_token()
        if not token:
            return self._scraping_fallback.fetch_trending(category=category, limit=limit)

        try:
            resp = requests.get(
                SEARCH_URL,
                headers={"Authorization": f"Bearer {token}"},
                params={"q": category or "trending", "limit": min(limit, 50), "sort": "bestMatch"},
                timeout=10,
            )
            resp.raise_for_status()
            items = resp.json().get("itemSummaries", [])
        except Exception:
            return self._scraping_fallback.fetch_trending(category=category, limit=limit)

        results = []
        for item in items[:limit]:
            price = item.get("price", {})
            results.append(RawProduct(
                source_adapter=self.name,
                external_id=item.get("itemId", ""),
                name=item.get("title", ""),
                price=float(price.get("value", 0) or 0),
                currency=price.get("currency", "USD"),
                url=item.get("itemWebUrl", ""),
                image_url=item.get("image", {}).get("imageUrl", ""),
            ))
        return results

    def fetch_offer(self, external_id_or_url: str) -> RawProduct | None:
        results = self.fetch_trending(category=external_id_or_url, limit=1)
        return results[0] if results else self._scraping_fallback.fetch_offer(external_id_or_url)

    def build_affiliate_link(self, raw_url: str) -> str:
        if not self.campaign_id:
            return raw_url
        separator = "&" if "?" in raw_url else "?"
        return (
            f"{raw_url}{separator}mkcid=1&mkrid=711-53200-19255-0"
            f"&siteid=0&campid={self.campaign_id}&customid="
        )
