"""Affiliate click tracking + best-offer routing."""
from urllib.parse import urlencode, urlparse, parse_qsl, urlunparse

from app.core.models import ClickLog, AffiliateClick, Product


def append_ref_to_url(url: str, ref: str) -> str:
    if not url:
        return "#"
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query))
    query["ref"] = ref
    return urlunparse(parsed._replace(query=urlencode(query)))


def choose_best_target(product: Product) -> tuple[str, str]:
    """Pick which affiliate link to send a click to when a product has
    offers across multiple suppliers. Priority order is deliberately
    configurable here rather than scattered across templates."""
    priority = ["aliexpress", "ebay", "amazon", "temu"]
    links = product.affiliate_links or {}
    if isinstance(links, str):
        links = {}

    target_url = product.affiliate_url or product.supplier_url or "#"
    source = product.source_adapter or "original"
    for candidate in priority:
        if candidate in links:
            target_url = links[candidate]
            source = candidate
            break
    return target_url, source


def log_click(db, product_id: int, source: str, user_ip: str, session_id: str | None = None, ref: str | None = None):
    session_value = session_id or "guest"
    ref_value = ref or "site"
    session_value = f"{session_value}|ref:{ref_value}"

    click = ClickLog(product_id=product_id, source=source, user_ip=user_ip, session_id=session_value)
    affiliate_click = AffiliateClick(
        product_id=product_id,
        source=source,
        ref=ref_value,
        session_id=session_id or "guest",
        user_ip=user_ip,
    )
    db.add(click)
    db.add(affiliate_click)
    db.commit()
