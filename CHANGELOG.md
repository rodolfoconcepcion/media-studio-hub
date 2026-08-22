# Changelog

All notable changes to **Media Studio Hub** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.1] - 2026-08-22

### Added
- **Interactive Downloaded Tracks Drawer**: Each download queue card now includes an expandable accordion allowing users to inspect, browse, and play downloaded tracks directly in the browser or via VLC.
- **Accurate Per-Job Progress Counter**: Track downloads are now tallied strictly against matching release tracks instead of aggregate directory file count.
- **Explicit Bash Script Invocation**: Enforced `bash` execution for `download_spotify.sh` and `download_youtube.sh` across all environments.

### Fixed
- Fixed bug where jobs reaching maximum auto-retries were falsely marked as `completed` even with 0 tracks downloaded; introduced `failed` and `partial` statuses.
- Added missing CSS badge styles for `.status-failed` and `.status-partial`.

---

## [1.0.0] - 2026-08-22

### Added
- Initial production release of Media Studio Hub.
- Multi-source audio extraction engine (`yt-dlp` and `spotdl`).
- In-browser sticky audio player with waveform and cover art display.
- ID3 tag editor and duplicate track detection cleaner.
- Home Assistant Ingress Add-on configuration and CasaOS app manifest.
- Automated test suite with Unit, Integration, and Playwright E2E browser tests.
