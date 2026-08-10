"""
The core of "bring hot products in from every site automatically".

This service:
1. Asks every registered adapter for currently-trending products.
2. Scores each candidate (demand + rating + price-sanity).
3. Writes them to TrendingCandidate (staging table) — NOT directly to
   Product. This is deliberate: fully automatic, zero-review imports are
   how affiliate sites end up live with broken links, wildly wrong prices,
   or duplicate junk. High-scoring candidates can be auto-promoted (see
   AUTO_PROMOTE_THRESHOLD), everything else waits for a human glance in
   the admin dashboard.

Add a new supplier by writing an adapter class and adding one line to
ADAPTERS below — nothing else in the app needs to change.
"""
import logging

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.core.models import TrendingCandidate, Product
from app.services.product_matcher import find_existing_product_match, merge_offer_into_product
from app.services import meili_search_service
from app.adapters.aliexpress_adapter import AliExpressAdapter
from app.adapters.amazon_adapter import AmazonAdapter
from app.adapters.ebay_adapter import EbayAdapter
from app.adapters.temu_adapter import TemuAdapter
from app.adapters.awin_adapter import AwinAdapter
from app.adapters.cj_adapter import CJAdapter
from app.adapters.bhphoto_adapter import BHPhotoAdapter
from app.adapters.rakuten_adapter import RakutenAdapter

ADAPTERS = {
    "aliexpress": AliExpressAdapter,
    "amazon": AmazonAdapter,
    "ebay": EbayAdapter,
    "temu": TemuAdapter,
    "awin": AwinAdapter,
    "cj": CJAdapter,
    "rakuten": RakutenAdapter,
    "bhphoto": BHPhotoAdapter,
}

# Candidates scoring at/above this are promoted to live Products without
# waiting for admin approval. Keep this conservative — it's cheap to raise
# once you trust the pipeline, expensive to walk back a bad auto-publish.
AUTO_PROMOTE_THRESHOLD = 85.0


def _score_candidate(demand_score: float, rating: float, review_count: int, price: float) -> float:
    if price <= 0:
        return 0.0  # never trust a zero/negative price enough to auto-promote
    demand_component = min(demand_score, 100) * 0.5
    quality_component = min(rating / 5.0 * 100, 100) * 0.3
    volume_component = min(review_count / 500.0 * 100, 100) * 0.2
    return round(demand_component + quality_component + volume_component, 2)


def discover_trending(
    db: Session,
    categories: list[str] | None = None,
    limit_per_source: int = 15,
    sources: list[str] | None = None,
) -> dict:
    """Pull trending products and stage them for review/promotion.

    By default every registered adapter runs; pass `sources` (e.g.
    ["ebay"]) to pull from ONE supplier only — the admin "משוך מוצרים
    עכשיו" per-supplier button uses this so an operator can refresh a
    single source without triggering all of them."""
    categories = categories or [None]
    summary = {"discovered": 0, "duplicates": 0, "auto_promoted": 0, "cross_vendor_merged": 0, "by_source": {}}

    selected = [(n, c) for n, c in ADAPTERS.items() if not sources or n in sources]
    for source_name, adapter_cls in selected:
        adapter = adapter_cls()
        source_count = 0
        for category in categories:
            try:
                raw_items = adapter.fetch_trending(category=category, limit=limit_per_source)
            except Exception as exc:
                logger.warning("Adapter %r failed for category %r: %s", source_name, category, exc)
                raw_items = []

            for item in raw_items:
                existing = (
                    db.query(TrendingCandidate)
                    .filter_by(source_adapter=item.source_adapter, external_id=item.external_id)
                    .first()
                )
                score = _score_candidate(item.demand_score, item.rating, item.review_count, item.price)

                if existing:
                    existing.raw_price = item.price
                    existing.demand_score = item.demand_score
                    existing.quality_score = score
                    existing.raw_rating = item.rating
                    existing.raw_review_count = item.review_count
                    summary["duplicates"] += 1
                    continue

                # Cross-vendor dedup: the same physical product is often
                # sold on several stores (e.g. AliExpress + Temu both carry
                # "Wireless Charger 15W"). Before staging a brand-new
                # candidate, check whether an existing live Product from a
                # DIFFERENT source already matches — if so, fold this offer
                # into it (price-war widget + affiliate link) instead of
                # creating a duplicate product that would split clicks.
                match = find_existing_product_match(
                    db, item.source_adapter, item.name, item.price
                )
                if match:
                    merge_offer_into_product(
                        db,
                        match,
                        source_adapter=item.source_adapter,
                        offer_price=item.price,
                        offer_url=item.url,
                        affiliate_link=adapter.build_affiliate_link(item.url),
                    )
                    summary["cross_vendor_merged"] += 1
                    continue

                candidate = TrendingCandidate(
                    source_adapter=item.source_adapter,
                    external_id=item.external_id,
                    raw_name=item.name,
                    raw_price=item.price,
                    raw_currency=item.currency,
                    raw_url=item.url,
                    raw_image_url=item.image_url,
                    demand_score=item.demand_score,
                    quality_score=score,
                    raw_rating=item.rating,
                    raw_review_count=item.review_count,
                    status="pending",
                    raw_payload=item.extra,
                )
                db.add(candidate)
                db.flush()
                source_count += 1
                summary["discovered"] += 1

                if score >= AUTO_PROMOTE_THRESHOLD:
                    promote_candidate(db, candidate, adapter)
                    summary["auto_promoted"] += 1

        summary["by_source"][source_name] = source_count

    db.commit()
    return summary


def promote_candidate(db: Session, candidate: TrendingCandidate, adapter=None) -> Product:
    """
    Turn a staged candidate into a live Product. AI enrichment (title,
    description, pros/cons, AI verdict) is deliberately NOT done here —
    that's the auto_import_worker's job right after promotion, so this
    function stays fast and side-effect-light for use from the admin
    'approve' button too.
    """
    adapter = adapter or ADAPTERS[candidate.source_adapter]()
    affiliate_url = adapter.build_affiliate_link(candidate.raw_url)

    product = Product(
        sku=f"{candidate.source_adapter}-{candidate.external_id}",
        source_adapter=candidate.source_adapter,
        external_id=candidate.external_id,
        import_score=candidate.quality_score,
        name=candidate.raw_name,
        original_name=candidate.raw_name,
        price=candidate.raw_price,
        image_url=candidate.raw_image_url,
        supplier_name=candidate.source_adapter.capitalize(),
        supplier_url=candidate.raw_url,
        rating=candidate.raw_rating or 0.0,
        review_count=candidate.raw_review_count or 0,
        affiliate_url=affiliate_url,
        affiliate_links={candidate.source_adapter: affiliate_url},
        is_active=True,
        is_verified=False,  # flips true once AI enrichment + a sanity check pass
        slug=_slugify(candidate.raw_name, candidate.source_adapter, candidate.external_id),
    )
    db.add(product)
    db.flush()

    # Auto-index in Meilisearch so the product appears in instant search
    # immediately — no manual reindex run needed
    meili_search_service.index_product(product)

    candidate.status = "promoted"
    candidate.promoted_product_id = product.id
    return product


def _slugify(name: str, source: str, external_id: str) -> str:
    base = "".join(c.lower() if c.isalnum() else "-" for c in (name or "product"))
    base = "-".join(filter(None, base.split("-")))[:60]
    return f"{base}-{source}-{external_id}"
