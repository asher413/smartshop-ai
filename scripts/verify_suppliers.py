"""
Full-supplier onboarding verification script.

Simulates the ENTIRE registration flow for every supplier adapter in one
run, the same way the admin "בדוק חיבור" button does — but end to end:

    for each supplier:
      1. key presence   — are the required API credentials configured?
      2. live key test  — call the supplier's real auth/verify endpoint
      3. real pull      — fetch_trending(limit=1): one REAL product
      4. affiliate link — build_affiliate_link() actually contains the
                          supplier's commission/tracking params?

Reports one line per supplier (✅ PASS / ❌ FAIL / ⏭️ SKIP), a summary
table, and exits non-zero when any *configured* supplier fails. Suppliers
with no credentials are reported as SKIP with the exact .env keys needed —
they never fail the run, so you can run this before you've set anything up.

The per-supplier rules (required keys, tracking markers, link checks) live
in app/services/supplier_verification.py — shared with the admin panel so
the CLI and the UI can never drift apart.

Usage:
    python scripts/verify_suppliers.py                # full flow (network)
    python scripts/verify_suppliers.py --dry-run      # key presence + link-template checks only, no network
    python scripts/verify_suppliers.py --supplier ebay
    python scripts/verify_suppliers.py --timeout 12 --json

Exit codes: 0 = all configured suppliers OK, 1 = at least one FAIL, 2 = usage/IO error.
"""
import argparse
import concurrent.futures
import json
import signal
import sys
import time
from pathlib import Path

# Windows consoles default to cp125x which can't encode Hebrew — force UTF-8.
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Make `app` importable regardless of CWD: this script lives in scripts/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.services.aggregator_service import ADAPTERS  # noqa: E402
from app.services import settings_service  # noqa: E402
from app.services.supplier_verification import (  # noqa: E402
    AFFILIATE_MARKERS,
    LINK_CRITICAL_ATTRS,
    PASS_THROUGH,
    REQUIRED_ATTRS,
    SERVICE_NAME,
    check_affiliate_link,
    env_for,
    missing_keys,
)

__all__ = [
    "AFFILIATE_MARKERS", "LINK_CRITICAL_ATTRS", "PASS_THROUGH", "REQUIRED_ATTRS",
    "SERVICE_NAME", "check_affiliate_link", "env_for", "missing_keys",
    "run_supplier", "main",
]


# --- Steps 2+3 with a hard wall-clock timeout ------------------------------
class _wall_clock_timeout:
    """POSIX SIGALRM-based timeout. On Windows (no SIGALRM) it's a no-op:
    the adapters carry their own internal request timeouts, so the pull can
    never hang forever either way.

    IMPORTANT: the pull MUST run in the main thread — the scraping adapters
    share one Playwright browser that crashes with "cannot switch to a
    different thread" if touched from a worker thread.
    """
    def __init__(self, seconds: int):
        self.seconds = int(seconds)
        self._old_handler = None

    def __enter__(self):
        if hasattr(signal, "SIGALRM"):
            def _timeout(*_):
                raise concurrent.futures.TimeoutError()
            self._old_handler = signal.signal(signal.SIGALRM, _timeout)
            signal.alarm(self.seconds)
        return self

    def __exit__(self, *exc):
        if hasattr(signal, "SIGALRM"):
            signal.alarm(0)
            if self._old_handler is not None:
                signal.signal(signal.SIGALRM, self._old_handler)


def _pull_one(supplier: str) -> tuple[bool, str]:
    """Run key test + real pull + link check for one supplier."""
    messages = []

    # Step 2 — live key test against the supplier's own endpoint.
    service = SERVICE_NAME.get(supplier)
    if service:
        ok, msg = settings_service.run_test(service, {})
        if not ok:
            return False, f"בדיקת מפתחות נכשלה: {msg}"
        messages.append(f"מפתחות תקינים ({msg.split('(')[0].strip()})")

    # Step 3 — one real product.
    adapter = ADAPTERS[supplier]()
    raw_items = adapter.fetch_trending(category=None, limit=1)
    if not raw_items:
        return False, "המשיכה לא החזירה מוצר אמיתי (0 תוצאות) — בדקו את חיפוש הקטגוריה / חסימה"
    product = raw_items[0]

    # Sanity: a real product has a name, a sane price, a URL.
    if not (product.name or "").strip():
        return False, "המוצר שנמשך ללא שם — משהו לא תקין בתשובת הספק"
    if not (product.price or 0) > 0:
        return False, f"מחיר המוצר שנמשך אינו תקין ({product.price}) — בדקו את הספק"
    if not str(product.url or "").startswith("http"):
        return False, f"URL המוצר שנמשך אינו תקין: {str(product.url)[:80]!r}"

    # Step 4 — commission link.
    link = adapter.build_affiliate_link(product.url)
    ok, link_msg = check_affiliate_link(supplier, product.url, link)
    if not ok:
        # Link-critical attrs (e.g. missing EBAY_CAMPAIGN_ID) produce a
        # tracking-less link — surface the exact env var to fill in.
        missing_critical = [a for a in LINK_CRITICAL_ATTRS.get(supplier, []) if not (getattr(settings, a, "") or "")]
        if missing_critical:
            hint = " — הזינו: " + ", ".join(env_for(a) for a in missing_critical)
        else:
            hint = ""
        return False, link_msg + hint

    detail = f"'{product.name[:48]}' | {product.currency or 'USD'} {product.price}"
    return True, " · ".join([detail] + messages + [link_msg])


def run_supplier(supplier: str, timeout: float, dry_run: bool = False) -> dict:
    start = time.time()
    missing = missing_keys(supplier)
    if missing:
        # Scraping-only suppliers (temu/bhphoto) never need keys — they're
        # "configured" by definition and go straight to the pull.
        if REQUIRED_ATTRS.get(supplier):
            keys = ", ".join(env_for(a) for a in missing)
            return {"supplier": supplier, "status": "SKIP",
                    "message": f"לא הוזנו מפתחות. נדרשים ב-.env / דף ההגדרות: {keys}",
                    "seconds": round(time.time() - start, 1)}
    # Dry-run: no network — verify only presence + that a link template can
    # be produced from a synthetic URL. AliExpress's official link.generate
    # is a network call, so pin it to the local template for the probe.
    if dry_run:
        probe = "https://example.com/item/12345"
        try:
            adapter = ADAPTERS[supplier]()
            if supplier == "aliexpress" and adapter.uses_official_api:
                adapter.uses_official_api = False
            link = adapter.build_affiliate_link(probe)
            ok, msg = check_affiliate_link(supplier, probe, link)
            return {"supplier": supplier, "status": "PASS" if ok else "FAIL",
                    "message": msg, "seconds": round(time.time() - start, 1)}
        except Exception as exc:
            return {"supplier": supplier, "status": "ERROR",
                    "message": f"בניית קישור עמלה נכשלה: {str(exc)[:140]}",
                    "seconds": round(time.time() - start, 1)}
    try:
        with _wall_clock_timeout(int(timeout)):
            ok, msg = _pull_one(supplier)
        return {"supplier": supplier, "status": "PASS" if ok else "FAIL",
                "message": msg, "seconds": round(time.time() - start, 1)}
    except concurrent.futures.TimeoutError:
        return {"supplier": supplier, "status": "TIMEOUT",
                "message": f"לא הושלם תוך {int(timeout)} שניות — הספק איטי/חסום",
                "seconds": round(time.time() - start, 1)}
    except Exception as exc:
        return {"supplier": supplier, "status": "ERROR",
                "message": f"שגיאה: {str(exc)[:140]}", "seconds": round(time.time() - start, 1)}


def main() -> int:
    parser = argparse.ArgumentParser(description="בדיקת זרימת הרשמת ספקים מלאה (מפתחות → משיכה → קישור עמלה)")
    parser.add_argument("--supplier", "-s", help="בדוק ספק אחד בלבד (למשל: ebay)")
    parser.add_argument("--timeout", type=int, default=45, help="timeout שניות לכל ספק (ברירת מחדל 45)")
    parser.add_argument("--dry-run", action="store_true", help="בלי רשת: רק נוכחות מפתחות + בדיקת תבנית קישור העמלה")
    parser.add_argument("--json", action="store_true", help="פלט JSON בלבד")
    args = parser.parse_args()

    suppliers = [args.supplier] if args.supplier else list(ADAPTERS.keys())
    unknown = [s for s in suppliers if s not in ADAPTERS]
    if unknown:
        print(f"ספק לא ידוע: {', '.join(unknown)}. קיימים: {', '.join(ADAPTERS)}", file=sys.stderr)
        return 2

    results = [run_supplier(s, float(args.timeout), dry_run=args.dry_run) for s in suppliers]

    if args.json:
        ok_all = all(r["status"] in ("PASS", "SKIP") for r in results)
        print(json.dumps({"results": results, "exit_code": 0 if ok_all else 1},
                         ensure_ascii=False, indent=2))
        return 0 if ok_all else 1

    # Human-readable report.
    print("\n" + "═" * 76)
    print("  אימות מלא של זרימת ההרשמה לספקים — מפתחות → משיכת מוצר → קישור עמלה")
    print("═" * 76)
    icons = {"PASS": "✅ PASS", "FAIL": "❌ FAIL", "SKIP": "⏭️  SKIP", "TIMEOUT": "⏱ TIMEOUT", "ERROR": "💥 ERROR"}
    for r in results:
        print(f"\n[{icons.get(r['status'], r['status'])}] {r['supplier']:<12} ({r['seconds']}s)")
        print(f"      {r['message']}")

    failed = [r for r in results if r["status"] not in ("PASS", "SKIP")]
    print("\n" + "─" * 76)
    if failed:
        print(f"סיכום: {len(failed)}/{len(results)} ספקים נכשלו — ראה למעלה. "
              f"(ספקים בלי מפתחות מוצגים כ-SKIP ואינם נכשלים.)")
        return 1
    print(f"סיכום: כל {len(results)} הספקים תקינים ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
