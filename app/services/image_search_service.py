"""
Visual (image) search — "חיפוש לפי תמונה".

Computes a 64-bit perceptual hash (dHash) of the uploaded image and compares
it against hashes of our catalog's product images. dHash only looks at
relative brightness of neighbouring pixels, so it tolerates resizing,
recompression and small crop differences — exactly the right property for
finding the same product sold with slightly different artwork.

Implementation notes:
  * Product-image hashes are computed lazily and cached in-process for an
    hour, so a repeated search does not re-download every image.
  * Every outbound download is capped at TIMEOUT seconds and failures just
    skip that product — search never blocks on a dead image host.
  * The comparison is capped to the most recent MAX_CATALOG products so the
    first search stays fast even as the catalog grows.
"""
import io
import logging
import threading
import time

import requests
from PIL import Image

from app.core.models import Product

logger = logging.getLogger(__name__)

_TIMEOUT = 4.0
_MAX_CATALOG = 120
_CACHE_TTL = 3600
_catalog_hashes: dict[str, tuple[int, float]] = {}  # url -> (dhash, fetched_at)
_lock = threading.Lock()


def _dhash(img) -> int:
    """64-bit difference hash: 9x8 grayscale, 1 bit per neighbouring pair."""
    gray = img.convert("L").resize((9, 8), Image.LANCZOS)
    px = list(gray.getdata())
    h = 0
    for row in range(8):
        for col in range(8):
            h = (h << 1) | (1 if px[row * 9 + col] > px[row * 9 + col + 1] else 0)
    return h


def _fetch_hash(url: str):
    now = time.time()
    with _lock:
        cached = _catalog_hashes.get(url)
        if cached and now - cached[1] < _CACHE_TTL:
            return cached[0]
    try:
        resp = requests.get(url, timeout=_TIMEOUT)
        if resp.status_code != 200:
            return None
        img = Image.open(io.BytesIO(resp.content)).convert("RGB")
        h = _dhash(img)
        with _lock:
            _catalog_hashes[url] = (h, now)
        return h
    except Exception as exc:
        logger.debug("image hash fetch failed for %s: %s", url, exc)
        return None


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def search_by_image(image_bytes: bytes, db, limit: int = 8, max_distance: int = 12) -> list[dict]:
    """Return catalog products visually similar to the uploaded image."""
    try:
        uploaded = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception:
        return []
    target = _dhash(uploaded)

    products = (
        db.query(Product)
        .filter(Product.is_active == True, Product.image_url.isnot(None))  # noqa: E712
        .order_by(Product.last_updated.desc())
        .limit(_MAX_CATALOG)
        .all()
    )
    scored = []
    for p in products:
        h = _fetch_hash(p.image_url)
        if h is None:
            continue
        d = _hamming(h, target)
        if d <= max_distance:
            scored.append((d, p))
    scored.sort(key=lambda x: x[0])
    return [
        {"id": p.id, "name": p.name, "price": p.price, "image_url": p.image_url}
        for _, p in scored[:limit]
    ]
