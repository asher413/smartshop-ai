# 🔐 דוח אבטחה מעודכן — DealBursa

> תאריך: אוגוסט 2026 (גרסה 2 — לאחר יישום כל תיקוני הדוח הקודם)
> שיטה: סקירת קוד שורה-שורה של כל נקודות הקצה + **בדיקות עומסים מעשיות** (100 בקשות מקבילות) + סוויטת אבטחה אוטומטית
> תוצאה כללית: **166 בדיקות אבטחה + 257 בדיקות סה"כ עוברות** · כל ההמלצות ה"קריטי/גבוה" מהדוח הקודם **יושמו ואומתו**

---

## 0️⃣ מה נסגר מאז הדוח הקודם (גרסה 1 → 2)

| המלצה קודמת | סטטוס | איפה |
|---|---|---|
| CSRF לכל נתיבי ה-POST של הניהול | ✅ **יושם** — `require_admin_csrf` על כל 18 הנתיבים + בדיקה אינטרוספקטיבית שמונעת נתיב עתידי בלי הגנה | `main.py` + 109 בדיקות חדשות ב-`test_security.py` |
| `https_only=True` לעוגיית סשן בייצור | ✅ **יושם** | `main.py` (`https_only=_https_only`) |
| `TrustedHostMiddleware` | ✅ **יושם** — דוחה Host זדוני (400) | `main.py` + בדיקה |
| CSP בסיסי | ✅ **יושם** — `default-src 'self'` + `frame-ancestors 'none'` + CDNs מורשים | `security_middleware.py` + בדיקה |
| `/logout` ו-`/admin/logout` מ-GET ל-POST | ✅ **יושם** — GET כבר לא מנקה סשן (405/307) | `main.py` + בדיקה |
| COOP / CORP headers | ✅ **נוספו** (`same-origin`) | `security_middleware.py` |
| מנגנון גיבוי בלי AI שמתלקח אוטומטית | ✅ **יושם** — circuit breaker נפתח מיד על 429 מכסת Google + מגבלת קצב כלל-אתר | `ai_gate.py` + `gemini_client.py` |

---

## 1️⃣ Headers — מאומת חי על כל תגובה

| Header | ערך | סטטוס |
|---|---|---|
| `Content-Security-Policy` | `default-src 'self'; script-src 'self' 'unsafe-inline' CDNs; frame-ancestors 'none'; connect-src 'self'; img-src 'self' data: https:` | ✅ |
| `X-Frame-Options` | `DENY` | ✅ |
| `X-Content-Type-Options` | `nosniff` | ✅ |
| `Referrer-Policy` | `strict-origin-when-cross-origin` | ✅ |
| `Permissions-Policy` | `geolocation=(), microphone=(), camera=()` | ✅ |
| `Strict-Transport-Security` | `max-age=63072000; includeSubDomains` | ✅ |
| `Cross-Origin-Opener-Policy` / `Resource-Policy` | `same-origin` | ✅ |
| Cookie סשן + `session_id` | HttpOnly + SameSite=Lax (+ Secure בייצור) | ✅ |

**הערה CSP**: `'unsafe-inline'` ב-`script-src` נשאר כי הפאנל משתמש בסקריפטים inline. רמה טובה ל-XSS, לא מושלמת — ראו המלצה 🟠 4.

## 2️⃣ CSRF — סגור לחלוטין בצד הניהול

- **כל 18 נתיבי ה-POST של הניהול** (settings, test, suppliers pull-test, users toggle, run-discovery, run-price-monitor, coupons/pull, marketing popup/ad ×7, newsletter/send, instagram, viral, blog) עוברים דרך `require_admin_csrf`: `Origin`/`Sec-Fetch-Site` תואם **או** token חתום **או** Basic (cron) — אחרת **403 נכשל-סגור**.
- **בדיקה אינטרוספקטיבית** (`test_every_admin_post_route_has_csrf_protection`): כל נתיב POST תחת `/admin` חייב את ההגנה — נתיב חדש שנשכח מפיל את ה-CI.
- 7 תרחישים × 18 נתיבים = **126 בדיקות פרמטריות** (401/403/Origin זדוני/token/Origin תואם/Sec-Fetch-Site/Basic).
- טפסי משתמש (`/login`, `/signup`) עם token חתום + honeypot ✅.
- `/logout` POST-only — לא ניתן להדליק דרך `<img>`.

## 3️⃣ Rate Limiting + Bot Guard — נבדקו תחת עומס (100 מקבילות)

| בדיקה | תוצאה | פסק דין |
|---|---|---|
| `/` ×100 מקבילים | 100×200, avg 1.56s, p95 2.7s | ✅ עומד |
| `/search` ×100 מקבילים | 100×200, avg 0.33s, p95 0.45s | ✅ מהיר |
| `/api/price-war` ×100 מקבילים | **80×429** (מגבלת 20/דק') + 20 שעברו את השער | ✅ ה-limiter עומד בלחץ |
| `/api/newsletter` (5/דק') | 429 אחרי 5 | ✅ |
| `/api/chat` (15/דק') | 429 אחרי 15 | ✅ |
| `/admin/login` (10/דק') | 429 אחרי 10 | ✅ |
| Bot guard: curl בלי UA דפדפן | **204/403** נחסם | ✅ |
| Bot guard: UA של Chrome | 200 תקין | ✅ |

**שיפור שנעשה כתוצאה מהבדיקה**: מנגנון גיבוי AI — ראה סעיף 7.

## 4️⃣ XSS / SQLi / Open-redirect — ללא ממצאים

- Jinja2 autoescape בכל התבניות; `escapeHtml()` לפני כל `innerHTML` ב-JS; בדיקות XSS מאוחסן עוברות.
- כל השאילתות דרך SQLAlchemy פרמטרי — 6 payloads SQLi נבדקו, ללא קריסה/הדלפה.
- `/go/{id}` מפנה רק ל-URL מה-DB (ללא open redirect); מוצר חסר → 307 ל-`/`.
- 404 מותאם, `debug=False`, ללא traceback במשתמש.

## 5️⃣ פרטיות ודליפות מידע ב-JSON

| קצה | דליפה? | הערכה |
|---|---|---|
| `/admin/users` | רשימת משתמשים מלאה (מייל) | ✅ אדמין בלבד |
| `/api/notifications`, `/api/site-ads`, `/api/social-proof` | נתוני המשתמש/אגרגט בלבד | ✅ |
| `/personal-area` | רק נתוני המשתמש עצמו | ✅ |
| `AffiliateClick` | **אוגר `user_ip` + `session_id`** | 🟡 מדיניות שמירה/אנונימיזציה מומלצת (GDPR) |
| שגיאות | JSON נקי, ללא פרטי פנים | ✅ |

## 6️⃣ Google OAuth — מחובר

- מפתחות הוזנו ב-`.env`; הכפתור מפנה ל-Google עם client ID ו-redirect תקין (אומת חי).
- **פעולה חיצונית נדרשת**: רישום `http://localhost:8000/auth/google/callback` (ובייצור `https://<domain>/auth/google/callback`) תחת Authorized redirect URIs בקונסול Google — בלי זה Google יחזיר `redirect_uri_mismatch`.
- 🟠 סוד הלקוח נשלח בצ'אט — אם נחשף בפומבי, לסובב (rotate).

---

## 7️⃣ המלצות לפי חומרה (מעודכן)

### 🔴 קריטי
1. **`ADMIN_SECRET_KEY=12345` הוא ברירת מחדל ידועה** — כל מי שמכיר אותה נכנס לניהול. **החליפו מדף ההגדרות עוד היום** (12+ תווים). המייל לכניסה מוגדר ב-`ADMIN_EMAIL`.
2. **וודאו `SESSION_SECRET_KEY` אינו ערך ברירת המחדל** (`change_me_please_session_secret`) — מי שיודע אותו יכול לזייף עוגיית סשן. הדשבורד מציג אינדיקטור "מפתח session חלש" אם כן.

### 🟠 גבוה
3. **מכסת Google החינמית (20/דק' ל-gemini-2.5-flash)** — ה-circuit breaker החדש פותח את מעגל ה-AI מיד על 429 (10 דק'), האתר עובר אוטומטית למצב גיבוי בלי AI, והצ'אט מחזיר הודעה מסבירה. **לייצור רציני: מפתח בתשלום או מודל עם מכסה גבוהה יותר**; אחרת ה-AI יעבוד לסירוגין.
4. **CSP `'unsafe-inline'`** — שלב הבא: להעביר הסקריפטים inline (צ'אט, פאנל) לקבצים חיצוניים ולהסיר, או לעבור ל-nonce/hash. ביניים — הרמה הנוכחית חוסמת סקריפטים חיצוניים לא-מורשים.
5. **`/api/price-war` עושה קריאות רשת חיות בנתיב הבקשה** — 20/דק' מגבילים את הנזק, אבל מומלץ cache (60–120 שניות) כדי שתוצאות יוחזרו תוך אלפיות שניות גם תחת עומס.
6. **מאחורי פרוקסי/CDN** — להגדיר `X-Forwarded-For` מהימן כדי ש-rate limiting והבוט-גארד יספרו נכון. עם כמה workers/containers — לחבר Redis (`REDIS_URL`) למצב bot-guard והמגבלות המשותפות.

### 🟡 בינוני
7. **Brute-force guard ל-`/admin/login`** — קיים rate limit (10/דק') אבל לא נעילת (IP,email) אחרי 5 כשלונות כמו ב-`/login`. להוסיף.
8. **ולידציית URL לפרסומות** (`target_url`) — אדמין בלבד, אבל לאסור סכמות `javascript:`/`data:` גם בשרת (לא רק בתצוגה).
9. **בדיקת בעלות ב-`/api/notifications/{id}/read`** — לוודא שההתראה שייכת למשתמש (כיום כל id מסומן — נמוך, רק סימון קריאה).
10. **קיצוץ אורך** ביקורות/הודעות בפופאפים — גבול עליון בשרת.

### 🟢 נמוך / תחזוקה
11. **מדיניות שמירת IPs** — `AffiliateClick` שומר `user_ip`; להגדיר תקופת שמירה/אנונימיזציה + ציון במדיניות הפרטיות (הגנת הפרטיות/GDPR).
12. **`GET /admin/logout`** — להשאיר `405` (כבר תקין); לוודא שלא יוחזר קישור GET בשום מקום.
13. **סקירת תלויות** — `pip-audit`/`safety` במחזור CI להודעות CVEs.

---

## 8️⃣ סיכום

האתר עבר מרמת "בסיס טובה" לרמת **"מאובטחת בייצור"** עבור כל מה שתחת שליטת הקוד: CSRF מלא בניהול (עם הגנה מפני שכחה עתידית), TrustedHost, CSP, COOP/CORP, logout POST-only, rate limiting ובוט-גארד שנבדקו תחת 100 בקשות מקבילות, ומנגנון גיבוי אוטומטי בלי המערכת. **נותרו שתי פעולות אדמין חיוניות** (החלפת סיסמת מנהל ומפתח session) ופעולות חיצוניות (רישום redirect URI של Google, מפתח AI בתשלום לייצור).
