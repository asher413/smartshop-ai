"""
Smart natural-language search — "אני מחפש מתנה לילד גיל 5" should return
real suitable products, not an empty result set.

Pipeline per query:
1. Intent parsing: LLM (Gemini) when GOOGLE_API_KEY is set, otherwise a
   robust Hebrew/English heuristic fallback (age regex, "מתנה" keywords,
   category keywords, budget regex) — the site must work without keys too.
2. Candidate retrieval: semantic (vector) search + keyword LIKE search.
3. Ranking: score each candidate by intent match (age-band fit, budget
   fit, keyword overlap) and return the strongest with a human-readable
   Hebrew reason per product ("מתאים לילד בן 5 כי מדובר בגאדג'ט פופולרי
   בטווח המחירים שביקשת").

The age fit heuristic is deliberately conservative: products whose names
mention 'baby', 'toddler' etc. get age-band boosters; everything else is
ranked by demand + budget, so we never *invent* suitability — we explain
why a match is plausible.
"""
import logging
import re

from sqlalchemy.orm import Session

from app.agents.gemini_client import gemini_generate_json
from app.core.config import settings
from app.core.models import Product
from app.services.product_matcher import name_similarity

logger = logging.getLogger(__name__)

BUDGET_RE = re.compile(r"(?:מתחת ל|עד|עד ל|בסביבות|בערך)\s*([\d,]+)\s*(?:ש\"?ח|₪|שקל|shekel|nis)?")
AGE_RE = re.compile(r"(?:גיל|בן|בת)\s*(\d+)")
GIFT_KEYWORDS = ("מתנה", "מתנות", "gift", "לבן שלי", "לבת שלי", "לילד", "לילדה", "לאמא", "לאבא", "לסבתא", "לסבא")

# Category keyword -> our catalog categories (free-text mapping used when
# there is no LLM to parse the query).
CATEGORY_HINTS = {
    "אלקטרוניקה": ["אלקטרוניקה", "סמארטפון", "טלפון", "מטען", "אוזניות", "headphone", "charger", "power bank", "מצלמה", "camera", "רמקול", "speaker", "טלוויזיה", "tv"],
    "גאדג'טים": ["גאדג'ט", "gadget", "סמארטצ'", "smartwatch", "שעון חכם", "גיימינג", "gaming", "משחק", "רובוט", "drone", "רחפן", "vr"],
    "לבית ולמטבח": ["מטבח", "בית", "kitchen", "home", "בלנדר", "קומקום", "ספל", "מחבת", "סירים", "תאורה", "lamp", "מנורה"],
    "כלי עבודה": ["עבודה", "tools", "מקדחה", "drill", "wrench", "פנס", "flashlight", "מסור", "saw"],
    "אביזרי רכב": ["רכב", "car", "auto", "רכב", "מכונית", "טלפון לרכב", "מטען לרכב", "car charger", "דשבורד", "dash cam"],
    "אופנה": ["אופנה", "fashion", "בגדים", "חולצה", "נעליים", "shoes", "שעון", "watch", "תיק", "bag", "ארנק", "wallet", "משקפי שמש", "sunglasses", "שרשרת", "תכשיטים", "jewelry"],
    "ספורט ופנאי": ["ספורט", "sport", "כושר", "fitness", "יוגה", "yoga", "מזרן", "משקולות", "weights", "אופניים", "bike", "קמפינג", "camping"],
    "יופי וטיפוח": ["יופי", "beauty", "טיפוח", "מברשת שיניים", "toothbrush", "מייבש שיער", "hair dryer", "מחליק שיער", "straightener", "עור", "skin", "בושם", "perfume"],
    "משחקים וצעצועים": ["צעצוע", "toy", "משחק לילדים", "לגו", "lego", "בובה", "doll", "פאזל", "puzzle", "קלפים", "cards", "משחקי קופסה"],
    "מוצרי תינוקות": ["תינוק", "baby", "עגלה", "stroller", "מוצץ", "pacifier", "החתלה", "diaper", "טיטולים", "מושב בטיחות", "car seat"],
    "משרד ומחשבים": ["משרד", "office", "מחשב", "computer", "מקלדת", "keyboard", "עכבר", "mouse", "מסך", "monitor", "לפטופ", "laptop", "כיסא משרדי", "chair"],
    "חיות מחמד": ["חיות", "pet", "כלב", "dog", "חתול", "cat", "מזון לחיות", "pet food", "צעצוע לכלב", "רצועה", "leash"],
    "מזון וחטיפים": ["אוכל", "חטיף", "snack", "מזון", "food", "קפה", "coffee", "תה", "tea", "שוקולד", "chocolate", "פופקורן", "בוטנים"],
    "גינון": ["גינה", "garden", "גינון", "עציץ", "pot", "מזלף", "watering", "תאורה לגינה", "זרעים", "seeds", "מסור ענפים", "pruning"],
    "צילום ומוזיקה": ["מצלמה", "camera", "צילום", "פוטו", "photo", "גיטרה", "guitar", "כינור", "מיקרופון", "microphone", "אוזניות סטודיו", "מקלדת מוזיקלית", "סאונדבר"],
    "תכשיטים ושעונים": ["תכשיט", "jewelry", "שרשרת", "necklace", "צמיד", "bracelet", "עגיל", "earring", "שעון", "watch", "טבעת", "ring", "טיטניום", "סטרלינג"],
    "ספרים ותחביבים": ["ספר", "book", "קריאה", "reading", "פאזל", "puzzle", "תחביב", "hobby", "ציור", "painting", "ערכת יצירה", "craft"],
    "בריאות ומטבח": ["בריאות", "health", "ויטמין", "vitamin", "ספורט", "עיסוי", "massage", "מסאג'ר", "סולם", "step", "משקל", "scale", "מד לחץ דם", "blood pressure"],
}

# Hebrew/English synonym groups — real product terms that mean the same
# thing in both languages, so "אוזניות" also matches "headphones" listings.
# Used to expand the LIKE search when there is no LLM.
SYNONYM_GROUPS = [
    ["אוזניות", "headphones", "earbuds", "אוזניות אלחוטיות"],
    ["מטען", "charger", "מטען אלחוטי", "power bank", "powerbank"],
    ["שעון", "watch", "שעון חכם", "smartwatch"],
    ["טלפון", "phone", "סמארטפון", "smartphone", "מכשיר"],
    ["מצלמה", "camera", "מצלמת אבטחה", "webcam"],
    ["רמקול", "speaker", "בלוטוס", "bluetooth"],
    ["מקלדת", "keyboard"],
    ["עכבר", "mouse"],
    ["מסך", "monitor", "צג"],
    ["תיק", "bag", "backpack", "ילקוט", "תרמיל"],
    ["נעליים", "shoes", "סניקרס", "sneakers"],
    ["חולצה", "shirt", "t-shirt", "טי-שירט", "blouse"],
    ["מנורה", "lamp", "light", "תאורה"],
    ["בלנדר", "blender"],
    ["קומקום", "kettle", "קומקום חשמלי"],
    ["מזרן", "mat", "מזרן יוגה", "yoga mat"],
    ["רובוט", "robot", "רובוט שואב", "vacuum", "שואב אבק"],
    ["משחק", "game", "toy", "צעצוע", "משחקי קופסה", "board game"],
    ["בובה", "doll"],
    ["תינוק", "baby", "תינוקת"],
    ["כלב", "dog"],
    ["חתול", "cat"],
]

# Hebrew product words that appear inside listings with a common prefix
# (e.g. "מטען" matches "מטענים" and "מטען אלחוטי") — used to generate
# smarter LIKE patterns from the query words.
HEB_PREFIX_STRIP = ["מטענ", "אוזני", "נעל", "מצלמ", "מקלד", "שעונ", "מנור", "תיק"]

def _synonym_expansion(words: list[str]) -> list[str]:
    """Expand Hebrew/English query words into their synonym group so the
    LIKE search matches listings in either language. Real terms only."""
    if not words:
        return []
    lower = [w.lower() for w in words]
    extra: list[str] = []
    for group in SYNONYM_GROUPS:
        if any(g in " ".join(lower) or any(w in g for w in lower) for g in group):
            for term in group:
                if term.lower() not in lower and term.lower() not in extra:
                    extra.append(term)
    return words + extra

AGE_BAND_KEYWORDS = {
    "baby": ["baby", "תינוק", "תינוקת"],
    "toddler": ["toddler", "פעוט"],
    "kids": ["kids", "children", "לילדים", "משחק לילדים"],
    "teens": ["teen", "teenager", "נוער"],
}

MAX_PRICE_BUDGET_MULTIPLIER = 1.15  # allow slight over-budget (shipping etc.)


class SmartSearchAgent:
    def __init__(self):
        # LLM usage is optional: parse_intent uses the REST Gemini client
        # when GOOGLE_API_KEY is set and ai_gate allows, and the robust
        # Hebrew/English heuristics otherwise. No langchain dependency here
        # anymore (its grpc transport hung indefinitely on some networks).
        self._llm = settings.google_api_key or None

    # --- intent parsing ---

    def parse_intent(self, query: str) -> dict:
        """Return {gift, age, budget, categories, keywords}."""
        q = (query or "").strip()
        intent = {
            "gift": bool(re.search(r"מתנה|gift", q, re.IGNORECASE)),
            "age": None,
            "budget": None,
            "categories": [],
            "keywords": [],
        }

        m = BUDGET_RE.search(q)
        if m:
            try:
                intent["budget"] = float(m.group(1).replace(",", ""))
            except ValueError:
                pass

        m = AGE_RE.search(q)
        if m:
            intent["age"] = int(m.group(1))

        if self._llm:
            llm_intent = self._llm_parse(q)
            if llm_intent:
                intent.update({k: v for k, v in llm_intent.items() if v})
                return intent

        # Heuristic fallback (works with no API key).
        for cat, hints in CATEGORY_HINTS.items():
            if any(h.lower() in q.lower() for h in hints):
                intent["categories"].append(cat)

        # Pull 1-2 concrete keywords: longest words from the query that
        # aren't stopwords — these anchor the LIKE search. "עד", "שקל" and
        # friends are budget/amount words, not product words: including them
        # makes the LIKE scan match nothing ("אני מחפש מתנה לילד גיל 5 עד
        # 200 שקל" used to extract ["עד", "שקל"] and return ~nothing).
        stopwords = {
            "אני", "מחפש", "מחפשת", "מתנה", "מתנות", "לילד", "לילדה", "בן", "בת",
            "גיל", "של", "את", "אתם", "מה", "עבור", "משהו", "עד", "עד ל", "שקל",
            "שח", "ש\"ח", "₪", "nis", "shekel", "בסביבות", "בערך", "אנחנו", "אתה", "אותי",
            "תן", "לי", "רוצה", "אשמח", "לקנות", "לקנות", "מחפשת", "חפש", "למצוא",
            "שיש", "שיהיה", "בשביל", "אותו", "כזה", "בדיוק", "חייב", "הכי", "טוב",
        }
        words = [w for w in re.findall(r"[\u0590-\u05ffa-zA-Z]+", q) if w not in stopwords]
        # Prefer the longest words (most likely the actual product noun,
        # e.g. "אוזניות" over "אלחוטיות"), then pick 2.
        words.sort(key=len, reverse=True)
        intent["keywords"] = _synonym_expansion(words[:2])
        return intent

    def _llm_parse(self, query: str) -> dict | None:
        try:
            prompt = (
                "Parse this shopping query into JSON. Query: " + query + "\n"
                "Return ONLY JSON: "
                '{"gift": bool, "age": int|null, "budget": float|null, '
                '"categories": [string], "keywords": [string]}'
            )
            data = gemini_generate_json(prompt, timeout_seconds=6.0)
            if data is None:
                logger.warning("LLM intent parse timed out/failed — using heuristics")
                return None
            return {
                "gift": bool(data.get("gift")),
                "age": data.get("age"),
                "budget": float(data["budget"]) if data.get("budget") else None,
                "categories": data.get("categories") or [],
                "keywords": data.get("keywords") or [],
            }
        except Exception as e:
            logger.error("LLM intent parse failed: %s", e)
            return None

    # --- retrieval + ranking ---

    def search(self, db: Session, query: str, limit: int = 24) -> list[dict]:
        """Return ranked [{product, reason, score}] for a natural-language query."""
        intent = self.parse_intent(query)
        keywords = intent["keywords"]
        categories = intent["categories"]

        # 1) Semantic search (vector) — best for fuzzy intent.
        semantic_ids = self._semantic_ids(query, db)

        # 2) Keyword LIKE search — expanded with synonyms (Hebrew/English)
        # and Hebrew root-stripping so "אוזניות" also finds "headphones"
        # and "מטענים".
        like_matches = []
        if keywords:
            like_query = db.query(Product).filter(Product.is_active == True, Product.is_verified == True)  # noqa: E712
            from sqlalchemy import or_
            patterns = set()
            for k in keywords:
                patterns.add(f"%{k}%")
                # Strip a common Hebrew plural/derivative prefix for a
                # broader, still-real match (מטען -> מטענים/מטען אלחוטי).
                for root in HEB_PREFIX_STRIP:
                    if k.startswith(root) and len(k) > len(root):
                        patterns.add(f"%{root}%")
            like_query = like_query.filter(or_(*(Product.name.ilike(p) for p in patterns)))
            like_matches = like_query.limit(limit * 2).all()

        # 3) Category-filtered products (fallback when no keywords matched).
        cat_matches = []
        if categories and not like_matches:
            cat_query = db.query(Product).filter(Product.is_active == True, Product.is_verified == True)  # noqa: E712
            from sqlalchemy import or_
            cat_query = cat_query.filter(or_(*(Product.category == c for c in categories)))
            cat_matches = cat_query.limit(limit).all()

        # 4) Budget/popularity fallback: when the query is intent-heavy but
        # keyword-light ("מתנה לילד גיל 5 עד 200 שקל"), LIKE finds nothing
        # useful. Instead surface the most in-demand products within the
        # budget so the user always gets suitable options, not an empty page.
        popular_matches = []
        if not like_matches and not cat_matches:
            pop_query = db.query(Product).filter(Product.is_active == True, Product.is_verified == True)  # noqa: E712
            if intent.get("budget"):
                pop_query = pop_query.filter(Product.price <= intent["budget"] * MAX_PRICE_BUDGET_MULTIPLIER)
            # Gift queries skew young/fun: prefer high-demand products.
            popular_matches = pop_query.order_by(Product.buying_score.desc()).limit(limit).all()

        # Merge unique candidates.
        seen: dict[int, Product] = {}
        for p in list(like_matches) + list(cat_matches) + list(popular_matches):
            seen[p.id] = p
        for pid in semantic_ids:
            if pid not in seen:
                p = db.query(Product).filter(Product.id == pid).first()
                if p:
                    seen[p.id] = p

        # 4) Score each candidate against the intent.
        ranked = []
        for p in seen.values():
            score, reason = self._score_product(p, intent, query)
            if score > 0:
                ranked.append({"product": p, "reason": reason, "score": score})

        ranked.sort(key=lambda r: r["score"], reverse=True)
        return ranked[:limit]

    def _semantic_ids(self, query: str, db: Session) -> list:
        try:
            from app.services.semantic_search_service import SemanticSearchService
            svc = SemanticSearchService()
            return svc.search_intent(query)
        except Exception as e:
            logger.debug("Semantic search unavailable: %s", e)
            return []

    def _score_product(self, product: Product, intent: dict, query: str) -> tuple[float, str]:
        score = 0.0
        reasons = []

        # Budget fit.
        if intent.get("budget"):
            budget = intent["budget"]
            price = product.price or 0
            if price <= budget * MAX_PRICE_BUDGET_MULTIPLIER:
                score += 40
                reasons.append(f"בטווח התקציב שביקשת (~₪{int(budget)})")
            else:
                return 0.0, ""  # over budget -> not a match

        # Keyword overlap (name similarity to query keywords).
        kw = intent.get("keywords") or []
        if kw:
            name_l = (product.name or "").lower()
            sim = max(name_similarity(k, product.name) for k in kw)
            # Synonym overlap: query word appears in the product name in
            # either language (the LIKE path already expanded synonyms, but
            # the semantic path needs the same credit here).
            syn_hit = any(k.lower() in name_l for k in kw)
            if syn_hit:
                score += 35
                reasons.append("מתאים למונחי החיפוש שלך")
            elif sim >= 0.3:
                score += sim * 30
                if sim >= 0.5:
                    reasons.append("מתאים למונחי החיפוש שלך")

        # Demand signal.
        demand = (product.buying_score or 0) / 100.0
        score += demand * 15
        if demand >= 0.85:
            reasons.append("מוצר מבוקש מאוד באתר")

        # Age fit (conservative): boost for age-appropriate keywords in name.
        if intent.get("age") is not None:
            age = intent["age"]
            name_l = (product.name or "").lower()
            if age <= 3 and any(k in name_l for k in AGE_BAND_KEYWORDS["baby"] + AGE_BAND_KEYWORDS["toddler"]):
                score += 20
                reasons.append("מותאם לפעוטות")
            elif 4 <= age <= 10 and any(k in name_l for k in AGE_BAND_KEYWORDS["kids"]):
                score += 20
                reasons.append("מתאים לילדים")
            elif age >= 13 and any(k in name_l for k in AGE_BAND_KEYWORDS["teens"]):
                score += 20
                reasons.append("מתאים לבני נוער")

        # Verified + category relevance floor.
        if intent.get("categories") and product.category in intent["categories"]:
            score += 15
            reasons.append(f"קטגוריה: {product.category}")

        if not reasons:
            return 0.0, ""
        return round(score, 2), " · ".join(reasons[:3])
