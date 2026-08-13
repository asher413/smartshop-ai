"""
Image Proxy Service — fetch external product images, convert to WebP/AVIF, cache on disk.

Flow:
  Request → hashed disk cache? → YES → serve cached image
                                → NO  → fetch original (8s timeout)
                                       → convert to best format (AVIF > WebP)
                                       → save to disk cache
                                       → serve

Content negotiation (Vary: Accept):
  - Browsers that send image/avif in Accept → get AVIF (~30% smaller than WebP)
  - Everyone else → get WebP

Only allows images from known product-image CDNs (ali/amazon/ebay/temu/etc.) —
random URLs return 400. Max source file size 8MB.

Disk cache lives under app/static/img_cache/ so it survives deploys and gets
picked up by any CDN sitting in front of the server.
"""
import base64
import hashlib
import logging
import os
import re
import time
from io import BytesIO
from urllib.parse import urlparse

import requests
from PIL import Image

logger = logging.getLogger(__name__)

# Only proxy images from known product-image hosts. This prevents the proxy
# from being abused as an open redirect / SSRF vector.
ALLOWED_HOSTS = (
    "aliexpress.com", "alicdn.com", "aliexpress-media.com",
    "amazon.com", "images-amazon.com", "ssl-images-amazon.com",
    "media-amazon.com",
    "ebay.com", "ebayimg.com", "ebaystatic.com",
    "temu.com", "img.kwcdn.com",
    "shein.com", "shein.com.mx",
    "walmart.com", "walmartimages.com",
    "bestbuy.com",
    "bhphotovideo.com", "bhphoto.com",
    "awin.com",
    "cj.com",
    "rakuten.com", "linksynergy.com",
    "cdn.shopify.com",
    "placehold.co",  # our own placeholder generator
)

# Local cache directory — inside static/ so it lives through deploys and is
# directly served if a reverse proxy/CDN sits in front.
CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "static", "img_cache")
MAX_SOURCE_BYTES = 8 * 1024 * 1024  # 8 MB — anything bigger is already too heavy
WEBP_QUALITY = 82   # good balance: ~4-5x smaller than JPEG at same perceived quality
AVIF_QUALITY = 55   # AVIF is more efficient at lower quality — similar visual result as WebP 82
FETCH_TIMEOUT = 8.0  # seconds — drop slow origins instead of hogging workers
CACHE_MAX_AGE = 86400 * 7  # 7 days


def _ensure_cache_dir():
    os.makedirs(CACHE_DIR, exist_ok=True)


def _cache_path(source_url: str, fmt: str = "webp", width: int = 0) -> str:
    """Deterministic cache filename from source URL + format + width."""
    key = source_url + "|" + fmt + ("|w" + str(width) if width else "")
    h = hashlib.sha256(key.encode()).hexdigest()[:32]
    return os.path.join(CACHE_DIR, f"{h}.{fmt}")


def _is_allowed_url(url: str) -> bool:
    """Block requests to non-product-image hosts."""
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return False
    return any(host.endswith(allowed) or host == allowed.lstrip(".") for allowed in ALLOWED_HOSTS)


def _wants_avif(accept_header: str) -> bool:
    """Check if the client's Accept header includes image/avif."""
    if not accept_header:
        return False
    return "image/avif" in accept_header.lower()


def _convert_image(img: Image.Image, fmt: str, target_width: int = 0) -> bytes:
    """Convert a PIL image to the requested format (webp or avif).
    If target_width > 0, resize the image to that width before converting."""
    # Ensure usable mode — RGBA only if the source actually has transparency
    if img.mode in ("RGBA", "P", "LA"):
        if img.mode == "P":
            img = img.convert("RGBA")
        has_alpha = (
            img.mode == "RGBA"
            and any(p[3] < 255 for p in img.getdata() if isinstance(p, tuple) and len(p) == 4)
        )
        if not has_alpha:
            img = img.convert("RGB")
    elif img.mode not in ("RGB",):
        img = img.convert("RGB")

    # Resize to target width if requested (maintains aspect ratio)
    if target_width and target_width > 0 and img.width > target_width:
        ratio = target_width / img.width
        new_height = int(img.height * ratio)
        img = img.resize((target_width, new_height), Image.LANCZOS)

    # Limit max dimension to 1200px
    max_dim = 1200
    if img.width > max_dim or img.height > max_dim:
        ratio = max_dim / max(img.width, img.height)
        new_size = (int(img.width * ratio), int(img.height * ratio))
        img = img.resize(new_size, Image.LANCZOS)

    out = BytesIO()
    if fmt == "avif":
        img.save(out, format="AVIF", quality=AVIF_QUALITY)
    else:
        img.save(out, format="WEBP", quality=WEBP_QUALITY, method=4)
    return out.getvalue()


def get_or_convert(source_url: str, accept_header: str = "", target_width: int = 0) -> tuple[bytes, str]:
    """Return (image_bytes, content_type) for a source image URL.

    Content negotiation:
      - If the client's Accept header includes image/avif → return AVIF
      - Otherwise → return WebP

    target_width: if > 0, resize the image to this width before converting.
    Each (url, format, width) combination is cached separately.

    Uses disk cache keyed by source URL + format + width. If the cached
    file is fresh (under CACHE_MAX_AGE old), serve it directly.

    Raises ValueError if the URL is not allowed or the source is unreadable.
    """
    if not _is_allowed_url(source_url):
        raise ValueError(f"Image proxy: host not allowed for {source_url[:120]}")

    _ensure_cache_dir()

    use_avif = _wants_avif(accept_header)
    fmt = "avif" if use_avif else "webp"
    mime = "image/avif" if use_avif else "image/webp"
    width = max(target_width, 0) if target_width else 0

    cache_file = _cache_path(source_url, fmt, width)

    # Hit: serve cached image if fresh enough
    if os.path.exists(cache_file):
        age = time.time() - os.path.getmtime(cache_file)
        if age < CACHE_MAX_AGE:
            with open(cache_file, "rb") as f:
                return f.read(), mime
        try:
            os.remove(cache_file)
        except OSError:
            pass

    # Fetch original
    try:
        resp = requests.get(source_url, timeout=FETCH_TIMEOUT, stream=True,
                           headers={"User-Agent": "DealBursa-ImageProxy/1.0"})
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning("Image proxy fetch failed for %s: %s", source_url[:120], e)
        raise ValueError(f"Failed to fetch source image: {e}")

    raw = b""
    for chunk in resp.iter_content(chunk_size=65536):
        raw += chunk
        if len(raw) > MAX_SOURCE_BYTES:
            raise ValueError("Source image exceeds 8MB limit")

    if not raw:
        raise ValueError("Empty source image")

    try:
        img = Image.open(BytesIO(raw))
        converted = _convert_image(img, fmt, width)
    except Exception as e:
        logger.error("%s conversion failed for %s: %s", fmt.upper(), source_url[:120], e)
        raise ValueError(f"Image conversion failed: {e}")

    try:
        with open(cache_file, "wb") as f:
            f.write(converted)
    except OSError as e:
        logger.warning("Could not write cache file %s: %s", cache_file, e)

    logger.info("Image proxy: %s w=%d → %s %d bytes (%.1fx smaller)",
                source_url[:80], width, fmt.upper(), len(converted),
                len(raw) / max(len(converted), 1))

    return converted, mime


def cache_stats() -> dict:
    """Return cache directory stats for admin dashboard."""
    _ensure_cache_dir()
    files = [f for f in os.listdir(CACHE_DIR) if f.endswith((".webp", ".avif"))]
    total = sum(os.path.getsize(os.path.join(CACHE_DIR, f)) for f in files)
    webp = sum(1 for f in files if f.endswith(".webp"))
    avif = sum(1 for f in files if f.endswith(".avif"))
    return {
        "files": len(files),
        "webp_files": webp,
        "avif_files": avif,
        "total_bytes": total,
        "total_mb": round(total / (1024 * 1024), 2),
        "cache_dir": CACHE_DIR,
    }


def clear_cache() -> dict:
    """Delete all cached WebP/AVIF images. Returns the count and freed space."""
    _ensure_cache_dir()
    files = [f for f in os.listdir(CACHE_DIR) if f.endswith((".webp", ".avif"))]
    total_bytes = sum(os.path.getsize(os.path.join(CACHE_DIR, f)) for f in files)
    removed = 0
    failed = 0
    for f in files:
        try:
            os.remove(os.path.join(CACHE_DIR, f))
            removed += 1
        except OSError as e:
            logger.warning("Could not delete cache file %s: %s", f, e)
            failed += 1
    logger.info("Image cache cleared: %d files removed, %d failed, %.2f MB freed",
                removed, failed, total_bytes / (1024 * 1024))
    return {
        "removed": removed,
        "failed": failed,
        "freed_mb": round(total_bytes / (1024 * 1024), 2),
        "cache_dir": CACHE_DIR,
    }


def warm_cache(image_urls: list[str], max_workers: int = 4) -> dict:
    """Pre-fetch and convert a batch of image URLs into BOTH WebP and AVIF.

    Skips URLs that are already cached and fresh. Runs fetches in a thread
    pool so slow origins don't block the whole batch. Catches per-URL
    failures so one bad image doesn't kill the whole run.

    Returns a summary: total URLs, how many were already cached, how many
    were freshly converted, and how many failed.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    _ensure_cache_dir()
    unique_urls = list(dict.fromkeys(u for u in image_urls if u and u.startswith("http") and _is_allowed_url(u)))

    if not unique_urls:
        return {"total": 0, "already": 0, "converted": 0, "failed": 0, "formats": ["webp", "avif"]}

    already = 0
    to_fetch: list[str] = []

    # Check what's already cached (fresh) for BOTH formats.
    for url in unique_urls:
        webp_path = _cache_path(url, "webp")
        avif_path = _cache_path(url, "avif")
        webp_fresh = os.path.exists(webp_path) and (time.time() - os.path.getmtime(webp_path) < CACHE_MAX_AGE)
        avif_fresh = os.path.exists(avif_path) and (time.time() - os.path.getmtime(avif_path) < CACHE_MAX_AGE)
        if webp_fresh and avif_fresh:
            already += 1
        else:
            to_fetch.append(url)

    converted = 0
    failed = 0

    def _fetch_one(url: str) -> bool:
        """Fetch one URL and convert to BOTH formats. Returns True if at least one succeeded."""
        ok = False
        # Fetch the source once, convert twice (to avoid double network cost).
        try:
            resp = requests.get(url, timeout=FETCH_TIMEOUT, stream=True,
                               headers={"User-Agent": "DealBursa-ImageProxy/2.0"})
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.warning("Warm-cache fetch failed for %s: %s", url[:120], e)
            return False

        raw = b""
        for chunk in resp.iter_content(chunk_size=65536):
            raw += chunk
            if len(raw) > MAX_SOURCE_BYTES:
                break

        if not raw:
            return False

        try:
            img = Image.open(BytesIO(raw))
        except Exception as e:
            logger.warning("Warm-cache open failed for %s: %s", url[:120], e)
            return False

        for fmt in ("webp", "avif"):
            cache_file = _cache_path(url, fmt)
            # Skip if already fresh (another thread may have finished it).
            if os.path.exists(cache_file) and (time.time() - os.path.getmtime(cache_file) < CACHE_MAX_AGE):
                ok = True
                continue
            try:
                converted_bytes = _convert_image(img.copy(), fmt)
                with open(cache_file, "wb") as f:
                    f.write(converted_bytes)
                ok = True
            except Exception as e:
                logger.warning("Warm-cache %s conversion failed for %s: %s", fmt, url[:120], e)

        return ok

    if to_fetch:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_fetch_one, url): url for url in to_fetch}
            for i, future in enumerate(as_completed(futures)):
                if future.result():
                    converted += 1
                else:
                    failed += 1

    logger.info("Image cache warmed: %d total, %d already fresh, %d converted, %d failed",
                len(unique_urls), already, converted, failed)

    return {
        "total": len(unique_urls),
        "already": already,
        "converted": converted,
        "failed": failed,
        "formats": ["webp", "avif"],
    }
