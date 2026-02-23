"""Constants and configuration for the Epstein Files scraper."""

import os

BASE_URL = "https://www.justice.gov"
DATASETS = range(1, 13)
DATASET_URL_TEMPLATE = BASE_URL + "/epstein/doj-disclosures/data-set-{dataset}-files"

DEFAULT_OUTPUT_DIR = os.path.join(os.getcwd(), "data")
DB_FILENAME = "epstein_files.db"

DOWNLOAD_TIMEOUT = 120
SCRAPE_DELAY = 0.5
BROWSER_RESTART_INTERVAL = 100

EXTRA_FILES = [
    {
        "url": "https://www.justice.gov/media/1426281/dl?inline",
        "subdir": "memoranda",
        "filename": "EFTA_First_Level_Review_Protocol.pdf",
    },
]
