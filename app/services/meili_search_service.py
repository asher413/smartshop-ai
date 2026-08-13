"""
Instant-search engine backed by Meilisearch (self-hosted, free, no API limits).

Why Meilisearch instead of just the existing chromadb vector search:
- Typo-tolerant: "אוזנית" finds "אוזניות" (chromadb embedding doesn't handle typos well)
- Faceted filtering: price range, category, supplier, rating — all filterable
- Instant results: < 50ms even with 100k products (chromadb is slower under load)
- Sorting: price ascending, newest, rating — native sort
- Relevance scoring: tuned per-index, no need to manage embedding models

The service degrades gracefully: if Meilisearch is down, the app falls back to
the existing SQL keyword search. Nothing breaks, nothing hangs.
"""
import logging
import os

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.models import Product

logger = logging.getLogger(__name__)

MEILI_URL = os.getenv("MEILI_URL", "http://127.0.0.1:7700")
MEILI_MASTER_KEY = os.getenv("MEILI_MASTER_KEY", "dealbursa-meili-master-key-change-me")
INDEX_NAME = "products"

# Fields indexed for full-text search (typo-tolerant)
SEARCHABLE_ATTRIBUTES = ["name", "description", "category", "ai_summary", "supplier_name"]

# Fields available for faceted filtering (filterable in the sidebar)
FILTERABLE_ATTRIBUTES = [
    "category",
    "source_adapter",
    "price",
    "rating",
    "stock_count",
    "is_trending",
    "is_verified",
    "discount_percent",
]

# Fields available for sorting
SORTABLE_ATTRIBUTES = ["price", "rating", "review_count", "import_score", "last_updated"]

# Fields returned in search results
DISPLAYED_ATTRIBUTES = [
    "id", "name", "price", "image_url", "category", "source_adapter",
    "supplier_name", "rating", "review_count", "discount_percent",
    "is_trending", "is_verified", "stock_count", "ai_summary",
    "coupon_code", "shipping_days",
]


class MeiliSearchService:
    """Thin wrapper around the Meilisearch Python client. All public methods
    never raise: they log and return empty results / False on failure so the
    app never goes down just because the search engine is unreachable."""

    def __init__(self, url: str = MEILI_URL, api_key: str = MEILI_MASTER_KEY):
        self._url = url
        self._api_key = api_key
        self._client = None
        self._available = None  # tri-state: None=unknown, True/False

    @property
    def client(self):
        if self._client is None and self._available is not False:
            try:
                import meilisearch
                self._client = meilisearch.Client(self._url, self._api_key)
                # Health check — a single fast ping
                self._client.health()
                self._available = True
                logger.info("Meilisearch connected at %s", self._url)
            except Exception as e:
                self._available = False
                self._client = None
                logger.warning("Meilisearch unavailable at %s: %s. Falling back to SQL search.", self._url, e)
        return self._client

    @property
    def available(self) -> bool:
        if self._available is None:
            self.client  # trigger health check
        return self._available is True

    # ── Index management ──────────────────────────────────────────

    def ensure_index(self) -> bool:
        """Create or update the products index with correct settings. Idempotent."""
        if not self.available:
            return False
        try:
            index = self.client.get_index(INDEX_NAME)
        except Exception:
            try:
                self.client.create_index(INDEX_NAME, {"primaryKey": "id"})
                index = self.client.get_index(INDEX_NAME)
            except Exception as e:
                logger.warning("Failed to create Meilisearch index: %s", e)
                return False

        try:
            index.update_searchable_attributes(SEARCHABLE_ATTRIBUTES)
            index.update_filterable_attributes(FILTERABLE_ATTRIBUTES)
            index.update_sortable_attributes(SORTABLE_ATTRIBUTES)
            index.update_displayed_attributes(DISPLAYED_ATTRIBUTES)
            # Typo tolerance: allow 1 typo for short words, 2 for longer
            index.update_typo_tolerance({"enabled": True, "minWordSizeForTypos": {"oneTypo": 3, "twoTypos": 6}})
            # Ranking: exact match > word count > typo > proximity > attribute > sort
            index.update_ranking_rules([
                "words",
                "typo",
                "proximity",
                "attribute",
                "sort",
                "exactness",
                "price:asc",
            ])
            return True
        except Exception as e:
            logger.warning("Failed to configure Meilisearch index: %s", e)
            return False

    def reset_index(self) -> bool:
        """Delete and recreate the index. Used from admin panel."""
        if not self.available:
            return False
        try:
            self.client.delete_index(INDEX_NAME)
            return self.ensure_index()
        except Exception:
            return self.ensure_index()

    def get_stats(self) -> dict:
        """Return index statistics for the admin dashboard."""
        if not self.available:
            return {"available": False, "document_count": 0, "index_size": 0}
        try:
            stats = self.client.get_index(INDEX_NAME).get_stats()
            return {
                "available": True,
                "document_count": stats.number_of_documents,
                "index_size": stats.used_database_size if hasattr(stats, "used_database_size") else 0,
                "last_update": stats.updated_at if hasattr(stats, "updated_at") else None,
            }
        except Exception as e:
            logger.warning("Failed to get Meilisearch stats: %s", e)
            return {"available": True, "document_count": 0, "index_size": 0, "error": str(e)}

    # ── Document CRUD ─────────────────────────────────────────────

    @staticmethod
    def _product_to_doc(product: Product) -> dict:
        """Convert a SQLAlchemy Product to a Meilisearch document."""
        return {
            "id": product.id,
            "name": product.name or "",
            "price": float(product.price or 0),
            "image_url": product.image_url or "",
            "category": product.category or "",
            "source_adapter": product.source_adapter or "",
            "supplier_name": product.supplier_name or "",
            "rating": float(product.rating or 0),
            "review_count": product.review_count or 0,
            "discount_percent": _calc_discount(product),
            "is_trending": bool(product.is_trending),
            "is_verified": bool(product.is_verified),
            "stock_count": product.stock_count or 0,
            "ai_summary": product.ai_summary or "",
            "coupon_code": product.coupon_code or "",
            "shipping_days": product.shipping_days or 0,
            "description": product.description or "",
            "last_updated": product.last_updated.isoformat() if product.last_updated else None,
            "import_score": float(product.import_score or 0),
        }

    def index_product(self, product: Product) -> bool:
        """Add or update a single product. Safe to call from any thread."""
        if not self.available or product is None:
            return False
        try:
            doc = self._product_to_doc(product)
            self.client.index(INDEX_NAME).add_documents([doc])
            return True
        except Exception as e:
            logger.warning("Failed to index product %s in Meilisearch: %s", getattr(product, "id", "?"), e)
            return False

    def index_products_batch(self, products: list[Product]) -> int:
        """Batch-index many products (e.g. during full reindex). Returns count indexed."""
        if not self.available or not products:
            return 0
        try:
            docs = [self._product_to_doc(p) for p in products]
            self.client.index(INDEX_NAME).add_documents(docs)
            return len(docs)
        except Exception as e:
            logger.warning("Failed to batch-index %d products: %s", len(products), e)
            return 0

    def delete_product(self, product_id: int) -> bool:
        """Remove a product from the index."""
        if not self.available:
            return False
        try:
            self.client.index(INDEX_NAME).delete_document(str(product_id))
            return True
        except Exception:
            return False

    def reindex_all(self, db: Session, batch_size: int = 500) -> dict:
        """Full reindex: read all active products from DB, push to Meilisearch."""
        if not self.available:
            return {"success": False, "message": "Meilisearch לא זמין"}
        try:
            self.reset_index()
            total = db.query(Product).filter(Product.is_active == True).count()
            indexed = 0
            offset = 0
            while offset < total:
                batch = (
                    db.query(Product)
                    .filter(Product.is_active == True)
                    .order_by(Product.id)
                    .offset(offset)
                    .limit(batch_size)
                    .all()
                )
                if not batch:
                    break
                indexed += self.index_products_batch(batch)
                offset += batch_size
            return {"success": True, "total": total, "indexed": indexed}
        except Exception as e:
            logger.error("Full reindex failed: %s", e)
            return {"success": False, "message": str(e)}

    # ── Search ────────────────────────────────────────────────────

    def search(
        self,
        query: str = "",
        page: int = 1,
        hits_per_page: int = 24,
        category: str = "",
        source: str = "",
        min_price: float | None = None,
        max_price: float | None = None,
        min_rating: float | None = None,
        sort: str = "relevance",
        trending_only: bool = False,
        in_stock_only: bool = False,
    ) -> dict:
        """Execute a faceted, typo-tolerant search against Meilisearch.

        Returns a dict with 'hits', 'total', 'facets', 'processing_time_ms'.
        On failure or if Meilisearch is down, returns empty result (the caller
        falls back to SQL search)."""
        if not self.available:
            return {"hits": [], "total": 0, "facets": {}, "processing_time_ms": 0}

        # Build filter expression
        filters = []
        if category:
            filters.append(f"category = \"{category}\"")
        if source:
            filters.append(f"source_adapter = \"{source}\"")
        if min_price is not None:
            filters.append(f"price >= {min_price}")
        if max_price is not None:
            filters.append(f"price <= {max_price}")
        if min_rating is not None and min_rating > 0:
            filters.append(f"rating >= {min_rating}")
        if trending_only:
            filters.append("is_trending = true")
        if in_stock_only:
            filters.append("stock_count > 0")

        filter_expr = " AND ".join(filters) if filters else None

        # Sort mapping
        sort_options = {
            "relevance": None,
            "price_asc": ["price:asc"],
            "price_desc": ["price:desc"],
            "rating": ["rating:desc"],
            "newest": ["last_updated:desc"],
        }
        sort_by = sort_options.get(sort)

        try:
            result = self.client.index(INDEX_NAME).search(
                query or "",
                {
                    "page": page,
                    "hitsPerPage": hits_per_page,
                    "filter": filter_expr,
                    "sort": sort_by,
                    "facets": ["category", "source_adapter"],
                    "attributesToHighlight": ["name"],
                },
            )
            return {
                "hits": result.get("hits", []),
                "total": result.get("estimatedTotalHits", 0),
                "facets": result.get("facetDistribution", {}),
                "processing_time_ms": result.get("processingTimeMs", 0),
            }
        except Exception as e:
            logger.warning("Meilisearch search failed: %s", e)
            return {"hits": [], "total": 0, "facets": {}, "processing_time_ms": 0}


def _calc_discount(product: Product) -> float:
    """Calculate discount percentage from competitor/local market price."""
    if product.local_market_price and product.local_market_price > 0 and product.price:
        diff = product.local_market_price - product.price
        if diff > 0:
            return round((diff / product.local_market_price) * 100, 1)
    return 0.0


# ── Module-level singleton (same pattern as SemanticSearchService) ──

_meili = None
_meili_lock = None


def _get_service() -> MeiliSearchService:
    """Lazy singleton — connects once per process, never reconnects."""
    global _meili, _meili_lock
    if _meili is None:
        if _meili_lock is None:
            import threading
            _meili_lock = threading.Lock()
        with _meili_lock:
            if _meili is None:
                svc = MeiliSearchService()
                if svc.available:
                    svc.ensure_index()
                _meili = svc
    return _meili


def index_product(product: Product) -> bool:
    """Auto-index a single product. Safe to call from any thread/worker.
    Never raises — indexing is best-effort."""
    return _get_service().index_product(product) if product else False


def delete_product(product_id: int) -> bool:
    """Remove a product from the instant-search index."""
    return _get_service().delete_product(product_id) if product_id else False


def search_instant(**kwargs) -> dict:
    """Convenience: search via the singleton."""
    return _get_service().search(**kwargs)
