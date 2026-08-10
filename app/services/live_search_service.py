"""Live cross-site search — answers the "search shouldn't only search OUR
catalog, it should search ALL the products of ALL registered sites"
requirement.

When the local catalog has few/no matches for a query, this service asks
every supplier adapter (AliExpress / Amazon / eBay / Temu / Awin / CJ / B&H)
for live listings of that query and merges them in as clearly-labeled
"live from <source>" results with the supplier's own link and price.

Performance & safety rules:
- Only adapters with OFFICIAL API credentials are queried live (a JSON API
  round-trip is fast). Scraping-based adapters are excluded — launching
  Playwright per search keystroke would be slow and fragile.
- All calls run CONCURRENTLY on a shared bounded pool, each capped at a
  3s timeout, so the search page never blocks for more than ~3-4s even
  when some supplier APIs are down.
- Results are deduped and normalized; failures degrade to "no live
  results" silently.
"""
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

from app.services.aggregator_service import ADAPTERS

logger = logging.getLogger(__name__)

_LIVE_EXECUTOR = ThreadPoolExecutor(max_workers=4)
LIVE_TIMEOUT_SECONDS = 3.0


def _query_source(adapter_cls, query: str, limit: int) -> list[dict]:
    try:
        adapter = adapter_cls()
        if not adapter.uses_official_api:
            return []
        items = adapter.fetch_trending(category=query, limit=limit) or []
        out = []
        for it in items:
            if not (it.name and it.price):
                continue
            out.append({
                "name": it.name,
                "price": float(it.price),
                "currency": it.currency,
                "url": it.url,
                "image_url": it.image_url,
                "source": getattr(adapter, "name", "supplier").capitalize(),
            })
        return out
    except Exception as e:
        logger.debug("Live search failed for %s: %s", getattr(adapter_cls, "name", "?"), e)
        return []


def live_search(query: str, limit_per_source: int = 5, max_total: int = 12) -> list[dict]:
    """Concurrent live search across official-API adapters. Returns
    [{"name","price","url","image_url","source"}] deduped by URL."""
    query = (query or "").strip()
    if len(query) < 2:
        return []

    futures = []
    for name, adapter_cls in ADAPTERS.items():
        futures.append((_LIVE_EXECUTOR.submit(_query_source, adapter_cls, query, limit_per_source), name))

    merged: list[dict] = []
    seen_urls: set[str] = set()
    for future, name in futures:
        try:
            items = future.result(timeout=LIVE_TIMEOUT_SECONDS) or []
        except FutureTimeout:
            future.cancel()
            logger.warning("Live search timed out for %s", name)
            items = []
        for it in items:
            url = it.get("url") or ""
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            merged.append(it)
            if len(merged) >= max_total:
                break
    return merged
