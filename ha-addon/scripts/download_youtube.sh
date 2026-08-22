#!/bin/bash
# Universal YouTube Downloader with Media Stream Engine
URL="$1"
MODE="${2:---audio}"
OUTPUT_DIR="$HOME/Music"

if [ -z "$URL" ] || [ "$URL" = "-h" ] || [ "$URL" = "--help" ]; then
    echo "Usage: download-youtube <youtube_url> [--audio | --video] [output_directory]"
    exit 0
fi

YT_ARGS=(
    --extractor-args "youtube:player_client=android,ios,web"
    --no-check-certificates
)

if [ "$MODE" = "--video" ]; then
    OUTPUT_DIR="${3:-$HOME/Videos}"
    mkdir -p "$OUTPUT_DIR"
    echo "📺 Downloading Video: $URL -> $OUTPUT_DIR"
    uvx yt-dlp "${YT_ARGS[@]}" --download-archive "$OUTPUT_DIR/.yt_archive.txt" --no-overwrites -f "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best" -o "$OUTPUT_DIR/%(title)s.%(ext)s" "$URL"
else
    OUTPUT_DIR="${3:-$HOME/Music}"
    mkdir -p "$OUTPUT_DIR/_PLAYLISTS_"
    mkdir -p "$OUTPUT_DIR/_UNKNOWN_"
    echo "🎵 Extracting Audio (MP3 320k): $URL -> $OUTPUT_DIR"
    uvx yt-dlp "${YT_ARGS[@]}" --download-archive "$OUTPUT_DIR/.yt_archive.txt" --no-overwrites -x --audio-format mp3 --audio-quality 0 --embed-thumbnail --add-metadata \
        -o "$OUTPUT_DIR/%(artist,creator,uploader,NA)s/%(album,playlist_title,Single)s/%(track_number,playlist_index|01)02d - %(title)s.%(ext)s" "$URL"
fi

(
    ACTION=$(notify-send -i audio-speakers -a "Media Downloader" -A default="Abrir Media Studio" "📺 Descarga de YouTube Lista" "Descarga completada en $OUTPUT_DIR\nHaz clic para abrir Media Studio" 2>/dev/null)
    if [ -n "$ACTION" ]; then
        xdg-open "http://localhost:8888" 2>/dev/null || true
    fi
) &
echo "✅ Finished downloading YouTube media!"
