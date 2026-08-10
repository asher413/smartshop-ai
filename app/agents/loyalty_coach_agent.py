"""
LoyaltyCoachAgent — a personal "next best action" coach for the coins
economy. Given a user's real state (points, what they already earned),
it recommends the highest-value actions they can still do to earn more
coins and climb ranks. All recommendations come from the actual award
rules in loyalty_service — nothing fabricated.
"""
import logging

from sqlalchemy.orm import Session

from app.core.models import User, ProductFavorite, PriceAlert
from app.services import loyalty_service

logger = logging.getLogger(__name__)

# (reason, coins, label, done_check)
_ACTIONS = [
    ("signup", 50, "הצטרפות לאתר", "has_account"),
    ("email_verified", 30, "אימות כתובת האימייל", "email_verified"),
    ("first_favorite", 20, "שמירת המוצר הראשון למועדפים", "has_favorite"),
    ("click", 1, "לחיצה על דיל (עד 10 ביום)", "click"),
    ("price_alert_created", 5, "יצירת התראת מחיר", "has_alert"),
    ("price_alert_hit", 25, "התראת מחיר שהתממשה", "alert_hit"),
]


class LoyaltyCoachAgent:
    def next_actions(self, db: Session, user: User, limit: int = 4) -> list[dict]:
        if not user:
            return []

        has_favorite = db.query(ProductFavorite).filter_by(user_id=user.id).first() is not None
        has_alert = db.query(PriceAlert).filter_by(user_id=user.id).first() is not None

        def _done(action: dict) -> bool:
            reason, _, _, check = action
            if reason == "email_verified":
                return bool(user.email_verified)
            if check == "has_favorite":
                return has_favorite
            if check == "has_alert":
                return has_alert
            if check == "click":
                return loyalty_service.clicks_today(db, user.id) >= 10
            if check == "alert_hit":
                return loyalty_service.user_earned_reason_before(db, user.id, "price_alert_hit")
            return loyalty_service.user_earned_reason_before(db, user.id, reason)

        suggestions = []
        for reason, coins, label, _ in _ACTIONS:
            if _done((reason, coins, label, _)):
                continue
            suggestions.append({"reason": reason, "coins": coins, "label": label, "link": _link_for(reason)})
            if len(suggestions) >= limit:
                break
        return suggestions

    def rank_progress(self, user: User) -> dict:
        return loyalty_service.next_rank_progress(user.points or 0)


def _link_for(reason: str) -> str:
    return {
        "email_verified": "/personal-area",
        "first_favorite": "/",
        "price_alert_created": "/",
        "click": "/",
    }.get(reason, "/")
