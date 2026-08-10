"""
Two real features that existed as unused DB columns before this file:

1. Price history — DailyPrice rows were defined in models.py but nothing
   ever wrote to that table. record_daily_prices() snapshots every active
   product's current price once a day, which is what powers the price
   history chart on the product page (a real trust/conversion driver —
   showing "here's the last 30 days of price" is standard on any serious
   price-comparison site).

2. Price alert triggering — PriceAlert.is_triggered existed but nothing
   ever set it to True. check_price_alerts() compares every active alert's
   target_price against the product's current price and flips the flag
   (and returns newly-triggered alerts so a caller can email/notify).
"""
import datetime
import logging

from app.core.models import Product, DailyPrice, PriceAlert

logger = logging.getLogger(__name__)


def record_daily_prices(db) -> int:
    """Snapshot today's price for every active product. Safe to run more
    than once a day — it just adds another data point, which is harmless
    for a line chart (rare edge case, not worth extra dedup complexity)."""
    products = db.query(Product).filter(Product.is_active == True).all()  # noqa: E712
    count = 0
    for p in products:
        if p.price is None:
            continue
        db.add(DailyPrice(product_id=p.id, price=p.price))
        count += 1
    db.commit()
    return count


def check_price_alerts(db) -> list[PriceAlert]:
    """Returns alerts that just crossed their target this run, so the
    caller (worker/notification layer) can act on them once — not on
    every future check, since is_triggered flips to True immediately."""
    newly_triggered = []
    pending = db.query(PriceAlert).filter(PriceAlert.is_triggered == False).all()  # noqa: E712
    for alert in pending:
        product = db.query(Product).filter(Product.id == alert.product_id).first()
        if not product or product.price is None:
            continue
        if product.price <= alert.target_price:
            alert.is_triggered = True
            newly_triggered.append(alert)
    if newly_triggered:
        db.commit()
        logger.info("%d price alerts triggered this cycle", len(newly_triggered))
    return newly_triggered
