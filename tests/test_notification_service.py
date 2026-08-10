"""Tests for notification_service — broadcasts, targeted, popup dedup, mark-read."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.models import Base, Notification
from app.services import notification_service


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


class TestBroadcast:
    def test_creates_without_user_id(self, db):
        n = notification_service.broadcast(db, "Hello", "World")
        assert n.user_id is None
        assert n.title == "Hello"
        assert n.message == "World"
        assert n.is_popup is False

    def test_popup_flag(self, db):
        n = notification_service.broadcast(db, "Pop", "Up", is_popup=True)
        assert n.is_popup is True


class TestNotifyUser:
    def test_creates_with_user_id(self, db):
        n = notification_service.notify_user(db, user_id=42, title="T", message="M")
        assert n.user_id == 42

    def test_link_is_stored(self, db):
        n = notification_service.notify_user(db, user_id=1, title="T", message="M", link="/deals")
        assert n.link == "/deals"


class TestLatestPopup:
    def test_returns_newest_broadcast_popup(self, db):
        import time
        notification_service.broadcast(db, "old", "popup", is_popup=True)
        time.sleep(0.02)  # ensure distinct created_at
        notification_service.broadcast(db, "new", "popup", is_popup=True)
        latest = notification_service.latest_popup(db)
        assert latest is not None
        assert latest.title == "new"

    def test_ignores_targeted(self, db):
        notification_service.notify_user(db, user_id=1, title="targeted", message="popup", is_popup=True)
        latest = notification_service.latest_popup(db)
        assert latest is None  # not a broadcast

    def test_ignores_non_popup_broadcasts(self, db):
        notification_service.broadcast(db, "not a popup", "msg", is_popup=False)
        assert notification_service.latest_popup(db) is None


class TestUnreadForUser:
    def test_shows_broadcasts_to_everyone(self, db):
        notification_service.broadcast(db, "B", "for all")
        unread = notification_service.unread_for_user(db, user_id=99)
        assert len(unread) == 1

    def test_shows_targeted_only_to_owner(self, db):
        notification_service.notify_user(db, user_id=10, title="mine", message="private")
        for_me = notification_service.unread_for_user(db, user_id=10)
        for_other = notification_service.unread_for_user(db, user_id=99)
        assert any(n.title == "mine" for n in for_me)
        assert not any(n.title == "mine" for n in for_other)

    def test_anonymous_sees_only_broadcasts(self, db):
        notification_service.broadcast(db, "public", "yes")
        notification_service.notify_user(db, user_id=5, title="private", message="no")
        unread = notification_service.unread_for_user(db, user_id=None)
        titles = [n.title for n in unread]
        assert "public" in titles
        assert "private" not in titles


class TestMarkRead:
    def test_sets_read_at(self, db):
        n = notification_service.broadcast(db, "Read", "me")
        assert n.read_at is None
        ok = notification_service.mark_read(db, n.id)
        assert ok
        db.refresh(n)
        assert n.read_at is not None

    def test_nonexistent_returns_false(self, db):
        ok = notification_service.mark_read(db, 99999)
        assert ok is False
