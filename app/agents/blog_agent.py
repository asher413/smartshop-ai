from app.agents.gemini_client import gemini_generate_text
from app.core.config import settings


class BlogAgent:
    def write_buying_guide(self, category, products_list):
        prompt = f"""
            Create a 'Top 5 Best {category} for 2026' Buying Guide in Hebrew, using ONLY the
            products and data provided below - do not invent products or specs that weren't given.
            Products to include: {products_list}.

            For each product, include:
            1. A mini-review based on the ratings/pros/cons actually provided.
            2. A comparison note (Price vs Value).
            3. A clear 'Verdict': who should buy this?

            Return a JSON object with:
            'title', 'content' (HTML), 'excerpt' (2 sentences), 'meta_description', 'slug'.
            """
        result = gemini_generate_text(prompt, timeout_seconds=10.0, temperature=0.8)
        if not result:
            raise RuntimeError("AI timed out")
        return result
