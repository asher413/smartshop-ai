# 🛒 מדריך מלא: חיבור ספקים עם עמלות — שלב אחרי שלב

כל המדריך הזה עוקב **בדיוק** אחרי מה שהקוד מצפה לו. לכל ספק יש:
- מאיפה מקבלים כל מפתח
- איזה שדה להכניס ל-`.env` / דף ההגדרות
- איך לבדוק שהחיבור באמת עובד (כפתור "בדוק חיבור" בלוח הניהול)

> 💡 **הדרך המהירה ביותר להתחיל:** eBay (אישור תוך שעות) → AliExpress (יום-יומיים)
> → אחר כך Awin ו-CJ, שנותנות **אלפי סוחרים** דרך אינטגרציה אחת.

---

## 0. איפה מכניסים את המפתחות

| דרך | מתי |
|---|---|
| **דף ההגדרות בניהול** (`/admin/settings`) | מומלץ — אין צורך לערוך קבצים, יש כפתור "בדוק חיבור" חי לכל שירות |
| **קובץ `.env`** | דרך קבצים, ואז הפעלה מחדש של השרת |

בדף ההגדרות, אחרי שמירת ערכים חדשים, האתר מחיל אותם **מיידית בלי restart** —
ואפשר ללחוץ "בדוק חיבור" על כל שירות כדי לראות ✅/❌ עם הודעת שגיאה אמיתית.

---

## 1. eBay — הכי קל ומהיר (2 הרשמות, כ-30 דקות)

האתר משתמש ב-**eBay Browse API** (OAuth2, client-credentials) כדי לחפש מוצרים,
ועוטף קישורים עם פרמטרי ה-**eBay Partner Network (ePN)** לעמלות.

### א. תוכנית השותפים — מקבלים את Campaign ID (מספר קמפיין)
1. צרו חשבון eBay רגיל ב-[ebay.com](https://www.ebay.com) (אם אין לכם).
2. היכנסו ל-[epn.ebay.com](https://epn.ebay.com) (eBay Partner Network) → **Join the Program**.
3. מלאו את הבקשה (שם, אתר/פלטפורמה, מדינה). אישור בדרך כלל מהיר.
4. אחרי האישור, בדשבורד ePN תמצאו את **Campaign ID** (מספר) — זה
   `EBAY_CAMPAIGN_ID`.
   > אם עוד לא אושרתם ל-ePN, אפשר להתחיל עם ה-API בלי זה — המוצרים יימשכו,
   > פשוט בלי עמלה עד שיתווסף ה-Campaign ID.

### ב. חשבון מפתחים — מקבלים את ה-API Keys
1. הירשמו ב-[developer.ebay.com](https://developer.ebay.com) → **Register** (חינם, מיידי).
2. **Dashboard → My Applications → Create a keyset**.
3. מקבלים שתי מחרוזות:
   - **App ID** = client id → `EBAY_APP_ID`
   - **Cert ID** = client secret → `EBAY_CERT_ID`
4. **חובה לבקש גישת Production** כדי שה-API יעבוד מחוץ ל-sandbox:
   בתוך ה-keyset לחצו **"Request production access"** → בחרו **Buy APIs → Browse API**
   → מלאו טופס קצר (תיאור השימוש). אישור לרוב תוך שעות-ימים.
5. ה-**scope** שהקוד מבקש (אוטומטית, אין צורך בהתערבות):
   `https://api.ebay.com/oauth/api_scope` — Grant type: `client_credentials`
   (אין צורך ב-user consent — ה-API לא קורא נתוני משתמשים).

### ג. מכניסים ומאמתים
```env
EBAY_APP_ID=<App ID>
EBAY_CERT_ID=<Cert ID>
EBAY_CAMPAIGN_ID=<Campaign ID מ-ePN>
```
בדף ההגדרות → "בדוק חיבור" (eBay) — בודק token חי מול `api.ebay.com`.

> ⚠️ ה-Finding API הישן (`svcs.ebay.com`) מושבת כבר שנים — הקוד משתמש ב-**Browse API**
> החדש (`api.ebay.com/buy/browse/v1/item_summary/search`).

---

## 2. AliExpress — תוכנית שותפים (1–3 ימי אישור)

האתר משתמש ב-**AliExpress Affiliate Open API** (AliOpen) — gateway
`api-sg.aliexpress.com/sync` עם חתימת MD5 (כמו שהקוד מיישם).

### שלבים
1. צרו חשבון AliExpress רגיל ב-[aliexpress.com](https://www.aliexpress.com).
2. היכנסו ל-[portals.aliexpress.com](https://portals.aliexpress.com) → בחרו
   **Affiliate Program** → **Apply** → מלאו את הטופס (תיאור האתר והקהל שלכם).
   אישור אוטומטי או ידני תוך 1–3 ימים. (אתר פעיל עם תוכן מקורי עוזר לאישור.)
3. אחרי האישור, בדשבורד השותפים → **API & Data (AliOpen)** → **Create App**:
   מקבלים **App Key** + **App Secret**.
4. **Tracking ID**: בדשבורד → הגדרות מעקב (Tracking Settings) → צרו Tracking ID
   (או "default"). זה מה שמזהה אתכם בעמלות — **חובה בכל קריאה**.
5. **הרשאות API**: תחת API & Data יש רשימת ממשקים — לחצו **Apply/Enable** על:
   - `aliexpress.affiliate.hotproduct.query` — חיפוש מוצרים חמים (משיכת מוצרים)
   - `aliexpress.affiliate.productdetail.get` — פרטי מוצר (השוואת מחירים)
   - `aliexpress.affiliate.link.generate` — יצירת קישורי עמלה
   - `aliexpress.affiliate.coupon.query` — משיכת קופונים
   (לחלקם ייתכן צורך באישור נפרד — עשו זאת ושמרו.)

### מכניסים ומאמתים
```env
ALIEXPRESS_APP_KEY=<App Key>
ALIEXPRESS_APP_SECRET=<App Secret>
ALIEXPRESS_TRACKING_ID=<Tracking ID>
```
בדף ההגדרות → "בדוק חיבור" (AliExpress) — מבצע קריאת API אמיתית
(`hotproduct.query` עם page_size=1) ומדווח אם המפתחות תקינים.

---

## 3. Amazon — מתחילים עם Partner Tag בלבד

- הרשמה: [affiliate-program.amazon.com](https://affiliate-program.amazon.com) → **Associates**.
- מייד מקבלים **Partner Tag** → `AMAZON_PARTNER_TAG` — אפשר כבר להציג קישורי
  Amazon עם עמלה (הקוד עוטף אותם).
- **PA-API 5.0** (חיפוש מוצרים דרך API) נפתח רק אחרי **3 מכירות מאושרות
  בתוך 180 יום** — עד אז מכניסים את ה-Tag בלבד, והאתר עדיין עובד (לינקים +
  סקרייפינג מתון).
- אחרי פתיחת ה-API: מוסיפים `AMAZON_PAAPI_ACCESS_KEY` + `AMAZON_PAAPI_SECRET_KEY`.
- בדף ההגדרות יש כפתור "בדוק חיבור" (Amazon) עם רמזי שגיאה מפורטים.

---

## 4. Awin — הרשת שמוסיפה לכם אלפי סוחרים בבת אחת ⭐

רשת שותפים (לא חנות אחת): **אינטגרציה אחת = גישה לאלפי סוחרים**
(Shein, מותגי אלקטרוניקה, קמעונאים אזוריים ועוד).

1. הרשמה: [awin.com](https://www.awin.com) → **Join as Publisher**.
   (פיקדון החזר של ~1€ לאימות זהות — מוחזר בהקצאה הראשונה.)
2. אחרי אישור החשבון: **Toolbox → API Credentials** → מקבלים את ה-**API Token**.
3. **Publisher ID** — מופיע בדשבורד (מספר).
4. מכניסים:
   ```env
   AWIN_API_TOKEN=<API Token>
   AWIN_PUBLISHER_ID=<Publisher ID>
   ```
5. **אשרו לתוכניות סוחרים** (Advertiser Programs) מהדשבורד — כל סוחר חדש
   שמאושרים אליו מופיע אוטומטית באתר בלי קוד חדש. כפתור "בדוק חיבור" (Awin)
   בודק את הטוקן מול רשימת ה-feeds.

---

## 5. CJ Affiliate — רשת שנייה (Walmart, Wayfair ועוד)

1. הרשמה: [cj.com](https://www.cj.com) → **Join CJ** (חינם).
2. אחרי אישור: **Account → API Credentials** → **API Token**.
3. **Company ID** — מההגדרות (מספר).
4. ```env
   CJ_API_TOKEN=<API Token>
   CJ_COMPANY_ID=<Company ID>
   ```
5. אשרו לתוכניות מפרסמים (Advertisers) מהדשבורד — מוצרים חדשים זורמים אוטומטית.
   כפתור "בדוק חיבור" (CJ) בודק מול Coupon API.

---

## 6. Rakuten Advertising — הרשת של Etsy ומותגים גדולים ⭐

Rakuten Advertising (לשעבר LinkShare) היא רשת השותפים שענקית המכירות
**Etsy** עברה אליה — ומתווספים אליה כל הזמן מותגים גדולים (אופנה, בית,
יופי, אלקטרוניקה). אינטגרציה אחת = גישה לאלפי סוחרים, בדיוק כמו Awin/CJ,
אבל עם **חיפוש מוצרים בזמן אמת** (Product Search API) במקום קבצי feeds.

### א. הרשמה והצטרפות
1. הירשמו ב-[rakutenadvertising.com](https://www.rakutenadvertising.com) → **Join/Publisher**.
   (אישור לרוב תוך כמה ימי עסקים; לפעמים מבקשים פרטי עסק/אתר.)
2. בדשבורד: **Advertiser Search** → חפשו סוחרים (למשל **Etsy**) → שלחו
   **בקשת הצטרפות** לתוכניות שלהם. האישור הוא ידני לסוחרים מסוימים (Etsy
   כולל), אז שלחו כמה בקשות בבת אחת.

### ב. קבלת המפתחות — Developer Portal
1. בדשבורד → **Developer Portal** (או developers.rakutenadvertising.com).
2. **API Credentials** → מקבלים:
   - **Client ID** (App Key) → `RAKUTEN_CLIENT_ID`
   - **Client Secret** (App Secret) → `RAKUTEN_CLIENT_SECRET`
3. **Site ID / Account ID** — מספר הרשת שלכם, מופיע בדשבורד או ב-API Credentials
   (זה ה-`scope` בהרשאת OAuth) → `RAKUTEN_ACCOUNT_ID`.

### ג. מכניסים ומאמתים
```env
RAKUTEN_CLIENT_ID=<Client ID>
RAKUTEN_CLIENT_SECRET=<Client Secret>
RAKUTEN_ACCOUNT_ID=<Site/Network ID>
```
בדף ההגדרות → "בדוק חיבור" (Rakuten) — מבצע OAuth token אמיתי מול
`api.linksynergy.com/token` ואז חיפוש מוצר אחד כדי לוודא שהטוקן עובד.

> 🔒 ההרשאה היא OAuth2 **client-credentials** (בלי consent של משתמש) —
> הקוד שולח `Authorization: Bearer base64(client_id:client_secret)` עם
> `scope=<account_id>` ומקבל access_token. אין צורך בהתערבות ידנית.

---

## 7. Temu — אין API ציבורי (עדיין משתלם)

- תוכנית שותפים פתוחה (עמלות 5%–20% לפי קטגוריה, אך חלון cookie של 24 שעות בלבד).
- אין API רשמי למוצרים — האתר מריץ אותו במצב סקרייפינג.
- מכניסים `TEMU_AFFILIATE_ID` כדי שהקישורים יכללו פרמטר מעקב.

---

## 8. עוד רשתות ששווה להוסיף (כשהתנועה תגדל)

| רשת | הרשמה | עמלות | הערות |
|---|---|---|---|
| **Walmart** (דרך Impact) | impact.com + walmart.com/affiliates | 1–4% | נדרש בסיס ב-ארה"ב |
| **Admitad** | admitad.com | משתנה | רשת נוספת, מכסה גם Temu |
| **Partnerize** | partnerize.com | משתנה | רשת גדולה של מותגים |
| **Shopee Affiliate** | דרכי חשבון מקומי | 2.5–12% | מוגבל גיאוגרפית (ד"א מזרח) |

> **העיקרון שמרוויח לכם הכי הרבה:** כל רשת שותפים (Awin/CJ/Rakuten) = מאות
> סוחרים עם אינטגרציה אחת. במקום להוסיף אדפטר לכל חנות, מאשרים עוד תוכניות
> בתוך הרשת — האתר כבר יודע למשוך מהן.

---

## 9. שאר המפתחות (SMTP, אינסטגרם, Google OAuth, טלגרם, 17TRACK)

### 📧 SMTP — ניוזלטר ואימיילים
| ספק | איפה | הגדרות | הערות |
|---|---|---|---|
| **Gmail** | myaccount.google.com → אבטחה → **App passwords** (חייב 2FA פעיל) | Host: `smtp.gmail.com`, Port: `587`, User: המייל, Password: סיסמת האפליקציה (16 תווים) | הכי מהיר להתחלה |
| **Brevo** (לשעבר Sendinblue) | brevo.com → SMTP & API | Host: `smtp-relay.brevo.com`, Port: `587`, User: `login`, Password: ה-SMTP Key | 300 מיילים/יום חינם |
| **Resend** | resend.com → API Keys | Host: `smtp.resend.com`, Port: `587` | מודרני, חדש |
| **SendGrid** | sendgrid.com → Settings → Sender Auth | Host: `smtp.sendgrid.net`, Port: `587`, User: `apikey` | 100 מיילים/יום חינם |

`SMTP_FROM_EMAIL` + `SMTP_FROM_NAME` = מאיפה האימייל נראה שנשלח. כפתור "בדוק חיבור" מבצע SMTP login אמיתי.

### 📸 אינסטגרם — פרסום אוטומטי
1. החשבון חייב להיות **חשבון עסקי/יוצר** (הגדרות → חשבון → החלף לחשבון מקצועי).
2. קושרים אותו לדף פייסבוק (חובה ל-Graph API).
3. [developers.facebook.com](https://developers.facebook.com) → Create App (type: Business) → מוסיפים את המוצר **Instagram Graph API**.
4. ב-**Graph API Explorer** (עם הטוקן) שולחים בקשת הרשאה ומקבלים **Access Token** קצר-מועד.
5. מאריכים ל-**long-lived token** (60 יום) ומחדשים לפי הצורך — זה `INSTAGRAM_ACCESS_TOKEN`.
6. `INSTAGRAM_ACCOUNT_ID` — ה-IG User ID של החשבון (Graph API Explorer → `GET /me/accounts` → Instagram Business Account ID).

### 🔑 Google OAuth — "המשך עם Google"
1. [console.cloud.google.com](https://console.cloud.google.com) → צרו פרויקט.
2. **APIs & Services → OAuth consent screen** → External → מלאו שם האפליקציה ומייל.
3. **Credentials → Create Credentials → OAuth Client ID** → Application type: **Web application**.
4. ב-**Authorized redirect URIs** הוסיפו: `https://האתר-שלך/auth/google/callback` (וב-localhost: `http://127.0.0.1:8000/auth/google/callback`).
5. מקבלים **Client ID** + **Client Secret** → `GOOGLE_OAUTH_CLIENT_ID` + `GOOGLE_OAUTH_CLIENT_SECRET`.

### ✈️ טלגרם — התראות
1. בוט: צ'אט עם **@BotFather** → `/newbot` → מקבלים **Bot Token** → `TELEGRAM_BOT_TOKEN`.
2. Chat ID: צ'אט עם **@userinfobot** → מספר ה-ID → `TELEGRAM_CHAT_ID`.

### 🚚 17TRACK — מעקב משלוחים
- 17track.net → הרשמה → **API Key** מהדשבורד → `SEVENTEEN_TRACK_API_KEY`.

> 💡 **כל המפתחות נכנסים באותו מקום:** דף ההגדרות בלוח הניהול (`/admin/settings`),
> או ישירות בקובץ `.env` (אז יש להפעיל את השרת מחדש). כל שדה מגיע עם כפתור
> "בדוק חיבור" שבודק את הערכים שזה עתה הוקלדו מול השירות האמיתי.

---

## 10. סדר פעולות מומלץ (סיכום)

1. **eBay** — היום (מפתחות + Production access).
2. **AliExpress** — הגישו בקשה עכשיו, מאשרים תוך יומיים.
3. **Amazon Partner Tag** — מיידי (עמלה גם בלי API).
4. **Awin + CJ + Rakuten** — אחרי שה-API של eBay/AliExpress עובד, כדי להכפיל את היצע המוצרים (Rakuten = גישה ל-Etsy ומותגים גדולים).
5. **Temu Affiliate ID** — מתי שמתאים (סקרייפינג).

אחרי כל חיבור: בלוח הניהול → סטטוס ספקים יופיע **API רשמי** ירוק במקום
"סקרייפינג בלבד", ומנוע האיסוף מתחיל למשוך מוצרים אמיתיים עם קישורי עמלה.
