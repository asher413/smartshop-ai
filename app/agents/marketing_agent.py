import logging

import requests

from app.core.config import settings
from app.agents import ai_gate
from app.agents.gemini_client import gemini_generate_text, gemini_generate_json

logger = logging.getLogger(__name__)


def _instance_cache(maxsize=128):
    """
    Instance-scoped memoization cache.

    הבאג במקור: `@lru_cache` על מתודה עם `self` שומר רפרנס חזק ל-`self` בתוך
    ה-cache הגלובלי של הפונקציה, כך שאף instance לא נאסף ע"י ה-GC לעולם (memory leak
    אמיתי בתהליך ארוך-חיים כמו שרת FastAPI). הפתרון: cache פר-instance ב-dict רגיל,
    שנעלם כשה-instance עצמו נאסף.
    """
    def decorator(func):
        def wrapper(self, *args, **kwargs):
            cache = self.__dict__.setdefault(f"_cache_{func.__name__}", {})
            key = (args, tuple(sorted(kwargs.items())))
            if key not in cache:
                if len(cache) >= maxsize:
                    cache.pop(next(iter(cache)))
                cache[key] = func(self, *args, **kwargs)
            return cache[key]
        return wrapper
    return decorator


class MarketingAgent:
    def __init__(self):
        if not settings.google_api_key:
            logger.warning("GOOGLE_API_KEY not set - MarketingAgent will return fallback text only.")

    @_instance_cache(maxsize=128)
    def generate_ad_copy(self, product_name, platform="Facebook"):
        logger.info("Generating ad copy for %s on %s", product_name, platform)
        if not ai_gate.ai_available():
            return f"🔥 {product_name} — הדיל שאסור לפספס! מחיר שווה, איכות גבוהה. לחצו עכשיו לפני שיגמר."
        prompt = f"""
            צור תוכן מודעה בעלת אחוז המרה גבוה עבור {platform} למוצר: {product_name}.
            התמקד בתועלות (Benefits), השתמש באימוג'י, וצור הנעה חזקה לפעולה.
            אל תמציא נתונים (למשל אחוזי הנחה או ביקורות) שלא סופקו לך.
            שפה: עברית. התשובה צריכה להיות קצרה ותמציתית.
            """
        try:
            response = gemini_generate_text(prompt, timeout_seconds=8.0, temperature=0.7, max_output_tokens=300)
            return response.strip() if response else f"🔥 {product_name} — הדיל שאסור לפספס!"
        except Exception as e:
            logger.error("Error generating ad copy for %s: %s", product_name, e)
            return f"שגיאה ביצירת תוכן מודעה: {e}"

    @_instance_cache(maxsize=128)
    def generate_urgency_badge(self, product_name, source, stock_count, price_dropped=False):
        logger.info("Generating urgency badge for %s", product_name)
        if not ai_gate.ai_available():
            if price_dropped:
                return "המחיר ירד!"
            if (stock_count or 0) > 0 and (stock_count or 0) < 5:
                return "המלאי מוגבל!"
            return "דיל חם!"
        prompt = f"""
            צור תגית דחיפות (Urgency Badge) קצרה מאוד וקליטה עבור המוצר {product_name} ב-{source}.
            נתונים אמיתיים: מלאי: {stock_count}, ירידת מחיר: {price_dropped}.
            אל תמציא מספרים - הישען רק על הנתונים שסופקו.
            דוגמאות: "המלאי מוגבל!", "המחיר הנמוך ביותר החודש!".
            שפה: עברית. החזר טקסט בלבד (מקסימום 5 מילים).
            """
        try:
            response = gemini_generate_text(prompt, timeout_seconds=6.0, temperature=0.4, max_output_tokens=40)
            return response.strip() if response else "מלאי מוגבל"
        except Exception as e:
            logger.error("Error generating urgency badge for %s: %s", product_name, e)
            return "מלאי מוגבל"

    def generate_comparison_summary(self, product_name, offers_list, pros=None, cons=None):
        """מייצר 'AI Verdict' בעברית שמשווה הצעות, כולל 'למי זה מתאים'."""
        logger.info("Generating comparison summary for %s", product_name)
        if not ai_gate.ai_available():
            return f"{product_name} — מחיר תחרותי לפי ההצעות הזמינות. מתאים למי שמחפש ערך טוב."
        pros_text = f"יתרונות: {', '.join(pros)}." if pros else ""
        cons_text = f"חסרונות: {', '.join(cons)}." if cons else ""

        prompt = f"""
            Analyze these real offers for {product_name}: {offers_list}.
            {pros_text}
            {cons_text}
            Write a 1-2 sentence 'AI Verdict' in Hebrew, grounded only in the data given above.
            Focus on price vs shipping speed, and explicitly state "למי זה מתאים?" (Who is it for?).
            Keep it concise and conversion-oriented, but never invent facts not in the input.
            """
        try:
            response = gemini_generate_text(prompt, timeout_seconds=8.0, temperature=0.4)
            return response.strip() if response else "שגיאה ביצירת סיכום השוואה."
        except Exception as e:
            logger.error("Error generating comparison summary for %s: %s", product_name, e)
            return "שגיאה ביצירת סיכום השוואה."

    @_instance_cache(maxsize=128)
    def generate_coupon_suggestion(self, product_name, supplier):
        """
        חשוב: זו יכולה להיות רק 'הצעה' - אין לזייף קוד קופון אמיתי. אם אין קופון פעיל
        אמיתי, צריך להציג ל-frontend הודעה כנה ("אין קופון פעיל כרגע") ולא קוד מומצא,
        כדי לא להטעות לקוחות עם קוד שלא עובד בקופה.
        """
        logger.info("Generating coupon suggestion for %s from %s", product_name, supplier)
        return None  # אין מקור אמיתי לקופונים כרגע - ה-frontend צריך להתמודד עם None בעדינות

    def post_to_social_media(self, product_name, ad_copy):
        print(f"[MOCK] Post created for {product_name}: {ad_copy}")

    def send_whatsapp_deal(self, product_name, price, source, url, deal_hook=""):
        """Send deal-of-the-day to WhatsApp subscribers via Cloud API.
        Uses the WhatsApp Cloud API (Meta) — same pattern as the Telegram bot.
        Phone number ID + permanent access token are configured in .env or
        the admin settings panel."""
        phone_id = settings.whatsapp_phone_number_id
        token = settings.whatsapp_access_token
        if not phone_id or not token:
            return

        # Build a simple template message — no pre-approved template needed
        # for the first 24h of a customer-initiated conversation, but for
        # proactive outbound (deal broadcast) we use a text message via
        # the /messages endpoint.
        body = (
            f"🔥 *{deal_hook or 'דיל היום'}*\n\n"
            f"*{product_name}*\n"
            f"מחיר: ₪{price} | ספק: {source}\n\n"
            f"👇 לפרטים ורכישה:\n{url}"
        )
        api_url = f"https://graph.facebook.com/v22.0/{phone_id}/messages"
        # Send to all registered WhatsApp subscribers (stored in a simple
        # JSON file for now — production would use the DB).
        subscribers = self._get_whatsapp_subscribers()
        for recipient in subscribers:
            try:
                payload = {
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": recipient,
                    "type": "text",
                    "text": {"body": body, "preview_url": True},
                }
                requests.post(
                    api_url,
                    json=payload,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=10,
                )
            except requests.RequestException as e:
                logger.error("WhatsApp send to %s failed: %s", recipient, e)

    def _get_whatsapp_subscribers(self) -> list[str]:
        """Return list of phone numbers subscribed to daily deal broadcasts.
        Stored in a simple file; production should use the DB."""
        import json, os
        path = os.path.join(os.path.dirname(__file__), "..", "data", "whatsapp_subscribers.json")
        try:
            with open(path, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _save_whatsapp_subscriber(self, phone: str):
        import json, os
        path = os.path.join(os.path.dirname(__file__), "..", "data", "whatsapp_subscribers.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        subs = self._get_whatsapp_subscribers()
        if phone not in subs:
            subs.append(phone)
            with open(path, "w") as f:
                json.dump(subs, f)

    def send_telegram_viral_post(self, product_name, price, source, url):
        token = settings.telegram_bot_token
        chat_id = settings.telegram_chat_id
        if not token or not chat_id:
            return

        message = (
            f"🔥 *דיל ויראלי זוהה!*\n\n"
            f"מוצר: {product_name}\n"
            f"מחיר חדש: ₪{price}\n"
            f"ספק: {source}\n\n"
            f"👇 לפרטים ורכישה:\n{url}"
        )
        telegram_url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            requests.post(telegram_url, data={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}, timeout=10)
        except requests.RequestException as e:
            logger.error("Telegram send failed: %s", e)

    def generate_ab_subject_lines(self, products_summary):
        import json

        if not ai_gate.ai_available():
            return {"variant_a": "🔥 הדילים החמים של השבוע", "variant_b": "🛍️ אל תפספסו — הדילים הטובים ביותר"}
        prompt = f"""
            Generate two distinct subject lines in Hebrew for a newsletter featuring: {products_summary}.
            Variant A: Direct and benefit-oriented.
            Variant B: Curiosity-driven or urgency-based (FOMO), but not misleading.
            Return JSON: {{"variant_a": "string", "variant_b": "string"}}
            """
        try:
            data = gemini_generate_json(prompt, timeout_seconds=8.0)
            if data and data.get("variant_a") and data.get("variant_b"):
                return data
        except Exception as e:
            logger.error("AB subject line generation failed: %s", e)
        return {"variant_a": products_summary, "variant_b": products_summary}

    def summarize_reviews(self, product_name: str, reviews_text: str):
        if not ai_gate.ai_available():
            return '{"pros": ["איכות טובה", "מחיר הוגן"], "cons": []}'
        prompt = f"""
            Summarize the following reviews for '{product_name}' into 3 main pros and 2 main cons,
            using only what the reviews actually say.
            Reviews: {reviews_text}
            Return JSON: {{"pros": list, "cons": list}}
            """
        data = gemini_generate_json(prompt, timeout_seconds=8.0)
        return json.dumps(data) if data else '{"pros": [], "cons": []}'
