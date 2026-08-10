"""Vector search over the product catalog, used by the chatbot and the
personal-area recommender for intent-aware lookups (e.g. 'משהו למטבח מתחת ל-200').

chromadb is imported lazily (not at module import time) because chromadb 0.5.5
is incompatible with numpy>=2.0 (np.float_ was removed) — a hard import would
crash the entire app at startup. If chromadb is missing/broken the service
degrades to a no-op so search falls back to the keyword path instead of
taking the whole site down.
"""
import logging
import re

logger = logging.getLogger(__name__)


class SemanticSearchService:
    def __init__(self, persist_path: str = "./chroma_db"):
        self._collection = None
        # Guard BEFORE touching chromadb: numpy>=2.0 breaks chromadb 0.5.5 at
        # import time (np.float_ was removed), and initializing chromadb can
        # also trigger network calls (model download/telemetry) that hang on
        # restricted networks. Skipping early keeps the app fast and boot-safe.
        try:
            import numpy as _np
            numpy_ok = int(_np.__version__.split(".")[0]) < 2
        except Exception:
            numpy_ok = True
        if not numpy_ok:
            logger.warning(
                "Semantic search disabled: numpy>=2 is incompatible with "
                "chromadb 0.5.5. Fix with: pip install 'numpy<2'"
            )
            return
        try:
            import os as _os
            _os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
            import chromadb
            from chromadb.utils import embedding_functions
            # Local, offline embedding (all-MiniLM-L6-v2 via onnxruntime).
            client = chromadb.PersistentClient(path=persist_path)
            embedding_fn = embedding_functions.DefaultEmbeddingFunction()
            self._collection = client.get_or_create_collection(
                name="store_products",
                embedding_function=embedding_fn,
            )
        except Exception:
            logger.warning(
                "Semantic search disabled: chromadb unavailable. "
                "Keyword search will be used. "
                "Fix with: pip install 'numpy<2'"
            )

    @property
    def available(self) -> bool:
        return self._collection is not None

    def add_product_to_index(self, product):
        if not self.available:
            return
        try:
            self._collection.upsert(
                documents=[f"{product.name} {product.description} {product.category}"],
                metadatas=[{"id": product.id, "price": product.price}],
                ids=[str(product.id)],
            )
        except Exception:
            logger.warning("Failed to index product %s", product.id)

    def remove_product_from_index(self, product_id: int):
        if not self.available:
            return
        try:
            self._collection.delete(ids=[str(product_id)])
        except Exception:
            pass

    def search_intent(self, user_query: str) -> list[str]:
        if not self.available:
            return []
        max_price = None
        budget_match = re.search(r"(?:מתחת ל|עד)\s*(\d+)", user_query)
        if budget_match:
            try:
                max_price = float(budget_match.group(1))
            except ValueError:
                max_price = None

        try:
            results = self._collection.query(query_texts=[user_query], n_results=5)
        except Exception:
            logger.warning("Vector query failed, returning empty", exc_info=True)
            return []
        ids = results.get("ids", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]

        if max_price is None:
            return ids

        filtered_ids = []
        for idx, pid in enumerate(ids):
            metadata = metadatas[idx] if idx < len(metadatas) else {}
            price = metadata.get("price") if isinstance(metadata, dict) else None
            try:
                if price is not None and float(price) <= max_price:
                    filtered_ids.append(pid)
            except (TypeError, ValueError):
                continue
        return filtered_ids or ids


# --- Auto-indexing helpers ------------------------------------------------
# The discovery worker and the interest-pull service both create Products
# outside the web request path (background threads / cron). Every new or
# changed product must land in the vector index automatically — no manual
# reindex run. These module-level helpers hold ONE lazy shared service so a
# thread doesn't reopen the chromadb PersistentClient on every product.
# They never raise: indexing is best-effort and must never take down the
# discovery/pull pipeline (chromadb may be disabled entirely — the service
# degrades to a no-op when numpy>=2 or chromadb is missing).

_indexer = None
_indexer_lock = None


def _get_indexer() -> "SemanticSearchService | None":
    """Lazy shared indexer instance, created once per process."""
    global _indexer, _indexer_lock
    if _indexer is None:
        if _indexer_lock is None:
            import threading
            _indexer_lock = threading.Lock()
        with _indexer_lock:
            if _indexer is None:
                try:
                    _indexer = SemanticSearchService()
                except Exception:
                    logger.warning("Semantic indexer init failed — auto-indexing disabled")
                    _indexer = None
    return _indexer


def index_product(product) -> bool:
    """Add or refresh a product in the semantic index (upsert by id).
    Returns True if indexed, False if unavailable or failed. Safe to call
    from any thread; never raises."""
    svc = _get_indexer()
    if svc is None or not svc.available or product is None:
        return False
    try:
        svc.add_product_to_index(product)
        return True
    except Exception:
        logger.warning("Auto-index failed for product %s", getattr(product, "id", "?"))
        return False


def remove_product_from_index(product_id) -> bool:
    """Remove a product from the semantic index (used when a product is
    deactivated / deleted). Best-effort, never raises."""
    svc = _get_indexer()
    if svc is None or not svc.available or product_id is None:
        return False
    try:
        svc.remove_product_from_index(product_id)
        return True
    except Exception:
        logger.warning("Remove-from-index failed for product %s", product_id)
        return False
