"""
Thin caching layer used to keep the homepage/product-listing queries fast
under high concurrent load (this is what actually lets a small Postgres
instance serve tens of thousands of concurrent readers — without it,
every single page view re-runs the full product query + enrichment
against the database, which is the first thing that falls over under
real traffic).

Falls back to a per-process in-memory dict if REDIS_URL isn't configured
or Redis is unreachable — so caching is a performance optimization that
degrades gracefully, never a hard dependency that can take the site down.
Note: the in-memory fallback is NOT shared across multiple app processes/
containers — fine for a single free-tier instance, but once you run
multiple web workers/instances you need real Redis for cache consistency.
"""
import json
import time
import logging

from app.core.config import settings

logger = logging.getLogger(__name__)

_memory_cache: dict[str, tuple[float, str]] = {}
_redis_client = None
_redis_checked = False


def _get_redis():
    global _redis_client, _redis_checked
    if _redis_checked:
        return _redis_client
    _redis_checked = True
    if not settings.redis_url:
        return None
    try:
        import redis
        client = redis.from_url(settings.redis_url, socket_connect_timeout=2)
        client.ping()
        _redis_client = client
        logger.info("Cache service connected to Redis")
    except Exception:
        logger.info("Redis not available — falling back to in-memory cache (single-process only)")
        _redis_client = None
    return _redis_client


def get(key: str):
    client = _get_redis()
    if client:
        try:
            raw = client.get(key)
            return json.loads(raw) if raw else None
        except Exception:
            return None

    entry = _memory_cache.get(key)
    if not entry:
        return None
    expires_at, raw = entry
    if time.time() > expires_at:
        _memory_cache.pop(key, None)
        return None
    return json.loads(raw)


def set(key: str, value, ttl_seconds: int = 60):
    raw = json.dumps(value)
    client = _get_redis()
    if client:
        try:
            client.setex(key, ttl_seconds, raw)
            return
        except Exception:
            pass
    _memory_cache[key] = (time.time() + ttl_seconds, raw)


def invalidate(key: str):
    client = _get_redis()
    if client:
        try:
            client.delete(key)
        except Exception:
            pass
    _memory_cache.pop(key, None)
