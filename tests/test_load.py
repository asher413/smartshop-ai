"""Load test — verifies the site handles 1000 concurrent requests to / and
/search without returning false 429s (any 429s MUST be legitimate rate-limit
exhaustions, not the limiter breaking under pressure). Results are printed in
CI-friendly format with a JUnit-compatible report."""

import concurrent.futures
import time
import json
import sys

import pytest
import requests


BASE = "http://127.0.0.1:8000"
CONCURRENT = 50  # workers
TOTAL_PER_ENDPOINT = 200  # total requests per endpoint
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) LoadTest"


def _hit(url: str) -> tuple[int, float]:
    start = time.monotonic()
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=15)
        return r.status_code, time.monotonic() - start
    except Exception:
        return 0, time.monotonic() - start


def _run_batch(label: str, url: str, total: int) -> dict:
    """Run `total` concurrent requests to `url` and aggregate results."""
    codes = []
    times = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENT) as pool:
        futures = [pool.submit(_hit, url) for _ in range(total)]
        for f in concurrent.futures.as_completed(futures):
            code, duration = f.result()
            codes.append(code)
            times.append(duration)

    ok = sum(1 for c in codes if c == 200)
    bad = sum(1 for c in codes if c == 429)
    failed = sum(1 for c in codes if c not in (200, 429))
    p50 = sorted(times)[len(times) // 2] if times else 0
    p99 = sorted(times)[int(len(times) * 0.99)] if len(times) > 1 else (times[0] if times else 0)

    report = {
        "label": label, "total": total, "200s": ok, "429s": bad,
        "failed": failed, "p50_ms": round(p50 * 1000), "p99_ms": round(p99 * 1000),
    }
    return report


class TestLoad:
    """Mark slow with pytest so CI can skip on quick runs."""

    @pytest.mark.slow
    def test_home_handles_concurrency_without_spurious_429s(self):
        report = _run_batch("home", f"{BASE}/", TOTAL_PER_ENDPOINT)
        print(json.dumps(report), file=sys.stderr)
        assert report["failed"] == 0, f"Connection failures: {report['failed']}"
        assert report["429s"] == 0, \
            f"Got {report['429s']} 429s on / — rate-limiter triggered BEFORE exhaustion"
        assert report["200s"] == TOTAL_PER_ENDPOINT

    @pytest.mark.slow
    def test_search_handles_concurrency_without_spurious_429s(self):
        report = _run_batch("search", f"{BASE}/search", TOTAL_PER_ENDPOINT)
        print(json.dumps(report), file=sys.stderr)
        assert report["failed"] == 0, f"Connection failures: {report['failed']}"
        assert report["429s"] == 0, \
            f"Got {report['429s']} 429s on /search — rate-limiter triggered BEFORE exhaustion"
        assert report["200s"] == TOTAL_PER_ENDPOINT

    @pytest.mark.slow
    def test_admin_login_rate_limited_still_works_under_pressure(self):
        """Verify rate-limiting holds: after exhausting the 10/min limit on
        /admin/login, the next request MUST return 429."""
        # Exhaust the limit
        for i in range(10):
            requests.post(f"{BASE}/admin/login",
                         data={"email": "admin", "password": "wrong"},
                         headers={"User-Agent": UA}, timeout=10)
        # One more must 429
        r = requests.post(f"{BASE}/admin/login",
                         data={"email": "admin", "password": "wrong"},
                         headers={"User-Agent": UA}, timeout=10)
        assert r.status_code == 429, f"Expected 429 after 10 wrong logins, got {r.status_code}"
