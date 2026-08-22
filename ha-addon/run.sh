#!/usr/bin/with-contenv bashio

bashio::log.info "Starting Media Studio Home Assistant Add-on..."

DOWNLOAD_DIR=$(bashio::config 'download_dir')
DEFAULT_BITRATE=$(bashio::config 'default_bitrate')
NOTIFS=$(bashio::config 'notifications_enabled')

mkdir -p "$DOWNLOAD_DIR"
mkdir -p /root/.agents/media_downloader

python3 -c "
import json
data = {
  'download_dir': '${DOWNLOAD_DIR}',
  'default_bitrate': '${DEFAULT_BITRATE}',
  'notifications_enabled': ${NOTIFS},
  'auto_retry_enabled': True,
  'auto_m3u_sync': True,
  'default_language': 'en',
  'default_theme': 'dark',
  'max_retries': 5,
  'auto_clean_duplicates': False
}
with open('/root/.agents/media_downloader/settings.json', 'w') as f:
    json.dump(data, f, indent=2)
"

bashio::log.info "Music library target configured to: $DOWNLOAD_DIR"
bashio::log.info "Launching Media Studio daemon on Ingress port 8888..."

exec python3 /app/media_server.py
