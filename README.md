# 🎵 Media Studio Hub

[![CI / Automated Tests](https://github.com/rodolfoconcepcion/media-studio-hub/actions/workflows/ci.yml/badge.svg)](https://github.com/rodolfoconcepcion/media-studio-hub/actions/workflows/ci.yml)
[![Security & CodeQL](https://github.com/rodolfoconcepcion/media-studio-hub/actions/workflows/security.yml/badge.svg)](https://github.com/rodolfoconcepcion/media-studio-hub/actions/workflows/security.yml)
[![Latest Release](https://img.shields.io/github/v/release/rodolfoconcepcion/media-studio-hub?style=flat&color=10b981)](https://github.com/rodolfoconcepcion/media-studio-hub/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-3776ab.svg)](https://www.python.org/)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-Ingress%20Ready-41BDF5.svg)](https://www.home-assistant.io/)
[![CasaOS](https://img.shields.io/badge/CasaOS-App-black.svg)](https://casaos.io)

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

1. In Home Assistant, navigate to **Settings** ➔ **Add-ons** (or **Apps**) ➔ **Add-on Store**.
2. Click the **⋮ (top right menu)** ➔ **Repositories**.
3. Add the repository URL:
   ```text
   https://github.com/rodolfoconcepcion/media-studio-hub
   ```
4. Click **Add** ➔ **Close**, then search for **Media Studio** in the store.
5. Click **Install**, toggle **Show in sidebar**, and click **Start**.

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
