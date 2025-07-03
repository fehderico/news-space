#!/usr/bin/env python3
"""
space-news-slack-bot
Scrapes four space-industry sites, makes a ≤100-token summary of each
new article, and posts one Slack message per article.
Runs happily on the free GitHub Actions runner.
"""

# ---------- 1. SETTINGS you may touch ---------------------------------------
WEBHOOK_URL = "https://hooks.slack.com/services/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"   # <── replace this

SOURCES = {
    "iceye":     "https://www.iceye.com/newsroom/press-releases",
    "rocketlab": "https://rocketlabcorp.com/updates/",
    "capella":   "https://www.capellaspace.com/media",
    "spacewatch":"https://spacewatch.global/news/",
}

CACHE_FILE = "sent_urls.json"      # remembers what we already posted
SUMMARY_SENTENCES = 3              # ≈100 tokens
# ---------------------------------------------------------------------------

import json, re, time, hashlib, os, logging
import requests
import feedparser
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from newspaper import Article, Config
from urllib.parse import urljoin

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)s  %(message)s")

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
    Reads Capella Space press-release RSS feed, returns absolute URLs.
    """
    feed = feedparser.parse("https://www.capellaspace.com/media/feed/")
    for entry in feed.entries:
        yield entry.link


def get_spacewatch_urls():
    """
    Reads Spacewatch Global news RSS feed.
    """
    feed = feedparser.parse("https://spacewatch.global/news/feed/")
    for entry in feed.entries:
        yield entry.link


SCRAPER_FUNCS = [get_iceye_urls, get_rocketlab_urls,
                 get_capella_urls, get_spacewatch_urls]

# ---------- 3. SUMMARY ------------------------------------------------------
def summarise(url: str) -> tuple[str, str]:
    """
    Download the article and return (title, ≤100-token preview).
    No NLTK; just take the first ~100 words of the cleaned text.
    """
    cfg = Config(); cfg.request_headers = HEADERS
    art = Article(url, language="en", config=cfg)
    art.download(); art.parse()

    title = art.title or "Untitled"
    words = art.text.replace("\n", " ").split()

    if not words:
        return title, "Summary unavailable."

    preview = " ".join(words[:100])
    if preview[-1] not in ".!?":
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
        for url in fn():
            key = hash_url(url)
            if key in seen or key in new_seen:
                continue
            try:
                title, summary = summarise(url)
                send_slack(title, summary, url)
                logging.info("Posted %s", title)
                new_seen.add(key)
            except Exception as e:
                logging.error("Failed on %s : %s", url, e)
    save_cache(seen.union(new_seen))

if __name__ == "__main__":
    main()

