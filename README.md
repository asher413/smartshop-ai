---
title: DealBursa
emoji: 🛒
colorFrom: blue
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
license: mit
---

# DealBursa — בורסת דילים חכמה נבחרים ע"י הצוות שלנו

אתר אפילייט רב-ספקים עם מנוע איסוף מוצרים אוטומטי (AliExpress / Amazon / eBay / Temu),
העשרת תוכן ב-AI (Gemini), צ'אטבוט קונסיירז', והשוואת מחירים חיה.

[![Deploy to Hugging Face](https://huggingface.co/datasets/huggingface/badges/raw/main/deploy-to-spaces-lg.svg)](https://huggingface.co/new-space?template=asher413/smartshop-ai)

## 🚀 פריסה מהירה בחינם — Hugging Face Spaces

1. לחץ על הכפתור למעלה
2. בחר **Docker** כ-Space SDK
3. המתן 3-5 דקות לבנייה
4. האתר זמין ב-`https://yourname-smartshop-ai.hf.space`

**מה כלול בחינם:** 16 GB RAM, 2 vCPU, 50 GB SSD, HTTPS אוטומטי — **בלי כרטיס אשראי!**

> ⚠️ ה-Space נכנס לשינה אחרי 48 שעות ללא בקשות. כדי לשמור אותו ער 24/7,
> היכנס ל-[cron-job.org](https://cron-job.org) (חינם) וצור קריאת GET ל-`https://yourname-smartshop-ai.hf.space/healthz` כל 5 דקות.


אתר אפילייט רב-ספקים עם מנוע איסוף מוצרים אוטומטי (AliExpress / Amazon / eBay / Temu),
העשרת תוכן ב-שירות (Gemini), צ'אטבוט קונסיירז', והשוואת מחירים חיה.

## מבנה הפרויקט

```
app/
  core/
    config.py, database.py, models.py
    security_middleware.py  # rate limiting (slowapi) + security headers
  adapters/       # שכבת ספקים אחידה — הלב של האיסוף האוטומטי
    base_adapter.py        # ה-interface שכל ספק חייב לממש
    aliexpress_adapter.py  # API רשמי + fallback לסקרייפינג
    amazon_adapter.py      # PA-API 5.0 + fallback לסקרייפינג
    ebay_adapter.py        # Browse API + fallback לסקרייפינג
    temu_adapter.py        # אין API רשמי כרגע -> סקרייפינג בלבד
    awin_adapter.py         # רשת שותפים #1 — אלפי סוחרים דרך API אחד
    cj_adapter.py           # רשת שותפים #2 (CJ / Commission Junction)
    scraping_adapter.py    # הפולבק הגנרי (AI-assisted, מבוסס Playwright)
  services/
    fraud_service.py, tracking_service.py, product_service.py
    aggregator_service.py, price_service.py, price_monitor_service.py
    semantic_search_service.py, order_tracking_service.py
    auth_service.py, csrf_service.py, brute_force_guard.py, cache_service.py
    email_service.py       # SMTP verification emails
  agents/         # content_generator, marketing_agent, chatbot, recommender,
                  # blog_agent, auto_viral_engine, fulfillment_agent
  workers/        # auto_import_worker + scheduler (APScheduler)
  api/main.py     # FastAPI — כל ה-routes
  templates/      # Jinja2, עיצוב glassmorphism כהה, 13 תבניות
  static/         # css/js/manifest
scripts/init_db.py
tests/
```

## איך מנוע האיסוף האוטומטי עובד

1. **Discovery** (`aggregator_service.discover_trending`) — כל אדפטר (AliExpress,
   Amazon, eBay, Temu) מתבקש להחזיר "מה חם עכשיו". כל אחד עובד ב-API רשמי אם יש
   הרשאות, ואם אין — נופל אוטומטית לסקרייפינג עם AI לניתוח הדף.
2. **Staging** — כל מוצר שנמצא נשמר תחילה בטבלת `TrendingCandidate`, לא ישירות
   כ-`Product` חי. זה ה-checkpoint שמונע ממוצרים זבל/כפולים/עם מחיר שגוי לעלות
   אוטומטית בלי שום בדיקה.
3. **Scoring** — כל מועמד מקבל ציון 0-100 (ביקוש + דירוג + כמות ביקורות). ציון
   מעל `AUTO_PROMOTE_THRESHOLD` (ברירת מחדל 85) מקודם אוטומטית ל-`Product` חי;
   השאר ממתינים לאישור ידני בממשק הניהול.
4. **Enrichment** (`auto_import_worker.enrich_pending_products`) — מוצרים
   שקודמו אך עדיין ללא תוכן AI (`is_verified=False`) מקבלים כותרת, תיאור,
   יתרונות/חסרונות, AI Verdict, ותגית דחיפות — ורק אז עולים לעמוד הבית.
5. **Scheduler** — `app/workers/scheduler.py` מריץ discovery כל שעתיים ו-
   enrichment כל 15 דקות. אפשר גם להריץ ידנית מכפתור בממשק הניהול (`/admin`).

## הגדרת ספקים (הכי חשוב לפני production)

| ספק | סטטוס API | מה לעשות |
|---|---|---|
| **eBay** | Browse API — קל להשגה | הירשמו ל-eBay Developer Program, מלאו `EBAY_APP_ID` |
| **AliExpress** | Affiliate API רשמי | הצטרפו ל-AliExpress Affiliate Program, מלאו `ALIEXPRESS_APP_KEY/SECRET/TRACKING_ID` |
| **Amazon** | PA-API 5.0 — דורש 3 מכירות תוך 180 יום | התחילו עם לינק פשוט + `AMAZON_PARTNER_TAG` בלבד, שדרגו ל-API אחרי המכירות הראשונות |
| **Temu** | אין API ציבורי | ישאר במצב סקרייפינג — עדכנו `TEMU_AFFILIATE_ID` להוספת פרמטר מעקב בלבד |
| **Awin** (רשת שותפים) | Product Feed API רשמי | ⭐ **המינוף הכי גבוה** — הצטרפו ל-[awin.com](https://www.awin.com), אשרו לתוכניות סוחרים (Shein, מותגי אלקטרוניקה, קמעונאים אזוריים ועוד), מלאו `AWIN_API_TOKEN` + `AWIN_PUBLISHER_ID`. במקום אינטגרציה בודדת פר-אתר, אינטגרציה אחת נותנת גישה לאלפי סוחרים — כל סוחר חדש שתאושרו אליו מופיע אוטומטית, בלי לכתוב קוד. |
| **CJ Affiliate** (רשת שותפים) | Product Catalog API רשמי | רשת שותפים שנייה — [cj.com](https://www.cj.com), מכסה סוחרים גדולים נוספים (Walmart, Wayfair ועוד). מלאו `CJ_API_TOKEN` + `CJ_COMPANY_ID`. אותה לוגיקה בדיוק כמו Awin: יותר סוחרים מאושרים = יותר מוצרים, בלי קוד נוסף. |

**חשוב:** סקרייפינג בקנה מידה גדול עלול להפר תנאי שימוש של ספקים מסוימים. ה-
`ScrapingAdapter` בנוי במכוון עם delay מינימלי בין בקשות ובלי עקיפת CAPTCHA/
זיוף טביעת אצבע — שמרו על שימוש מתון (discovery + רענון מחיר מדי פעם), לא
harvesting אגרסיבי, ועברו ל-API הרשמי ברגע שהוא זמין לכם.

## מערכת משתמשים ואזור אישי

- **הרשמה/התחברות אמיתית** (`/signup`, `/login`) — סיסמאות מוצפנות עם bcrypt
  (`passlib`), session מבוסס cookie חתום (`SESSION_SECRET_KEY`), לא JWT
  מיותר לצורך הזה.
- **אזור אישי** (`/personal-area`, דורש התחברות) מציג: הזמנות + מעקב משלוח
  חי, מוצרים שמורים (❤️ בעמוד מוצר), התראות מחיר, והיסטוריית קליקים.
- **ניהול משתמשים** בלוח הניהול (`/admin`) — טבלת כל המשתמשים עם אפשרות
  להשעות/להפעיל מחדש חשבון (למקרה של ניצול לרעה או מחלוקת חיוב), בלי למחוק
  את היסטוריית ההזמנות שלו.
- **אימות אימייל** — נשלח אוטומטית בהרשמה (קישור חתום, בתוקף 48 שעות,
  בלי צורך בטבלת DB נפרדת). אם לא הוגדר SMTP, האתר לא נשבר — פשוט לא
  נשלח מייל (רואים לוג "SMTP not configured"), והמשתמש עדיין יכול
  להתחבר; באזור האישי מוצג באנר "שלח שוב" ברגע שמגדירים SMTP.
- **התחברות עם Google** — כפתור "המשיך עם Google" מופיע אוטומטית
  בעמודי login/signup ברגע שמגדירים `GOOGLE_OAUTH_CLIENT_ID/SECRET`.
  משתמשים שנרשמים כך מסומנים כמאומתים אוטומטית (Google כבר אימת את
  המייל בעצמו). אם כתובת האימייל כבר קיימת מהרשמה רגילה, החשבון
  מתחבר אליה במקום ליצור כפילות.

### הגדרת שליחת אימייל (בחינם)
הכי קל להתחלה: **Brevo** (לשעבר Sendinblue) — [brevo.com](https://www.brevo.com),
free tier של 300 מיילים ביום. אחרי הרשמה: SMTP & API → SMTP → מעתיקים
Host/Port/Login/Password ל-`SMTP_HOST/PORT/USER/PASSWORD`. חלופה: Gmail
עם [App Password](https://myaccount.google.com/apppasswords) (מוגבל יותר
בנפח, טוב לבדיקות).

### הגדרת Google Sign-In
1. [console.cloud.google.com](https://console.cloud.google.com) → פרויקט
   חדש → **APIs & Services** → **OAuth consent screen** — מגדירים כ-External,
   ממלאים שם אפליקציה ואימייל תמיכה.
2. **Credentials** → **Create Credentials** → **OAuth client ID** → סוג
   **Web application**.
3. **Authorized redirect URIs** — מוסיפים בדיוק: `https://<your-app>.onrender.com/auth/google/callback`
   (וגם `http://localhost:8000/auth/google/callback` לבדיקות מקומיות).
4. מעתיקים Client ID + Client Secret ל-`GOOGLE_OAUTH_CLIENT_ID/SECRET`.

## 📧 חיבור SMTP אמיתי (אימייל אימות + ניוזלטר) — צעד אחר צעד

הניוזלטר ואימיילי האימות עובדים רק אחרי שממלאים 4 משתנים ב-`.env`.
הדרך הכי קלה בחינם: **Brevo** (לשעבר Sendinblue).

1. הרשמו ב-[brevo.com](https://www.brevo.com) (חינם, 300 מיילים ליום).
2. תפריט צדדי → **SMTP & API** → **SMTP**.
3. תחת "SMTP Settings" תמצאו: **Host** (`smtp-relay.brevo.com`), **Port** (`587`),
   **Login** (האימייל של חשבון Brevo), **Password** (הסיסמה שמוצגת שם).
4. מכניסים ל-`.env`:
   ```
   SMTP_HOST=smtp-relay.brevo.com
   SMTP_PORT=587
   SMTP_USER=<האימייל של חשבון Brevo>
   SMTP_PASSWORD=<הסיסמה מ-Brevo>
   SMTP_FROM_EMAIL=no-reply@<הדומיין שלכם>
   SMTP_FROM_NAME=DealBursa
   ```
5. **חובה לאמת את הדומיין ב-Brevo** (הגדרות → Senders → הוספת domain
   + הוספת רשומת DNS אצל ספק הדומיין שלכם) — אחרת מיילים יגיעו ל-Spam.
   בשלב dev אפשר לשלוח גם מ-`no-reply@brevo.com`.
6. אתחלו מחדש את השרת. בלוח הניהול תראו "📧 SMTP מחובר".

**חלופה (לבדיקות בלבד):** Gmail — [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords),
אחרי הפעלת אימות דו-שלבי. `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`,
`SMTP_USER=<האימייל>`, `SMTP_PASSWORD=<App Password בן 16 תווים>`. מוגבל
בנפח ולכן פחות מתאים לניוזלטר של אלפי נרשמים.

## 📸 חיבור אינסטגרם אמיתי (פרסום אוטומטי של דילים) — צעד אחר צעד

האתר משתמש ב-**Instagram Graph API** (הדרך הרשמית היחידה לפרסום אוטומטי).
זה מצריך אפליקציית פייסבוק + חשבון אינסטגרם **עסקי**. פעם אחת, כ-20 דקות:

1. **החשבון העסקי**: באפליקציית אינסטגרם → הגדרות → חשבון → "החלף לחשבון
   מקצועי" → קטגוריה (קניות/עסק) → חברו לעמוד פייסבוק אם יש.
2. **האפליקציה**: [developers.facebook.com](https://developers.facebook.com) →
   **My Apps** → **Create App** → בחרו "**Business**" (חובה! לא Consumer).
   הוסיפו את ה-Product "**Instagram Graph API**".
3. **חיבור החשבון**: בתוך ה-Product → "Instagram API Setup" → **Add
   Instagram Account** → התחברו עם חשבון האינסטגרם העסקי.
4. **ההרשאות**: הגדרות → **Basic** → 
   [אשרו את האפליקציה](https://developers.facebook.com/docs/development/create-an-app/app-dashboard/basic-settings#app-review)
   (App Review) עם שלוש הרשאות: `instagram_basic`, `instagram_content_publish`,
   `pages_read_engagement` — אפשר להגיש בקשה מוקדמת גם בלי אישור סופי.
5. **הטוקן**: [developers.facebook.com/tools/explorer](https://developers.facebook.com/tools/explorer) →
   בחרו את האפליקציה → **Generate Access Token** (נכנסים עם חשבון הפייסבוק).
   **חשוב:** להמיר ל-**Long-Lived Token** (60 יום, ואז מרעננים) — אפשר
   [בפייסבוק Exchange Token](https://developers.facebook.com/docs/facebook-login/guides/access-tokens#longlived).
6. **מזהה החשבון**: באותו Explorer הרצת `GET /me/accounts` → רושמים את
   ה-**id** של העמוד → הרצת `GET /<page-id>?fields=instagram_business_account`
   → זה ה-**id** של חשבון האינסטגרם.
7. מכניסים ל-`.env` ומאתחלים:
   ```
   INSTAGRAM_ACCESS_TOKEN=<הטוקן הארוך>
   INSTAGRAM_ACCOUNT_ID=<מזהה חשבון האינסטגרם>
   ```
8. בלוח הניהול → "📸 פרסום דיל" → מזינים מזהה מוצר → לוחצים.
   אם משהו נכשל, האתר יראה לכם את **קוד השגיאה המדויק** מ-Facebook
   (למשל 190 = טוקן פג, 2207029 = טוקן לא ארוך-טווח).

> תמונת המוצר חייבת להיות נגישה דרך כתובת URL ציבורית (ה-Graph API מוריד
> אותה מהשרת שלו) — תמונות מ-`localhost` או משרת מקומי אחר ייכשלו.

## 🔐 כניסה לניהול האתר

1. היכנסו ל-`https://<האתר שלכם>/admin` (מקומית: `http://localhost:8000/admin`).
2. מזינים **מייל מערכת + סיסמה** — שניהם מוגדרים בקובץ ההגדרות:
   - מייל = `ADMIN_EMAIL` (ברירת מחדל: `admin@smartshop.ai`)
   - סיסמה = `ADMIN_SECRET_KEY` (ברירת מחדל: `12345`)
3. אחרי הכניסה אפשר: להפעיל איסוף מוצרים, לשלוח ניוזלטר, לפרסם לאינסטגרם,
   לנהל פרסומות ופופאפים, לערוך את כל המפתחות מדף ההגדרות, ולראות סטטוס
   כל ההגדרות.
4. **חובה** להחליף את סיסמת ברירת המחדל (`12345`) בטרם עלייה לאוויר —
   מדף ההגדרות בניהול או בקובץ `.env`.

### ⚙️ דף ההגדרות (בלי לערוך .env ידנית)

בלוח הניהול → `https://<האתר>/admin/settings` (או כפתור "ניהול הגדרות"
בלוח הבקרה) אפשר להזין, לשמור ולבדוק את **כל** המפתחות מהממשק:

- **AI** — `GOOGLE_API_KEY` + `GEMINI_MODEL` (בדיקת חיבור אמיתית מול Gemini).
- **SMTP** — Host/Port/User/Password/From (בדיקת connect+login בפועל).
- **אינסטגרם** — Token + Account ID (בדיקה מול Graph API).
- **ספקים** — כל מפתחות השותפים: AliExpress, eBay (עם בדיקת OAuth),
  Amazon, Temu, Awin (בדיקת טוקן), CJ, 17TRACK וטלגרם (בדיקת בוט).
- **כללי** — `SITE_URL`, `ADMIN_EMAIL`, `ADMIN_SECRET_KEY`,
  `SESSION_SECRET_KEY`.

איך זה עובד: הערכים נשמרים לתוך קובץ `.env` (שורות קיימות נשמרות),
מוחלים על הגדרות האתר מיידית, וכל סוכני ה-AI נטענים מחדש — **בלי הפעלה
מחדש של השרת**. סיסמאות מוצגות ממוסכות, ושדה ריק = לא לגעת בערך הקיים
(יש תיבת סימון "מחק" כדי לנקות סוד).

### ⚔️ השוואת מחירים בין אתרים בעמוד המוצר

בעמוד מוצר יש פאנל "השוואת מחירים בין האתרים שמוכרים את המוצר":
- בודק את המחיר העדכני מול הספק המקורי, ומחפש את אותו מוצר אצל ספקים
  נוספים (AliExpress/eBay/Amazon/... דרך ה-adapters).
- מסדר את ההצעות מהזול ליקר, מסמן את **הזול ביותר** בתגית ירוקה,
  ומציג את החיסכון הפוטנציאלי וקישור לכל חנות.
- התאמות בין ספקים מסומנות "התאמה משוערת" — אין מניפולציה על הנתונים.

## 🛒 חיבור ספקים אמיתי עם עמלות — eBay + AliExpress

> 📘 **המדריך המלא והעדכני (כולל Awin, CJ, Amazon, Temu ורשתות נוספות) נמצא
> בקובץ [`SUPPLIERS_GUIDE.md`](SUPPLIERS_GUIDE.md)** — שלב-שלב עם ההרשאות
> הנדרשות, השדות המדויקים לכל ספק, ואיך לבדוק שכל חיבור עובד.

כדי שהאתר ימשוך מוצרים אמיתיים עם קישורי עמלה (ולא רק סקרייפינג), צריך
להצטרף לתוכניות השותפים. כך עושים את זה, שלב-שלב:

### eBay — 2 הרשמות (כ-30 דקות)

**א. תוכנית השותפים — מקבלים את Campaign ID (מספר קמפיין):**
1. צרו חשבון eBay רגיל ([ebay.com](https://www.ebay.com)) אם אין לכם.
2. היכנסו ל-[epn.ebay.com](https://epn.ebay.com) (eBay Partner Network)
   → "Join the Program" → הגישו בקשה עם הפרטים שלכם.
3. אחרי אישור, מהדשבורד של ePN תמצאו את **Campaign ID** — זה `EBAY_CAMPAIGN_ID`.
   (אם עוד לא אושרתם, אפשר להתחיל עם Browse API בלי ePN, ופשוט להוסיף
   את ה-Campaign ID מאוחר יותר כשמאושרים.)

**ב. חשבון מפתחים — מקבלים את ה-API Keys:**
1. הירשמו ב-[developer.ebay.com](https://developer.ebay.com) → **Register**
   (חינם, מיידי).
2. **Dashboard → My Applications → Create a keyset**.
3. מחרוזת ה-**App ID** = client id → `EBAY_APP_ID`; מחרוזת ה-**Cert ID**
   = client secret → `EBAY_CERT_ID`.
4. כדי שה-Browse API יעבוד ב-Production: בתוך ה-keyset לחצו
   **"Request production access"** → בחרו **Browse API** (וגם Marketing
   APIs אם רוצים) → מלאו את הטופס (מתאר קצר על האתר, בדרך כלל מאושר
   תוך כמה שעות-ימים). Browse API לא דורש user consent — רק את ה-keyset
   והרשאת Production.

**אחרי האישור מכניסים ל-.env (או דרך דף ההגדרות בניהול):**
```
EBAY_APP_ID=<App ID>
EBAY_CERT_ID=<Cert ID>
EBAY_CAMPAIGN_ID=<Campaign ID מ-ePN>
```

### AliExpress — תוכנית שותפים (כ-1-3 ימי אישור)

1. צרו חשבון AliExpress רגיל ([aliexpress.com](https://www.aliexpress.com)).
2. היכנסו ל-[portals.aliexpress.com](https://portals.aliexpress.com) →
   בחרו **Affiliate Program** → "Apply" → מלאו את הטופס (תיאור האתר
   והקהל — אישור אוטומטי או ידני תוך יום-יומיים).
3. אחרי האישור: בדשבורד השותפים → **API & Data** (AliOpen) →
   **Create App** → מקבלים **App Key** ו-**App Secret**.
4. **Tracking ID**: באותו דשבורד, תחת הגדרות מעקב (Tracking Settings) →
   צרו Tracking ID (אפשר גם ברירת המחדל "default"). זה מזהה אותך
   בעמלות, חובה לכל קישור.
5. **הרשאות API**: בחלק מ-API & Data צריך ללחוץ "Apply" על שירותי
   ה-API (Product/Order API) כדי להפעיל אותם — עשו זאת ושמרו.

**מכניסים ל-.env (או דרך דף ההגדרות):**
```
ALIEXPRESS_APP_KEY=<App Key>
ALIEXPRESS_APP_SECRET=<App Secret>
ALIEXPRESS_TRACKING_ID=<Tracking ID>
```

אחרי שהטוקנים נכנסו, בלוח הניהול → "סטטוס ספקים" יופיע **API רשמי** ירוק
במקום "סקרייפינג בלבד", ומנוע האיסוף יתחיל למשוך מוצרים אמיתיים עם
קישורי עמלה. המלצה: התחילו עם eBay (הכי קל ומהיר לאישור), והוסיפו את
AliExpress כשיאושרו.

## "איפה המוצר עכשיו" — מעקב משלוחים חי

`app/services/order_tracking_service.py` מתחבר ל-**17TRACK**
([features.17track.net](https://features.17track.net)), שמזהה אוטומטית את
חברת השילוח מתוך יותר מ-1,700 רשתות (China Post, Yanwen, ePacket וכו') —
בדיוק המצב הטיפוסי בהזמנות AliExpress/Temu שבו אי אפשר לדעת מראש דרך איזו
חברה המשלוח יגיע. יש להם free tier — בדקו מגבלות עדכניות באתר שלהם לפני
שמסתמכים על נפח גדול.

איך זה עובד בפועל: כשיש להזמנה מספר מעקב (`Order.tracking_number`), הלקוח
לוחץ "רענן סטטוס משלוח" באזור האישי → `/api/orders/<id>/refresh-tracking`
→ שולף את הסטטוס העדכני ומציג אותו בעברית ("בדרך אליך", "יצא לחלוקה",
"נמסר בהצלחה! 📦") בלי לצאת מהאתר.

## חוויית משתמש — חיפוש, מיון, ופרסונליזציה

- **חיפוש אמיתי** (`/search`) עם autocomplete חי (`/api/search-suggest`,
  debounced) — שורת החיפוש בניווט הייתה עיצובית בלבד קודם, בלי שום דבר
  מאחוריה. עכשיו מחוברת בפועל.
- **מיון ופאג'ינציה** בעמוד הבית — לפי החדש ביותר / מחיר / דירוג, 24
  מוצרים לעמוד. בעבר היה `limit(24)` קשיח בלי שום דרך לראות מעבר לזה.
- **"המשיכו מאיפה שהפסקתם"** — קרוסלת מוצרים שנצפו לאחרונה בעמוד הבית,
  באמצעות נתוני `ProductView` שכבר נאספו אבל מעולם לא הוצגו בשום מקום.

## מעקב מחירים והתראות (price history + price alerts)

שני פיצ'רים ש"חיכו" בסכמת ה-DB בלי שום קוד שמפעיל אותם — עכשיו פעילים:

- **היסטוריית מחיר** (`price_monitor_service.record_daily_prices`) — לוקח
  תמונת מצב יומית של המחיר לכל מוצר פעיל, ומציג אותה כגרף (Chart.js) בעמוד
  המוצר. גרף היסטוריית מחיר הוא פיצ'ר אמון סטנדרטי באתרי השוואת מחירים
  רציניים (בדומה ל-camelcamelcamel לאמזון).
- **הפעלת התראות מחיר** (`price_monitor_service.check_price_alerts`) —
  משווה כל התראה פעילה מול המחיר הנוכחי, ומסמן `is_triggered=True` ברגע
  שהמחיר יורד מתחת ליעד. מוצג מיד באזור האישי ("הופעל! 🎉").
- מוגדר לרוץ כל 6 שעות ב-`scheduler.py`, וגם דרך `/admin/run-price-monitor`
  (אותו pattern בדיוק כמו `/admin/run-discovery` — ל-cron-job.org חינמי).

## כללי ציות לאפילייט שכבר מיושמים באתר

- **גילוי נאות "ברור ובולט" (FTC-style)** — לא רק בפוטר: יש טקסט גילוי
  צמוד ממש ליד כפתור הקנייה בעמוד מוצר ("🔗 קישור שותפים...").
- **`rel="sponsored noopener"`** על קישורי שותפים חיצוניים — עומד בהנחיות
  גוגל לתוכן ממומן/שותפים (חשוב לדירוג SEO תקין, לא רק לאתיקה).
- **הטקסט הרשמי הנדרש ע"י Amazon Associates** ("As an Amazon Associate I
  earn from qualifying purchases") מופיע בפוטר בכל עמוד — זו דרישה
  מפורשת בהסכם התפעול של Amazon Associates לכל עמוד עם קישורי Amazon.
- **הודעת Cookie Consent** — נדרש כי האתר קובע cookie טכני (session_id).
- **עמודי אודות/פרטיות/תנאי שימוש** — נבדקים בפועל ע"י צוותי האישור של
  AliExpress/Amazon/eBay Affiliate לפני אישור חשבון.
- **בלי "הוכחה חברתית" מזויפת** — כל מספר שמוצג (קליקים, ביקורות) מגיע
  מנתונים אמיתיים בלבד; ראו הערה נפרדת למטה.

⚠️ **זה עדיין לא ייעוץ משפטי.** לפני production אמיתי עם תנועה משמעותית,
מומלץ לוודא מול עו"ד שהניסוחים מתאימים לחוק הגנת הצרכן הישראלי ולתנאי כל
תוכנית שותפים ספציפית שתצטרפו אליה (חלקן עם דרישות ניסוח משלהן).

## 🛍️ להופיע בחיפוש הקניות של גוגל (Google Shopping) — צעד אחר צעד

האתר כבר מפיק את כל הנכסים הטכניים שצריך כדי להופיע בכרטיסיית
"קניות" של גוגל (הקרוסלה מעל תוצאות החיפוש הרגילות). מה שנשאר זה
לחבר את ה-feed ל-Google Merchant Center:

1. **מה כבר מוכן באתר:**
   - מזין מוצרים חי: `https://<האתר שלכם>/feed/google-shopping.xml`
     (כל המוצרים המאומתים, עם מחיר ILS, זמינות, מותג, תמונות — מתעדכן
     כל 15 דקות).
   - כל עמוד מוצר כולל JSON-LD מסוג `Product` + `BreadcrumbList`
     (מחיר, מלאי, מותג, דירוג, sku, priceValidUntil).
   - עמוד הבית כולל `Organization` + `WebSite` עם SearchAction
     (מעניק "תיבת חיפוש" בגוגל).
   - `sitemap.xml` ו-`robots.txt` כבר פעילים.
2. **הירשמו** ב-[merchants.google.com](https://merchants.google.com) —
   מתחברים עם חשבון Google, בוחרים מדינה ומטבע (ישראל / ILS).
3. **אמתו את האתר** — Google Merchant Center מבקש אימות דרך
   **Google Search Console** (הדרך הקלה): הוסיפו את הדומיין ב-Search
   Console → ואז מקשרים בין החשבונות. (חובה ש-`SITE_URL` ב-`.env`
   יהיה הדומיין האמיתי, לא localhost.)
4. **הוסיפו את המזין**: מוצרים → **מזינים (Feeds)** → **Create feed**
   → סוג "רכישה חופשית / Free listings" → הדביקו את כתובת ה-feed
   `https://<האתר>/feed/google-shopping.xml`. גוגל יסרוק ויטען את
   המוצרים (לרוב תוך מספר שעות).
5. **בדקו את האבחון**: מוצרים → **כל המוצרים** → אם יש אזהרות (למשל
   תמונה קטנה/מחיר חסר), מתקנים — הדירוג בכרטיסיית הקניות תלוי באיכות
   הנתונים.
6. **רשימות חינמיות**: הגדרות → **רשימות חינמיות (Free listings)** →
   הפעילו. המוצרים יתחילו להופיע בחינם בכרטיסיית הקניות.

> ⚠️ הדירוג הגבוה בגוגל לא מובטח ע"י הקוד בלבד — הוא תלוי גם באיכות
> התמונות, תחרותיות המחירים, מהירות האתר, וכמובן תנועה/קישורים נכנסים.
> ה-feed והנתונים המובנים הם התשתית שמאפשרת להופיע שם בכלל.

## אבטחה — מה בפועל מוגן וברמה מה (בכנות, בלי הבטחות מוגזמות)

**מה כבר מיושם:**
- **Rate limiting** (slowapi) על כל endpoint רגיש: login (10/דקה), signup
  (5/דקה), chat (15/דקה — מגן על quota של Gemini), newsletter (5/דקה),
  `/go/` ו-`/api/price-war` (מונע הצפת בוטים). משותף בין workers דרך Redis
  אם מוגדר — בלי Redis, כל worker סופר בנפרד (עדיין עובד, פחות מדויק).
- **הגנה מפני brute-force בהתחברות** — נעילה זמנית (15 דקות) אחרי 5
  ניסיונות כושלים לאותו IP+אימייל.
- **CSRF tokens** על טפסי login/signup (חתומים, תוקף שעה).
- **Security headers**: X-Frame-Options, X-Content-Type-Options,
  Strict-Transport-Security, Referrer-Policy, Permissions-Policy.
- **סיסמאות מוצפנות** עם bcrypt (לא טקסט גלוי, לא הצפנה הפיכה).
- **הגנה מפני SQL Injection** — כל שאילתה עוברת דרך SQLAlchemy ORM עם
  parameterized queries; אין שום מקום שבו קלט משתמש נכנס ל-SQL כטקסט גולמי.
- **זיהוי בוטים בסיסי** (`fraud_service.py`) — לפי User-Agent וקצב בקשות.

**מה זה *לא* מכסה — ותצטרכו להוסיף אם התנועה תגדל:**
- **DDoS אמיתי** — rate limiting ברמת האפליקציה לא עוצר מתקפת DDoS
  מבוזרת רצינית. הפתרון הסטנדרטי: להעביר את הדומיין דרך
  [Cloudflare](https://www.cloudflare.com) (יש free tier מלא) לפני
  ה-Render/שרת שלכם — זה שכבת ההגנה הראשונה שכל אתר רציני משתמש בה, ולא
  משהו שאפליקציית Python אמורה לפתור בעצמה.
  - CAPTCHA (hCaptcha/Turnstile) על טפסי הרשמה/התחברות אם מתחילים לראות ספאם.

## סקאלה — עשרות אלפי משתמשים בו-זמנית

**מה כבר בנוי כדי לתמוך בזה:**
- **Gunicorn עם מספר workers** (`Dockerfile`) — לא Uvicorn יחיד. ברירת
  מחדל 4 workers, מתכוונן דרך `WEB_CONCURRENCY`.
- **Connection pooling מוגדר** ל-DB (`pool_size=5, max_overflow=7` לכל
  worker) — עם מגבלת Neon free tier (~100 חיבורים) זה מאפשר סדר גודל של
  10-12 workers בלי לחרוג.
- **Caching עם Redis** (`cache_service.py`) — sitemap ונתונים שלא
  משתנים לעיתים קרובות נשמרים ב-cache במקום לפגוע ב-DB בכל בקשה. נופל
  בחזרה ל-cache בזיכרון תהליך יחיד אם Redis לא מוגדר.
- **פאג'ינציה בעמוד הבית ובחיפוש** — לא טוענים את כל הקטלוג בבת אחת.
- **אינדקסים על כל שדה שמסונן/ממוין** במודלים (category, price, rating וכו').

**חשוב לדעת:** ה-free tier של Render (512MB RAM, 0.1 CPU) לא יחזיק
"עשרות אלפי משתמשים בו-זמנית" — זו מגבלת חומרה, לא קוד. הארכיטקטורה
תומכת בסקאלה אופקית (עוד instances) ואנכית (יותר RAM/CPU) ברגע שתעברו
לתוכנית בתשלום ($7-25/חודש ב-Render, למשל) — שם ה-Gunicorn workers,
ה-connection pooling וה-Redis caching שכבר בנויים יתחילו לתת את הערך
האמיתי שלהם. בלעדיהם עדיין יעבוד, פשוט לא ינצל את החומרה הנוספת.

## הרצה מקומית

```bash
cp .env.example .env   # מלאו GOOGLE_API_KEY לפחות כדי שה-AI יעבוד
docker compose up --build
python scripts/init_db.py   # פעם ראשונה בלבד, ליצירת הטבלאות
```

האתר יעלה על http://localhost:8000, ולוח הניהול על http://localhost:8000/admin
(שם משתמש `admin`, סיסמה = הערך של `ADMIN_SECRET_KEY` שהגדרתם ב-`.env`).

---

## 🚀 מדריך מלא: מהתחלה ועד עלייה לאוויר בחינם

זה סדר הפעולות המדויק, שלב-אחר-שלב. כל שלב לוקח כמה דקות; הכל בחינם.

### שלב 0 — הכנה (כ-10 דקות)

1. פתחו חשבון ב-[GitHub](https://github.com) אם אין לכם.
2. הורידו/חלצו את הפרויקט הזה למחשב, ופתחו טרמינל בתוך התיקייה.
3. צרו repo חדש וריק ב-GitHub (בלי README/gitignore), ואז:
   ```bash
   git init
   git add .
   git commit -m "DealBursa - initial commit"
   git branch -M main
   git remote add origin https://github.com/<your-username>/<repo-name>.git
   git push -u origin main
   ```

### שלב 1 — מפתח AI (חובה, בחינם)

1. גשו ל-[Google AI Studio](https://aistudio.google.com/app/apikey).
2. התחברו עם Gmail, לחצו "Create API key" — יש רמה חינמית נדיבה של Gemini.
3. שמרו את המפתח בצד, זה `GOOGLE_API_KEY`.

### שלב 2 — מסד נתונים חינמי שלא נמחק (Neon.tech)

הסיבה שלא משתמשים ב-SQLite בפרודקשן: ברוב אירוח החינמי הדיסק **זמני** —
כל redeploy מוחק את קובץ ה-SQLite. Neon נותן Postgres מנוהל, חינמי, קבוע.

1. גשו ל-[neon.tech](https://neon.tech), הרשמו חינם (יש free tier קבוע, לא ניסיון).
2. צרו Project חדש → תבחרו region קרוב (Europe).
3. מהדשבורד, העתיקו את ה-**Connection String** (מתחיל ב-`postgresql://...`).
4. זה יהיה `DATABASE_URL` שלכם.

### שלב 3 — חיבור לספקים (אפשר להתחיל רק עם eBay, ולהוסיף בהמשך)

| ספק | קישור הרשמה | מה מקבלים |
|---|---|---|
| eBay (הכי קל, התחילו כאן) | [developer.ebay.com](https://developer.ebay.com) → Register → Create keyset | `EBAY_APP_ID` + `EBAY_CERT_ID` |
| AliExpress | [portals.aliexpress.com](https://portals.aliexpress.com) → Affiliate Program | `ALIEXPRESS_APP_KEY` / `SECRET` / `TRACKING_ID` |
| Amazon | [affiliate-program.amazon.com](https://affiliate-program.amazon.com) → Associates | `AMAZON_PARTNER_TAG` (PA-API רק אחרי 3 מכירות) |
| Temu | אין API — דלגו | — |

אפשר גם לדלג על כל השלב הזה בהתחלה ולהריץ רק במצב סקרייפינג — האתר יעבוד,
פשוט עם דיוק/יציבות נמוכים יותר עד שתחברו API רשמי.

### שלב 4 — פריסה חינמית ב-Render.com

יש שתי דרכים — בחרו אחת:

**אופציה מהירה (מומלץ להתחלה) — Blueprint אוטומטי:**
1. גשו ל-[render.com](https://render.com), הרשמו עם GitHub.
2. **New +** → **Blueprint** → בחרו את הריפו שלכם. Render יזהה אוטומטית
   את `render.yaml` שכבר קיים בפרויקט (שירות Python קליל, בלי Docker/
   Playwright — הכי מתאים ל-free tier).
3. מלאו את המשתנים המבוקשים (`DATABASE_URL` מ-Neon, `GOOGLE_API_KEY`,
   `SITE_URL`) — `ADMIN_SECRET_KEY` ו-`SESSION_SECRET_KEY` נוצרים אוטומטית עבורכם (השני נדרש כדי שהתחברות משתמשים/אזור אישי תעבוד).
4. **Apply**. שימו לב: באופציה הזו Temu וה-fallback לסקרייפינג לא יעבדו
   (אין Playwright מותקן) — AliExpress ו-eBay עם API רשמי כן יעבדו במלואם.

**אופציה מלאה — Docker (כולל Playwright, גם Temu/סקרייפינג עובדים):**
1. **New +** → **Web Service** → בחרו את הריפו שלכם.
2. **Runtime**: Docker (Render יזהה את ה-Dockerfile אוטומטית) · **Instance Type**: Free.
3. הוסיפו תחת **Environment** את אותם משתנים כמו למעלה.
4. **Create Web Service**. הבנייה הראשונה לוקחת כ-5-10 דקות (מתקינה גם
   Chromium) — ייתכן שזה ידחק בזיכרון של 512MB בפעולות scraping כבדות;
   שקלו לעבור ל-Starter ($7/חודש) אם רואים קריסות זיכרון.

**בשתי האופציות אין צורך להריץ שום סקריפט ידני** — הטבלאות נוצרות
אוטומטית באתחול הראשון של השרת (`_create_tables_if_missing` ב-`main.py`).

האתר שלכם באוויר! בכתובת `https://<your-app>.onrender.com`.

### שלב 5 — הפעלת האיסוף האוטומטי (חינם, בלי שרת נפרד)

ב-Render, ה-**Free tier תומך רק בשירות Web אחד** — אין שם "Background Worker"
חינמי שיריץ את `scheduler.py` ברציפות. הפתרון החינמי: להשתמש בשירות cron
חיצוני שמפעיל את ה-endpoint הקיים `/admin/run-discovery` כל כמה שעות:

1. גשו ל-[cron-job.org](https://cron-job.org) (חינמי לגמרי), הרשמו.
2. צרו Cron Job ראשון (איסוף מוצרים חדשים):
   - URL: `https://<your-app>.onrender.com/admin/run-discovery`
   - Method: `POST`
   - Authentication: Basic Auth — username `admin`, password = `ADMIN_SECRET_KEY` שלכם
   - תזמון: כל 2-3 שעות
3. צרו Cron Job שני (היסטוריית מחיר + בדיקת התראות):
   - URL: `https://<your-app>.onrender.com/admin/run-price-monitor`
   - Method: `POST`, Authentication: אותו Basic Auth כמו למעלה
   - תזמון: כל 6-12 שעות
4. שמרו. זהו — עכשיו האתר "מתמלא לבד" במוצרים חדשים, ההיסטוריית מחיר
   נבנית לבד, והתראות מחיר מופעלות לבד — בלי לשלם על שרת נוסף.

**הערה על מגבלת Free tier**: שירות Web חינמי ב-Render "נרדם" אחרי 15 דקות
ללא תנועה, וקם מחדש תוך כ-30-50 שניות בבקשה הבאה. זה אומר שקריאת ה-cron
הראשונה אחרי תקופת שקט תהיה איטית — זה תקין ולא באג. אם תרצו ביצועים
עקביים לגמרי, שדרוג ל-Starter Plan (כ-$7/חודש) מבטל את ה"הרדמות".

### שלב 6 — בדיקה שהכל עובד

1. היכנסו ל-`https://<your-app>.onrender.com/admin` (עם admin + הסיסמה).
2. לחצו "הפעל איסוף מוצרים עכשיו" ותנו לזה כמה דקות.
3. חזרו לעמוד הבית — אמורים להופיע מוצרים חדשים עם תוכן שנוצר ע"י המערכת.

### סיכום עלויות (הכל בחינם בשלב זה)

| שירות | תוכנית | עלות |
|---|---|---|
| Render Web Service | Free | ₪0 |
| Neon Postgres | Free tier | ₪0 |
| Google Gemini API | Free tier | ₪0 |
| cron-job.org | Free | ₪0 |
| eBay/AliExpress/Amazon Affiliate | הרשמה | ₪0 (מרוויחים עמלות) |

כשהתנועה תגדל ותרצו יציבות/מהירות גבוהה יותר, השדרוג הטבעי הבא הוא Render
Starter (~$7/חודש) או VPS קטן (Hetzner/DigitalOcean, ~$5-6/חודש) עם
docker-compose המצורף — זהה קונספטואלית, פשוט בלי הגבלות ה-Free tier.

## תיקוני באגים אמיתיים מהקוד המקורי (כבר בוצעו כאן)

- הוסר path קשיח ל-Chrome של Windows ב-scraper וב-fulfillment agent — עכשיו
  משתמשים ב-Chromium המובנה של Playwright, עובד זהה בכל מערכת הפעלה/Docker.
- הוסר `@lru_cache` על מתודות instance (memory leak אמיתי — מחזיק רפרנס
  ל-`self` לנצח) והוחלף ב-cache פר-instance.
- תוקן באג ב-`recommender.py` שבו dict פייתון הודבק כטקסט חופשי בתוך prompt
  (לא JSON תקין), מה שגרם ל-`json.loads` להיכשל בפועל תמיד.
- הוסרו מספרי "הוכחה חברתית" מזויפים (`random.randint` על צפיות/רכישות) —
  הכל מבוסס נתונים אמיתיים מ-`ClickLog`/`AffiliateClick` בלבד.
- קופונים: הסוכן השיווקי כבר לא ממציא קודי הנחה שלא קיימים בפועל.
- `Base.metadata.create_all()` הוסר מ-import side-effect של models.py; יש
  סקריפט ייעודי (`scripts/init_db.py`) — קריטי ברגע שמוסיפים Alembic.

## מה עוד שווה להוסיף (סדר עדיפויות מוצע)

1. Alembic migrations (כרגע `init_db.py` פשוט ל-dev)
2. ~~Deduping חכם יותר בין ספקים~~ ✅ **בוצע** — `app/services/product_matcher.py`: אותו מוצר פיזי שנמכר גם ב-AliExpress וגם ב-Temu כבר לא הופך לשני מוצרים חיים. מנוע discovery מזהה התאמה לפי שם+מחיר (token normalization + Jaccard/containment + פער מחיר סביר) וממזג את ההצעה לתוך המוצר הקיים (`offers[]` + `affiliate_links{}`) במקום ליצור כפילות שמפצלת קליקים. אותו שער התאמה בדיוק מסנן עכשיו גם את גרף "מלחמת המחירים" — רק ליסטינגים שעברו את מבחן הדמיון מוצגים, מסודרים לפי עוצמת ההתאמה (במקום כל תוצאה שהאדפטר החזיר).
3. A/B testing framework אמיתי לכותרות/תמונות (יש שדות DB מוכנים: `description_b`, `sales_count_a/b`)

---

## ✅ Deploy Checklist — לפני שעולים לאוויר (כ-5 דקות)

סימון מהיר לפני הלחיצה על **Deploy** (או מיד אחריה) — מבטיח שאף חלק חיוני לא נשכח:

**חובה (בלעדיהם האתר לא שלם):**
- [ ] `SITE_URL` = הדומיין האמיתי (לא yourdomain.com / localhost) — נדרש לקישורי עמלה, אימיילים, OAuth ו-SEO
- [ ] `DATABASE_URL` = Postgres מנוהל (Neon/Supabase חינמי) — SQLite נמחק בכל redeploy
- [ ] `GOOGLE_API_KEY` = מפתח Gemini (AI Studio, חינם)
- [ ] `ADMIN_EMAIL` מוגדר + `ADMIN_SECRET_KEY` הוחלף מהברירת מחדל `12345` (דף ההגדרות או ה-.env)
- [ ] `SESSION_SECRET_KEY` = מפתח ארוך ואקראי (לא 12345!) — אחרת הסשנים ניתנים לזיוף
- [ ] `SMTP_HOST` + `SMTP_USER` + `SMTP_PASSWORD` — אימייל אימות וניוזלטר

**ספקים (מומלץ להתחיל ב-eBay + AliExpress):**
- [ ] `EBAY_APP_ID` + `EBAY_CERT_ID` — developer.ebay.com → Create keyset
- [ ] `ALIEXPRESS_APP_KEY` + `APP_SECRET` + `TRACKING_ID` — portals.aliexpress.com (אישור 1-3 ימים)
- [ ] אופציונלי: Amazon / Awin / CJ / Rakuten / Temu (`TEMU_AFFILIATE_ID` בלבד, scraping)

**חיבור עם Google + אינסטגרם (אם רוצים):**
- [ ] ב-Google Cloud Console: Authorized redirect URI = `<SITE_URL>/auth/google/callback`
- [ ] `GOOGLE_OAUTH_CLIENT_ID` + `CLIENT_SECRET` בקובץ / בדף ההגדרות
- [ ] `INSTAGRAM_ACCESS_TOKEN` (long-lived) + `INSTAGRAM_ACCOUNT_ID`

**אחרי שעולים לאוויר:**
- [ ] בדיקת בריאות: `https://<your-app>.onrender.com/healthz` → `{"status":"ok"}`
- [ ] בדיקת `/admin` — כניסה עם `ADMIN_EMAIL` + הסיסמה החדשה
- [ ] cron-job.org: `POST /admin/run-discovery` כל 2-3 שעות + `POST /admin/run-price-monitor` כל 6-12 שעות (Basic auth: `admin` + `ADMIN_SECRET_KEY`)
- [ ] Google Search Console: הוסיפו את ה-sitemap (`/sitemap.xml`) ואת ה-feed (`/feed/google-shopping.xml`) לקניות של Google
- [ ] וידוא עמודי חוקיות: פרטיות, תנאים, גילוי אפילייט (כבר קיימים — בדקו שמולאו)
- [ ] הפעלת הסוויטה: `pytest` — 376 בדיקות צריכות לעבור לפני ואחרי
