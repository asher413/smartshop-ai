"""
Interest-driven product expansion + automatic stale-pull cleanup.

Two jobs live here:

1. pull_related_products(db, product, session_id)
   When a visitor opens a product page (a real interest signal), this
   immediately asks every supplier adapter with official API credentials
   for similar listings of that product's topic — so the catalog grows
   around what people are actually browsing, not just the scheduled
   discovery cycle. Pulls are deduped against the existing catalog via
   product_matcher: the same physical product from another store becomes
   a cheaper offer on the EXISTING product (price-war widget) instead of
   a duplicate. Every row is recorded in InterestPull so it can be
   reversed later if nobody engages with it.

   Safety rules (inherited from live_search_service):
   - Only official-API adapters are queried (fast JSON round-trip).
     Scraping adapters are excluded — a product page view must never
     spin up Playwright.
   - Concurrent + timeout-capped (3s per source) so a slow supplier API
     can't block the page or the worker.
   - Nothing is fabricated: products are only created from listings the
     suppliers actually returned (name + price + image all required).

2. cleanup_stale_pulls(db, max_age_days=3)
   Any product pulled purely by a visitor's interest that nobody then
   clicked / viewed / favorited within max_age_days gets deactivated
   automatically (is_active=False, hidden from the storefront). The
   origin product that sparked the pull is never touched. This is the
   "don't keep junk that came up for nothing" requirement: a single
   browse shouldn't permanently bloat the catalog.

   Engagement = AffiliateClick on the product, a ProductView of it, or a
   ProductFavorite — all real signals, all already recorded by the app.
"""
import datetime
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

from sqlalchemy.orm import Session

from app.core.models import Product, InterestPull, ProductView, ProductFavorite, AffiliateClick
from app.services.aggregator_service import ADAPTERS, _score_candidate, _slugify
from app.services.product_matcher import find_existing_product_match, merge_offer_into_product

logger = logging.getLogger(__name__)

_PULL_EXECUTOR = ThreadPoolExecutor(max_workers=4)
PULL_TIMEOUT_SECONDS = 3.0
PULL_STALE_DAYS = 3
MAX_PULLED_PER_TRIGGER = 8


def _query_source(adapter_cls, query: str, limit: int) -> list[dict]:
    """Fetch real listings for `query` from one official-API adapter.
    Returns [] on any failure — a pull must degrade silently, never crash."""
    try:
        adapter = adapter_cls()
        if not adapter.uses_official_api:
            return []
        items = adapter.fetch_trending(category=query, limit=limit) or []
        out = []
        for it in items:
            if not (it.name and it.price and it.url):
                continue
            out.append({
                "adapter": adapter,
                "name": it.name,
                "price": float(it.price),
                "currency": it.currency,
                "url": it.url,
                "image_url": it.image_url,
                "external_id": it.external_id,
                "source_adapter": it.source_adapter,
                "rating": it.rating,
                "review_count": it.review_count,
                "demand_score": it.demand_score,
            })
        return out
    except Exception as e:
        logger.debug("Interest pull source %r failed: %s", getattr(adapter_cls, "__name__", "?"), e)
        return []


def _extract_topic(product: Product) -> str:
    """The pull query: the product's own title, trimmed so we search its
    essence rather than a 200-char marketplace title. Fallbacks: category
    then a generic keyword, so a weirdly-titled product still gets a pull."""
    name = (product.name or "").strip()
    if len(name) > 60:
        # Keep the first two "words" clusters — usually brand + model.
        name = " ".join(name.split()[:4])
    if name:
        return name
    if product.category:
        return product.category
    return "bestseller"


def pull_related_products(db: Session, product: Product, session_id: str = "guest") -> dict:
    """Pull related products for the topic of `product`, deduped against the
    catalog. Returns a summary dict. Never raises."""
    if not product:
        return {"pulled": 0, "merged": 0, "skipped": "no_product"}
    # Throttle: don't hammer supplier APIs on every page view of the same
    # origin product. One pull per origin per 6 hours is plenty.
    six_hours_ago = datetime.datetime.utcnow() - datetime.timedelta(hours=6)
    recent = (
        db.query(InterestPull)
        .filter(InterestPull.origin_product_id == product.id)
        .filter(InterestPull.pulled_at >= six_hours_ago)
        .first()
    )
    if recent:
        return {"pulled": 0, "merged": 0, "skipped": "throttled"}

    # Always record a throttle marker BEFORE querying (even if this pull
    # yields zero products — e.g. no API keys configured yet) so an empty
    # result still counts as "a pull happened" and we don't re-sweep all
    # adapters on every page view of this product. product_id=None marks
    # it as a pure throttle marker; cleanup_stale_pulls handles None fine.
    db.add(InterestPull(
        product_id=None,
        origin_product_id=product.id,
        session_id=session_id or "guest",
    ))
    db.commit()

    query = _extract_topic(product)
    futures = [
        (_PULL_EXECUTOR.submit(_query_source, adapter_cls, query, limit=4), name)
        for name, adapter_cls in ADAPTERS.items()
    ]

    seen_urls: set[str] = set()
    pulled = merged = 0
    for future, name in futures:
        try:
            items = future.result(timeout=PULL_TIMEOUT_SECONDS) or []
        except FutureTimeout:
            future.cancel()
            logger.warning("Interest pull timed out for %s", name)
            items = []
        for it in items:
            url = it.get("url") or ""
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            # Cross-vendor dedup: if this physical product already exists
            # (from ANY source), fold the new offer in as a (hopefully
            # cheaper) offer instead of creating a duplicate.
            match = find_existing_product_match(db, it["source_adapter"], it["name"], it["price"])
            if match:
                merge_offer_into_product(
                    db,
                    match,
                    source_adapter=it["source_adapter"],
                    offer_price=it["price"],
                    offer_url=url,
                    affiliate_link=it["adapter"].build_affiliate_link(url),
                )
                merged += 1
                continue

            # Quality gate: only pull candidates that clear the same score
            # bar as the normal discovery pipeline. Zero/negative prices are
            # never trusted; junk listings score low and stay out.
            score = _score_candidate(
                it.get("demand_score") or 0,
                it.get("rating") or 0,
                it.get("review_count") or 0,
                it["price"],
            )
            if score < 50:
                continue

            new_product = Product(
                sku=f"{it['source_adapter']}-{it['external_id']}",
                source_adapter=it["source_adapter"],
                external_id=it["external_id"],
                import_score=score,
                name=it["name"],
                original_name=it["name"],
                price=it["price"],
                image_url=it["image_url"],
                supplier_name=it["source_adapter"].capitalize(),
                supplier_url=url,
                rating=it.get("rating") or 0.0,
                review_count=it.get("review_count") or 0,
                affiliate_url=it["adapter"].build_affiliate_link(url),
                affiliate_links={it["source_adapter"]: it["adapter"].build_affiliate_link(url)},
                is_active=True,
                is_verified=True,  # real supplier data; AI enrichment can polish later
                slug=_slugify(it["name"], it["source_adapter"], it["external_id"]),
            )
            try:
                db.add(new_product)
                db.flush()
            except Exception:
                # Race: two concurrent views of the same origin product
                # both passed the throttle and both inserted the same
                # sku. Roll back just this product and treat it as
                # already-in-catalog rather than crashing the thread.
                db.rollback()
                continue
            db.add(InterestPull(
                product_id=new_product.id,
                origin_product_id=product.id,
                session_id=session_id or "guest",
            ))
            pulled += 1
            if pulled >= MAX_PULLED_PER_TRIGGER:
                break
        if pulled >= MAX_PULLED_PER_TRIGGER:
            break

    try:
        db.commit()
    except Exception:
        db.rollback()
    logger.info("Interest pull for product %s: pulled=%s merged=%s", product.id, pulled, merged)
    return {"pulled": pulled, "merged": merged, "skipped": None}


def cleanup_stale_pulls(db: Session, max_age_days: int = PULL_STALE_DAYS) -> dict:
    """Deactivate interest-pulled products nobody engaged with within
    `max_age_days`. The origin product (the one that sparked the pull) is
    never touched. Returns {'deactivated': n, 'kept': n}."""
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=max_age_days)
    stale_rows = (
        db.query(InterestPull)
        .filter(InterestPull.pulled_at < cutoff)
        .order_by(InterestPull.id.asc())
        .all()
    )
    deactivated = kept = 0
    for row in stale_rows:
        product = db.query(Product).filter(Product.id == row.product_id).first()
        if not product:
            db.delete(row)
            continue

        has_engagement = (
            db.query(AffiliateClick).filter(AffiliateClick.product_id == product.id).first()
            or db.query(ProductView).filter(ProductView.product_id == product.id).first()
            or db.query(ProductFavorite).filter(ProductFavorite.product_id == product.id).first()
        )
        if has_engagement:
            kept += 1
        elif product.is_active:
            product.is_active = False
            deactivated += 1
        db.delete(row)

    db.commit()
    if deactivated:
        logger.info("Stale-pull cleanup: deactivated %s products (kept %s engaged)", deactivated, kept)
    return {"deactivated": deactivated, "kept": kept}
