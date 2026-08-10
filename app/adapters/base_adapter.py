"""
Every supplier plugs into the aggregator through this one interface.
The aggregator, the auto-import worker, and the fulfillment agent never
need to know whether AliExpress is on-API today and Temu is scraped —
they just call fetch_trending() / fetch_offer() / build_affiliate_link().

When you get approved for a new official API, write a new adapter class,
register it in aggregator_service.ADAPTERS, and nothing else changes.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class RawProduct:
    source_adapter: str
    external_id: str
    name: str
    price: float
    currency: str
    url: str
    image_url: str
    in_stock: bool = True
    rating: float = 0.0
    review_count: int = 0
    demand_score: float = 0.0     # 0-100 "how hot is this right now"
    extra: dict = field(default_factory=dict)


class BaseSupplierAdapter(ABC):
    name: str = "base"
    uses_official_api: bool = False

    @abstractmethod
    def fetch_trending(self, category: str | None = None, limit: int = 20) -> list[RawProduct]:
        """Return currently-hot products for a category (or overall if None)."""
        raise NotImplementedError

    @abstractmethod
    def fetch_offer(self, external_id_or_url: str) -> RawProduct | None:
        """Refresh live price/stock for one product — used for price-war checks."""
        raise NotImplementedError

    @abstractmethod
    def build_affiliate_link(self, raw_url: str) -> str:
        """Wrap a raw supplier URL with this adapter's affiliate/tracking params."""
        raise NotImplementedError

    def fetch_coupons(self, limit: int = 20) -> list[dict]:
        """Return active coupon codes from this supplier.

        Shape per item: {"code": str, "discount": str|None, "source": str,
        "valid_until": str|None}. Default is no coupons — subclasses that
        have a real coupon/offer endpoint override this. The coupon pull
        pipeline (services/coupon_service.py) calls this on every adapter
        and never crashes when a source has no coupon feed.
        """
        return []
