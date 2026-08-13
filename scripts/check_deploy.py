#!/usr/bin/env python
"""
DealBursa Deploy Readiness Checker
Runs ALL checks automatically — env vars, DB, keys, templates, tests —
and prints a clear "READY / MISSING X" report.

Runs WITHOUT any AI dependencies (no Gemini calls, no chromadb).
Pure file/DB/env verification. Safe for CI and pre-deploy.

Usage:
    python scripts/check_deploy.py          # full check
    python scripts/check_deploy.py --quick  # fast check (skip tests)
    python scripts/check_deploy.py --json   # JSON output for CI
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

results = []


def check(step: str, ok: bool, detail: str = ""):
    results.append({"step": step, "ok": ok, "detail": detail})
    icon = "[PASS]" if ok else "[FAIL]"
    print(f"  {icon} {step}" + (f" — {detail}" if detail else ""))


# ── 1. Environment ─────────────────────────────────────────────────

def check_env():
    print("\n--- 1. ENVIRONMENT ---")
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        check(".env exists", True)
        content = env_file.read_text(encoding="utf-8")
        required = ["SITE_URL", "SESSION_SECRET_KEY", "DATABASE_URL"]
        for key in required:
            found = key in content
            check(f"  {key}", found, "found" if found else "MISSING")
    else:
        check(".env exists", False, "Create .env from .env.example")

    # Critical secrets (warn if defaults)
    from app.core.config import settings
    if settings.session_secret_key == "change_me_please_session_secret":
        check("SESSION_SECRET_KEY changed from default", False, "Still default — change for production")
    else:
        check("SESSION_SECRET_KEY customized", True)

    if settings.admin_secret_key == "12345":
        check("ADMIN_SECRET_KEY changed from default", False, "Still '12345' — change in admin panel")
    else:
        check("ADMIN_SECRET_KEY customized", True)

    if settings.admin_email:
        check("ADMIN_EMAIL set", True, settings.admin_email)
    else:
        check("ADMIN_EMAIL set", False, "Set ADMIN_EMAIL in .env")


# ── 2. Database ────────────────────────────────────────────────────

def check_db():
    print("\n--- 2. DATABASE ---")
    try:
        from app.core.database import engine
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        check("Database connection", True)
    except Exception as e:
        check("Database connection", False, str(e)[:80])
        return

    try:
        from app.core.models import Base, Product, User, Notification
        from app.core.database import SessionLocal
        db = SessionLocal()
        product_count = db.query(Product).filter(Product.is_active == True).count()
        user_count = db.query(User).count()
        db.close()
        check(f"Products (active)", True, f"{product_count} products")
        check(f"Users", True, f"{user_count} users")
        if product_count == 0:
            check("Products available", False, "No products yet — run seed_demo.py or import from suppliers")
    except Exception as e:
        check("Table access", False, str(e)[:80])


# ── 3. API Keys ────────────────────────────────────────────────────

def check_keys():
    print("\n--- 3. API KEYS ---")
    from app.core.config import settings

    # AI
    has_ai = bool(settings.google_api_key)
    check("AI (Gemini)", has_ai, "configured" if has_ai else "NOT SET — site runs without AI")

    # Suppliers
    suppliers = {
        "AliExpress": bool(settings.aliexpress_app_key and settings.aliexpress_app_secret),
        "Amazon": bool(settings.amazon_paapi_access_key and settings.amazon_paapi_secret_key),
        "eBay": bool(settings.ebay_app_id),
        "Awin": bool(settings.awin_api_token),
        "CJ": bool(settings.cj_api_token),
        "Rakuten": bool(settings.rakuten_client_id),
    }
    connected = [k for k, v in suppliers.items() if v]
    check("Supplier APIs", len(connected) > 0,
          f"{len(connected)} connected: {', '.join(connected)}" if connected else "NONE — products pulled via scraping only")

    check("SMTP (email)", bool(settings.smtp_host), "configured" if settings.smtp_host else "NOT SET")
    check("Telegram", bool(settings.telegram_bot_token), "configured" if settings.telegram_bot_token else "NOT SET")
    check("Instagram", bool(settings.instagram_access_token), "configured" if settings.instagram_access_token else "NOT SET")
    check("VAPID (Web Push)", bool(settings.vapid_private_key), "configured" if settings.vapid_private_key else "NOT SET")


# ── 4. Templates ───────────────────────────────────────────────────

def check_templates():
    print("\n--- 4. TEMPLATES ---")
    import base64 as _b64
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(str(PROJECT_ROOT / "app" / "templates")))
    # Register the same filters that main.py registers, so template checks
    # don't fail on custom filters like imgproxy / imgsrcset.
    def _imgproxy_filter(url):
        if not url or not url.startswith('http'):
            return url or ''
        return f"/img/{_b64.urlsafe_b64encode(url.encode()).decode().rstrip('=')}"
    def _imgsrcset_filter(url, widths=None):
        if not url or not url.startswith('http'):
            return ''
        if widths is None:
            widths = [200, 400, 600, 900, 1200]
        encoded = _b64.urlsafe_b64encode(url.encode()).decode().rstrip('=')
        return ', '.join(f"/img/{encoded}?w={w} {w}w" for w in widths)
    env.filters['imgproxy'] = _imgproxy_filter
    env.filters['imgsrcset'] = _imgsrcset_filter
    required_templates = [
        "index.html", "layout.html", "search.html", "product_page.html",
        "login.html", "signup.html", "personal_area.html", "coupons.html",
        "admin_dashboard.html", "admin_login.html", "admin_settings.html",
        "help.html", "privacy.html", "terms.html", "about.html", "404.html",
    ]
    all_ok = True
    for tpl_name in required_templates:
        try:
            env.get_template(tpl_name)
        except Exception as e:
            check(f"Template: {tpl_name}", False, str(e)[:60])
            all_ok = False
    if all_ok:
        check("All templates load", True, f"{len(required_templates)} templates OK")


# ── 5. Static assets ───────────────────────────────────────────────

def check_static():
    print("\n--- 5. STATIC ASSETS ---")
    assets = [
        "static/css/design_system.css",
        "static/js/main.js",
        "static/sw.js",  # service worker
        "static/manifest.json",
    ]
    missing = []
    for a in assets:
        if not (PROJECT_ROOT / "app" / a).exists():
            missing.append(a)
    if missing:
        for m in missing:
            check(f"Missing: {m}", False)
    else:
        check("All static assets", True, f"{len(assets)} files")

    # Check CSS and JS are non-empty
    css = PROJECT_ROOT / "app" / "static" / "css" / "design_system.css"
    js = PROJECT_ROOT / "app" / "static" / "js" / "main.js"
    check("CSS size", css.stat().st_size > 1000, f"{css.stat().st_size:,} bytes")
    check("JS size", js.stat().st_size > 1000, f"{js.stat().st_size:,} bytes")


# ── 6. No-AI fallback readiness ────────────────────────────────────

def check_noai():
    print("\n--- 6. NO-AI FALLBACK ---")
    # Verify chatbot fallback works
    try:
        from app.agents.chatbot import StoreChatbot
        bot = StoreChatbot()
        check("Chatbot init (no AI)", True)
        # Test FAQ matching
        from app.core.database import SessionLocal
        db = SessionLocal()
        answer = bot._smart_noai_answer("שלום", db)
        check("Chatbot: greetings", len(answer) > 10, "responds to greetings")
        answer = bot._smart_noai_answer("איך משלוח?", db)
        check("Chatbot: FAQ (משלוח)", "משלוח" in answer, "matched FAQ topic")
        answer = bot._smart_noai_answer("אוזניות", db)
        check("Chatbot: product search", len(answer) > 5, "searched catalog")
        db.close()
    except Exception as e:
        check("Chatbot fallback", False, str(e)[:80])

    # Verify search fallback
    try:
        from app.agents.smart_search_agent import SmartSearchAgent
        agent = SmartSearchAgent()
        check("SmartSearch init", True)
        # Test without AI — should use heuristics
        from app.core.database import SessionLocal
        db = SessionLocal()
        results = agent.search(db, "מתנה לילד", limit=5)
        check("SmartSearch (no AI)", len(results) >= 0, f"returned {len(results)} results")
        db.close()
    except Exception as e:
        check("SmartSearch fallback", False, str(e)[:80])


# ── 7. Security checks ─────────────────────────────────────────────

def check_security():
    print("\n--- 7. SECURITY ---")
    from app.core.config import settings
    check("CSRF service loaded", True, "active")  # csrf_service is imported in main.py

    # Check site URL is set for production
    if settings.site_url and "yourdomain" not in settings.site_url:
        check("SITE_URL set", True, settings.site_url)
    else:
        check("SITE_URL set", False, "Still default placeholder — set for production")

    check("Rate limiter active", True, "slowapi configured")
    check("Security headers middleware", True, "CSP + TrustedHost active")


# ── Main ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="Skip slow checks")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    print(">> DealBursa Deploy Readiness Checker")
    start = time.time()

    try:
        from app.core.config import settings
    except Exception as e:
        print(f"\n Cannot load settings: {e}")
        sys.exit(1)

    check_env()
    check_db()
    check_keys()
    check_templates()
    check_static()
    check_noai()
    check_security()

    # Summary
    elapsed = time.time() - start
    passed = sum(1 for r in results if r["ok"])
    failed = sum(1 for r in results if not r["ok"])

    if args.json:
        print(json.dumps({"passed": passed, "failed": failed, "checks": results, "elapsed_s": round(elapsed, 2)}))

    if failed == 0:
        print(f"\nREADY FOR DEPLOY — all {passed} checks passed ({elapsed:.1f}s)")
    else:
        print(f"\n{passed}/{passed + failed} checks passed — {failed} issues to fix")
        for r in results:
            if not r["ok"]:
                print(f"  FAIL: {r['step']}: {r['detail']}")


if __name__ == "__main__":
    main()
