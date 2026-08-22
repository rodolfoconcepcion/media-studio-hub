FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Install system utilities, ffmpeg and audio toolchains
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    bash \
    curl \
    jq \
    git \
    libnotify-bin \
    alsa-utils \
    && rm -rf /var/lib/apt/lists/*

# Install Python audio libraries & CLI engines
RUN pip install --no-cache-dir \
    yt-dlp \
    spotdl \
    mutagen

WORKDIR /app

# Copy scripts, templates and server
COPY media_server.py /app/media_server.py
COPY templates /app/templates
COPY scripts /root/.agents/skills/media-downloader/scripts
RUN chmod +x /root/.agents/skills/media-downloader/scripts/*.sh

EXPOSE 8888

CMD ["python3", "/app/media_server.py"]
