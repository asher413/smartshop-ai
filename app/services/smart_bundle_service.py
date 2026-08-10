"""Smart Bundles — AI-powered complementary product bundles.

When a customer views a product (e.g. a camera), the system suggests a
bundle of related items (camera + memory card + bag) at a bundled discount
price. Uses hardcoded category templates first; falls back to Gemini AI
when no template matches the product's category.
"""
import logging
import random

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.models import Product

logger = logging.getLogger(__name__)

# ── Bundle templates per category ────────────────────────────────────
# Each entry: { "items": [list of search queries to find products in DB],
#               "discount_pct": bundled discount (e.g. 15 = 15% off total),
#               "label": human-readable Hebrew name for the bundle }
BUNDLE_TEMPLATES = {
    "צילום ומוזיקה": [
        {
            "items": ["מצלמה", "כרטיס זיכרון", "תיק למצלמה"],
            "discount_pct": 12,
            "label": "ערכת צלם — חבילת התחלה",
        },
        {
            "items": ["חצובה", "כרטיס זיכרון", "ערכת ניקוי"],
            "discount_pct": 10,
            "label": "אביזרים חיוניים למצלמה",
        },
    ],
    "גאדג'טים": [
        {
            "items": ["שעון חכם", "מטען אלחוטי", "צמיד כושר"],
            "discount_pct": 10,
            "label": "ערכת גאדג'טים חכמה",
        },
    ],
    "משרד ומחשבים": [
        {
            "items": ["מקלדת", "עכבר", "משטח לעכבר"],
            "discount_pct": 15,
            "label": "ערכת משרד מלאה",
        },
        {
            "items": ["מסך", "מקלדת", "עכבר"],
            "discount_pct": 12,
            "label": "עמדת עבודה שלמה",
        },
        {
            "items": ["נתב", "כבל רשת", "מגן מתח"],
            "discount_pct": 8,
            "label": "ערכת רשת ביתית מלאה",
        },
    ],
    "מכשירי חשמל ביתיים": [
        {
            "items": ["בלנדר", "טוסטר", "מכונת קפה"],
            "discount_pct": 10,
            "label": "ערכת מטבח חשמלית",
        },
        {
            "items": ["שואב אבק", "מגב", "מטאטא"],
            "discount_pct": 10,
            "label": "ערכת ניקיון מלאה",
        },
    ],
    "ספורט ופנאי": [
        {
            "items": ["בקבוק מים", "מגבת ספורט", "צמיד כושר"],
            "discount_pct": 8,
            "label": "ערכת כושר אישית",
        },
        {
            "items": ["מזרן יוגה", "רצועות מתיחה", "בלוק יוגה"],
            "discount_pct": 12,
            "label": "ערכת יוגה מלאה",
        },
    ],
    "אופנה": [
        {
            "items": ["חולצה", "ג'ינס", "נעלי ספורט"],
            "discount_pct": 8,
            "label": "לוק מושלם ב-3 פריטים",
        },
    ],
    "יופי וטיפוח": [
        {
            "items": ["מברשת שיער", "מייבש שיער", "מחליק שיער"],
            "discount_pct": 12,
            "label": "ערכת טיפוח שיער מלאה",
        },
    ],
    "לבית ולמטבח": [
        {
            "items": ["סט סירים", "סט סכינים", "קרש חיתוך"],
            "discount_pct": 10,
            "label": "ערכת בישול מתקדמת",
        },
    ],
    "רכיבים אלקטרוניים": [
        {
            "items": ["ארדואינו", "חיישן", "כבל USB"],
            "discount_pct": 8,
            "label": "ערכת DIY אלקטרוניקה",
        },
        {
            "items": ["רחפן", "סוללה", "סט מדחפים"],
            "discount_pct": 12,
            "label": "ערכת רחפן — מוכן לטיסה",
        },
    ],
    "תיקים ומזוודות": [
        {
            "items": ["מזוודה", "תיק גב", "ארנק נסיעות"],
            "discount_pct": 12,
            "label": "חבילת נסיעות מושלמת",
        },
    ],
}

# Fallback bundles that work across any category — used when no template matches.
FALLBACK_BUNDLES = [
    {"items": ["מטען אלחוטי", "כבל USB", "מחזיק פלאפון"], "discount_pct": 10, "label": "חבילת אביזרים שימושיים"},
    {"items": ["מטען", "כבל", "אוזניות"], "discount_pct": 8, "label": "ערכת גאדג'טים"},
    {"items": ["פנס", "סכין רב-תכליתי", "כבל"], "discount_pct": 15, "label": "חבילת שימושי — 3 ב-1"},
]


def _fuzzy_match_category(product_category: str | None) -> str | None:
    """Find the best template key for a product's category string."""
    if not product_category:
        return None
    product_category = product_category.strip()
    # Exact match
    if product_category in BUNDLE_TEMPLATES:
        return product_category
    # Fuzzy match: check if the product's category contains or is contained by any key
    for key in BUNDLE_TEMPLATES:
        if key in product_category or product_category in key:
            return key
    return None


def _search_product(db: Session, query: str, exclude_id: int | None = None) -> Product | None:
    """Find the best matching active product for a search query."""
    like = f"%{query}%"
    q = (
        db.query(Product)
        .filter(Product.is_active == True, Product.is_verified == True)
        .filter(
            Product.name.ilike(like) | Product.description.ilike(like) | Product.category.ilike(like)
        )
        .order_by(Product.review_count.desc(), Product.rating.desc())
    )
    if exclude_id is not None:
        q = q.filter(Product.id != exclude_id)
    return q.first()


def get_bundle(db: Session, product: Product, viewed_product_ids: list[int] | None = None) -> dict | None:
    """Build a Smart Bundle for a given product.

    Returns None if no suitable bundle can be constructed (no matching items
    in the catalog). The returned dict has:
      - products: list of Product objects (the items in the bundle)
      - total_price: sum of individual prices
      - bundle_price: discounted total
      - discount_pct: the bundled discount percentage
      - savings: money saved
      - label: human-readable bundle name (Hebrew)
    """
    if not product:
        return None

    category = _fuzzy_match_category(product.category)

    # Pick a template — prefer category-specific, fall back to generic
    templates = BUNDLE_TEMPLATES.get(category or "", []) or FALLBACK_BUNDLES
    if not templates:
        return None

    # Try each template in order until we find one where we can find all items
    exclude = {product.id}
    if viewed_product_ids:
        exclude.update(viewed_product_ids)

    for template in templates:
        bundle_products = []
        for item_query in template["items"]:
            # Skip items whose name already matches the current product
            if item_query.lower() in (product.name or "").lower():
                continue
            found = _search_product(db, item_query, exclude_id=product.id)
            if found and found.id not in exclude:
                bundle_products.append(found)
                exclude.add(found.id)

        # Need at least 2 additional items for a meaningful bundle
        if len(bundle_products) >= 2:
            total = sum(p.price or 0 for p in bundle_products)
            if total <= 0:
                continue
            discount = template["discount_pct"]
            bundle_price = round(total * (1 - discount / 100))
            return {
                "products": bundle_products[:4],  # max 4 items
                "total_price": total,
                "bundle_price": bundle_price,
                "discount_pct": discount,
                "savings": total - bundle_price,
                "label": template["label"],
            }

    return None


def get_ai_bundle(db: Session, product: Product) -> dict | None:
    """Fallback: use Gemini to suggest a bundle when no template matches.

    This is only called when get_bundle() returns None. The AI generates
    up to 3 complementary product search queries based on the product name
    and category, then we look them up in the catalog.
    """
    if not settings.google_api_key:
        return None

    try:
        from app.agents.gemini_client import gemini_generate_text

        prompt = (
            f"Product: {product.name}\n"
            f"Category: {product.category or 'unknown'}\n\n"
            f"Suggest 3 complementary products that a buyer of the above product would also want to purchase. "
            f"Reply with ONLY a JSON array of 3 Hebrew product search queries, nothing else.\n"
            f'Example: ["כרטיס זיכרון", "תיק למצלמה", "חצובה"]'
        )

        result = gemini_generate_text(prompt, max_tokens=120)
        import json

        # Strip markdown fences if present
        result = result.strip()
        if result.startswith("```"):
            result = result.split("\n", 1)[1].rsplit("\n", 1)[0]

        queries = json.loads(result)
        if not isinstance(queries, list) or len(queries) < 2:
            return None

        bundle_products = []
        exclude = {product.id}
        for q in queries[:4]:
            found = _search_product(db, str(q), exclude_id=product.id)
            if found and found.id not in exclude:
                bundle_products.append(found)
                exclude.add(found.id)

        if len(bundle_products) < 2:
            return None

        total = sum(p.price or 0 for p in bundle_products)
        discount_pct = 10
        bundle_price = round(total * (1 - discount_pct / 100))
        return {
            "products": bundle_products,
            "total_price": total,
            "bundle_price": bundle_price,
            "discount_pct": discount_pct,
            "savings": total - bundle_price,
            "label": "חבילה מותאמת אישית",
        }
    except Exception:
        logger.debug("AI bundle generation failed, falling back", exc_info=True)
        return None
