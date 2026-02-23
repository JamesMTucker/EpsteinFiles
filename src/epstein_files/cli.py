"""Click CLI entry point for the Epstein Files scraper."""

import logging
import os
import sys

import click

from .browser import BrowserManager
from .config import DEFAULT_OUTPUT_DIR
from .db import Database
from .downloader import Downloader
from .scraper import Scraper


@click.group()
@click.option("--output-dir", "-o", default=DEFAULT_OUTPUT_DIR, help="Output directory for downloaded files.", show_default=True)
@click.option("--verbose", "-v", is_flag=True, default=False, help="Enable verbose (DEBUG) logging.")
@click.pass_context
def cli(ctx, output_dir, verbose):
    """Scrape and download PDF files from the DOJ Epstein Files disclosures."""
    ctx.ensure_object(dict)
    ctx.obj["output_dir"] = os.path.abspath(output_dir)

    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


@cli.command()
@click.option("--dataset", "-d", type=int, default=None, help="Scrape only this dataset number (1-12).")
@click.pass_context
def scrape(ctx, dataset):
    """Discover PDF URLs from the DOJ website and store them in the database."""
    output_dir = ctx.obj["output_dir"]
    db = Database(output_dir)
    db.initialize()

    # Check for legacy all_urls.txt to import
    url_list_path = os.path.join(output_dir, "all_urls.txt")
    if os.path.exists(url_list_path) and os.path.getsize(url_list_path) > 0:
        run_id = db.start_scrape_run(dataset=None)
        count = db.import_from_url_list(url_list_path, run_id)
        if count > 0:
            print(f"Imported {count} URLs from existing all_urls.txt")
            db.finish_scrape_run(run_id, pages_scraped=0, urls_discovered=count)
        else:
            db.finish_scrape_run(run_id, pages_scraped=0, urls_discovered=0)

    run_id = db.start_scrape_run(dataset=dataset)

    print("=" * 60)
    if dataset:
        print(f"Scraping Data Set {dataset}...")
    else:
        print("Scraping all 12 data sets...")
    print("=" * 60)
    sys.stdout.flush()

    browser = BrowserManager()
    try:
        browser.start()
        scraper = Scraper(db, browser)

        if dataset:
            pages, urls = scraper.scrape_dataset(dataset, run_id)
        else:
            pages, urls = scraper.scrape_all(run_id)

        db.finish_scrape_run(run_id, pages_scraped=pages, urls_discovered=urls)
        print(f"\nScrape complete: {pages} pages scraped, {urls} new URLs discovered")
    except Exception as e:
        db.finish_scrape_run(run_id, pages_scraped=0, urls_discovered=0, status="failed")
        print(f"\nScrape failed: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        browser.quit()
        db.close()


@cli.command()
@click.option("--dataset", "-d", type=int, default=None, help="Download only this dataset number (1-12).")
@click.option("--retry-failed", is_flag=True, default=False, help="Retry previously failed downloads.")
@click.pass_context
def download(ctx, dataset, retry_failed):
    """Download PDFs from URLs stored in the database."""
    output_dir = ctx.obj["output_dir"]
    db = Database(output_dir)
    db.initialize()

    browser = BrowserManager()
    try:
        browser.start()
        downloader = Downloader(db, browser, output_dir)

        print("=" * 60)
        print("Downloading extra files...")
        print("=" * 60)
        downloader.download_extras()

        print("\n" + "=" * 60)
        if dataset:
            print(f"Downloading Data Set {dataset}...")
        else:
            print("Downloading all data sets...")
        print("=" * 60)
        sys.stdout.flush()

        downloader.download_all(dataset=dataset, retry_failed=retry_failed)
    except Exception as e:
        print(f"\nDownload failed: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        browser.quit()
        db.close()


@cli.command()
@click.option("--dataset", "-d", type=int, default=None, help="Show status for only this dataset number (1-12).")
@click.pass_context
def status(ctx, dataset):
    """Show download status summary."""
    output_dir = ctx.obj["output_dir"]
    db = Database(output_dir)
    db.initialize()

    try:
        summaries = db.get_status_summary(dataset)

        if not summaries:
            print("No data in database. Run 'scrape' first.")
            return

        print(f"{'Dataset':>10} {'Discovered':>12} {'Downloaded':>12} {'Pending':>10} {'Failed':>10} {'404':>8}")
        print("-" * 66)

        total = DatasetStatusTotals()
        for s in summaries:
            print(f"{'Set ' + str(s.dataset):>10} {s.discovered:>12} {s.downloaded:>12} {s.pending:>10} {s.failed:>10} {s.not_found:>8}")
            total.discovered += s.discovered
            total.downloaded += s.downloaded
            total.pending += s.pending
            total.failed += s.failed
            total.not_found += s.not_found

        print("-" * 66)
        print(f"{'TOTAL':>10} {total.discovered:>12} {total.downloaded:>12} {total.pending:>10} {total.failed:>10} {total.not_found:>8}")
        print(f"\nDatabase: {db.db_path}")
        print(f"Output:   {output_dir}")
    finally:
        db.close()


class DatasetStatusTotals:
    def __init__(self):
        self.discovered = 0
        self.downloaded = 0
        self.pending = 0
        self.failed = 0
        self.not_found = 0
