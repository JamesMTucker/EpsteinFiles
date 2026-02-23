"""Selenium browser management for headless Chromium."""

import logging
import os
import time

from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .config import BASE_URL, DOWNLOAD_TIMEOUT

log = logging.getLogger(__name__)


class BrowserManager:
    def __init__(self):
        self.driver: webdriver.Chrome | None = None

    def start(self):
        """Launch a headless Chromium browser with anti-detection measures."""
        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")

        # Anti-detection: prevent Akamai WAF from flagging headless Chrome
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument(
            "--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
        )
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)

        # Prevent Chrome PDF viewer from opening PDFs inline
        options.add_experimental_option("prefs", {
            "download.prompt_for_download": False,
            "plugins.always_open_pdf_externally": True,
            "download.directory_upgrade": True,
        })

        chrome_bin = os.environ.get("CHROME_BIN")
        if chrome_bin:
            options.binary_location = chrome_bin

        chromedriver_path = os.environ.get("CHROMEDRIVER_PATH")
        if chromedriver_path:
            service = Service(executable_path=chromedriver_path)
        else:
            service = Service()

        self.driver = webdriver.Chrome(service=service, options=options)

        # Remove navigator.webdriver flag that WAFs use to detect automation
        self.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        })

        log.info("Browser started")

    def get_page(self, url: str) -> str:
        """Navigate to a URL, dismiss gates if present, and return page source.

        If the page returns "Access Denied", injects the age cookie and retries.
        """
        # Ensure the age cookie is always set before navigating
        self._ensure_age_cookie()

        log.debug("Navigating to %s", url)
        self.driver.get(url)
        self._dismiss_gates(url)

        source = self.driver.page_source
        log.debug("Page source length: %d chars, final URL: %s", len(source), self.driver.current_url)

        # Detect "Access Denied" and retry with fresh cookie
        if len(source) < 1000 and "access denied" in source.lower():
            log.warning("Got 'Access Denied' for %s, injecting cookie and retrying", url)
            self._inject_age_cookie()
            self.driver.get(url)
            time.sleep(2)
            self._dismiss_gates(url)
            source = self.driver.page_source
            log.debug("Retry page source length: %d chars", len(source))

        return source

    def _ensure_age_cookie(self):
        """Make sure the age verification cookie is set in the browser."""
        try:
            cookies = {c["name"]: c["value"] for c in self.driver.get_cookies()}
            if "justiceGovAgeVerified" not in cookies:
                self._inject_age_cookie()
        except WebDriverException:
            pass

    def _inject_age_cookie(self):
        """Inject the age verification cookie into the browser."""
        try:
            # Must be on a justice.gov page to set a cookie for that domain
            current_url = self.driver.current_url
            if "justice.gov" not in current_url:
                self.driver.get(BASE_URL)
                time.sleep(1)
            self.driver.add_cookie({
                "name": "justiceGovAgeVerified",
                "value": "true",
                "domain": ".justice.gov",
                "path": "/",
            })
            log.debug("Injected justiceGovAgeVerified cookie")
        except WebDriverException as e:
            log.warning("Failed to inject age cookie: %s", e)

    def _dismiss_gates(self, target_url: str):
        """Handle bot check and age verification gates.

        The DOJ site may present two sequential gates:
        1. Bot check ("Are you a bot?" - click "No" / "not a bot" / "human")
           After dismissing, the browser often redirects to the homepage,
           so we re-navigate to the target URL.
        2. Age verification ("Are you over 18?" - click "Yes")

        Falls back to injecting the age verification cookie if clicks fail.
        """
        # Give the page a moment to render any gate
        time.sleep(1)

        # --- Bot check gate ---
        bot_dismissed = self._try_click_gate("Bot", [
            (By.LINK_TEXT, "No"),
            (By.PARTIAL_LINK_TEXT, "not a bot"),
            (By.PARTIAL_LINK_TEXT, "human"),
            (By.XPATH, "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'no')]"),
            (By.XPATH, "//a[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'not a bot')]"),
            (By.XPATH, "//input[@type='submit' and contains(translate(@value, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'no')]"),
        ])

        if bot_dismissed:
            # After bot gate, the site often redirects to homepage.
            # Re-navigate to the target URL to reach the age gate or content.
            log.info("Re-navigating to target URL after bot gate: %s", target_url)
            self.driver.get(target_url)
            time.sleep(2)

        # --- Age verification gate ---
        age_dismissed = self._try_click_gate("Age", [
            (By.LINK_TEXT, "Yes"),
            (By.PARTIAL_LINK_TEXT, "Yes"),
            (By.XPATH, "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'yes')]"),
            (By.XPATH, "//a[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'yes')]"),
            (By.XPATH, "//input[@type='submit' and contains(translate(@value, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'yes')]"),
        ])

        # --- Fallback: inject cookie if age gate wasn't handled ---
        if not age_dismissed:
            page_text = self.driver.page_source.lower()
            gate_indicators = ("are you over 18", "age verification", "are you a bot", "access denied")
            if any(indicator in page_text for indicator in gate_indicators):
                log.warning("Gate/block still present after click attempts, injecting cookie fallback")
                self._inject_age_cookie()
                self.driver.get(target_url)
                time.sleep(2)
                log.info("Cookie injected and re-navigated to %s", target_url)

    def _try_click_gate(self, gate_name: str, strategies: list[tuple]) -> bool:
        """Try multiple strategies to click through a gate. Returns True if a click succeeded."""
        for by, value in strategies:
            try:
                elem = WebDriverWait(self.driver, 2).until(
                    EC.element_to_be_clickable((by, value))
                )
                log.info("%s gate: clicking element found by %s='%s'", gate_name, by, value)
                elem.click()
                time.sleep(2)
                log.info("%s gate dismissed", gate_name)
                return True
            except (TimeoutException, NoSuchElementException, WebDriverException):
                continue

        log.debug("No %s gate detected", gate_name)
        return False

    def get_cookies_dict(self) -> dict[str, str]:
        """Extract all browser cookies (including httpOnly) as a dict for use with requests."""
        cookies = {}
        # Use CDP to get ALL cookies including httpOnly ones (e.g. Akamai ak_bmsc)
        try:
            cdp_cookies = self.driver.execute_cdp_cmd("Network.getAllCookies", {})
            for cookie in cdp_cookies.get("cookies", []):
                if "justice.gov" in cookie.get("domain", ""):
                    cookies[cookie["name"]] = cookie["value"]
        except WebDriverException:
            # Fall back to standard Selenium cookies
            for cookie in self.driver.get_cookies():
                cookies[cookie["name"]] = cookie["value"]
        log.debug("Extracted %d cookies from browser", len(cookies))
        return cookies

    def ensure_session(self, url: str):
        """Navigate to a URL and dismiss all gates to establish a valid session.

        Use this before bulk downloads to ensure cookies are set.
        """
        log.info("Establishing browser session via %s", url)
        self.get_page(url)
        self._ensure_age_cookie()
        cookies = self.get_cookies_dict()
        log.info("Session established, cookies: %s", list(cookies.keys()))

    def download_file(self, url: str, dest_dir: str, expected_filename: str) -> str | None:
        """Download a file using CDP to set the download directory.

        Returns the path to the downloaded file, or None if download failed.
        """
        os.makedirs(dest_dir, exist_ok=True)

        # Use CDP to set download behavior
        self.driver.execute_cdp_cmd("Page.setDownloadBehavior", {
            "behavior": "allow",
            "downloadPath": dest_dir,
        })

        self.driver.get(url)

        # Poll for download completion
        expected_path = os.path.join(dest_dir, expected_filename)
        deadline = time.time() + DOWNLOAD_TIMEOUT

        while time.time() < deadline:
            # Check if file exists and isn't still downloading
            if os.path.exists(expected_path) and os.path.getsize(expected_path) > 0:
                # Make sure no .crdownload file exists
                crdownload = expected_path + ".crdownload"
                if not os.path.exists(crdownload):
                    return expected_path

            # Also check for any .crdownload files in the directory
            crdownload_files = [f for f in os.listdir(dest_dir) if f.endswith(".crdownload")]
            if not crdownload_files and os.path.exists(expected_path):
                return expected_path

            time.sleep(0.5)

        # Timeout - check one last time
        if os.path.exists(expected_path) and os.path.getsize(expected_path) > 0:
            return expected_path

        return None

    def quit(self):
        """Shut down the browser."""
        if self.driver is not None:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None
            log.info("Browser stopped")

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.quit()
        return False
