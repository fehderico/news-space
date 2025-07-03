#!/usr/bin/env python3
"""
space-news-slack-bot
Scrapes four space-industry sites, makes a ≤100-token summary of each
new article, and posts one Slack message per article.
Runs happily on the free GitHub Actions runner.
"""

import json, re, time, hashlib, os, logging
import requests
import feedparser
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright

from bs4 import BeautifulSoup
from newspaper import Article, Config


# ---------- 1. SETTINGS you may touch ---------------------------------------
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
if not WEBHOOK_URL:
    raise RuntimeError("Set WEBHOOK_URL environment variable or secret.")
   # <── replace this

SOURCES = {
    "iceye":     "https://www.iceye.com/newsroom/press-releases",
    "rocketlab": "https://rocketlabcorp.com/updates/",
    "capella":   "https://www.capellaspace.com/media",
    "spacewatch":"https://spacewatch.global/news/",
}

CACHE_FILE = "sent_urls.json"      # remembers what we already posted
SUMMARY_SENTENCES = 3              # ≈100 tokens
# ---------------------------------------------------------------------------


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    force=True,      # ensures INFO actually prints
)




def load_cache():
    if os.path.isfile(CACHE_FILE):
        with open(CACHE_FILE) as f:
            return set(json.load(f))
    return set()

def save_cache(cache):
    with open(CACHE_FILE, "w") as f:
        json.dump(sorted(cache), f)

def hash_url(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()

# ---------- 2. SCRAPERS -----------------------------------------------------
HEADERS = {"User-Agent": "space-news-bot (+https://github.com/yourrepo)"}

# ---------- Playwright helper ------------------------------------------------
def render_and_get_links(url, selector, max_links=30):
    """
    Open the page in headless Chromium, wait for JS to load, return the first
    max_links hrefs that match CSS selector.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=30000)
        links = page.locator(selector).evaluate_all("els => els.map(e => e.href)")
        browser.close()
        return links[:max_links]

def click_then_get_links(url, click_text, card_selector, max_links=30):
    """
    • Open `url` in headless Chromium
    • Click the button whose visible text is `click_text`
    • After network is idle, collect hrefs that match `card_selector`
    """
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=30000)
        page.click(f"text={click_text}", timeout=10000)
        page.wait_for_load_state("networkidle", timeout=30000)
        links = page.locator(card_selector).evaluate_all("els=>els.map(e=>e.href)")
        browser.close()
        return links[:max_links]

# ------------------------------------------------------------------------------

def get_iceye_urls():
    soup = BeautifulSoup(requests.get(SOURCES["iceye"], headers=HEADERS,
                                      timeout=20).text, "html.parser")
    for a in soup.select("a"):
        if a.text.strip().startswith("Read more"):
            yield a["href"]

def get_rocketlab_urls():
    soup = BeautifulSoup(requests.get(SOURCES["rocketlab"], headers=HEADERS,
                                      timeout=20).text, "html.parser")
    for a in soup.select("a"):
        if a.text.strip().endswith("Read more"):
            yield urljoin(SOURCES["rocketlab"], a["href"])

def get_capella_urls(max_cards: int = 30):
    base  = "https://www.capellaspace.com"
    start = f"{base}/media"

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page    = browser.new_page()
        page.goto(start, wait_until="networkidle", timeout=30000)

        logging.info("🌐 Capella loaded /media, DOM length %s",
                     len(page.content()))                       # ← 1

        page.click("text=/press releases/i", timeout=10000)

        # Wait up to 15 s for *any* injected card to appear
        try:
            page.wait_for_selector("a.resource-card[href]", timeout=15000)
            logging.info("✔️  Cards injected; DOM length %s",
                         len(page.content()))                   # ← 2
        except:
            logging.warning("❌ No cards appeared after click")  # ← 3
            browser.close()
            return []

        links = page.locator("a.resource-card[href]").evaluate_all(
                     "els => els.map(e => e.href)")
        browser.close()
    return links[:max_cards]



def get_spacewatch_urls():
    # render /news front page; pick headline links
    url = "https://spacewatch.global/news/"
    return render_and_get_links(url, "h3.entry-title a")





SCRAPER_FUNCS = [
    get_iceye_urls,
    get_rocketlab_urls,
    get_capella_urls,
    get_spacewatch_urls,
]

# ---------- 3. SUMMARY ------------------------------------------------------
def summarise(url: str, fallback_text: str = "") -> tuple[str, str]:
    """
    Try to download the full article; if that fails, use the fallback_text
    provided by the RSS/JSON feed.  Always return (title, ≤100-token preview).
    """
    cfg = Config(); cfg.request_headers = HEADERS
    art = Article(url, language="en", config=cfg)
    try:
        art.download(); art.parse()
        title = art.title or "Untitled"
        words = art.text.replace("\n", " ").split()
        preview = " ".join(words[:100]) or "(no preview)"
    except Exception as e:
        logging.warning("Fallback to feed text for %s  (%s)", url, e)
        title = "Untitled (feed)"
        preview = fallback_text[:500] or "(no preview)"

    if preview and preview[-1] not in ".!?":
        preview += "…"
    return title, preview


# ---------- 4. SLACK --------------------------------------------------------
def send_slack(title: str, summary: str, url: str):
    payload = {
        "text": f"*{title}*\n{summary}\n<{url}|Read the full article>",
    }
    r = requests.post(WEBHOOK_URL, json=payload, timeout=10)
    r.raise_for_status()          # stop if webhook URL is wrong
    time.sleep(1.1)               # ≤1 message/sec (Slack limit)

# ---------- 5. MAIN ---------------------------------------------------------
def main():
    seen = load_cache()
    new_seen = set()

    for fn in SCRAPER_FUNCS:
        logging.info("Running scraper: %s", fn.__name__)   # inside the loop
        found = False                                      # inside the loop

        for entry in fn():                                 # still indented
            found = True
            url   = entry.link if hasattr(entry, "link") else entry
            descr = getattr(entry, "summary", "") if hasattr(entry, "summary") else ""
            key   = hash_url(url)

            if key in seen or key in new_seen:
                continue
            try:
                title, summary = summarise(url, descr)     # pass feed summary
                send_slack(title, summary, url)
                logging.info("Posted %s", title)
                new_seen.add(key)
            except Exception as e:
                logging.error("Failed on %s : %s", url, e)

        if not found:                                      # still inside outer loop
            logging.warning("Scraper %s returned ZERO URLs", fn.__name__)

    # after the outer loop finishes
    save_cache(seen.union(new_seen))

# ---------- 6. ENTRY POINT --------------------------------------------------
if __name__ == "__main__":
    main()



