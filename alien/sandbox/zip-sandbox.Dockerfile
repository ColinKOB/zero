# Zip sandbox image: the container each Zip's terminal, code runner and file
# tools run in, under each home's rootless Docker on alien. Built once by the
# owner's daemon and saved to /srv/zero/images for the other homes to load.
# Build on alien, from this folder (readfile is copied in):
#   docker build -f zip-sandbox.Dockerfile -t taffy/zip-sandbox:<date>-v<n> .
# New versions get new tags; a tag is never overwritten.
# No Hermes code, browser, desktop, secrets, or logged-in CLIs go in here.

FROM ghcr.io/astral-sh/uv:latest AS uv

FROM nikolaik/python-nodejs:python3.13-nodejs22

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      git curl wget jq ripgrep fd-find ffmpeg imagemagick zip unzip rsync tree \
      procps less nano file sqlite3 openssh-client ca-certificates tzdata \
      locales sudo gnupg \
      libnss3 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 libasound2t64 \
      libxrandr2 libxkbcommon0 libxfixes3 libxcomposite1 libxdamage1 \
      libgbm1 libcups2t64 libcairo2 libpango-1.0-0 fonts-liberation \
 && install -m 0755 -d /etc/apt/keyrings \
 && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
 && chmod a+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends gh \
 && ln -sf /usr/bin/fdfind /usr/local/bin/fd \
 && sed -i 's/^# *en_US.UTF-8/en_US.UTF-8/' /etc/locale.gen && locale-gen \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /uvx /usr/local/bin/

RUN pip install --no-cache-dir pillow requests beautifulsoup4

# Reading files (Nov 10 2026): PDF, Word, PowerPoint, Excel, old Office files and scans, all through `readfile`.
# Their own layers after the others, so a rebuild reuses everything above.
RUN apt-get update \
 && apt-get install -y --no-install-recommends poppler-utils tesseract-ocr tesseract-ocr-eng catdoc \
 && apt-get clean && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir python-docx python-pptx openpyxl
COPY readfile /usr/local/bin/readfile
RUN chmod 0755 /usr/local/bin/readfile && readfile --help >/dev/null

# HOME and TZ are set per home by the profile's docker_env (zero-home-render), so the image names nobody.
RUN mkdir -p /home/zip
ENV HOME=/home/zip \
    LANG=en_US.UTF-8 \
    LC_ALL=en_US.UTF-8

LABEL org.opencontainers.image.title="taffy/zip-sandbox" \
      org.opencontainers.image.description="Terminal sandbox for Zero's Zips"
