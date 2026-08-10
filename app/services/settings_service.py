"""
Runtime settings editor — lets the site admin view, update and TEST the
AI / SMTP / Instagram credentials from the admin UI instead of hand-editing
.env. Values are persisted straight into .env (existing lines preserved,
new keys appended) AND applied to the in-memory Settings object so changes
take effect immediately, without a server restart.

Secrets are masked when shown; empty input on save = keep the existing value
(use the explicit "clear" checkbox to wipe a secret).
"""
import logging
import os
import smtplib
from pathlib import Path

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def _env_path() -> Path:
    """Accept both Path and plain string (e.g. tests overriding the file)."""
    return ENV_FILE if isinstance(ENV_FILE, Path) else Path(str(ENV_FILE))

# attr name on Settings -> env var name in .env
EDITABLE = {
    # AI
    "google_api_key": "GOOGLE_API_KEY",
    "gemini_model": "GEMINI_MODEL",
    # Site / admin
    "site_url": "SITE_URL",
    "admin_email": "ADMIN_EMAIL",
    "admin_secret_key": "ADMIN_SECRET_KEY",
    "session_secret_key": "SESSION_SECRET_KEY",
    # Team support emails (comma-separated)
    "team_emails": "TEAM_EMAILS",
    # Email (SMTP)
    "smtp_host": "SMTP_HOST",
    "smtp_port": "SMTP_PORT",
    "smtp_user": "SMTP_USER",
    "smtp_password": "SMTP_PASSWORD",
    "smtp_from_email": "SMTP_FROM_EMAIL",
    "smtp_from_name": "SMTP_FROM_NAME",
    # Instagram
    "instagram_access_token": "INSTAGRAM_ACCESS_TOKEN",
    "instagram_account_id": "INSTAGRAM_ACCOUNT_ID",
    # Suppliers — AliExpress / eBay / Amazon / Temu / Awin / CJ
    "aliexpress_app_key": "ALIEXPRESS_APP_KEY",
    "aliexpress_app_secret": "ALIEXPRESS_APP_SECRET",
    "aliexpress_tracking_id": "ALIEXPRESS_TRACKING_ID",
    "ebay_app_id": "EBAY_APP_ID",
    "ebay_cert_id": "EBAY_CERT_ID",
    "ebay_campaign_id": "EBAY_CAMPAIGN_ID",
    "amazon_partner_tag": "AMAZON_PARTNER_TAG",
    "amazon_paapi_access_key": "AMAZON_PAAPI_ACCESS_KEY",
    "amazon_paapi_secret_key": "AMAZON_PAAPI_SECRET_KEY",
    "temu_affiliate_id": "TEMU_AFFILIATE_ID",
    "awin_api_token": "AWIN_API_TOKEN",
    "awin_publisher_id": "AWIN_PUBLISHER_ID",
    "cj_api_token": "CJ_API_TOKEN",
    "cj_company_id": "CJ_COMPANY_ID",
    "rakuten_client_id": "RAKUTEN_CLIENT_ID",
    "rakuten_client_secret": "RAKUTEN_CLIENT_SECRET",
    "rakuten_account_id": "RAKUTEN_ACCOUNT_ID",
    # Tracking + notifications + Google OAuth
    "seventeen_track_api_key": "SEVENTEEN_TRACK_API_KEY",
    "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
    "telegram_chat_id": "TELEGRAM_CHAT_ID",
    "whatsapp_phone_number_id": "WHATSAPP_PHONE_NUMBER_ID",
    "whatsapp_access_token": "WHATSAPP_ACCESS_TOKEN",
    "whatsapp_verify_token": "WHATSAPP_VERIFY_TOKEN",
    "vapid_private_key": "VAPID_PRIVATE_KEY",
    "vapid_public_key": "VAPID_PUBLIC_KEY",
    "vapid_claims_email": "VAPID_CLAIMS_EMAIL",
    "google_oauth_client_id": "GOOGLE_OAUTH_CLIENT_ID",
    "google_oauth_client_secret": "GOOGLE_OAUTH_CLIENT_SECRET",
}

SECRET_ENV = {
    "GOOGLE_API_KEY",
    "SMTP_PASSWORD",
    "INSTAGRAM_ACCESS_TOKEN",
    "ADMIN_SECRET_KEY",
    "SESSION_SECRET_KEY",
    "ALIEXPRESS_APP_SECRET",
    "AMAZON_PAAPI_SECRET_KEY",
    "EBAY_CERT_ID",
    "AWIN_API_TOKEN",
    "CJ_API_TOKEN",
    "RAKUTEN_CLIENT_SECRET",
    "SEVENTEEN_TRACK_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "WHATSAPP_ACCESS_TOKEN",
    "GOOGLE_OAUTH_CLIENT_SECRET",
}


def _mask(value) -> str:
    if not value:
        return ""
    value = str(value)
    if len(value) <= 8:
        return "•" * len(value)
    return value[:4] + "••••••" + value[-3:]


def get_current() -> dict:
    """{ENV_NAME: masked-or-plain current value} for the settings form."""
    out = {}
    for attr, env in EDITABLE.items():
        val = getattr(settings, attr, "") or ""
        out[env] = _mask(val) if env in SECRET_ENV else str(val)
    return out


def _coerce(attr: str, value: str):
    if attr == "smtp_port":
        try:
            return int(value)
        except ValueError:
            return 587
    return value


def _read_lines() -> list[str]:
    path = _env_path()
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return f.readlines()


def save(payload: dict) -> list[str]:
    """Persist {ENV_NAME: value} to .env and mirror into the live Settings.
    Empty values only apply when the key isn't already set (or when the UI
    explicitly sent a clear request via a "delete" key). Returns the list
    of env names that actually changed."""
    lines = _read_lines()
    # Normalize: explicit clears come as {"KEY": "", "KEY__clear": "1"}
    values = {}
    for k, v in payload.items():
        if k.endswith("__clear"):
            continue
        env = str(k)
        if env not in EDITABLE.values():
            continue
        if str(v).strip():
            values[env] = str(v).strip()
        elif payload.get(f"{env}__clear") in ("1", "on", "true", True):
            values[env] = ""  # explicit wipe
        # else: empty + no clear flag -> keep existing, skip entirely

    changed = []
    for env, val in values.items():
        attr = next(a for a, e in EDITABLE.items() if e == env)
        setattr(settings, attr, _coerce(attr, val))
        changed.append(env)

    if changed:
        keys = set(values)
        out = []
        present = set()
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                out.append(line)
                continue
            k = stripped.split("=", 1)[0].strip()
            if k in keys:
                out.append(f"{k}={values[k]}\n")
                present.add(k)
            else:
                out.append(line)
        for k in keys:
            if k not in present:
                out.append(f"{k}={values[k]}\n")
        with open(_env_path(), "w", encoding="utf-8") as f:
            f.writelines(out)
    return changed


# --- Live connection tests -------------------------------------------------

# Which form fields feed each service test (env var names).
TEST_FIELDS = {
    "ai": ["GOOGLE_API_KEY", "GEMINI_MODEL"],
    "smtp": ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD"],
    "instagram": ["INSTAGRAM_ACCESS_TOKEN", "INSTAGRAM_ACCOUNT_ID"],
    "ebay": ["EBAY_APP_ID", "EBAY_CERT_ID"],
    "aliexpress": ["ALIEXPRESS_APP_KEY", "ALIEXPRESS_APP_SECRET", "ALIEXPRESS_TRACKING_ID"],
    "amazon": ["AMAZON_PARTNER_TAG", "AMAZON_PAAPI_ACCESS_KEY", "AMAZON_PAAPI_SECRET_KEY"],
    "awin": ["AWIN_API_TOKEN", "AWIN_PUBLISHER_ID"],
    "cj": ["CJ_API_TOKEN", "CJ_COMPANY_ID"],
    "rakuten": ["RAKUTEN_CLIENT_ID", "RAKUTEN_CLIENT_SECRET", "RAKUTEN_ACCOUNT_ID"],
    "telegram": ["TELEGRAM_BOT_TOKEN"],
    "whatsapp": ["WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_ACCESS_TOKEN"],
}


def run_test(service: str, overrides: dict) -> tuple[bool, str]:
    """Test a live connection. `overrides` maps Settings attrs to freshly
    typed form values (non-empty only); anything missing falls back to the
    currently saved settings, so a test always reflects what the user sees."""
    def val(attr: str) -> str:
        return overrides.get(attr) or (getattr(settings, attr, "") or "")

    if service == "ai":
        key = val("google_api_key")
        model = val("gemini_model") or "gemini-2.5-flash"
        if not key:
            return False, "GOOGLE_API_KEY ריק — הזינו מפתח לפני הבדיקה"
        try:
            from app.agents.gemini_client import gemini_generate_text
            # bypass_gate=True: test the KEY THE USER JUST TYPED (the gate
            # would read the SAVED key instead) and don't touch production
            # circuit-breaker state from a manual admin test.
            resp = gemini_generate_text(
                "ענה בדיוק במילה אחת: אוקי",
                timeout_seconds=12.0,
                bypass_gate=True,
                model=model,
            )
            if resp is None:
                return False, "החיבור איטי או חסום כרגע — נסו שוב בעוד כמה דקות (או בדקו שהרשת פתוחה ל-Google AI)"
            return True, f"החיבור עובד ✅ (מודל {model})"
        except Exception as e:
            return False, f"שגיאה: {str(e)[:160]}"

    if service == "smtp":
        host, port, user, password = val("smtp_host"), val("smtp_port"), val("smtp_user"), val("smtp_password")
        if not host or not user:
            return False, "SMTP לא מוגדר — מלאו Host ו-User לפני הבדיקה"
        try:
            with smtplib.SMTP(host, int(port or 587), timeout=10) as server:
                server.starttls()
                server.login(user, password)
            return True, f"החיבור והתחברות החשבון הצליחו ✅ ({host}:{port or 587})"
        except Exception as e:
            return False, f"שגיאה: {str(e)[:160]}"

    if service == "instagram":
        token, account_id = val("instagram_access_token"), val("instagram_account_id")
        if not token or not account_id:
            return False, "אינסטגרם לא מוגדר — מלאו token ו-Account ID לפני הבדיקה"
        try:
            resp = requests.get(
                f"https://graph.facebook.com/v23.0/{account_id}",
                params={"fields": "id,username", "access_token": token},
                timeout=15,
            )
            data = resp.json()
            if "username" in data or "id" in data:
                username = data.get("username") or data.get("id")
                return True, f"מחובר בהצלחה כחשבון @{username} ✅"
            err = data.get("error", {})
            return False, f"שגיאת API ({err.get('code', '?')}): {str(err.get('message', ''))[:140]}"
        except requests.RequestException as e:
            return False, f"שגיאת רשת: {str(e)[:140]}"

    if service == "ebay":
        app_id, cert_id = val("ebay_app_id"), val("ebay_cert_id")
        if not app_id or not cert_id:
            return False, "eBay לא מוגדר — מלאו App ID ו-Cert ID לפני הבדיקה"
        try:
            resp = requests.post(
                "https://api.ebay.com/identity/v1/oauth2/token",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"},
                auth=(app_id, cert_id),
                timeout=12,
            )
            if resp.status_code == 200 and resp.json().get("access_token"):
                return True, "מפתחות eBay תקינים — ה-API מחובר ✅"
            err = resp.json().get("error_description", resp.text)[:140]
            return False, f"שגיאה ({resp.status_code}): {err}"
        except requests.RequestException as e:
            return False, f"שגיאת רשת: {str(e)[:140]}"

    if service == "aliexpress":
        app_key, app_secret, tracking_id = val("aliexpress_app_key"), val("aliexpress_app_secret"), val("aliexpress_tracking_id")
        if not app_key or not app_secret:
            return False, "AliExpress לא מוגדר — מלאו App Key + App Secret לפני הבדיקה"
        try:
            from app.adapters.aliexpress_adapter import AliExpressAdapter
            adapter = AliExpressAdapter()
            adapter.app_key = app_key
            adapter.app_secret = app_secret
            adapter.tracking_id = tracking_id
            adapter.uses_official_api = True
            data = adapter._call("aliexpress.affiliate.hotproduct.query", {
                "page_size": "1", "target_currency": "USD", "target_language": "EN", "tracking_id": tracking_id,
            })
            if data:
                return True, f"המפתחות תקינים — ה-API של AliExpress הגיב ✅ (tracking: {tracking_id or 'ריק'})"
            return False, "המפתחות לא אומתו — ה-API לא החזיר תשובה (בדקו App Key/Secret, או שמתחם ה-API חסום)"
        except Exception as e:
            return False, f"שגיאה: {str(e)[:160]}"

    if service == "amazon":
        tag, access, secret = val("amazon_partner_tag"), val("amazon_paapi_access_key"), val("amazon_paapi_secret_key")
        if not tag or not access or not secret:
            return False, "Amazon לא מוגדר — מלאו Partner Tag + Access Key + Secret Key"
        try:
            from app.adapters.amazon_adapter import AmazonAdapter
            adapter = AmazonAdapter()
            adapter.partner_tag = tag
            adapter.access_key = access
            adapter.secret_key = secret
            adapter.uses_official_api = True
            data = adapter._call("SearchItems", {
                "Keywords": "headphones", "SearchIndex": "All", "ItemCount": 1,
                "Resources": ["ItemInfo.Title"],
            })
            errors = (data or {}).get("Errors", [])
            if data and not errors:
                return True, "המפתחות תקינים — Amazon PA-API הגיב עם תוצאות ✅"
            msg = errors[0].get("Message", "") if errors else "ה-API לא החזיר תוצאות"
            code = errors[0].get("Code", "") if errors else ""
            hints = {"InvalidSignature": "החתימה נכשלה — בדקו Access/Secret Key",
                     "InvalidParameterValue": "Partner Tag לא תקין",
                     "AccessDenied": "החשבון עדיין לא אושר ל-PA-API (דרושות 3 מכירות ב-180 הימים הראשונים)"}
            return False, f"שגיאה ({code}): {msg[:120]} {hints.get(code, '')}".strip()
        except Exception as e:
            return False, f"שגיאה: {str(e)[:160]}"

    if service == "cj":
        token, company = val("cj_api_token"), val("cj_company_id")
        if not token or not company:
            return False, "CJ לא מוגדר — מלאו API Token + Company ID"
        try:
            resp = requests.get(
                f"https://cjtok.cj.com/coupon/v1/coupons?advertiser-ids={company}&page-size=1",
                headers={"Authorization": f"Bearer {token}"},
                timeout=12,
            )
            if resp.status_code == 200:
                return True, "טוקן CJ תקין — ה-API הגיב ✅"
            return False, f"שגיאה ({resp.status_code}): {resp.text[:140]}"
        except requests.RequestException as e:
            return False, f"שגיאת רשת: {str(e)[:140]}"

    if service == "rakuten":
        client_id, client_secret, account_id = val("rakuten_client_id"), val("rakuten_client_secret"), val("rakuten_account_id")
        if not client_id or not client_secret or not account_id:
            return False, "Rakuten לא מוגדר — מלאו Client ID + Client Secret + Account ID לפני הבדיקה"
        try:
            import base64 as _b64
            basic = _b64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
            token_resp = requests.post(
                "https://api.linksynergy.com/token",
                headers={"Authorization": f"Bearer {basic}", "Content-Type": "application/x-www-form-urlencoded"},
                data={"scope": account_id},
                timeout=12,
            )
            if token_resp.status_code != 200:
                return False, f"שגיאה בקבלת טוקן ({token_resp.status_code}): {token_resp.text[:120]}"
            try:
                access_token = token_resp.json().get("access_token")
            except ValueError:
                return False, f"התשובה אינה JSON תקין: {token_resp.text[:120]}"
            if not access_token:
                return False, "התשובה לא הכילה access_token — בדקו את ה-Credentials"
            # Prove the token works: one product-search query.
            search = requests.get(
                "https://api.linksynergy.com/productsearch/1.0",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"keyword": "headphones", "max": 1, "pagenumber": 1, "language": "en_US"},
                timeout=12,
            )
            if search.status_code == 200:
                return True, f"מפתחות Rakuten תקינים — חיפוש מוצר עבד ✅ (account: {account_id})"
            return False, f"הטוקן התקבל אבל חיפוש המוצר נכשל ({search.status_code}): {search.text[:120]}"
        except requests.RequestException as e:
            return False, f"שגיאת רשת: {str(e)[:140]}"

    if service == "awin":
        token = val("awin_api_token")
        if not token:
            return False, "Awin לא מוגדר — מלאו את ה-API Token לפני הבדיקה"
        try:
            resp = requests.get(
                f"https://productdata.awin.com/datafeed/list/apikey/{token}",
                timeout=15,
            )
            if resp.status_code == 200:
                return True, "טוקן Awin תקין — רשימת הזנות נטענה ✅"
            return False, f"שגיאה ({resp.status_code}): {resp.text[:140]}"
        except requests.RequestException as e:
            return False, f"שגיאת רשת: {str(e)[:140]}"

    if service == "whatsapp":
        phone_id, token = val("whatsapp_phone_number_id"), val("whatsapp_access_token")
        if not phone_id or not token:
            return False, "WhatsApp לא מוגדר — מלאו Phone Number ID ו-Access Token לפני הבדיקה"
        try:
            resp = requests.get(
                f"https://graph.facebook.com/v22.0/{phone_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=12,
            )
            data = resp.json()
            if data.get("verified_name"):
                return True, f"WhatsApp מחובר — {data.get('verified_name')} ✅ (ID: {phone_id})"
            err = data.get("error", {})
            return False, f"שגיאת API ({err.get('code', '?')}): {str(err.get('message', ''))[:140]}"
        except requests.RequestException as e:
            return False, f"שגיאת רשת: {str(e)[:140]}"

    if service == "telegram":
        token = val("telegram_bot_token")
        if not token:
            return False, "טלגרם לא מוגדר — מלאו את Bot Token לפני הבדיקה"
        try:
            resp = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=12)
            data = resp.json()
            if data.get("ok"):
                bot = data.get("result", {}).get("username", "")
                return True, f"הבוט @{bot} מחובר ומאומת ✅"
            return False, f"שגיאה: {str(data.get('description', resp.text))[:140]}"
        except requests.RequestException as e:
            return False, f"שגיאת רשת: {str(e)[:140]}"

    return False, "שירות לא ידוע"
