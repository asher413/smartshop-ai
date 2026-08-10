"""
Load test for the SmartShop AI server.

1. Concurrency test: fire N parallel requests at / (home), /search, and
   /api/price-war/<id> — measure avg / p95 / max response times.
2. Rate-limit test: hammer /api/newsletter (5/min) and /api/chat (15/min)
   and verify the limiter returns 429 once the quota is exhausted.

Usage:
    python scripts/loadtest_runner.py [base_url] [concurrency]
"""
import sys
import time
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

# Defaults (overridable via argv only when run directly, not when pytest
# imports this module for collection).
BASE = "http://127.0.0.1:8000"
N = 50

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36"}
HEADERS = {**UA, "Accept": "text/html,application/json"}


def timed_get(url):
    t0 = time.perf_counter()
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        return r.status_code, time.perf_counter() - t0, r.elapsed.total_seconds()
    except Exception as e:
        return f"ERR:{type(e).__name__}", time.perf_counter() - t0, -1


def report(name, results):
    codes = [r[0] for r in results]
    ok = [r[2] for r in results if isinstance(r[0], int) and r[0] < 500 and r[2] >= 0]
    if not ok:
        print(f"[{name}] ALL FAILED: {codes[:5]}...")
        return
    avg = statistics.mean(ok)
    p95 = sorted(ok)[int(len(ok) * 0.95) - 1]
    mx = max(ok)
    from collections import Counter
    dist = Counter(str(c) for c in codes)
    print(f"[{name}] n={len(results)} avg={avg*1000:.0f}ms p95={p95*1000:.0f}ms max={mx*1000:.0f}ms | codes={dict(dist)}")


def run_concurrency():
    print(f"=== Concurrency test: {N} parallel x 3 endpoints on {BASE} ===")
    urls = {
        "home": f"{BASE}/",
        "search": f"{BASE}/search?q=%D7%90%D7%95%D7%96%D7%A0%D7%99%D7%95%D7%AA",
        "price-war": f"{BASE}/api/price-war/1",
    }
    for name, url in urls.items():
        with ThreadPoolExecutor(max_workers=N) as pool:
            futures = [pool.submit(timed_get, url) for _ in range(N)]
            results = [f.result() for f in as_completed(futures)]
        report(name, results)


def _admin_login():
    """Log in as admin once (reads .env creds, never prints them) and return
    (session, csrf_token_from_settings_page)."""
    import re
    s = requests.Session()
    s.headers.update(UA)
    env_text = open(".env", encoding="utf-8").read()
    email = re.search(r"^ADMIN_EMAIL=(.*)$", env_text, re.M).group(1).strip()
    secret = re.search(r"^ADMIN_SECRET_KEY=(.*)$", env_text, re.M).group(1).strip()
    # The per-IP limiter caps /admin/login at 10/minute — if previous test
    # runs already burned the window, retry until it resets (max ~75s).
    import time as _time
    r = None
    for _ in range(16):
        r = s.post(f"{BASE}/admin/login", data={"email": email, "password": secret}, allow_redirects=False)
        if r.status_code != 429:
            break
        _time.sleep(5)
    assert r is not None and r.status_code == 303, f"admin login failed: {r.status_code if r else 'no response'}"
    page = s.get(f"{BASE}/admin/settings")
    # The settings page embeds the signed token in a hidden input:
    # <input type="hidden" name="csrf_token" value="...">
    m = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    tok = m.group(1) if m else ""
    return s, tok


def run_admin_pressure():
    """50-100 parallel requests against /admin/settings and protected POST
    routes. Verifies CSRF does NOT fall under pressure: every request
    without a token/Origin must be 403, every request WITH the token must
    reach the handler (never 401/403). Uses read-only or no-side-effect
    endpoints only — no real settings saves or supplier pulls."""
    print(f"\n=== Admin pressure: {N} parallel on settings + protected routes ===")
    s, tok = _admin_login()
    print(f"admin session ok, csrf token on page: {bool(tok)}")

    def get_settings():
        r = s.get(f"{BASE}/admin/settings", timeout=20)
        return r.status_code, 0, r.elapsed.total_seconds()

    def get_suppliers_api():
        r = s.get(f"{BASE}/admin/suppliers/api", timeout=20)
        return r.status_code, 0, r.elapsed.total_seconds()

    def post_no_csrf():
        # Authenticated admin session, but NO Origin/token -> must 403 every time.
        r = s.post(f"{BASE}/admin/users/99999/toggle-active", timeout=20)
        return r.status_code, 0, r.elapsed.total_seconds()

    def post_with_csrf():
        # Valid token -> must REACH the handler (404 here; never 401/403).
        r = s.post(f"{BASE}/admin/users/99999/toggle-active",
                   headers={"X-CSRF-Token": tok}, timeout=20)
        return r.status_code, 0, r.elapsed.total_seconds()

    for name, fn in (("GET /admin/settings", get_settings),
                     ("GET /admin/suppliers/api", get_suppliers_api),
                     ("POST protected NO-csrf (expect 403)", post_no_csrf),
                     ("POST protected WITH csrf (expect handler)", post_with_csrf)):
        with ThreadPoolExecutor(max_workers=N) as pool:
            futures = [pool.submit(fn) for _ in range(N)]
            results = [f.result() for f in as_completed(futures)]
        report(name, results)
        codes = [r[0] for r in results]
        if "NO-csrf" in name:
            print(f"    -> all 403 under pressure = {'PASS' if codes and all(c == 403 for c in codes) else 'FAIL'}")
        elif "WITH csrf" in name:
            print(f"    -> none 401/403 = {'PASS' if codes and all(c not in (401, 403) for c in codes) else 'FAIL'}")


def run_rate_limit():
    print("\n=== Rate-limit verification (per-IP limiter) ===")
    # /api/newsletter is limited to 5/minute — the 6th within a minute must 429.
    codes = []
    for i in range(7):
        r = requests.post(f"{BASE}/api/newsletter", data={"email": f"load{i}@test.local"}, headers=HEADERS, timeout=15)
        codes.append(r.status_code)
    print(f"/api/newsletter (limit 5/min): {codes}  -> 429 on burst = {'PASS' if 429 in codes else 'FAIL'}")
    # /api/chat limited to 15/minute.
    codes = []
    for i in range(18):
        r = requests.post(f"{BASE}/api/chat", data={"query": "שלום", "mode": "standard"}, headers=HEADERS, timeout=20)
        codes.append(r.status_code)
    print(f"/api/chat (limit 15/min): {codes}  -> 429 on burst = {'PASS' if 429 in codes else 'FAIL'}")
    # /admin/login limited to 10/minute.
    codes = []
    for i in range(12):
        r = requests.post(f"{BASE}/admin/login", data={"email": "a@b.c", "password": "x"}, headers=HEADERS, timeout=15)
        codes.append(r.status_code)
    print(f"/admin/login (limit 10/min): {codes}  -> 429 on burst = {'PASS' if 429 in codes else 'FAIL'}")


if __name__ == "__main__":
    # Module scope here, so a plain assignment updates the module-level
    # defaults above (no `global` statement — it's invalid after a prior
    # module-level assignment).
    BASE = sys.argv[1] if len(sys.argv) > 1 else BASE
    try:
        N = int(sys.argv[2]) if len(sys.argv) > 2 else N
    except ValueError:
        N = N
    run_admin_pressure()  # admin login first (10/min quota shared with run_rate_limit)
    run_concurrency()
    run_rate_limit()
    print("\nDone.")
