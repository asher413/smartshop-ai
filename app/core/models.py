"""
SQLAlchemy models. Carried over from the original project with fixes:
- Removed the Base.metadata.create_all(bind=engine) side-effect that used
  to run on import. Use scripts/init_db.py (dev) or Alembic (prod).
- Added TrendingCandidate: a staging table for the auto-import pipeline so
  scraped/API products are scored/reviewed BEFORE becoming live Products —
  this is the checkpoint that stops "automatic import" from ever putting
  garbage, duplicate, or mispriced listings live with zero oversight.
- Added source_adapter / external_id / import_score on Product for dedup
  across AliExpress / Amazon / eBay / Temu.
"""
import datetime
from sqlalchemy import (
    Column, Integer, String, Float, Text, DateTime, Boolean, JSON, UniqueConstraint, ForeignKey
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class AppSetting(Base):
    """Persistent key/value overrides written from the admin settings page.

    On hosts with an ephemeral filesystem (Render), the .env file doesn't
    survive a restart — so settings saved from the admin panel (admin
    password, supplier API keys, ...) are mirrored here and re-applied on
    boot. This is what makes changing the admin password from the UI stick.
    """
    __tablename__ = "app_settings"
    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(64), unique=True, index=True, nullable=False)
    value = Column(Text, nullable=False, default="")
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("source_adapter", "external_id", name="uq_source_external"),)

    id = Column(Integer, primary_key=True, index=True)
    sku = Column(String, unique=True, index=True)

    source_adapter = Column(String, index=True)   # "aliexpress" | "amazon" | "ebay" | "temu"
    external_id = Column(String, index=True)
    import_score = Column(Float, default=0.0)

    offers = Column(JSON, default=list)
    affiliate_links = Column(JSON, default=dict)
    last_updated = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    name = Column(String, index=True)
    original_name = Column(String)
    price = Column(Float)
    cost_price = Column(Float, default=0.0)
    profit_margin = Column(Float, default=0.0)
    description = Column(Text)
    original_description = Column(Text)
    translations = Column(JSON, default=dict)
    ai_summary = Column(Text, nullable=True)
    image_url = Column(String)
    gallery_images = Column(JSON, default=list)
    supplier_name = Column(String)
    category = Column(String, index=True)
    seo_title = Column(String)
    slug = Column(String, unique=True, index=True)
    stock_count = Column(Integer, default=10)
    supplier_url = Column(String)
    affiliate_url = Column(String, nullable=True)
    competitor_price = Column(Float)
    competitor_min_price = Column(Float, nullable=True)
    local_market_price = Column(Float, nullable=True)
    local_market_name = Column(String, default="המחיר הממוצע בארץ")
    coupon_code = Column(String, nullable=True)
    shipping_reliability_stat = Column(Integer, default=90)
    shipping_days = Column(Integer, default=14)
    other_sites_prices = Column(JSON, default=dict)
    commission_rate = Column(Float, default=0.05)
    ai_analysis_tag = Column(String, nullable=True)
    rating = Column(Float, default=0.0)
    review_count = Column(Integer, default=0)
    review_summary = Column(Text, nullable=True)
    blog_content = Column(Text, nullable=True)
    is_verified = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    is_trending = Column(Boolean, default=False)
    buying_score = Column(Integer, default=85)
    pros = Column(JSON, default=list)
    cons = Column(JSON, default=list)
    feature_ratings = Column(JSON, default=dict)
    video_url = Column(String, nullable=True)
    description_b = Column(Text, nullable=True)
    sales_count_a = Column(Integer, default=0)
    sales_count_b = Column(Integer, default=0)


class TrendingCandidate(Base):
    """Staging row for the auto-import pipeline (see module docstring)."""
    __tablename__ = "trending_candidates"
    __table_args__ = (UniqueConstraint("source_adapter", "external_id", name="uq_candidate_source_external"),)

    id = Column(Integer, primary_key=True, index=True)
    source_adapter = Column(String, index=True)
    external_id = Column(String, index=True)
    raw_name = Column(String)
    raw_price = Column(Float)
    raw_currency = Column(String, default="USD")
    raw_url = Column(String)
    raw_image_url = Column(String)
    demand_score = Column(Float, default=0.0)
    quality_score = Column(Float, default=0.0)
    raw_rating = Column(Float, default=0.0)
    raw_review_count = Column(Integer, default=0)
    status = Column(String, default="pending")  # pending | approved | rejected | promoted
    discovered_at = Column(DateTime, default=datetime.datetime.utcnow)
    promoted_product_id = Column(Integer, nullable=True)
    raw_payload = Column(JSON, default=dict)


class ClickLog(Base):
    __tablename__ = "click_logs"
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, index=True)
    source = Column(String)
    session_id = Column(String)
    user_ip = Column(String)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class AffiliateClick(Base):
    __tablename__ = "affiliate_clicks"
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, index=True)
    source = Column(String, index=True)
    ref = Column(String, index=True)
    session_id = Column(String, index=True)
    user_ip = Column(String)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)


class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=True)
    product_id = Column(Integer, index=True)
    customer_email = Column(String)
    total_price = Column(Float)
    status = Column(String, default="Pending")  # Pending | Ordered | Shipped | Delivered | Cancelled
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # --- Shipment tracking (see services/order_tracking_service.py) ---
    tracking_number = Column(String, nullable=True)
    carrier_code = Column(String, nullable=True)      # e.g. "china-post", "yanwen", auto-detected where possible
    shipment_status = Column(String, default="not_registered")  # not_registered | in_transit | out_for_delivery | delivered | exception
    shipment_last_event = Column(String, nullable=True)   # human-readable latest scan, e.g. "יצא ממרכז מיון בשנזן"
    shipment_last_checked_at = Column(DateTime, nullable=True)


class Coupon(Base):
    __tablename__ = "coupons"
    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, unique=True, index=True)
    discount_percent = Column(Float)
    valid_until = Column(DateTime)


class ProductView(Base):
    __tablename__ = "product_views"
    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String, index=True)
    product_id = Column(Integer)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)


class PriceAudit(Base):
    __tablename__ = "price_audits"
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, index=True)
    old_price = Column(Float)
    new_price = Column(Float)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)


class PriceAlert(Base):
    __tablename__ = "price_alerts"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    product_id = Column(Integer, index=True)
    target_price = Column(Float)
    is_triggered = Column(Boolean, default=False)


class ProductFavorite(Base):
    """Wishlist / saved items — shown in the personal area."""
    __tablename__ = "product_favorites"
    __table_args__ = (UniqueConstraint("user_id", "product_id", name="uq_user_favorite_product"),)
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    product_id = Column(Integer, index=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class DailyPrice(Base):
    __tablename__ = "daily_prices"
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, index=True)
    price = Column(Float)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    password_hash = Column(String, nullable=True)  # nullable: Google-only accounts have no password
    is_active = Column(Boolean, default=True)
    interests = Column(JSON, default=list)
    points = Column(Integer, default=0)
    rank = Column(String, default="Bronze Hunter")

    # --- Email verification ---
    email_verified = Column(Boolean, default=False)
    verification_email_sent_at = Column(DateTime, nullable=True)

    # --- OAuth (Google Sign-In) ---
    oauth_provider = Column(String, nullable=True)   # "google" if this account was created/linked via Google
    oauth_subject_id = Column(String, nullable=True, index=True)  # Google's stable "sub" claim


class NewsletterSubscriber(Base):
    """Real footer signup capture — no fake subscriber counts anywhere;
    the count shown in admin comes straight from a COUNT(*) on this table."""
    __tablename__ = "newsletter_subscribers"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    is_active = Column(Boolean, default=True)


class PointTransaction(Base):
    """Every coin-earning action, so the points balance is auditable and
    never silently fabricated. A user's balance = SUM(amount) of their
    transactions. Earned for signup, email verification, first favorite,
    outbound clicks, creating price alerts, and triggered price alerts."""
    __tablename__ = "point_transactions"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    amount = Column(Integer, default=0)          # positive = earn, negative = spend
    reason = Column(String)                      # e.g. "signup", "first_favorite", "click"
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class Notification(Base):
    """In-site notifications + popup messages. Broadcast rows (user_id
    NULL) go to everyone; user_id set means a targeted notification.
    The home page pops the latest unread broadcast as a marketing popup,
    and the bell in the nav lists the rest."""
    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=True)
    title = Column(String)
    message = Column(Text)
    link = Column(String, nullable=True)
    is_popup = Column(Boolean, default=False)     # show as a modal popup on next visit
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    read_at = Column(DateTime, nullable=True)


class AdPlacement(Base):
    """Ad slots the site owner can fill from the admin dashboard.
    position: 'home_top' | 'home_side' | 'product_banner'.
    Honest by construction: ads are clearly labeled 'פרסומת' in the UI,
    click-through goes to target_url, and impressions are counted so the
    owner sees real numbers."""
    __tablename__ = "ad_placements"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    position = Column(String, index=True)
    image_url = Column(String)
    target_url = Column(String)
    is_active = Column(Boolean, default=True)
    impressions = Column(Integer, default=0)
    clicks = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class InterestPull(Base):
    """Tracks products pulled into the catalog *because a specific visitor
    showed interest in a related product* (see interest_pull_service).

    Each row = one interest-driven pull. The cleanup job uses these rows to
    reverse pulls nobody engaged with: a pulled product with zero clicks /
    views / favorites within PULL_STALE_DAYS is deactivated automatically,
    so a single visitor's interest can't permanently bloat the catalog
    with items nobody else wants.
    """
    __tablename__ = "interest_pulls"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, index=True)          # the pulled product
    origin_product_id = Column(Integer, index=True)   # the product that sparked the pull
    session_id = Column(String, index=True)
    pulled_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)


class ProductReview(Base):
    """Real user ratings (1-5 stars + comment) on product pages. Aggregate
    displayed alongside the supplier rating. No fake review counts."""
    __tablename__ = "product_reviews"
    __table_args__ = (UniqueConstraint("user_id", "product_id", name="uq_user_review_product"),)
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    product_id = Column(Integer, index=True)
    rating = Column(Integer)          # 1-5
    comment = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class PushSubscription(Base):
    """Browser push subscriptions (Web Push API / VAPID). Each row is one
    browser that opted in to receive push notifications from our Service
    Worker. user_id is NULL when the subscription was created before login;
    once the user authenticates we can link it (see main.js). Uniqueness is
    (endpoint, user_id) so the same browser can be re-registered after logout."""
    __tablename__ = "push_subscriptions"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=True)
    endpoint = Column(String)
    p256dh = Column(String)      # client public key (base64url)
    auth = Column(String)         # client authentication secret (base64url)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class SupportMessage(Base):
    """Messages submitted through the help-center contact form. Stored in
    the DB so nothing is lost even when SMTP isn't configured yet, and the
    admin can read/reply from the panel. Delivered by email to the team
    list (TEAM_EMAILS + admin email) when SMTP is live."""
    __tablename__ = "support_messages"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, default="")
    email = Column(String, index=True)
    subject = Column(String, default="")
    message = Column(Text)
    status = Column(String, default="new")        # new | read | replied | closed
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)
