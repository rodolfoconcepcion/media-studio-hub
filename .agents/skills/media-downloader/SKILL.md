---
name: media-downloader
description: Download audio, songs, playlists, albums, and videos from YouTube, Spotify, SoundCloud, Bandcamp, TikTok, and other platforms. Supports 320kbps MP3 extraction, lossless FLAC, video downloads up to 4K, album artwork tagging, M3U8 auto-sync playlist generation, real-time download job controls (pause/resume/cancel), and Media Studio web dashboard.
---

# Media Downloader & Control Studio

## Features
- **YouTube & Spotify Downloader:** 320kbps MP3s with official embedded ID3 album artwork and metadata.
- **Media Studio Web Control Center:** Web app running on `http://localhost:8888` (or command `media-server open`).
  - **Metrics Dashboard & View Navigation:** 4 primary view modes:
    - ⚡ **Studio & Queue:** Active download progress, job controls (Pause/Resume/Restart/Cancel), and live track triage.
    - 📁 **Music Folder Explorer:** Interactive file and folder browser of `~/Music/` with live search, column sorting, dynamic filters (Artist, Album, Bitrate, Duration, Format), Artist & Album collections, and direct in-browser / VLC playback.
    - 👯 **Duplicates & Cleaner:** Smart fuzzy duplicate and similar track detector. Compares acoustic similarity and tag variations across single vs album releases, side-by-side listening, and 1-click **`⚡ Auto-Clean All`** (keeping the highest quality audio file).
    - 📜 **Download History & Analytics:** Persistent download records (`history.json`), all-time statistics (success rate %, total tracks acquired, duration, retries), and 1-click **`🔄 Re-Download`** buttons.
  - **ID3 Metadata Editor & Online Fetcher:** Search official music databases (iTunes / Apple Music) with 1-click auto-fill for Track Title, Artist, Album, Genre, Year, Track Number, and 1000x1000 HD Cover Artwork.
  - **Download Queue & Job Controls:** Direct UI URL submission, Auto-Scheduler with smart background retry backoff, and **Pause (`SIGSTOP`)**, **Resume (`SIGCONT`)**, **Restart (`🔄`)**, and **Cancel (`SIGKILL`)** actions.
  - **Real-Time M3U8 Playlist Sync:** Automatically generates and updates `playlist.m3u8` for downloaded playlists/albums.
  - **1-Click VLC Integration:** Launch single songs, albums, or entire playlists directly in VLC (`/api/open_playlist_vlc`).
  - **Sticky In-Browser Streaming Player:** HD Album cover art, continuous sequential auto-play, media queue drawer, and minimize/expand floating pill controls.
  - **Bilingual & Themes:** English / Spanish toggle + Dark / Light theme with persistent `localStorage` settings.

## Quick CLI Usage
```bash
# Manage Media Server Daemon
media-server status     # Check daemon status
media-server restart    # Restart server
media-server open       # Open Web Studio in browser (http://localhost:8888)

# Download Spotify track, album, or playlist to ~/Music/
~/.agents/skills/media-downloader/scripts/download_spotify.sh "<spotify_url>"

# Download YouTube video or audio
~/.agents/skills/media-downloader/scripts/download_youtube.sh "<youtube_url>" --audio
```
