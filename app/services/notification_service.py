"""
Notifications + popup marketing messages + Web Push delivery.

Broadcast rows (user_id NULL) are pushed to every visitor: the home page
pops the latest unread is_popup broadcast as a marketing modal, and the
bell in the nav lists all unread. Targeted rows (user_id set) only appear
in that user's bell — used for things like "your price alert hit!".

When VAPID keys are configured, every notification (broadcast or targeted)
also attempts Web Push delivery via pywebpush to every subscribed browser.
Failing push deliveries (expired subscriptions, etc.) are cleaned silently.
"""
import datetime
import json
import logging

from sqlalchemy.orm import Session

from app.core.models import Notification, PushSubscription
from app.core.config import settings

logger = logging.getLogger(__name__)


def _send_push_notification(subscription: PushSubscription, title: str, message: str, url: str | None = None) -> bool:
    """Try to deliver one push notification via the Web Push protocol.
    Returns False on any failure so the caller can decide to delete stale
    subscriptions."""
    if not settings.vapid_private_key or not settings.vapid_public_key:
        return False
    try:
        from pywebpush import webpush, WebPushException
        payload = json.dumps({
            "title": title,
            "message": message,
            "url": url or "/",
        })
        webpush(
            subscription_info={
                "endpoint": subscription.endpoint,
                "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
            },
            data=payload,
            vapid_private_key=settings.vapid_private_key,
            vapid_claims={"sub": settings.vapid_claims_email or f"mailto:{settings.admin_email}"},
            timeout=10,
        )
        return True
    except WebPushException as e:
        # 410 Gone = subscription expired/unsubscribed — safe to delete
        if e.response and e.response.status_code == 410:
            logger.info("Push subscription expired: %s", subscription.id)
            return False  # caller should delete
        logger.debug("Web push delivery failed (non-terminal): %s", e)
        return True  # keep the subscription — might be a transient error
    except Exception as e:
        logger.debug("Web push unavailable: %s", e)
        return False


def _push_to_all(title: str, message: str, url: str | None = None, user_id: int | None = None):
    """Fire-and-forget push delivery to all (or targeted) subscriptions.
    Runs inline so the caller DB session can be closed; failures are logged
    quietly — push is a best-effort channel."""
    if not settings.vapid_private_key:
        return
    try:
        from app.core.database import SessionLocal
        db = SessionLocal()
        try:
            q = db.query(PushSubscription)
            if user_id is not None:
                q = q.filter(PushSubscription.user_id == user_id)
            stale_ids: list[int] = []
            for sub in q.all():
                ok = _send_push_notification(sub, title, message, url)
                if not ok:
                    stale_ids.append(sub.id)
            if stale_ids:
                db.query(PushSubscription).filter(
                    PushSubscription.id.in_(stale_ids)
                ).delete(synchronize_session=False)
                db.commit()
                logger.info("Cleaned %d stale push subscriptions", len(stale_ids))
        finally:
            db.close()
    except Exception as e:
        logger.debug("Push delivery sweep failed (non-critical): %s", e)


def broadcast(db: Session, title: str, message: str, link: str | None = None, is_popup: bool = False) -> Notification:
    n = Notification(title=title, message=message, link=link, is_popup=is_popup)
    db.add(n)
    db.commit()
    db.refresh(n)
    # Push to all subscribed browsers — fire-and-forget in a background
    # thread so it never blocks the HTTP response (see _push_to_all).
    import threading
    threading.Thread(target=_push_to_all, args=(title, message, link, None), daemon=True).start()
    return n


def notify_user(db: Session, user_id: int, title: str, message: str, link: str | None = None, is_popup: bool = False) -> Notification:
    n = Notification(user_id=user_id, title=title, message=message, link=link, is_popup=is_popup)
    db.add(n)
    db.commit()
    db.refresh(n)
    import threading
    threading.Thread(target=_push_to_all, args=(title, message, link, user_id), daemon=True).start()
    return n


def send_push_test(db_session=None) -> tuple[int, str]:
    """Admin test: send one push to every subscriber. Returns (count, message).
    Uses its own session when called without one, so it's safe from the API."""
    if not settings.vapid_private_key:
        return 0, "VAPID keys not configured"
    try:
        from app.core.database import SessionLocal
        db = db_session or SessionLocal()
        own_session = db_session is None
        try:
            count = db.query(PushSubscription).count()
            if count == 0:
                return 0, "No subscribers yet — nothing to send"
            _push_to_all(
                "בדיקת התראות Push",
                "ההתראה הגיעה בהצלחה מדילבורסה! עכשיו אתם מחוברים לקבלת דילים חמים ישירות לדפדפן.",
                url="/",
            )
            return count, f"Test push sent to {count} subscriber(s)"
        finally:
            if own_session:
                db.close()
    except Exception as e:
        logger.debug("send_push_test failed: %s", e)
        return 0, f"Push delivery failed: {e}"


def latest_popup(db: Session) -> Notification | None:
    """Latest unread broadcast flagged as a popup — shown once per browser."""
    return (
        db.query(Notification)
        .filter(Notification.user_id.is_(None), Notification.is_popup == True)  # noqa: E712
        .order_by(Notification.created_at.desc())
        .first()
    )


def unread_for_user(db: Session, user_id: int | None, limit: int = 20) -> list[Notification]:
    """Broadcasts + this user's targeted notifications, newest first."""
    q = db.query(Notification).filter(Notification.read_at.is_(None))
    if user_id is not None:
        q = q.filter((Notification.user_id.is_(None)) | (Notification.user_id == user_id))
    else:
        q = q.filter(Notification.user_id.is_(None))
    return q.order_by(Notification.created_at.desc()).limit(limit).all()


def mark_read(db: Session, notification_id: int) -> bool:
    n = db.query(Notification).filter(Notification.id == notification_id).first()
    if not n:
        return False
    n.read_at = datetime.datetime.utcnow()
    db.commit()
    return True
