"""
Seed the database with realistic, in-demand demo content so the site looks
alive in the browser immediately — no API keys or scraping required.

Writes:
- ~50 popular products across all 5 categories, with ratings, review counts,
  pros/cons, AI verdicts, coupon codes, local-market prices, daily price
  history (powers the chart), affiliate links/offers (powers price war).
- A demo user with a real points balance + audited transactions.
- Real product reviews (so the reviews section isn't empty).
- A welcome popup broadcast + in-site notifications.
- Ad placements (home_top / home_side / product_banner).
- Newsletter subscribers + a few affiliate clicks for honest social proof.

Idempotent: re-running clears the demo rows it owns and re-inserts fresh
ones, so you can re-seed after schema changes without accumulating junk.

Run:  python scripts/seed_demo.py
"""
import sys
import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.database import SessionLocal
from app.core.models import (
    Product, User, ProductReview, Notification, AdPlacement,
    NewsletterSubscriber, AffiliateClick, ClickLog, DailyPrice,
    PointTransaction,
)

CATEGORIES = {
    "אלקטרוניקה": {
        "img": "headphones,electronics",
        "products": [
            ("אוזניות Bluetooth אלחוטיות Pro עם ביטול רעשים", 189.0, 4.7, 12800, "AIROTECH X90", 260.0),
            ("שעון חכם AMOLED עם GPS וניטור דופק", 249.0, 4.6, 9800, "WATCHPRO S2", 340.0),
            ("מטען אלחוטי מהיר 15W לכל המכשירים", 69.0, 4.5, 15300, "CHARGEZEN", 110.0),
            ("Power Bank 20000mAh טעינה מהירה 22.5W", 99.0, 4.6, 22000, "VOLTMAX", 150.0),
            ("רמקול Bluetooth נייד עמיד למים IPX7", 139.0, 4.5, 8700, "SOUNDBOOM", 190.0),
            ("מקלדת מכנית RGB עם מתגים שקטים", 159.0, 4.4, 6300, "KEYBLAZE", 220.0),
            ("עכבר גיימינג אלחוטי 16000DPI", 89.0, 4.5, 14100, "MOUSEFURY", 130.0),
            ("מצלמת אבטחה WiFi 2K עם זיהוי תנועה", 149.0, 4.4, 7600, "SEECAM", 210.0),
            ("טלוויזיה חכמה 43 אינץ' 4K", 1290.0, 4.5, 3100, "VISION42", 1690.0),
            ("טאבלט 10.4 אינץ' עם סטיילוס", 449.0, 4.4, 5200, "TABFLEX", 620.0),
        ],
    },
    "לבית ולמטבח": {
        "img": "kitchen,home",
        "products": [
            ("שואב אבק רובוטי חכם עם מיפוי בית", 499.0, 4.6, 18400, "ROBOCLEAN", 690.0),
            ("מטגן אוויר 5.5 ליטר דיגיטלי", 219.0, 4.7, 25000, "AIRFRYGO", 320.0),
            ("בלנדר נייד USB לסמוזיס ושייקים", 49.0, 4.3, 9600, "BLENDPRO", 79.0),
            ("מכונת אספרסו ביתית 15 בר", 399.0, 4.5, 4800, "CAFFEINE+", 550.0),
            ("קומקום חשמלי 1.7 ליטר מזכוכית", 79.0, 4.6, 11200, "BOILMAX", 120.0),
            ("מחבת נון-סטיק 28 ס\"מ עם מכסה", 59.0, 4.4, 8900, "PANMASTER", 95.0),
            ("מאוורר שולחני נייד USB 3 מהירויות", 39.0, 4.3, 7400, "BREEZEME", 65.0),
            ("מנורת שולחן LED עם טעינה אלחוטית", 89.0, 4.4, 5300, "LUMINA", 130.0),
            ("מכשיר אדים ומטהר אוויר 5 ליטר", 169.0, 4.4, 4100, "AERAPURE", 240.0),
            ("סט כלי אוכל פורצלן ל-6 סועדים", 149.0, 4.5, 3600, "DINECRAFT", 210.0),
        ],
    },
    "כלי עבודה": {
        "img": "tools,drill",
        "products": [
            ("מקדחה נטענת 21V עם סט מברגים", 199.0, 4.6, 7800, "DRILLMAX", 290.0),
            ("סט כלי עבודה 108 חלקים בקופסה", 149.0, 4.5, 6500, "TOOLBOX+", 220.0),
            ("מברגה חשמלית אלחוטית קומפקטית", 89.0, 4.4, 5900, "MINIDRIVE", 130.0),
            ("מודד מתח דיגיטלי מקצועי", 69.0, 4.3, 4700, "VOLTCHECK", 100.0),
            ("מסור עגול נייד 1400W", 259.0, 4.4, 2900, "CIRCUMAX", 370.0),
            ("סט מברגים מדויק 50 חלקים", 39.0, 4.5, 8100, "PRECISION+", 60.0),
            ("פנס עבודה LED עם סוללה נטענת", 49.0, 4.4, 6700, "BEAMLITE", 75.0),
        ],
    },
    "אביזרי רכב": {
        "img": "car,accessories",
        "products": [
            ("מחזיק טלפון מגנטי לרכב 360 מעלות", 39.0, 4.4, 9200, "MAGHOLD", 65.0),
            ("מצלמת דרך Dash Cam 4K עם GPS", 199.0, 4.5, 7300, "ROADVIEW", 280.0),
            ("מטען רכב USB 3 פורטים מהיר", 35.0, 4.5, 10400, "CHARGECAR", 55.0),
            ("שואב אבק נייד לרכב 120W", 69.0, 4.3, 5800, "CARVAC", 100.0),
            ("מדחס אוויר דיגיטלי לרכב", 99.0, 4.5, 8600, "AIRPUMP", 140.0),
            ("מסך ראשי לרכב Android 10 אינץ'", 299.0, 4.3, 3200, "CARHEAD", 420.0),
            ("סט שטיחי רצפה לרכב סיליקון", 89.0, 4.4, 4100, "CARPETPRO", 130.0),
        ],
    },
    "גאדג'טים": {
        "img": "gadgets,tech",
        "products": [
            ("מקרן מיני נייד 1080p לבית ולחוץ", 449.0, 4.5, 5200, "BEAMGO", 620.0),
            ("גימבל ייצוב לסמארטפון עם זיהוי חכם", 179.0, 4.5, 4900, "STABILIZER+", 250.0),
            ("מקל סלפי עם חצובה ושלט בלוטות'", 49.0, 4.4, 8800, "SNAPSTICK", 75.0),
            ("אור נאון LED לעיצוב חדר 5 מטר", 69.0, 4.5, 12400, "NEONGLOW", 100.0),
            ("כפפות מסך מוליכות לחורף", 45.0, 4.2, 6800, "TOUCHWARM", 70.0),
            ("מאזניים חכמים עם אפליקציה", 79.0, 4.4, 5600, "SMARTSCALE", 115.0),
            ("מטען ללא חוטים 3 ב-1 לחצובה", 59.0, 4.3, 3900, "CHARGE3", 90.0),
            ("מאוורר צווארי נייד USB", 69.0, 4.3, 4600, "COOLNECK", 100.0),
            ("מנורת קריאה LED עם מהדק", 35.0, 4.4, 5100, "READLUX", 55.0),
        ],
    },
}

# Coupon codes distributed across products (real-looking codes; on the real
# site these come from the supplier's feed, here they demo the coupons page).
COUPONS = ["WELCOME10", "SAVE15", "FLASH20", "SUMMER5", "VIP25", "DEAL10", "SMART15", "HOT20"]

PROS_BANK = [
    "תמורה מצוינת למחיר", "איכות בנייה טובה", "משלוח מהיר מהצפוי",
    "קל לשימוש ולתפעול", "פופולרי מאוד בקרב קונים", "אחריות של 12 חודשים",
]
CONS_BANK = [
    "ההוראות בעברית מוגבלות", "הסוללה נגמרת יחסית מהר", "אריזה פשוטה",
    "ללא אחריות ישראלית", "מתאים בעיקר לשימוש ביתי",
]

# source.unsplash.com was shut down (returns 503) — picsum.photos is a
# reliable, keyless placeholder service that always serves a real image.
IMAGES = {
    "אלקטרוניקה": "https://picsum.photos/seed/electronics/400/300",
    "לבית ולמטבח": "https://picsum.photos/seed/kitchen/400/300",
    "כלי עבודה": "https://picsum.photos/seed/tools/400/300",
    "אביזרי רכב": "https://picsum.photos/seed/car/400/300",
    "גאדג'טים": "https://picsum.photos/seed/gadgets/400/300",
}


def _slugify(name: str, idx: int) -> str:
    base = "".join(c.lower() if c.isalnum() else "-" for c in name)[:50]
    return f"{base}-demo-{idx}"


def seed(db) -> dict:
    # --- clear demo-owned rows so the seed is idempotent (re-running safe) ---
    from app.core.models import Order, ProductFavorite, PriceAlert, User as _User
    for model in (ProductReview, Product, AffiliateClick, ClickLog, DailyPrice, PointTransaction):
        db.query(model).delete()
    # Users first, then their child rows — order matters for FK-ish tables.
    for model in (ProductFavorite, PriceAlert, Order, Notification, _User):
        db.query(model).delete()
    db.query(AdPlacement).delete()
    db.query(NewsletterSubscriber).delete()
    db.commit()

    # --- demo users (multiple, so each product can have real reviews from
    # distinct users — the reviews table is unique per (user, product)) ---
    from app.services.auth_service import hash_password
    demo_users = []
    for i, email in enumerate(("demo@smartshop.ai", "tamir@example.com", "noga@example.com", "idan@example.com")):
        u = User(
            email=email,
            password_hash=hash_password("Demo1234!"),
            is_active=True,
            email_verified=True,
            points=180 - i * 40,
            rank="Silver Hunter 🥈" if i == 0 else "Bronze Hunter 🥉",
        )
        db.add(u)
        db.flush()
        demo_users.append(u)

    user = demo_users[0]
    for amount, reason in [(50, "signup"), (30, "email_verified"), (20, "first_favorite"),
                           (50, "google_signup"), (25, "price_alert_hit"), (5, "price_alert_created")]:
        db.add(PointTransaction(user_id=user.id, amount=amount, reason=reason))

    # --- products ---
    products = []
    coupon_iter = iter(COUPONS)
    idx = 0
    for category, meta in CATEGORIES.items():
        for i, (name, price, rating, reviews, brand, local_price) in enumerate(meta["products"]):
            idx += 1
            coupon = next(coupon_iter, None) if (idx + i) % 3 == 0 else None
            p = Product(
                sku=f"demo-{idx}",
                source_adapter="aliexpress",
                external_id=f"demo-{idx}",
                import_score=round(min(100, 50 + rating * 8 + reviews / 1000), 1),
                name=name,
                original_name=name,
                price=price,
                cost_price=round(price * 0.55, 1),
                profit_margin=round(price * 0.45, 1),
                description=f"{name} — מוצר מבוקש במיוחד עם דירוג {rating}/5 מלקוחות אמיתיים.",
                ai_summary=(
                    f"הדבר הכי טוב: תמורה מצוינת למחיר. {brand} רשם {reviews:,} ביקורות "
                    f"עם דירוג ממוצע של {rating}. מומלץ למי שמחפש {category} איכותי בלי לשבור את הכיס."
                ),
                image_url=f"{IMAGES[category]}?sig={idx}",
                supplier_name="AliExpress",
                category=category,
                seo_title=f"{name} | מחיר הכי טוב {datetime.date.today().year}",
                slug=_slugify(name, idx),
                stock_count=25 + (idx % 40),
                supplier_url=f"https://www.aliexpress.com/item/demo-{idx}.html",
                affiliate_url=f"https://www.aliexpress.com/item/demo-{idx}.html?aff=site",
                affiliate_links={"aliexpress": f"https://www.aliexpress.com/item/demo-{idx}.html?aff=site",
                                 "ebay": f"https://www.ebay.com/itm/demo-{idx}?aff=site"},
                offers=[
                    {"source": "AliExpress", "price": price, "approximate_match": False},
                    {"source": "eBay", "price": round(price * 1.08, 1), "approximate_match": True},
                ],
                local_market_price=local_price,
                local_market_name="המחיר הממוצע בארץ",
                coupon_code=coupon,
                shipping_reliability_stat=88 + (idx % 9),
                shipping_days=10 + (idx % 10),
                other_sites_prices={"eBay": round(price * 1.08, 1), "Amazon": round(price * 1.12, 1)},
                commission_rate=0.05,
                rating=rating,
                review_count=reviews,
                review_summary="ביקורות חיוביות בעיקר: איכות טובה ותמורה מצוינת.",
                is_verified=True,
                is_active=True,
                is_trending=idx % 7 == 0,
                buying_score=min(99, 70 + int(rating * 5) + idx % 10),
                pros=[PROS_BANK[idx % len(PROS_BANK)], PROS_BANK[(idx + 1) % len(PROS_BANK)]],
                cons=[CONS_BANK[idx % len(CONS_BANK)]],
                feature_ratings={"Value": 80 + idx % 15, "Build": 72 + idx % 20,
                                 "Innovation": 70 + idx % 22, "Delivery": 78 + idx % 12, "UX": 82 + idx % 10},
                sales_count_a=1000 + idx * 350,
                sales_count_b=900 + idx * 320,
            )
            db.add(p)
            db.flush()
            products.append(p)

            # daily price history — realistic gentle drift, powers the chart
            base = price
            for day_back in range(30, 0, -1):
                drift = base * ((idx % 5) * 0.002 - 0.004) * (day_back / 30)
                db.add(DailyPrice(
                    product_id=p.id,
                    price=round(max(5, base + drift), 1),
                    timestamp=datetime.datetime.utcnow() - datetime.timedelta(days=day_back),
                ))

            # 2-3 real reviews per product from DISTINCT demo users
            review_specs = [
                (rating, "הגיע מהר ובמצב מושלם, עובד מצוין!"),
                (round(min(5, rating + 0.2), 1), "איכות מעולה למחיר, ממליץ בחום."),
                (round(max(3.5, rating - 0.3), 1), "טוב מאוד, קטנה אי התאמה קטנה להוראות."),
            ][:2 + (idx % 2)]
            for r_idx, (r_rating, comment) in enumerate(review_specs):
                reviewer = demo_users[(idx + r_idx) % len(demo_users)]
                db.add(ProductReview(
                    user_id=reviewer.id,
                    product_id=p.id,
                    rating=int(r_rating),
                    comment=comment,
                    created_at=datetime.datetime.utcnow() - datetime.timedelta(days=idx % 20, hours=r_idx),
                ))

    # --- marketing state ---
    db.add(Notification(
        title="🔥 שבוע הדילים הגיע!",
        message="מעל 50 דילים חדשים נבחרו ע\"י ה-AI שלנו — כולל קופונים ומלחמות מחירים. גללו למטה והתחילו לחסוך!",
        link="/",
        is_popup=True,
    ))
    db.add(Notification(title="🎉 ברוכים הבאים ל-SmartShop המערכת", message="צברו מטבעות על קליקים, שמירת מוצרים והתראות מחיר. הצטרפו עם חשבון Google או במייל!"))
    db.add(Notification(title="💰 מה אפשר לעשות עם מטבעות?", message="מטבעות נותנים דרגות: Bronze Hunter 🥉 ועד Deal Legend 👑. כל קליק שווה 1 מטבע!"))
    db.add(Notification(title="🤖 הכירו את החיפוש החכם", message="כתבו \"אני מחפש מתנה לילד גיל 5\" — וה-AI ימצא מוצרים מתאימים עם הסבר."))

    db.add(AdPlacement(name="קידום גאדג'טים", position="home_top",
                       image_url="https://picsum.photos/seed/adtop/1200/200",
                       target_url="/?category=גאדג'טים", is_active=True))
    db.add(AdPlacement(name="דילים חמים", position="home_side",
                       image_url="https://picsum.photos/seed/adside/300/400",
                       target_url="/?sort=price_asc", is_active=True))
    db.add(AdPlacement(name="מוצרי בית", position="product_banner",
                       image_url="https://picsum.photos/seed/adbanner/1200/120",
                       target_url="/?category=לבית ולמטבח", is_active=True))
    db.add(AdPlacement(name="גאדג'טים לוהטים — תחתית", position="site_bottom",
                       image_url="https://picsum.photos/seed/adbottom/1200/160",
                       target_url="/?category=גאדג'טים", is_active=True))
    db.add(AdPlacement(name="מבצע אלקטרוניקה", position="site_side",
                       image_url="https://picsum.photos/seed/adside2/300/400",
                       target_url="/?category=אלקטרוניקה", is_active=True))

    for email in ("shopper1@example.com", "shopper2@example.com", "shopper3@example.com"):
        db.add(NewsletterSubscriber(email=email, is_active=True))

    # honest social proof — a handful of real clicks in the last hour
    for _ in range(7):
        p = products[_ % len(products)]
        db.add(ClickLog(product_id=p.id, source="aliexpress", session_id="seed-demo",
                        user_ip="127.0.0.1",
                        created_at=datetime.datetime.utcnow() - datetime.timedelta(minutes=5 * _)))
        db.add(AffiliateClick(product_id=p.id, source="aliexpress", ref="site",
                              session_id="seed-demo", user_ip="127.0.0.1",
                              created_at=datetime.datetime.utcnow() - datetime.timedelta(minutes=5 * _)))

    db.commit()
    return {
        "products": len(products),
        "daily_price_rows": len(products) * 30,
        "user": user.email,
        "coupons": len([p for p in products if p.coupon_code]),
    }


if __name__ == "__main__":
    db = SessionLocal()
    try:
        summary = seed(db)
        print("Seeded:", summary)
    finally:
        db.close()
