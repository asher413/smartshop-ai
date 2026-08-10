"""
Ad placements — clearly labeled ad slots the site owner controls from the
admin dashboard. Impressions and clicks are counted so the owner sees real
performance numbers; the UI always labels these 'פרסומת' (the disclosure
requirement in the README applies to these too).
"""
from sqlalchemy.orm import Session

from app.core.models import AdPlacement

POSITIONS = ("home_top", "home_side", "product_banner", "site_bottom", "site_side")


def get_active_for_position(db: Session, position: str, limit: int = 2) -> list[AdPlacement]:
    ads = (
        db.query(AdPlacement)
        .filter(AdPlacement.position == position, AdPlacement.is_active == True)  # noqa: E712
        .order_by(AdPlacement.created_at.desc())
        .limit(limit)
        .all()
    )
    for ad in ads:
        ad.impressions = (ad.impressions or 0) + 1
    if ads:
        db.commit()
    return ads


def record_ad_click(db: Session, ad_id: int) -> None:
    ad = db.query(AdPlacement).filter(AdPlacement.id == ad_id).first()
    if ad:
        ad.clicks = (ad.clicks or 0) + 1
        db.commit()
