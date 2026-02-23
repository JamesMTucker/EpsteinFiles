"""PDF download logic using requests with cookies from Selenium."""

import logging
import os
from urllib.parse import unquote

import requests

from .browser import BrowserManager
from .config import EXTRA_FILES
from .db import Database
from .models import PdfFile

log = logging.getLogger(__name__)

SESSION_REFRESH_INTERVAL = 500


class Downloader:
    def __init__(self, db: Database, browser: BrowserManager, output_dir: str):
        self.db = db
        self.browser = browser
        self.output_dir = output_dir
        self._session: requests.Session | None = None

    def _init_session(self, dataset: int | None = None):
        """Establish browser session and create a requests.Session with extracted cookies."""
        # Navigate to a dataset page to trigger gate dismissal and set cookies
        ds = dataset or 1
        from .config import DATASET_URL_TEMPLATE
        url = DATASET_URL_TEMPLATE.format(dataset=ds)
        self.browser.ensure_session(url)

        self._session = requests.Session()
        cookies = self.browser.get_cookies_dict()
        self._session.cookies.update(cookies)
        self._session.headers.update({
            "User-Agent": self.browser.driver.execute_script("return navigator.userAgent"),
        })
        log.info("HTTP session initialized with %d cookies", len(cookies))

    def _refresh_session(self, dataset: int | None = None):
        """Refresh browser cookies and update the requests session."""
        log.info("Refreshing session cookies")
        ds = dataset or 1
        from .config import DATASET_URL_TEMPLATE
        url = DATASET_URL_TEMPLATE.format(dataset=ds)
        self.browser.ensure_session(url)

        cookies = self.browser.get_cookies_dict()
        self._session.cookies.clear()
        self._session.cookies.update(cookies)
        log.info("Session refreshed with %d cookies", len(cookies))

    def download_all(self, dataset: int | None = None, retry_failed: bool = False):
        """Download all pending (and optionally failed) PDFs."""
        if retry_failed:
            self.db.reset_failed(dataset)

        pending = self.db.get_pending_downloads(dataset)
        total = len(pending)

        if total == 0:
            print("No pending downloads.")
            return

        print(f"Downloading {total} files...")

        # Initialize HTTP session from browser cookies
        self._init_session(dataset)

        downloaded = 0
        skipped = 0
        failed = 0
        not_found = 0

        for i, pdf in enumerate(pending, 1):
            ds_dir = os.path.join(self.output_dir, f"DataSet_{pdf.dataset}")
            dest_path = os.path.join(ds_dir, pdf.filename)

            # Skip if file already exists on disk with size > 0
            if os.path.exists(dest_path) and os.path.getsize(dest_path) > 0:
                file_size = os.path.getsize(dest_path)
                self.db.mark_downloaded(pdf.url, file_size)
                skipped += 1
                if i % 100 == 0:
                    print(f"  [{i}/{total}] downloaded={downloaded} skipped={skipped} failed={failed} 404={not_found}")
                continue

            status, msg = self._download_file(pdf, ds_dir, dataset)

            if status == "downloaded":
                downloaded += 1
            elif status == "not_found":
                not_found += 1
            else:
                failed += 1

            if i % 50 == 0:
                print(f"  [{i}/{total}] downloaded={downloaded} skipped={skipped} failed={failed} 404={not_found}")

            if status == "failed":
                print(f"  ERROR: {pdf.filename}: {msg}")

            # Proactively refresh session to prevent cookie expiry
            if i % SESSION_REFRESH_INTERVAL == 0 and i < total:
                log.info("Proactive session refresh after %d files", i)
                self._refresh_session(dataset)

        print(f"\nDownload complete: downloaded={downloaded} skipped={skipped} failed={failed} 404={not_found} total={total}")

    def download_extras(self):
        """Download extra files (protocol document, etc.)."""
        for entry in EXTRA_FILES:
            self.db.upsert_extra_file(entry["url"], entry["subdir"], entry["filename"])

        pending = self.db.get_pending_extra_files()
        if not pending:
            print("No pending extra file downloads.")
            return

        for extra in pending:
            dest_dir = os.path.join(self.output_dir, extra["subdir"])
            dest_path = os.path.join(dest_dir, extra["filename"])

            if os.path.exists(dest_path) and os.path.getsize(dest_path) > 0:
                file_size = os.path.getsize(dest_path)
                self.db.mark_extra_downloaded(extra["url"], file_size)
                print(f"  [skipped] {extra['filename']}")
                continue

            try:
                result_path = self.browser.download_file(extra["url"], dest_dir, extra["filename"])
                if result_path and os.path.exists(result_path):
                    file_size = os.path.getsize(result_path)
                    self.db.mark_extra_downloaded(extra["url"], file_size)
                    print(f"  [downloaded] {extra['filename']}")
                else:
                    self.db.mark_extra_failed(extra["url"], "Download timed out")
                    print(f"  [failed] {extra['filename']}: timeout")
            except Exception as e:
                self.db.mark_extra_failed(extra["url"], str(e))
                print(f"  [failed] {extra['filename']}: {e}")

    def _download_file(self, pdf: PdfFile, dest_dir: str, dataset: int | None = None) -> tuple[str, str]:
        """Download a single PDF file via HTTP GET. Returns (status, message)."""
        os.makedirs(dest_dir, exist_ok=True)
        dest_path = os.path.join(dest_dir, pdf.filename)

        try:
            resp = self._session.get(pdf.url, stream=True, timeout=120)

            # Handle 404
            if resp.status_code == 404:
                self.db.mark_not_found(pdf.url)
                return ("not_found", "404")

            # Handle 401/403 - likely session expired, refresh and retry once
            if resp.status_code in (401, 403):
                log.warning("Got %d for %s, refreshing session and retrying", resp.status_code, pdf.filename)
                self._refresh_session(dataset)
                resp = self._session.get(pdf.url, stream=True, timeout=120)
                if resp.status_code in (401, 403):
                    self.db.mark_failed(pdf.url, f"HTTP {resp.status_code} after session refresh")
                    return ("failed", f"HTTP {resp.status_code} after retry")

            resp.raise_for_status()

            # Detect gate interception: if we got HTML instead of a PDF, the gate intercepted
            content_type = resp.headers.get("Content-Type", "")
            if "text/html" in content_type:
                log.warning("Got HTML instead of PDF for %s (gate interception?), refreshing session", pdf.filename)
                resp.close()
                self._refresh_session(dataset)
                # Retry
                resp = self._session.get(pdf.url, stream=True, timeout=120)
                resp.raise_for_status()
                content_type = resp.headers.get("Content-Type", "")
                if "text/html" in content_type:
                    self.db.mark_failed(pdf.url, "Gate interception: got HTML after retry")
                    return ("failed", "Gate interception after retry")

            # Stream to disk
            with open(dest_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    f.write(chunk)

            file_size = os.path.getsize(dest_path)
            if file_size > 0:
                self.db.mark_downloaded(pdf.url, file_size)
                return ("downloaded", "ok")
            else:
                os.remove(dest_path)
                self.db.mark_not_found(pdf.url)
                return ("not_found", "empty file")

        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response is not None else 0
            if status_code == 404:
                self.db.mark_not_found(pdf.url)
                return ("not_found", str(e))
            self.db.mark_failed(pdf.url, str(e))
            return ("failed", str(e))

        except Exception as e:
            error_msg = str(e)
            if "404" in error_msg or "not found" in error_msg.lower():
                self.db.mark_not_found(pdf.url)
                return ("not_found", error_msg)
            self.db.mark_failed(pdf.url, error_msg)
            return ("failed", error_msg)
