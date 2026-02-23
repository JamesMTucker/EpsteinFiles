"""Page scraping logic using Selenium + BeautifulSoup."""

import logging
import re
import time
from urllib.parse import unquote, urljoin

from bs4 import BeautifulSoup

from .browser import BrowserManager
from .config import BASE_URL, DATASET_URL_TEMPLATE, DATASETS, SCRAPE_DELAY
from .db import Database

log = logging.getLogger(__name__)

# Match PDF/PPDF links containing EFTA (case-insensitive)
_PDF_HREF_RE = re.compile(r"\.p?pdf$", re.IGNORECASE)
_EFTA_RE = re.compile(r"EFTA", re.IGNORECASE)

MAX_PAGES = 1500


class Scraper:
    def __init__(self, db: Database, browser: BrowserManager):
        self.db = db
        self.browser = browser

    def scrape_all(self, run_id: int) -> tuple[int, int]:
        """Scrape all datasets. Returns (total_pages, total_urls)."""
        total_pages = 0
        total_urls = 0

        for ds in DATASETS:
            pages, urls = self.scrape_dataset(ds, run_id)
            total_pages += pages
            total_urls += urls

            # Restart browser between datasets to manage memory
            print(f"  Restarting browser...")
            self.browser.quit()
            self.browser.start()

        return total_pages, total_urls

    def scrape_dataset(self, dataset_num: int, run_id: int) -> tuple[int, int]:
        """Scrape all pages for a single dataset. Returns (pages_scraped, urls_discovered)."""
        base_url = DATASET_URL_TEMPLATE.format(dataset=dataset_num)
        pages_scraped = 0
        urls_discovered = 0
        prev_urls = set()
        page = 0

        log.info("Starting scrape of Data Set %d, base URL: %s", dataset_num, base_url)

        while page < MAX_PAGES:
            page_url = base_url if page == 0 else f"{base_url}?page={page}"
            print(f"  Scraping Data Set {dataset_num} page {page}...", end="", flush=True)

            try:
                pdf_urls, has_next = self._scrape_page(page_url)
            except Exception as e:
                log.error("Error scraping %s: %s", page_url, e)
                print(f" ERROR: {e}")
                break

            current_urls = set(pdf_urls)

            # Stop only when there are no PDFs AND no "Next" link
            if not pdf_urls and not has_next:
                log.info("No PDFs and no Next link on page %d, stopping", page)
                print(" end of pages")
                break

            # Also stop if we see exact same URLs as previous page (stuck pagination)
            if pdf_urls and current_urls == prev_urls:
                log.info("Same URLs as previous page on page %d, stopping", page)
                print(" end of pages (duplicate)")
                break

            new_count = 0
            for url in pdf_urls:
                filename = unquote(url.split("/")[-1])
                if self.db.upsert_pdf(url, dataset_num, filename, run_id):
                    new_count += 1
                    urls_discovered += 1
            pages_scraped += 1
            print(f" {len(pdf_urls)} files ({new_count} new)")
            log.debug("Page %d: %d PDFs found, %d new, has_next=%s", page, len(pdf_urls), new_count, has_next)

            prev_urls = current_urls
            page += 1
            time.sleep(SCRAPE_DELAY)

        if page >= MAX_PAGES:
            log.warning("Hit max pages limit (%d) for Data Set %d", MAX_PAGES, dataset_num)

        print(f"  Data Set {dataset_num}: {pages_scraped} pages, {urls_discovered} new URLs\n")
        return pages_scraped, urls_discovered

    def _scrape_page(self, url: str) -> tuple[list[str], bool]:
        """Navigate to a page with Selenium, parse with BS4, extract PDF links.

        Returns (pdf_urls, has_next) where has_next indicates a "Next" pagination link exists.
        """
        page_source = self.browser.get_page(url)
        soup = BeautifulSoup(page_source, "html.parser")

        pdf_links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            # Case-insensitive check for .pdf/.ppdf extension and EFTA in the URL
            if _PDF_HREF_RE.search(href) and _EFTA_RE.search(href):
                # Handle both relative and absolute hrefs
                full_url = urljoin(url, href)
                pdf_links.append(full_url)

        # Deduplicate while preserving order
        seen = set()
        unique = []
        for link in pdf_links:
            if link not in seen:
                seen.add(link)
                unique.append(link)

        # Check for "Next" pagination link
        has_next = False
        for a in soup.find_all("a"):
            text = (a.get_text() or "").strip()
            if text.lower() in ("next", "next ›", "next »", "next page"):
                has_next = True
                break
            rel = a.get("rel", [])
            if "next" in rel:
                has_next = True
                break

        log.debug("Parsed %s: %d PDF links, has_next=%s", url, len(unique), has_next)
        if not unique:
            # Log page title for debugging when no PDFs found
            title = soup.title.string if soup.title else "(no title)"
            log.warning("No PDF links found on %s (title: %s)", url, title)

        return unique, has_next
