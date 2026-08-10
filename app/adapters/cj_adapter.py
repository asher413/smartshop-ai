"""
CJ Affiliate (formerly Commission Junction) — the second major affiliate
NETWORK alongside Awin. Between the two, they cover a huge share of large
US/EU retailers (Walmart, Wayfair, GoPro, many electronics/home brands)
that don't have their own public product API. Same "one integration, many
merchants" leverage as Awin — approve into more CJ advertiser programs
from their dashboard and they show up here with zero code changes.

Uses CJ's Product Catalog Search API (real-time product search across
advertisers you're linked to), which is more immediately useful than
Awin's bulk feed files for a "what's trending right now" discovery flow.
"""
import requests

from app.core.config import settings
from app.adapters.base_adapter import BaseSupplierAdapter, RawProduct

SEARCH_URL = "https://ads.api.cj.com/query"


class CJAdapter(BaseSupplierAdapter):
    name = "cj"

    def __init__(self):
        self.api_token = settings.cj_api_token
        self.company_id = settings.cj_company_id
        self.uses_official_api = bool(self.api_token and self.company_id)

    def fetch_trending(self, category: str | None = None, limit: int = 20) -> list[RawProduct]:
        if not self.uses_official_api:
            return []

        # CJ's product search is GraphQL-based; this is the query shape,
        # scoped to advertisers your account is linked to.
        query = """
        query {
          productSearch(companyId: "%s", keywords: "%s", limit: %d) {
            products {
              id
              title
              price { amount currency }
              buyUrl
              imageUrl
              advertiserName
            }
          }
        }
        """ % (self.company_id, category or "trending gadgets", min(limit, 50))

        try:
            resp = requests.post(
                SEARCH_URL,
                json={"query": query},
                headers={"Authorization": f"Bearer {self.api_token}"},
                timeout=15,
            )
            resp.raise_for_status()
            products = resp.json().get("data", {}).get("productSearch", {}).get("products", [])
        except Exception:
            return []

        results = []
        for item in products[:limit]:
            price = item.get("price", {})
            results.append(RawProduct(
                source_adapter=self.name,
                external_id=str(item.get("id", "")),
                name=item.get("title", ""),
                price=float(price.get("amount", 0) or 0),
                currency=price.get("currency", "USD"),
                url=item.get("buyUrl", ""),
                image_url=item.get("imageUrl", ""),
                extra={"advertiser": item.get("advertiserName", "")},
            ))
        return results

    def fetch_offer(self, external_id_or_url: str) -> RawProduct | None:
        return None  # per-item refresh isn't part of the search API; re-search by name instead

    def build_affiliate_link(self, raw_url: str) -> str:
        # CJ's buyUrl from the Product Catalog API already includes your
        # tracking — pass-through as-is rather than re-wrapping it.
        return raw_url

    def fetch_coupons(self, limit: int = 20) -> list[dict]:
        """Active advertiser coupons from the CJ Coupon API (requires token;
        returns [] without credentials)."""
        if not self.uses_official_api:
            return []
        try:
            resp = requests.get(
                f"https://cjtok.cj.com/coupon/v1/coupons?advertiser-ids={self.company_id}&page-size={min(limit, 50)}",
                headers={"Authorization": f"Bearer {self.api_token}"},
                timeout=12,
            )
            resp.raise_for_status()
            rows = resp.json().get("coupons", [])
        except Exception:
            return []

        items = []
        for c in rows[:limit]:
            code = c.get("couponCode") or ""
            if not code:
                continue
            items.append({
                "code": code,
                "discount": c.get("discount") or c.get("description"),
                "source": "cj",
                "valid_until": c.get("expiryDate"),
            })
        return items
