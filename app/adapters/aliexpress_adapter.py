"""
AliExpress adapter.

Preferred path: official AliExpress Affiliate API (open.aliexpress.com /
portals.aliexpress.com affiliate program). You need app_key + app_secret +
tracking_id from your affiliate account — see README "Supplier Setup".

The signing scheme below (MD5, sorted params) matches AliExpress's TOP
gateway convention. Endpoint/method names change occasionally on their side
— confirm against your affiliate dashboard's current API docs before going
live, and treat this as a well-structured starting point, not a black box.

If no credentials are configured, falls back to the generic AI-assisted
scraper (see adapters/scraping_adapter.py) so the pipeline never breaks —
it just runs in a lower-confidence, more-fragile mode until you add the API.
"""
import hashlib
import time
import requests

from app.core.config import settings
from app.adapters.base_adapter import BaseSupplierAdapter, RawProduct
from app.adapters.scraping_adapter import ScrapingAdapter

API_GATEWAY = "https://api-sg.aliexpress.com/sync"


class AliExpressAdapter(BaseSupplierAdapter):
    name = "aliexpress"

    def __init__(self):
        self.app_key = settings.aliexpress_app_key
        self.app_secret = settings.aliexpress_app_secret
        self.tracking_id = settings.aliexpress_tracking_id
        self.uses_official_api = bool(self.app_key and self.app_secret)
        self._scraping_fallback = ScrapingAdapter(source_name="aliexpress", supported_domain="aliexpress.com")

    def _sign(self, params: dict) -> str:
        ordered = "".join(f"{k}{params[k]}" for k in sorted(params.keys()))
        base = f"{self.app_secret}{ordered}{self.app_secret}"
        return hashlib.md5(base.encode("utf-8")).hexdigest().upper()

    def _call(self, method: str, extra_params: dict) -> dict | None:
        params = {
            "method": method,
            "app_key": self.app_key,
            "sign_method": "md5",
            "timestamp": str(int(time.time() * 1000)),
            "v": "2.0",
            "format": "json",
            **extra_params,
        }
        params["sign"] = self._sign(params)
        try:
            resp = requests.get(API_GATEWAY, params=params, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return None

    def fetch_trending(self, category: str | None = None, limit: int = 20) -> list[RawProduct]:
        if not self.uses_official_api:
            return self._scraping_fallback.fetch_trending(category=category, limit=limit)

        data = self._call("aliexpress.affiliate.hotproduct.query", {
            "category_ids": category or "",
            "page_size": str(min(limit, 50)),
            "target_currency": "USD",
            "target_language": "EN",
            "tracking_id": self.tracking_id,
        })
        if not data:
            return self._scraping_fallback.fetch_trending(category=category, limit=limit)

        results = []
        try:
            items = (
                data.get("aliexpress_affiliate_hotproduct_query_response", {})
                .get("resp_result", {})
                .get("result", {})
                .get("products", {})
                .get("product", [])
            )
            for item in items[:limit]:
                results.append(RawProduct(
                    source_adapter=self.name,
                    external_id=str(item.get("product_id")),
                    name=item.get("product_title", ""),
                    price=float(item.get("target_sale_price", 0) or 0),
                    currency=item.get("target_sale_price_currency", "USD"),
                    url=item.get("promotion_link") or item.get("product_detail_url", ""),
                    image_url=item.get("product_main_image_url", ""),
                    rating=float(item.get("evaluate_rate", "0").rstrip("%") or 0) / 20,  # rough 0-5 scale
                    demand_score=float(item.get("lastest_volume", 0) or 0),
                    extra={"raw": item},
                ))
        except Exception:
            return self._scraping_fallback.fetch_trending(category=category, limit=limit)
        return results

    def fetch_offer(self, external_id_or_url: str) -> RawProduct | None:
        if not self.uses_official_api:
            return self._scraping_fallback.fetch_offer(external_id_or_url)

        data = self._call("aliexpress.affiliate.productdetail.get", {
            "product_ids": external_id_or_url,
            "target_currency": "USD",
            "target_language": "EN",
            "tracking_id": self.tracking_id,
        })
        if not data:
            return self._scraping_fallback.fetch_offer(external_id_or_url)
        try:
            item = (
                data.get("aliexpress_affiliate_productdetail_get_response", {})
                .get("resp_result", {})
                .get("result", {})
                .get("products", {})
                .get("product", [])[0]
            )
            return RawProduct(
                source_adapter=self.name,
                external_id=str(item.get("product_id")),
                name=item.get("product_title", ""),
                price=float(item.get("target_sale_price", 0) or 0),
                currency=item.get("target_sale_price_currency", "USD"),
                url=item.get("promotion_link") or item.get("product_detail_url", ""),
                image_url=item.get("product_main_image_url", ""),
            )
        except Exception:
            return self._scraping_fallback.fetch_offer(external_id_or_url)

    def build_affiliate_link(self, raw_url: str) -> str:
        if not self.uses_official_api:
            return self._scraping_fallback.build_affiliate_link(raw_url)
        data = self._call("aliexpress.affiliate.link.generate", {
            "source_values": raw_url,
            "promotion_link_type": "0",
            "tracking_id": self.tracking_id,
        })
        try:
            links = (
                data.get("aliexpress_affiliate_link_generate_response", {})
                .get("resp_result", {})
                .get("result", {})
                .get("promotion_links", {})
                .get("promotion_link", [])
            )
            if links:
                return links[0].get("promotion_link", raw_url)
        except Exception:
            pass
        return raw_url

    # ILAFF campaign coupons — sourced from the Affiracle AliExpress affiliate
    # portal (affiracle.com/affiliates/aliexpress). Real codes valid for the
    # summer 2026 campaign period. These are hardcoded because the AliExpress
    # coupon API endpoint rarely returns campaign-level codes; the ILAFF series
    # is distributed through partner portals, not the public API.
    _ILAFF_COUPONS = [
        {"code": "ILAFF1", "discount": "$15 / ₪46", "source": "aliexpress", "valid_until": None},
        {"code": "ILAFF2", "discount": "$30 / ₪94", "source": "aliexpress", "valid_until": None},
        {"code": "ILAFF3", "discount": "$55 / ₪170", "source": "aliexpress", "valid_until": None},
        {"code": "ILAFF4", "discount": "$80 / ₪249", "source": "aliexpress", "valid_until": None},
        {"code": "ILAFF5", "discount": "$209 / ₪650", "source": "aliexpress", "valid_until": None},
        {"code": "ILAFF6", "discount": "$329 / ₪1,030", "source": "aliexpress", "valid_until": None},
        {"code": "ILAFF7", "discount": "$449 / ₪1,400", "source": "aliexpress", "valid_until": None},
        # ILSC series (ILS coupons) — from AliExpress homepage via affiracle
        {"code": "ILSC05", "discount": "₪78", "source": "aliexpress", "valid_until": None},
        {"code": "ILSC06", "discount": "₪125", "source": "aliexpress", "valid_until": None},
        {"code": "ILSC07", "discount": "₪170", "source": "aliexpress", "valid_until": None},
    ]
    # AliExpress summer campaign landing page (non-affiliate link).
    _CAMPAIGN_LANDING = (
        "https://www.aliexpress.com/ssr/300003326/-262kfs"
        "?disableNav=YES&pha_manifest=ssr&_immersiveMode=true&businessCode=guide"
    )
    # Affiracle AliExpress affiliate store link (Choice page with af=8541
    # tracking parameter). Generated via affiracle.com/s/T1Q4aC — redirects
    # to the Hebrew AliExpress Choice page with full affiliate params.
    # Use this as a general-purpose store entry point when building links
    # without official API access.
    _AFFILIATE_STORE_LINK = (
        "https://he.aliexpress.com/ssr/300000556/zQFHEaEPNJ"
        "?af=8541"
    )
    # AliExpress homepage affiliate link (general store entry).
    _AFFILIATE_HOME_LINK = (
        "https://he.aliexpress.com/?af=8541"
    )

    def fetch_coupons(self, limit: int = 20) -> list[dict]:
        """Active coupons from the AliExpress affiliate platform.
        Returns ILAFF campaign codes (from Affiracle partner portal) plus
        any live codes the official coupon API reports. Without API
        credentials, returns the static ILAFF series only."""
        items = list(self._ILAFF_COUPONS)
        if not self.uses_official_api:
            return items[:limit]
        data = self._call("aliexpress.affiliate.coupon.query", {
            "tracking_id": self.tracking_id,
            "page_size": str(min(limit, 50)),
        })
        if data:
            try:
                result = (
                    data.get("aliexpress_affiliate_coupon_query_response", {})
                    .get("resp_result", {})
                    .get("result", {})
                )
                coupons = result.get("coupon_list", []) if isinstance(result, dict) else []
                for c in coupons[:limit]:
                    code = c.get("coupon_code") or c.get("coupon_name") or ""
                    if not code:
                        continue
                    items.append({
                        "code": code,
                        "discount": c.get("discount") or c.get("amount"),
                        "source": "aliexpress",
                        "valid_until": None,
                    })
            except Exception:
                pass
        return items[:limit]
