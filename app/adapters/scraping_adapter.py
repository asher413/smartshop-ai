"""
Generic fallback adapter used whenever a supplier has no official API yet
(this is the ONLY path for Temu today, and the fallback path for the
others until you configure their API credentials).

Fixes vs. the original product_scraper.py:
- No hardcoded "C:\\Program Files\\Google\\Chrome..." path — that only
  worked on one specific Windows dev machine and would crash on any
  Linux server/Docker container. Playwright's bundled Chromium is used
  instead (`playwright install chromium` in the Dockerfile).
- Adds a minimum delay between requests to the same domain (politeness /
  rate limiting) instead of firing requests back-to-back.
- Scraping public listing pages is legally greyer than using official
  APIs and can violate a site's Terms of Service — this is why the
  aggregator always prefers the official adapter when credentials exist,
  and why this class deliberately does NOT do session/login automation,
  CAPTCHA solving, or fingerprint spoofing. Keep usage light (discovery +
  occasional price refresh), not high-frequency bulk harvesting.
"""
import threading
import time
import random
from bs4 import BeautifulSoup

from app.adapters.base_adapter import BaseSupplierAdapter, RawProduct

MIN_DELAY_SECONDS = 2.0

# --- Shared Playwright instance ------------------------------------------
# sync_playwright().start() may only be called ONCE per process/thread — a
# second start() raises "Playwright Sync API inside the asyncio loop". The
# discovery pipeline instantiates one ScrapingAdapter per supplier (7 of
# them), so every adapter shares this single browser instead of each trying
# to start its own.
_playwright_lock = threading.Lock()
# Playwright's sync API is NOT thread-safe: only one thread may create
# contexts / drive pages on the shared browser at a time. The init lock
# above only guards lazy startup; this lock serializes actual usage so two
# concurrent requests (e.g. two price-war checks) can't crash the browser.
_browser_use_lock = threading.Lock()
_shared_playwright = None
_shared_browser = None


def _get_shared_browser():
    global _shared_playwright, _shared_browser
    with _playwright_lock:
        if _shared_browser is not None:
            return _shared_browser
        from playwright.sync_api import sync_playwright
        _shared_playwright = sync_playwright().start()
        _shared_browser = _shared_playwright.chromium.launch(headless=True)
        return _shared_browser


class ScrapingAdapter(BaseSupplierAdapter):
    uses_official_api = False

    def __init__(self, source_name: str, supported_domain: str):
        self.name = source_name
        self.supported_domain = supported_domain
        self._last_request_at = 0.0
        self._browser = None
        self._playwright = None
        # Lazy import: avoids requiring Playwright/browsers installed for
        # adapters that never end up using the fallback path.
        self._ai_parser = None
        # When a search page comes back blocked/empty, remember it so the
        # whole discovery cycle stops hammering that domain (each dead
        # timeout costs ~35s; Amazon/eBay/AliExpress/Temu all block us).
        self._blocked_until = 0.0
        self._consecutive_empty = 0

    def _mark_blocked(self):
        self._consecutive_empty += 1
        if self._consecutive_empty >= 2:
            self._blocked_until = time.time() + 3600  # 1h chill-out
        else:
            self._blocked_until = time.time() + 300

    def _throttle(self):
        elapsed = time.time() - self._last_request_at
        if elapsed < MIN_DELAY_SECONDS:
            time.sleep(MIN_DELAY_SECONDS - elapsed + random.uniform(0, 0.5))
        self._last_request_at = time.time()

    def _get_browser(self):
        # All ScrapingAdapter instances share one Playwright browser (see
        # _get_shared_browser above) — starting a second sync_playwright()
        # in the same process raises "Playwright Sync API inside the asyncio
        # loop" and silently killed every discovery run.
        return _get_shared_browser()

    def _get_ai_parser(self):
        if self._ai_parser is None:
            from app.agents.content_generator import ContentGenerator
            self._ai_parser = ContentGenerator()
        return self._ai_parser

    # Page titles that mean "you've been blocked / shown a challenge page",
    # not a real listing page. Detected early so the pipeline moves on to
    # working sources instead of wasting minutes scraping captcha walls.
    _BLOCK_PAGE_MARKERS = (
        "error page", "robot or human", "just a moment", "access denied",
        "attention required", "we\u2019re sorry", "something went wrong",
        "verify you are a human", "captcha", "forbidden", "enable javascript",
    )

    @staticmethod
    def _looks_blocked(html: str) -> bool:
        head = (html or "")[:6000].lower()
        return any(marker in head for marker in ScrapingAdapter._BLOCK_PAGE_MARKERS)

    def _fetch_html(self, url: str) -> str:
        self._throttle()
        # Hold the usage lock across the whole browser section: another
        # thread may be mid-page on the same shared browser (sync API is
        # not thread-safe), so context creation and page drives must be
        # serialized or Playwright raises/crashes.
        with _browser_use_lock:
            browser = self._get_browser()
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
                ),
                locale="en-US",
            )
            page = context.new_page()
            html = ""
            try:
                # Amazon/eBay render product cards only after client-side JS —
                # wait for the network to settle so the returned DOM actually
                # contains the product links, not just the shell page.
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass  # heavy marketplaces never fully idle; use what we have
                page.wait_for_timeout(800)  # let lazy-loaded cards land
                html = page.content()
            except Exception:
                # One retry — marketplace bot-checks are often transient (a
                # captcha interstitial that clears, a slow first paint, etc.).
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    page.wait_for_timeout(800)
                    html = page.content()
                except Exception:
                    html = ""
            finally:
                context.close()
        # Fail fast on bot-check/challenge pages instead of returning them
        # as if they were real listings.
        if self._looks_blocked(html):
            return ""
        return html

    # Per-domain product-link fingerprints. Search pages are full of
    # navigation/refinement links; these patterns pick out actual product
    # detail pages (Amazon /dp/<ASIN>, eBay /itm/<id>, AliExpress
    # /item/<id>.html, Temu /goods.html?...&goods_id=..., B&H /c/product/).
    _PRODUCT_LINK_PATTERNS = {
        "amazon.com": ["/dp/", "/gp/product/"],
        "ebay.com": ["/itm/", "/p/"],
        "aliexpress.com": ["/item/", "item/", "html"],
        "temu.com": ["/goods.html", "goods_id=", "/goods"],
        "bhphotovideo.com": ["/c/product/", "/c/product/"],
    }

    def _is_product_link(self, href: str) -> bool:
        patterns = self._PRODUCT_LINK_PATTERNS.get(self.supported_domain, [])
        if not patterns:
            return False
        return any(p in href for p in patterns)

    def fetch_trending(self, category: str | None = None, limit: int = 20) -> list[RawProduct]:
        """
        There's no generic "trending" page across arbitrary sites, so this
        pulls candidate product URLs from a category/search page and lets
        the AI parser (ContentGenerator.parse_supplier_data) extract price/
        rating/stock per item. Wire real search-URL templates per site in
        SEARCH_URL_TEMPLATES below once you pick target categories.
        """
        # Fast-skip domains that are currently in their blocked chill-out
        # window — a full discovery cycle otherwise burns minutes on them.
        if time.time() < self._blocked_until:
            return []

        search_templates = {
            "temu.com": "https://www.temu.com/search_result.html?search_key={q}",
            "aliexpress.com": "https://www.aliexpress.com/wholesale?SearchText={q}",
            "amazon.com": "https://www.amazon.com/s?k={q}",
            "ebay.com": "https://www.ebay.com/sch/i.html?_nkw={q}",
            "bhphotovideo.com": "https://www.bhphotovideo.com/c/search?q={q}",
        }
        template = search_templates.get(self.supported_domain)
        if not template:
            return []

        query = (category or "trending gadgets").replace(" ", "+")
        html = self._fetch_html(template.format(q=query))
        if not html:
            self._mark_blocked()
            return []
        self._consecutive_empty = 0
        soup = BeautifulSoup(html, "html.parser")
        # Extract candidate product links using per-site URL fingerprints,
        # then de-duplicate and normalize to absolute URLs.
        links = []
        seen = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not self._is_product_link(href):
                continue
            if href.startswith("/"):
                href = f"https://www.{self.supported_domain}{href}"
            elif not href.startswith("http"):
                continue
            # Drop Amazon redirect-ish URLs with extra clutter, keep the bare
            # canonical product path (they resolve fine with query stripped).
            href = href.split("?")[0]
            if href not in seen:
                seen.add(href)
                links.append(href)
            if len(links) >= limit:
                break

        results = []
        seen_names = set()
        for link in links[:limit]:
            offer = self.fetch_offer(link)
            if not offer:
                continue
            # Search pages often repeat the same product (sponsored card +
            # organic card, two hrefs). De-dupe by normalized name so a
            # single product can't flood the staging table with twins.
            norm = offer.name.strip().lower()
            if norm in seen_names:
                continue
            seen_names.add(norm)
            results.append(offer)
        return results

    def fetch_offer(self, external_id_or_url: str) -> RawProduct | None:
        if not external_id_or_url.startswith("http"):
            return None  # scraping adapter needs a real URL, not just an ID
        html = self._fetch_html(external_id_or_url)
        parsed = self._get_ai_parser().parse_supplier_data(html)
        if not parsed or not parsed.get("price"):
            return None
        # parse_supplier_data returns price/stock/rating/reviews only — the
        # title and image are pulled straight from the page's own metadata.
        title, image_url = self._extract_title_and_image(html)
        return RawProduct(
            source_adapter=self.name,
            external_id=external_id_or_url,
            name=title or "Unknown product",
            price=float(parsed.get("price", 0) or 0),
            currency="USD",
            url=external_id_or_url,
            image_url=image_url,
            in_stock=bool(parsed.get("in_stock", True)),
            rating=float(parsed.get("rating", 0) or 0),
            review_count=int(parsed.get("review_count", 0) or 0),
        )

    @staticmethod
    def _extract_title_and_image(html: str) -> tuple[str, str]:
        """Pull the product title and image from schema.org JSON-LD / og:
        metadata in the page, which every major marketplace embeds."""
        soup = BeautifulSoup(html or "", "html.parser")
        title = ""
        image_url = ""

        # schema.org JSON-LD is the most reliable signal (Amazon, eBay,
        # B&H, Newegg, Walmart all embed Product objects).
        from app.agents.content_generator import ContentGenerator
        ld = ContentGenerator._extract_jsonld(html or "")
        title = ld.get("name") or ""
        image_url = ld.get("image") or ""

        og_title = soup.find("meta", property="og:title")
        if not title and og_title and og_title.get("content"):
            title = og_title["content"].strip()
        if not title:
            t = soup.title
            if t and t.string:
                title = t.string.strip()
        # Cut trailing marketplace suffixes like " | Amazon.com" or " - eBay".
        for sep in (" | ", " - ", " – "):
            if sep in title:
                title = title.split(sep)[0]
                break

        if not image_url:
            og_image = soup.find("meta", property="og:image")
            if og_image and og_image.get("content"):
                image_url = og_image["content"].strip()
        return title, image_url

    def build_affiliate_link(self, raw_url: str) -> str:
        # No official tracking available without an API — appends a plain
        # ref param so click_log/tracking_service can still attribute clicks.
        separator = "&" if "?" in raw_url else "?"
        return f"{raw_url}{separator}ref=smartshopai"

    def __del__(self):
        # Shared browser — individual adapters must NOT close it out from
        # under their siblings. The process shuts it down on exit.
        pass
