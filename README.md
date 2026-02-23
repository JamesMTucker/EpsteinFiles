# The Epstein Files

Scraper and downloader for the DOJ Epstein Files disclosures (Data Sets 1-12).

Uses Selenium (headless Chromium) to maintain a live browser session, avoiding token expiration issues. A SQLite database tracks scrape/download state so interrupted operations resume cleanly.

## Roadmap

- [x] Implement scraping of PDF URLs from the DOJ website
- [x] Analyze PDF with Llava-CoT for downstream processing
- [ ] OCR PDFs with Nanonets v2 for structured data extraction
- [ ] Inference redacted inline text for list of candidates (names, locations, dates) using LLaMA-3.1-70B-Instruct
- [ ] Generate knowledge graph of entities and relationships using LLaMA-3.1-70B-Instruct

## Usage

### Install locally

```bash
pip install .
```

### Commands

```bash
# Discover PDF URLs from the DOJ website
epstein-files scrape
epstein-files scrape -d 1          # scrape only Data Set 1

# Download discovered PDFs
epstein-files download
epstein-files download -d 1        # download only Data Set 1
epstein-files download --retry-failed

# View status summary
epstein-files status
epstein-files status -d 1
```

### Docker

```bash
docker build -t epstein-files .

# Scrape
docker run -v $(pwd)/data:/app/data epstein-files scrape

# Download
docker run -v $(pwd)/data:/app/data epstein-files download

# Status
docker run -v $(pwd)/data:/app/data epstein-files status
```

## Project Structure

```
src/epstein_files/
  cli.py           # Click CLI entry point
  config.py        # Constants (URLs, delays, etc.)
  db.py            # SQLite database layer
  models.py        # Dataclasses
  browser.py       # Selenium browser management
  scraper.py       # Page scraping (Selenium + BS4)
  downloader.py    # PDF download logic (Selenium CDP)
```

Downloaded files are stored in `data/` (gitignored), organized as `data/DataSet_N/`.
