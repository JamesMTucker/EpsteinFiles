FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common \
    gpg-agent \
    curl \
    && add-apt-repository ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y --no-install-recommends \
    python3.13 \
    python3.13-venv \
    python3.13-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Google Chrome (Ubuntu 24.04's chromium packages are snap-based and broken in Docker)
RUN curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
    | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
    > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update && apt-get install -y --no-install-recommends google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# Selenium Manager (bundled with selenium 4.x) auto-downloads the matching chromedriver
ENV CHROME_BIN=/usr/bin/google-chrome-stable

WORKDIR /app

# Create and activate virtual environment
RUN python3.13 -m venv /venv
ENV PATH="/venv/bin:$PATH"

# Copy project files and install
COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

# Volume for persistent data storage
VOLUME /app/data

ENTRYPOINT ["epstein-files"]
CMD ["--help"]
