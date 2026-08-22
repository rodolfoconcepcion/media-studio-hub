#!/bin/bash
# Universal Spotify Downloader with Multi-Source Audio Extraction Engine

URL="$1"
OUTPUT_DIR="${2:-$HOME/Music}"

if [ -z "$URL" ] || [ "$URL" = "-h" ] || [ "$URL" = "--help" ]; then
    echo "Usage: download-spotify <spotify_url> [output_directory]"
    echo "Example: download-spotify 'https://open.spotify.com/playlist/...' ~/Music"
    exit 0
fi

mkdir -p "$OUTPUT_DIR"
cd "$OUTPUT_DIR" || exit 1

echo "🎵 Downloading from Spotify with Media Stream Engine: $URL"
echo "🛡️  Providers: YouTube Music -> YouTube -> SoundCloud -> Bandcamp"

INITIAL_COUNT=$(find "$OUTPUT_DIR" -type f -name "*.mp3" 2>/dev/null | wc -l)

# Multi-provider fallback and mobile client emulation for speed and compatibility
SPOTDL_ARGS=(
    --audio youtube-music youtube soundcloud bandcamp
    --threads 4
    --overwrite skip
    --archive "$OUTPUT_DIR/.spotdl_archive.txt"
    --headless
)

mkdir -p "$OUTPUT_DIR/_PLAYLISTS_"

if echo "$URL" | grep -q "playlist"; then
    uvx spotdl "$URL" "${SPOTDL_ARGS[@]}" --output "$OUTPUT_DIR/{artist}/{album}/{track-number} - {title}.{output-ext}" --m3u "$OUTPUT_DIR/_PLAYLISTS_/{list-name}.m3u8"
else
    uvx spotdl "$URL" "${SPOTDL_ARGS[@]}" --output "$OUTPUT_DIR/{artist}/{album}/{track-number} - {title}.{output-ext}"
fi

# Post-processing: ensure clean metadata organization
TARGET_DIR="$OUTPUT_DIR" python3 << 'PYEOF' 2>/dev/null || true
import os, subprocess, json, shutil, re

base = os.environ.get("TARGET_DIR", os.path.expanduser("~/Music"))
playlists_dir = os.path.join(base, "_PLAYLISTS_")
misc_dir = os.path.join(base, "_UNKNOWN_")
os.makedirs(playlists_dir, exist_ok=True)
os.makedirs(misc_dir, exist_ok=True)

def clean_name(s):
    if not s: return ""
    s = s.replace("’", "'").replace("‘", "'").replace("“", "'").replace("”", "'").replace('"', "'")
    s = re.sub(r'[\\/*?:"<>|]', "", s).strip()
    return s

for root, dirs, files in os.walk(base):
    if os.path.abspath(root).startswith(playlists_dir) or os.path.abspath(root).startswith(misc_dir):
        continue
    for f in files:
        if f.endswith((".mp3", ".m4a", ".flac")):
            full_path = os.path.join(root, f)
            try:
                cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", full_path]
                res = subprocess.run(cmd, capture_output=True, text=True)
                data = json.loads(res.stdout)
                tags = data.get("format", {}).get("tags", {})
                
                artist = clean_name(tags.get("artist") or tags.get("album_artist") or tags.get("ARTIST"))
                album = clean_name(tags.get("album") or tags.get("ALBUM"))
                title = clean_name(tags.get("title") or tags.get("TITLE") or os.path.splitext(f)[0])
                
                if not artist:
                    target_dir = misc_dir
                    target_file = os.path.join(target_dir, f"{title}{os.path.splitext(f)[1]}")
                else:
                    raw_track = str(tags.get("track", "1")).split("/")[0].strip()
                    try:
                        track_num = f"{int(raw_track):02d}"
                    except:
                        track_num = "01"
                        
                    target_dir = os.path.join(base, artist, album or "Single")
                    os.makedirs(target_dir, exist_ok=True)
                    ext = os.path.splitext(f)[1]
                    target_file = os.path.join(target_dir, f"{track_num} - {title}{ext}")
                
                if full_path != target_file:
                    if os.path.exists(target_file):
                        os.remove(full_path)
                    else:
                        shutil.move(full_path, target_file)
            except:
                pass

# Clean empty directories
for root, dirs, files in os.walk(base, topdown=False):
    if root == base or root == playlists_dir or root == misc_dir:
        continue
    if not os.listdir(root):
        try: os.rmdir(root)
        except: pass
PYEOF

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
echo "📁 Nuevas canciones añadidas: $NEW_SONGS"
echo "🎵 Total canciones en tu carpeta: $FINAL_COUNT"
echo "======================================================="
