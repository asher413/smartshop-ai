import subprocess
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.agents.gemini_client import gemini_generate_json


class AutoViralEngine:
    """מייצר סקריפט קצר + מרנדר וידאו פרומו של 15 שניות מתמונת מוצר."""

    def generate_short_script(self, product_name: str, product_price: float | None = None) -> dict[str, Any]:
        prompt = f"""
            Create a 15-second short-form Hebrew script for TikTok/Instagram Reels.
            Product: {product_name}
            Price: {product_price}
            Return JSON with keys:
            - hook (short line)
            - voiceover_lines (array of 3 short lines, each <= 9 words)
            - cta (short call to action with urgency, but not misleading)
            - captions (array of 3 overlay caption strings)
            """
        fallback = {
            "hook": f"3 סיבות למה כדאי להכיר את {product_name}",
            "voiceover_lines": [
                "נראה פרימיום ועובד מעולה",
                "המחיר כרגע נמוך משמעותית",
                "מוצר שמשתמשים בו כל יום",
            ],
            "cta": "לחצו לקישור למידע נוסף",
            "captions": ["נראה יוקרתי", "דיל חם עכשיו", "לחצו לקישור"],
        }
        try:
            data = gemini_generate_json(prompt, timeout_seconds=10.0, temperature=0.6)
            return data if isinstance(data, dict) else fallback
        except Exception:
            return fallback

    def render_short_video(self, image_url: str, script: dict[str, Any], output_file: str) -> str | None:
        """משתמש ב-ffmpeg (אם מותקן) ליצירת שורט 15 שניות עם overlay טקסט."""
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        captions = (script.get("captions", []) + ["דיל חם", "הזדמנות מוגבלת", "לחצו לקישור"])[:3]

        cmd = [
            "ffmpeg", "-y", "-loop", "1", "-i", image_url, "-t", "15",
            "-vf",
            (
                "scale=1080:1920:force_original_aspect_ratio=cover,"
                "drawtext=text='{}':fontcolor=white:fontsize=56:x=(w-text_w)/2:y=h*0.2:enable='between(t,0,5)',"
                "drawtext=text='{}':fontcolor=white:fontsize=56:x=(w-text_w)/2:y=h*0.5:enable='between(t,5,10)',"
                "drawtext=text='{}':fontcolor=white:fontsize=56:x=(w-text_w)/2:y=h*0.8:enable='between(t,10,15)'"
            ).format(*[c.replace(":", " ") for c in captions]),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(output_path),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            return str(output_path)
        except Exception:
            return None

    def build_social_caption(self, product_name: str, affiliate_url: str, script: dict[str, Any]) -> str:
        hook = script.get("hook", f"דיל חם על {product_name}")
        cta = script.get("cta", "לפרטים ולרכישה לחצו כאן")
        return (
            f"{hook}\n\n✅ {product_name}\n🔗 {affiliate_url}\n\n{cta}\n"
            f"#deals #shorts #tiktokmademebuyit #shopnow"
        )
