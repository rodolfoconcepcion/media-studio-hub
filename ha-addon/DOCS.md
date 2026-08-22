# Home Assistant App: Media Studio

**Media Studio** is an all-in-one universal media downloading, music tagging, streaming, and playlist studio designed natively for Home Assistant OS and Supervised environments with complete Ingress support.

---

## ✨ Features

- 🎵 **Universal Downloader**: Extract high-bitrate audio (320kbps MP3, lossless FLAC) and 4K video from online media streams, audio links, and public video sources via standard open-source toolchains.
- ⚡ **Ingress & Native HA Sidebar**: Seamlessly integrated into the Home Assistant sidebar without port forwarding or SSL certificate complexities.
- 🏷️ **Built-in ID3 Tag Editor**: Edit metadata, song titles, artists, albums, genres, release years, and embed custom album artwork directly into audio files.
- 📜 **Auto-Sync M3U Playlists**: Automatically maintains and synchronizes `.m3u8` playlists for your local music collection.
- 📂 **Direct Media Storage Access**: Saves files directly into `/media/music` or `/share` for immediate playback in Home Assistant Media Browser, Music Assistant, Plex, or DLNA/Sonos players.
- 🎨 **Responsive Dark/Light Studio UI**: Real-time progress bars, download speed metrics, job pause/resume/cancel controls, and batch downloads.

---

## 🚀 Installation & Setup

1. Open **Home Assistant** ➔ **Settings** ➔ **Apps** ➔ **Install App**.
2. Refresh the store catalog and locate **Media Studio** under **Local Apps**.
3. Click **Install**.
4. Configure your preferred options in the **Configuration** tab (see below).
5. Toggle **Start on boot**, **Watchdog**, and **Show in sidebar**.
6. Click **Start** and launch the studio from your sidebar.

---

## ⚙️ Configuration Options

| Option | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `download_dir` | string | `/media/music` | Destination directory on Home Assistant storage where downloaded media will be stored. |
| `default_bitrate` | string | `320k` | Audio bitrate encoding quality (`320k`, `256k`, `192k`, `flac`). |
| `notifications_enabled` | boolean | `true` | Send live push notifications upon download completion or error. |
| `auto_retry_enabled` | boolean | `true` | Automatically retry interrupted network transfers up to 5 times. |
| `auto_m3u_sync` | boolean | `true` | Automatically regenerate M3U playlists when albums/tracks are downloaded. |
| `default_theme` | string | `dark` | Default dashboard appearance (`dark` or `light`). |

---

## 📁 Storage Integration

- **Home Assistant Media Browser:** Files placed in `/media/music` are immediately indexed by Home Assistant's built-in media player and available across all smart speakers (Google Cast, Sonos, AirPlay, HomePod).
- **Samba Share:** You can also manage your downloaded songs over the local network via `smb://<ha-ip>/media/music`.

---

## 🛠️ Troubleshooting

- **Downloads Failing on Restricted URLs:** Ensure your Home Assistant host has active internet connectivity and DNS resolution.
- **Permission Errors on Custom Paths:** Ensure the chosen `download_dir` resides within `/media` or `/share` as mapped in container options.
