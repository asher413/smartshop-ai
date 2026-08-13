# 🚀 מדריך העלאה חינמי לשרת — DealBursa

## 📋 סיכום מצב נוכחי

| בדיקה | סטטוס |
|---|---|
| תבניות HTML (16 קבצים) | ✅ תקין |
| CSS + JS | ✅ תקין (35KB + 50KB) |
| Service Worker (PWA) | ✅ תקין |
| אבטחה (CSRF, CSP, Rate Limit) | ✅ פעיל |
| צ'אט ללא AI (fallback) | ✅ עובד |
| חיפוש ללא AI | ✅ עובד |
| מסד נתונים | ✅ מחובר |
| בדיקות (pytest) | ✅ 468 עוברות |
| מפתחות API | ⚠️ יש להזין ידנית |

---

## 🆓 שלב 1: מסד נתונים חינמי — Neon.tech

1. כנסו ל- **[neon.tech](https://neon.tech)** ← לחצו Sign Up (עם GitHub/Google)
2. צרו פרויקט חדש בשם `smartshop`
3. אחרי שהמסד מוכן ← העתיקו את שורת החיבור (Connection String):
   ```
   postgresql://smartshop_owner:xxxxx@ep-xxxx.us-east-2.aws.neon.tech/smartshop?sslmode=require
   ```
4. **שמרו את השורה הזאת** — היא משמשת כ-`DATABASE_URL`

---

## 🆓 שלב 2: העלאה ל-Render

1. כנסו ל- **[render.com](https://render.com)** ← Sign Up (עם GitHub)
2. לחצו **New + → Web Service**
3. חברו את חשבון GitHub שלכם ← בחרו את הריפו `smartshop-ai`
4. Render יזהה אוטומטית את `render.yaml` וימלא את כל ההגדרות

### הגדרות שצריך למלא ידנית בלוח המחוונים של Render:

| משתנה | מה להזין |
|---|---|
| `DATABASE_URL` | שורת החיבור מ-Neon (שלב 1) |
| `SITE_URL` | `https://smartshop-ai.onrender.com` (או השם שבחרתם) |
| `ADMIN_EMAIL` | האימייל שלכם לכניסה לפאנל ניהול |
| `ADMIN_SECRET_KEY` | סיסמה חזקה לפאנל הניהול (להחליף מ-`12345`!) |
| `SESSION_SECRET_KEY` | Render ייצר אוטומטית ✅ |
| `GOOGLE_API_KEY` | מפתח Gemini מ-[aistudio.google.com](https://aistudio.google.com) |
| `SMTP_HOST` | שרת SMTP לשליחת מיילים (Brevo/Gmail) |
| `SMTP_USER` | שם משתמש SMTP |
| `SMTP_PASSWORD` | סיסמת SMTP |
| `SMTP_FROM_EMAIL` | כתובת השולח |
| `GOOGLE_OAUTH_CLIENT_ID` | מפתח OAuth מ-Google Cloud Console |
| `GOOGLE_OAUTH_CLIENT_SECRET` | סוד OAuth מ-Google Cloud Console |
| `ALIEXPRESS_APP_KEY` | מפתח API של AliExpress |
| `ALIEXPRESS_APP_SECRET` | סוד API של AliExpress |
| `ALIEXPRESS_TRACKING_ID` | Tracking ID של AliExpress |
| `EBAY_APP_ID` | App ID של eBay Developer |
| `EBAY_CERT_ID` | Cert ID של eBay Developer |
| `EBAY_CAMPAIGN_ID` | Campaign ID של eBay |
| `AMAZON_PARTNER_TAG` | Amazon Associates tag |
| `AMAZON_PAAPI_ACCESS_KEY` | Amazon Product API key |
| `AMAZON_PAAPI_SECRET_KEY` | Amazon Product API secret |
| `TELEGRAM_BOT_TOKEN` | Token של הבוט בטלגרם |
| `TELEGRAM_CHAT_ID` | Chat ID של הערוץ |
| `INSTAGRAM_ACCESS_TOKEN` | טוקן גרף API של אינסטגרם |
| `INSTAGRAM_ACCOUNT_ID` | מזהה חשבון אינסטגרם |
| `VAPID_PRIVATE_KEY` | מפתח פרטי ל-Web Push |
| `VAPID_PUBLIC_KEY` | מפתח ציבורי ל-Web Push |
| `VAPID_CLAIMS_EMAIL` | `mailto:admin@yourdomain.com` |

> **חשוב:** כל מה שלא תמלאו — האתר יעבוד בלעדיו. רק בלי AI / מייל / התראות / מוצרים אמיתיים. הבסיס (דפים, חיפוש, צ'אט fallback) עובד תמיד.

---

## 🔐 שלב 3: Google OAuth (כפתור "המשך עם Google")

1. כנסו ל- **[console.cloud.google.com/apis/credentials](https://console.cloud.google.com/apis/credentials)**
2. צרו **OAuth 2.0 Client ID** ← Web Application
3. ב-**Authorized redirect URIs** הוסיפו:
   ```
   https://smartshop-ai.onrender.com/auth/google/callback
   ```
4. העתיקו את Client ID ו-Client Secret ל-Render

---

## 🧪 שלב 4: בדיקה אחרי העלאה

1. חכו 3-5 דקות שה-build יסתיים (צפו ב-Deploy log)
2. פתחו את `https://smartshop-ai.onrender.com`
3. בדקו:
   - ✅ דף הבית נטען
   - ✅ חיפוש עובד
   - ✅ התחברות/הרשמה
   - ✅ `/healthz` מחזיר 200
   - ✅ `/admin/login` — כניסה עם ADMIN_EMAIL + ADMIN_SECRET_KEY
   - ✅ `/admin/settings` — הגדרות האתר
   - ✅ `/admin/suppliers` — סטטוס ספקים

### להריץ בדיקות מהירות:
```bash
# בדיקת בריאות
curl https://smartshop-ai.onrender.com/healthz

# בדיקת דף הבית
curl -I https://smartshop-ai.onrender.com/

# בדיקת robots.txt
curl https://smartshop-ai.onrender.com/robots.txt
```

---

## 📊 שלב 5: זריעת מוצרים ראשונית

אחרי שהאתר עולה, היכנסו לפאנל הניהול ולחצו:
1. **Admin Dashboard** → **הרץ גילוי מוצרים**
2. או דרך API:
```bash
curl -u admin:YOUR_ADMIN_SECRET_KEY -X POST https://smartshop-ai.onrender.com/admin/run-discovery
```

---

## 🛡️ שלב 6: אבטחה אחרונה

אחרי העלאה, היכנסו ל-`/admin/settings` ושנו:
1. **ADMIN_SECRET_KEY** ← סיסמה חזקה (לא `12345`!)
2. **SITE_URL** ← וודאו שזה `https://smartshop-ai.onrender.com`
3. לחצו **שמור הגדרות**

---

## 💰 עלות: 0 ש"ח

| שירות | עלות חודשית |
|---|---|
| **Render** (Web Service) | חינם — 750 שעות/חודש |
| **Neon** (PostgreSQL) | חינם — 0.5GB אחסון |
| **Google Gemini API** | חינם — 1,500 בקשות/יום |
| **סה"כ** | **0 ₪** |

> ⚠️ Render חינמי: האתר "נרדם" אחרי 15 דקות ללא תנועה. ביקור ראשון לוקח ~30 שניות להתעורר.
> רוצים למנוע את זה? השתמשו ב-[UptimeRobot](https://uptimerobot.com) (חינם) לשלוח פינג כל 5 דקות.

---

## 📁 קבצים שכבר מוכנים לפריסה

| קובץ | תפקיד |
|---|---|
| `render.yaml` | תצורת Render — כל משתני הסביבה מוגדרים |
| `Dockerfile` | בניית Docker (לא חובה ב-Render, אבל קיים) |
| `requirements.txt` | כל התלויות Python |
| `scripts/check_deploy.py` | בדיקת מוכנות לפריסה |
| `scripts/seed_demo.py` | זריעת נתוני דמו |
| `scripts/init_db.py` | אתחול מסד נתונים |

---

## 🔧 פקודות שימושיות

```bash
# בדיקת מוכנות להעלאה
python scripts/check_deploy.py

# הרצת כל הבדיקות
python -m pytest tests/ -q --ignore=tests/test_load.py

# זריעת מוצרי דמו
python scripts/seed_demo.py
```

---

**✅ הפרויקט מוכן להעלאה! כל מה שצריך — לשים את ה-DATABASE_URL ב-Render וללחוץ Deploy.**
