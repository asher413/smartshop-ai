"""
Powers the 'live price war' widget on the product page and the periodic
price-audit job. Two distinct operations, kept honest about their
confidence level:

- refresh_own_offer(): re-fetches the exact product from its own source
  adapter — this is an exact match, safe to show with full confidence.
- find_cross_supplier_matches(): searches OTHER adapters for listings
  that pass product_matcher's name+price similarity gate. A pass is a
  strong signal but not a guarantee of an identical item, so results are
  still labeled 'approximate match' in the API response — don't strip
  that label in the frontend.

Every outbound call is wrapped in a hard wall-clock timeout. Live
scraping / supplier APIs can hang for 30-45s each when the network is
blocked or a marketplace bot-checks us, and the price-war endpoint would
otherwise stall the product page indefinitely.
"""
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

from app.core.models import Product, PriceAudit
from app.services.aggregator_service import ADAPTERS
from app.services.product_matcher import name_similarity, price_compatible, MATCH_SIMILARITY_THRESHOLD

logger = logging.getLogger(__name__)

# Shared bounded pool so hung outbound calls can't leak threads per request.
_PRICE_EXECUTOR = ThreadPoolExecutor(max_workers=3)


def _with_timeout(fn, timeout_seconds: float = 4.0):
    """Run fn on a worker thread and return its result, or None on timeout.
    The abandoned call keeps running until the network gives up, but the
    caller never waits longer than timeout_seconds."""
    future = _PRICE_EXECUTOR.submit(fn)
    try:
        return future.result(timeout=timeout_seconds)
    except FutureTimeout:
        future.cancel()
        logger.warning("Price adapter call timed out after %ss", timeout_seconds)
        return None
    except Exception:
        logger.warning("Price adapter call failed", exc_info=True)
        return None


def refresh_own_offer(db, product: Product) -> dict:
    adapter_cls = ADAPTERS.get(product.source_adapter)
    if not adapter_cls or not product.supplier_url:
        return {"status": "unavailable", "message": "אין מקור מחיר חי למוצר זה כרגע"}

    adapter = adapter_cls()
    fresh = _with_timeout(lambda: adapter.fetch_offer(product.supplier_url))
    if not fresh:
        return {"status": "unavailable", "message": "לא הצלחנו לרענן את המחיר כרגע, נסה שוב מאוחר יותר"}

    if fresh.price and abs(fresh.price - (product.price or 0)) > 0.01:
        db.add(PriceAudit(product_id=product.id, old_price=product.price, new_price=fresh.price))
        product.price = fresh.price
        db.commit()

    return {
        "status": "ok",
        "message": f"המחיר עודכן מול {product.supplier_name}",
        "offers": [{
            "source": product.supplier_name,
            "price": fresh.price,
            "shipping_time": f"{product.shipping_days} ימים",
        }],
    }


def find_cross_supplier_matches(product: Product, limit_per_source: int = 3) -> list[dict]:
    """
    Find the same item on OTHER stores for the price-war widget.

    Before this change every result an adapter returned for the category
    query was appended verbatim — so the "price comparison" often showed
    unrelated listings. Now every candidate must clear the same name+price
    gate used by the import pipeline (product_matcher) before it appears;
    matches are sorted by similarity, strongest first.
    """
    matches = []
    # Fire every source's fetch concurrently (one future each) so the total
    # worst-case wait is one adapter timeout (~4s), not N × 4s. Each future
    # is still individually timeout-capped so a single hung adapter can't
    # stall the widget.
    futures = []
    for name, adapter_cls in ADAPTERS.items():
        if name == product.source_adapter:
            continue
        adapter = adapter_cls()
        futures.append(
            (_PRICE_EXECUTOR.submit(
                lambda a=adapter: a.fetch_trending(category=product.name, limit=limit_per_source)
            ), name)
        )
    for future, name in futures:
        try:
            candidates = future.result(timeout=4.0) or []
        except FutureTimeout:
            future.cancel()
            logger.warning("Cross-source %s timed out", name)
            continue
        except Exception:
            logger.warning("Cross-source %s failed", name, exc_info=True)
            continue
        for c in candidates:
            if not price_compatible(product.price, c.price):
                continue
            score = name_similarity(product.name, c.name)
            if score < MATCH_SIMILARITY_THRESHOLD:
                continue
            matches.append({
                "source": name.capitalize(),
                "price": c.price,
                "url": c.url,
                "similarity": round(score, 2),
                "approximate_match": True,  # never drop this flag in the UI
            })
    matches.sort(key=lambda m: m["similarity"], reverse=True)
    return matches
