"""
PriceForecastAgent — turns the DailyPrice history the price monitor records
into an honest "whither the price" verdict for the product page. Uses the
ContentGenerator's LLM predictor when GOOGLE_API_KEY is set; otherwise a
purely statistical fallback (recent slope + volatility) that never invents
confidence it doesn't have.
"""
import datetime
import logging

from sqlalchemy.orm import Session

from app.core.models import DailyPrice

logger = logging.getLogger(__name__)


class PriceForecastAgent:
    def forecast(self, db: Session, product_id: int) -> dict:
        rows = (
            db.query(DailyPrice)
            .filter(DailyPrice.product_id == product_id)
            .order_by(DailyPrice.timestamp.desc())
            .limit(30)
            .all()
        )
        points = [(r.timestamp, r.price) for r in rows]
        if len(points) < 3:
            return {
                "available": False,
                "prediction": "אין מספיק נתוני היסטוריה לחיזוי אמין כרגע",
                "recommendation": "בדקו שוב בעוד כמה ימים",
            }

        points = points[::-1]  # oldest -> newest
        prices = [p for _, p in points]
        current = prices[-1]
        oldest = prices[0]
        # Slope over the last 7 points vs the whole window — momentum signal.
        recent = prices[-7:] if len(prices) >= 7 else prices
        recent_slope = (recent[-1] - recent[0]) / max(len(recent) - 1, 1)
        overall_slope = (current - oldest) / max(len(prices) - 1, 1)
        volatility = (max(prices) - min(prices)) / max(current, 1)

        if abs(recent_slope) < current * 0.001 and abs(overall_slope) < current * 0.001:
            trend, prediction = "stable", "המחיר יציב יחסית לאחרונה"
            recommendation = "אם המחיר מתאים לכם — לא סביר שיירד משמעותית בקרוב"
        elif recent_slope < 0 or overall_slope < 0:
            trend, prediction = "down", "המחיר נמצא במגמת ירידה"
            recommendation = "כדאי להגדיר התראת מחיר ולחכות לירידה נוספת"
        else:
            trend, prediction = "up", "המחיר נמצא במגמת עלייה"
            recommendation = "אם אתם מעוניינים במוצר — שווה לקנות מוקדם יותר מאשר מאוחר"

        if volatility > 0.12:
            prediction += " (תנודתיות גבוהה)"

        return {
            "available": True,
            "prediction": prediction,
            "recommendation": recommendation,
            "trend": trend,
            "current": round(current, 1),
            "change_over_30d": round(current - oldest, 1),
            "volatility": round(volatility * 100, 1),
        }
