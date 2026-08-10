"""
Smart cross-vendor dedup.

The old approach matched products across suppliers by raw name equality or
by dumping every adapter's "trending in <product-name>" result into the
price-war widget — that's how the same physical gadget ends up twice on the
site (once from AliExpress, once from Temu) with split clicks, or how a
price comparison shows a random unrelated listing.

This module is the "same product, different store" detector:

- normalize_name(): canonical token set for a listing title (lowercased,
  punctuation stripped, marketplace filler words dropped).
- name_similarity(): Jaccard-style overlap between two normalized names.
- price_compatible(): are two prices plausibly the same item (currencies,
  promos, and per-vendor markup vary; identical title + wildly different
  price is more likely two different SKUs).
- find_existing_product_match(): strongest existing live Product (from a
  DIFFERENT source adapter) matching a candidate.
- merge_offer_into_product(): fold a cross-vendor offer into the existing
  product (offers[] + affiliate_links{}) instead of creating a duplicate.
"""
from __future__ import annotations

import re
import unicodedata

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.models import Product

# Marketplace/vendor filler words that add no product identity. Kept small
# and conservative: stripping too much merges genuinely different items
# ("Wireless Charger" vs "Charging Cable" would both reduce to "charger").
# Anything not listed here is treated as a real identity token.
STOPWORDS = {
    # English filler
    "the", "a", "an", "and", "or", "for", "with", "new", "hot", "sale",
    "best", "top", "deal", "deals", "price", "cheap", "discount", "free",
    "shipping", "fast", "original", "genuine", "official", "brand",
    "quality", "high", "quality", "mini", "pro", "max", "plus", "deluxe",
    "premium", "latest", "upgrade", "upgraded", "universal", "portable",
    "lightweight", "foldable", "adjustable", "multi", "double", "type",
    # Common units/quantifiers that appear differently across stores
    "set", "pack", "pcs", "piece", "pieces", "lot", "kit", "1pc",
    "100", "2024", "2025", "2023",
}

# Token sets smaller than this can't carry enough identity to match on.
MIN_IDENTITY_TOKENS = 2

# Similarity at/above which two names are considered the same product.
MATCH_SIMILARITY_THRESHOLD = 0.62

# Max relative price gap between two offers of the same physical item.
# Cross-vendor prices routinely differ 20-40% (currency, promos, stock
# swings); anything beyond ~50% is a different SKU.
MAX_PRICE_RATIO = 1.5  # cheaper / more_expensive >= 1/1.5 => same-ish


def _tokens_similarity(a: set[str], b: set[str]) -> float:
    """Shared scoring core so the DB match loop doesn't re-normalize the
    query name (and re-apply the identity-token guard) for every row."""
    if len(a) < MIN_IDENTITY_TOKENS or len(b) < MIN_IDENTITY_TOKENS:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    jaccard = intersection / union if union else 0.0
    containment = intersection / min(len(a), len(b)) if a and b else 0.0
    return max(jaccard, containment * 0.85)


def normalize_name(name: str | None) -> set[str]:
    """Canonical identity token set for a listing title."""
    if not name:
        return set()
    # NFKD splits ligatures/accents so "Café" -> "cafe", "Ａ" -> "A".
    text = unicodedata.normalize("NFKD", name).lower()
    tokens = re.findall(r"[a-z0-9]+", text)
    return {t for t in tokens if t not in STOPWORDS and len(t) > 1}


def name_similarity(name_a: str | None, name_b: str | None) -> float:
    """
    0..1 how likely two listing titles describe the same physical product.

    Uses a combined Jaccard + containment score: exact token-set equality
    scores 1.0, and a title that is a strict subset of the other (one store
    writes "Wireless Charger 15W" where another writes "Wireless Charger
    15W Fast Charging") still scores high instead of being treated as
    unrelated.

    Names that don't carry enough identity tokens are never matched: a bare
    "Charger" would otherwise containment-match every "X Charger" listing
    in the catalog. MIN_IDENTITY_TOKENS is the guard against that.
    """
    return _tokens_similarity(normalize_name(name_a), normalize_name(name_b))


def price_compatible(price_a: float | None, price_b: float | None) -> bool:
    """True if two prices could plausibly be the same item across stores."""
    if not price_a or not price_b or price_a <= 0 or price_b <= 0:
        return False
    cheaper, pricier = min(price_a, price_b), max(price_a, price_b)
    return (pricier / cheaper) <= MAX_PRICE_RATIO


def find_existing_product_match(
    db: Session,
    source_adapter: str,
    name: str,
    price: float | None,
    limit: int = 150,
) -> Product | None:
    """
    Return the strongest existing LIVE Product from a *different* source
    adapter that matches this candidate's name + price, or None.

    Scans the most recent `limit` live products (the pipeline mostly deals
    with fresh imports, so recent-first keeps this cheap while still
    catching the duplicate a day later). Only products from another source
    are considered — the same source is already deduped by external_id.
    """
    query_tokens = normalize_name(name)
    if len(query_tokens) < MIN_IDENTITY_TOKENS:
        return None

    candidates = (
        db.query(Product)
        .filter(Product.is_active == True)  # noqa: E712
        .filter(
            # any source except the candidate's own (own-source dedup is
            # already handled by the external_id unique constraint)
            or_(
                Product.source_adapter.is_(None),
                Product.source_adapter != source_adapter,
            )
        )
        .order_by(Product.last_updated.desc())
        .limit(limit)
        .all()
    )

    best: Product | None = None
    best_score = MATCH_SIMILARITY_THRESHOLD
    for candidate in candidates:
        # Normalize the candidate name once per row; the query name's
        # tokens are already computed above and reused across the loop.
        candidate_tokens = normalize_name(candidate.name)
        score = _tokens_similarity(query_tokens, candidate_tokens)
        if score >= best_score and price_compatible(price, candidate.price):
            best, best_score = candidate, score
    return best


def merge_offer_into_product(
    db: Session,
    product: Product,
    source_adapter: str,
    offer_price: float | None,
    offer_url: str | None,
    affiliate_link: str | None,
) -> Product:
    """
    Fold a cross-vendor offer into an existing product instead of creating
    a duplicate. Preserves the primary listing; the other store becomes an
    entry in product.offers[] (what the price-war widget renders) plus a
    tracked affiliate link in product.affiliate_links{}.
    """
    offers = list(product.offers or [])
    # Replace any stale entry for the same source (e.g. a re-discovery run).
    offers = [o for o in offers if o.get("source") != source_adapter]
    offers.append(
        {
            "source": source_adapter,
            "price": offer_price,
            "url": offer_url,
            "approximate_match": True,  # cross-vendor match is by name+price
        }
    )
    product.offers = offers

    links = dict(product.affiliate_links or {})
    links[source_adapter] = affiliate_link or offer_url
    product.affiliate_links = links

    db.add(product)
    return product
