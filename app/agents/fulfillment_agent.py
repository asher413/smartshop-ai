"""
Autonomous checkout agent.

Fixes vs. the original:
- No hardcoded Windows Chrome/Edge executable path — uses Playwright's own
  bundled Chromium, so this runs identically in Docker/Linux/CI.
- Explicit, narrow per-site selectors instead of a generic
  'button:has-text(Buy Now)' catch-all, which is fragile and can click the
  wrong element on a page with multiple CTAs (e.g. 'Buy Now' inside a
  recommended-products carousel).

Important operational note: automating checkout on a third-party site's
UI (rather than through an official order/checkout API) generally sits in
a legal/ToS gray zone for that supplier - most marketplaces' terms restrict
automated purchasing. Treat `shadow_mode=True` as the default for anything
beyond a supplier you've explicitly confirmed allows this, and prefer an
official order API the moment one exists (e.g. some AliExpress dropship
integrations offer one) over UI automation.
"""
import logging

from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

SITE_SELECTORS = {
    "aliexpress": lambda page: page.get_by_role("button", name="Buy Now").first.click(),
    "amazon": lambda page: page.get_by_id("add-to-cart-button").click(),
    "ebay": lambda page: page.get_by_text("Buy It Now", exact=False).first.click(),
    "temu": lambda page: page.get_by_label("Add to cart").click(),
}


class FulfillmentAgent:
    def purchase_product(self, supplier_name: str, supplier_url: str, customer_data: dict, shadow_mode: bool = True):
        """shadow_mode defaults to True on purpose — flip to False only for
        a supplier/site you've verified permits automated purchase flows."""
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
                    )
                )
                page = context.new_page()
                logger.info("Opening %s page: %s", supplier_name, supplier_url)
                page.goto(supplier_url, wait_until="networkidle", timeout=60000)

                if shadow_mode:
                    logger.info("Shadow mode active — simulating purchase, no click performed.")
                    browser.close()
                    return True

                click_action = SITE_SELECTORS.get(supplier_name.lower())
                if not click_action:
                    logger.warning("No verified selector for supplier '%s' — refusing to guess with a generic click.", supplier_name)
                    browser.close()
                    return False

                click_action(page)
                logger.info("Fulfillment executed for customer=%s", customer_data.get("name", "unknown"))
                browser.close()
                return True
        except Exception as e:
            logger.error("Fulfillment error: %s", e)
            return False
