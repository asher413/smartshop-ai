"""Tests for ads_service — placement filtering, click counting, position integrity."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.models import Base, AdPlacement
from app.services import ads_service


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def _ad(db, name="ad1", position="home_top", active=True):
    a = AdPlacement(name=name, position=position, image_url="https://example.com/img.png",
                    target_url="/deals", is_active=active)
    db.add(a)
    db.commit()
    return a


class TestGetActiveForPosition:
    def test_returns_only_active_for_position(self, db):
        _ad(db, "top1", "home_top")
        _ad(db, "side1", "home_side")
        _ad(db, "top2-inactive", "home_top", active=False)

        results = ads_service.get_active_for_position(db, "home_top")
        names = [r.name for r in results]
        assert "top1" in names
        assert "side1" not in names
        assert "top2-inactive" not in names

    def test_increments_impressions(self, db):
        a = _ad(db, "banner", "product_banner")
        assert a.impressions == 0
        ads_service.get_active_for_position(db, "product_banner")
        db.refresh(a)
        assert a.impressions == 1

    def test_respects_limit(self, db):
        for i in range(5):
            _ad(db, f"top{i}", "home_top")
        results = ads_service.get_active_for_position(db, "home_top", limit=2)
        assert len(results) == 2

    def test_empty_position_returns_empty(self, db):
        results = ads_service.get_active_for_position(db, "site_bottom")
        assert results == []


class TestRecordAdClick:
    def test_increments_clicks(self, db):
        a = _ad(db, "clickable", "home_top")
        assert a.clicks == 0
        ads_service.record_ad_click(db, a.id)
        db.refresh(a)
        assert a.clicks == 1

    def test_nonexistent_id_does_not_crash(self, db):
        ads_service.record_ad_click(db, 99999)  # must not raise


class TestPositionsList:
    def test_all_positions_exist(self):
        assert "home_top" in ads_service.POSITIONS
        assert "home_side" in ads_service.POSITIONS
        assert "product_banner" in ads_service.POSITIONS
        assert "site_bottom" in ads_service.POSITIONS
        assert "site_side" in ads_service.POSITIONS
