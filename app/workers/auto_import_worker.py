"""
This is the job that actually makes the site "auto-fill with hit products".

Pipeline per run:
  1. aggregator_service.discover_trending()  -> pulls from every adapter,
     stages in TrendingCandidate, auto-promotes high scorers to Product.
  2. enrich_pending_products()               -> any Product missing AI
     content (fresh promotions) gets a generated title/description/pros/
     cons/AI-verdict/urgency-badge/coupon via the existing agents, then is
     marked is_verified=True so it's safe to show on the storefront.
  3. Optional: ping Telegram for standout new deals.

Run this via scheduler.py (APScheduler, in-process) for small/medium
scale, or swap the loop body into an RQ/Celery task if you need multiple
worker machines later — the function bodies don't change either way.
"""
import logging

from app.core.database import SessionLocal
from app.core.models import Product
from app.services import aggregator_service
from app.agents.content_generator import ContentGenerator
from app.agents.marketing_agent import MarketingAgent

logger = logging.getLogger(__name__)

# Real product-style queries (not broad taxonomy words): broad terms like
# "electronics" return generic category shells on several suppliers, while
# concrete product queries reliably match listing cards and parse cleanly.
# Expanded to cover many product types so the catalog fills with variety.
DEFAULT_CATEGORIES = [
    "wireless charger", "power bank", "headphones", "smart watch",
    "bluetooth speaker", "phone case", "usb c cable", "led strip light",
    "robot vacuum", "air fryer", "electric kettle", "webcam",
    "mechanical keyboard", "gaming mouse", "yoga mat", "water bottle",
    "sunglasses", "backpack", "car phone holder", "dash cam",
    "electric toothbrush", "hair dryer", "coffee maker", "blender",
]


def run_discovery_cycle():
    db = SessionLocal()
    try:
        summary = aggregator_service.discover_trending(db, categories=DEFAULT_CATEGORIES, limit_per_source=6)
        logger.info("Discovery cycle complete: %s", summary)
        return summary
    finally:
        db.close()


def enrich_pending_products(batch_size: int = 10):
    """AI-enrich any Product that was auto-promoted but hasn't been
    written up yet. Bounded batch size so one worker tick can't run for
    hours against a huge backlog."""
    db = SessionLocal()
    content_gen = ContentGenerator()
    marketing = MarketingAgent()
    enriched = 0
    try:
        pending = (
            db.query(Product)
            .filter(Product.is_verified == False)  # noqa: E712
            .filter(Product.is_active == True)      # noqa: E712
            .limit(batch_size)
            .all()
        )
        for product in pending:
            try:
                listing = content_gen.generate_product_listing(product.original_name or product.name)
                product.name = listing.get("title") or product.name
                product.seo_title = listing.get("seo_title") or product.name
                product.description = listing.get("description") or product.description
                product.pros = listing.get("pros", [])
                product.cons = listing.get("cons", [])
                product.feature_ratings = listing.get("feature_ratings", {})
                product.buying_score = int(listing.get("buying_score", 7)) * 10
                product.ai_summary = listing.get("verdict") or product.ai_summary
                product.local_market_price = listing.get("local_market_price_estimate") or None

                product.ai_analysis_tag = marketing.generate_urgency_badge(
                    product_name=product.name,
                    source=product.supplier_name or "",
                    stock_count=product.stock_count,
                )
                product.coupon_code = marketing.generate_coupon_suggestion(
                    product_name=product.name, supplier=product.supplier_name or ""
                )
                product.is_verified = True
                enriched += 1
            except Exception:
                logger.exception("Enrichment failed for product id=%s", product.id)
                continue

        db.commit()
        return enriched
    finally:
        db.close()


def run_full_cycle():
    """Convenience entrypoint: discover, then enrich whatever just landed."""
    discovery_summary = run_discovery_cycle()
    enriched_count = enrich_pending_products(batch_size=25)
    return {**discovery_summary, "enriched": enriched_count}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(run_full_cycle())
