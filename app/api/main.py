"""
FastAPI app. Routes map directly onto what the existing Jinja templates
already expect (/, /product/<id>, /go/<id>, /api/chat, /api/track-view,
/api/price-war/<id>, /personal-area, /admin) so the templates in
app/templates/ can be dropped in without rewriting their fetch() calls.
"""
import datetime
import logging
import os
import secrets
import time

import requests
from fastapi import FastAPI, Request, Depends, Form, BackgroundTasks, HTTPException, Header, UploadFile, File
from fastapi.responses import RedirectResponse, JSONResponse, Response, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.gzip import GZipMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler
from authlib.integrations.starlette_client import OAuth
from sqlalchemy.orm import Session

from app.core.database import get_db, SessionLocal
from app.core.config import settings
from app.core.security_middleware import limiter, SecurityHeadersMiddleware
from app.core import models as models_module
from app.core.models import (
    Product, User, Order, ProductView, PriceAlert, AffiliateClick,
    NewsletterSubscriber, ProductFavorite, DailyPrice,
    PointTransaction, Notification, AdPlacement, ProductReview, Coupon,
    PushSubscription,
)
from app.services.fraud_service import FraudService
from app.services.tracking_service import choose_best_target, log_click
from app.services.product_service import enrich_products_for_home
from app.services import price_service, auth_service, order_tracking_service, cache_service, brute_force_guard, csrf_service, email_service
from app.services.price_monitor_service import record_daily_prices, check_price_alerts
from app.services import notification_service, loyalty_service, ads_service, meili_search_service, image_proxy_service
from app.agents.chatbot import StoreChatbot
from app.agents.recommender import RecommenderAgent
from app.agents.smart_search_agent import SmartSearchAgent
from app.agents.instagram_agent import InstagramAgent
from app.agents.marketing_agent import MarketingAgent
from app.agents.email_campaign_agent import EmailCampaignAgent
from app.agents.deal_of_the_day_agent import DealOfTheDayAgent
from app.agents.price_forecast_agent import PriceForecastAgent
from app.agents.review_insights_agent import ReviewInsightsAgent
from app.agents.loyalty_coach_agent import LoyaltyCoachAgent
from app.agents.blog_agent import BlogAgent
from app.agents.auto_viral_engine import AutoViralEngine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="SmartShop")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Host-header poisoning defense: only serve requests whose Host matches the
# configured site domain or a known local/dev host. The API is deliberately
# NOT wildcarded — a Host of "evil.com" must not render pages or emails
# with attacker-chosen links.
from starlette.middleware.trustedhost import TrustedHostMiddleware as _TrustedHost
_trusted_hosts = ["localhost", "127.0.0.1", "0.0.0.0", "[::1]"]
_site_host = settings.site_url.split("//")[-1].split("/")[0]
if _site_host and _site_host not in _trusted_hosts:
    _trusted_hosts.append(_site_host)
# The default site_url (https://yourdomain.com) is a placeholder — if it's
# still unset in production, TrustedHost would 400 every request with
# "Invalid host header" and the failure mode is a blank page, not an
# obvious error. Surface it loudly at boot instead.
if settings.site_url.strip().lower() in ("", "https://yourdomain.com", "http://yourdomain.com"):
    logger.warning(
        "SITE_URL is still the default placeholder — TrustedHostMiddleware will reject "
        "requests with any other Host. Set SITE_URL in .env / the settings page "
        "before going live, otherwise the site may appear blank."
    )
app.add_middleware(_TrustedHost, allowed_hosts=_trusted_hosts)

# Session cookie: HttpOnly + SameSite=Lax always; https_only only when we're
# in production (HTTPS) — during local dev the site runs over http and a
# secure-only cookie would silently drop every session.
_https_only = settings.env == "production"
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret_key,
    same_site="lax",
    https_only=_https_only,
    max_age=60 * 60 * 24 * 7,
)
# GZip compression: reduces response size by 60-80% for HTML/JSON/CSS/JS.
# Minimum size 500 bytes so tiny responses (like CSRF tokens) skip the overhead.
app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(SecurityHeadersMiddleware)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

# Cache-busting version for static assets: every template renders
# /static/...?... with the newest file's mtime, so a deploy (which touches
# CSS/JS) instantly invalidates browser caches — no stale-theme issues.
def _static_version() -> str:
    newest = 0.0
    for path in ("app/static/css/design_system.css", "app/static/js/main.js"):
        try:
            newest = max(newest, os.path.getmtime(path))
        except OSError:
            pass
    return str(int(newest))

templates.env.globals["static_version"] = _static_version()

# Category metadata for the AliExpress-style mega-menu — available on EVERY
# page (layout renders it) without every route having to pass it.
CATEGORY_ICONS = {
    "אלקטרוניקה": "fa-solid fa-plug", "גאדג'טים": "fa-solid fa-headphones",
    "לבית ולמטבח": "fa-solid fa-house", "כלי עבודה": "fa-solid fa-toolbox",
    "אביזרי רכב": "fa-solid fa-car-side", "אופנה": "fa-solid fa-shirt",
    "ספורט ופנאי": "fa-solid fa-volleyball", "יופי וטיפוח": "fa-solid fa-spa",
    "משחקים וצעצועים": "fa-solid fa-gamepad", "מוצרי תינוקות": "fa-solid fa-baby-carriage",
    "משרד ומחשבים": "fa-solid fa-desktop", "חיות מחמד": "fa-solid fa-paw",
    "מזון וחטיפים": "fa-solid fa-candy-cane", "גינון": "fa-solid fa-seedling",
    "צילום ומוזיקה": "fa-solid fa-camera-retro", "תכשיטים ושעונים": "fa-solid fa-gem",
    "ספרים ותחביבים": "fa-solid fa-book-open", "בריאות ומטבח": "fa-solid fa-kitchen-set",
    "ריהוט ועיצוב הבית": "fa-solid fa-couch", "תיקים ומזוודות": "fa-solid fa-suitcase-rolling",
    "נעליים": "fa-solid fa-shoe-prints", "מכשירי חשמל ביתיים": "fa-solid fa-blender",
    "רכיבים אלקטרוניים": "fa-solid fa-microchip", "כלי נגינה": "fa-solid fa-music",
}
CATEGORY_SUBS = {
    "אלקטרוניקה": ["אוזניות", "מטענים אלחוטיים", "רמקולים", "כבלים ואביזרים", "שעונים חכמים", "טאבלטים", "מצלמות", "אביזרי גיימינג"],
    "גאדג'טים": ["גאדג'טים למטבח", "תאורת LED", "מאווררים ניידים", "מקרנים", "רובוטיקה", "שואבים רובוטיים", "מדפסות תלת-ממד", "משקפי VR"],
    "לבית ולמטבח": ["אחסון וארגון", "כלי מטבח", "טקסטיל לבית", "עיצוב", "מכשירי מטבח קטנים", "וילונות", "שטיחים", "אביזרי אמבטיה"],
    "כלי עבודה": ["מקדחות", "ערכות מפתחות", "מדידות", "כלי יד", "ציוד בטיחות", "מברגות חשמליות", "מסורים", "משחזות"],
    "אביזרי רכב": ["מטענים לרכב", "מחזיקי פלאפון", "תאורה לרכב", "ניקוי רכב", "אביזרים פנימיים", "מצלמות דרך", "חיישני חניה", "כיסויי הגה"],
    "אופנה": ["חולצות", "ג'ינס", "שמלות", "בגדי ספורט", "גרביים", "מעילים", "פיג'מות", "בגדי ים"],
    "ספורט ופנאי": ["ציוד כושר", "יוגה", "אופניים ואביזרים", "מחנאות", "שחייה", "כדורגל", "כדורסל", "ריצה"],
    "יופי וטיפוח": ["איפור", "טיפוח עור", "מברשות", "טיפוח שיער", "מניקור", "בשמים", "מסכות פנים", "ערכות גילוח"],
    "משחקים וצעצועים": ["צעצועי ילדים", "משחקי קופסה", "ערכות יצירה", "דמויות אקשן", "צעצועים לפעוטות", "פאזלים", "לגו", "משחקי קלפים"],
    "מוצרי תינוקות": ["הנקה", "עגלות", "בגדי תינוקות", "מוצצים ובקבוקים", "מוביילים", "חיתולים", "מנשאים", "צעצועי התפתחות"],
    "משרד ומחשבים": ["מקלדות", "עכברים", "מסכים", "אביזרי מחשב נייד", "כיסאות משרד", "מדפסות", "נתבים", "כוננים חיצוניים"],
    "חיות מחמד": ["מזון לחיות", "צעצועים לחתולים", "כלובים", "קולרים ורצועות", "טיפוח", "אקווריומים", "מיטות לכלבים", "חול לחתולים"],
    "מזון וחטיפים": ["חטיפים", "ממתקים", "תה וקפה", "מזון בריאות", "תבלינים", "שוקולד", "פירות יבשים", "שתייה"],
    "גינון": ["עציצים", "כלים לגינון", "השקיה", "תאורת גינה", "ריהוט גן", "זרעים", "דשא סינתטי", "ערסלים"],
    "צילום ומוזיקה": ["מצלמות", "חצובות", "מיקרופונים", "כלי נגינה", "אוזניות אולפן", "כרטיסי זיכרון", "תאורת סטודיו", "רמקולי Bluetooth"],
    "תכשיטים ושעונים": ["טבעות", "שרשראות", "צמידים", "שעונים", "עגילים", "צמידי כושר", "תכשיטי כסף", "קופסאות תכשיטים"],
    "ספרים ותחביבים": ["ספרים", "תחביבים יצירתיים", "אומנות", "ציוד ציור", "משחקי חשיבה", "דוגמנות", "רקמה", "אלבומי תמונות"],
    "בריאות ומטבח": ["מכשירי מטבח", "בלנדרים", "סירים ומחבתות", "בריאות", "מסנני מים", "תוספי תזונה", "מכשירי אדים", "מדי חום"],
    "ריהוט ועיצוב הבית": ["מדפים", "כיסאות", "שולחנות", "תאורה", "עיצוב קירות", "מראות", "שידות", "יחידות אחסון"],
    "תיקים ומזוודות": ["תרמילים", "מזוודות", "תיקי יד", "תיקי איפור", "תיקי מחשב", "ארנקים", "תיקי גב", "תיקי נסיעות"],
    "נעליים": ["סניקרס", "נעלי בית", "מגפיים", "סנדלים", "נעלי ספורט", "נעלי עקב", "כפכפים", "מדרסים"],
    "מכשירי חשמל ביתיים": ["שואבים", "מייבשי כביסה", "מגהצים", "מאווררים", "מזגנים ניידים", "טוסטרים", "מיקרוגלים", "מכונות קפה"],
    "רכיבים אלקטרוניים": ["ארדואינו", "חיישנים", "מודולים", "הלחמה", "רחפנים DIY", "סוללות", "מנועים", "מעגלים מודפסים"],
    "כלי נגינה": ["גיטרות", "מקלדות MIDI", "סטים לתופים", "קאחונים", "אביזרים", "כינורות", "מפוחיות", "יוקלילי"],
}
CATEGORY_SUB_ICONS = {
    "אוזניות": "fa-solid fa-headphones", "מטענים אלחוטיים": "fa-solid fa-bolt",
    "רמקולים": "fa-solid fa-volume-high", "כבלים ואביזרים": "fa-solid fa-plug",
    "שעונים חכמים": "fa-solid fa-clock", "טאבלטים": "fa-solid fa-tablet-screen-button",
    "מצלמות": "fa-solid fa-camera", "אביזרי גיימינג": "fa-solid fa-gamepad",
    "גאדג'טים למטבח": "fa-solid fa-blender", "תאורת LED": "fa-solid fa-lightbulb",
    "מאווררים ניידים": "fa-solid fa-fan", "מקרנים": "fa-solid fa-film",
    "רובוטיקה": "fa-solid fa-robot", "שואבים רובוטיים": "fa-solid fa-broom",
    "מדפסות תלת-ממד": "fa-solid fa-cubes", "משקפי VR": "fa-solid fa-vr-cardboard",
    "אחסון וארגון": "fa-solid fa-box-archive", "כלי מטבח": "fa-solid fa-kitchen-set",
    "טקסטיל לבית": "fa-solid fa-rug", "עיצוב": "fa-solid fa-paintbrush",
    "מכשירי מטבח קטנים": "fa-solid fa-mug-saucer", "וילונות": "fa-solid fa-bars",
    "שטיחים": "fa-solid fa-rug", "אביזרי אמבטיה": "fa-solid fa-shower",
    "מקדחות": "fa-solid fa-gear", "ערכות מפתחות": "fa-solid fa-wrench",
    "מדידות": "fa-solid fa-ruler-combined", "כלי יד": "fa-solid fa-hammer",
    "ציוד בטיחות": "fa-solid fa-hard-hat", "מברגות חשמליות": "fa-solid fa-screwdriver-wrench",
    "מסורים": "fa-solid fa-bore-hole", "משחזות": "fa-solid fa-circle-notch",
    "מטענים לרכב": "fa-solid fa-car-battery", "מחזיקי פלאפון": "fa-solid fa-mobile-screen-button",
    "תאורה לרכב": "fa-solid fa-car-side", "ניקוי רכב": "fa-solid fa-soap",
    "אביזרים פנימיים": "fa-solid fa-car", "מצלמות דרך": "fa-solid fa-video",
    "חיישני חניה": "fa-solid fa-sensor", "כיסויי הגה": "fa-solid fa-circle",
    "חולצות": "fa-solid fa-shirt", "ג'ינס": "fa-solid fa-person",
    "שמלות": "fa-solid fa-person-dress", "בגדי ספורט": "fa-solid fa-person-running",
    "גרביים": "fa-solid fa-socks", "מעילים": "fa-solid fa-jacket",
    "פיג'מות": "fa-solid fa-moon", "בגדי ים": "fa-solid fa-umbrella-beach",
    "ציוד כושר": "fa-solid fa-dumbbell", "יוגה": "fa-solid fa-spa",
    "אופניים ואביזרים": "fa-solid fa-bicycle", "מחנאות": "fa-solid fa-tent",
    "שחייה": "fa-solid fa-water-ladder", "כדורגל": "fa-solid fa-futbol",
    "כדורסל": "fa-solid fa-basketball", "ריצה": "fa-solid fa-person-running",
    "איפור": "fa-solid fa-paintbrush", "טיפוח עור": "fa-solid fa-droplet",
    "מברשות": "fa-solid fa-brush", "טיפוח שיער": "fa-solid fa-scissors",
    "מניקור": "fa-solid fa-hand-sparkles", "בשמים": "fa-solid fa-spray-can-sparkles",
    "מסכות פנים": "fa-solid fa-theater-masks", "ערכות גילוח": "fa-solid fa-razor",
    "צעצועי ילדים": "fa-solid fa-puzzle-piece", "משחקי קופסה": "fa-solid fa-dice",
    "ערכות יצירה": "fa-solid fa-palette", "דמויות אקשן": "fa-solid fa-mask",
    "צעצועים לפעוטות": "fa-solid fa-baby", "פאזלים": "fa-solid fa-puzzle-piece",
    "לגו": "fa-solid fa-cubes", "משחקי קלפים": "fa-solid fa-clubs",
    "הנקה": "fa-solid fa-person-breastfeeding", "עגלות": "fa-solid fa-baby-carriage",
    "בגדי תינוקות": "fa-solid fa-baby", "מוצצים ובקבוקים": "fa-solid fa-bottle-water",
    "מוביילים": "fa-solid fa-mobile", "חיתולים": "fa-solid fa-diaper",
    "מנשאים": "fa-solid fa-person-carry-box", "צעצועי התפתחות": "fa-solid fa-child-reaching",
    "מקלדות": "fa-solid fa-keyboard", "עכברים": "fa-solid fa-computer-mouse",
    "מסכים": "fa-solid fa-desktop", "אביזרי מחשב נייד": "fa-solid fa-laptop",
    "כיסאות משרד": "fa-solid fa-chair", "מדפסות": "fa-solid fa-print",
    "נתבים": "fa-solid fa-wifi", "כוננים חיצוניים": "fa-solid fa-hard-drive",
    "מזון לחיות": "fa-solid fa-bowl-food", "צעצועים לחתולים": "fa-solid fa-cat",
    "כלובים": "fa-solid fa-cage", "קולרים ורצועות": "fa-solid fa-dog",
    "טיפוח": "fa-solid fa-scissors", "אקווריומים": "fa-solid fa-fish",
    "מיטות לכלבים": "fa-solid fa-bed", "חול לחתולים": "fa-solid fa-box",
    "חטיפים": "fa-solid fa-cookie", "ממתקים": "fa-solid fa-candy-cane",
    "תה וקפה": "fa-solid fa-mug-hot", "מזון בריאות": "fa-solid fa-leaf",
    "תבלינים": "fa-solid fa-jar", "שוקולד": "fa-solid fa-cubes",
    "פירות יבשים": "fa-solid fa-apple-whole", "שתייה": "fa-solid fa-bottle-water",
    "עציצים": "fa-solid fa-seedling", "כלים לגינון": "fa-solid fa-shovel",
    "השקיה": "fa-solid fa-droplet", "תאורת גינה": "fa-solid fa-lightbulb",
    "ריהוט גן": "fa-solid fa-couch", "זרעים": "fa-solid fa-seedling",
    "דשא סינתטי": "fa-solid fa-clover", "ערסלים": "fa-solid fa-hammock",
    "חצובות": "fa-solid fa-tripod", "מיקרופונים": "fa-solid fa-microphone",
    "אוזניות אולפן": "fa-solid fa-headphones", "כרטיסי זיכרון": "fa-solid fa-sd-card",
    "תאורת סטודיו": "fa-solid fa-lightbulb", "רמקולי Bluetooth": "fa-solid fa-bluetooth",
    "טבעות": "fa-solid fa-ring", "שרשראות": "fa-solid fa-necklace",
    "צמידים": "fa-solid fa-bracelet", "שעונים": "fa-solid fa-clock",
    "עגילים": "fa-solid fa-earring", "צמידי כושר": "fa-solid fa-heart-pulse",
    "תכשיטי כסף": "fa-solid fa-gem", "קופסאות תכשיטים": "fa-solid fa-box-open",
    "ספרים": "fa-solid fa-book-open", "תחביבים יצירתיים": "fa-solid fa-palette",
    "אומנות": "fa-solid fa-paintbrush", "ציוד ציור": "fa-solid fa-pencil",
    "משחקי חשיבה": "fa-solid fa-brain", "דוגמנות": "fa-solid fa-cubes",
    "רקמה": "fa-solid fa-thread", "אלבומי תמונות": "fa-solid fa-images",
    "בלנדרים": "fa-solid fa-blender", "סירים ומחבתות": "fa-solid fa-pot-food",
    "מסנני מים": "fa-solid fa-filter-circle-dollar", "תוספי תזונה": "fa-solid fa-capsules",
    "מכשירי אדים": "fa-solid fa-smog", "מדי חום": "fa-solid fa-temperature-high",
    "מדפים": "fa-solid fa-layer-group", "כיסאות": "fa-solid fa-chair",
    "שולחנות": "fa-solid fa-table", "תאורה": "fa-solid fa-lightbulb",
    "עיצוב קירות": "fa-solid fa-paint-roller", "מראות": "fa-solid fa-mirror",
    "שידות": "fa-solid fa-box", "יחידות אחסון": "fa-solid fa-boxes-stacked",
    "תרמילים": "fa-solid fa-backpack", "מזוודות": "fa-solid fa-suitcase-rolling",
    "תיקי יד": "fa-solid fa-bag-shopping", "תיקי איפור": "fa-solid fa-pouch",
    "תיקי מחשב": "fa-solid fa-briefcase", "ארנקים": "fa-solid fa-wallet",
    "תיקי גב": "fa-solid fa-backpack", "תיקי נסיעות": "fa-solid fa-suitcase",
    "סניקרס": "fa-solid fa-shoe-prints", "נעלי בית": "fa-solid fa-slippers",
    "מגפיים": "fa-solid fa-boot", "סנדלים": "fa-solid fa-flip-flop",
    "נעלי ספורט": "fa-solid fa-person-running", "נעלי עקב": "fa-solid fa-shoe-prints",
    "כפכפים": "fa-solid fa-flip-flop", "מדרסים": "fa-solid fa-insole",
    "שואבים": "fa-solid fa-broom", "מייבשי כביסה": "fa-solid fa-dryer",
    "מגהצים": "fa-solid fa-iron", "מאווררים": "fa-solid fa-fan",
    "מזגנים ניידים": "fa-solid fa-temperature-arrow-down", "טוסטרים": "fa-solid fa-bread-slice",
    "מיקרוגלים": "fa-solid fa-microwave", "מכונות קפה": "fa-solid fa-mug-saucer",
    "ארדואינו": "fa-solid fa-microchip", "חיישנים": "fa-solid fa-sensor",
    "מודולים": "fa-solid fa-puzzle-piece", "הלחמה": "fa-solid fa-fire-burner",
    "רחפנים DIY": "fa-solid fa-drone", "סוללות": "fa-solid fa-battery-full",
    "מנועים": "fa-solid fa-engine", "מעגלים מודפסים": "fa-solid fa-circle-nodes",
    "גיטרות": "fa-solid fa-guitar", "מקלדות MIDI": "fa-solid fa-keyboard",
    "סטים לתופים": "fa-solid fa-drum", "קאחונים": "fa-solid fa-cube",
    "אביזרים": "fa-solid fa-sliders", "כינורות": "fa-solid fa-violin",
    "מפוחיות": "fa-solid fa-wind", "יוקלילי": "fa-solid fa-guitar",
}
# Category thumbnail images — one real product image per category, loaded from
# the DB on first access and cached for the lifetime of the process. Used in
# the mega-menu and filter sidebar instead of Font Awesome icons for a more
# authentic, AliExpress-style look.
_category_thumbnails: dict | None = None

def _get_category_thumbnails(db):
    global _category_thumbnails
    if _category_thumbnails is not None:
        return _category_thumbnails
    thumbs = {}
    for cat in CATEGORIES:
        p = db.query(Product).filter(
            Product.category == cat, Product.is_active == True,
            Product.image_url.isnot(None), Product.image_url != ''
        ).order_by(Product.review_count.desc()).first()
        if p:
            thumbs[cat] = p.image_url
    _category_thumbnails = thumbs
    logger.info("Loaded %d category thumbnails", len(thumbs))
    return thumbs

# The category globals are wired after CATEGORIES/CATEGORY_ICONS are defined
# (see below, next to the CATEGORIES list).

oauth = OAuth()
if settings.google_oauth_client_id and settings.google_oauth_client_secret:
    oauth.register(
        name="google",
        client_id=settings.google_oauth_client_id,
        client_secret=settings.google_oauth_client_secret,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )
GOOGLE_OAUTH_ENABLED = bool(settings.google_oauth_client_id and settings.google_oauth_client_secret)


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    """A raw {"detail":"Not Found"} JSON blob looks broken to a visitor who
    followed a stale/typo'd link — a branded 404 keeps them on-site and
    funnels them back to the deals grid instead of bouncing immediately."""
    return templates.TemplateResponse("404.html", {"request": request}, status_code=404)


# DB init result exposed via /db-check. We never crash the app on a DB
# hiccup at boot: retry a few times (managed Postgres like Neon cold-starts
# on first connect), then keep serving so /healthz stays green and the real
# error is visible on /db-check instead of a blank page.
_db_init_error: str | None = None


@app.on_event("startup")
def _create_tables_if_missing():
    """create_all() only ever adds missing tables — never drops or alters
    existing ones — so it's safe to run on every boot. This matters on
    free hosting tiers where you typically don't get a shell to run
    scripts/init_db.py by hand; the app becomes self-installing instead.
    Once you introduce real schema migrations (Alembic), replace this
    with an explicit migration step in your deploy pipeline."""
    global _db_init_error
    from app.core.database import engine
    from app.core.models import Base
    import time as _time
    last_err = None
    for attempt in range(6):
        try:
            Base.metadata.create_all(bind=engine)
            _db_init_error = None
            logger.info("Database ready (tables ensured).")
            return
        except Exception as e:  # noqa: BLE001 — surface via /db-check, keep serving
            last_err = e
            logger.warning("DB init attempt %d/6 failed: %s", attempt + 1, e)
            _time.sleep(5)
    _db_init_error = f"{type(last_err).__name__}: {last_err}"
    logger.error("DB init failed after retries: %s", _db_init_error)

fraud_service = FraudService()
chatbot = StoreChatbot()
recommender = RecommenderAgent()
smart_search = SmartSearchAgent()
instagram_agent = InstagramAgent()
email_campaign = EmailCampaignAgent()
deal_of_day = DealOfTheDayAgent()
price_forecast = PriceForecastAgent()
review_insights = ReviewInsightsAgent()
loyalty_coach = LoyaltyCoachAgent()
blog_agent = BlogAgent()
viral_engine = AutoViralEngine()

CATEGORIES = [
    "אלקטרוניקה",
    "גאדג'טים",
    "לבית ולמטבח",
    "כלי עבודה",
    "אביזרי רכב",
    "אופנה",
    "ספורט ופנאי",
    "יופי וטיפוח",
    "משחקים וצעצועים",
    "מוצרי תינוקות",
    "משרד ומחשבים",
    "חיות מחמד",
    "מזון וחטיפים",
    "גינון",
    "צילום ומוזיקה",
    "תכשיטים ושעונים",
    "ספרים ותחביבים",
    "בריאות ומטבח",
    "ריהוט ועיצוב הבית",
    "תיקים ומזוודות",
    "נעליים",
    "מכשירי חשמל ביתיים",
    "רכיבים אלקטרוניים",
    "כלי נגינה",
]

# Layout renders the category nav + mega-menu on EVERY page, so the list
# and its metadata must be globally available (not just on home/search).
templates.env.globals["categories"] = CATEGORIES
templates.env.globals["category_icons"] = CATEGORY_ICONS
templates.env.globals["category_subs"] = CATEGORY_SUBS
templates.env.globals["category_sub_icons"] = CATEGORY_SUB_ICONS

# Register a helper so templates can generate image-proxy URLs:
# <img src="/img/{{ product.image_url | imgproxy }}"> becomes
# <img src="/img/aHR0cHM6Ly..."> — the endpoint fetches + converts to WebP.
import base64 as _b64_filter
def _imgproxy_filter(url: str) -> str:
    """Return a single proxy URL (no width → max 1200px default)."""
    if not url or not url.startswith('http'):
        return url or ''
    encoded = _b64_filter.urlsafe_b64encode(url.encode()).decode().rstrip('=')
    return f"/img/{encoded}"

def _imgsrcset_filter(url: str, widths: list[int] | None = None) -> str:
    """Return a srcset string for responsive images.
    Usage: <img src="{{ url | imgproxy }}" srcset="{{ url | imgsrcset }}" sizes="...">
    Default widths: 200, 400, 600, 900, 1200"""
    if not url or not url.startswith('http'):
        return ''
    if widths is None:
        widths = [200, 400, 600, 900, 1200]
    encoded = _b64_filter.urlsafe_b64encode(url.encode()).decode().rstrip('=')
    return ', '.join(f"/img/{encoded}?w={w} {w}w" for w in widths)

templates.env.filters['imgproxy'] = _imgproxy_filter
templates.env.filters['imgsrcset'] = _imgsrcset_filter

# --- Admin auth ---
# Session-based admin login (password from ADMIN_SECRET_KEY) so the admin
# panel has a real "כניסת מנהל" button instead of a browser Basic-auth
# prompt (which fetch() calls from the dashboard can't reuse reliably).
# HTTP Basic is kept as a fallback so external cron jobs / curl -u still
# work against /admin/run-discovery etc.


def _is_admin(request: Request) -> bool:
    """Shared check: session flag OR valid HTTP Basic admin credentials.
    The Basic username may be the legacy literal "admin" (used by cron jobs)
    or the configured system email — either works as long as the password
    matches ADMIN_SECRET_KEY.

    The session flag is stamped with admin_login_at on login and expires
    after admin_session_hours (default 24h), so a stale browser tab can't
    stay in the panel forever even though the session cookie itself persists."""
    import base64
    import time as _time
    if request.session.get("is_admin"):
        login_at = request.session.get("admin_login_at") or 0
        if _time.time() - login_at < settings.admin_session_hours * 3600:
            return True
        # Session too old — clear the flag so the user is asked to log in again.
        request.session["is_admin"] = False
    auth = request.headers.get("authorization", "")
    if auth.startswith("Basic "):
        try:
            decoded = base64.b64decode(auth[6:]).decode()
            user, _, pw = decoded.partition(":")
            user_ok = user == "admin" or (settings.admin_email and user.lower() == settings.admin_email.lower())
            if user_ok and secrets.compare_digest(pw, settings.admin_secret_key):
                return True
        except Exception:
            pass
    return False


def require_admin(request: Request):
    """Session-first, HTTP Basic fallback (for cron/scripts)."""
    if _is_admin(request):
        return True
    raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Basic"})


async def require_admin_csrf(request: Request):
    """Admin auth + CSRF for every state-changing admin POST.

    Closes the window where an attacker's page could silently submit a
    fetch()/form to an admin endpoint using the victim's session cookie.
    Three ways to pass, any one is enough:

    1. Basic-auth client (cron jobs / curl -u): carries no ambient session
       cookie, so there is no CSRF to defend against — exempt by design.
    2. Same-origin proof from the browser: the Origin header matching the
       request host, or Sec-Fetch-Site == same-origin/same-site. Browsers
       send these on every fetch, so a legit admin dashboard click passes
       without needing a token field on every button.
    3. A valid signed CSRF token (X-CSRF-Token header or csrf_token form
       field) — belt-and-suspenders for endpoints whose forms already
       embed a token.

    Fails closed: any admin-authenticated POST that can't prove origin
    (and has no valid token) gets 403, never executed.
    """
    if not _is_admin(request):
        raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Basic"})

    # 1. Non-browser admin clients (cron/scripts) — no ambient cookie.
    if request.headers.get("authorization", "").startswith("Basic "):
        return True

    # 2. Same-origin proof the browser attaches to every fetch/form POST.
    origin = request.headers.get("origin") or ""
    if origin:
        from urllib.parse import urlparse
        try:
            if urlparse(origin).netloc == request.url.netloc:
                return True
        except Exception:
            pass
    sec_fetch_site = request.headers.get("sec-fetch-site") or ""
    if sec_fetch_site in ("same-origin", "same-site"):
        return True

    # 3. Signed CSRF token (header or form field).
    token = request.headers.get("x-csrf-token") or ""
    if not token:
        try:
            form = await request.form()
            token = str(form.get("csrf_token") or "")
        except Exception:
            token = ""
    if token and csrf_service.verify_csrf_token(token):
        return True

    raise HTTPException(status_code=403, detail="CSRF verification failed")


@app.get("/admin/login")
def admin_login_page(request: Request):
    if request.session.get("is_admin"):
        return RedirectResponse("/admin", status_code=303)
    return templates.TemplateResponse("admin_login.html", {
        "request": request, "error": None,
        "admin_email": settings.admin_email,
    })


@app.post("/admin/login")
@limiter.limit("10/minute")
def admin_login_submit(request: Request, email: str = Form(""), password: str = Form(...)):
    """Admin sign-in with the SYSTEM email + password (both defined in .env
    / the settings page: ADMIN_EMAIL + ADMIN_SECRET_KEY).
    The legacy username "admin" also works, so users who remember the old
    password-only flow aren't blocked by a forgotten email address."""
    given = email.strip().lower()
    expected = (settings.admin_email or "").strip().lower()
    email_ok = (not expected) or given in (expected, "admin")
    if email_ok and secrets.compare_digest(password, settings.admin_secret_key):
        request.session["is_admin"] = True
        request.session["admin_email"] = given  # used by rate-limit key function for per-admin isolation
        request.session["admin_login_at"] = int(datetime.datetime.utcnow().timestamp())
        return RedirectResponse("/admin", status_code=303)
    return templates.TemplateResponse(
        "admin_login.html",
        {"request": request, "error": "המייל או הסיסמה שגויים", "admin_email": settings.admin_email},
        status_code=401,
    )


@app.post("/admin/logout")
def admin_logout(request: Request):
    """Logout as POST (logout-CSRF guard): a GET link could be triggered by
    an attacker's <img>, silently signing the admin out mid-session."""
    request.session["is_admin"] = False
    return RedirectResponse("/", status_code=303)


# --- Runtime settings editor (admin) ---
# Lets the operator manage AI / SMTP / Instagram credentials from the UI
# (saved straight to .env, applied live) plus a one-click connection test.

def _reload_ai_agents():
    """Rebuild agent singletons after settings change so new credentials
    (Gemini key, Instagram token...) are picked up without a restart."""
    global chatbot, recommender, smart_search, instagram_agent, email_campaign, marketing_agent
    global deal_of_day, price_forecast, review_insights, loyalty_coach, blog_agent, viral_engine
    chatbot = StoreChatbot()
    recommender = RecommenderAgent()
    smart_search = SmartSearchAgent()
    instagram_agent = InstagramAgent()
    marketing_agent = MarketingAgent()
    email_campaign = EmailCampaignAgent()
    deal_of_day = DealOfTheDayAgent()
    price_forecast = PriceForecastAgent()
    review_insights = ReviewInsightsAgent()
    loyalty_coach = LoyaltyCoachAgent()
    blog_agent = BlogAgent()
    viral_engine = AutoViralEngine()


@app.get("/admin/settings")
def admin_settings_page(request: Request, _auth: bool = Depends(require_admin)):
    from app.services import settings_service
    return templates.TemplateResponse("admin_settings.html", {
        "request": request,
        "values": settings_service.get_current(),
        "csrf_token": csrf_service.generate_csrf_token(),
    })


@app.post("/admin/settings/save")
async def admin_settings_save(request: Request, _auth: bool = Depends(require_admin_csrf)):
    from starlette.concurrency import run_in_threadpool
    from app.services import settings_service
    form = await request.form()
    if not csrf_service.verify_csrf_token(str(form.get("csrf_token") or "")):
        return JSONResponse({"status": "error", "message": "הטופס פג תוקף — רעננו את הדף ונסו שוב"}, status_code=400)
    payload = {str(k): str(v) for k, v in form.items()}

    def _do_save():
        changed = settings_service.save(payload)
        if changed:
            _reload_ai_agents()
        return changed

    changed = await run_in_threadpool(_do_save)
    return JSONResponse({
        "status": "ok",
        "changed": changed,
        "message": "ההגדרות נשמרו ונכנסו לתוקף! ✅",
    })


@app.post("/admin/settings/test/{service}")
@limiter.limit("10/minute")  # only authenticated admin requests count (the CSRF gate 401s before the limiter runs)
async def admin_settings_test(request: Request, service: str, _auth: bool = Depends(require_admin_csrf)):
    from starlette.concurrency import run_in_threadpool
    from app.services import settings_service
    form = await request.form()
    if not csrf_service.verify_csrf_token(str(form.get("csrf_token") or "")):
        return JSONResponse({"status": "error", "message": "הטופס פג תוקף — רעננו את הדף ונסו שוב"}, status_code=400)
    if service not in settings_service.TEST_FIELDS:
        return JSONResponse({"status": "error", "message": "שירות לא ידוע"}, status_code=400)
    # Freshly-typed (non-empty) values win; empty fields fall back to saved settings.
    overrides = {}
    for env in settings_service.TEST_FIELDS[service]:
        raw = str(form.get(env) or "").strip()
        if raw:
            attr = next(a for a, e in settings_service.EDITABLE.items() if e == env)
            overrides[attr] = settings_service._coerce(attr, raw)
    ok, msg = await run_in_threadpool(settings_service.run_test, service, overrides)
    return JSONResponse({"status": "ok" if ok else "error", "message": msg})


# --- User authentication (separate from admin auth above) ---
@app.get("/signup")
def signup_page(request: Request):
    return templates.TemplateResponse("signup.html", {
        "request": request, "error": None, "notice": None,
        "csrf_token": csrf_service.generate_csrf_token(),
        "google_oauth_enabled": GOOGLE_OAUTH_ENABLED,
    })


@app.post("/signup")
@limiter.limit("5/minute")
def signup_submit(request: Request, email: str = Form(...), password: str = Form(...), csrf_token: str = Form(""), website: str = Form(""), db: Session = Depends(get_db)):
    ctx = {"request": request, "csrf_token": csrf_service.generate_csrf_token(), "google_oauth_enabled": GOOGLE_OAUTH_ENABLED}
    # Honeypot: real users never see/fill this hidden field; bots do.
    # Silently "succeed" so the bot thinks it worked — no signup happens.
    if website:
        request.session["user_id"] = None
        return RedirectResponse("/", status_code=303)
    if not csrf_service.verify_csrf_token(csrf_token):
        return templates.TemplateResponse("signup.html", {**ctx, "error": "הטופס פג תוקף, נסה שוב"}, status_code=400)
    if len(password) < 8:
        return templates.TemplateResponse("signup.html", {**ctx, "error": "הסיסמה חייבת להכיל לפחות 8 תווים"})
    # Google-only accounts have password_hash=None. If the user previously
    # signed in with Google and now wants an email+password login too, set
    # the password on the existing account instead of showing "already registered".
    from app.core.models import User
    existing = db.query(User).filter(User.email == email.strip().lower()).first()
    if existing and not existing.password_hash:
        existing.password_hash = auth_service.hash_password(password)
        db.commit()
        user = existing
    else:
        user = auth_service.create_user(db, email, password)
    if not user:
        return templates.TemplateResponse("signup.html", {**ctx, "error": "כתובת האימייל כבר רשומה במערכת"})

    # Welcome coins + notification (real auditable points, not fake badges).
    loyalty_service.add_points(db, user, 50, "signup")
    notification_service.notify_user(
        db, user.id, "ברוכים הבאים! 🎉",
        "קיבלתם 50 מטבעות הרשמה! צברו עוד מטבעות על קליקים, שמירת מוצרים והתראות מחיר.",
        link="/personal-area",
    )

    _send_verification_email(request, db, user)
    request.session["user_id"] = user.id
    request.session["user_email"] = user.email
    request.session["user_points"] = user.points or 0
    return RedirectResponse("/personal-area", status_code=303)


def _send_verification_email(request: Request, db: Session, user: User):
    token = auth_service.generate_verification_token(user)
    verify_url = f"{settings.site_url}/verify-email?token={token}"
    email_service.send_verification_email(user.email, verify_url)
    auth_service.mark_verification_email_sent(db, user)


@app.get("/verify-email")
def verify_email(request: Request, token: str = "", db: Session = Depends(get_db)):
    user = auth_service.verify_email_token(db, token)
    if user:
        # +30 coins for verifying — audited by loyalty_service.
        loyalty_service.add_points(db, user, 30, "email_verified")
        notification_service.notify_user(
            db, user.id, "האימייל אומת ✅", "קיבלתם 30 מטבעות על אימות כתובת האימייל!",
        )
    return templates.TemplateResponse("verify_email.html", {"request": request, "success": user is not None})


@app.post("/api/resend-verification")
@limiter.limit("3/minute")
def resend_verification(request: Request, db: Session = Depends(get_db)):
    user = auth_service.get_current_user(request, db)
    if not user:
        return JSONResponse({"status": "error", "message": "יש להתחבר"}, status_code=401)
    if user.email_verified:
        return JSONResponse({"status": "ok", "message": "כתובת האימייל כבר מאומתת"})
    if not auth_service.can_resend_verification(user):
        return JSONResponse({"status": "error", "message": "כבר נשלח אימייל לאחרונה — נסה שוב בעוד דקה"}, status_code=429)

    _send_verification_email(request, db, user)
    return JSONResponse({"status": "ok", "message": "אימייל אימות נשלח מחדש!"})


@app.get("/login")
def login_page(request: Request):
    # google_login redirects here with notice=google_disabled when the button
    # is visible but OAuth creds aren't configured yet — show a clear message
    # instead of silently bouncing the user back to the form.
    notice = None
    if request.query_params.get("notice") == "google_disabled":
        notice = "ההתחברות עם Google עדיין לא הופעלה באתר (יש להגדיר את מפתחות ה-OAuth בדף ההגדרות). אפשר להיכנס עם אימייל וסיסמה."
    return templates.TemplateResponse("login.html", {
        "request": request, "error": None, "notice": notice,
        "csrf_token": csrf_service.generate_csrf_token(),
        "google_oauth_enabled": GOOGLE_OAUTH_ENABLED,
    })


@app.post("/login")
@limiter.limit("10/minute")
def login_submit(request: Request, email: str = Form(...), password: str = Form(...), csrf_token: str = Form(""), db: Session = Depends(get_db)):
    ctx = {"request": request, "csrf_token": csrf_service.generate_csrf_token(), "google_oauth_enabled": GOOGLE_OAUTH_ENABLED}
    if not csrf_service.verify_csrf_token(csrf_token):
        return templates.TemplateResponse("login.html", {**ctx, "error": "הטופס פג תוקף, נסה שוב"}, status_code=400)

    client_ip = request.client.host if request.client else "unknown"
    if brute_force_guard.is_locked_out(client_ip, email):
        return templates.TemplateResponse("login.html", {**ctx, "error": "יותר מדי ניסיונות כושלים. נסה שוב בעוד 15 דקות."}, status_code=429)

    user = auth_service.authenticate_user(db, email, password)
    if not user:
        brute_force_guard.record_failed_attempt(client_ip, email)
        return templates.TemplateResponse("login.html", {**ctx, "error": "אימייל או סיסמה שגויים"})

    brute_force_guard.clear_attempts(client_ip, email)
    request.session["user_id"] = user.id
    request.session["user_email"] = user.email
    request.session["user_points"] = user.points or 0
    return RedirectResponse("/personal-area", status_code=303)


@app.post("/logout")
def logout(request: Request):
    """Logout as POST (logout-CSRF guard) — same reason as admin/logout."""
    request.session.clear()
    return RedirectResponse("/")


# --- Google Sign-In ---
@app.get("/auth/google/login")
@limiter.limit("15/minute")
async def google_login(request: Request):
    if not GOOGLE_OAUTH_ENABLED:
        # The button is always visible now; if creds aren't configured, tell
        # the user honestly instead of a silent bounce.
        return RedirectResponse("/login?notice=google_disabled", status_code=303)
    redirect_uri = f"{settings.site_url}/auth/google/callback"
    return await oauth.google.authorize_redirect(request, redirect_uri)


@app.get("/auth/google/callback")
async def google_callback(request: Request, db: Session = Depends(get_db)):
    if not GOOGLE_OAUTH_ENABLED:
        return RedirectResponse("/login")
    try:
        token = await oauth.google.authorize_access_token(request)
        userinfo = token.get("userinfo") or await oauth.google.parse_id_token(request, token)
    except Exception:
        logger.exception("Google OAuth callback failed")
        return RedirectResponse("/login")

    if not userinfo or not userinfo.get("email"):
        return RedirectResponse("/login")

    user = auth_service.get_or_create_google_user(
        db,
        google_sub=userinfo.get("sub", ""),
        email=userinfo["email"],
        email_verified_by_google=bool(userinfo.get("email_verified")),
    )
    if not user.is_active:
        return RedirectResponse("/login")

    if not loyalty_service.user_earned_reason_before(db, user.id, "google_signup"):
        loyalty_service.add_points(db, user, 50, "google_signup")
    request.session["user_id"] = user.id
    request.session["user_email"] = user.email
    request.session["user_points"] = user.points or 0
    return RedirectResponse("/", status_code=303)



SORT_OPTIONS = {
    "newest": Product.last_updated.desc(),
    "price_asc": Product.price.asc(),
    "price_desc": Product.price.desc(),
    "rating": Product.rating.desc(),
}
PAGE_SIZE = 24

# Slider scale shared with the frontend (search.html hardcodes the max);
# histogram buckets must align so bars and handle positions agree.
PRICE_CAP = 50000.0
PRICE_BUCKETS = 10


def _build_price_histogram(products, max_price_cap: float = PRICE_CAP, buckets: int = PRICE_BUCKETS):
    """Small price-distribution histogram for the filter sidebar (the bars
    above the dual range slider). Buckets span 0..cap and everything above
    the cap falls into the last bucket, so the bars always line up with the
    slider's scale. pct is normalized to the tallest bucket (tallest bar = 100%).

    Returns [{lo, hi, label, count, pct}] or [] when there's nothing to plot.
    """
    if not products:
        return []
    step = max_price_cap / buckets
    counts = [0] * buckets
    for p in products:
        price = p.price or 0
        idx = min(int(price / step), buckets - 1)
        counts[idx] += 1
    top = max(counts) or 1
    out = []
    for i, c in enumerate(counts):
        lo = int(i * step)
        hi = int((i + 1) * step) if i < buckets - 1 else int(max_price_cap)
        out.append({
            "lo": lo,
            "hi": hi,
            "label": f"₪{lo}",
            "count": c,
            "pct": round(c / top * 100, 1),
        })
    return out


def _build_price_stats(products) -> dict | None:
    """Quick average + median for the current category/query — shown as a
    one-liner above the histogram so users understand the price landscape at
    a glance: 'ממוצע ₪285 · רוב המוצרים עד ₪500'."""
    if not products:
        return None
    prices = sorted(p.price or 0 for p in products)
    n = len(prices)
    avg = round(sum(prices) / n)
    if n % 2 == 0:
        median = round((prices[n // 2 - 1] + prices[n // 2]) / 2)
    else:
        median = round(prices[n // 2])
    # "most products up to" = the 75th percentile
    p75_idx = max(0, int(n * 0.75) - 1)
    most_up_to = round(prices[p75_idx])
    return {"avg": avg, "median": median, "most_up_to": most_up_to, "count": n}


@app.get("/feed")
def fresh_feed(request: Request, db: Session = Depends(get_db)):
    """'דילים טריים מהספקים' — the latest imports grouped by source, so
    visitors see the live pipeline at work instead of only the ranked
    homepage. Also serves as a transparency page."""
    from sqlalchemy import func

    by_source_rows = (
        db.query(Product.source_adapter, func.count(Product.id), func.max(Product.last_updated))
        .filter(Product.is_active == True)  # noqa: E712
        .group_by(Product.source_adapter)
        .all()
    )
    fresh = (
        db.query(Product)
        .filter(Product.is_active == True, Product.is_verified == True)  # noqa: E712
        .order_by(Product.last_updated.desc())
        .limit(30)
        .all()
    )
    return templates.TemplateResponse("feed.html", {
        "request": request, "fresh": fresh,
        "by_source": by_source_rows,
        "total_fresh": len(fresh),
    })


@app.get("/")
def home(
    request: Request,
    category: str | None = None,
    sort: str = "newest",
    page: int = 1,
    db: Session = Depends(get_db),
):
    page = max(page, 1)
    sort_clause = SORT_OPTIONS.get(sort, SORT_OPTIONS["newest"])

    query = db.query(Product).filter(Product.is_active == True, Product.is_verified == True)  # noqa: E712
    if category:
        cat = category.strip()
        query = query.filter(Product.category.ilike(f"%{cat}%"))

    total_count = query.count()
    products = (
        query.order_by(sort_clause)
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
        .all()
    )
    enrich_products_for_home(products, db)

    # "Recently viewed" — reuses ProductView data that was already being
    # recorded on every product-page visit but never surfaced anywhere.
    # Amazon/AliExpress-style personalization at zero extra tracking cost.
    session_id = request.cookies.get("session_id", "guest")
    recently_viewed_ids = [
        row[0] for row in
        db.query(ProductView.product_id)
        .filter(ProductView.session_id == session_id)
        .order_by(ProductView.timestamp.desc())
        .limit(10)
        .all()
    ]
    recently_viewed = []
    if recently_viewed_ids:
        seen = set()
        rv_products = db.query(Product).filter(Product.id.in_(recently_viewed_ids)).all()
        by_id = {p.id: p for p in rv_products}
        for pid in recently_viewed_ids:
            if pid in by_id and pid not in seen:
                recently_viewed.append(by_id[pid])
                seen.add(pid)

    # Marketing popup (latest unread broadcast, once per browser) + ads.
    popup = notification_service.latest_popup(db)
    home_ads = ads_service.get_active_for_position(db, "home_top")
    side_ads = ads_service.get_active_for_position(db, "home_side")

    # דיל היום — hero slot driven by the DealOfTheDayAgent (real signals).
    deal_ranks = deal_of_day.pick(db, limit=1)
    deal = deal_ranks[0] if deal_ranks else None
    deal_hook = deal_of_day.hook(deal) if deal else None

    # Hot-deals ticker — the 10 hottest active products (real catalog rows,
    # no fabricated numbers), reused on the home page only.
    ticker_products = (
        db.query(Product)
        .filter(Product.is_active == True, Product.is_verified == True)  # noqa: E712
        .order_by(Product.rating.desc(), Product.review_count.desc())
        .limit(10)
        .all()
    )

    return templates.TemplateResponse("index.html", {
        "request": request,
        "products": products,
        "categories": CATEGORIES,
        "active_category": category,
        "active_sort": sort,
        "page": page,
        "has_next_page": (page * PAGE_SIZE) < total_count,
        "has_prev_page": page > 1,
        "total_count": total_count,
        "recently_viewed": recently_viewed[:8],
        "banner": {"title": "סמארטשופ", "subtitle": "בורסת הדילים — נבחרים על ידי הצוות שלנו"},
        "category_thumbnails": _get_category_thumbnails(db),
        "affiliate_disclosure": "האתר כולל קישורי שותפים (Affiliate). אנו עשויים לקבל עמלה מרכישות דרך הקישורים שלנו, ללא עלות נוספת עבורך.",
        "site_url": settings.site_url,
        "popup": popup,
        "home_ads": home_ads,
        "side_ads": side_ads,
        "deal": deal,
        "deal_hook": deal_hook,
        "ticker_products": ticker_products,
    })


@app.get("/search")
def search(
    request: Request,
    q: str = "",
    page: int = 1,
    category: str = "",
    min_price: str = "",
    max_price: str = "",
    min_rating: str = "",
    sort: str = "newest",
    db: Session = Depends(get_db),
):
    """Smart search — understands natural language.

    Queries like "מתנה לילד גיל 5 עד 100 שקל" go through the
    SmartSearchAgent (LLM intent parsing when GOOGLE_API_KEY is set,
    Hebrew/English heuristics otherwise), which returns products WITH a
    human-readable reason each one was matched. Plain keyword queries fall
    back to the fast LIKE path.

    AliExpress-style filters (category, price range, min rating, sort) are
    applied on top of whichever retrieval path matched.
    """
    page = max(page, 1)
    q = q.strip()
    # Filter values arrive as strings (HTML forms submit empty fields as ""),
    # so coerce defensively — float("") would raise a 422 otherwise.
    def _to_float(raw: str, default: float = 0.0) -> float:
        try:
            return float(raw) if raw not in ("", None) else default
        except (TypeError, ValueError):
            return default

    min_price_f = _to_float(min_price)
    max_price_f = _to_float(max_price)
    min_rating_f = _to_float(min_rating)

    products, total_count, search_reasons, smart = [], 0, {}, False
    if q:
        # Natural-language heuristic: queries containing a gift/age/budget
        # signal (or just longer than 3 words with spaces) get the smart
        # path; short single keywords use the fast LIKE scan.
        is_natural = (
            len(q.split()) >= 3
            or any(k in q for k in ("מתנה", "מחפש", "מחפשת", "gift", "עד ", "מתחת ל", "גיל"))
        )
        if is_natural:
            results = smart_search.search(db, q, limit=PAGE_SIZE * 3)
            products = [r["product"] for r in results]
            search_reasons = {r["product"].id: r["reason"] for r in results}
            smart = True
        else:
            like_pattern = f"%{q}%"
            query = (
                db.query(Product)
                .filter(Product.is_active == True, Product.is_verified == True)  # noqa: E712
                .filter(
                    Product.name.ilike(like_pattern)
                    | Product.description.ilike(like_pattern)
                    | Product.category.ilike(like_pattern)
                )
            )
            products = query.all()
            enrich_products_for_home(products, db)
    else:
        # No keyword: allow browsing the whole verified catalog with filters.
        products = (
            db.query(Product)
            .filter(Product.is_active == True, Product.is_verified == True)  # noqa: E712
            .all()
        )
        enrich_products_for_home(products, db)

    # --- Filters (AliExpress-style) ---
    # Category matching: uses a fuzzy contains check so "אלקטרוניקה"
    # matches products tagged "אלקטרוניקה" exactly, while also
    # catching mis-typed or closely related categories.
    def _cat_ok(p):
        if not category:
            return True
        p_cat = (p.category or '').strip()
        cat = category.strip()
        # Exact match first, then fuzzy (category inside product category or vice versa)
        return p_cat == cat or cat in p_cat or p_cat in cat

    def _matches(p):
        if not _cat_ok(p):
            return False
        price = p.price or 0
        if min_price_f and price < min_price_f:
            return False
        if max_price_f and price > max_price_f:
            return False
        if min_rating_f and (p.rating or 0) < min_rating_f:
            return False
        return True

    filtered = [p for p in products if _matches(p)]
    sort_key = {
        "newest": lambda p: (p.last_updated or datetime.datetime.min).timestamp(),
        "price_asc": lambda p: p.price or 0,
        "price_desc": lambda p: -(p.price or 0),
        "rating": lambda p: -(p.rating or 0),
    }.get(sort, lambda p: (p.last_updated or datetime.datetime.min).timestamp())
    filtered.sort(key=sort_key)

    total_count = len(filtered)
    start = (page - 1) * PAGE_SIZE
    products = filtered[start:start + PAGE_SIZE]

    # --- Cross-site live search ("search ALL registered sites, not just
    # our catalog") ---
    # When the local catalog matched FEW results, ask every supplier
    # adapter with official API credentials for live listings of this query
    # (concurrent, timeout-capped — never blocks the page). This is what
    # makes search genuinely span every registered site, not just the
    # products already imported into our catalog. These are shown as
    # clearly-labeled "live from <source>" cards with the supplier's own
    # price and link. No fabrication: results are only what suppliers
    # actually returned, and none of them are logged as affiliate clicks.
    live_results = []
    if q and total_count < 6:
        from app.services import live_search_service
        live_results = live_search_service.live_search(q, limit_per_source=5, max_total=12)

    return templates.TemplateResponse("search.html", {
        "request": request,
        "query": q,
        "products": products,
        "search_reasons": search_reasons,
        "smart": smart,
        "total_count": total_count,
        "page": page,
        "has_next_page": (page * PAGE_SIZE) < total_count,
        "has_prev_page": page > 1,
        "categories": CATEGORIES,
        "active_category": category,
        "min_price": min_price_f,
        "max_price": max_price_f,
        "min_rating": min_rating_f,
        "active_sort": sort,
        "has_filters": bool(category or min_price_f or max_price_f or min_rating_f),
        "live_results": live_results,
        "category_thumbnails": _get_category_thumbnails(db),
        # Histogram shows the whole category/query distribution (price and
        # rating filters excluded) so users see where most products sit even
        # while they're narrowing the range.
        "price_hist": _build_price_histogram([p for p in products if _cat_ok(p)]),
        "price_stats": _build_price_stats([p for p in products if _cat_ok(p)]),
    })


@app.get("/api/smart-search")
@limiter.limit("30/minute")
def smart_search_api(request: Request, q: str = "", db: Session = Depends(get_db)):
    """JSON version of the smart search — powers the live chat widget and
    any frontend that wants typed-as-you-go natural-language results."""
    q = q.strip()
    if len(q) < 2:
        return JSONResponse({"results": []})
    results = smart_search.search(db, q, limit=8)
    return JSONResponse({
        "results": [
            {
                "id": r["product"].id,
                "name": r["product"].name,
                "price": r["product"].price,
                "image_url": r["product"].image_url,
                "reason": r["reason"],
            }
            for r in results
        ]
    })


@app.post("/api/image-search")
@limiter.limit("10/minute")
async def image_search(request: Request, image: UploadFile = File(...), db: Session = Depends(get_db)):
    """Visual search — 'חיפוש לפי תמונה'. Accepts an uploaded image, runs
    perceptual-hash matching against the catalog and returns the most
    similar products. 10/min keeps a scraper from re-downloading our whole
    catalog through this endpoint."""
    data = await image.read()
    if len(data) > 5 * 1024 * 1024:
        return JSONResponse({"products": [], "message": "התמונה גדולה מדי (עד 5MB)"})
    if not data:
        return JSONResponse({"products": [], "message": "התמונה ריקה"})
    from starlette.concurrency import run_in_threadpool
    from app.services import image_search_service
    products = await run_in_threadpool(image_search_service.search_by_image, data, db)
    return JSONResponse({"products": products})


@app.get("/category/{category_name}")
def category_page(request: Request, category_name: str, sort: str = "newest", page: int = 1, db: Session = Depends(get_db)):
    """Dedicated AliExpress-style category landing page with breadcrumbs,
    product count, sort bar, sidebar filters, and clean grid."""
    page = max(page, 1)
    sort_clause = SORT_OPTIONS.get(sort, SORT_OPTIONS["newest"])

    # Fuzzy match: find all products whose category contains or is contained
    # by the requested category (handles typos, subcategories, etc.).
    name_lower = category_name.strip()
    all_active = db.query(Product).filter(Product.is_active == True, Product.is_verified == True).all()  # noqa: E712
    matching = [p for p in all_active if name_lower in (p.category or '').strip() or (p.category or '').strip() in name_lower]

    # If no fuzzy matches, try exact match
    if not matching:
        matching = db.query(Product).filter(
            Product.is_active == True, Product.is_verified == True,  # noqa: E712
            Product.category == category_name
        ).all()

    total_count = len(matching)
    sort_key = {
        "newest": lambda p: (p.last_updated or datetime.datetime.min).timestamp(),
        "price_asc": lambda p: p.price or 0,
        "price_desc": lambda p: -(p.price or 0),
        "rating": lambda p: -(p.rating or 0),
    }.get(sort, lambda p: (p.last_updated or datetime.datetime.min).timestamp())
    matching.sort(key=sort_key)

    start = (page - 1) * PAGE_SIZE
    products = matching[start:start + PAGE_SIZE]
    enrich_products_for_home(products, db)

    # Breadcrumb: home > category
    breadcrumbs = [
        {"label": "דף הבית", "url": "/"},
        {"label": category_name, "url": None},
    ]

    return templates.TemplateResponse("search.html", {
        "request": request,
        "query": "",
        "products": products,
        "search_reasons": {},
        "smart": False,
        "total_count": total_count,
        "page": page,
        "has_next_page": (page * PAGE_SIZE) < total_count,
        "has_prev_page": page > 1,
        "categories": CATEGORIES,
        "active_category": category_name,
        "min_price": 0,
        "max_price": 0,
        "min_rating": 0,
        "active_sort": sort,
        "has_filters": True,
        "live_results": [],
        "breadcrumbs": breadcrumbs,
        "is_category_page": True,
        "category_name": category_name,
        "category_thumbnails": _get_category_thumbnails(db),
        "price_hist": _build_price_histogram(matching),
        "price_stats": _build_price_stats(matching),
    })


@app.get("/api/search-suggest")
@limiter.limit("30/minute")
def search_suggest(request: Request, q: str = "", db: Session = Depends(get_db)):
    """Powers the autocomplete dropdown under the nav search box.
    Uses Meilisearch for typo-tolerant instant results; falls back to
    SQL ILIKE when Meilisearch is down."""
    q = q.strip()
    if len(q) < 2:
        return JSONResponse({"results": []})

    # Try Meilisearch first — typo-tolerant, instant, handles Hebrew well
    meili_result = meili_search_service.search_instant(query=q, hits_per_page=6)
    if meili_result.get("hits"):
        return JSONResponse({
            "results": [
                {"id": h["id"], "name": h["name"], "price": h.get("price"),
                 "image_url": h.get("image_url"), "_source": "meilisearch"}
                for h in meili_result["hits"]
            ]
        })

    # Fallback: SQL ILIKE
    like_pattern = f"%{q}%"
    products = (
        db.query(Product)
        .filter(Product.is_active == True, Product.is_verified == True)  # noqa: E712
        .filter(Product.name.ilike(like_pattern))
        .limit(6)
        .all()
    )
    return JSONResponse({
        "results": [
            {"id": p.id, "name": p.name, "price": p.price, "image_url": p.image_url}
            for p in products
        ]
    })


@app.get("/api/instant-search")
@limiter.limit("60/minute")
def instant_search(
    request: Request,
    q: str = "",
    page: int = 1,
    category: str = "",
    source: str = "",
    min_price: str = "",
    max_price: str = "",
    min_rating: str = "",
    sort: str = "relevance",
    trending_only: str = "",
    in_stock_only: str = "",
    db: Session = Depends(get_db),
):
    """Instant faceted search backed by Meilisearch — typo-tolerant,
    filterable, sortable, with facet counts. Falls back to SQL search
    when Meilisearch is unavailable.

    This is the endpoint that powers the AliExpress-style instant
    search experience: type a few letters, see results immediately,
    refine with facets without a page reload."""
    q = q.strip()
    page = max(page, 1)

    # Parse numeric filters
    _min_price = float(min_price) if min_price else None
    _max_price = float(max_price) if max_price else None
    _min_rating = float(min_rating) if min_rating else None
    _trending_only = trending_only == "1"
    _in_stock_only = in_stock_only == "1"

    # Try Meilisearch first
    result = meili_search_service.search_instant(
        query=q,
        page=page,
        hits_per_page=24,
        category=category,
        source=source,
        min_price=_min_price,
        max_price=_max_price,
        min_rating=_min_rating,
        sort=sort,
        trending_only=_trending_only,
        in_stock_only=_in_stock_only,
    )

    if result.get("hits"):
        return JSONResponse({
            "hits": result["hits"],
            "total": result["total"],
            "facets": result.get("facets", {}),
            "page": page,
            "processing_time_ms": result.get("processing_time_ms", 0),
            "engine": "meilisearch",
        })

    # Fallback: SQL search with ILIKE + filters
    from sqlalchemy import desc, asc
    query = db.query(Product).filter(Product.is_active == True)
    if q:
        like_pattern = f"%{q}%"
        query = query.filter(Product.name.ilike(like_pattern))
    if category:
        query = query.filter(Product.category.ilike(f"%{category}%"))
    if source:
        query = query.filter(Product.source_adapter == source)
    if _min_price is not None:
        query = query.filter(Product.price >= _min_price)
    if _max_price is not None:
        query = query.filter(Product.price <= _max_price)
    if _min_rating is not None and _min_rating > 0:
        query = query.filter(Product.rating >= _min_rating)
    if _trending_only:
        query = query.filter(Product.is_trending == True)
    if _in_stock_only:
        query = query.filter(Product.stock_count > 0)

    sort_map = {
        "price_asc": asc(Product.price),
        "price_desc": desc(Product.price),
        "rating": desc(Product.rating),
        "newest": desc(Product.last_updated),
    }
    order = sort_map.get(sort, desc(Product.import_score))
    query = query.order_by(order)

    total = query.count()
    offset = (page - 1) * 24
    products = query.offset(offset).limit(24).all()

    hits = [{
        "id": p.id, "name": p.name or "", "price": float(p.price or 0),
        "image_url": p.image_url or "", "category": p.category or "",
        "source_adapter": p.source_adapter or "", "supplier_name": p.supplier_name or "",
        "rating": float(p.rating or 0), "review_count": p.review_count or 0,
        "discount_percent": round((1 - p.price / p.local_market_price) * 100, 1)
        if p.local_market_price and p.price and p.local_market_price > p.price else 0,
        "is_trending": bool(p.is_trending), "is_verified": bool(p.is_verified),
        "stock_count": p.stock_count or 0, "ai_summary": p.ai_summary or "",
        "coupon_code": p.coupon_code or "", "shipping_days": p.shipping_days or 0,
    } for p in products]

    return JSONResponse({
        "hits": hits,
        "total": total,
        "facets": {},
        "page": page,
        "processing_time_ms": 0,
        "engine": "sql",
    })


@app.get("/product/{product_id}")
def product_page(product_id: int, request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        return RedirectResponse("/")

    db.add(ProductView(session_id=request.cookies.get("session_id", "guest"), product_id=product_id))
    db.commit()

    # Interest-driven catalog expansion: a product-page view is a real
    # interest signal, so pull related products from official-API suppliers
    # in the background (throttled + timeout-capped, deduped by matcher).
    # BackgroundTasks already runs after the response on a separate thread,
    # so no extra thread is needed. Opens its OWN DB session because the
    # request-scoped one is closed by then; any failure is logged, never
    # propagated back to the page.
    def _interest_pull_task():
        from app.services import interest_pull_service
        db2 = SessionLocal()
        try:
            p = db2.query(Product).filter(Product.id == product_id).first()
            if p:
                interest_pull_service.pull_related_products(
                    db2, p, request.cookies.get("session_id", "guest")
                )
        except Exception:
            logger.exception("Interest pull failed for product id=%s", product_id)
        finally:
            db2.close()

    background_tasks.add_task(_interest_pull_task)

    user = auth_service.get_current_user(request, db)
    is_favorited = False
    if user:
        is_favorited = db.query(ProductFavorite).filter_by(user_id=user.id, product_id=product_id).first() is not None

    all_products = db.query(Product).filter(Product.is_active == True).limit(100).all()  # noqa: E712
    viewed = db.query(Product).join(
        ProductView, ProductView.product_id == Product.id
    ).filter(ProductView.session_id == request.cookies.get("session_id", "guest")).limit(5).all()
    recommended_ids = recommender.get_recommendations(viewed or [product], all_products)
    recommended = [p for p in all_products if p.id in recommended_ids and p.id != product_id]

    # Real user reviews (aggregate rating + list) — no fabricated counts.
    reviews = (
        db.query(ProductReview, User)
        .join(User, User.id == ProductReview.user_id)
        .filter(ProductReview.product_id == product_id)
        .order_by(ProductReview.created_at.desc())
        .limit(20)
        .all()
    )
    avg_rating = 0
    if reviews:
        avg_rating = round(sum(r.rating for r, _ in reviews) / len(reviews), 1)
    my_review = None
    if user:
        my_review = db.query(ProductReview).filter_by(user_id=user.id, product_id=product_id).first()

    product_ads = ads_service.get_active_for_position(db, "product_banner")
    forecast = price_forecast.forecast(db, product_id)
    insights = review_insights.summarize(db, product_id)
    similar_deals = deal_of_day.pick(db, limit=4)

    # Smart Bundles — complementary products at a discounted bundle price.
    # Uses predefined templates per category first; falls back to Gemini AI
    # when the product's category has no template.
    from app.services import smart_bundle_service
    smart_bundle = smart_bundle_service.get_bundle(db, product)
    if not smart_bundle:
        smart_bundle = smart_bundle_service.get_ai_bundle(db, product)

    # JSON-LD needs a valid-until date; computed here (not in the template)
    # so Jinja doesn't need access to the datetime module.
    price_valid_until = (datetime.datetime.utcnow() + datetime.timedelta(days=30)).strftime("%Y-%m-%d")

    # Gallery: main image first, then any extra gallery_images, de-duplicated.
    gallery = [product.image_url] if product.image_url else []
    for img in (product.gallery_images or []):
        if img and img not in gallery:
            gallery.append(img)
    gallery = gallery[:6]  # keep the page light

    return templates.TemplateResponse("product_page.html", {
        "request": request,
        "product": product,
        "recommended": recommended,
        "sentiment_pros": product.pros or [],
        "sentiment_cons": product.cons or [],
        "site_url": settings.site_url,
        "user": user,
        "is_favorited": is_favorited,
        "reviews": reviews,
        "avg_rating": avg_rating,
        "my_review": my_review,
        "product_ads": product_ads,
        "forecast": forecast,
        "insights": insights,
        "similar_deals": [d["product"] for d in similar_deals],
        "price_valid_until": price_valid_until,
        "gallery": gallery,
        "smart_bundle": smart_bundle,
    })


@app.get("/go/{product_id}")
@limiter.limit("30/minute")
def go_to_affiliate(request: Request, product_id: int, ref: str | None = None, db: Session = Depends(get_db)):
    """Every outbound affiliate click funnels through here so it's logged
    exactly once, from one place, regardless of which page linked to it."""
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        return RedirectResponse("/")

    target_url, source = choose_best_target(product)
    log_click(
        db, product_id=product_id, source=source,
        user_ip=request.client.host if request.client else "unknown",
        session_id=request.cookies.get("session_id"),
        ref=ref or settings.default_affiliate_ref,
    )

    # +1 coin per click, capped at 10/day so it can't be farmed by
    # hammering the button — real, auditable points.
    user = auth_service.get_current_user(request, db)
    if user and loyalty_service.clicks_today(db, user.id) < 10:
        loyalty_service.add_points(db, user, 1, "click")
        request.session["user_points"] = user.points or 0
    return RedirectResponse(target_url)


@app.get("/api/price-war/{product_id}")
@limiter.limit("20/minute")
def price_war(request: Request, product_id: int, db: Session = Depends(get_db)):
    # Cache price-war results for 10 minutes — suppliers don't change prices
    # every second, and the slow cross-adapter calls burn the free-tier DB.
    cache_key = f"price_war:{product_id}"
    cached = cache_service.get(cache_key)
    if cached:
        return JSONResponse(cached)
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        result = {"status": "unavailable", "message": "מוצר לא נמצא"}
        cache_service.set(cache_key, result, ttl_seconds=600)
        return JSONResponse(result)
    result = price_service.refresh_own_offer(db, product)
    cross = price_service.find_cross_supplier_matches(product)
    if cross:
        result.setdefault("offers", []).extend(cross)
        result["message"] = result.get("message", "") + " (כולל התאמות משוערות מספקים נוספים)"
    cache_service.set(cache_key, result, ttl_seconds=600)
    return JSONResponse(result)


@app.post("/api/chat")
@limiter.limit("15/minute")
def chat(request: Request, query: str = Form(...), mode: str = Form("standard"), db: Session = Depends(get_db)):
    # Cache identical queries for 5 minutes — free-tier AI quota is limited,
    # and this saves real money while making repeated questions instant.
    cache_key = f"chat:{query.strip().lower()}:{mode}"
    cached = cache_service.get(cache_key)
    if cached:
        return JSONResponse({"answer": cached, "cached": True})
    answer = chatbot.ask(query, db, mode=mode)
    if answer and len(answer) > 10:
        cache_service.set(cache_key, answer, ttl_seconds=300)
    return JSONResponse({"answer": answer})


@app.post("/api/track-view/{product_id}")
@limiter.limit("60/minute")
def track_view(product_id: int, request: Request, db: Session = Depends(get_db)):
    db.add(ProductView(session_id=request.cookies.get("session_id", "guest"), product_id=product_id))
    db.commit()
    return JSONResponse({"status": "ok"})


@app.get("/api/social-proof")
@limiter.limit("60/minute")
def social_proof(request: Request, db: Session = Depends(get_db)):
    """Real count from the last hour — no randomized padding. If there's
    genuinely no recent activity, the frontend should simply not show the
    bubble rather than get a fabricated number.

    Also returns a sample saved_amount from the last-hour's orders so the
    frontend can say "משתמש חסך ₪120 בקנייה חכמה" with REAL numbers."""
    since = datetime.datetime.utcnow() - datetime.timedelta(hours=1)
    count = db.query(AffiliateClick).filter(AffiliateClick.created_at > since).count()
    # Real saved amount: sum of (local_market_price - total_price) for recent orders
    saved_row = db.query(Order).filter(Order.created_at > since, Order.status.in_(["Shipped", "Ordered"])).all()
    saved_amount = 0.0
    if saved_row:
        for o in saved_row:
            product = db.query(Product).filter(Product.id == o.product_id).first()
            if product and product.local_market_price and o.total_price:
                diff = product.local_market_price - o.total_price
                if diff > 0:
                    saved_amount += diff
    return JSONResponse({"count": count, "saved_amount": round(saved_amount, 2)})


@app.get("/personal-area")
def personal_area(request: Request, db: Session = Depends(get_db)):
    user = auth_service.get_current_user(request, db)
    if not user:
        return RedirectResponse("/login")

    watchlist = (
        db.query(PriceAlert, Product)
        .join(Product, Product.id == PriceAlert.product_id)
        .filter(PriceAlert.user_id == user.id)
        .all()
    )
    favorites = (
        db.query(ProductFavorite, Product)
        .join(Product, Product.id == ProductFavorite.product_id)
        .filter(ProductFavorite.user_id == user.id)
        .order_by(ProductFavorite.created_at.desc())
        .all()
    )
    orders = (
        db.query(Order)
        .filter(Order.user_id == user.id)
        .order_by(Order.created_at.desc())
        .all()
    )
    session_id = request.cookies.get("session_id", "guest")
    click_history = (
        db.query(AffiliateClick, Product)
        .join(Product, Product.id == AffiliateClick.product_id)
        .filter(AffiliateClick.session_id == session_id)
        .order_by(AffiliateClick.created_at.desc())
        .limit(20)
        .all()
    )
    coach_actions = loyalty_coach.next_actions(db, user)
    next_rank = loyalty_coach.rank_progress(user)
    request.session["user_points"] = user.points or 0

    # Every active coupon from all registered suppliers — shown to every
    # logged-in user in their personal area (the "all my coupons" view).
    from app.services import coupon_service
    coupons = coupon_service.coupons_for_display(db, limit=20)

    # The admin panel link appears ONLY on the personal area page of the
    # account whose email matches the configured system admin — never in
    # the global navbar for everyone. Clicking it still requires the admin
    # password (/admin/login), so a shared account alone can't enter.
    is_site_admin = bool(
        settings.admin_email
        and user.email.strip().lower() == settings.admin_email.strip().lower()
    )

    # Monthly savings: compare this month's order prices against local market
    # prices to show the user how much they saved by shopping through us.
    now = datetime.datetime.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    monthly_orders = (
        db.query(Order, Product)
        .join(Product, Product.id == Order.product_id)
        .filter(
            Order.user_id == user.id,
            Order.created_at >= month_start,
            Product.local_market_price.isnot(None),
            Product.local_market_price > 0,
        )
        .all()
    )
    monthly_saved = 0.0
    monthly_market = 0.0
    for order, product in monthly_orders:
        if order.total_price and product.local_market_price:
            saved = max(0.0, product.local_market_price - order.total_price)
            monthly_saved += saved
            monthly_market += product.local_market_price
    # Progress: what % of market price did they save? Cap at a reasonable ceiling.
    monthly_savings_pct = round((monthly_saved / monthly_market * 100), 1) if monthly_market > 0 else 0

    return templates.TemplateResponse("personal_area.html", {
        "request": request, "user": user, "watchlist": watchlist,
        "favorites": favorites, "orders": orders, "click_history": click_history,
        "coach_actions": coach_actions, "next_rank": next_rank,
        "coupons": coupons, "is_site_admin": is_site_admin,
        "monthly_saved": monthly_saved,
        "monthly_savings_pct": monthly_savings_pct,
        "monthly_order_count": len(monthly_orders),
    })


@app.post("/api/favorites/{product_id}/toggle")
def toggle_favorite(product_id: int, request: Request, db: Session = Depends(get_db)):
    user = auth_service.get_current_user(request, db)
    if not user:
        return JSONResponse({"status": "error", "message": "יש להתחבר כדי לשמור מוצרים"}, status_code=401)

    existing = db.query(ProductFavorite).filter_by(user_id=user.id, product_id=product_id).first()
    if existing:
        db.delete(existing)
        db.commit()
        return JSONResponse({"status": "ok", "favorited": False})

    db.add(ProductFavorite(user_id=user.id, product_id=product_id))
    db.commit()

    # +20 coins the FIRST time a user saves a favorite (audited, once-only).
    if not loyalty_service.user_earned_reason_before(db, user.id, "first_favorite"):
        loyalty_service.add_points(db, user, 20, "first_favorite")
        notification_service.notify_user(
            db, user.id, "מוצר נשמר! ❤️", "קיבלתם 20 מטבעות על שמירת המוצר הראשון.",
        )
    return JSONResponse({"status": "ok", "favorited": True})


@app.post("/api/price-alerts")
def create_price_alert(request: Request, product_id: int = Form(...), target_price: float = Form(...), db: Session = Depends(get_db)):
    user = auth_service.get_current_user(request, db)
    if not user:
        return JSONResponse({"status": "error", "message": "יש להתחבר כדי להגדיר התראת מחיר"}, status_code=401)
    if target_price <= 0:
        return JSONResponse({"status": "error", "message": "יש להזין מחיר יעד תקין"}, status_code=400)

    db.add(PriceAlert(user_id=user.id, product_id=product_id, target_price=target_price))
    db.commit()
    return JSONResponse({"status": "ok", "message": "התראת מחיר נשמרה!"})


@app.post("/api/orders/{order_id}/refresh-tracking")
def refresh_order_tracking(order_id: int, request: Request, db: Session = Depends(get_db)):
    """'Where is my product right now' — pulls the latest carrier scan for
    an order's tracking number via 17TRACK and shows it in the personal
    area, so the user doesn't need to leave the site to check AliExpress'
    own tracking page."""
    user = auth_service.get_current_user(request, db)
    if not user:
        return JSONResponse({"status": "error", "message": "יש להתחבר"}, status_code=401)

    order = db.query(Order).filter(Order.id == order_id, Order.user_id == user.id).first()
    if not order:
        return JSONResponse({"status": "error", "message": "הזמנה לא נמצאה"}, status_code=404)

    result = order_tracking_service.refresh_status(db, order)
    return JSONResponse(result)


@app.get("/admin")
def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    # Page route: unauthenticated admins get the pretty login page, not the
    # browser's Basic-auth dialog. API endpoints below still 401 via
    # require_admin so fetch() callers can detect an expired session.
    if not _is_admin(request):
        return RedirectResponse("/admin/login", status_code=303)
    from sqlalchemy import func
    product_count = db.query(Product).count()
    user_count = db.query(User).count()
    subscriber_count = db.query(NewsletterSubscriber).count()
    orders = db.query(Order).order_by(Order.created_at.desc()).limit(50).all()
    users = db.query(User).order_by(User.id.desc()).limit(100).all()
    products = db.query(Product).order_by(Product.buying_score.desc()).limit(8).all()

    # --- Real business stats (no fabricated numbers) ---
    click_count = db.query(AffiliateClick).count()
    coins_awarded = db.query(func.coalesce(func.sum(PointTransaction.amount), 0)).filter(PointTransaction.amount > 0).scalar()
    revenue = db.query(func.coalesce(func.sum(Order.total_price), 0)).scalar()
    order_count = db.query(Order).count()
    coupon_products = (
        db.query(Product)
        .filter(Product.coupon_code.isnot(None), Product.coupon_code != "")
        .order_by(Product.buying_score.desc())
        .limit(20)
        .all()
    )
    popups = (
        db.query(Notification)
        .filter(Notification.user_id.is_(None), Notification.is_popup == True)  # noqa: E712
        .order_by(Notification.created_at.desc())
        .limit(20)
        .all()
    )
    ads = db.query(AdPlacement).order_by(AdPlacement.created_at.desc()).limit(30).all()
    # Help-center inbox count (new messages badge on the dashboard).
    support_new_count = (
        db.query(models_module.SupportMessage)
        .filter(models_module.SupportMessage.status == "new")
        .count()
    )

    # --- Real configuration status, shown honestly in the dashboard so the
    # operator sees at a glance what's connected and what still needs the
    # .env to be filled in. ---
    smtp_connected = bool(settings.smtp_host and settings.smtp_user)
    site_url_lower = settings.site_url.lower()
    site_url_real = not any(bad in site_url_lower for bad in ("yourdomain", "localhost", "127.0.0.1", ":8000", ":5000", ":3000", ".local"))
    admin_secure = len(settings.admin_secret_key) >= 12 and "change_me" not in settings.admin_secret_key and "test" not in settings.admin_secret_key.lower()
    session_secure = len(settings.session_secret_key) >= 16 and "change_me" not in settings.session_secret_key and "test" not in settings.session_secret_key.lower()
    from app.agents import ai_gate as ai_gate_mod
    ai_degraded = ai_gate_mod.degraded_until() > time.time()
    config_checks = [
        {"ok": bool(settings.google_api_key) and not ai_degraded, "title": "שירות (Gemini)", "hint": ("GOOGLE_API_KEY מוגדר, אבל מעגל ה-AI פתוח (כשלונות חוזרים) — האתר רץ במצב fallback עד שיתאפס. " if ai_degraded else "GOOGLE_API_KEY — יוצרים ב-Google AI Studio (חינם). בלעדיו כל ה-AI במצב fallback.")},
        {"ok": smtp_connected, "title": "SMTP — אימיילים וניוזלטר", "hint": "SMTP_HOST / SMTP_USER / SMTP_PASSWORD — Brevo בחינם (300 מייל/יום) או Gmail App Password."},
        {"ok": instagram_agent.is_connected, "title": "אינסטגרם — פרסום אוטומטי", "hint": "INSTAGRAM_ACCESS_TOKEN + INSTAGRAM_ACCOUNT_ID — מדריך מלא ב-README."},
        {"ok": bool(settings.admin_email), "title": "מייל מערכת לכניסת מנהל", "hint": f"ADMIN_EMAIL — המייל שאיתו נכנסים ל-/admin (כרגע {settings.admin_email or 'לא הוגדר'})."},
        {"ok": admin_secure, "title": "סיסמת מנהל חזקה", "hint": "ADMIN_SECRET_KEY — הסיסמה הנוכחית (12345) היא ברירת מחדל. החליפו אותה מדף ההגדרות בהקדם."},
        {"ok": session_secure, "title": "מפתח session חזק", "hint": "SESSION_SECRET_KEY — מפתח חתימת הסשנים. אם הוא חלש, אסור שהוא ישמש גם כסיסמת מנהל — החליפו מדף ההגדרות."},
        {"ok": site_url_real, "title": "כתובת אתר אמיתית (SITE_URL)", "hint": "SITE_URL — הדומיין הרשמי שלכם, לא localhost. נדרש גם לאימיילים ואינסטגרם."},
    ]

    return templates.TemplateResponse("admin_dashboard.html", {
        "request": request, "product_count": product_count, "user_count": user_count,
        "subscriber_count": subscriber_count, "orders": orders, "users": users,
        "products": products, "categories": CATEGORIES,
        "adapter_status": settings.adapter_status(),
        "click_count": click_count, "coins_awarded": coins_awarded,
        "revenue": revenue, "order_count": order_count,
        "coupon_products": coupon_products, "popups": popups, "ads": ads,
        "support_new_count": support_new_count,
        "ad_positions": ads_service.POSITIONS,
        "instagram_connected": instagram_agent.is_connected,
        "smtp_connected": smtp_connected,
        "google_key_set": bool(settings.google_api_key),
        "config_checks": config_checks,
        "site_url": settings.site_url,
        "csrf_token": csrf_service.generate_csrf_token(),
    })


@app.get("/admin/reports")
def admin_reports(request: Request, db: Session = Depends(get_db)):
    """Analytics dashboard: supplier distribution, daily clicks, revenue,
    top-10 products — all from real DB queries, no fabrication."""
    if not _is_admin(request):
        return RedirectResponse("/admin/login", status_code=303)
    from sqlalchemy import func

    # --- Supplier distribution ---
    supplier_rows = (
        db.query(Product.source_adapter, func.count(Product.id))
        .filter(Product.is_active == True)
        .group_by(Product.source_adapter)
        .order_by(func.count(Product.id).desc())
        .all()
    )
    supplier_labels = [r[0] or 'ללא ספק' for r in supplier_rows]
    supplier_counts = [r[1] for r in supplier_rows]

    # --- Category pie chart ---
    category_rows = (
        db.query(Product.category, func.count(Product.id))
        .filter(Product.is_active == True, Product.category.isnot(None), Product.category != '')
        .group_by(Product.category)
        .order_by(func.count(Product.id).desc())
        .all()
    )
    category_labels = [r[0] for r in category_rows]
    category_counts = [r[1] for r in category_rows]

    # --- Daily clicks (last 30 days) ---
    thirty_days_ago = datetime.datetime.utcnow() - datetime.timedelta(days=30)
    daily_clicks_rows = (
        db.query(
            func.date(AffiliateClick.created_at).label("day"),
            func.count(AffiliateClick.id),
        )
        .filter(AffiliateClick.created_at >= thirty_days_ago)
        .group_by("day")
        .order_by("day")
        .all()
    )
    click_labels = [str(r[0]) for r in daily_clicks_rows]
    click_data = [r[1] for r in daily_clicks_rows]

    # --- Monthly revenue (last 12 months) ---
    twelve_months_ago = datetime.datetime.utcnow() - datetime.timedelta(days=365)
    monthly_rows = (
        db.query(
            func.strftime('%Y-%m', Order.created_at).label("month"),
            func.sum(Order.total_price),
        )
        .filter(Order.created_at >= twelve_months_ago)
        .group_by("month")
        .order_by("month")
        .all()
    )
    revenue_labels = [r[0] for r in monthly_rows]
    revenue_data = [round(r[1] or 0, 2) for r in monthly_rows]

    # --- Revenue ---
    total_revenue = db.query(func.coalesce(func.sum(Order.total_price), 0)).scalar()
    order_count = db.query(Order).count()
    avg_order = round(total_revenue / order_count, 2) if order_count else 0

    # --- Top-10 products (by review count + rating) ---
    top_products = (
        db.query(Product)
        .filter(Product.is_active == True)
        .order_by(Product.review_count.desc(), Product.rating.desc())
        .limit(10)
        .all()
    )

    # --- Clicks per product (for additional context) ---
    clicks_per_product = dict(
        db.query(AffiliateClick.product_id, func.count(AffiliateClick.id))
        .group_by(AffiliateClick.product_id)
        .all()
    )

    return templates.TemplateResponse("admin_reports.html", {
        "request": request,
        "supplier_labels": supplier_labels,
        "supplier_counts": supplier_counts,
        "click_labels": click_labels,
        "click_data": click_data,
        "revenue_labels": revenue_labels,
        "revenue_data": revenue_data,
        "total_revenue": total_revenue,
        "order_count": order_count,
        "avg_order": avg_order,
        "top_products": top_products,
        "clicks_per_product": clicks_per_product,
        "category_labels": category_labels,
        "category_counts": category_counts,
        "product_count": db.query(Product).filter(Product.is_active == True).count(),
        "user_count": db.query(User).count(),
        "total_clicks": db.query(AffiliateClick).count(),
    })


@app.get("/admin/reports/export-top10")
def admin_reports_export_top10(request: Request, db: Session = Depends(get_db), _auth: bool = Depends(require_admin)):
    """CSV export of the TOP-10 products table — same query as the reports
    page, just delivered as a downloadable UTF-8-BOM CSV."""
    from sqlalchemy import func
    import csv, io

    top_products = (
        db.query(Product)
        .filter(Product.is_active == True)
        .order_by(Product.buying_score.desc())
        .limit(10)
        .all()
    )
    clicks_per_product = {}
    if top_products:
        ids = [p.id for p in top_products]
        click_rows = (
            db.query(AffiliateClick.product_id, func.count(AffiliateClick.id))
            .filter(AffiliateClick.product_id.in_(ids))
            .group_by(AffiliateClick.product_id)
            .all()
        )
        clicks_per_product = {r[0]: r[1] for r in click_rows}

    buf = io.StringIO()
    buf.write('\ufeff')  # UTF-8 BOM — Excel on Windows opens Hebrew correctly
    w = csv.writer(buf)
    w.writerow(["#", "שם המוצר", "ספק", "מחיר", "דירוג", "ביקורות", "קליקים"])
    for i, p in enumerate(top_products, 1):
        w.writerow([
            i,
            p.name or "",
            p.source_adapter or "",
            p.price or 0,
            p.rating or 0,
            p.review_count or 0,
            clicks_per_product.get(p.id, 0),
        ])

    csv_bytes = buf.getvalue().encode('utf-8-sig')
    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=top10.csv"},
    )


@app.get("/admin/suppliers")
def admin_suppliers_page(request: Request, db: Session = Depends(get_db)):
    """Live supplier-status page: per-supplier connection mode (official
    API vs scraping), product counts, last pull time, pending candidates,
    and a one-click full pull test. JSON endpoint below powers auto-refresh."""
    if not _is_admin(request):
        return RedirectResponse("/admin/login", status_code=303)
    from app.services import supplier_status_service
    return templates.TemplateResponse("admin_suppliers.html", {
        "request": request,
        "statuses": supplier_status_service.get_supplier_status(db),
        "csrf_token": csrf_service.generate_csrf_token(),
        "image_cache": image_proxy_service.cache_stats(),
    })


@app.get("/admin/suppliers/api")
def admin_suppliers_api(request: Request, db: Session = Depends(get_db), _auth: bool = Depends(require_admin)):
    """JSON snapshot for the live page — cheap DB reads only, no network."""
    from app.services import supplier_status_service
    return JSONResponse({
        "statuses": supplier_status_service.get_supplier_status(db),
        "meili_stats": meili_search_service._get_service().get_stats(),
        "image_cache": image_proxy_service.cache_stats(),
    })


@app.post("/admin/reindex-meili")
@limiter.limit("5/minute")
def admin_reindex_meili(request: Request, db: Session = Depends(get_db), _auth: bool = Depends(require_admin_csrf)):
    """Full reindex: push all active products to Meilisearch. Used from
    the admin panel after first setup or after Meilisearch data loss."""
    result = meili_search_service._get_service().reindex_all(db)
    return JSONResponse(result)


@app.post("/admin/reset-meili")
@limiter.limit("3/minute")
def admin_reset_meili(request: Request, _auth: bool = Depends(require_admin_csrf)):
    """Reset Meilisearch index (delete + recreate)."""
    ok = meili_search_service._get_service().reset_index()
    return JSONResponse({"success": ok, "message": "אינדקס אופס" if ok else "שגיאה באיפוס האינדקס"})


@app.post("/admin/suppliers/pull-test/{supplier}")
@limiter.limit("10/minute")  # only authenticated admin requests count (the CSRF gate 401s before the limiter runs)
async def admin_suppliers_pull_test(request: Request, supplier: str, _auth: bool = Depends(require_admin_csrf)):
    """One-click full registration test for a single supplier: key check →
    live key test → real 1-product pull → affiliate-link check. Runs in a
    thread so the admin UI isn't blocked by the supplier's response time."""
    from starlette.concurrency import run_in_threadpool
    from app.services import supplier_status_service
    result = await run_in_threadpool(supplier_status_service.test_supplier_pull, supplier)
    return JSONResponse(result)


@app.post("/admin/suppliers/pull/{supplier}")
def admin_suppliers_pull_now(
    supplier: str,
    background_tasks: BackgroundTasks,
    _auth: bool = Depends(require_admin_csrf),
):
    """Pull products from ONE supplier now (not all suppliers). The admin
    "משוך מוצרים עכשיו" per-supplier button calls this; discovery runs in
    the background so the button responds instantly and the status page
    auto-refreshes the counters a few seconds later."""
    from app.services import supplier_status_service

    def _run():
        result = supplier_status_service.pull_supplier_products(supplier)
        logger.info("Manual pull for %s: %s", supplier, result.get("message", result))

    background_tasks.add_task(_run)
    return JSONResponse({"status": "started", "supplier": supplier, "message": "המשיכה הופעלה ברקע — המונה יתעדכן כאן בעוד כמה רגעים."})


@app.post("/admin/image-cache/clear")
async def admin_clear_image_cache(request: Request, _auth: bool = Depends(require_admin_csrf)):
    """Clear the image proxy cache (WebP + AVIF files). After clearing,
    the next request for each image will fetch and re-convert the source."""
    from starlette.concurrency import run_in_threadpool
    from app.services import image_proxy_service
    stats_before = image_proxy_service.cache_stats()
    result = await run_in_threadpool(image_proxy_service.clear_cache)
    return JSONResponse({
        "status": "ok",
        "was_files": stats_before["files"],
        "was_mb": stats_before["total_mb"],
        "removed": result["removed"],
        "failed": result["failed"],
        "freed_mb": result["freed_mb"],
        "message": f"נמחקו {result['removed']} קבצים ({result['freed_mb']}MB). התמונות יומרו מחדש בביקוש הבא.",
    })


@app.post("/admin/warm-image-cache")
async def admin_warm_image_cache(request: Request, _auth: bool = Depends(require_admin_csrf)):
    """Pre-warm the image cache: fetch and convert every active product image
    to BOTH WebP and AVIF in the background (thread pool). Skips images that
    are already cached and fresh, so it's safe to run after a clear."""
    from starlette.concurrency import run_in_threadpool
    from app.services import image_proxy_service
    from app.core.database import SessionLocal
    from app.core.models import Product

    # Collect all unique active product image URLs.
    db = SessionLocal()
    try:
        urls = [
            row[0] for row in
            db.query(Product.image_url)
            .filter(Product.is_active == True, Product.image_url.isnot(None), Product.image_url != '')
            .distinct()
            .all()
        ]
    finally:
        db.close()

    stats_before = image_proxy_service.cache_stats()

    # Run the actual warming in a thread so the admin UI isn't blocked.
    result = await run_in_threadpool(image_proxy_service.warm_cache, urls)

    stats_after = image_proxy_service.cache_stats()

    return JSONResponse({
        "status": "ok",
        "total_urls": result["total"],
        "already_cached": result["already"],
        "converted": result["converted"],
        "failed": result["failed"],
        "formats": result["formats"],
        "cache_before": f"{stats_before['files']} files / {stats_before['total_mb']}MB",
        "cache_after": f"{stats_after['files']} files / {stats_after['total_mb']}MB",
        "message": 'חימום מטמון תמונות: ' + str(result['converted']) + ' הומרו, ' + str(result['already']) + ' כבר במטמון, ' + str(result['failed']) + ' נכשלו. סה"כ ' + str(stats_after['files']) + ' קבצים (' + str(stats_after['total_mb']) + 'MB).',
    })


@app.post("/admin/users/{user_id}/toggle-active")
def admin_toggle_user_active(user_id: int, db: Session = Depends(get_db), _auth: bool = Depends(require_admin_csrf)):
    """Suspend/reactivate an account — e.g. for abuse or a chargeback
    dispute — without deleting their order history."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return JSONResponse({"status": "error", "message": "משתמש לא נמצא"}, status_code=404)
    user.is_active = not user.is_active
    db.commit()
    return JSONResponse({"status": "ok", "is_active": user.is_active})


@app.post("/admin/run-price-monitor")
def trigger_price_monitor(
    background_tasks: BackgroundTasks,
    _auth: bool = Depends(require_admin_csrf),
):
    """Same free-tier pattern as run-discovery above: point a second
    cron-job.org schedule (every 6-12h) at this URL instead of relying on
    scheduler.py running 24/7, which free hosting tiers don't offer."""
    def _run():
        db = SessionLocal()
        try:
            snapshot_count = record_daily_prices(db)
            triggered = check_price_alerts(db)
            logger.info("Manual price monitor run: %s snapshots, %s alerts triggered", snapshot_count, len(triggered))
        finally:
            db.close()

    background_tasks.add_task(_run)
    return JSONResponse({"status": "started"})


@app.get("/api/price-history/{product_id}")
def price_history(product_id: int, db: Session = Depends(get_db)):
    """Powers the price-history chart on the product page. Returns up to
    the last 90 recorded daily snapshots — an empty list just means no
    history yet (new product), which the frontend treats as 'not enough
    data' rather than an error."""
    rows = (
        db.query(DailyPrice)
        .filter(DailyPrice.product_id == product_id)
        .order_by(DailyPrice.timestamp.asc())
        .limit(90)
        .all()
    )
    return JSONResponse({
        "labels": [r.timestamp.strftime("%d/%m") for r in rows],
        "prices": [r.price for r in rows],
    })


@app.post("/admin/run-discovery")
@limiter.limit("10/minute")
def trigger_discovery(
    request: Request,
    background_tasks: BackgroundTasks,
    _auth: bool = Depends(require_admin_csrf),
):
    """Manual trigger for the auto-import pipeline. Also doubles as the
    endpoint an external free cron service (e.g. cron-job.org) can hit on a
    schedule — see README 'free deployment' section — since it needs no
    long-running background process on the host, just an HTTP call."""
    from app.workers.auto_import_worker import run_full_cycle
    background_tasks.add_task(run_full_cycle)
    return JSONResponse({"status": "started"})


@app.post("/api/newsletter")
@limiter.limit("5/minute")
def newsletter_signup(request: Request, email: str = Form(...), website: str = Form(""), db: Session = Depends(get_db)):
    # Honeypot for newsletter bots — pretend success, store nothing.
    if website:
        return JSONResponse({"status": "ok", "message": "נרשמתם בהצלחה! נעדכן אתכם בדילים החמים ביותר."})
    email = email.strip().lower()
    if "@" not in email or "." not in email.split("@")[-1]:
        return JSONResponse({"status": "error", "message": "אימייל לא תקין"}, status_code=400)

    existing = db.query(NewsletterSubscriber).filter(NewsletterSubscriber.email == email).first()
    if existing:
        return JSONResponse({"status": "ok", "message": "כבר רשומים! תודה 🙌"})

    db.add(NewsletterSubscriber(email=email))
    db.commit()
    return JSONResponse({"status": "ok", "message": "נרשמתם בהצלחה! נעדכן אתכם בדילים החמים ביותר."})


@app.get("/robots.txt")
def robots_txt():
    content = f"User-agent: *\nAllow: /\nSitemap: {settings.site_url}/sitemap.xml\n"
    return PlainTextResponse(content)


@app.get("/offline")
def offline_page(request: Request):
    """Service Worker fallback — served when the network is down. Returns a
    lightweight self-contained HTML page that requires zero external resources
    (no Tailwind CDN, no Font Awesome CDN) so it actually renders offline."""
    html = f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>סמארטשופ — אין חיבור לאינטרנט</title>
    <style>
        body {{ font-family: -apple-system, 'Segoe UI', sans-serif; background: #f8fafc; color: #0f172a; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; text-align: center; }}
        .card {{ background: #fff; border-radius: 1rem; padding: 2rem; max-width: 400px; box-shadow: 0 10px 30px rgba(0,0,0,0.08); }}
        h1 {{ font-size: 1.5rem; margin-bottom: 0.5rem; }}
        p {{ color: #64748b; font-size: 0.9rem; margin-bottom: 1.5rem; line-height: 1.6; }}
        .btn {{ display: inline-block; background: #1e293b; color: #fff; padding: 0.75rem 2rem; border-radius: 0.75rem; text-decoration: none; font-weight: 600; }}
    </style>
</head>
<body>
    <div class="card">
        <h1>📡 אין חיבור לאינטרנט</h1>
        <p>נראה שאתם לא מחוברים כרגע. ברגע שהחיבור יחזור, תוכלו להמשיך לגלוש בדילים הכי חמים.</p>
        <p style="font-size:0.8rem;">דפים שביקרתם בהם לאחרונה ימשיכו להיות זמינים — נסו לרענן את העמוד.</p>
        <a href="/" class="btn">נסו שוב</a>
    </div>
</body>
</html>"""
    return Response(content=html, media_type="text/html", headers={"Cache-Control": "public, max-age=86400"})


# ── WhatsApp Cloud API Webhook ────────────────────────────────────────────

@app.get("/api/whatsapp-webhook")
def whatsapp_webhook_verify(
    request: Request,
    hub_mode: str = "",
    hub_challenge: str = "",
    hub_verify_token: str = "",
):
    """Meta sends a GET with hub.mode=subscribe, hub.challenge, and
    hub.verify_token to confirm the webhook endpoint is owned by you.
    Return the challenge as plain text to complete verification."""
    if hub_mode == "subscribe" and settings.whatsapp_verify_token:
        if secrets.compare_digest(hub_verify_token, settings.whatsapp_verify_token):
            return PlainTextResponse(hub_challenge, status_code=200)
    raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/api/whatsapp-webhook")
async def whatsapp_webhook_receive(request: Request):
    """Receive incoming WhatsApp messages. Currently handles:
    - 'הרשמה' / 'subscribe' → saves phone number to broadcast list
    - 'ביטול' / 'stop' → removes from broadcast list
    - Anything else → replies with a help message
    Auto-reply via the Cloud API so the user gets an instant response."""
    try:
        raw = await request.body()
        body = __import__('json').loads(raw)
    except Exception as exc:
        logger.debug("WhatsApp webhook: invalid body — %s", exc)
        return JSONResponse({"status": "ignored"})

    entries = body.get("entry", [])
    for entry in entries:
        for change in entry.get("changes", []):
            messages = change.get("value", {}).get("messages", [])
            for msg in messages:
                phone = msg.get("from", "")
                text = (msg.get("text", {}).get("body") or "").strip().lower()
                reply = None

                if text in ("הרשמה", "subscribe", "הצטרף", "start", "דיל"):
                    marketing_agent._save_whatsapp_subscriber(phone)
                    reply = (
                        "✅ נרשמת בהצלחה להתראות דיל היום!\n"
                        "תקבלו ממני דיל אחד ביום — המחיר הכי טוב שמצאנו.\n"
                        "להסרה: שלחו 'ביטול'."
                    )
                elif text in ("ביטול", "stop", "הסר", "הסרה"):
                    # Simple unsubscribe: rewrite the list without this phone
                    subs = marketing_agent._get_whatsapp_subscribers()
                    if phone in subs:
                        subs.remove(phone)
                        import json, os
                        path = os.path.join(os.path.dirname(__file__), "..", "data", "whatsapp_subscribers.json")
                        with open(path, "w") as f:
                            json.dump(subs, f)
                    reply = "👋 הסרנו אתכם מרשימת ההתראות. תמיד תוכלו לחזור — פשוט שלחו 'הרשמה'."
                elif text:
                    reply = (
                        "👋 היי! ברוכים הבאים לסמארטשופ.\n\n"
                        "שלחו 'הרשמה' כדי לקבל דיל אחד חם ביום 📦\n"
                        "שלחו 'ביטול' להסרה.\n\n"
                        "חפשו דילים באתר: smartshop.co.il"
                    )

                if reply and settings.whatsapp_phone_number_id and settings.whatsapp_access_token:
                    api_url = f"https://graph.facebook.com/v22.0/{settings.whatsapp_phone_number_id}/messages"
                    try:
                        requests.post(
                            api_url,
                            json={
                                "messaging_product": "whatsapp",
                                "recipient_type": "individual",
                                "to": phone,
                                "type": "text",
                                "text": {"body": reply, "preview_url": False},
                            },
                            headers={"Authorization": f"Bearer {settings.whatsapp_access_token}"},
                            timeout=8,
                        )
                    except Exception as e:
                        logger.warning("WhatsApp auto-reply failed: %s", e)

    return JSONResponse({"status": "ok"})


@app.get("/img/{source_hash}")
@limiter.limit("300/minute")
def image_proxy(request: Request, source_hash: str):
    """Real-time image proxy: fetch external product images, convert to WebP/AVIF,
    cache on disk, and serve. The source_hash is a base64url-encoded URL.
    Returns 400 if the source host isn't in the allowed list.

    Content negotiation via Accept header:
      - Browsers sending image/avif → get AVIF (~30% smaller)
      - Everyone else → get WebP
      - Vary: Accept ensures CDNs cache both versions separately.

    Template usage: <img src="/img/{{ product.image_url | b64enc }}">
    (b64enc is registered as a Jinja2 global filter below.)
    """
    import base64 as _b64
    try:
        # Add padding if needed (base64url may strip '=')
        padded = source_hash + '=' * (4 - len(source_hash) % 4) if len(source_hash) % 4 else source_hash
        source_url = _b64.urlsafe_b64decode(padded).decode('utf-8')
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid image URL encoding")

    # Pass Accept header for AVIF/WebP content negotiation
    accept = request.headers.get("accept", "")
    # Optional ?w= query param for responsive images (srcset)
    width_raw = request.query_params.get("w", "")
    target_width = 0
    if width_raw:
        try:
            target_width = int(width_raw)
            if target_width < 1 or target_width > 2400:
                target_width = 0
        except (ValueError, TypeError):
            pass

    try:
        image_bytes, content_type = image_proxy_service.get_or_convert(
            source_url, accept_header=accept, target_width=target_width
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return Response(
        content=image_bytes,
        media_type=content_type,
        headers={
            "Cache-Control": "public, max-age=604800, immutable",
            "Vary": "Accept",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/healthz")
def healthz():
    """Deployment health check (Render / Docker HEALTHCHECK): cheap, always
    200 while the app is up. No DB query on purpose — a DB blip shouldn't
    crash the health probe and trigger a restart loop; the app's own
    error handling covers DB issues per-request."""
    return JSONResponse({"status": "ok"})


@app.get("/db-check")
def db_check():
    """Diagnostic: DB init result — "ok" when connected, otherwise the
    error string. Makes a managed-DB (Neon/Postgres) connection problem
    visible without shell or log access on free tiers."""
    return JSONResponse({"db": "ok" if _db_init_error is None else _db_init_error})


@app.get("/sitemap.xml")
def sitemap_xml(db: Session = Depends(get_db)):
    """Every live, AI-verified product gets an entry so search engines can
    index new imported deals without a manual step. Cached for 10 minutes —
    crawlers hit this repeatedly and it doesn't need to be real-time."""
    cached = cache_service.get("sitemap_xml")
    if cached:
        return Response(content=cached, media_type="application/xml")

    products = db.query(Product).filter(Product.is_active == True, Product.is_verified == True).all()  # noqa: E712
    urls = [f"{settings.site_url}/", f"{settings.site_url}/coupons", f"{settings.site_url}/about"]
    urls += [f"{settings.site_url}/product/{p.id}" for p in products]
    body = "\n".join(f"  <url><loc>{u}</loc></url>" for u in urls)
    xml = f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{body}\n</urlset>'
    cache_service.set("sitemap_xml", xml, ttl_seconds=600)
    return Response(content=xml, media_type="application/xml")


@app.get("/feed/google-shopping.xml")
def google_shopping_feed(db: Session = Depends(get_db)):
    """Google Merchant Center product feed — the file you paste into
    merchants.google.com → Products → Feeds so your products can appear in
    Google Shopping (free listings + paid). Cached 15 min; hits are cheap
    XML generation. If the site is on a real domain, this is the single
    highest-leverage SEO action for shopping visibility."""
    cached = cache_service.get("gshopping_feed")
    if cached:
        return Response(content=cached, media_type="application/xml")

    products = (
        db.query(Product)
        .filter(Product.is_active == True, Product.is_verified == True)  # noqa: E712
        .order_by(Product.buying_score.desc())
        .limit(2000)
        .all()
    )
    from xml.sax.saxutils import escape

    # Google's taxonomy is English-only; map our Hebrew categories so the
    # feed isn't labeled "Electronics" for everything (mislabels hurt
    # Merchant Center approval and click-through).
    GOOGLE_CATEGORY = {
        "אלקטרוניקה": "Electronics",
        "גאדג'טים": "Electronics > Gadgets",
        "לבית ולמטבח": "Home & Garden > Kitchen & Dining",
        "כלי עבודה": "Tools & Hardware",
        "אביזרי רכב": "Vehicles & Parts > Vehicle Parts & Accessories",
        "אופנה": "Apparel & Accessories",
        "ספורט ופנאי": "Sporting Goods",
        "יופי וטיפוח": "Beauty",
        "משחקים וצעצועים": "Toys & Games",
        "מוצרי תינוקות": "Baby & Toddler",
        "משרד ומחשבים": "Computers > Computer Accessories",
        "חיות מחמד": "Pet Supplies",
        "מזון וחטיפים": "Food, Beverages & Tobacco > Food",
        "גינון": "Home & Garden > Gardening & Lawn Care",
        "צילום ומוזיקה": "Electronics > Photography & Music",
        "תכשיטים ושעונים": "Jewelry & Watches",
        "ספרים ותחביבים": "Media > Books",
        "בריאות ומטבח": "Home & Garden > Kitchen & Dining",
        "ריהוט ועיצוב הבית": "Home & Garden > Furniture",
        "תיקים ומזוודות": "Luggage & Bags",
        "נעליים": "Apparel & Accessories > Shoes",
        "מכשירי חשמל ביתיים": "Home & Garden > Kitchen & Dining > Small Appliances",
        "רכיבים אלקטרוניים": "Electronics > Circuit Boards & Components",
        "כלי נגינה": "Arts & Entertainment > Musical Instruments",
    }

    items = []
    for p in products:
        price = float(p.price or 0)
        if price <= 0:
            continue
        title = escape((p.seo_title or p.name or "")[:150])
        desc = escape((p.description or p.name or "")[:5000])
        link = escape(f"{settings.site_url}/product/{p.id}")
        img = escape(p.image_url or "")
        brand = escape((p.supplier_name or "SmartShop")[:70])
        avail = "in stock" if (p.stock_count or 0) > 0 else "out of stock"
        gid = f"smartshop_{p.id}"
        gcat = escape(GOOGLE_CATEGORY.get(p.category or "", "Other"))
        items.append(f"""  <item>
    <g:id>{gid}</g:id>
    <g:title>{title}</g:title>
    <g:description>{desc}</g:description>
    <g:link>{link}</g:link>
    <g:image_link>{img}</g:image_link>
    <g:availability>{avail}</g:availability>
    <g:price>{price:.2f} ILS</g:price>
    <g:condition>new</g:condition>
    <g:brand>{brand}</g:brand>
    <g:google_product_category>{gcat}</g:google_product_category>
    <g:identifier_exists>no</g:identifier_exists>
  </item>""")
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:g="http://base.google.com/ns/1.0">\n'
        "<channel>\n"
        f"<title>{escape(settings.site_url)}</title>\n"
        f"<link>{escape(settings.site_url)}</link>\n"
        "<description>SmartShop — דילים חמים עם השוואת מחירים</description>\n"
        + "\n".join(items)
        + "\n</channel>\n</rss>"
    )
    cache_service.set("gshopping_feed", xml, ttl_seconds=900)
    return Response(content=xml, media_type="application/xml")


@app.get("/coupons")
def coupons_page(request: Request, db: Session = Depends(get_db)):
    """All ACTIVE coupons from every registered supplier — both the Coupon
    table (pulled via /admin/coupons/pull from official affiliate coupon
    feeds) and live products carrying a coupon_code. Unified display via
    coupon_service.coupons_for_display()."""
    from app.services import coupon_service
    coupons = coupon_service.coupons_for_display(db)
    return templates.TemplateResponse("coupons.html", {"request": request, "coupons": coupons})


@app.post("/admin/coupons/pull")
def admin_pull_coupons(
    background_tasks: BackgroundTasks,
    _auth: bool = Depends(require_admin_csrf),
):
    """Pull coupon codes from EVERY registered supplier adapter (official
    coupon/offer feeds), upsert into the Coupon table, then the coupons
    page / personal area pick them up automatically. Runs in background so
    the admin UI responds instantly."""
    from app.services import coupon_service

    def _run():
        db = SessionLocal()
        try:
            report = coupon_service.pull_coupons_from_sources(db)
            logger.info("Coupon pull complete: %s", report)
        finally:
            db.close()

    background_tasks.add_task(_run)
    return JSONResponse({"status": "ok", "message": "משיכת קופונים הופעלה ברקע — רעננו את הדף בעוד כמה רגעים."})


@app.post("/api/reviews/{product_id}")
def submit_review(product_id: int, request: Request, rating: int = Form(...), comment: str = Form(""), db: Session = Depends(get_db)):
    """Real user review submission — 1 review per (user, product), stars
    must be 1-5. Logged-in users only, so reviews can't be spammed
    anonymously. Reviewers earn +5 coins (audited, once per product)."""
    user = auth_service.get_current_user(request, db)
    if not user:
        return JSONResponse({"status": "error", "message": "יש להתחבר כדי לכתוב ביקורת"}, status_code=401)
    if rating < 1 or rating > 5:
        return JSONResponse({"status": "error", "message": "הדירוג חייב להיות בין 1 ל-5 כוכבים"}, status_code=400)
    existing = db.query(ProductReview).filter_by(user_id=user.id, product_id=product_id).first()
    if existing:
        existing.rating = rating
        existing.comment = comment
        existing.created_at = datetime.datetime.utcnow()
        db.commit()
        return JSONResponse({"status": "ok", "message": "הביקורת עודכנה! תודה 🙏"})

    db.add(ProductReview(user_id=user.id, product_id=product_id, rating=rating, comment=comment))
    db.commit()
    if not loyalty_service.user_earned_reason_before(db, user.id, f"review_{product_id}"):
        loyalty_service.add_points(db, user, 5, f"review_{product_id}")
        request.session["user_points"] = user.points or 0
        notification_service.notify_user(db, user.id, "ביקורת נרשמה ⭐", "קיבלתם 5 מטבעות על הביקורת!")
    return JSONResponse({"status": "ok", "message": "הביקורת נשמרה! תודה 🙏 (+5 מטבעות)"})


@app.get("/api/notifications")
@limiter.limit("60/minute")
def my_notifications(request: Request, db: Session = Depends(get_db)):
    user = auth_service.get_current_user(request, db)
    user_id = user.id if user else None
    items = notification_service.unread_for_user(db, user_id)
    return JSONResponse({
        "notifications": [
            {"id": n.id, "title": n.title, "message": n.message, "link": n.link}
            for n in items
        ]
    })


@app.post("/api/notifications/{notification_id}/read")
@limiter.limit("60/minute")
def mark_notification_read(request: Request, notification_id: int, db: Session = Depends(get_db)):
    notification_service.mark_read(db, notification_id)
    return JSONResponse({"status": "ok"})


# --- Web Push Notifications (VAPID) ---
@app.post("/api/push/subscribe")
@limiter.limit("30/minute")
async def push_subscribe(request: Request, db: Session = Depends(get_db)):
    """Store a browser push subscription. The frontend sends the
    PushSubscription JSON after the user grants notification permission.
    If the endpoint already exists for this (user, endpoint) pair we
    update the keys silently (the browser may rotate them)."""
    import json as _json
    try:
        body = _json.loads((await request.body()).decode())
    except Exception:
        return JSONResponse({"status": "error", "message": "Invalid JSON"}, status_code=400)
    endpoint = (body.get("endpoint") or "").strip()
    keys = body.get("keys") or {}
    p256dh = (keys.get("p256dh") or "").strip()
    auth = (keys.get("auth") or "").strip()
    if not endpoint or not p256dh or not auth:
        return JSONResponse({"status": "error", "message": "Missing subscription fields"}, status_code=400)

    user = auth_service.get_current_user(request, db)
    user_id = user.id if user else None

    # Replace old endpoint if the browser sends a migration hint.
    old_endpoint = (body.get("old_endpoint") or "").strip()
    if old_endpoint:
        old = db.query(PushSubscription).filter(
            PushSubscription.endpoint == old_endpoint
        ).first()
        if old:
            db.delete(old)

    # Upsert: same (user, endpoint) -> update keys; new -> insert.
    existing = db.query(PushSubscription).filter(
        PushSubscription.endpoint == endpoint,
        PushSubscription.user_id == user_id,
    ).first()
    if existing:
        existing.p256dh = p256dh
        existing.auth = auth
    else:
        db.add(PushSubscription(endpoint=endpoint, p256dh=p256dh, auth=auth, user_id=user_id))
    db.commit()

    # Link any anonymous subscriptions to this user now that they're
    # authenticated (same endpoint, different user).
    if user_id:
        db.query(PushSubscription).filter(
            PushSubscription.endpoint == endpoint,
            PushSubscription.user_id.is_(None),
        ).update({"user_id": user_id})
        db.commit()

    return JSONResponse({"status": "ok"})


@app.post("/api/push/unsubscribe")
@limiter.limit("30/minute")
async def push_unsubscribe(request: Request, db: Session = Depends(get_db)):
    """Remove a browser push subscription (user clicks 'unsubscribe from
    notifications' in the personal area). Matches by endpoint, scoped to
    the current user so users can't unsubscribe someone else's device."""
    import json as _json
    try:
        body = _json.loads((await request.body()).decode())
    except Exception:
        return JSONResponse({"status": "error", "message": "Invalid JSON"}, status_code=400)
    endpoint = (body.get("endpoint") or "").strip()
    if not endpoint:
        return JSONResponse({"status": "error", "message": "Missing endpoint"}, status_code=400)

    user = auth_service.get_current_user(request, db)
    q = db.query(PushSubscription).filter(PushSubscription.endpoint == endpoint)
    if user:
        q = q.filter(PushSubscription.user_id == user.id)
    q.delete()
    db.commit()
    return JSONResponse({"status": "ok"})


@app.get("/api/push/vapid-public-key")
def push_vapid_key():
    """Expose the VAPID public key so the frontend can pass it to
    pushManager.subscribe(). The key is a setting — no auth needed."""
    if not settings.vapid_public_key:
        return JSONResponse({"publicKey": None, "enabled": False})
    return JSONResponse({"publicKey": settings.vapid_public_key, "enabled": True})


@app.post("/api/push/send-test")
def push_send_test(request: Request, db: Session = Depends(get_db), _auth: bool = Depends(require_admin)):
    """Admin-only: send a test push to every subscribed browser to verify
    the VAPID configuration is correct end-to-end."""
    count, message = notification_service.send_push_test(db)
    return JSONResponse({"status": "ok" if count else "error", "message": message})


@app.get("/api/popup")
@limiter.limit("60/minute")
def latest_popup_api(request: Request, db: Session = Depends(get_db)):
    """Latest marketing popup for the frontend — shown once per browser via
    localStorage so it doesn't nag on every page load."""
    popup = notification_service.latest_popup(db)
    if not popup:
        return JSONResponse({"popup": None})
    return JSONResponse({"popup": {"id": popup.id, "title": popup.title, "message": popup.message, "link": popup.link}})


@app.post("/api/popup/{notification_id}/dismiss")
@limiter.limit("30/minute")
def dismiss_popup(request: Request, notification_id: int, db: Session = Depends(get_db)):
    notification_service.mark_read(db, notification_id)
    return JSONResponse({"status": "ok"})


@app.post("/api/ads/{ad_id}/click")
@limiter.limit("60/minute")
def ad_click(request: Request, ad_id: int, db: Session = Depends(get_db)):
    ads_service.record_ad_click(db, ad_id)
    return JSONResponse({"status": "ok"})


@app.get("/api/loyalty/coach")
def loyalty_coach_api(request: Request, db: Session = Depends(get_db)):
    """Next-actions coach for the logged-in user's coins economy."""
    user = auth_service.get_current_user(request, db)
    if not user:
        return JSONResponse({"status": "error", "message": "יש להתחבר"}, status_code=401)
    return JSONResponse({
        "status": "ok",
        "suggestions": loyalty_coach.next_actions(db, user),
        "rank_progress": loyalty_coach.rank_progress(user),
    })


@app.post("/admin/marketing/popup")
def admin_create_popup(request: Request, title: str = Form(...), message: str = Form(""), link: str = Form(""), db: Session = Depends(get_db), _auth: bool = Depends(require_admin_csrf)):
    notification_service.broadcast(db, title, message, link or None, is_popup=True)
    return JSONResponse({"status": "ok", "message": "הפופאפ נוצר! הוא יופיע למבקרים הבאים."})


@app.post("/admin/marketing/popup/{popup_id}/update")
def admin_update_popup(popup_id: int, request: Request, title: str = Form(...), message: str = Form(""), link: str = Form(""), db: Session = Depends(get_db), _auth: bool = Depends(require_admin_csrf)):
    popup = db.query(Notification).filter(Notification.id == popup_id, Notification.user_id.is_(None)).first()
    if not popup:
        return JSONResponse({"status": "error", "message": "פופאפ לא נמצא"}, status_code=404)
    popup.title = title
    popup.message = message
    popup.link = link or None
    popup.read_at = None  # re-arm so visitors see the updated version
    db.commit()
    return JSONResponse({"status": "ok", "message": "הפופאפ עודכן!"})


@app.post("/admin/marketing/popup/{popup_id}/delete")
def admin_delete_popup(popup_id: int, request: Request, db: Session = Depends(get_db), _auth: bool = Depends(require_admin_csrf)):
    popup = db.query(Notification).filter(Notification.id == popup_id, Notification.user_id.is_(None)).first()
    if not popup:
        return JSONResponse({"status": "error", "message": "פופאפ לא נמצא"}, status_code=404)
    db.delete(popup)
    db.commit()
    return JSONResponse({"status": "ok", "message": "הפופאפ נמחק"})


@app.post("/admin/marketing/ad")
def admin_create_ad(request: Request, name: str = Form(...), position: str = Form(...), image_url: str = Form(""), target_url: str = Form("#"), db: Session = Depends(get_db), _auth: bool = Depends(require_admin_csrf)):
    if position not in ads_service.POSITIONS:
        return JSONResponse({"status": "error", "message": "מיקום פרסום לא חוקי"}, status_code=400)
    db.add(AdPlacement(name=name, position=position, image_url=image_url, target_url=target_url, is_active=True))
    db.commit()
    return JSONResponse({"status": "ok", "message": "המודעה נוספה!"})


@app.post("/admin/marketing/ad/{ad_id}/update")
def admin_update_ad(ad_id: int, request: Request, name: str = Form(...), position: str = Form(...), image_url: str = Form(""), target_url: str = Form("#"), db: Session = Depends(get_db), _auth: bool = Depends(require_admin_csrf)):
    ad = db.query(AdPlacement).filter(AdPlacement.id == ad_id).first()
    if not ad:
        return JSONResponse({"status": "error", "message": "מודעה לא נמצאה"}, status_code=404)
    if position not in ads_service.POSITIONS:
        return JSONResponse({"status": "error", "message": "מיקום פרסום לא חוקי"}, status_code=400)
    ad.name = name
    ad.position = position
    ad.image_url = image_url
    ad.target_url = target_url
    db.commit()
    return JSONResponse({"status": "ok", "message": "המודעה עודכנה"})


@app.post("/admin/marketing/ad/{ad_id}/toggle")
def admin_toggle_ad(ad_id: int, request: Request, db: Session = Depends(get_db), _auth: bool = Depends(require_admin_csrf)):
    ad = db.query(AdPlacement).filter(AdPlacement.id == ad_id).first()
    if not ad:
        return JSONResponse({"status": "error", "message": "מודעה לא נמצאה"}, status_code=404)
    ad.is_active = not ad.is_active
    db.commit()
    return JSONResponse({"status": "ok", "is_active": ad.is_active})


@app.post("/admin/marketing/ad/{ad_id}/delete")
def admin_delete_ad(ad_id: int, request: Request, db: Session = Depends(get_db), _auth: bool = Depends(require_admin_csrf)):
    ad = db.query(AdPlacement).filter(AdPlacement.id == ad_id).first()
    if not ad:
        return JSONResponse({"status": "error", "message": "מודעה לא נמצאה"}, status_code=404)
    db.delete(ad)
    db.commit()
    return JSONResponse({"status": "ok", "message": "המודעה נמחקה"})


@app.post("/admin/newsletter/send")
def admin_send_newsletter(db: Session = Depends(get_db), _auth: bool = Depends(require_admin_csrf)):
    report = email_campaign.send_newsletter(db, limit=6)
    return JSONResponse({"status": "ok", "report": report})


@app.get("/admin/newsletter/preview")
def admin_newsletter_preview(db: Session = Depends(get_db), _auth: bool = Depends(require_admin)):
    """Rendered newsletter preview so the admin can see exactly what
    subscribers get before hitting send."""
    subject, html = email_campaign.build_deal_newsletter(db, limit=6)
    return JSONResponse({"status": "ok", "subject": subject, "html": html})


@app.get("/api/site-ads")
@limiter.limit("60/minute")
def site_ads_api(request: Request, db: Session = Depends(get_db)):
    """Bottom + sticky-side ads shown on every page (rendered by layout)."""
    bottom = ads_service.get_active_for_position(db, "site_bottom", limit=2)
    side = ads_service.get_active_for_position(db, "site_side", limit=2)
    return JSONResponse({
        "bottom": [{"id": a.id, "name": a.name, "image_url": a.image_url, "target_url": a.target_url} for a in bottom],
        "side": [{"id": a.id, "name": a.name, "image_url": a.image_url, "target_url": a.target_url} for a in side],
    })


@app.post("/admin/instagram/post")
def admin_instagram_post(product_id: int = Form(...), db: Session = Depends(get_db), _auth: bool = Depends(require_admin_csrf)):
    """Post a selected product as a deal to Instagram via the agent."""
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        return JSONResponse({"status": "error", "message": "מוצר לא נמצא"}, status_code=404)
    result = instagram_agent.post_deal(
        product_name=product.name, price=product.price,
        url=f"{settings.site_url}/go/{product.id}", image_url=product.image_url,
    )
    return JSONResponse(result)


@app.post("/admin/viral/script")
def admin_viral_script(product_id: int = Form(...), db: Session = Depends(get_db), _auth: bool = Depends(require_admin_csrf)):
    """Generate a short-video script + social caption for a product via the
    AutoViralEngine (TikTok/Reels ready)."""
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        return JSONResponse({"status": "error", "message": "מוצר לא נמצא"}, status_code=404)
    script = viral_engine.generate_short_script(product.name, product.price)
    caption = viral_engine.build_social_caption(product.name, f"{settings.site_url}/go/{product.id}", script)
    return JSONResponse({"status": "ok", "script": script, "caption": caption})


@app.post("/admin/blog/guide")
def admin_blog_guide(category: str = Form(...), db: Session = Depends(get_db), _auth: bool = Depends(require_admin_csrf)):
    """Generate a Top-5 buying guide for a category via the BlogAgent.
    Degrades gracefully without GOOGLE_API_KEY (never hangs the admin UI)."""
    if not settings.google_api_key:
        return JSONResponse({"status": "error", "message": "הגדרת GOOGLE_API_KEY נדרשת ליצירת מדריך AI. בינתיים אפשר לראות את המוצרים בקטגוריה בעמוד הבית."})
    products = (
        db.query(Product)
        .filter(Product.category == category, Product.is_active == True, Product.is_verified == True)  # noqa: E712
        .limit(5)
        .all()
    )
    if not products:
        return JSONResponse({"status": "error", "message": "אין מוצרים בקטגוריה הזו"}, status_code=400)
    products_list = "\n".join(f"- {p.name} | מחיר ₪{p.price} | דירוג {p.rating}" for p in products)
    try:
        guide = blog_agent.write_buying_guide(category, products_list)
    except Exception:
        logger.exception("Blog guide generation failed")
        return JSONResponse({"status": "error", "message": "יצירת המדריך נכשלה כרגע — נסו שוב מאוחר יותר"})
    return JSONResponse({"status": "ok", "guide": guide})


@app.get("/admin/candidates")
def admin_candidates(db: Session = Depends(get_db), _auth: bool = Depends(require_admin)):
    """Staging-table review queue: candidates the pipeline found but didn't
    auto-promote, waiting for a human glance."""
    from app.core.models import TrendingCandidate
    candidates = (
        db.query(TrendingCandidate)
        .filter(TrendingCandidate.status == "pending")
        .order_by(TrendingCandidate.quality_score.desc())
        .limit(50)
        .all()
    )
    return JSONResponse({"candidates": [{"id": c.id, "name": c.raw_name, "price": c.raw_price, "score": c.quality_score} for c in candidates]})


@app.get("/about")
def about_page(request: Request):    return templates.TemplateResponse("about.html", {
        "request": request,
        "contact_email": settings.smtp_from_email or settings.admin_email or "hello@yourdomain.com",
        "site_name": "SmartShop",
    })


# --- SEO Landing Pages ---
@app.get("/brand/{brand_name}")
def brand_landing(request: Request, brand_name: str, page: int = 1, db: Session = Depends(get_db)):
    """SEO landing page: /brand/xiaomi, /brand/apple, etc."""
    brand = brand_name.strip()
    page = max(page, 1)
    q = db.query(Product).filter(Product.is_active == True, Product.is_verified == True)
    q = q.filter(Product.name.ilike(f"%{brand}%") | (Product.supplier_name.ilike(f"%{brand}%")))
    total = q.count()
    products = q.order_by(Product.rating.desc()).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()
    enrich_products_for_home(products, db)
    return templates.TemplateResponse("search.html", {"request": request, "query": brand, "products": products, "total_count": total, "page": page, "has_next_page": (page * PAGE_SIZE) < total, "has_prev_page": page > 1, "categories": CATEGORIES, "active_category": None, "min_price": 0, "max_price": 0, "min_rating": 0, "active_sort": "newest", "has_filters": False, "search_reasons": {}, "smart": False, "live_results": [], "category_thumbnails": _get_category_thumbnails(db), "price_hist": [], "price_stats": None})


@app.get("/deals/{slug}")
def deals_landing(request: Request, slug: str, page: int = 1, db: Session = Depends(get_db)):
    """SEO deal landing pages: /deals/under-50-nis, /deals/best-rated, etc."""
    s = slug.strip()
    page = max(page, 1)
    q = db.query(Product).filter(Product.is_active == True, Product.is_verified == True)
    label = s.replace('-', ' ').title()
    if "under" in s or "מתחת" in s or "עד" in s:
        import re
        nums = re.findall(r'\d+', s)
        if nums:
            q = q.filter(Product.price <= float(nums[0]))
            label = f"עד {nums[0]} ₪"
    elif "trending" in s or "חמים" in s:
        q = q.filter(Product.is_trending == True)
        label = "דילים חמים"
    elif "best" in s or "מומלצים" in s:
        q = q.filter(Product.rating >= 4.0)
        label = "המומלצים ביותר"
    total = q.count()
    products = q.order_by(Product.rating.desc()).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()
    enrich_products_for_home(products, db)
    return templates.TemplateResponse("search.html", {"request": request, "query": label, "products": products, "total_count": total, "page": page, "has_next_page": (page * PAGE_SIZE) < total, "has_prev_page": page > 1, "categories": CATEGORIES, "active_category": None, "min_price": 0, "max_price": 0, "min_rating": 0, "active_sort": "newest", "has_filters": False, "search_reasons": {}, "smart": False, "live_results": [], "category_thumbnails": _get_category_thumbnails(db), "price_hist": [], "price_stats": None})


@app.post("/api/spin-reward")
@limiter.limit("5/minute")
async def spin_reward(request: Request, db: Session = Depends(get_db)):
    """Record a daily spin wheel reward. The frontend sends the prize label."""
    import json as _json
    try:
        body = _json.loads((await request.body()).decode())
    except Exception:
        return JSONResponse({"status": "ok"})
    user = auth_service.get_current_user(request, db)
    if not user:
        return JSONResponse({"status": "ok"})
    coins = body.get("coins", 0)
    if coins and coins > 0:
        loyalty_service.add_points(db, user, int(coins), "daily_spin")
        request.session["user_points"] = (request.session.get("user_points") or 0) + int(coins)
    return JSONResponse({"status": "ok", "coins": coins})





# --- Help center (מרכז עזרה) ---
# FAQ + a contact form that reaches the team by email. Messages are always
# stored in the DB (so nothing is lost when SMTP is off) and forwarded by
# email to the team list when SMTP is configured.

from html import escape as _htmlesc


def _team_email_list() -> list[str]:
    """Parsed team emails: TEAM_EMAILS (comma-separated) + admin email
    as a guaranteed fallback recipient."""
    emails = [e.strip() for e in (settings.team_emails or "").split(",") if e.strip()]
    if settings.admin_email and settings.admin_email not in emails:
        emails.append(settings.admin_email)
    return emails


HELP_FAQS = [
    ("מה זה SmartShop AI?", "בורסת דילים חכמה שמשווה מחירים בין AliExpress, Amazon, eBay, Temu ועוד — ומוצאת עבורכם את העסקה הכי משתלמת, עם קישורי עמלה שקופים."),
    ("האם השימוש באתר עולה כסף?", "לא. הגלישה, החיפוש, השוואת המחירים והצ'אט חינמיים לחלוטין. אנחנו מרוויחים מעמלת שותפים קטנה כשאתם קונים דרך הקישורים שלנו — ללא עלות נוספת עבורכם."),
    ("כמה זמן לוקח המשלוח?", "בדרך כלל 7–21 ימי עסקים לפי הספק. בעמוד המוצר מוצג הערכת זמן משלוח (shipping_days) ולכל הזמנה יש מעקב באזור האישי."),
    ("מה עושים אם המוצר לא מגיע או מגיע פגום?", "אפשר לפתוח תביעה ישירות מול הספק שממנו קניתם. SmartShop AI הוא אתר אפילאייט שמשווה מחירים וממליץ על דילים — לא חנות שמוכרת ישירות, ולכן האחריות והמשלוח הם באחריות הספק. עם זאת, רוב הספקים הגדולים (AliExpress, eBay, Amazon) מציעים הגנת קונה ומדיניות החזרות. נשמח לכוון אתכם — פנו אלינו בצ'אט."),
    ("איך מתבצעים ההחזרות?", "לפי מדיניות ההחזרה של הספק (ברוב הספקים 15–90 יום). פרטי ההחזרה המלאים מופיעים בעמוד המוצר ובמדיניות ההחזרות."),
    ("איך מקבלים מטבעות (🪙)?", "הרשמה (+50), אימות אימייל (+30), שמירת מוצר ראשון למועדפים (+20), קליקים על עסקאות (עד 10 ליום), והתראות מחיר. המטבעות מתועדים במערכת — אפשר לראות כל עסקה באזור האישי."),
    ("איך מגדירים התראת מחיר?", "בעמוד המוצר לוחצים על 'התראת מחיר', מזינים את המחיר הרצוי, ונקבל הודעה ברגע שהמחיר יורד מתחת לסף."),
    ("מה זה חיפוש לפי תמונה?", "מעלים תמונה של מוצר (📷 בסרגל החיפוש) והאתר מוצא מוצרים דומים בקטלוג לפי תפיסה ויזואלית."),
    ("האם הנתונים האישיים שלי בטוחים?", "כן. אנו שומרים רק את מה שצריך כדי לספק את השירות, לא מוכרים מידע לצדדים שלישיים, ומשתמשים בעוגיות הכרחיות בלבד עד לאישורכם. פרטים מלאים במדיניות הפרטיות."),
    ("איך מציעים שיפור או שיתוף פעולה?", "כתבו לנו דרך הטופס 'כתבו לנו' פה למטה, או בצ'אט — נחזור אליכם."),
]
@app.get("/help")
def help_page(request: Request):
    return templates.TemplateResponse("help.html", {
        "request": request,
        "faqs": HELP_FAQS,
        "contact_email": settings.smtp_from_email or settings.admin_email or "hello@yourdomain.com",
        "csrf_token": csrf_service.generate_csrf_token(),
    })


@app.post("/help/contact")
@limiter.limit("5/minute")
def help_contact_submit(
    request: Request,
    name: str = Form(""),
    email: str = Form(...),
    subject: str = Form(""),
    message: str = Form(...),
    website: str = Form(""),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    """Help-center contact form: validates CSRF, drops honeypot bots,
    stores the message in the DB, then emails the team when SMTP is live."""
    ctx = {"request": request, "faqs": HELP_FAQS,
           "contact_email": settings.smtp_from_email or settings.admin_email or "hello@yourdomain.com",
           "csrf_token": csrf_service.generate_csrf_token()}
    # Honeypot: silent fake success for bots.
    if website:
        return RedirectResponse("/help?sent=1", status_code=303)
    if not csrf_service.verify_csrf_token(csrf_token):
        return templates.TemplateResponse("help.html", {**ctx, "error": "הטופס פג תוקף — רעננו את הדף ונסו שוב", "values": {"name": name, "email": email, "subject": subject, "message": message}}, status_code=400)
    if len(message) < 5 or len(message) > 5000:
        return templates.TemplateResponse("help.html", {**ctx, "error": "נא לכתוב פנייה של לפחות 5 תווים", "values": {"name": name, "email": email, "subject": subject, "message": message}}, status_code=400)
    email = email.strip().lower()
    if "@" not in email or "." not in email:
        return templates.TemplateResponse("help.html", {**ctx, "error": "כתובת האימייל אינה תקינה", "values": {"name": name, "email": email, "subject": subject, "message": message}}, status_code=400)

    user = auth_service.get_current_user(request, db)
    msg = models_module.SupportMessage(
        name=name.strip()[:100],
        email=email,
        subject=subject.strip()[:200],
        message=message.strip(),
        user_id=user.id if user else None,
    )
    db.add(msg)
    db.commit()

    # Forward to the team by email (best effort — never blocks the reply).
    body_html = (
        f"<div dir='rtl' style='font-family:Arial;max-width:520px;margin:0 auto'>"
        f"<h2 style='color:#e11d48'>פנייה חדשה ממרכז העזרה</h2>"
        f"<p><b>שם:</b> {_htmlesc(name)}</p><p><b>אימייל:</b> {_htmlesc(email)}</p>"
        f"<p><b>נושא:</b> {_htmlesc(subject)}</p><hr><p style='white-space:pre-wrap'>{_htmlesc(message)}</p>"
        f"<p style='color:#888;font-size:12px'>מקור: {settings.site_url}/admin/messages</p></div>"
    )
    sent_any = False
    for team_email in _team_email_list():
        if email_service.send_email(team_email, f"פנייה חדשה: {subject or 'ממרכז העזרה'}", body_html):
            sent_any = True
    if not sent_any:
        # Email delivery failed (SMTP not configured or down) — store a
        # dashboard notification so the admin still knows a message arrived.
        notification_service.notify_user(
            db, user_id=None,  # broadcast to admin
            title="📥 פנייה חדשה במרכז העזרה",
            message=f"{name or 'אנונימי'} ({email}): {message[:120]}",
            link="/admin/messages",
        )

    return RedirectResponse("/help?sent=1", status_code=303)


@app.get("/admin/messages")
def admin_messages_page(request: Request, db: Session = Depends(get_db)):
    """Inbox for help-center contact messages — read, reply-to by email,
    and mark as handled. Requires the admin session."""
    if not _is_admin(request):
        return RedirectResponse("/admin/login", status_code=303)
    from sqlalchemy import desc as _desc
    messages = (
        db.query(models_module.SupportMessage)
        .order_by(_desc(models_module.SupportMessage.created_at))
        .limit(100)
        .all()
    )
    return templates.TemplateResponse("admin_messages.html", {
        "request": request,
        "messages": messages,
        "csrf_token": csrf_service.generate_csrf_token(),
    })


@app.post("/admin/messages/{message_id}/status")
async def admin_message_status(message_id: int, request: Request, db: Session = Depends(get_db), _auth: bool = Depends(require_admin_csrf)):
    """Mark a support message as new/read/replied/closed."""
    msg = db.query(models_module.SupportMessage).filter(models_module.SupportMessage.id == message_id).first()
    if not msg:
        return JSONResponse({"status": "error", "message": "הודעה לא נמצאה"}, status_code=404)
    form = await request.form()
    status = str(form.get("status") or "read")
    if status not in ("new", "read", "replied", "closed"):
        status = "read"
    msg.status = status
    db.commit()
    return JSONResponse({"status": "ok", "message_id": message_id, "status": status})


@app.get("/privacy")
def privacy_page(request: Request):
    return templates.TemplateResponse("privacy.html", {
        "request": request,
        "contact_email": settings.smtp_from_email or settings.admin_email or "hello@yourdomain.com",
        "site_name": "SmartShop",
    })


@app.get("/terms")
def terms_page(request: Request):
    return templates.TemplateResponse("terms.html", {
        "request": request,
        "contact_email": settings.smtp_from_email or settings.admin_email or "hello@yourdomain.com",
        "site_name": "SmartShop",
    })
