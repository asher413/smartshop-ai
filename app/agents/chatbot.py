import json
import logging
import re

from app.core.config import settings
from app.services.semantic_search_service import SemanticSearchService
from app.agents.gemini_client import gemini_generate_text

logger = logging.getLogger(__name__)

_AI_BUSY_MSG = (
    "המערכת עמוסה כרגע — השאירו פרטים ואנחנו נחזור אליכם בהקדם. "
    "בינתיים, נסו את החיפוש החכם או גלשו בקטגוריות!"
)

# ── Hebrew FAQ: common questions + canned answers (no AI needed) ──
_HEBREW_FAQ = {
    "משלוח": "📦 זמן משלוח ממוצע: 7-21 ימי עסקים, תלוי בספק. AliExpress Choice מספק תוך 10-14 ימים בד\"כ. Amazon בד\"כ 7-12 ימים. eBay משתנה בין מוכרים.",
    "החזרה": "↩️ מדיניות החזרות: כל ספק מספק הגנת קונה. באפשרותכם לפתוח תביעה מול הספק אם המוצר לא הגיע או לא תואם. אנחנו אתר השוואת מחירים והמלצות (אפילייט) — לא חנות ישירה.",
    "תשלום": "💳 התשלום מתבצע ישירות מול הספק (AliExpress, Amazon, eBay). אנחנו לא מעבדים תשלומים — רק מפנים לדילים הטובים ביותר.",
    "קופון": "🎟️ קופונים פעילים זמינים בדף /coupons. אפשר להעתיק קוד בלחיצה ולהדביק בקופה אצל הספק.",
    "מעקב": "📮 מעקב משלוחים זמין באזור האישי. הזינו מספר מעקב ונוכל לרענן את הסטטוס מול ספקי המשלוחים.",
    "הרשמה": "📝 ההרשמה בחינם! לחצו על 'התחבר/הרשמה' למעלה. אפשר גם להתחבר עם Google.",
    "צור קשר": "📧 אפשר לשלוח לנו הודעה דרך מרכז העזרה (/help) ואנחנו נחזור אליכם במייל בהקדם.",
    "מחיר": "💰 אנחנו משווים מחירים בין AliExpress, Amazon, eBay ועוד — ומציגים את המחיר הזול ביותר בכל רגע. המחירים מתעדכנים אוטומטית.",
    "אמינות": "🛡️ אנחנו מציגים רק מוצרים שנבדקו ואומתו. הדירוגים והביקורות נלקחים מהספקים עצמם ומהמשתמשים שלנו.",
    "עמלה": "💡 אנחנו אתר שותפים (Affiliate). כשאתם קונים דרך הקישורים שלנו — אנחנו מקבלים עמלה קטנה מהספק, ואתם לא משלמים יותר.",
    "חיפוש": "🔍 חפשו מוצרים בסרגל החיפוש למעלה, או גלשו בקטגוריות. אפשר גם לחפש לפי תמונה (לחצו על אייקון המצלמה).",
    "מתנה": "🎁 מחפשים מתנה? ספרו לנו למי ובאיזה תקציב — ונמצא רעיונות מהקטלוג. אפשר גם לבחור קטגוריה ולסנן לפי מחיר.",
}

# ── Greeting patterns in Hebrew (respond instantly, no search needed) ──
_GREETINGS = ["שלום", "היי", "הי", "אהלן", "בוקר טוב", "ערב טוב", "לילה טוב", "מה נשמע", "מה קורה", "hello", "hi", "hey"]
_HELP_WORDS = ["עזרה", "עזור", "תעזור", "מה אפשר", "איך", "help", "מה אתם", "מי אתם"]

_GREETING_RESPONSE = (
    "👋 היי! ברוכים הבאים לדילבורסה — בורסת הדילים החכמה.\n\n"
    "אני כאן לעזור לכם למצוא את הדילים הכי משתלמים!\n"
    "• חפשו מוצר בסרגל החיפוש 🔍\n"
    "• גלשו בקטגוריות 📂\n"
    "• שאלו אותי שאלות על משלוחים, החזרות, קופונים ועוד\n"
    "• חפשו לפי תמונה 📷\n\n"
    "איך אפשר לעזור?"
)

_HELP_RESPONSE = (
    "ℹ️ הנה מה שאפשר לעשות בדילבורסה:\n\n"
    "🔍 חיפוש — חפשו כל מוצר בסרגל למעלה. אנחנו משווים מחירים בין AliExpress, Amazon, eBay, Temu ועוד.\n"
    "📂 קטגוריות — גלשו לפי קטגוריות: אלקטרוניקה, אופנה, גאדג'טים, כלי עבודה ועוד.\n"
    "📷 חיפוש לפי תמונה — צלמו מוצר והעלו, נמצא מוצרים דומים.\n"
    "❤️ מועדפים — שמרו מוצרים שמוצאים חן בעיניכם.\n"
    "💰 השוואת מחירים — בכל מוצר תראו מאיפה הכי זול לקנות.\n"
    "🔔 התראות מחיר — הגדירו יעד מחיר וקבלו התראה כשהוא יורד.\n"
    "🎟️ קופונים — בדקו קופונים פעילים ב-/coupons.\n"
    "📧 מרכז עזרה — שלחו לנו הודעה דרך /help.\n\n"
    "אם יש לכם שאלה ספציפית — תשאלו!"
)


class StoreChatbot:
    def __init__(self):
        try:
            self.semantic_search = SemanticSearchService()
        except Exception as e:
            logger.error("Semantic search unavailable, chatbot will run without it: %s", e)
            self.semantic_search = None

    def _llm_available(self) -> bool:
        return bool(settings.google_api_key)

    # ── No-AI answer engine ────────────────────────────────────────

    def _match_faq(self, query: str) -> str | None:
        """Check if the query matches any FAQ topic. Returns answer or None."""
        q_lower = query.lower()
        best = None
        best_len = 0
        for keyword, answer in _HEBREW_FAQ.items():
            kw_lower = keyword.lower()
            if kw_lower in q_lower or q_lower in kw_lower:
                if len(kw_lower) > best_len:
                    best = answer
                    best_len = len(kw_lower)
        if best:
            return best
        # Fuzzy: check word overlap (uses \w+ which only matches ASCII;
        # Hebrew fallback: split on spaces and common punctuation)
        q_words = set(q_lower.replace('?',' ').replace(',',' ').replace('.',' ').split())
        for keyword, answer in _HEBREW_FAQ.items():
            kw_words = set(keyword.lower().split())
            if q_words & kw_words:
                return answer  # first match wins
        return None

    def _is_greeting(self, query: str) -> bool:
        q_stripped = query.strip().lower()
        for g in _GREETINGS:
            if q_stripped.startswith(g) or q_stripped == g:
                return True
        return False

    def _is_help_request(self, query: str) -> bool:
        q_lower = query.strip().lower()
        return any(w in q_lower for w in _HELP_WORDS)

    def _smart_noai_answer(self, query: str, db_session) -> str:
        """Comprehensive no-AI response engine:
        1. Greetings → friendly welcome
        2. Help requests → feature overview
        3. FAQ matches → canned answers
        4. Product search → catalog results
        5. Fallback → suggestion to browse categories
        """
        # 1. FAQ check FIRST (before greetings/help, so "איך המשלוח" gets the FAQ answer)
        faq_answer = self._match_faq(query)
        if faq_answer:
            return faq_answer

        # 2. Greetings (only for very short queries)
        if self._is_greeting(query) and len(query.strip()) < 15:
            return _GREETING_RESPONSE

        # 3. Help (only if no FAQ matched and query is short)
        if self._is_help_request(query) and len(query.strip()) < 20:
            return _HELP_RESPONSE

        # 4. Product search (the core fallback)
        catalog = self._search_catalog_answer(query, db_session)
        return catalog

    def _search_catalog_answer(self, query: str, db_session) -> str:
        """Search the product catalog and return top 3 matches."""
        from app.core.models import Product
        like = f"%{query}%"
        results = (
            db_session.query(Product)
            .filter(Product.is_active == True, Product.is_verified == True)
            .filter(
                Product.name.ilike(like)
                | Product.description.ilike(like)
                | Product.category.ilike(like)
            )
            .order_by(Product.buying_score.desc())
            .limit(5)
            .all()
        )
        if not results:
            return (
                "🔍 לא מצאנו התאמה מדויקת בקטלוג.\n\n"
                "אפשרויות:\n"
                "• חפשו במילים אחרות\n"
                "• גלשו בקטגוריות\n"
                "• שלחו לנו הודעה במרכז העזרה (/help) ואנחנו נעזור"
            )
        lines = ["🔍 הנה מה שמצאנו עבורך:\n"]
        for i, p in enumerate(results):
            rating = f"⭐{round(p.rating, 1)}" if p.rating else "ללא דירוג"
            source = f" ({p.source_adapter})" if p.source_adapter else ""
            lines.append(
                f"{i + 1}. **{p.name}**{source} — ₪{int(p.price or 0)} | {rating} | "
                f"[צפה במוצר](/product/{p.id})"
            )
        lines.append(
            "\n💡 טיפ: הקלידו מילות מפתח מדויקות יותר, או גלשו בקטגוריות לסינון טוב יותר."
        )
        return "\n".join(lines)

    # ── AI-powered methods ─────────────────────────────────────────

    def analyze_sentiment(self, query):
        if not self._llm_available():
            return "neutral"
        text = gemini_generate_text(
            "Analyze the sentiment of this customer message. Return only one word: 'angry', 'neutral', or 'happy'.",
            system="",
            timeout_seconds=6.0,
            temperature=0.0,
        )
        return text.strip().lower() if text else "neutral"

    def ask(self, customer_query, db_session, mode="standard"):
        # ── No AI key → smart no-AI answer ──
        if not self._llm_available():
            return self._smart_noai_answer(customer_query, db_session)

        # ── Circuit open (quota exhausted) → no-AI with polite notice ──
        from app.agents import ai_gate
        if not ai_gate.ai_available():
            catalog = self._smart_noai_answer(customer_query, db_session)
            return catalog + "\n\n" + _AI_BUSY_MSG

        # ── AI path ──
        try:
            related_ids = []
            if self.semantic_search is not None:
                related_ids = self.semantic_search.search_intent(customer_query)
            from app.core.models import Product

            related_products = []
            if related_ids:
                related_products = db_session.query(Product).filter(Product.id.in_(related_ids)).all()

            context = (
                "You are a helpful, honest shopping concierge for an affiliate deals store. "
                "Ground every recommendation only in the catalog data given below - never invent "
                "prices, ratings, or stock status.\n"
                "Relevant items from our catalog:\n"
            )
            for p in related_products:
                offers_text = json.dumps(p.offers) if getattr(p, "offers", None) else "No live offers"
                context += f"- ID: {p.id}, Name: {p.name}, Description: {p.description}, Offers: {offers_text}\n"

            if mode == "gift":
                context += (
                    "\nThe user is looking for a gift. Ask about the recipient's age/hobbies if needed, "
                    "and explain briefly why each suggestion fits.\n"
                )

            context += (
                "\nWhen comparing suppliers, only mention price/shipping differences that are actually "
                "present in the offers data above. If a product is out of stock, say so and suggest an "
                "in-stock alternative from the catalog if one exists.\n"
                "Always mention the product ID when recommending something specific."
            )

            text = gemini_generate_text(
                customer_query,
                system=context,
                timeout_seconds=8.0,
                temperature=0.0,
            )
            if not text:
                if not ai_gate.ai_available():
                    catalog = self._smart_noai_answer(customer_query, db_session)
                    return catalog + "\n\n" + _AI_BUSY_MSG
                return "סליחה, אני מתקשה לענות כרגע. תוכל לנסות שוב?"
            return text
        except Exception as e:
            logger.error("Chatbot failed to answer: %s", e)
            # Final fallback: no-AI answer
            try:
                return self._smart_noai_answer(customer_query, db_session)
            except Exception:
                return "סליחה, אני מתקשה לענות כרגע. תוכל לנסות שוב?"
