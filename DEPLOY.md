# 🚀 מדריך העלאה — דילבורסה (DealBursa)

## איפה אפשר להעלות בחינם / הכי זול?

| ספק | עלות | מתאים ל... | קישור |
|---|---|---|---|
| **Render** | **חינם** (750 שעות/חודש) | הכי קל, אוטומטי מ-GitHub | [render.com](https://render.com) |
| **Fly.io** | **חינם** (3 אפליקציות קטנות) | יותר שליטה, צריך CLI | [fly.io](https://fly.io) |
| **Railway** | $5/חודש (אשראי התחלתי $5) | ביצועים טובים, קל | [railway.app](https://railway.app) |
| **PythonAnywhere** | חינם (מוגבל) | הכי פשוט, אין צורך ב-Docker | [pythonanywhere.com](https://www.pythonanywhere.com) |

**המלצה:** Render — הכי קל, חינמי, מתחבר אוטומטית ל-GitHub.

---

## 📦 Render — צעד אחר צעד

### שלב 1: דחוף את הקוד ל-GitHub
```bash
git init
git add .
git commit -m "DealBursa ready for deploy"
git remote add origin https://github.com/המשתמש-שלך/smartshop-ai.git
git push -u origin main
```

### שלב 2: צור Web Service ב-Render
1. היכנס ל-[dashboard.render.com](https://dashboard.render.com)
2. לחץ **New +** → **Web Service**
3. חבר את חשבון GitHub ובחר את הריפו `smartshop-ai`
4. הגדרות:
   - **Name:** `smartshop`
   - **Runtime:** `Python 3`
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `uvicorn app.api.main:app --host 0.0.0.0 --port $PORT`
5. **Free plan** — 750 שעות בחודש (מספיק לאתר אחד)
6. לחץ **Create Web Service**

### שלב 3: הגדר משתני סביבה (Environment Variables)
ב-Render → Dashboard → Environment:

| משתנה | חובה? | איפה להשיג |
|---|---|---|
| `ADMIN_SECRET_KEY` | ✅ חובה | הסיסמה שלך לפאנל הניהול |
| `ADMIN_EMAIL` | ✅ חובה | המייל לכניסה לניהול |
| `SESSION_SECRET_KEY` | ✅ חובה | מחרוזת אקראית ארוכה (למשל `openssl rand -hex 32`) |
| `SITE_URL` | ✅ חובה | `https://smartshop.onrender.com` (הכתובת ש-Render נותן) |
| `DATABASE_URL` | ✅ חובה | Render נותן PostgreSQL חינמי — צור **New PostgreSQL** והוסף את ה-URL |
| `GOOGLE_API_KEY` | 🔸 מומלץ | מ-[aistudio.google.com](https://aistudio.google.com) (חינם) |
| `GOOGLE_OAUTH_CLIENT_ID` | 🔸 מומלץ | Google Cloud Console → OAuth |
| `GOOGLE_OAUTH_CLIENT_SECRET` | 🔸 מומלץ | Google Cloud Console → OAuth |
| `SMTP_HOST` | 🔸 אופציונלי | Brevo (חינם 300 מיילים/יום) |
| `SMTP_PORT` | 🔸 אופציונלי | `587` |
| `SMTP_USER` | 🔸 אופציונלי | מהגדרות Brevo |
| `SMTP_PASSWORD` | 🔸 אופציונלי | מהגדרות Brevo |
| `SMTP_FROM_EMAIL` | 🔸 אופציונלי | `no-reply@smartshop.co.il` |
| `INSTAGRAM_ACCESS_TOKEN` | 🔸 אופציונלי | Facebook Developers → Instagram API |
| `ALIEXPRESS_APP_KEY` | 🔸 אופציונלי | [portals.aliexpress.com](https://portals.aliexpress.com) |
| `EBAY_APP_ID` | 🔸 אופציונלי | [developer.ebay.com](https://developer.ebay.com) |

> 💡 **את כל המפתחות אפשר להזין גם דרך פאנל הניהול** (`/admin/settings`) — בלי לגעת ב-Render.

### שלב 4: המתן לדיפלוי
Render יבנה אוטומטית — כ-3-5 דקות. כשתראה `Live`, האתר זמין בכתובת `https://smartshop.onrender.com`.

---

## 🛡️ מה כלול באתר (נכון לעכשיו)

### 🔐 אבטחה
- CSRF protection על כל נתיבי POST בניהול
- Rate limiting (per-user, per-admin)
- Brute-force guard על התחברות
- TrustedHostMiddleware — מונע Host-header poisoning
- CSP headers בסיסיים
- HttpOnly + SameSite=Lax cookies
- HTTPS-only session בפרודקשן

### 🧠 AI & חיפוש
- **Smart Search** — חיפוש בשפה טבעית (Gemini)
- **Smart Bundles** — חבילות מוצרים משלימים בהנחה
- **חיפוש לפי תמונה** — העלאת תמונה ומציאת מוצרים דומים
- **צ'אט AI** — עוזר קניות מובנה
- **חיזוי מחירים** — המלצה אם לחכות או לקנות עכשיו
- **סיכום ביקורות** — AI מסכם את תמצית הביקורות
- **NLP fallback** — החיפוש עובד גם בלי AI (LIKE queries)

### 🛒 תכונות
- **השוואת מחירים** — Price War בין כל הספקים
- **מעקב משלוחים** — חיבור ל-17TRACK
- **קופונים** — משיכה אוטומטית מכל הספקים
- **מועדפים** — שמירת מוצרים בלב אחד
- **התראות מחיר** — קבלו מייל כשהמחיר יורד
- **Social Proof** — עדכוני רכישות בזמן אמת
- **טיימרים למבצעים** — ספירה לאחור עד חצות
- **קונים חכמים בחרו גם** — Cross-sell חכם

### 🎨 UX
- **ערכת נושא בהירה/כהה** — נשמרת per-user
- **עיצוב AliExpress מתקדם** — Mega-menu, סרגל צד, היסטוגרמת מחירים
- **מותאם למובייל וטאבלט** — Responsive מלא
- **Cookie consent** — GDPR-friendly
- **Sticky header** — זכוכית בסקרול
- **Quick View** — תצוגה מהירה של מוצר בלי לעזוב את העמוד
- **שיתוף בוואטסאפ/פייסבוק/טלגרם**

### 🖥️ פאנל ניהול (`/admin`)
- **הגדרות** — כל המפתחות ניתנים לעריכה דרך הממשק
- **סטטוס ספקים** — איזה ספק מחובר, משיכת מוצרים בלחיצה
- **בדיקת חיבור** — כפתור "בדוק" חי לכל שירות
- **דוחות** — גרפי עוגה, TOP-10, יצוא CSV
- **פניות ממרכז העזרה** — ניהול הודעות ויצירת קשר
- **דשבורד** — תמונת מצב מלאה

### 🔌 ספקים מחוברים
| ספק | סוג חיבור |
|---|---|
| AliExpress | API רשמי (App Key/Secret) + Affiliate |
| Amazon | PA-API v5 + Associates |
| eBay | API רשמי (App ID/Cert ID) + ePN |
| Temu | Affiliate ID (ללא API ציבורי) |
| Awin | API Token |
| CJ Affiliate | API Token |
| Rakuten Advertising | Client ID/Secret |
| B&H Photo | Scraping |

---

## 💰 איך מרוויחים?

האתר משתמש ב**קישורי שותפים (Affiliate Links)**. כל לחיצה על "קנה עכשיו" עוברת דרך `/go/{product_id}` שמוסיף אוטומטית את תגי העמלה שלכם. אתם מקבלים עמלה על כל רכישה — בלי שהקונה משלם אגורה נוספת.

**ספקים עם תוכניות שותפים:**
- **AliExpress** — עד 9% עמלה
- **Amazon** — עד 10% עמלה
- **eBay** — עד 7% עמלה
- **Temu** — עד 5% עמלה
- **Awin / CJ / Rakuten** — אלפי מותגים, עמלות משתנות

---

## 🆓 איך להשיג מפתחות חינם?

### Google Gemini (AI) — חינם
1. היכנסו ל-[aistudio.google.com](https://aistudio.google.com)
2. לחצו **Get API Key**
3. העתיקו את המפתח — `AIza...`
4. הדביקו ב-`GOOGLE_API_KEY`

### Brevo SMTP — חינם (300 מיילים/יום)
1. הירשמו ב-[brevo.com](https://www.brevo.com)
2. **SMTP & API** → **SMTP Keys** → Generate
3. העתיקו: `SMTP_HOST=smtp-relay.brevo.com`, `SMTP_PORT=587`, `SMTP_USER=...`, `SMTP_PASSWORD=...`

### Google OAuth (כפתור "המשך עם Google") — חינם
1. [console.cloud.google.com](https://console.cloud.google.com) → **APIs & Services** → **Credentials**
2. **Create Credentials** → **OAuth Client ID** → **Web application**
3. Authorized redirect URIs: `https://האתר-שלכם/auth/google/callback`
4. העתיקו Client ID + Client Secret

### AliExpress Affiliate — חינם (צריך אישור)
1. [portals.aliexpress.com](https://portals.aliexpress.com) → **Affiliate Program**
2. הרשמה + אישור (1-3 ימים)
3. **AliOpen** → Create App → App Key / App Secret
4. **Tracking ID** — מפורטל השותפים

### eBay Developer — חינם, מיידי
1. [developer.ebay.com](https://developer.ebay.com) → Create Account
2. **Create App** → App ID + Cert ID (מיידי)
3. [epn.ebay.com](https://epn.ebay.com) → Campaign ID לעמלות

---

## 📞 תמיכה

נתקעתם? פתחו Issue ב-GitHub או שלחו הודעה דרך מרכז העזרה באתר (`/help`).
