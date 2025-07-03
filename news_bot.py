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
    force=True          # ← overwrites any previous logging setup
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

def get_capella_urls():
    """
    Scrape https://www.capellaspace.com/media and yield the absolute URLs
    of cards whose chip reads “Press Releases”.
    A one-line debug log shows the HTTP status and raw HTML size so we can
    see at a glance whether the request succeeded and returned content.
    """
    base = "https://www.capellaspace.com"
    res  = requests.get(f"{base}/media", headers=HEADERS, timeout=30)

    logging.info("Capella status %s, length %s bytes",
                 res.status_code, len(res.text))          # DEBUG

    soup = BeautifulSoup(res.text, "html.parser")

    for card in soup.select("a.resource-card"):
        tag = card.select_one("div.category-tag")         # chip <div> text
        if tag and tag.get_text(strip=True).lower() == "press releases":
            yield urljoin(base, card["href"])





def get_spacewatch_urls():
    """
    Reads Spacewatch Global news RSS feed.
    """
    feed = feedparser.parse("https://spacewatch.global/news/feed/")
    for entry in feed.entries:
        yield entry.link


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



