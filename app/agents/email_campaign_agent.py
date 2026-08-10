"""
Email campaign agent — sends deal newsletters to the subscriber list.

Uses the existing SMTP email_service (which degrades gracefully when SMTP
isn't configured). Campaigns are built from REAL catalog data: top deals by
buying score, honest prices, no invented discounts. Subject lines come
from the marketing agent's A/B generator when Gemini is available.

The newsletter is a designed, conversion-oriented HTML email: a hero
"deal of the day" block, a weekly-picks grid with real prices/coupons,
and clear CTAs — no fabricated urgency or fake counts.
"""
import datetime
import logging

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.models import Product, NewsletterSubscriber
from app.services.email_service import send_email

logger = logging.getLogger(__name__)


class EmailCampaignAgent:
    def __init__(self):
        from app.agents.marketing_agent import MarketingAgent
        self.marketing = MarketingAgent()

    def build_deal_newsletter(self, db: Session, limit: int = 6) -> tuple[str, str]:
        """Return (subject, html_body) for a deals newsletter from REAL data."""
        deals = (
            db.query(Product)
            .filter(Product.is_active == True, Product.is_verified == True)  # noqa: E712
            .order_by(Product.buying_score.desc())
            .limit(limit)
            .all()
        )
        subject = "🔥 הדילים החמים של השבוע — SmartShop"
        if settings.google_api_key:
            try:
                variants = self.marketing.generate_ab_subject_lines(
                    ", ".join(p.name for p in deals[:3])
                )
                subject = variants.get("variant_a") or subject
            except Exception:
                pass

        # Hero = the single strongest deal (highest buying score).
        hero = deals[0] if deals else None
        rest = deals[1:] if deals else []

        hero_html = ""
        if hero:
            savings = ""
            if hero.local_market_price and hero.local_market_price > (hero.price or 0):
                savings = f"חוסכים ₪{int(hero.local_market_price - hero.price)}!"
            hero_html = f"""
            <a href="{settings.site_url}/product/{hero.id}" style="display:block;text-decoration:none;color:inherit;">
              <div style="background:linear-gradient(135deg,#4338ca,#7c3aed);border-radius:16px;padding:20px;margin-bottom:24px;">
                <div style="color:#fbbf24;font-size:12px;font-weight:800;letter-spacing:1px;margin-bottom:6px;">🏆 דיל היום</div>
                <div style="color:#fff;font-size:17px;font-weight:800;line-height:1.4;">{hero.name}</div>
                <div style="margin-top:10px;display:flex;align-items:center;gap:8px;">
                  <span style="color:#fff;font-size:22px;font-weight:900;">₪{int(hero.price or 0)}</span>
                  {'<span style="color:#c4b5fd;font-size:13px;text-decoration:line-through;">₪' + str(int(hero.local_market_price)) + '</span>' if hero.local_market_price else ''}
                  <span style="color:#86efac;font-size:12px;font-weight:700;">{savings}</span>
                </div>
              </div>
            </a>"""

        items_html = ""
        for p in rest:
            price_line = f"₪{int(p.price or 0)}"
            coupon = f'<span style="background:#052e16;color:#4ade80;border:1px dashed #4ade80;border-radius:6px;padding:2px 8px;font-size:10px;font-weight:800;">✂️ {p.coupon_code}</span>' if p.coupon_code else ""
            items_html += f"""
            <a href="{settings.site_url}/product/{p.id}" style="display:block;text-decoration:none;color:inherit;margin-bottom:16px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td width="88" valign="top">
                    <img src="{p.image_url}" width="80" height="80" style="border-radius:10px;object-fit:cover;display:block;">
                  </td>
                  <td valign="top" style="padding-right:12px;">
                    <div style="font-weight:700;font-size:13px;line-height:1.4;">{p.name}</div>
                    <div style="margin-top:4px;"><span style="color:#4338ca;font-weight:900;font-size:15px;">{price_line}</span> {coupon}</div>
                    <div style="color:#64748b;font-size:11px;margin-top:3px;">★ {p.rating or '—'}/5 · {p.review_count or 0:,} ביקורות</div>
                  </td>
                </tr>
              </table>
            </a>"""

        html = f"""
        <div dir="rtl" style="font-family:'Segoe UI',Arial,sans-serif;background:#f8fafc;padding:24px 12px;">
          <div style="max-width:540px;margin:0 auto;background:#ffffff;border-radius:20px;overflow:hidden;border:1px solid #e2e8f0;">
            <div style="background:linear-gradient(135deg,#1e1b4b,#4338ca);padding:24px;text-align:center;">
              <div style="font-size:24px;">🛍️</div>
              <div style="color:#ffffff;font-size:20px;font-weight:900;">SmartShop <span style="color:#fbbf24;">AI</span></div>
              <div style="color:#c7d2fe;font-size:12px;margin-top:4px;">הדילים החמים — נבחרים אוטומטית ע"י מערכת מתקדמת</div>
            </div>
            <div style="padding:24px;">
              {hero_html}
              <div style="font-size:13px;font-weight:800;color:#1e293b;margin-bottom:12px;">🔥 מוצרי השבוע הנבחרים</div>
              {items_html}
              <a href="{settings.site_url}/" style="display:block;text-align:center;background:#4338ca;color:#fff;font-weight:800;font-size:14px;padding:14px;border-radius:12px;text-decoration:none;margin:8px 0 4px;">לכל הדילים →</a>
              <p style="color:#94a3b8;font-size:11px;margin-top:20px;line-height:1.7;">
                קישורי שותפים — ייתכן שנקבל עמלה מרכישות, ללא עלות נוספת עבורך.
                <br><a href="{settings.site_url}/privacy" style="color:#6366f1;">מדיניות פרטיות</a> ·
                <a href="{settings.site_url}/terms" style="color:#6366f1;">תנאי שימוש</a>
              </p>
            </div>
          </div>
        </div>"""
        return subject, html

    def send_newsletter(self, db: Session, limit: int = 6) -> dict:
        """Send the deal newsletter to all active subscribers. Returns a
        real report (subscribers/sent/failed) for the admin dashboard."""
        subject, html = self.build_deal_newsletter(db, limit)
        subscribers = (
            db.query(NewsletterSubscriber)
            .filter(NewsletterSubscriber.is_active == True)  # noqa: E712
            .limit(500)
            .all()
        )
        sent, failed = 0, 0
        for sub in subscribers:
            ok = send_email(sub.email, subject, html)
            if ok:
                sent += 1
            else:
                failed += 1
        return {"subscribers": len(subscribers), "sent": sent, "failed": failed}
