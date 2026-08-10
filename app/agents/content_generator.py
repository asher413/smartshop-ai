import json
import logging
import re
import time

from app.core.config import settings
from app.agents import ai_gate
from app.agents.gemini_client import gemini_generate_text, gemini_generate_json

logger = logging.getLogger(__name__)

# Currency-looking amount in plain text, e.g. "$12.99", "€ 45,50", "12.99 USD".
_PRICE_RE = re.compile(
    r"(?:\$|€|£|₪|USD|US\$)?\s*(?P<amt>\d{1,3}(?:[.,]\d{3})*(?:\.\d{1,2})?)\s*(?:USD|US\$)?",
    re.IGNORECASE,
)


class ContentGenerator:
    def __init__(self):
        if not settings.google_api_key:
            logger.warning("GOOGLE_API_KEY is not set - ContentGenerator will use fallback content only.")

    def _invoke_json_with_retry(self, prompt, fallback, max_attempts=3):
        # No-AI mode: return the deterministic fallback immediately instead
        # of burning time on network retries that will fail anyway (circuit
        # open / no key). Everything downstream already handles fallback
        # content, so the enrichment pipeline keeps running without Gemini.
        if not ai_gate.ai_available():
            return fallback
        for attempt in range(max_attempts):
            data = gemini_generate_json(prompt, timeout_seconds=8.0, temperature=0.7)
            if data:
                return data
            if attempt < max_attempts - 1:
                time.sleep(0.7 * (attempt + 1))
        return fallback

    def generate_product_listing(self, product_name, target_lang="Hebrew"):
        prompt = f"""
            You are a world-class e-commerce growth hacker.
            Create a catchy title and a persuasive, SEO-optimized description for: {product_name}.
            FOCUS ON BENEFITS OVER FEATURES. Use psychological triggers like urgency and social proof,
            but do not state or imply false claims (no fake guarantees, no invented certifications).
            Language: {target_lang}.
            Include the keys: 'brand_vibe', 'title', 'seo_title', 'description', 'local_market_price_estimate',
            'pros' (list), 'cons' (list),
            'feature_ratings' (dict of 5 attributes 1-100: Value, Build, Innovation, Delivery, UX),
            'buying_score' (1-10), 'verdict', 'meta_description',
            'whatsapp_share_text' (Hebrew), 'image_alt_text'.
            Return ONLY valid JSON.
            """
        fallback = {
            "brand_vibe": "Elite Performance. Timeless Design.",
            "title": product_name,
            "seo_title": f"{product_name} - Recommended Deal",
            "description": f"אפשרות מעשית ואמינה לקונים שמחפשים ערך טוב: {product_name}.",
            "local_market_price_estimate": 0,
            "pros": ["ערך טוב ביחס למחיר", "פופולרי בקרב קונים אונליין"],
            "cons": ["מידע מוגבל מהספק כרגע"],
            "feature_ratings": {"Value": 75, "Build": 70, "Innovation": 65, "Delivery": 60, "UX": 80},
            "buying_score": 7,
            "verdict": "בחירה סבירה לפי הנתונים הזמינים כרגע.",
            "meta_description": f"השוואת מחירים ובדיקת {product_name} עם תובנות AI.",
            "whatsapp_share_text": f"מצאתי דיל חזק על {product_name}, שווה בדיקה!",
            "image_alt_text": f"תמונת מוצר של {product_name}",
        }
        return self._invoke_json_with_retry(prompt, fallback)

    def parse_supplier_data(self, content: str):
        """
        ניתוח דף/טקסט ספק והוצאת מחיר, מלאי, דירוג, כמות ביקורות וקופונים
        (Self-healing scraper).

        כשמפתח GOOGLE_API_KEY מוגדר — מנתחים עם LLM זול; אחרת נופלים ל-parser
        רגקס-אופליין שלא דורש רשת או מפתח (מחיר/מלאי/דירוג מתוך meta tags,
        JSON-LD וטקסט העמוד). כך הפיפרליין עובד גם במצב "ללא מפתחות" במקום
        להחזיר 0 מוצרים.
        """
        fallback = self._parse_supplier_html_offline(content)
        if not ai_gate.ai_available():
            return fallback

        prompt = f"""
            Analyze the following text content from a supplier page.
            Extract:
            1. Price (float).
            2. Stock status (boolean).
            3. Star rating out of 5 (float).
            4. Review count (int).
            5. Any active coupon codes or discount text (list of strings).
            Return JSON: {{"price": float, "in_stock": boolean, "rating": float, "review_count": int, "coupons": list}}.
            Content: {content[:5000]}
            """
        llm_result = self._invoke_json_with_retry(prompt, fallback)
        # Never trust a zero price from the LLM when the offline parser found
        # a real one — keep the better of the two.
        if not llm_result.get("price") and fallback.get("price"):
            return fallback
        return llm_result

    @staticmethod
    def _parse_supplier_html_offline(content: str) -> dict:
        """Extract price/stock/rating/reviews/coupons from raw HTML without an
        LLM. Works across Amazon/eBay/AliExpress/Temu product pages because
        they all expose og:/JSON-LD metadata."""
        text = content or ""
        parsed = {"price": 0.0, "in_stock": True, "rating": 0.0, "review_count": 0, "coupons": []}

        # ---- Best source of truth: schema.org JSON-LD (Amazon, eBay,
        # B&H, Newegg, etc. all embed a Product/Offer object). ----
        ld_info = ContentGenerator._extract_jsonld(text)
        if ld_info.get("price"):
            parsed["price"] = ld_info["price"]

        # ---- Meta-tag + attribute price extraction (og:price:amount,
        # schema.org Offer inside HTML attributes). ----
        if not parsed["price"]:
            price_candidates = []
            for m in re.finditer(
                r'"?price(?:Amount|_amount)?"?\s*[:=]\s*"?(\d+(?:[.,]\d+)?)"?', text
            ):
                price_candidates.append(m.group(1))
            for m in re.finditer(
                r'property=["\']og:price:amount["\'][^>]*content=["\']([\d.,]+)["\']', text
            ):
                price_candidates.append(m.group(1))
            if price_candidates:
                try:
                    raw = price_candidates[0].replace(",", "")
                    parsed["price"] = float(raw)
                except ValueError:
                    pass

        if not parsed["price"]:
            # Fall back to the first currency-looking amount in plain text.
            m = _PRICE_RE.search(text[:20000])
            if m and m.group("amt"):
                try:
                    parsed["price"] = float(m.group("amt").replace(",", "").replace(" ", ""))
                except ValueError:
                    pass

        # Stock: pages that openly say out of stock are the only clear signal.
        out_like = re.search(r"\b(out\s*of\s*stock|sold\s*out|unavailable|not\s*available)\b", text[:30000], re.IGNORECASE)
        parsed["in_stock"] = not bool(out_like)

        # Rating (Amazon/AliExpress embed e.g. 4.5 out of 5 stars).
        rating_m = re.search(r"([\d.]+)\s*(?:out\s*of\s*5|/\s*5|stars?)", text[:50000], re.IGNORECASE)
        if rating_m:
            try:
                parsed["rating"] = min(5.0, float(rating_m.group(1)))
            except ValueError:
                pass

        reviews_m = re.search(r"([\d,.]+)\s*(?:ratings?|reviews?|global\s*ratings?)", text[:50000], re.IGNORECASE)
        if reviews_m:
            try:
                parsed["review_count"] = int(reviews_m.group(1).replace(",", ""))
            except ValueError:
                pass

        coupons = re.findall(r"(?:coupon|coupons|code|promo)[^\n]{0,40}?([A-Z0-9]{5,12})", text[:50000], re.IGNORECASE)
        parsed["coupons"] = list(dict.fromkeys(coupons))[:5]
        return parsed

    @staticmethod
    def _extract_jsonld(text: str) -> dict:
        """Pull the Product/Offer fields out of the page's schema.org
        JSON-LD blocks (the most reliable structured data on marketplace
        product pages)."""
        out = {"name": "", "image": "", "price": 0.0, "rating": 0.0, "review_count": 0}
        for m in re.finditer(r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", text, re.DOTALL | re.IGNORECASE):
            blob = m.group(1).strip()
            if not blob:
                continue
            try:
                data = json.loads(blob)
            except Exception:
                # Some sites split the JSON across multiple statements.
                try:
                    data = json.loads(blob.split("</script>")[0])
                except Exception:
                    continue
            for node in ContentGenerator._walk_jsonld(data):
                t = str(node.get("@type", "")).lower()
                if "product" not in t:
                    continue
                if not out["name"] and node.get("name"):
                    out["name"] = str(node["name"]).strip()
                if not out["image"] and node.get("image"):
                    img = node["image"]
                    out["image"] = img if isinstance(img, str) else (img[0] if isinstance(img, list) and img else str(img))
                offers = node.get("offers")
                if offers and not out["price"]:
                    offs = offers if isinstance(offers, list) else [offers]
                    for off in offs:
                        p = off.get("price") if isinstance(off, dict) else None
                        if p:
                            try:
                                out["price"] = float(str(p).replace(",", "").replace("$", ""))
                            except ValueError:
                                pass
                agg = node.get("aggregateRating")
                if agg and isinstance(agg, dict):
                    if not out["rating"] and agg.get("ratingValue"):
                        try:
                            out["rating"] = float(agg["ratingValue"])
                        except ValueError:
                            pass
                    if not out["review_count"] and agg.get("reviewCount"):
                        try:
                            out["review_count"] = int(agg["reviewCount"])
                        except ValueError:
                            pass
            if out["price"]:
                break
        return out

    @staticmethod
    def _walk_jsonld(data) -> list:
        """Flatten a JSON-LD tree (which may be a dict, list, or @graph)
        into a list of node dicts."""
        nodes = []
        stack = [data]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                nodes.append(item)
                for v in item.values():
                    if isinstance(v, (dict, list)):
                        stack.append(v)
            elif isinstance(item, list):
                stack.extend(item)
        return nodes

    def predict_price_trend(self, product_name, current_price, history):
        prompt = f"""
            Analyze price history for '{product_name}'.
            Current Price: {current_price}. History: {history}.
            Predict if price will drop or rise in the next 14 days based on trends/seasonality.
            Be calibrated - if there isn't enough data, say so rather than inventing a confident number.
            Return JSON in Hebrew: {{"prediction": string, "recommendation": string, "change_pct": float}}.
            """
        fallback = {
            "prediction": "אין מספיק מידע אמין לחיזוי מדויק כרגע",
            "recommendation": "כדאי לעקוב ולבדוק שוב בימים הקרובים",
            "change_pct": 0.0,
        }
        return self._invoke_json_with_retry(prompt, fallback)

    def generate_marketing_banner(self, product_name):
        """Placeholder image (royalty-free) instead of paid image generation."""
        return f"https://source.unsplash.com/featured/?{product_name.replace(' ', '%20')},product"

    def generate_best_10_list(self, category, year="2026"):
        if not ai_gate.ai_available():
            return f"10 המוצרים המובילים ב-{category} ל-{year} — (מצב ללא AI: המלצות כלליות בלבד)"
        prompt = f"""
            Write a 'Top 10 Best {category} for {year}' guide in Hebrew, based on general market knowledge.
            For each spot: name, one-sentence summary, why it earned the spot.
            Keep it professional; do not invent specific review statistics you don't have.
            """
        text = gemini_generate_text(prompt, timeout_seconds=10.0, temperature=0.8)
        return text or "לא ניתן היה ליצור את המדריך כרגע. נסו שוב מאוחר יותר."

    def generate_long_tail_niche_ideas(self, seed_keyword):
        if not ai_gate.ai_available():
            return [f"אביזרים ל-{seed_keyword}", f"גאדג'טים ל-{seed_keyword}", f"מתנות ל-{seed_keyword}"]
        prompt = (
            "Generate 5 hyper-niche product category ideas in Hebrew related to: "
            f"{seed_keyword}. Format: list of strings."
        )
        text = gemini_generate_text(prompt, timeout_seconds=10.0, temperature=0.8)
        if not text:
            return [f"אביזרים ל-{seed_keyword}"]
        return [line.lstrip("- ").strip() for line in text.splitlines() if line.strip()][:5]
