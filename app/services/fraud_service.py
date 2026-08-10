"""
Bot/scraper detection for the storefront itself — protects your AI-written
content, pricing data, and affiliate links from being scraped wholesale by
competitors' bots (the same class of scraper this project uses against
suppliers, aimed back at you).
"""
import time


class FraudService:
    BOT_KEYWORDS = ("bot", "spider", "crawler", "scrape", "python-requests", "headless", "curl", "wget")

    def is_bot(self, user_agent: str) -> bool:
        ua = (user_agent or "").lower()
        return any(keyword in ua for keyword in self.BOT_KEYWORDS)

    def risk_score(
        self,
        user_agent: str,
        accept_language: str = "",
        request_count_last_minute: int = 0,
        last_request_time: float = 0,
    ) -> int:
        """
        Composite risk score 0-100 combining UA signature, missing headers,
        request velocity, and burst timing. Feed this into a soft block
        (CAPTCHA / slow response) above ~70, hard block above ~90 — never
        silently corrupt data shown to suspected bots, that just teaches
        them your detection thresholds.
        """
        score = 0
        current_time = time.time()

        if last_request_time > 0 and (current_time - last_request_time) < 0.2:
            score += 40  # inhuman request cadence
        if self.is_bot(user_agent):
            score += 70
        if not accept_language:
            score += 10
        if request_count_last_minute > 120:
            score += 30

        return min(score, 100)
