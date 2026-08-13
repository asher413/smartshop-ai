"""
Instagram marketing agent — posts deal content to Instagram when
credentials are configured, and always produces shareable captions.

The Instagram Graph API (Instagram Content Publishing) is the only
official way to auto-post; it requires a Facebook app + Page + access
token. That's heavy setup, so this agent degrades gracefully:
- with INSTAGRAM_ACCESS_TOKEN + INSTAGRAM_ACCOUNT_ID set -> real POST
- otherwise -> logs + returns the ready-to-post caption so the operator
  can paste it (and the UI still shows the generated content).

Never fabricates engagement metrics — captions are honest about price and
link, and posts include the affiliate disclosure line required by FTC.
"""
import logging

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)

# Graph API version — old pinned versions are retired ~2 years after release,
# so a stale version (e.g. v19.0 in 2026) silently 400s every request. Keep
# this on the newest stable version; v23.0 was current as of this writing.
GRAPH_API_VERSION = "v23.0"


class InstagramAgent:
    def __init__(self):
        self.access_token = settings.instagram_access_token
        self.account_id = settings.instagram_account_id

    @property
    def is_connected(self) -> bool:
        return bool(self.access_token and self.account_id)

    def build_caption(self, product_name: str, price: float | None, url: str, hook: str = "") -> str:
        price_line = f"מחיר: ₪{int(price)}" if price else ""
        return (
            f"{hook or '🔥 דיל חם!'}\n\n"
            f"✅ {product_name}\n"
            f"💸 {price_line}\n"
            f"👇 קישור בסטורי שלנו / בקישור שבתיאור הפרופיל\n"
            f"🔗 קישור שותפים — ייתכן שנקבל עמלה, ללא עלות נוספת עבורך\n\n"
            f"#deals #shopping #dealbursa #דילים #קניות #דילחם #salefinds"
        )

    def _api_error(self, resp: requests.Response) -> str:
        """Turn a Graph API error payload into a short Hebrew hint so the
        admin sees WHY a post failed instead of a generic message."""
        try:
            data = resp.json()
            err = data.get("error", {})
            code = err.get("code")
            msg = str(err.get("message", data))[:200]
        except Exception:
            code, msg = None, resp.text[:200]
        hints = {
            190: "הטוקן פג או לא תקין — צרו token חדש (וגם אפשר token ארוך-טווח)",
            10: "אין הרשאות פרסום — בדקו שהוספתם את המשתמש כבקר (Admin) באפליקציית הפייסבוק",
            4: "הטוקן נחסם ע\"י רייט-לימיט — נסו שוב בעוד כמה דקות",
            2207029: "הטוקן קצר-טווח — צרו long-lived token",
        }
        hint = hints.get(code, "")
        return f"({code or '?'}) {msg} {hint}".strip()

    def create_media_container(self, image_url: str, caption: str) -> tuple[str | None, str]:
        """Step 1 of the IG publishing flow: upload the image.
        Returns (container_id, error_message)."""
        if not self.is_connected:
            return None, "Instagram לא מחובר — מגדירים INSTAGRAM_ACCESS_TOKEN + INSTAGRAM_ACCOUNT_ID"
        try:
            resp = requests.post(
                f"https://graph.facebook.com/{GRAPH_API_VERSION}/{self.account_id}/media",
                data={"image_url": image_url, "caption": caption, "access_token": self.access_token},
                timeout=20,
            )
            data = resp.json()
            if "id" in data:
                return data["id"], ""
            logger.error("IG media container failed: %s", data)
            return None, self._api_error(resp)
        except requests.RequestException as e:
            logger.error("IG media request failed: %s", e)
            return None, f"שגיאת רשת בהעלאת המדיה: {e}"

    def publish(self, container_id: str) -> tuple[bool, str]:
        if not self.is_connected:
            return False, "Instagram לא מחובר"
        try:
            resp = requests.post(
                f"https://graph.facebook.com/{GRAPH_API_VERSION}/{self.account_id}/media_publish",
                data={"creation_id": container_id, "access_token": self.access_token},
                timeout=20,
            )
            data = resp.json()
            if "id" in data:
                return True, ""
            logger.error("IG publish failed: %s", data)
            return False, self._api_error(resp)
        except requests.RequestException as e:
            logger.error("IG publish failed: %s", e)
            return False, f"שגיאת רשת בפרסום: {e}"

    def post_deal(self, product_name: str, price: float | None, url: str, image_url: str) -> dict:
        """End-to-end: caption -> upload -> publish. Returns a status dict
        the admin dashboard can show honestly."""
        caption = self.build_caption(product_name, price, url)
        if not self.is_connected:
            return {
                "status": "not_connected",
                "message": "Instagram לא מחובר — מגדירים INSTAGRAM_ACCESS_TOKEN + INSTAGRAM_ACCOUNT_ID ב-.env",
                "caption": caption,
            }
        container_id, err = self.create_media_container(image_url, caption)
        if not container_id:
            return {"status": "error", "message": f"העלאת המדיה נכשלה. {err}", "caption": caption}
        ok, err = self.publish(container_id)
        return {
            "status": "published" if ok else "error",
            "message": "הפוסט פורסם! 🎉" if ok else f"הפרסום נכשל. {err}",
            "caption": caption,
        }
