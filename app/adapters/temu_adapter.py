"""
Temu has no public affiliate/product API at the time of writing, so this
adapter is a thin, explicit wrapper around the scraping fallback. Kept as
its own file (rather than just using ScrapingAdapter directly in the
registry) so that the day Temu *does* ship an API, you swap the internals
here without touching the aggregator or worker code at all.
"""
from app.adapters.base_adapter import BaseSupplierAdapter, RawProduct
from app.adapters.scraping_adapter import ScrapingAdapter
from app.core.config import settings


class TemuAdapter(BaseSupplierAdapter):
    name = "temu"
    uses_official_api = False

    def __init__(self):
        self._impl = ScrapingAdapter(source_name="temu", supported_domain="temu.com")
        self.affiliate_id = settings.temu_affiliate_id

    def fetch_trending(self, category: str | None = None, limit: int = 20) -> list[RawProduct]:
        return self._impl.fetch_trending(category=category, limit=limit)

    def fetch_offer(self, external_id_or_url: str) -> RawProduct | None:
        return self._impl.fetch_offer(external_id_or_url)

    def build_affiliate_link(self, raw_url: str) -> str:
        if not self.affiliate_id:
            return self._impl.build_affiliate_link(raw_url)
        separator = "&" if "?" in raw_url else "?"
        return f"{raw_url}{separator}aff_id={self.affiliate_id}"
