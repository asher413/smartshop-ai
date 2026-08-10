"""
Live supplier status — the data behind the admin "סטטוס ספקים" page.

For every registered supplier adapter this reports, in real time:
  * connection mode — official API (green) vs. scraping fallback (yellow)
  * how many live products were imported from it
  * when its products were last pulled/updated
  * how many candidates are waiting in the staging queue

Plus a full end-to-end pull test (key test -> real 1-product pull ->
affiliate link check) that reuses the exact logic of
scripts/verify_suppliers.py, so the admin button and the CLI verifier
never drift apart.
"""
import datetime

from sqlalchemy import func

from app.core.config import settings
from app.core.models import Product, TrendingCandidate
from app.services.aggregator_service import ADAPTERS

# Human-facing supplier metadata for the admin cards.
SUPPLIER_META = {
    "aliexpress": {"icon": "🛍️", "label": "AliExpress"},
    "amazon": {"icon": "📦", "label": "Amazon"},
    "ebay": {"icon": "🏷️", "label": "eBay"},
    "temu": {"icon": "🧸", "label": "Temu"},
    "awin": {"icon": "🌐", "label": "Awin (רשת)"},
    "cj": {"icon": "🤝", "label": "CJ Affiliate"},
    "rakuten": {"icon": "🛍️", "label": "Rakuten Advertising"},
    "bhphoto": {"icon": "📷", "label": "B&H Photo"},
}


def _time_ago(dt) -> str:
    """'לפני 5 דקות' / 'לפני 3 שעות' / 'אתמול' style relative time."""
    if not dt:
        return "אף פעם"
    if isinstance(dt, str):
        try:
            dt = datetime.datetime.fromisoformat(dt)
        except ValueError:
            return dt
    delta = datetime.datetime.utcnow() - dt
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return "עכשיו"
    if seconds < 60:
        return "לפני רגע"
    if seconds < 3600:
        return f"לפני {seconds // 60} דקות"
    if seconds < 86400:
        return f"לפני {seconds // 3600} שעות"
    if seconds < 172800:
        return "אתמול"
    return f"לפני {seconds // 86400} ימים"


def get_supplier_status(db) -> list[dict]:
    """Live status rows for every registered adapter. Pure DB reads — no
    network, safe to call on every page refresh / poll."""
    counts = dict(
        db.query(Product.source_adapter, func.count(Product.id))
        .group_by(Product.source_adapter)
        .all()
    )
    last_updated = dict(
        db.query(Product.source_adapter, func.max(Product.last_updated))
        .group_by(Product.source_adapter)
        .all()
    )
    last_discovered = dict(
        db.query(TrendingCandidate.source_adapter, func.max(TrendingCandidate.discovered_at))
        .group_by(TrendingCandidate.source_adapter)
        .all()
    )
    pending = dict(
        db.query(TrendingCandidate.source_adapter, func.count(TrendingCandidate.id))
        .filter(TrendingCandidate.status == "pending")
        .group_by(TrendingCandidate.source_adapter)
        .all()
    )

    adapter_status = settings.adapter_status()
    rows = []
    for name, cls in ADAPTERS.items():
        official = bool(adapter_status.get(name))
        meta = SUPPLIER_META.get(name, {"icon": "📦", "label": name.capitalize()})
        # "Last pull" = the MOST RECENT signal from this supplier, whether
        # that's a product update or a fresh candidate discovery.
        last_pull = max(
            (ts for ts in (last_updated.get(name), last_discovered.get(name)) if ts),
            default=None,
        )
        rows.append({
            "name": name,
            "icon": meta["icon"],
            "label": meta["label"],
            "mode": "api" if official else "scraping",
            "mode_label": "API רשמי" if official else "סקרייפינג",
            "product_count": int(counts.get(name) or 0),
            "last_pull": (last_pull.isoformat() + "Z") if last_pull else None,
            "last_pull_ago": _time_ago(last_pull),
            "pending_count": int(pending.get(name) or 0),
        })
    return rows


def pull_supplier_products(supplier: str, db=None) -> dict:
    """Run discovery for ONE supplier only and stage/promote its products.

    The admin "משוך מוצרים עכשיו" button per supplier hits this (through
    a background task). When called from the background task no db is
    passed — an own SessionLocal is opened and closed here, exactly like
    run-discovery does. Returns a {status, message, summary} dict.
    """
    if supplier not in ADAPTERS:
        return {"status": "error", "message": f"ספק לא ידוע: {supplier}"}

    own_session = False
    if db is None:
        from app.core.database import SessionLocal
        db = SessionLocal()
        own_session = True
    try:
        from app.services.aggregator_service import discover_trending
        summary = discover_trending(db, sources=[supplier])
        count = summary["discovered"]
        auto = summary["auto_promoted"]
        merged = summary["cross_vendor_merged"]
        parts = [f"{count} מועמדים חדשים"]
        if auto:
            parts.append(f"{auto} עלו לאוויר אוטומטית")
        if merged:
            parts.append(f"{merged} מוזגו למוצרים קיימים")
        duplicates = summary.get("duplicates", 0)
        if not count and not merged:
            if duplicates:
                parts.append("כל המוצרים כבר קיימים במערכת")
            else:
                # Zero items AND zero duplicates = the supplier returned
                # nothing (network down / blocked / no results) — say so
                # honestly instead of pretending everything is a duplicate.
                parts.append("המשיכה לא החזירה מוצרים (0 תוצאות מהספק)")
        return {"status": "ok", "message": ", ".join(parts), "summary": summary}
    except Exception as exc:
        return {"status": "error", "message": f"שגיאה במשיכה: {str(exc)[:160]}"}
    finally:
        if own_session:
            db.close()


def test_supplier_pull(supplier: str) -> dict:
    """Full end-to-end test for one supplier — the same flow the CLI
    verifier runs: key presence -> live key test -> real 1-product pull ->
    affiliate link check. Returns a {status, message} dict for the admin UI."""
    if supplier not in ADAPTERS:
        return {"status": "error", "message": f"ספק לא ידוע: {supplier}"}

    from app.services import settings_service
    from app.services.supplier_verification import (
        REQUIRED_ATTRS,
        SERVICE_NAME,
        check_affiliate_link,
        env_for,
        missing_keys,
    )

    try:
        missing = missing_keys(supplier)
        if missing and REQUIRED_ATTRS.get(supplier):
            keys = ", ".join(env_for(a) for a in missing)
            return {"status": "skip", "message": f"לא הוזנו מפתחות. נדרשים: {keys}"}

        # 1. Live key test (supplier's own endpoint) where one exists.
        service = SERVICE_NAME.get(supplier)
        if service:
            ok, msg = settings_service.run_test(service, {})
            if not ok:
                return {"status": "fail", "message": f"מפתחות נכשלו: {msg}"}

        # 2+3. Pull one real product, then check the affiliate link carries tracking.
        adapter = ADAPTERS[supplier]()
        raw_items = adapter.fetch_trending(category=None, limit=1)
        if not raw_items:
            return {"status": "fail", "message": "המשיכה לא החזירה מוצר (0 תוצאות) — בדקו חסימה או הגדרות"}
        product = raw_items[0]
        link = adapter.build_affiliate_link(product.url)
        ok, msg = check_affiliate_link(supplier, product.url, link)
        name = (product.name or "")[:44]
        price = f"{product.currency or 'USD'} {product.price}" if (product.price or 0) > 0 else "מחיר לא תקין"
        return {
            "status": "ok" if ok else "fail",
            "message": f"'{name}' | {price} · {msg}",
        }
    except Exception as exc:
        return {"status": "error", "message": f"שגיאה: {str(exc)[:160]}"}
