#!/usr/bin/with-contenv bashio

bashio::log.info "Starting Media Studio Home Assistant App v2.0.1..."

export DOWNLOAD_DIR="$(bashio::config 'download_dir')"
export DEFAULT_BITRATE="$(bashio::config 'default_bitrate')"
export NOTIFS="$(bashio::config 'notifications_enabled')"
export AUTO_RETRY="$(bashio::config 'auto_retry_enabled')"
export AUTO_M3U="$(bashio::config 'auto_m3u_sync')"
export AUTO_CLEAN="$(bashio::config 'auto_clean_duplicates')"
export MAX_RETRIES="$(bashio::config 'max_retries')"
export THEME="$(bashio::config 'default_theme')"
export LANG_CODE="$(bashio::config 'default_language')"

mkdir -p "$DOWNLOAD_DIR"
mkdir -p /root/.agents/media_downloader

python3 -c "
import json, os

data = {
    'download_dir': os.environ.get('DOWNLOAD_DIR', '/media/music'),
    'default_bitrate': os.environ.get('DEFAULT_BITRATE', '320k'),
    'notifications_enabled': os.environ.get('NOTIFS', 'true').lower() == 'true',
    'auto_retry_enabled': os.environ.get('AUTO_RETRY', 'true').lower() == 'true',
    'auto_m3u_sync': os.environ.get('AUTO_M3U', 'true').lower() == 'true',
    'auto_clean_duplicates': os.environ.get('AUTO_CLEAN', 'false').lower() == 'true',
    'max_retries': int(os.environ.get('MAX_RETRIES', '5') or '5'),
    'default_theme': os.environ.get('THEME', 'dark'),
    'default_language': os.environ.get('LANG_CODE', 'en')
}
os.makedirs('/root/.agents/media_downloader', exist_ok=True)
with open('/root/.agents/media_downloader/settings.json', 'w') as f:
    json.dump(data, f, indent=2)
"

bashio::log.info "Music library target configured to: $DOWNLOAD_DIR"
bashio::log.info "Launching Media Studio daemon on Ingress port 8888..."

exec python3 /app/media_server.py
