import json
import logging

from app.agents.gemini_client import gemini_generate_json
from app.core.config import settings

logger = logging.getLogger(__name__)


class RecommenderAgent:
    def get_recommendations(self, viewed_products, all_products):
        fallback_ids = [p.id for p in all_products[:3]]

        # No API key → fall back to cheap catalog heuristics instead of
        # hanging on failed Gemini calls (each retry can take ~20s+ on the
        # product page, which is exactly where users don't want slowness).
        if not settings.google_api_key:
            return fallback_ids

        history_desc = ", ".join(p.name for p in viewed_products)
        catalog_desc = "\n".join(f"ID {p.id}: {p.name} - {p.category}" for p in all_products)

        example_json = json.dumps({"recommended_ids": [1, 5, 12]})
        prompt = f"""
        User has viewed these products: {history_desc}.
        Based on this, pick the 3 most relevant product IDs from our catalog:
        {catalog_desc}
        Return only a JSON object with a key 'recommended_ids' containing a list of IDs,
        for example: {example_json}
        """

        system = "You are an e-commerce recommendation engine. Only recommend IDs that exist in the catalog given."
        data = gemini_generate_json(prompt, system=system, timeout_seconds=6.0)
        if data:
            candidate_ids = data.get("recommended_ids", [])
            if isinstance(candidate_ids, list) and candidate_ids:
                return candidate_ids[:3]
        return fallback_ids
