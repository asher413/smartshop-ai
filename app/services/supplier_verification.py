"""
Shared supplier-verification helpers.

Single source of truth for the per-supplier "what counts as configured" and
"does this affiliate link carry commission tracking" rules. Used by:

  * scripts/verify_suppliers.py   — the CLI full-registration verifier
  * app/services/supplier_status_service.py — the admin "בדיקת משיכה מלאה"
    button

Keeping this in `app/` (not `scripts/`) means the admin panel never depends
on a CLI script, and the rules can never drift between the two entry points.
"""
from app.core.config import settings
from app.services.settings_service import EDITABLE

# --- What counts as "configured" per supplier -------------------------------
# Settings attrs (not env names) required for the official-API path.
REQUIRED_ATTRS = {
    "aliexpress": ["aliexpress_app_key", "aliexpress_app_secret"],
    "amazon": ["amazon_partner_tag", "amazon_paapi_access_key", "amazon_paapi_secret_key"],
    "ebay": ["ebay_app_id", "ebay_cert_id"],
    "temu": [],                       # no public API — scraping only
    "awin": ["awin_api_token", "awin_publisher_id"],
    "cj": ["cj_api_token", "cj_company_id"],
    "rakuten": ["rakuten_client_id", "rakuten_client_secret", "rakuten_account_id"],
    "bhphoto": [],                    # no public API — scraping only
}

# Which settings_service.run_test() service name maps to each adapter.
SERVICE_NAME = {
    "aliexpress": "aliexpress",
    "amazon": "amazon",
    "ebay": "ebay",
    "awin": "awin",
    "cj": "cj",
    "rakuten": "rakuten",
    # temu / bhphoto have no key test — scraping-only
}

# Commission/tracking markers each supplier's affiliate link MUST contain.
# A link that's just the raw product URL = no commission = FAIL.
# Semantics are OR per supplier (e.g. Temu: aff_id= OR ref=).
AFFILIATE_MARKERS = {
    # official promotion_link style, or the scraping fallback's ref= tracking
    "aliexpress": ["s.click.aliexpress.com", "a_aid=", "aff_", "ref="],
    "amazon": ["tag="],                                          # ?tag=<partner-tag>
    "ebay": ["campid="],                                         # eBay Partner Network
    "temu": ["aff_id=", "ref="],                                 # affiliate id / click ref
    "awin": ["awinaffid="],                                      # publisher id
    "cj": ["aid=", "site="],                                     # CJ pass-through buyUrl
    "rakuten": ["linksynergy.com"],                              # official deep links
    "bhphoto": ["ref="],                                         # click attribution ref
}

# Required extra attrs that produce a *tracking-less* link when missing
# (the adapter happily returns a raw URL — the verifier must catch that).
LINK_CRITICAL_ATTRS = {
    "amazon": ["amazon_partner_tag"],
    "ebay": ["ebay_campaign_id"],
}

# Pass-through suppliers: build_affiliate_link() returns the API's buyUrl
# unchanged because it ALREADY carries tracking (CJ Product Catalog).
# Returning the raw URL is the designed, correct behaviour for them.
PASS_THROUGH = {"cj"}

ENV_NAME = {attr: env for attr, env in EDITABLE.items()}


def env_for(attr: str) -> str:
    return ENV_NAME.get(attr, attr.upper())


def missing_keys(supplier: str) -> list[str]:
    """Settings attrs that are empty for this supplier ([] = fully configured)."""
    return [attr for attr in REQUIRED_ATTRS.get(supplier, []) if not (getattr(settings, attr, "") or "")]


def check_affiliate_link(supplier: str, raw_url: str, link: str) -> tuple[bool, str]:
    """(ok, message) — does `link` carry the supplier's tracking params?"""
    if not link or not str(link).startswith("http"):
        return False, f"קישור עמלה ריק/לא תקין: {str(link)[:80]!r}"
    markers = AFFILIATE_MARKERS.get(supplier, [])
    # The raw URL from the supplier's API may ALREADY be a tracked link
    # (AliExpress promotion_link, CJ buyUrl) — that satisfies the check.
    if not markers:
        return True, "קישור עמלה נוצר ✅"
    if any(m in raw_url for m in markers):
        return True, "ה-URL מהספק כבר מכיל מעקב עמלה ✅"
    if link == raw_url:
        if supplier in PASS_THROUGH:
            return True, "pass-through — ה-URL מ-CJ כבר מכיל מעקב עמלה ✅"
        return False, "קישור העמלה זהה ל-URL המקורי — בלי פרמטרי מעקב, לא תתקבל עמלה"
    # Semantics are OR per supplier: at least one tracking marker must be
    # present AND non-empty (e.g. Temu: aff_id= OR ref=). A marker ending in
    # '=' with an empty value (?tag=) is not real tracking.
    def _valid(marker: str) -> bool:
        if marker not in link:
            return False
        if marker.endswith("="):
            rest = link.split(marker, 1)[1]
            return bool(rest.strip())
        return True

    present = [m for m in markers if _valid(m)]
    if not present:
        return False, f"חסרים פרמטרי עמלה תקינים בקישור: {' / '.join(markers)} — בדקו את ההגדרות"
    return True, f"קישור עמלה תקין (מכיל {present[0]}) ✅"
