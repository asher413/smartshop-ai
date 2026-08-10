"""
ReviewInsightsAgent — aggregates the REAL user reviews a product has
collected (from ProductReview) into an honest, useful summary: average
rating, sentiment split, most-mentioned themes, and a short verdict.
Only real review text is used — no fabricated pros/cons. Products with
too few reviews get an honest "not enough data" answer instead.
"""
import logging
import re
from collections import Counter

from sqlalchemy.orm import Session

from app.core.models import ProductReview, User

logger = logging.getLogger(__name__)

POSITIVE_HINTS = ("מצוין", "מעולה", "מושלם", "ממליץ", "איכות", "מהיר", "יפה", "נהדר", "שווה", "עובד", "טוב", "מדהים")
NEGATIVE_HINTS = ("חבל", "לא טוב", "בעיה", "שבור", "התקלקל", "אכזבה", "איטי", "רועש", "קטן", "לא עבד", "רע", "חסר")


class ReviewInsightsAgent:
    def summarize(self, db: Session, product_id: int, min_reviews: int = 2) -> dict:
        rows = (
            db.query(ProductReview, User)
            .join(User, User.id == ProductReview.user_id)
            .filter(ProductReview.product_id == product_id)
            .all()
        )
        if len(rows) < min_reviews:
            return {
                "available": False,
                "message": "עדיין אין מספיק ביקורות כדי לסכם — תהיו הראשונים לדרג!",
                "count": len(rows),
            }

        ratings = [r.rating for r, _ in rows]
        avg = round(sum(ratings) / len(ratings), 1)
        comments = [(r.comment or "").strip() for r, _ in rows if (r.comment or "").strip()]

        positive, negative = 0, 0
        themes = Counter()
        for text in comments:
            if any(h in text for h in POSITIVE_HINTS):
                positive += 1
            if any(h in text for h in NEGATIVE_HINTS):
                negative += 1
            for word in re.findall(r"[\u0590-\u05ff]{4,}", text):
                if word not in ("זה", "של", "עם", "את", "שלום"):
                    themes[word] += 1

        top_themes = [w for w, _ in themes.most_common(3)] if themes else []
        sentiment = "חיובית" if positive > negative else ("מעורבת" if positive == negative and positive > 0 else "שלילית")
        verdict = (
            f"המשתמשים מדרגים {avg}/5 — תמונה {sentiment}." if positive + negative > 0
            else f"המשתמשים מדרגים {avg}/5."
        )

        return {
            "available": True,
            "count": len(rows),
            "avg_rating": avg,
            "positive": positive,
            "negative": negative,
            "top_themes": top_themes,
            "sentiment": sentiment,
            "verdict": verdict,
        }
