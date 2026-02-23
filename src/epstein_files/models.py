"""Data models for the Epstein Files scraper."""

from dataclasses import dataclass


@dataclass
class PdfFile:
    url: str
    dataset: int
    filename: str
    download_status: str = "pending"


@dataclass
class DatasetStatus:
    dataset: int
    discovered: int = 0
    downloaded: int = 0
    failed: int = 0
    not_found: int = 0
    pending: int = 0
