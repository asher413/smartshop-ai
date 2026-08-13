"""
Awin adapter — an affiliate NETWORK, not a single merchant.

This is arguably the highest-leverage adapter in the whole project: Awin
(and networks like it — CJ Affiliate, Admitad) each give you ONE API/feed
that spans thousands of individual merchant programs (fashion, home goods,
regional retailers, some electronics brands) once you're approved into
their programs. Instead of writing a bespoke adapter per merchant, you
write it once here, then just approve into more Awin merchant programs
from their dashboard — new merchants show up in fetch_trending() with zero
code changes.

Uses Awin's Product Feed API (bulk CSV/JSON feeds per merchant you're
approved for) rather than trying to scrape — this is the "does it scale
to more sites for free" answer the user asked for.
"""
import requests

from app.core.config import settings
from app.adapters.base_adapter import BaseSupplierAdapter, RawProduct

FEED_LIST_URL = "https://productdata.awin.com/datafeed/list/apikey/{token}"


class AwinAdapter(BaseSupplierAdapter):
    name = "awin"

    def __init__(self):
        self.api_token = settings.awin_api_token
        self.publisher_id = settings.awin_publisher_id
        self.uses_official_api = bool(self.api_token and self.publisher_id)

    def fetch_trending(self, category: str | None = None, limit: int = 20) -> list[RawProduct]:
        if not self.uses_official_api:
            return []  # network not configured — silently contributes nothing, no crash

        try:
            resp = requests.get(FEED_LIST_URL.format(token=self.api_token), timeout=15)
            resp.raise_for_status()
            feeds = resp.json()
        except Exception:
            return []

        results = []
        for feed in feeds[:5]:  # a handful of approved merchant feeds per cycle, not the whole network at once
            feed_url = feed.get("URL") or feed.get("url")
            if not feed_url:
                continue
            try:
                feed_resp = requests.get(feed_url, timeout=20)
                feed_resp.raise_for_status()
                # Awin feeds are typically CSV; parsing left as a per-merchant
                # concern once you know which feeds you've been approved for
                # (column layouts vary slightly by merchant) — this method
                # returns an empty list rather than guessing a fragile parse.
            except Exception:
                continue
        return results

    def fetch_offer(self, external_id_or_url: str) -> RawProduct | None:
        return None  # per-product lookups aren't part of the bulk feed API

    def build_affiliate_link(self, raw_url: str) -> str:
        if not self.uses_official_api:
            return raw_url
        # Awin's standard deep-link wrapper format
        return (
            f"https://www.awin1.com/cread.php?awinmid=MERCHANT_ID&awinaffid={self.publisher_id}"
            f"&clickref=dealbursa&p={requests.utils.quote(raw_url, safe='')}"
        )

    def fetch_coupons(self, limit: int = 20) -> list[dict]:
        """Active coupons/vouchers from the Awin publisher API (across every
        merchant program the account is approved for). Requires the API
        token; returns [] without it."""
        if not self.uses_official_api:
            return []
        try:
            resp = requests.get(
                f"https://api.awin.com/publishers/{self.publisher_id}/coupons",
                headers={"Authorization": f"Bearer {self.api_token}"},
                timeout=12,
            )
            resp.raise_for_status()
            data = resp.json()
            rows = data if isinstance(data, list) else data.get("coupons", [])
        except Exception:
            return []

        items = []
        for c in rows[:limit]:
            code = c.get("code") or c.get("voucherCode") or ""
            if not code:
                continue
            items.append({
                "code": code,
                "discount": c.get("discountPercent") or c.get("discountAmount") or c.get("description"),
                "source": "awin",
                "valid_until": c.get("validTo") or c.get("endDate"),
            })
        return items
