"""
Central configuration. Replaces the old flat settings.py.
Uses pydantic-settings so a missing env var fails loudly at boot instead
of silently returning "" and breaking deep inside a scraper at 3am.
"""
from functools import lru_cache
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "development"
    database_url: str = "sqlite:///./dropship.db"
    redis_url: str = "redis://localhost:6379/0"
    site_url: str = "https://yourdomain.com"

    # AI
    google_api_key: str = ""
    gemini_api_key: str = ""
    openai_api_key: str = ""
    # Gemini model used by all AI agents. gemini-1.5-flash was deprecated in
    # 2026 (returned 404 on every call) — 2.5-flash is the current fast/cheap
    # default. Override via GEMINI_MODEL if you prefer 2.0-flash / 2.5-pro.
    gemini_model: str = "gemini-2.5-flash"

    # AliExpress
    aliexpress_app_key: str = ""
    aliexpress_app_secret: str = ""
    aliexpress_tracking_id: str = ""

    # Amazon
    amazon_partner_tag: str = ""
    amazon_paapi_access_key: str = ""
    amazon_paapi_secret_key: str = ""
    amazon_paapi_host: str = "webservices.amazon.com"

    # eBay — OAuth client-credentials needs BOTH the App ID (client id) and
    # the Cert ID (client secret) from your eBay Developer keyset.
    ebay_app_id: str = ""
    ebay_cert_id: str = ""
    ebay_campaign_id: str = ""

    # Temu (no public API today)
    temu_affiliate_id: str = ""

    # Awin — affiliate NETWORK (not a single merchant): one API/feed gives
    # access to thousands of merchants (Shein, Boohoo, many electronics
    # brands, regional retailers...) who'd otherwise each need their own
    # integration. This is usually the single highest-leverage connection
    # after your direct marketplace adapters, precisely because it's N
    # merchants for the integration cost of 1.
    awin_api_token: str = ""
    awin_publisher_id: str = ""

    # CJ Affiliate — second major affiliate network, same rationale as Awin
    cj_api_token: str = ""
    cj_company_id: str = ""

    # Rakuten Advertising (formerly LinkShare) — the network Etsy and many
    # big brands moved to. OAuth2 client-credentials: client_id + client_secret
    # from the developer portal, account_id = your network/site id.
    rakuten_client_id: str = ""
    rakuten_client_secret: str = ""
    rakuten_account_id: str = ""

    # Package tracking (see services/order_tracking_service.py)
    seventeen_track_api_key: str = ""

    # Session auth
    session_secret_key: str = "change_me_please_session_secret"

    # --- Transactional email (verification emails) ---
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_email: str = "no-reply@yourdomain.com"
    smtp_from_name: str = "SmartShop"

    # --- Google OAuth (Sign in with Google) ---
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""

    # Notifications
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # --- WhatsApp Cloud API (Meta) ---
    # Set up at developers.facebook.com → My Apps → WhatsApp → API Setup.
    # phone_number_id: the test phone number ID (e.g. 123456789012345)
    # access_token: permanent token from the WhatsApp Business Account
    # verify_token: arbitrary string you pick; Meta calls GET /api/whatsapp-webhook
    #   with ?hub.verify_token=... to prove you own the endpoint.
    whatsapp_phone_number_id: str = ""
    whatsapp_access_token: str = ""
    whatsapp_verify_token: str = ""

    # Instagram Graph API (Content Publishing) — see agents/instagram_agent.py
    instagram_access_token: str = ""
    instagram_account_id: str = ""

    # Team support emails (comma-separated) — the help-center contact form
    # and "talk to a human" messages are delivered to this list (plus the
    # admin email as a fallback).
    team_emails: str = ""

    # --- Web Push Notifications (VAPID) ---
    # Generate once: `python -c "from cryptography.hazmat.primitives.asymmetric import ec; from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, PublicFormat, NoEncryption; key=ec.generate_private_key(ec.SECP256R1()); print('VAPID_PRIVATE_KEY='+key.private_bytes(Encoding.DER, PrivateFormat.PKCS8, NoEncryption()).hex()); print('VAPID_PUBLIC_KEY='+key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo).hex())"`
    # Or use: `npx web-push generate-vapid-keys`
    # Copy both hex strings into .env (ignore the 04 prefix).
    vapid_private_key: str = ""
    vapid_public_key: str = ""
    vapid_claims_email: str = ""   # "mailto:admin@yourdomain.com" — required by the Web Push spec

    # Security / behavior
    # Admin login = ADMIN_EMAIL + ADMIN_SECRET_KEY (default password 12345 —
    # change it from the admin settings page once live). The session cookie
    # persists (Starlette default: 14 days) so a logged-in browser stays
    # logged in — admin_session_hours caps how long an admin session is valid
    # before a fresh login is required again.
    admin_email: str = ""
    admin_secret_key: str = "12345"
    admin_session_hours: int = 24
    default_affiliate_ref: str = "site"
    ip_lookup_timeout: float = 3.0

    @model_validator(mode="before")
    @classmethod
    def normalize_ai_keys(cls, values):
        if isinstance(values, dict):
            google_api_key = values.get("google_api_key")
            gemini_api_key = values.get("gemini_api_key")
            if not google_api_key and gemini_api_key:
                values["google_api_key"] = gemini_api_key
        return values

    def adapter_status(self) -> dict:
        """Which suppliers currently have real API creds vs. fall back to scraping."""
        return {
            "aliexpress": bool(self.aliexpress_app_key and self.aliexpress_app_secret),
            "amazon": bool(self.amazon_paapi_access_key and self.amazon_paapi_secret_key),
            "ebay": bool(self.ebay_app_id),
            "temu": False,
            "awin": bool(self.awin_api_token and self.awin_publisher_id),
            "cj": bool(self.cj_api_token and self.cj_company_id),
            "rakuten": bool(self.rakuten_client_id and self.rakuten_client_secret and self.rakuten_account_id),
            "bhphoto": False,  # no public API — always scraped, but it works
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
