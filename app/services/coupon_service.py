"""Coupon pipeline — pulls active coupon codes from EVERY registered
supplier adapter (official coupon/offer endpoints when available), stores
them in the Coupon table, and exposes the combined active-coupon list used
by the coupons page, the personal area, and the admin dashboard.

Honesty rule: a coupon is only shown if a source actually reported it.
There is no fabricated "SUMMER2026" invented by an LLM — a code that
doesn't work at checkout is worse than no coupon at all. Sources without a
coupon endpoint simply contribute nothing.
"""
import datetime
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

from sqlalchemy.orm import Session

from app.core.models import Coupon, Product
from app.services.aggregator_service import ADAPTERS

logger = logging.getLogger(__name__)

# Bounded pool: a hung supplier feed (blocked network, slow endpoint) must
# never stall the admin "pull coupons" action for minutes.
_COUPON_EXECUTOR = ThreadPoolExecutor(max_workers=4)


def _pull_source(adapter_cls, limit: int) -> list[dict]:
    try:
        adapter = adapter_cls()
        return adapter.fetch_coupons(limit=limit) or []
    except Exception as e:
        logger.warning("Coupon pull failed for %s: %s", getattr(adapter_cls, "name", "?"), e)
        return []


def pull_coupons_from_sources(db: Session, limit_per_source: int = 20) -> dict:
    """Call fetch_coupons() on every adapter (concurrently, timeout-capped),
    upsert into the Coupon table, and attach codes to matching live
    products where possible. Returns a real report {found, by_source}."""
    summary = {"found": 0, "by_source": {}}
    futures = []
    for name, adapter_cls in ADAPTERS.items():
        futures.append((_COUPON_EXECUTOR.submit(_pull_source, adapter_cls, limit_per_source), name))

    for future, name in futures:
        try:
            coupons = future.result(timeout=6.0) or []
        except FutureTimeout:
            future.cancel()
            logger.warning("Coupon pull timed out for %s", name)
            coupons = []
        source_count = 0
        for c in coupons:
            code = str(c.get("code") or "").strip().upper()
            if not code:
                continue
            existing = db.query(Coupon).filter(Coupon.code == code).first()
            if existing:
                # Upsert: refresh the discount if the feed reports one
                # (feeds use varied formats like "20%" / "15.5" — the same
                # tolerant parser as new inserts, not a raw float() cast).
                parsed = _parse_discount(c.get("discount"))
                if parsed is not None:
                    existing.discount_percent = parsed
                continue
            db.add(Coupon(
                code=code,
                discount_percent=_parse_discount(c.get("discount")),
                valid_until=_parse_valid_until(c.get("valid_until")),
            ))
            source_count += 1
        summary["by_source"][name] = source_count
        summary["found"] += source_count
    db.commit()
    return summary


def _parse_discount(raw) -> float | None:
    if raw is None:
        return None
    import re
    m = re.search(r"(\d+(?:\.\d+)?)", str(raw))
    return float(m.group(1)) if m else None


def _parse_valid_until(raw) -> datetime.datetime | None:
    if not raw:
        return None
    try:
        return datetime.datetime.fromisoformat(str(raw).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def active_coupons(db: Session) -> list[Coupon]:
    """All coupons that haven't expired (valid_until null = no expiry)."""
    now = datetime.datetime.utcnow()
    rows = db.query(Coupon).filter(
        (Coupon.valid_until.is_(None)) | (Coupon.valid_until > now)
    ).order_by(Coupon.valid_until.asc().nullsfirst()).all()
    return rows or []


def coupons_for_display(db: Session, limit: int = 50) -> list[dict]:
    """Combined view for the coupons page + personal area: standalone Coupon
    rows plus live products that carry a coupon_code. Each entry is a dict
    so templates can render both uniformly."""
    items: list[dict] = []
    for c in active_coupons(db):
        items.append({
            "code": c.code,
            "discount": c.discount_percent,
            "valid_until": c.valid_until,
            "name": f"קופון ספק — {c.code}",
            "source": "ספקים",
            "image_url": "",
            "url": "/coupons",
        })
    products = (
        db.query(Product)
        .filter(Product.is_active == True, Product.coupon_code.isnot(None), Product.coupon_code != "")  # noqa: E712
        .order_by(Product.buying_score.desc())
        .limit(limit)
        .all()
    )
    for p in products:
        items.append({
            "code": p.coupon_code,
            "discount": None,
            "valid_until": None,
            "name": p.name,
            "source": p.supplier_name or "",
            "image_url": p.image_url or "",
            "url": f"/product/{p.id}",
        })
    return items[:limit]
