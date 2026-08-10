"""
'Where is my product right now' — package tracking.

Uses 17TRACK's API (https://api.17track.net), which is the standard choice
for this because it auto-detects the carrier across 1,700+ postal/courier
networks worldwide — exactly what you need when packages ship via random
regional carriers (China Post, Yanwen, Cainiao, ePacket...) that AliExpress/
Temu orders typically use, and you don't know in advance which one.

Free tier covers a meaningful number of tracked packages/month — check
current limits at https://features.17track.net/en/api before relying on
volume beyond a small-to-medium catalog, since pricing tiers change.

Design: register_tracking() is called once when an order gets a tracking
number (e.g. from the fulfillment agent or manual entry). refresh_status()
is called periodically (see workers/) or on-demand when a user opens their
order in the personal area, and writes the latest human-readable event
onto the Order row so the site can show it without an extra API round-trip
on every page view.
"""
import logging
import requests

from app.core.config import settings
from app.core.models import Order

logger = logging.getLogger(__name__)

API_BASE = "https://api.17track.net/track/v2.2"

# 17TRACK status codes -> our simplified shipment_status + Hebrew label
STATUS_MAP = {
    0: ("not_registered", "טרם נרשם למעקב"),
    10: ("in_transit", "בדרך אליך"),
    20: ("in_transit", "יצא ממדינת המקור"),
    30: ("in_transit", "הגיע למדינת היעד"),
    35: ("out_for_delivery", "יצא לחלוקה"),
    40: ("delivered", "נמסר בהצלחה! 📦"),
    50: ("exception", "בעיה במשלוח — יש לבדוק מול הספק"),
}


def _headers():
    return {"17token": settings.seventeen_track_api_key, "Content-Type": "application/json"}


def register_tracking(tracking_number: str, carrier_code: str | None = None) -> bool:
    if not settings.seventeen_track_api_key:
        logger.info("No 17TRACK API key configured — tracking registration skipped.")
        return False
    payload = [{"number": tracking_number}]
    if carrier_code:
        payload[0]["carrier"] = carrier_code
    try:
        resp = requests.post(f"{API_BASE}/register", json=payload, headers=_headers(), timeout=10)
        resp.raise_for_status()
        return True
    except Exception:
        logger.exception("17TRACK registration failed for %s", tracking_number)
        return False


def refresh_status(db, order: Order) -> dict:
    """Pull the latest tracking event for one order and persist it."""
    if not order.tracking_number:
        return {"status": "no_tracking_number", "message": "עדיין לא הוזן מספר מעקב להזמנה זו"}

    if not settings.seventeen_track_api_key:
        return {"status": "unavailable", "message": "מעקב משלוחים לא מוגדר כרגע באתר (חסר מפתח API)"}

    try:
        resp = requests.post(
            f"{API_BASE}/gettrackinfo",
            json=[{"number": order.tracking_number}],
            headers=_headers(),
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        accepted = data.get("data", {}).get("accepted", [])
        if not accepted:
            return {"status": "unavailable", "message": "עדיין אין נתוני מעקב זמינים למשלוח זה"}

        track_info = accepted[0].get("track_info", {})
        latest_status_code = track_info.get("latest_status", {}).get("status", 0)
        events = track_info.get("tracking", {}).get("providers", [{}])[0].get("events", [])
        latest_event = events[0].get("description", "") if events else ""

        simplified_status, hebrew_label = STATUS_MAP.get(latest_status_code, ("in_transit", "בדרך אליך"))

        order.shipment_status = simplified_status
        order.shipment_last_event = latest_event or hebrew_label
        import datetime
        order.shipment_last_checked_at = datetime.datetime.utcnow()
        db.commit()

        return {
            "status": "ok",
            "shipment_status": simplified_status,
            "label": hebrew_label,
            "last_event": order.shipment_last_event,
        }
    except Exception:
        logger.exception("17TRACK status refresh failed for order %s", order.id)
        return {"status": "unavailable", "message": "לא הצלחנו לרענן את סטטוס המשלוח כרגע, נסו שוב מאוחר יותר"}
