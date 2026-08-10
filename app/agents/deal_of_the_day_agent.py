"""
DealOfTheDayAgent — picks the single strongest deal for the day's hero
slot (homepage banner + admin preview). "Strongest" is a blend of REAL
catalog signals: buying score, rating, review volume, recent affiliate
click heat, and price advantage vs. the local market average. No invented
urgency — the hook is generated from real numbers only.
"""
import datetime
import logging

from sqlalchemy.orm import Session

from app.core.models import Product, AffiliateClick

logger = logging.getLogger(__name__)

MAX_AGE_DAYS = 60  # don't crown a deal that hasn't been refreshed in 2 months


class DealOfTheDayAgent:
    def pick(self, db: Session, limit: int = 12) -> list[dict]:
        """Return ranked candidate deals (richest signal first) — the top
        entry is the official 'דיל היום'. Always returns real products."""
        now = datetime.datetime.utcnow()
        cutoff = now - datetime.timedelta(days=MAX_AGE_DAYS)
        since_week = now - datetime.timedelta(days=7)

        products = (
            db.query(Product)
            .filter(
                Product.is_active == True,  # noqa: E712
                Product.is_verified == True,  # noqa: E712
                Product.price > 0,
                Product.last_updated >= cutoff,
            )
            .order_by(Product.buying_score.desc())
            .limit(60)
            .all()
        )

        from sqlalchemy import func
        rows = (
            db.query(AffiliateClick.product_id, func.count(AffiliateClick.id))
            .filter(AffiliateClick.created_at >= since_week)
            .group_by(AffiliateClick.product_id)
            .all()
        )
        click_counts = {pid: c for pid, c in rows}

        ranked = []
        for p in products:
            heat = click_counts.get(p.id, 0)
            savings = max(0.0, (p.local_market_price or p.price) - p.price) if p.local_market_price else 0.0
            score = (
                (p.buying_score or 0) * 1.0
                + min((p.review_count or 0) / 500.0, 10)
                + min(heat * 2.0, 10)
                + min(savings / 10.0, 8)
                + (p.rating or 0) * 4
            )
            ranked.append({
                "product": p,
                "score": round(score, 1),
                "heat": heat,
                "savings": round(savings, 1),
            })

        ranked.sort(key=lambda r: r["score"], reverse=True)
        return ranked[:limit]

    def hook(self, deal: dict) -> str:
        """One-line Hebrew hook built ONLY from real data on the deal."""
        p = deal["product"]
        savings = deal.get("savings") or 0
        heat = deal.get("heat") or 0

        parts = [f"{p.name}"]
        if savings > 0:
            parts.append(f"חוסכים עד ₪{int(savings)} מול המחיר הממוצע בארץ")
        elif p.local_market_price and p.local_market_price > p.price:
            parts.append("מחיר נמוך מהממוצע בשוק")
        if p.coupon_code:
            parts.append(f"עם קופון {p.coupon_code}")
        if heat:
            parts.append(f"{heat} קליקים השבוע")
        if p.rating and p.rating > 4.4:
            parts.append(f"דירוג {p.rating}/5")
        return "🔥 דיל היום: " + " · ".join(parts[:3])
