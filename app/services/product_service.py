"""Home-page product enrichment: real conversion signal only, no fake
'X people bought this' numbers — social proof must come from actual
ClickLog data or be omitted, not randomly generated (that's a quiet
integrity issue that erodes trust the moment anyone checks two page loads
and sees the number jump for no reason)."""
import datetime

from sqlalchemy import func

from app.core.models import ClickLog, Product


def enrich_products_for_home(products: list[Product], db):
    """Attach recent click counts + best offer price to each product.

    Originally this ran 2 COUNT queries per product (N+1) — with 24 products
    per home page that's 48 queries per page load, which under concurrent
    load (and SQLite's write-lock serialization) turned the homepage into
    the slowest route on the site. Now the two count windows are fetched as
    two batched GROUP BY queries and joined in Python — 2 queries total
    regardless of page size.
    """
    if not products:
        return
    now = datetime.datetime.utcnow()
    ids = [p.id for p in products]

    hour_cutoff = now - datetime.timedelta(hours=1)
    day_cutoff = now - datetime.timedelta(hours=24)
    hour_rows = (
        db.query(ClickLog.product_id, func.count(ClickLog.id))
        .filter(ClickLog.product_id.in_(ids), ClickLog.created_at > hour_cutoff)
        .group_by(ClickLog.product_id)
        .all()
    )
    day_rows = (
        db.query(ClickLog.product_id, func.count(ClickLog.id))
        .filter(ClickLog.product_id.in_(ids), ClickLog.created_at > day_cutoff)
        .group_by(ClickLog.product_id)
        .all()
    )
    hour_counts = {pid: c for pid, c in hour_rows}
    day_counts = {pid: c for pid, c in day_rows}

    for product in products:
        product.recent_conversions = hour_counts.get(product.id, 0)
        product.recent_deal_clicks = day_counts.get(product.id, 0)

        offers = product.offers or []
        if offers:
            try:
                product.best_price = min(float(o.get("price", product.price)) for o in offers)
            except Exception:
                product.best_price = product.price
        else:
            product.best_price = product.price
