"""SQLite database layer for tracking scrape and download state."""

import os
import re
import sqlite3
from datetime import datetime, timezone

from .config import DB_FILENAME
from .models import DatasetStatus, PdfFile


class Database:
    def __init__(self, output_dir: str):
        self.db_path = os.path.join(output_dir, DB_FILENAME)
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            self._conn = sqlite3.connect(self.db_path)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def initialize(self):
        """Create all tables and indexes."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS scrape_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                dataset INTEGER,
                pages_scraped INTEGER DEFAULT 0,
                urls_discovered INTEGER DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'running'
            );

            CREATE TABLE IF NOT EXISTS pdf_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT UNIQUE NOT NULL,
                dataset INTEGER NOT NULL,
                filename TEXT NOT NULL,
                discovered_at TEXT NOT NULL,
                scrape_run_id INTEGER REFERENCES scrape_runs(id),
                last_seen_run_id INTEGER REFERENCES scrape_runs(id),
                download_status TEXT NOT NULL DEFAULT 'pending',
                downloaded_at TEXT,
                file_size INTEGER,
                error_message TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_pdf_files_dataset
                ON pdf_files(dataset);
            CREATE INDEX IF NOT EXISTS idx_pdf_files_download_status
                ON pdf_files(download_status);

            CREATE TABLE IF NOT EXISTS extra_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT UNIQUE NOT NULL,
                subdir TEXT NOT NULL,
                filename TEXT NOT NULL,
                download_status TEXT NOT NULL DEFAULT 'pending',
                downloaded_at TEXT,
                file_size INTEGER,
                error_message TEXT
            );
        """)
        self.conn.commit()

        # Migration: add last_seen_run_id to existing databases
        cols = {row[1] for row in self.conn.execute("PRAGMA table_info(pdf_files)").fetchall()}
        if "last_seen_run_id" not in cols:
            self.conn.execute("ALTER TABLE pdf_files ADD COLUMN last_seen_run_id INTEGER REFERENCES scrape_runs(id)")
            self.conn.commit()

    def start_scrape_run(self, dataset: int | None = None) -> int:
        """Start a new scrape run and return its ID."""
        now = datetime.now(timezone.utc).isoformat()
        cur = self.conn.execute(
            "INSERT INTO scrape_runs (started_at, dataset, status) VALUES (?, ?, 'running')",
            (now, dataset),
        )
        self.conn.commit()
        return cur.lastrowid

    def finish_scrape_run(self, run_id: int, pages_scraped: int, urls_discovered: int, status: str = "completed"):
        """Mark a scrape run as finished."""
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "UPDATE scrape_runs SET finished_at=?, pages_scraped=?, urls_discovered=?, status=? WHERE id=?",
            (now, pages_scraped, urls_discovered, status, run_id),
        )
        self.conn.commit()

    def upsert_pdf(self, url: str, dataset: int, filename: str, scrape_run_id: int) -> bool:
        """Insert a PDF URL if not already known; update last_seen_run_id if it is. Returns True if newly inserted."""
        now = datetime.now(timezone.utc).isoformat()
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO pdf_files (url, dataset, filename, discovered_at, scrape_run_id, last_seen_run_id) VALUES (?, ?, ?, ?, ?, ?)",
            (url, dataset, filename, now, scrape_run_id, scrape_run_id),
        )
        is_new = cur.rowcount > 0
        if not is_new:
            self.conn.execute(
                "UPDATE pdf_files SET last_seen_run_id=? WHERE url=?",
                (scrape_run_id, url),
            )
        self.conn.commit()
        return is_new

    def get_pending_downloads(self, dataset: int | None = None) -> list[PdfFile]:
        """Get all URLs with pending download status."""
        if dataset is not None:
            rows = self.conn.execute(
                "SELECT url, dataset, filename, download_status FROM pdf_files WHERE download_status='pending' AND dataset=? ORDER BY id",
                (dataset,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT url, dataset, filename, download_status FROM pdf_files WHERE download_status='pending' ORDER BY id",
            ).fetchall()
        return [PdfFile(url=r["url"], dataset=r["dataset"], filename=r["filename"], download_status=r["download_status"]) for r in rows]

    def get_failed_downloads(self, dataset: int | None = None) -> list[PdfFile]:
        """Get all URLs with failed download status."""
        if dataset is not None:
            rows = self.conn.execute(
                "SELECT url, dataset, filename, download_status FROM pdf_files WHERE download_status='failed' AND dataset=? ORDER BY id",
                (dataset,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT url, dataset, filename, download_status FROM pdf_files WHERE download_status='failed' ORDER BY id",
            ).fetchall()
        return [PdfFile(url=r["url"], dataset=r["dataset"], filename=r["filename"], download_status=r["download_status"]) for r in rows]

    def mark_downloaded(self, url: str, file_size: int):
        """Mark a URL as successfully downloaded."""
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "UPDATE pdf_files SET download_status='downloaded', downloaded_at=?, file_size=?, error_message=NULL WHERE url=?",
            (now, file_size, url),
        )
        self.conn.commit()

    def mark_failed(self, url: str, error_message: str):
        """Mark a URL as failed to download."""
        self.conn.execute(
            "UPDATE pdf_files SET download_status='failed', error_message=? WHERE url=?",
            (error_message, url),
        )
        self.conn.commit()

    def mark_not_found(self, url: str):
        """Mark a URL as 404 not found."""
        self.conn.execute(
            "UPDATE pdf_files SET download_status='not_found', error_message='404 not found' WHERE url=?",
            (url,),
        )
        self.conn.commit()

    def reset_failed(self, dataset: int | None = None):
        """Reset all failed downloads back to pending."""
        if dataset is not None:
            self.conn.execute(
                "UPDATE pdf_files SET download_status='pending', error_message=NULL WHERE download_status='failed' AND dataset=?",
                (dataset,),
            )
        else:
            self.conn.execute(
                "UPDATE pdf_files SET download_status='pending', error_message=NULL WHERE download_status='failed'",
            )
        self.conn.commit()

    def get_status_summary(self, dataset: int | None = None) -> list[DatasetStatus]:
        """Get download status counts per dataset."""
        if dataset is not None:
            rows = self.conn.execute(
                """SELECT dataset, download_status, COUNT(*) as cnt
                   FROM pdf_files WHERE dataset=? GROUP BY dataset, download_status ORDER BY dataset""",
                (dataset,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """SELECT dataset, download_status, COUNT(*) as cnt
                   FROM pdf_files GROUP BY dataset, download_status ORDER BY dataset""",
            ).fetchall()

        datasets: dict[int, DatasetStatus] = {}
        for row in rows:
            ds = row["dataset"]
            if ds not in datasets:
                datasets[ds] = DatasetStatus(dataset=ds)
            status = datasets[ds]
            count = row["cnt"]
            status.discovered += count
            match row["download_status"]:
                case "downloaded":
                    status.downloaded = count
                case "failed":
                    status.failed = count
                case "not_found":
                    status.not_found = count
                case "pending":
                    status.pending = count

        return [datasets[ds] for ds in sorted(datasets)]

    def get_new_files(self, run_id: int, dataset: int | None = None) -> list[PdfFile]:
        """Return URLs first discovered in the given scrape run."""
        if dataset is not None:
            rows = self.conn.execute(
                "SELECT url, dataset, filename, download_status FROM pdf_files WHERE scrape_run_id=? AND dataset=? ORDER BY id",
                (run_id, dataset),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT url, dataset, filename, download_status FROM pdf_files WHERE scrape_run_id=? ORDER BY id",
                (run_id,),
            ).fetchall()
        return [PdfFile(url=r["url"], dataset=r["dataset"], filename=r["filename"], download_status=r["download_status"]) for r in rows]

    def get_removed_files(self, run_id: int, dataset: int | None = None) -> list[PdfFile]:
        """Return URLs not seen in the given scrape run (potentially removed from site).

        Excludes files already marked not_found (known-gone from a prior download attempt).
        """
        if dataset is not None:
            rows = self.conn.execute(
                """SELECT url, dataset, filename, download_status FROM pdf_files
                   WHERE last_seen_run_id != ? AND last_seen_run_id IS NOT NULL
                   AND download_status != 'not_found'
                   AND dataset=?
                   ORDER BY id""",
                (run_id, dataset),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """SELECT url, dataset, filename, download_status FROM pdf_files
                   WHERE last_seen_run_id != ? AND last_seen_run_id IS NOT NULL
                   AND download_status != 'not_found'
                   ORDER BY id""",
                (run_id,),
            ).fetchall()
        return [PdfFile(url=r["url"], dataset=r["dataset"], filename=r["filename"], download_status=r["download_status"]) for r in rows]

    def upsert_extra_file(self, url: str, subdir: str, filename: str):
        """Insert an extra file if not already known."""
        self.conn.execute(
            "INSERT OR IGNORE INTO extra_files (url, subdir, filename) VALUES (?, ?, ?)",
            (url, subdir, filename),
        )
        self.conn.commit()

    def get_pending_extra_files(self) -> list[dict]:
        """Get extra files that haven't been downloaded yet."""
        rows = self.conn.execute(
            "SELECT url, subdir, filename FROM extra_files WHERE download_status='pending'",
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_extra_downloaded(self, url: str, file_size: int):
        """Mark an extra file as successfully downloaded."""
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "UPDATE extra_files SET download_status='downloaded', downloaded_at=?, file_size=? WHERE url=?",
            (now, file_size, url),
        )
        self.conn.commit()

    def mark_extra_failed(self, url: str, error_message: str):
        """Mark an extra file as failed."""
        self.conn.execute(
            "UPDATE extra_files SET download_status='failed', error_message=? WHERE url=?",
            (error_message, url),
        )
        self.conn.commit()

    def import_from_url_list(self, filepath: str, scrape_run_id: int) -> int:
        """Import URLs from an existing all_urls.txt file. Returns count of imported URLs."""
        count = 0
        with open(filepath) as f:
            for line in f:
                url = line.strip()
                if not url:
                    continue
                match = re.search(r"DataSet%20(\d+)/", url)
                if match:
                    dataset = int(match.group(1))
                    filename = url.split("/")[-1]
                    if self.upsert_pdf(url, dataset, filename, scrape_run_id):
                        count += 1
        return count
