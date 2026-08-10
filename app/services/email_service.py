"""
Transactional email via plain SMTP — deliberately not tied to a specific
paid provider's SDK. Any provider that offers SMTP credentials works:
Gmail (with an App Password, fine for low volume/testing), Brevo/Sendinblue
(free tier: 300 emails/day, easiest for a new project), or Resend/SendGrid
if you outgrow that. See README "Setting up email" for exact steps.

If SMTP isn't configured, send_email() logs and returns False instead of
raising — signup/login flows must never hard-fail just because email
delivery isn't set up yet (e.g. in local dev).
"""
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from app.core.config import settings

logger = logging.getLogger(__name__)


def send_email(to_email: str, subject: str, html_body: str) -> bool:
    if not settings.smtp_host or not settings.smtp_user:
        logger.info("SMTP not configured — skipping email to %s (subject: %s)", to_email, subject)
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_email}>"
    msg["To"] = to_email
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_password)
            server.sendmail(settings.smtp_from_email, [to_email], msg.as_string())
        return True
    except Exception:
        logger.exception("Failed to send email to %s", to_email)
        return False


def send_verification_email(to_email: str, verify_url: str):
    html = f"""
    <div dir="rtl" style="font-family: Arial, sans-serif; max-width: 480px; margin: 0 auto;">
        <h2 style="color:#4338ca;">אימות כתובת אימייל — SmartShop</h2>
        <p>תודה שנרשמת! לחצו על הכפתור למטה כדי לאמת את כתובת האימייל שלכם:</p>
        <a href="{verify_url}" style="display:inline-block; background:#6366f1; color:white;
           padding:12px 28px; border-radius:10px; text-decoration:none; font-weight:bold;">
           אימות כתובת אימייל
        </a>
        <p style="color:#888; font-size:12px; margin-top:24px;">
            הקישור בתוקף ל-48 שעות. אם לא נרשמתם לאתר, אפשר להתעלם מהודעה זו.
        </p>
    </div>
    """
    return send_email(to_email, "אימות כתובת אימייל — SmartShop", html)
