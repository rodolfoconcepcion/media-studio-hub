#!/bin/bash
# Universal Spotify Downloader with Anti-Bot Bypass & Multi-Source Fallback

URL="$1"
OUTPUT_DIR="${2:-$HOME/Music}"

if [ -z "$URL" ] || [ "$URL" = "-h" ] || [ "$URL" = "--help" ]; then
    echo "Usage: download-spotify <spotify_url> [output_directory]"
    echo "Example: download-spotify 'https://open.spotify.com/playlist/...' ~/Music"
    exit 0
fi

mkdir -p "$OUTPUT_DIR"
cd "$OUTPUT_DIR" || exit 1

echo "🎵 Downloading from Spotify with Anti-Blocking Engine: $URL"
echo "🛡️  Providers: YouTube Music -> YouTube -> SoundCloud -> Bandcamp"

INITIAL_COUNT=$(find "$OUTPUT_DIR" -type f -name "*.mp3" 2>/dev/null | wc -l)

# Multi-provider fallback and mobile client emulation to bypass bot protection
SPOTDL_ARGS=(
    --audio youtube-music youtube soundcloud bandcamp
    --threads 4
    --scan-for-songs
    --overwrite skip
    --archive "$OUTPUT_DIR/.spotdl_archive.txt"
    --yt-dlp-args "--extractor-args youtube:player_client=android,ios,web"
)

mkdir -p "$OUTPUT_DIR/_PLAYLISTS_"

if echo "$URL" | grep -q "playlist"; then
    uvx spotdl "$URL" "${SPOTDL_ARGS[@]}" --output "$OUTPUT_DIR/{artist}/{album}/{track-number} - {title}.{output-ext}" --m3u "$OUTPUT_DIR/_PLAYLISTS_/{list-name}.m3u8"
else
    uvx spotdl "$URL" "${SPOTDL_ARGS[@]}" --output "$OUTPUT_DIR/{artist}/{album}/{track-number} - {title}.{output-ext}"
fi

# Clean up empty directories
find "$OUTPUT_DIR" -type d -empty -delete 2>/dev/null || true

FINAL_COUNT=$(find "$OUTPUT_DIR" -type f -name "*.mp3" 2>/dev/null | wc -l)
(
    ACTION=$(notify-send -i audio-speakers -a "Media Downloader" -A default="Abrir Media Studio" "🎵 Descarga de Spotify Lista" "Se descargaron $NEW_SONGS nuevas canciones en ~/Music\nHaz clic para abrir Media Studio" 2>/dev/null)
    if [ -n "$ACTION" ]; then
        xdg-open "http://localhost:8888" 2>/dev/null || true
    fi
) &

echo "======================================================="
echo "✅ DESCARGA FINALIZADA"
echo "======================================================="
echo "🎵 Total canciones en tu carpeta: $FINAL_COUNT"
echo "======================================================="
