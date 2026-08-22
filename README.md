# 🎵 Media Studio

A self-hosted personal media downloader, in-browser audio player, ID3 tag editor, duplicate cleaner, and music library manager with native Home Assistant Ingress Add-on and CasaOS Docker support.

---

## ✨ Features

- **⚡ Multi-Engine Extraction**: High-performance audio and video stream extraction powered by `yt-dlp` and `spotdl`.
- **⚙️ Centralized Settings**: Configure download paths, desktop notifications, bitrates (320k/256k/FLAC/192k), and auto-retry rules directly from the web UI.
- **🔔 Desktop & Browser Notifications**: Instant alerts when downloads or scheduled playlist retries finish (fully toggleable).
- **🔁 Auto-Schedule & Smart Retry**: Continuously retries missing tracks in the background until 100% of large playlists are completed.
- **👯 Duplicates & Quality Cleaner**: Detects duplicate tracks, compares bitrates, and cleans redundant lower-quality versions with 1 click.
- **🎶 In-Browser Player & ID3 Tag Editor**: Web player with waveform progress, cover art preview, and EasyID3 tag editing.
- **🏠 Home Assistant Ingress Add-on**: Embeds directly into the Home Assistant sidebar without exposing open ports.
- **📦 CasaOS / ZimaOS App**: 1-click Docker Compose manifest for personal cloud servers.
- **🧪 Automated Test Suite**: 100% passing unit & integration tests covering APIs, threading locks, and mathematical aggregations.

---

## 🚀 Quick Start (Local Workstation)

```bash
# Start server
python3 media_server.py

# Open web interface
http://localhost:8888
```

---

## 🏠 Home Assistant Add-on Installation

1. Copy the `ha-addon` folder into your Home Assistant `/addons/media_studio` directory.
2. In Home Assistant, navigate to **Settings -> Apps -> App store -> ⋮ (top right) -> Check for Updates**.
3. Install **Media Studio** and click **Start**.
4. Enable **Show in sidebar** for seamless Ingress access.

---

## 📦 CasaOS Installation

1. Open CasaOS App Store -> **Custom Install**.
2. Import the `casaos/docker-compose.yml` file.
3. Set your storage paths (e.g. `/DATA/Media/Music`).
4. Click **Install**.

---

## 🧪 Running Automated Tests

```bash
python3 tests/test_media_server.py
```

---

## ⚖️ Legal & Fair-Use Compliance

This software is provided strictly for educational purposes, personal interoperability, and fair-use format shifting of media content that the user has the lawful right to access. 

- This software **does not host, store, or distribute** any copyrighted audio or video media.
- This software **does not decrypt, bypass, or circumvent** Digital Rights Management (DRM) or technical access controls.
- All media stream processing is executed locally on the user's hardware via established open-source toolchains (`ffmpeg`, `yt-dlp`).
- Users are solely responsible for complying with applicable local laws and third-party platform terms of service.
