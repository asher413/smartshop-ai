"""
Coins / loyalty system.

Every point-awarding action writes a PointTransaction row so the balance is
always auditable (balance = SUM(amount)) — nothing is ever fabricated in
the UI. Awards:

  signup              +50
  email_verified      +30
  first_favorite      +20   (once per user, not per product)
  click               +1    (capped per user per day to stop farming)
  price_alert_created +5
  price_alert_hit     +25   (price dropped to your target — the big win)
"""
import datetime

from sqlalchemy.orm import Session

from app.core.models import PointTransaction, User

RANKS = [
    (0, "Bronze Hunter 🥉"),
    (100, "Silver Hunter 🥈"),
    (300, "Gold Hunter 🥇"),
    (700, "Platinum Hunter 💎"),
    (1500, "Deal Legend 👑"),
]


def add_points(db: Session, user: User, amount: int, reason: str) -> int:
    """Record a point transaction and return the new balance."""
    db.add(PointTransaction(user_id=user.id, amount=amount, reason=reason))
    user.points = (user.points or 0) + amount
    user.rank = rank_for(user.points)
    db.commit()
    return user.points


def rank_for(points: int) -> str:
    """Highest rank whose threshold the balance passes."""
    rank = RANKS[0][1]
    for threshold, title in RANKS:
        if points >= threshold:
            rank = title
    return rank


def user_earned_reason_before(db: Session, user_id: int, reason: str) -> bool:
    return (
        db.query(PointTransaction)
        .filter(PointTransaction.user_id == user_id, PointTransaction.reason == reason)
        .first()
        is not None
    )


def clicks_today(db: Session, user_id: int, day: datetime.date | None = None) -> int:
    """How many click-points this user earned today (farming cap)."""
    day = day or datetime.date.today()
    start = datetime.datetime.combine(day, datetime.time.min)
    end = start + datetime.timedelta(days=1)
    return (
        db.query(PointTransaction)
        .filter(
            PointTransaction.user_id == user_id,
            PointTransaction.reason == "click",
            PointTransaction.created_at >= start,
            PointTransaction.created_at < end,
        )
        .count()
    )


def next_rank_progress(points: int) -> dict:
    """Progress toward the next rank for the personal area progress bar."""
    for i, (threshold, title) in enumerate(RANKS):
        if points < threshold:
            prev_threshold = RANKS[i - 1][0] if i > 0 else 0
            span = threshold - prev_threshold
            progress = min(100, int((points - prev_threshold) / span * 100)) if span else 100
            return {
                "current": RANKS[i - 1][1] if i > 0 else RANKS[0][1],
                "next": title,
                "points_to_next": threshold - points,
                "progress": progress,
            }
    return {"current": RANKS[-1][1], "next": None, "points_to_next": 0, "progress": 100}
