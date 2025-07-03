
import json, requests
from pathlib import Path
from time import sleep
from urllib.parse import urljoin


from pathlib import Path
from time import sleep
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from transformers import pipeline            # HuggingFace summariser

BASE_URL = "https://www.capellaspace.com/media"
OUTFILE  = Path("capella_media.jsonl")
summarise = pipeline("summarization", model="facebook/bart-large-cnn",
                     device_map="auto", max_length=60, min_length=25)

def scrape():
    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True)
        page = browser.new_page()
        page.goto(BASE_URL, wait_until="networkidle")

        # Repeatedly click "Load More" until it disappears
        while True:
            try:
                page.get_by_role("button", name="Load More").click()
                page.wait_for_timeout(800)          # ms – wait for cards
            except Exception:
                break                               # no more button

        soup = BeautifulSoup(page.content(), "lxml")
        browser.close()

    # Cards live inside <a> tags that wrap a <div role="article">
    cards = soup.select("a[href^='/'][href*='press-'], \
                          a[href^='/'][href*='blog-'], \
                          a[href^='/'][href*='in-the-news-']")

    results = []
    for a in cards:
        title = a.get_text(" ", strip=True).split(" Min Watch")[0]
        link  = urljoin(BASE_URL, a["href"])

        # fetch the individual article (now a static page)
        article_html = requests.get(link, timeout=20).text
        art_soup = BeautifulSoup(article_html, "lxml")
        body = " ".join(p.get_text(" ", strip=True)
                        for p in art_soup.select("article p")[:12])  # first grafs
        short = summarise(body)[0]["summary_text"]

        results.append({"title": title, "summary": short, "url": link})
        print(f"✓ {title}")

    OUTFILE.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in results))

if __name__ == "__main__":
    scrape()
