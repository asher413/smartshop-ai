"""
B&H Photo Video adapter.

B&H has no public product/affiliate API for catalog discovery, so this is
a thin, explicit wrapper around the scraping fallback — kept as its own
file (rather than just using ScrapingAdapter directly in the registry) so
that the day B&H ships an API, you swap the internals here without
touching the aggregator or worker code at all.

B&H is deliberately in the registry because its product pages are
scraper-friendly (rich schema.org JSON-LD with real prices/titles/images),
which gives the discovery pipeline a genuinely working source of real
products even before any supplier API credentials are configured.
"""
from app.adapters.base_adapter import BaseSupplierAdapter, RawProduct
from app.adapters.scraping_adapter import ScrapingAdapter


class BHPhotoAdapter(BaseSupplierAdapter):
    name = "bhphoto"
    uses_official_api = False

    def __init__(self):
        self._impl = ScrapingAdapter(source_name="bhphoto", supported_domain="bhphotovideo.com")

    def fetch_trending(self, category: str | None = None, limit: int = 20) -> list[RawProduct]:
        return self._impl.fetch_trending(category=category, limit=limit)

    def fetch_offer(self, external_id_or_url: str) -> RawProduct | None:
        return self._impl.fetch_offer(external_id_or_url)

    def build_affiliate_link(self, raw_url: str) -> str:
        return self._impl.build_affiliate_link(raw_url)
