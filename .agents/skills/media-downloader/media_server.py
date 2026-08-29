#!/usr/bin/env python3
import http.server
import socketserver
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import hashlib

try:
    import numpy as np
    import scipy.signal as signal
except ImportError:
    np = None
    signal = None

try:
    from mutagen.easyid3 import EasyID3
    from mutagen.mp3 import MP3
    from mutagen.id3 import ID3, APIC
    try:
        EasyID3.RegisterTextKey('bpm', 'TBPM')
    except Exception as err:
        _ = err
except ImportError:
    EasyID3 = None
    MP3 = None
    ID3 = None
    APIC = None

PORT = 8888
DATA_DIR = os.path.expanduser("~/.agents/media_downloader")
COVERS_DIR = os.path.join(DATA_DIR, "covers")
os.makedirs(COVERS_DIR, exist_ok=True)

QUEUE_FILE = os.path.join(DATA_DIR, "queue.json")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
LOG_FILE = os.path.join(DATA_DIR, "current_run.log")
CACHE_FILE = os.path.join(DATA_DIR, "media_meta_cache.json")

DEFAULT_SETTINGS = {
    "download_dir": os.path.expanduser("~/Music"),
    "default_bitrate": "320k",
    "notifications_enabled": True,
    "auto_retry_enabled": True,
    "auto_m3u_sync": True,
    "default_language": "en",
    "default_theme": "dark",
    "max_retries": 5,
    "auto_clean_duplicates": False
}

def get_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                s = json.load(f)
                res = DEFAULT_SETTINGS.copy()
                res.update(s)
                return res
        except Exception:
            return DEFAULT_SETTINGS.copy()
    return DEFAULT_SETTINGS.copy()

def save_settings(s):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2)
    except Exception as err:
        _ = err

def get_music_dir():
    s = get_settings()
    d = s.get("download_dir", os.path.expanduser("~/Music"))
    d = os.path.expanduser(d.strip()) if d else os.path.expanduser("~/Music")
    os.makedirs(d, exist_ok=True)
    return d

def get_queue():
    if os.path.exists(QUEUE_FILE):
        try:
            with open(QUEUE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_queue(q):
    with open(QUEUE_FILE, "w") as f:
        json.dump(q, f, indent=2)

def get_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_history(hist):
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(hist, f, indent=2)
    except Exception as err:
        _ = err

active_job = None
current_process = None
job_lock = threading.Lock()
is_queue_paused = False

def normalize_url(u):
    if not u:
        return ""
    return u.strip().split("?")[0].rstrip("/")

# --- Security: Hostname Whitelists & Path Traversal Guard ---
ALLOWED_MEDIA_HOSTS = (
    "spotify.com",
    "open.spotify.com",
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "soundcloud.com",
    "bandcamp.com",
    "tiktok.com",
    "apple.com",
    "music.apple.com",
    "example.com",
)

ALLOWED_RESOURCE_HOSTS = (
    "mzstatic.com",
    "apple.com",
    "itunes.apple.com",
    "scdn.co",
    "spotifycdn.com",
    "ytimg.com",
    "googleusercontent.com",
    "youtube.com",
)

def is_valid_media_service_url(url):
    """Strictly validates media service URLs to prevent SSRF and command injection."""
    if not url or not isinstance(url, str):
        return False
    # Reject shell metacharacters and control characters
    if re.search(r'[;&|`$\n\r\t<>\\\'"]', url):
        return False
    try:
        parsed = urllib.parse.urlsplit(url.strip())
        if parsed.scheme not in ("http", "https"):
            return False
        host = (parsed.hostname or "").lower()
        if not host:
            return False
        return any(host == d or host.endswith("." + d) for d in ALLOWED_MEDIA_HOSTS)
    except Exception:
        return False

def is_safe_remote_resource_url(url):
    """Strictly validates external image and API URLs to prevent SSRF."""
    if not url or not isinstance(url, str):
        return False
    try:
        parsed = urllib.parse.urlsplit(url.strip())
        if parsed.scheme != "https":
            return False
        host = (parsed.hostname or "").lower()
        if not host:
            return False
        # Block localhost, loopback, private RFC1918 IPs, link-local
        if host in ("localhost", "127.0.0.1", "::1", "169.254.169.254"):
            return False
        if host.startswith(("10.", "192.168.", "172.16.", "172.17.", "172.18.", "172.19.", "172.20.", "172.21.", "172.22.", "172.23.", "172.24.", "172.25.", "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31.")):
            return False
        return any(host == d or host.endswith("." + d) for d in ALLOWED_RESOURCE_HOSTS)
    except Exception:
        return False

def _get_allowed_roots():
    roots = [
        os.path.realpath(os.path.expanduser("~/Music")),
        os.path.realpath(os.path.expanduser("~/Videos")),
        os.path.realpath(DATA_DIR),
        "/media",
        "/share",
        "/DATA/Media/Music",
    ]
    custom_dir = get_music_dir()
    if custom_dir:
        roots.append(os.path.realpath(custom_dir))
    return [os.path.realpath(r) for r in roots]

def _safe_path(filepath):
    """
    Returns the resolved canonical path if it is strictly inside an allowed root directory,
    or None if the path would escape allowed directories (path traversal attempt).
    """
    if not filepath or not isinstance(filepath, str):
        return None
    cleaned = filepath.strip()
    if not cleaned or "\0" in cleaned:
        return None
    try:
        resolved = os.path.realpath(os.path.abspath(os.path.expanduser(cleaned)))
        for allowed in _get_allowed_roots():
            allowed_real = os.path.realpath(allowed)
            try:
                if os.path.commonpath([resolved, allowed_real]) == allowed_real:
                    return resolved
            except (ValueError, OSError):
                continue
    except (ValueError, OSError):
        return None
    return None

# --- Performance: TTL-based library + analytics cache ---
_library_cache = {"data": None, "ts": 0.0}
_analytics_cache = {"data": None, "ts": 0.0}
_LIBRARY_TTL = 20.0   # seconds before full re-scan
_ANALYTICS_TTL = 15.0
_cache_lock = threading.Lock()

def invalidate_library_cache():
    """Call this after any download completes or file mutation to force a fresh scan."""
    with _cache_lock:
        _library_cache["ts"] = 0.0
        _analytics_cache["ts"] = 0.0

def record_job_to_history(job, final_status=None):
    if not job or not job.get("url"):
        return
    history = get_history()
    job_id = job.get("id") or f"job_{int(time.time())}"
    
    expected = job.get("expected_count") or 1
    downloaded = job.get("downloaded_count", 0)
    status = final_status or job.get("status") or ("completed" if downloaded >= expected else ("partial" if downloaded > 0 else "failed"))
    if status == "completed" and expected and downloaded < expected:
        status = "partial" if downloaded > 0 else "failed"
    success_pct = round((downloaded / expected) * 100, 1) if expected else 100.0
    
    start_time_str = job.get("added_at") or time.strftime("%Y-%m-%d %H:%M:%S")
    completed_time_str = job.get("last_run") or time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        t_start = time.mktime(time.strptime(start_time_str, "%Y-%m-%d %H:%M:%S"))
        t_end = time.time()
        dur_secs = max(1, int(t_end - t_start))
        mins, secs = divmod(dur_secs, 60)
        dur_display = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"
    except Exception:
        dur_secs = 0
        dur_display = "N/A"
        
    entry = next((h for h in history if h.get("job_id") == job_id or h.get("id") == job_id), None)
    if not entry:
        entry = {
            "id": job_id if job_id.startswith("hist_") else f"hist_{job_id}",
            "job_id": job_id,
            "url": job.get("url"),
            "title": job.get("title") or job.get("url"),
            "mode": job.get("mode", "audio"),
            "status": status,
            "expected_count": expected,
            "downloaded_count": downloaded,
            "success_rate": success_pct,
            "added_at": start_time_str,
            "completed_at": completed_time_str,
            "duration_seconds": dur_secs,
            "duration_display": dur_display,
            "retry_count": job.get("retry_count", 0)
        }
        history.insert(0, entry)
    else:
        entry["title"] = job.get("title") or entry.get("title")
        entry["status"] = status
        entry["expected_count"] = expected or entry.get("expected_count")
        entry["downloaded_count"] = downloaded if downloaded is not None else entry.get("downloaded_count", 0)
        if entry.get("expected_count"):
            entry["success_rate"] = round((entry["downloaded_count"] / entry["expected_count"]) * 100, 1)
        entry["completed_at"] = completed_time_str
        entry["retry_count"] = max(job.get("retry_count", 0), entry.get("retry_count", 0))
        if dur_secs > 0:
            entry["duration_seconds"] = max(entry.get("duration_seconds", 0), dur_secs)
            mins, secs = divmod(entry["duration_seconds"], 60)
            entry["duration_display"] = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"
        if entry in history:
            history.remove(entry)
            history.insert(0, entry)
            
    history = history[:300]
    save_history(history)

def sync_history_with_queue():
    with job_lock:
        q = get_queue()
        for item in q:
            record_job_to_history(item, item.get("status"))

def get_history_analytics():
    now = time.time()
    with _cache_lock:
        if _analytics_cache["data"] is not None and (now - _analytics_cache["ts"]) < _ANALYTICS_TTL:
            return _analytics_cache["data"]

    sync_history_with_queue()
    hist = get_history()
    lib = get_media_library()
    total_jobs = len(hist)
    completed_jobs = sum(1 for h in hist if h.get("status") in ["completed", "ready"])
    cancelled_jobs = sum(1 for h in hist if h.get("status") == "cancelled")
    partial_jobs = sum(1 for h in hist if h.get("status") == "partial")

    total_tracks_downloaded = len(lib)
    total_expected = sum(h.get("expected_count", 1) for h in hist)

    avg_success_rate = round((total_tracks_downloaded / total_expected * 100), 1) if total_expected > 0 else (100.0 if total_jobs > 0 else 0.0)
    if avg_success_rate > 100.0:
        avg_success_rate = 100.0

    result = {
        "total_jobs": total_jobs,
        "completed_jobs": completed_jobs,
        "cancelled_jobs": cancelled_jobs,
        "partial_jobs": partial_jobs,
        "total_tracks_downloaded": total_tracks_downloaded,
        "total_expected": total_expected,
        "avg_success_rate": avg_success_rate
    }

    with _cache_lock:
        _analytics_cache["data"] = result
        _analytics_cache["ts"] = time.time()

    return result

def init_history_from_queue_and_library():
    sync_history_with_queue()

init_history_from_queue_and_library()

def get_playlist_expected_info(url):
    info = {"expected_count": None, "title": None, "type": "Media", "artist": None}
    if not url or not is_valid_media_service_url(url):
        return info
    try:
        parsed_url = urllib.parse.urlsplit(url)
        host = (parsed_url.hostname or "").lower()
        if host == "spotify.com" or host.endswith(".spotify.com"):
            m = re.search(r'(playlist|album|track|artist)/([a-zA-Z0-9]+)', url)
            if m:
                t_type, t_id = m.group(1), m.group(2)
                embed_url = f"https://open.spotify.com/embed/{t_type}/{t_id}"
                req = urllib.request.Request(embed_url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"})
                html = urllib.request.urlopen(req, timeout=4).read().decode("utf-8")
                m_json = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html)
                if m_json:
                    data = json.loads(m_json.group(1))
                    entity = data.get("props", {}).get("pageProps", {}).get("state", {}).get("data", {}).get("entity", {})
                    tracks = entity.get("trackList", [])
                    raw_title = entity.get("title") or entity.get("name")
                    raw_sub = entity.get("subtitle") or ""
                    
                    if t_type == "track":
                        info["expected_count"] = 1
                        info["type"] = "Song"
                        info["title"] = f"{raw_sub} - {raw_title}" if raw_sub and raw_title and raw_sub != "Spotify" else (raw_title or "Spotify Track")
                    elif t_type == "album":
                        info["expected_count"] = len(tracks) if tracks else 1
                        info["type"] = "Album"
                        info["title"] = f"{raw_sub} - {raw_title}" if raw_sub and raw_title and raw_sub != "Spotify" else (raw_title or "Spotify Album")
                    elif t_type == "artist":
                        info["expected_count"] = len(tracks) if tracks else 10
                        info["type"] = "Artist"
                        info["title"] = raw_title or "Spotify Artist"
                    else: # playlist
                        info["expected_count"] = len(tracks) if tracks else 50
                        info["type"] = "Playlist"
                        info["title"] = raw_title or "Spotify Playlist"
        elif host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com"):
            safe_url = parsed_url.geturl()
            if "list=" in safe_url:
                cmd = ["uvx", "yt-dlp", "--flat-playlist", "--dump-single-json", "--no-warnings", "--", safe_url]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=6)
                if res.returncode == 0:
                    data = json.loads(res.stdout)
                    info["expected_count"] = len(data.get("entries", []))
                    info["title"] = data.get("title") or "YouTube Playlist"
                    info["type"] = "Playlist"
            else:
                cmd = ["uvx", "yt-dlp", "--dump-single-json", "--no-warnings", "--", safe_url]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                if res.returncode == 0:
                    data = json.loads(res.stdout)
                    info["expected_count"] = 1
                    info["title"] = data.get("title") or "YouTube Video"
                    info["type"] = "Video / Audio"
                else:
                    info["expected_count"] = 1
    except Exception as e:
        _ = e
    return info

meta_cache = {}
if os.path.exists(CACHE_FILE):
    try:
        with open(CACHE_FILE, "r") as f:
            meta_cache = json.load(f)
    except Exception:
        meta_cache = {}

def save_cache():
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(meta_cache, f)
    except Exception as err:
        _ = err



def log(msg):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"[{timestamp}] {msg}\n")

def cleanup_stale_queue_on_boot():
    with job_lock:
        q = get_queue()
        changed = False
        for item in q:
            if item.get("status") in ["downloading", "running"]:
                item["status"] = "queued"
                changed = True
            if "expected_count" not in item or not item.get("expected_count"):
                meta = get_playlist_expected_info(item.get("url", ""))
                if meta.get("expected_count"):
                    item["expected_count"] = meta["expected_count"]
                    if meta.get("title") and not item.get("title"):
                        item["title"] = meta["title"]
                    changed = True
        if changed:
            save_queue(q)

cleanup_stale_queue_on_boot()

def count_music_files():
    music_dir = os.path.expanduser("~/Music")
    if not os.path.exists(music_dir):
        return 0
    return len([f for r, d, fs in os.walk(music_dir) for f in fs if f.endswith((".mp3", ".m4a", ".flac"))])

def sync_playlist_m3u(folder_path):
    if not os.path.exists(folder_path) or not os.path.isdir(folder_path):
        return None
        
    m3u_path = os.path.join(folder_path, "playlist.m3u8")
    mp3_files = sorted([f for f in os.listdir(folder_path) if f.endswith((".mp3", ".m4a", ".flac"))])
    
    if not mp3_files:
        return None
        
    lines = ["#EXTM3U", f"#PLAYLIST:{os.path.basename(folder_path)}"]
    for f in mp3_files:
        full_f = os.path.join(folder_path, f)
        info = meta_cache.get(full_f, {})
        title = info.get("title", f[:-4])
        artist = info.get("artist", "")
        display = f"{artist} - {title}" if artist else title
        lines.append(f"#EXTINF:-1,{display}")
        lines.append(f)
        
    try:
        with open(m3u_path, "w", encoding="utf-8") as fp:
            fp.write("\n".join(lines) + "\n")
        return m3u_path
    except Exception:
        return None

def get_cover_path(filepath):
    safe_fp = _safe_path(filepath)
    if not safe_fp or not os.path.exists(safe_fp):
        return None
    file_hash = hashlib.md5(safe_fp.encode("utf-8")).hexdigest()
    cover_file = os.path.join(COVERS_DIR, f"{file_hash}.jpg")
    
    if os.path.exists(cover_file) and os.path.getsize(cover_file) > 0:
        return cover_file
        
    # 1. Try extracting embedded APIC from the file with ffmpeg
    try:
        cmd = ["ffmpeg", "-y", "-i", safe_fp, "-an", "-vcodec", "copy", cover_file]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
        if os.path.exists(cover_file) and os.path.getsize(cover_file) > 0:
            return cover_file
    except Exception as err:
        _ = err

    # 2. Check for local folder image in same album directory
    parent_dir = os.path.dirname(filepath)
    if os.path.exists(parent_dir):
        for name in ["cover.jpg", "folder.jpg", "album.jpg", "cover.png", "artwork.jpg"]:
            local_art = os.path.join(parent_dir, name)
            if os.path.exists(local_art) and os.path.getsize(local_art) > 0:
                return local_art

        # 3. Check sibling audio files in the same album folder
        try:
            for f in os.listdir(parent_dir):
                if f.endswith((".mp3", ".m4a", ".flac")) and f != os.path.basename(filepath):
                    sib_path = os.path.join(parent_dir, f)
                    sib_hash = hashlib.md5(sib_path.encode("utf-8")).hexdigest()
                    sib_cover = os.path.join(COVERS_DIR, f"{sib_hash}.jpg")
                    if os.path.exists(sib_cover) and os.path.getsize(sib_cover) > 0:
                        return sib_cover
        except Exception as err:
            _ = err

    # 4. Auto-Fetch & Auto-Heal from online database (iTunes / Apple Music)
    try:
        info = get_file_audio_info(filepath, os.path.getmtime(filepath))
        title = info.get("title") or os.path.basename(filepath).replace(".mp3", "")
        artist = info.get("artist") or ""
        q = f"{artist} {title}".strip()
        if q:
            url = f"https://itunes.apple.com/search?term={urllib.parse.quote(q)}&media=music&entity=song&limit=1"
            if not is_safe_remote_resource_url(url):
                return None
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=4) as res:
                data = json.loads(res.read().decode())
                results = data.get("results", [])
                if results:
                    art_url = results[0].get("artworkUrl100", "").replace("100x100bb.jpg", "1000x1000bb.jpg")
                    if art_url and is_safe_remote_resource_url(art_url):
                        art_req = urllib.request.Request(art_url, headers={"User-Agent": "Mozilla/5.0"})
                        with urllib.request.urlopen(art_req, timeout=6) as img_res:
                            img_data = img_res.read()
                            with open(cover_file, "wb") as f_out:
                                f_out.write(img_data)
                            # Embed into file in background so next time it's native
                            try:
                                audio = MP3(filepath, ID3=ID3)
                                audio.tags.add(APIC(
                                    encoding=3,
                                    mime="image/jpeg",
                                    type=3,
                                    desc="Cover",
                                    data=img_data
                                ))
                                audio.save(v2_version=3)
                            except Exception as err:
                                _ = err
                            return cover_file
    except Exception as err:
        _ = err

    return None

def get_file_audio_info(filepath, mtime):
    cached = meta_cache.get(filepath)
    if cached and cached.get("mtime") == mtime:
        return cached

    info = {
        "bitrate": "N/A",
        "sample_rate": "N/A",
        "duration": "N/A",
        "artist": "",
        "title": os.path.basename(filepath),
        "mtime": mtime
    }
    
    try:
        cmd = [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", filepath
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
        if res.returncode == 0:
            data = json.loads(res.stdout)
            fmt = data.get("format", {})
            streams = data.get("streams", [{}])
            stream = streams[0] if streams else {}
            
            raw_br = int(fmt.get("bit_rate", stream.get("bit_rate", 0)))
            raw_dur = float(fmt.get("duration", stream.get("duration", 0)))
            file_size_bytes = float(fmt.get("size", 0))
            
            if raw_br == 0 and raw_dur > 0 and file_size_bytes > 0:
                raw_br = int((file_size_bytes * 8) / raw_dur)
                
            if raw_br > 0:
                info["bitrate"] = f"{raw_br // 1000} kbps"
            else:
                info["bitrate"] = "Standard MP3"
            
            if raw_dur > 0:
                mins = int(raw_dur // 60)
                secs = int(raw_dur % 60)
                info["duration"] = f"{mins}:{secs:02d}"
                
            raw_sr = int(stream.get("sample_rate", 0))
            if raw_sr > 0:
                info["sample_rate"] = f"{raw_sr / 1000:.1f} kHz"
                
            tags = fmt.get("tags", {})
            info["artist"] = tags.get("artist", tags.get("album_artist", tags.get("ARTIST", "")))
            info["album"] = tags.get("album", tags.get("ALBUM", "Single"))
            info["title"] = tags.get("title", tags.get("TITLE", os.path.basename(filepath)))
            info["genre"] = tags.get("genre", tags.get("GENRE", ""))
            info["track"] = tags.get("track", tags.get("TRACK", "1"))
            
            raw_bpm = tags.get("bpm", tags.get("TBPM", ""))
            if raw_bpm:
                val = raw_bpm[0] if isinstance(raw_bpm, list) else str(raw_bpm)
                info["bpm"] = f"{val} BPM" if not val.endswith("BPM") else val
            else:
                calc = calculate_bpm(filepath)
                info["bpm"] = f"{calc} BPM" if calc else "N/A"
    except Exception as err:
        _ = err
        
    meta_cache[filepath] = info
    return info

bpm_cache = {}

def calculate_bpm(filepath):
    if not os.path.exists(filepath) or not filepath.endswith(('.mp3', '.m4a', '.flac')):
        return None
    if filepath in bpm_cache:
        return bpm_cache[filepath]
        
    # Check ID3 tag first
    try:
        audio = EasyID3(filepath)
        if 'bpm' in audio and audio['bpm']:
            val = float(audio['bpm'][0])
            if 40 <= val <= 240:
                bpm_cache[filepath] = round(val)
                return bpm_cache[filepath]
    except Exception as err:
        _ = err

    if np is None or signal is None:
        return None
        
    try:
        cmd = [
            'ffmpeg', '-v', 'quiet', '-ss', '15', '-t', '35',
            '-i', filepath, '-ac', '1', '-ar', '11025', '-f', 'f32le', '-'
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=4)
        if proc.returncode != 0 or len(proc.stdout) < 44100:
            return None
            
        samples = np.frombuffer(proc.stdout, dtype=np.float32)
        if len(samples) < 11025 * 5:
            return None
            
        sr = 11025
        hop_size = 256
        n_hops = len(samples) // hop_size
        
        reshaped = samples[:n_hops * hop_size].reshape(n_hops, hop_size)
        envelope = np.sqrt(np.mean(reshaped**2, axis=1))
        
        novelty = np.diff(envelope)
        novelty = np.maximum(0, novelty)
        
        b, a = signal.butter(2, 0.1)
        novelty = signal.filtfilt(b, a, novelty)
        
        novelty = novelty - np.mean(novelty)
        autocorr = signal.correlate(novelty, novelty, mode='full')
        autocorr = autocorr[len(autocorr)//2:]
        
        fps = sr / hop_size
        min_lag = int(fps * 60 / 190)
        max_lag = int(fps * 60 / 60)
        
        if max_lag >= len(autocorr):
            max_lag = len(autocorr) - 1
            
        lags = np.arange(min_lag, max_lag)
        best_lag = lags[np.argmax(autocorr[lags])]
        
        bpm = (fps * 60.0) / best_lag
        while bpm < 75: bpm *= 2
        while bpm > 175: bpm /= 2
        
        final_bpm = int(round(bpm))
        bpm_cache[filepath] = final_bpm
        
        # Save to ID3 tag in background
        if filepath.endswith('.mp3'):
            try:
                audio = EasyID3(filepath)
                audio['bpm'] = str(final_bpm)
                audio.save()
            except Exception as err:
                _ = err
                
        return final_bpm
    except Exception:
        return None

def clean_metadata_name(s):
    if not s: return ''
    s = s.replace('’', "'").replace('‘', "'").replace('“', "'").replace('”', "'").replace('"', "'")
    s = re.sub(r'[\\/*?:"<>|]', '', s).strip()
    return s

def extract_clean_song_title(title, filename, artist_name):
    t = (title or "").strip()
    if not t:
        t = os.path.splitext(filename)[0]
    t = re.sub(r'^(?:\d+[\s.\-_]+)+', '', t)
    t = re.sub(r'\.(mp3|m4a|flac|wav)$', '', t, flags=re.IGNORECASE)
    if artist_name and ' - ' in t:
        parts = t.split(' - ')
        if parts[0].lower().strip() == artist_name.lower().strip():
            t = ' - '.join(parts[1:])
    return clean_metadata_name(t) or 'Track'

metadata_cache_lookup = {}

def lookup_track_metadata(query):
    if not query: return None
    clean_q = re.sub(r'\[.*?\]|\(.*?\)', '', query).strip()
    clean_q = re.sub(r'\.(mp3|m4a|flac|wav)$', '', clean_q, flags=re.IGNORECASE).strip()
    if not clean_q or len(clean_q) < 2: return None
    if clean_q in metadata_cache_lookup:
        return metadata_cache_lookup[clean_q]
    try:
        url = 'https://itunes.apple.com/search?' + urllib.parse.urlencode({
            'term': clean_q,
            'entity': 'song',
            'limit': 1
        })
        if not is_safe_remote_resource_url(url):
            return None
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=4) as response:
            data = json.loads(response.read().decode())
            results = data.get('results', [])
            if results:
                r = results[0]
                meta = {
                    'artist': r.get('artistName', ''),
                    'album': r.get('collectionName', ''),
                    'title': r.get('trackName', ''),
                    'genre': r.get('primaryGenreName', ''),
                    'track_number': str(r.get('trackNumber', '1')),
                    'year': r.get('releaseDate', '')[:4]
                }
                metadata_cache_lookup[clean_q] = meta
                return meta
    except Exception as err:
        _ = err
    metadata_cache_lookup[clean_q] = None
    return None

def search_metadata_online(query, limit=8):
    if not query: return []
    clean_q = re.sub(r'\[.*?\]|\(.*?\)', '', query).strip()
    clean_q = re.sub(r'\.(mp3|m4a|flac|wav)$', '', clean_q, flags=re.IGNORECASE).strip()
    if not clean_q: return []
    
    results = []
    try:
        url = 'https://itunes.apple.com/search?' + urllib.parse.urlencode({
            'term': clean_q,
            'entity': 'song',
            'limit': limit
        })
        if not is_safe_remote_resource_url(url):
            return None
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=4) as response:
            data = json.loads(response.read().decode())
            for r in data.get('results', []):
                raw_art = r.get('artworkUrl100', '')
                high_art = raw_art.replace('100x100bb.jpg', '1000x1000bb.jpg') if raw_art else ''
                results.append({
                    'title': r.get('trackName', ''),
                    'artist': r.get('artistName', ''),
                    'album': r.get('collectionName', ''),
                    'genre': r.get('primaryGenreName', ''),
                    'year': r.get('releaseDate', '')[:4] if r.get('releaseDate') else '',
                    'track_number': str(r.get('trackNumber', '1')),
                    'track_count': str(r.get('trackCount', '1')),
                    'cover_url': high_art or raw_art,
                    'preview_url': r.get('previewUrl', '')
                })
    except Exception as err:
        _ = err
    return results

def update_track_metadata(filepath, meta):
    safe_fp = _safe_path(filepath)
    if not safe_fp or not os.path.exists(safe_fp):
        return {"success": False, "error": "File not found or access denied"}
    filepath = safe_fp
    try:
        try:
            audio = EasyID3(filepath)
        except Exception:
            audio = MP3(filepath)
            audio.add_tags()
            audio = EasyID3(filepath)
            
        if meta.get("artist"): audio["artist"] = meta["artist"]
        if meta.get("album"): audio["album"] = meta["album"]
        if meta.get("title"): audio["title"] = meta["title"]
        if meta.get("genre"): audio["genre"] = meta["genre"]
        if meta.get("track_number"): audio["tracknumber"] = str(meta["track_number"])
        if meta.get("year"): audio["date"] = str(meta["year"])
        audio.save()
    except Exception as e:
        return {"success": False, "error": f"Failed to save ID3 tags: {e}"}

    cover_url = meta.get("cover_url")
    if cover_url and is_safe_remote_resource_url(cover_url):
        try:
            req = urllib.request.Request(cover_url, headers={'User-Agent': 'Mozilla/5.0'})
            img_data = urllib.request.urlopen(req, timeout=5).read()
            if img_data:
                from mutagen.id3 import ID3, APIC
                tags = ID3(filepath)
                tags.delall('APIC')
                tags.add(APIC(
                    encoding=3,
                    mime='image/jpeg' if ('jpg' in cover_url.lower() or 'jpeg' in cover_url.lower()) else 'image/png',
                    type=3,
                    desc=u'Cover',
                    data=img_data
                ))
                tags.save(v2_version=3)
        except Exception as err:
            _ = err

    artist = clean_metadata_name(meta.get("artist") or "Unknown Artist")
    album = clean_metadata_name(meta.get("album") or "Single")
    title = clean_metadata_name(meta.get("title") or os.path.splitext(os.path.basename(filepath))[0])
    
    raw_track = str(meta.get("track_number", "1")).split("/")[0].strip()
    try:
        track_num = f"{int(raw_track):02d}"
    except Exception:
        track_num = "01"
        
    base = os.path.expanduser("~/Music")
    target_dir = os.path.join(base, artist, album)
    os.makedirs(target_dir, exist_ok=True)
    ext = os.path.splitext(filepath)[1]
    target_file = os.path.join(target_dir, f"{track_num} - {title}{ext}")
    
    final_path = filepath
    if os.path.abspath(filepath) != os.path.abspath(target_file):
        if not os.path.exists(target_file):
            shutil.move(filepath, target_file)
            final_path = target_file
            old_dir = os.path.dirname(filepath)
            while old_dir and old_dir != base and not os.listdir(old_dir):
                try:
                    os.rmdir(old_dir)
                    old_dir = os.path.dirname(old_dir)
                except OSError:
                    break

    organize_and_sync_library()
    invalidate_library_cache()
    return {"success": True, "new_path": final_path}

def delete_track_from_library(filepath):
    safe_fp = _safe_path(filepath)
    if not safe_fp or not os.path.exists(safe_fp):
        return {"success": False, "error": "File not found or access denied"}
    try:
        os.remove(safe_fp)
        if safe_fp in meta_cache:
            del meta_cache[safe_fp]
        base = os.path.expanduser("~/Music")
        parent = os.path.dirname(filepath)
        while parent and parent != base and not os.listdir(parent):
            try:
                os.rmdir(parent)
                parent = os.path.dirname(parent)
            except OSError:
                break
        organize_and_sync_library()
        invalidate_library_cache()
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

def clean_alphanumeric_key(s):
    if not s: return ''
    s = re.sub(r'\(.*?\)|\[.*?\]', '', s)
    s = re.sub(r'\b(feat|ft|with|and|single|edit|version|remix|official|audio|video|album|ep|instrumental)\b', '', s, flags=re.IGNORECASE)
    s = re.sub(r'[^a-zA-Z0-9]', '', s)
    return s.lower()

def find_duplicate_and_similar_tracks():
    base = os.path.expanduser("~/Music")
    playlists_dir = os.path.join(base, "_PLAYLISTS_")
    if not os.path.exists(base):
        return [], 0
        
    tracks = []
    for root, dirs, files in os.walk(base):
        if os.path.abspath(root).startswith(playlists_dir):
            continue
        for f in files:
            if f.endswith((".mp3", ".m4a", ".flac", ".mp4")):
                fp = os.path.join(root, f)
                try:
                    stat = os.stat(fp)
                    info = get_file_audio_info(fp, stat.st_mtime)
                    art = info.get('artist', '')
                    alb = info.get('album', '')
                    tit = info.get('title', '') or os.path.splitext(f)[0]
                    dur = info.get('duration', 'N/A')
                    br = info.get('bitrate', 'N/A')
                    
                    tracks.append({
                        'path': fp,
                        'filename': f,
                        'title': tit,
                        'artist': art or 'Unknown Artist',
                        'album': alb or 'Single',
                        'folder': os.path.relpath(os.path.dirname(fp), base),
                        'duration': dur,
                        'bitrate': br,
                        'size_bytes': stat.st_size,
                        'size_mb': round(stat.st_size / (1024*1024), 2),
                        'mtime': stat.st_mtime,
                        'clean_title': clean_alphanumeric_key(tit),
                        'clean_artist': clean_alphanumeric_key(art)
                    })
                except Exception as err:
                    _ = err

    groups_map = {}
    for t in tracks:
        key = f"{t['clean_artist']}_{t['clean_title']}" if t['clean_artist'] else t['clean_title']
        if key:
            groups_map.setdefault(key, []).append(t)

    result_groups = []
    total_wasted_bytes = 0
    
    for key, items in groups_map.items():
        if len(items) > 1:
            def score(x):
                sc = x['size_bytes']
                # Prefer full album release over Single
                if x['album'] and x['album'].lower() not in ['single', 'unknown']:
                    sc += 1000000
                # Prefer 320kbps
                if '320' in str(x.get('bitrate', '')):
                    sc += 2000000
                return sc
                
            items.sort(key=score, reverse=True)
            for idx, item in enumerate(items):
                item['is_best'] = (idx == 0)
                if idx > 0:
                    total_wasted_bytes += item['size_bytes']
                    
            wasted_grp = sum(it['size_bytes'] for it in items[1:])
            result_groups.append({
                'group_key': key,
                'song_name': items[0]['title'],
                'artist': items[0]['artist'],
                'items': items,
                'duplicate_count': len(items) - 1,
                'wasted_mb': round(wasted_grp / (1024*1024), 2)
            })
            
    # Sort groups by largest recoverable space
    result_groups.sort(key=lambda g: g['wasted_mb'], reverse=True)
    return result_groups, round(total_wasted_bytes / (1024*1024), 2)

def clean_all_duplicates_auto(selected_group_keys=None):
    groups, _ = find_duplicate_and_similar_tracks()
    deleted_count = 0
    freed_bytes = 0
    
    for grp in groups:
        if selected_group_keys and grp['group_key'] not in selected_group_keys:
            continue
        for it in grp['items'][1:]: # All items except the best one
            fp = it.get('path')
            safe_fp = _safe_path(fp)
            if safe_fp and os.path.exists(safe_fp):
                try:
                    freed_bytes += os.path.getsize(safe_fp)
                    os.remove(safe_fp)
                    if safe_fp in meta_cache:
                        del meta_cache[safe_fp]
                    deleted_count += 1
                except Exception as err:
                    _ = err
                    
    # Clean empty directories
    base = os.path.expanduser("~/Music")
    for root, dirs, files in os.walk(base, topdown=False):
        if root == base or root.endswith("_PLAYLISTS_") or root.endswith("_UNKNOWN_"):
            continue
        if not os.listdir(root):
            try: os.rmdir(root)
            except Exception as err:
                _ = err
            
    organize_and_sync_library()
    return {
        "success": True,
        "deleted_count": deleted_count,
        "freed_mb": round(freed_bytes / (1024*1024), 2)
    }

def write_mp3_tags(filepath, meta):
    if not filepath.endswith('.mp3'):
        return False
    try:
        try:
            audio = EasyID3(filepath)
        except Exception:
            audio = MP3(filepath)
            audio.add_tags()
            audio = EasyID3(filepath)
            
        if meta.get('artist'): audio['artist'] = meta['artist']
        if meta.get('album'): audio['album'] = meta['album']
        if meta.get('title'): audio['title'] = meta['title']
        if meta.get('genre'): audio['genre'] = meta['genre']
        if meta.get('track_number'): audio['tracknumber'] = str(meta['track_number'])
        if meta.get('year'): audio['date'] = str(meta['year'])
        audio.save()
        return True
    except Exception:
        return False

def organize_and_sync_library():
    base = os.path.expanduser("~/Music")
    if not os.path.exists(base):
        return
    playlists_dir = os.path.join(base, "_PLAYLISTS_")
    misc_dir = os.path.join(base, "_UNKNOWN_")
    
    # Auto-migrate legacy folder names
    for leg_p in ["[Playlists]", "Playlists"]:
        legacy_p_dir = os.path.join(base, leg_p)
        if os.path.exists(legacy_p_dir):
            os.makedirs(playlists_dir, exist_ok=True)
            for f in os.listdir(legacy_p_dir):
                shutil.move(os.path.join(legacy_p_dir, f), os.path.join(playlists_dir, f))
            try: os.rmdir(legacy_p_dir)
            except Exception as err:
                _ = err

    for leg_m in ["[Misc]", "Misc", "Unknown", "Unknown Artist"]:
        legacy_m_dir = os.path.join(base, leg_m)
        if os.path.exists(legacy_m_dir):
            os.makedirs(misc_dir, exist_ok=True)
            for root, dirs, files in os.walk(legacy_m_dir):
                for f in files:
                    if f.endswith((".mp3", ".m4a", ".flac")):
                        shutil.move(os.path.join(root, f), os.path.join(misc_dir, f))
            try: shutil.rmtree(legacy_m_dir)
            except Exception as err:
                _ = err
        
    os.makedirs(playlists_dir, exist_ok=True)
    os.makedirs(misc_dir, exist_ok=True)
    
    genres_map = {}
    albums_map = {}
    all_tracks = []
    
    for root, dirs, files in os.walk(base):
        if os.path.abspath(root).startswith(playlists_dir):
            continue
        for f in files:
            if f.endswith((".mp3", ".m4a", ".flac")):
                full_path = os.path.join(root, f)
                try:
                    stat = os.stat(full_path)
                    info = get_file_audio_info(full_path, stat.st_mtime)
                    
                    artist = clean_metadata_name(info.get("artist"))
                    album = clean_metadata_name(info.get("album"))
                    title = extract_clean_song_title(info.get("title"), f, artist)
                    genre = clean_metadata_name(info.get("genre"))
                    
                    # If artist or album is missing, attempt metadata lookup!
                    missing_artist = not artist or artist.lower() in ["unknown", "unknown artist"]
                    missing_album = not album or album.lower() in ["single", "unknown"]
                    if missing_artist or missing_album:
                        query = title if missing_artist else f"{artist} {title}".strip()
                        meta = lookup_track_metadata(query)
                        if meta and meta.get("artist"):
                            write_mp3_tags(full_path, meta)
                            artist = clean_metadata_name(meta["artist"])
                            album = clean_metadata_name(meta.get("album") or "Single")
                            title = clean_metadata_name(meta.get("title") or title)
                            genre = clean_metadata_name(meta.get("genre") or genre)
                            info["track"] = meta.get("track_number", "1")
                            
                    # If still missing artist after search, move to _UNKNOWN_!
                    if not artist or artist.lower() in ["unknown", "unknown artist"]:
                        target_dir = misc_dir
                        target_file = os.path.join(target_dir, f"{title}{os.path.splitext(f)[1]}")
                    else:
                        raw_track = str(info.get("track", "1")).split("/")[0].strip()
                        try:
                            track_num = f"{int(raw_track):02d}"
                        except Exception:
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
                        actual_path = target_file
                    else:
                        actual_path = full_path
                        
                    all_tracks.append(actual_path)
                    if genre and genre != "Unknown":
                        genres_map.setdefault(genre, []).append(actual_path)
                    if album and album not in ["Single", "Unknown"]:
                        albums_map.setdefault(f"{artist} - {album}", []).append(actual_path)
                except Exception as err:
                    _ = err

    # Clean empty directories
    for root, dirs, files in os.walk(base, topdown=False):
        if root == base or root == playlists_dir or root == misc_dir:
            continue
        if not os.listdir(root):
            try: os.rmdir(root)
            except Exception as err:
                _ = err

    # Maintain standard library and BPM curated playlists
    if all_tracks:
        all_p = os.path.join(playlists_dir, "All Music.m3u8")
        with open(all_p, "w", encoding="utf-8") as pf:
            pf.write("#EXTM3U\n" + "\n".join(all_tracks) + "\n")
            
        bpm_groups = {
            "BPM - Chill (<90 BPM)": [],
            "BPM - Mid-Tempo (90-115 BPM)": [],
            "BPM - House & Dance (116-130 BPM)": [],
            "BPM - High Energy (130+ BPM)": []
        }
        
        for trk in all_tracks:
            bpm_val = calculate_bpm(trk)
            if bpm_val:
                if bpm_val < 90:
                    bpm_groups["BPM - Chill (<90 BPM)"].append(trk)
                elif 90 <= bpm_val <= 115:
                    bpm_groups["BPM - Mid-Tempo (90-115 BPM)"].append(trk)
                elif 116 <= bpm_val <= 130:
                    bpm_groups["BPM - House & Dance (116-130 BPM)"].append(trk)
                else:
                    bpm_groups["BPM - High Energy (130+ BPM)"].append(trk)
                    
        for p_name, trks in bpm_groups.items():
            if trks:
                p_file = os.path.join(playlists_dir, f"{p_name}.m3u8")
                with open(p_file, "w", encoding="utf-8") as pf:
                    pf.write("#EXTM3U\n" + "\n".join(trks) + "\n")

def _scan_media_library():
    """Full disk scan — expensive. Only called when TTL cache has expired."""
    organize_and_sync_library()
    items = []
    search_dirs = [os.path.expanduser("~/Music"), os.path.expanduser("~/Videos")]
    playlists_dir = os.path.join(os.path.expanduser("~/Music"), "_PLAYLISTS_")
    cache_dirty = False

    for base in search_dirs:
        if not os.path.exists(base):
            continue
        for root, dirs, files in os.walk(base):
            if os.path.abspath(root).startswith(playlists_dir):
                continue
            for f in files:
                if f.endswith((".mp3", ".m4a", ".flac", ".mp4", ".mkv", ".webm")):
                    full = os.path.join(root, f)
                    try:
                        stat = os.stat(full)
                        size_mb = round(stat.st_size / (1024 * 1024), 2)
                        mtime_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(stat.st_mtime))

                        audio_info = get_file_audio_info(full, stat.st_mtime)
                        cache_dirty = True

                        rel_folder = os.path.relpath(os.path.dirname(full), base)
                        folder_display = rel_folder if rel_folder != "." else "Root"

                        items.append({
                            "name": f,
                            "display_title": audio_info.get("title") or f,
                            "artist": audio_info.get("artist", ""),
                            "album": audio_info.get("album", "Single"),
                            "track_num": audio_info.get("track", "1"),
                            "genre": audio_info.get("genre", ""),
                            "bpm": audio_info.get("bpm", "N/A"),
                            "bitrate": audio_info.get("bitrate", "N/A"),
                            "sample_rate": audio_info.get("sample_rate", "N/A"),
                            "duration": audio_info.get("duration", "N/A"),
                            "folder": folder_display,
                            "folder_path": os.path.dirname(full),
                            "full_path": full,
                            "type": "Video" if f.endswith((".mp4", ".mkv", ".webm")) else "Audio",
                            "size": f"{size_mb} MB",
                            "size_bytes": stat.st_size,
                            "date": mtime_str,
                            "mtime_raw": stat.st_mtime
                        })
                    except Exception as err:
                        _ = err

    if cache_dirty:
        save_cache()

    items.sort(key=lambda x: x["mtime_raw"], reverse=True)
    return items


def get_media_library(force_refresh=False):
    """Returns the media library, using a TTL cache to avoid repeated full disk scans."""
    now = time.time()
    with _cache_lock:
        if not force_refresh and _library_cache["data"] is not None and (now - _library_cache["ts"]) < _LIBRARY_TTL:
            return _library_cache["data"]

    result = _scan_media_library()

    with _cache_lock:
        _library_cache["data"] = result
        _library_cache["ts"] = time.time()

    return result


def get_playlists_summary():
    base = os.path.expanduser("~/Music")
    playlists_dir = os.path.join(base, "_PLAYLISTS_")
    playlists = []
    
    if os.path.exists(playlists_dir):
        for f in sorted(os.listdir(playlists_dir)):
            if f.endswith((".m3u8", ".m3u")):
                m3u_path = os.path.join(playlists_dir, f)
                p_name = os.path.splitext(f)[0]
                lines = []
                try:
                    with open(m3u_path, "r", encoding="utf-8", errors="ignore") as pf:
                        lines = [l.strip() for l in pf.readlines() if l.strip() and not l.startswith("#")]
                except Exception as err:
                    _ = err
                    
                valid_tracks = [l for l in lines if os.path.exists(l)]
                total_bytes = sum(os.path.getsize(t) for t in valid_tracks if os.path.exists(t))
                total_mb = round(total_bytes / (1024 * 1024), 1)
                
                # Check category badge
                badge_type = "Playlist"
                if p_name.startswith("BPM - "):
                    badge_type = "BPM"
                
                playlists.append({
                    "name": p_name,
                    "badge_type": badge_type,
                    "m3u_path": m3u_path,
                    "folder_path": playlists_dir,
                    "track_count": len(valid_tracks),
                    "total_size_mb": total_mb,
                    "tracks": valid_tracks
                })
                
    return playlists

def get_library_explorer_data():
    lib = get_media_library()
    artists = {}
    albums = {}
    formats = {}
    folders = {}
    
    for item in lib:
        art_name = item.get("artist") or "Unknown Artist"
        alb_name = item.get("album") or "Single"
        fld_name = item.get("folder") or "Music"
        fmt = os.path.splitext(item.get("name", ""))[1].lower() or ".mp3"
        
        formats[fmt] = formats.get(fmt, 0) + 1
        
        # Artist grouping
        if art_name not in artists:
            artists[art_name] = {
                "name": art_name,
                "track_count": 0,
                "albums": set(),
                "total_size_bytes": 0,
                "sample_track": item.get("full_path")
            }
        artists[art_name]["track_count"] += 1
        artists[art_name]["albums"].add(alb_name)
        artists[art_name]["total_size_bytes"] += item.get("size_bytes", 0)
        
        # Album grouping
        alb_key = f"{art_name} • {alb_name}"
        if alb_key not in albums:
            albums[alb_key] = {
                "artist": art_name,
                "title": alb_name,
                "folder_path": item.get("folder_path"),
                "track_count": 0,
                "total_size_bytes": 0,
                "sample_track": item.get("full_path")
            }
        albums[alb_key]["track_count"] += 1
        albums[alb_key]["total_size_bytes"] += item.get("size_bytes", 0)

        # Folder grouping
        if fld_name not in folders:
            folders[fld_name] = {
                "name": fld_name,
                "folder_path": item.get("folder_path"),
                "track_count": 0,
                "total_size_bytes": 0
            }
        folders[fld_name]["track_count"] += 1
        folders[fld_name]["total_size_bytes"] += item.get("size_bytes", 0)

    artists_list = []
    for k, v in sorted(artists.items(), key=lambda x: x[1]["track_count"], reverse=True):
        artists_list.append({
            "name": v["name"],
            "track_count": v["track_count"],
            "album_count": len(v["albums"]),
            "total_size_mb": round(v["total_size_bytes"] / (1024 * 1024), 1),
            "sample_track": v["sample_track"]
        })
        
    albums_list = []
    for k, v in sorted(albums.items(), key=lambda x: x[1]["track_count"], reverse=True):
        albums_list.append({
            "artist": v["artist"],
            "title": v["title"],
            "folder_path": v["folder_path"],
            "track_count": v["track_count"],
            "total_size_mb": round(v["total_size_bytes"] / (1024 * 1024), 1),
            "sample_track": v["sample_track"]
        })

    folders_list = []
    for k, v in sorted(folders.items(), key=lambda x: x[1]["name"]):
        folders_list.append({
            "name": v["name"],
            "folder_path": v["folder_path"],
            "track_count": v["track_count"],
            "total_size_mb": round(v["total_size_bytes"] / (1024 * 1024), 1)
        })

    total_bytes = sum(m.get("size_bytes", 0) for m in lib)

    return {
        "total_tracks": len(lib),
        "total_artists": len(artists_list),
        "total_albums": len(albums_list),
        "total_folders": len(folders_list),
        "total_size_mb": round(total_bytes / (1024 * 1024), 1),
        "total_size_gb": round(total_bytes / (1024 * 1024 * 1024), 2),
        "formats": formats,
        "artists": artists_list,
        "albums": albums_list,
        "folders": folders_list
    }

active_playlist_cache = {}

def get_active_playlist_tracks(url):
    if not url:
        return []
    if url in active_playlist_cache:
        return active_playlist_cache[url]
    
    tracks = []
    if not is_valid_media_service_url(url):
        return []
    parsed_url = urllib.parse.urlsplit(url)
    host = (parsed_url.hostname or "").lower()
    if host == "spotify.com" or host.endswith(".spotify.com"):
        m = re.search(r'(playlist|album)/([a-zA-Z0-9]+)', url)
        if m:
            t_type, t_id = m.group(1), m.group(2)
            embed_url = f"https://open.spotify.com/embed/{t_type}/{t_id}"
            try:
                req = urllib.request.Request(embed_url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"})
                html = urllib.request.urlopen(req, timeout=4).read().decode("utf-8")
                m_json = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html)
                if m_json:
                    data = json.loads(m_json.group(1))
                    entity = data.get("props", {}).get("pageProps", {}).get("state", {}).get("data", {}).get("entity", {})
                    for t in entity.get("trackList", []):
                        tracks.append({
                            "title": t.get("title", "Unknown"),
                            "artist": t.get("subtitle", ""),
                            "duration_ms": t.get("duration", 0),
                            "uri": t.get("uri", "")
                        })
            except Exception as err:
                _ = err
    
    if tracks:
        active_playlist_cache[url] = tracks
    return tracks

def get_triage_data(active_job, queue_items, logs):
    active_url = active_job.get("url", "") if active_job else (queue_items[0]["url"] if queue_items else "")
    meta_tracks = get_active_playlist_tracks(active_url)
    
    lib = get_media_library()
    downloaded_titles = {}
    for item in lib:
        name_clean = item["name"].lower().replace(".mp3", "").replace(".m4a", "").replace(".flac", "")
        display_clean = item.get("display_title", "").lower()
        downloaded_titles[name_clean] = item
        downloaded_titles[display_clean] = item

    triage = []
    for t in meta_tracks:
        title = t["title"]
        artist = t["artist"]
        title_lower = title.lower()
        
        matched_item = None
        for k, v in downloaded_titles.items():
            if title_lower in k or (artist and artist.lower() in k and title_lower in k):
                matched_item = v
                break
                
        track_logs = []
        for line in logs:
            if title_lower in line.lower() or (artist and len(artist) > 3 and artist.lower() in line.lower()):
                track_logs.append(line)
        
        retry_count = 0
        if matched_item:
            status = "downloaded"
            status_note = f"Downloaded ({matched_item.get('bitrate', '320k')} • {matched_item.get('size', '')})"
        else:
            is_retrying = any("returned no usable results" in l or "attempt" in l for l in track_logs)
            is_downloading = any("downloading" in l.lower() or "searching" in l.lower() for l in track_logs)
            
            if is_retrying:
                status = "retrying"
                retry_count = sum(1 for l in track_logs if "returned no usable results" in l)
                status_note = f"Retrying with Fallback Engine (Attempts: {max(1, retry_count)})"
            elif is_downloading:
                status = "downloading"
                status_note = "Downloading audio stream..."
            else:
                status = "queued"
                status_note = "Waiting for downloader worker"
                
        triage.append({
            "title": title,
            "artist": artist,
            "query": f"{artist} - {title}" if artist else title,
            "status": status,
            "status_note": status_note,
            "retry_count": retry_count,
            "matched_file": matched_item.get("full_path") if matched_item else None,
            "duration": matched_item.get("duration") if matched_item else None,
            "bitrate": matched_item.get("bitrate") if matched_item else None,
            "size": matched_item.get("size") if matched_item else None,
            "logs": track_logs
        })
        
    return {
        "active_url": active_url,
        "total_tracks": len(meta_tracks),
        "downloaded_count": sum(1 for tr in triage if tr["status"] == "downloaded"),
        "retrying_count": sum(1 for tr in triage if tr["status"] == "retrying"),
        "queued_count": sum(1 for tr in triage if tr["status"] == "queued"),
        "tracks": triage
    }

def get_metrics_summary():
    lib = get_media_library()
    q = get_queue()
    
    total_files = len(lib)
    total_bytes = sum(m.get("size_bytes", 0) for m in lib)
    total_size_mb = round(total_bytes / (1024 * 1024), 1)
    total_size_gb = round(total_bytes / (1024 * 1024 * 1024), 2)
    
    completed_jobs = sum(1 for j in q if j.get("status") == "completed")
    active_jobs = sum(1 for j in q if j.get("status") in ["downloading", "running"])
    paused_jobs = sum(1 for j in q if j.get("status") == "paused")
    queued_jobs = sum(1 for j in q if j.get("status") in ["queued", "retry_scheduled"])
    
    return {
        "total_tracks": total_files,
        "total_size_mb": total_size_mb,
        "total_size_gb": total_size_gb,
        "completed_jobs": completed_jobs,
        "active_jobs": active_jobs,
        "paused_jobs": paused_jobs,
        "queued_jobs": queued_jobs,
        "is_queue_paused": is_queue_paused
    }

def is_item_ready(item):
    if item.get("status") == "queued":
        return True
    if item.get("status") == "retry_scheduled":
        retry_epoch = item.get("retry_epoch", 0)
        return time.time() >= retry_epoch
    return False

def get_job_track_analysis(url, job_id=None):
    if not url or not is_valid_media_service_url(url):
        return {"error": "Invalid or untrusted URL"}
    
    expected_tracks = []
    meta = get_playlist_expected_info(url)
    parsed_url = urllib.parse.urlsplit(url)
    host = (parsed_url.hostname or "").lower()
    
    if host == "spotify.com" or host.endswith(".spotify.com"):
        m = re.search(r'(playlist|album|track|artist)/([a-zA-Z0-9]+)', url)
        if m:
            t_type, t_id = m.group(1), m.group(2)
            embed_url = f"https://open.spotify.com/embed/{t_type}/{t_id}"
            try:
                req = urllib.request.Request(embed_url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"})
                html = urllib.request.urlopen(req, timeout=6).read().decode("utf-8")
                m_json = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html)
                if m_json:
                    data = json.loads(m_json.group(1))
                    entity = data.get("props", {}).get("pageProps", {}).get("state", {}).get("data", {}).get("entity", {})
                    raw_tracks = entity.get("trackList", [])
                    for idx, tr in enumerate(raw_tracks):
                        t_uri = tr.get("uri", "")
                        t_id_single = t_uri.split(":")[-1] if ":" in t_uri else ""
                        single_url = f"https://open.spotify.com/track/{t_id_single}" if t_id_single else url
                        dur_sec = round(tr.get("duration", 0) / 1000)
                        dur_str = f"{dur_sec // 60}:{dur_sec % 60:02d}" if dur_sec > 0 else "N/A"
                        expected_tracks.append({
                            "index": idx + 1,
                            "title": tr.get("title", ""),
                            "artist": tr.get("subtitle", "") or tr.get("artists", ""),
                            "track_url": single_url,
                            "uri": t_uri,
                            "expected_duration": dur_str
                        })
            except Exception as e:
                _ = e
    elif host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com"):
        safe_url = parsed_url.geturl()
        try:
            if "list=" in safe_url:
                cmd = ["uvx", "yt-dlp", "--flat-playlist", "--dump-single-json", "--no-warnings", "--", safe_url]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
                if res.returncode == 0:
                    data = json.loads(res.stdout)
                    for idx, ent in enumerate(data.get("entries", [])):
                        v_id = ent.get("id", "")
                        v_url = f"https://www.youtube.com/watch?v={v_id}" if v_id else url
                        dur_sec = ent.get("duration") or 0
                        dur_str = f"{dur_sec // 60}:{dur_sec % 60:02d}" if dur_sec > 0 else "N/A"
                        expected_tracks.append({
                            "index": idx + 1,
                            "title": ent.get("title", ""),
                            "artist": ent.get("uploader", "") or ent.get("channel", ""),
                            "track_url": v_url,
                            "uri": v_id,
                            "expected_duration": dur_str
                        })
        except Exception as e:
            _ = e

    if not expected_tracks:
        expected_tracks.append({
            "index": 1,
            "title": meta.get("title") or "Track 1",
            "artist": meta.get("artist") or "",
            "track_url": url,
            "uri": "",
            "expected_duration": "N/A"
        })

    history = get_history()
    hist_item = None
    for h in history:
        if (job_id and (h.get("id") == job_id or h.get("job_id") == job_id)) or (h.get("url") == url):
            hist_item = h
            break
            
    total_retries = hist_item.get("retry_count", 0) if hist_item else 0
    last_tried = hist_item.get("completed_at") or hist_item.get("added_at") if hist_item else "Recent"
    job_status = hist_item.get("status", "completed") if hist_item else "completed"

    lib = get_media_library()
    downloaded_tracks = []
    missing_tracks = []
    
    def match_track(tr, lib):
        s_tit = clean_alphanumeric_key(tr.get('title', ''))
        s_art = clean_alphanumeric_key(tr.get('artist', ''))
        for m in lib:
            l_tit = clean_alphanumeric_key(m.get('title', '') or m.get('display_title', '') or m.get('name', ''))
            l_art = clean_alphanumeric_key(m.get('artist', ''))
            if s_tit and l_tit and (s_tit == l_tit or s_tit in l_tit or l_tit in s_tit):
                if not s_art or not l_art or (s_art[:4] in l_art or l_art[:4] in s_art or s_art in l_art or l_art in s_art):
                    return m
        return None

    total_downloaded_bytes = 0
    tagged_count = 0

    for tr in expected_tracks:
        matched = match_track(tr, lib)
        if matched:
            total_downloaded_bytes += matched.get("size_bytes", 0)
            if matched.get("artist") and matched.get("album") and matched.get("album") != "Single":
                tagged_count += 1
            downloaded_tracks.append({
                "index": tr["index"],
                "title": matched.get("display_title") or tr["title"],
                "artist": matched.get("artist") or tr["artist"],
                "album": matched.get("album", "Single"),
                "duration": matched.get("duration", tr.get("expected_duration", "N/A")),
                "bitrate": matched.get("bitrate", "320 kbps"),
                "size": matched.get("size", "N/A"),
                "full_path": matched.get("full_path", ""),
                "track_url": tr["track_url"],
                "status": "Downloaded"
            })
        else:
            missing_tracks.append({
                "index": tr["index"],
                "title": tr["title"],
                "artist": tr["artist"],
                "duration": tr.get("expected_duration", "N/A"),
                "track_url": tr["track_url"],
                "uri": tr.get("uri", ""),
                "retry_count": total_retries,
                "status": "Missing"
            })

    total_exp = len(expected_tracks)
    total_dl = len(downloaded_tracks)
    total_mis = len(missing_tracks)
    pct = round((total_dl / total_exp) * 100) if total_exp > 0 else 0
    dl_mb = round(total_downloaded_bytes / (1024 * 1024), 2)
    est_missing_mb = round(total_mis * 8.5, 1)

    return {
        "url": url,
        "title": meta.get("title") or (hist_item.get("title") if hist_item else "Playlist Details"),
        "status": job_status,
        "total_expected": total_exp,
        "total_downloaded": total_dl,
        "total_missing": total_mis,
        "completion_pct": pct,
        "downloaded_mb": dl_mb,
        "estimated_missing_mb": est_missing_mb,
        "avg_bitrate": "320 kbps (High Quality)",
        "id3_health": f"{round((tagged_count / total_dl) * 100)}%" if total_dl > 0 else "100%",
        "total_retries": total_retries,
        "last_attempt": last_tried,
        "downloaded_tracks": downloaded_tracks,
        "missing_tracks": missing_tracks
    }

def run_download_loop():
    global active_job, current_process
    while True:
        if is_queue_paused:
            time.sleep(2)
            continue
            
        with job_lock:
            queue = get_queue()
            pending = [item for item in queue if is_item_ready(item)]
            if pending and active_job is None:
                job = pending[0]
                active_job = job
                job["status"] = "downloading"
                job["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S")
                if "downloaded_count" not in job:
                    job["downloaded_count"] = 0
                save_queue(queue)
            else:
                job = None
            
        if job:
            url = job["url"]
            mode = job.get("mode", "audio")
            auto_retry = job.get("auto_retry", True)
            
            log(f"🚀 STARTING DOWNLOAD: {url} (Mode: {mode})")
            
            
            home_dir = os.path.expanduser("~")
            base_dir = os.path.dirname(os.path.abspath(__file__))
            script_spotify = os.path.join(base_dir, "scripts", "download_spotify.sh")
            if not os.path.exists(script_spotify):
                script_spotify = os.path.join(home_dir, ".agents", "skills", "media-downloader", "scripts", "download_spotify.sh")

            script_yt = os.path.join(base_dir, "scripts", "download_youtube.sh")
            if not os.path.exists(script_yt):
                script_yt = os.path.join(home_dir, ".agents", "skills", "media-downloader", "scripts", "download_youtube.sh")

            parsed_job_url = urllib.parse.urlsplit(url)
            job_host = (parsed_job_url.hostname or "").lower()
            if job_host == "spotify.com" or job_host.endswith(".spotify.com"):
                cmd = ["bash", script_spotify, url, get_music_dir()]
            else:
                cmd = ["bash", script_yt, url, "--video" if mode == "video" else "--audio", get_music_dir()]
            
            try:
                current_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
                job_downloaded_count = 0
                for line in current_process.stdout:
                    clean_l = line.strip()
                    log(clean_l)
                    if clean_l.startswith("Downloaded ") or "Downloaded \"" in clean_l or "Downloaded '" in clean_l:
                        job_downloaded_count += 1
                        with job_lock:
                            fresh_q = get_queue()
                            target_in_q = next((j for j in fresh_q if j["id"] == job["id"]), None)
                            if target_in_q:
                                target_in_q["downloaded_count"] = job_downloaded_count
                                save_queue(fresh_q)
                    with job_lock:
                        fresh_q = get_queue()
                        target_in_q = next((j for j in fresh_q if j["id"] == job["id"]), None)
                        if target_in_q and target_in_q.get("status") == "cancelled":
                            break
                if current_process:
                    current_process.wait()
            except Exception as e:
                log(f"❌ Process error / cancelled: {e}")
            finally:
                current_process = None
            
            # Sync M3U playlists and organize library; invalidate TTL caches so next request gets fresh data
            organize_and_sync_library()
            invalidate_library_cache()
            get_media_library(force_refresh=True)
            
            with job_lock:
                fresh_queue = get_queue()
                target_job = next((j for j in fresh_queue if j["id"] == job["id"]), None)
                if target_job:
                    if target_job.get("status") in ["cancelled", "paused"]:
                        pass
                    else:
                        expected = target_job.get("expected_count") or 1
                        try:
                            analysis = get_job_track_analysis(url, target_job["id"])
                            actual_downloaded = analysis.get("total_downloaded", 0)
                        except Exception:
                            actual_downloaded = job_downloaded_count
                        target_job["downloaded_count"] = actual_downloaded
                        
                        if "playlist" in url and auto_retry and target_job.get("retry_count", 0) < 5 and (expected is None or actual_downloaded < expected):
                            target_job["retry_count"] = target_job.get("retry_count", 0) + 1
                            target_job["status"] = "retry_scheduled"
                            target_job["retry_epoch"] = time.time() + 120
                            target_job["next_retry"] = time.strftime("%H:%M:%S", time.localtime(time.time() + 120))
                            log(f"🔁 Auto-Schedule: Next retry for missing tracks ({actual_downloaded}/{expected}) at {target_job['next_retry']}")
                        else:
                            if actual_downloaded >= expected and expected > 0:
                                target_job["status"] = "completed"
                                log(f"✅ Download completed ({actual_downloaded}/{expected} tracks) for: {url}")
                            elif actual_downloaded > 0:
                                target_job["status"] = "partial"
                                log(f"⚠️ Download partially completed ({actual_downloaded}/{expected} tracks) for: {url}")
                            else:
                                target_job["status"] = "failed"
                                log(f"❌ Download failed (0/{expected} tracks) for: {url}")
                            
                    record_job_to_history(target_job)
                save_queue(fresh_queue)
                active_job = None
        
        time.sleep(3)

threading.Thread(target=run_download_loop, daemon=True).start()

class ReusableThreadingServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True

class MediaHandler(http.server.SimpleHTTPRequestHandler):
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        
        if parsed.path == "/api/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            
            sync_history_with_queue()
            
            with job_lock:
                queue = get_queue()
                curr = active_job
            
            logs = []
            if os.path.exists(LOG_FILE):
                with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
                    logs = [l.strip() for l in f.readlines() if l.strip()][-40:]
            
            lib = get_media_library()
            metrics = get_metrics_summary()
            playlists = get_playlists_summary()
            history = get_history()
            history_analytics = get_history_analytics()
            explorer = get_library_explorer_data()
            dup_groups, wasted_mb = find_duplicate_and_similar_tracks()
            
            data = {
                "active_job": curr,
                "queue": queue,
                "library": lib,
                "total_media": len(lib),
                "playlists": playlists,
                "metrics": metrics,
                "history": history,
                "history_analytics": history_analytics,
                "explorer": explorer,
                "duplicates_count": len(dup_groups),
                "duplicates_wasted_mb": wasted_mb,
                "triage": get_triage_data(curr, queue, logs),
                "logs": logs
            }
            self.wfile.write(json.dumps(data).encode())
            return
            
        elif parsed.path == "/api/stream":
            query = urllib.parse.parse_qs(parsed.query)
            filepath = query.get("path", [""])[0]
            safe_fp = _safe_path(filepath)
            if safe_fp and os.path.exists(safe_fp) and os.path.isfile(safe_fp):
                self.send_response(200)
                mime = "video/mp4" if safe_fp.endswith((".mp4", ".mkv")) else "audio/mpeg"
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(os.path.getsize(safe_fp)))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                with open(safe_fp, "rb") as f:
                    while chunk := f.read(65536):
                        self.wfile.write(chunk)
                return
            else:
                self.send_response(404)
                self.end_headers()
                return

        elif parsed.path == "/api/cover":
            query = urllib.parse.parse_qs(parsed.query)
            filepath = query.get("path", [""])[0]
            safe_fp = _safe_path(filepath) if filepath else None
            cover = get_cover_path(safe_fp) if safe_fp else None
            safe_cover = _safe_path(cover) if cover else None
            
            if safe_cover and os.path.exists(safe_cover):
                cover = safe_cover
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Cache-Control", "public, max-age=86400")
                self.send_header("Content-Length", str(os.path.getsize(cover)))
                self.end_headers()
                with open(cover, "rb") as f:
                    while chunk := f.read(65536):
                        self.wfile.write(chunk)
                return
            else:
                svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 24 24" fill="#0284c7"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 14.5c-2.49 0-4.5-2.01-4.5-4.5S9.51 7.5 12 7.5s4.5 2.01 4.5 4.5-2.01 4.5-4.5 4.5zm0-5.5c-.55 0-1 .45-1 1s.45 1 1 1 1-.45 1-1-.45-1-1-1z"/></svg>'
                self.send_response(200)
                self.send_header("Content-Type", "image/svg+xml")
                self.end_headers()
                self.wfile.write(svg)
                return

        elif parsed.path == "/api/get_playlist_tracks":
            query = urllib.parse.parse_qs(parsed.query)
            m3u_path = query.get("path", [""])[0]
            safe_m3u = _safe_path(m3u_path)
            tracks = []
            if safe_m3u and os.path.exists(safe_m3u):
                try:
                    with open(safe_m3u, "r", encoding="utf-8", errors="ignore") as f:
                        lines = [l.strip() for l in f.readlines() if l.strip() and not l.startswith("#")]
                    for l in lines:
                        safe_track = _safe_path(l)
                        if safe_track and os.path.exists(safe_track):
                            stat = os.stat(safe_track)
                            info = get_file_audio_info(safe_track, stat.st_mtime)
                            tracks.append({
                                "name": os.path.basename(safe_track),
                                "display_title": info.get("title") or os.path.basename(safe_track),
                                "artist": info.get("artist", ""),
                                "album": info.get("album", ""),
                                "duration": info.get("duration", "N/A"),
                                "bitrate": info.get("bitrate", "N/A"),
                                "full_path": safe_track
                            })
                except Exception as err:
                    _ = err
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"tracks": tracks}).encode())
            return

        elif parsed.path == "/api/duplicates":
            groups, wasted = find_duplicate_and_similar_tracks()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(json.dumps({
                "groups": groups,
                "total_wasted_mb": wasted,
                "total_groups": len(groups)
            }).encode())
            return

        elif parsed.path in ["/favicon.ico", "/favicon.svg"]:
            svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><defs><linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#6366f1"/><stop offset="100%" stop-color="#a855f7"/></linearGradient></defs><rect width="100" height="100" rx="26" fill="url(#g)"/><path d="M36 68 A12 12 0 1 1 48 56 V28 H74 V40 H58 V68 A12 12 0 1 1 36 68 Z" fill="#ffffff"/></svg>'
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(svg.encode("utf-8"))
            return

        elif parsed.path == "/api/settings":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True, "settings": get_settings()}).encode())
            return

        elif parsed.path in ["/", "/index.html"]:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()
            self.wfile.write(get_ui_html().encode("utf-8"))
            return

        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"error": "Not Found"}).encode("utf-8"))


    def do_POST(self):
        global active_job, current_process, is_queue_paused
        parsed = urllib.parse.urlparse(self.path)
        
        if parsed.path == "/api/download":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode())
            url = body.get("url", "").strip()
            mode = body.get("mode", "audio")
            auto_retry = body.get("auto_retry", True)
            
            if not url:
                self.send_response(400)
                self.end_headers()
                return

            norm_url = normalize_url(url)
            
            with job_lock:
                q = get_queue()
                for item in q:
                    if item["status"] in ["downloading", "queued", "retry_scheduled"]:
                        if normalize_url(item.get("url", "")) == norm_url:
                            self.send_response(200)
                            self.send_header("Content-Type", "application/json")
                            self.end_headers()
                            self.wfile.write(json.dumps({
                                "success": False,
                                "duplicate": True,
                                "message_en": f"⚠️ This URL is already downloading or in queue ({item['status']}).",
                                "message_es": f"⚠️ Esta URL ya está actualmente en proceso o en cola ({item['status']})."
                            }).encode())
                            return
                
                job_id = f"job_{int(time.time())}"
                meta = get_playlist_expected_info(url)
                new_job = {
                    "id": job_id,
                    "url": url,
                    "title": meta.get("title") or url,
                    "expected_count": meta.get("expected_count"),
                    "mode": mode,
                    "auto_retry": auto_retry,
                    "status": "queued",
                    "added_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "retry_count": 0,
                    "downloaded_count": 0
                }
                q.insert(0, new_job)
                save_queue(q)
                record_job_to_history(new_job, "queued")
                log(f"📥 New URL added to queue: {url} (Expected: {meta.get('expected_count')} tracks)")
            
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True, "duplicate": False, "job_id": job_id}).encode())
            return
            
        elif parsed.path == "/api/job_control":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode())
            job_id = body.get("id", "")
            action = body.get("action", "")
            
            with job_lock:
                q = get_queue()
                target = next((j for j in q if j["id"] == job_id), None)
                
                if target:
                    if action == "pause":
                        target["status"] = "paused"
                        if active_job and active_job["id"] == job_id and current_process:
                            try:
                                os.killpg(os.getpgid(current_process.pid), signal.SIGSTOP)
                                log(f"⏸️ Job paused by user: {target['url']}")
                            except Exception as err:
                                _ = err
                                
                    elif action == "resume":
                        if active_job and active_job["id"] == job_id and current_process:
                            try:
                                os.killpg(os.getpgid(current_process.pid), signal.SIGCONT)
                                target["status"] = "downloading"
                                log(f"▶️ Job resumed: {target['url']}")
                            except Exception:
                                target["status"] = "queued"
                        else:
                            target["status"] = "queued"
                            log(f"▶️ Queued job resumed: {target['url']}")
                            
                    elif action == "restart":
                        if active_job and active_job["id"] == job_id and current_process:
                            try:
                                os.killpg(os.getpgid(current_process.pid), signal.SIGKILL)
                            except Exception as err:
                                _ = err
                            active_job = None
                            current_process = None
                        target["status"] = "queued"
                        target["retry_count"] = 0
                        target["retry_epoch"] = 0
                        target["next_retry"] = None
                        target["auto_retry"] = True
                        log(f"🔄 Job restarted by user: {target['url']}")
                        
                    elif action == "cancel":
                        target["status"] = "cancelled"
                        target["auto_retry"] = False
                        if active_job and active_job["id"] == job_id and current_process:
                            try:
                                os.killpg(os.getpgid(current_process.pid), signal.SIGKILL)
                                log(f"🛑 Active process terminated by user: {target['url']}")
                            except Exception as err:
                                _ = err
                            active_job = None
                            current_process = None
                        try:
                            subprocess.run(["pkill", "-9", "-f", "spotdl|yt-dlp|download_spotify"], capture_output=True)
                        except Exception as err:
                            _ = err
                            
                    save_queue(q)
                    record_job_to_history(target, target.get("status"))
                    
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True}).encode())
            return

        elif parsed.path == "/api/delete_job":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode())
            job_id = body.get("id", "")
            
            with job_lock:
                q = get_queue()
                target = next((j for j in q if j["id"] == job_id), None)
                if target and active_job and active_job["id"] == job_id and current_process:
                    try:
                        os.killpg(os.getpgid(current_process.pid), signal.SIGKILL)
                    except Exception as err:
                        _ = err
                    active_job = None
                    current_process = None
                    try:
                        subprocess.run(["pkill", "-9", "-f", "spotdl|yt-dlp|download_spotify"], capture_output=True)
                    except Exception as err:
                        _ = err
                q = [j for j in q if j["id"] != job_id]
                save_queue(q)
                
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True}).encode())
            return

        elif parsed.path == "/api/open_playlist_vlc":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode())
            folder_path = body.get("folder_path", "")
            m3u = sync_playlist_m3u(folder_path)
            
            if m3u and os.path.exists(m3u):
                subprocess.Popen(["vlc", m3u], start_new_session=True)
                
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True, "m3u": m3u}).encode())
            return

        elif parsed.path == "/api/open_local":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode())
            filepath = body.get("path", "")
            action = body.get("action", "vlc")

            safe_fp = _safe_path(filepath)
            if safe_fp and os.path.exists(safe_fp):
                env = os.environ.copy()
                if "DISPLAY" not in env:
                    env["DISPLAY"] = ":0"
                if "WAYLAND_DISPLAY" not in env:
                    env["WAYLAND_DISPLAY"] = "wayland-0"
                if "XDG_RUNTIME_DIR" not in env:
                    env["XDG_RUNTIME_DIR"] = f"/run/user/{os.getuid()}"

                if action == "vlc":
                    subprocess.Popen(["vlc", safe_fp], env=env, start_new_session=True)
                elif action == "folder":
                    folder = os.path.dirname(safe_fp) if os.path.isfile(safe_fp) else safe_fp
                    subprocess.Popen(["xdg-open", folder], env=env, start_new_session=True)
            elif filepath and not safe_fp:
                self.send_response(403)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": "Access denied: path outside allowed directories"}).encode())
                return

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True}).encode())
            return

        elif parsed.path == "/api/retry_single_track":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode())
            query = body.get("query", "").strip()
            if query:
                job_id = f"job_track_{int(time.time())}"
                with job_lock:
                    q = get_queue()
                    q.insert(0, {
                        "id": job_id,
                        "url": query,
                        "title": query,
                        "expected_count": 1,
                        "mode": "audio",
                        "auto_retry": False,
                        "status": "queued",
                        "added_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "retry_count": 0,
                        "downloaded_count": 0
                    })
                    save_queue(q)
                    log(f"⚡ Single Track Forced Download Enqueued: {query}")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True}).encode())
            return

        elif parsed.path == "/api/restart_incomplete_jobs":
            with job_lock:
                q = get_queue()
                count = 0
                for item in q:
                    is_incomplete = (
                        item.get("status") in ["cancelled", "paused", "retry_scheduled", "failed", "partial"]
                        or (item.get("status") == "completed" and (item.get("expected_count") or 1) > (item.get("downloaded_count") or 0))
                    )
                    if is_incomplete:
                        item["status"] = "queued"
                        item["retry_count"] = 0
                        item["retry_epoch"] = 0
                        item["next_retry"] = None
                        item["auto_retry"] = True
                        count += 1
                save_queue(q)
                log(f"🔄 Restarted {count} incomplete/cancelled download jobs.")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True, "restarted": count}).encode())
            return

        elif parsed.path == "/api/clear_completed":
            with job_lock:
                q = get_queue()
                q = [j for j in q if j.get("status") not in ["completed", "cancelled"]]
                save_queue(q)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True}).encode())
            return

        elif parsed.path == "/api/delete_history_item":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode())
            hist_id = body.get("id", "")
            hist = get_history()
            hist = [h for h in hist if h.get("id") != hist_id and h.get("job_id") != hist_id]
            save_history(hist)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True}).encode())
            return

        elif parsed.path == "/api/clear_history":
            save_history([])
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True}).encode())
            return

        elif parsed.path == "/api/redownload_history_item":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode())
            url = body.get("url", "").strip()
            mode = body.get("mode", "audio")
            title = body.get("title") or url
            if url:
                job_id = f"job_{int(time.time())}"
                meta = get_playlist_expected_info(url)
                with job_lock:
                    q = get_queue()
                    new_job = {
                        "id": job_id,
                        "url": url,
                        "title": meta.get("title") or title,
                        "expected_count": meta.get("expected_count"),
                        "mode": mode,
                        "auto_retry": True,
                        "status": "queued",
                        "added_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "retry_count": 0,
                        "downloaded_count": 0
                    }
                    q.insert(0, new_job)
                    save_queue(q)
                    record_job_to_history(new_job, "queued")
                    log(f"🔄 Re-download from history enqueued: {url}")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True}).encode())
            return

        elif parsed.path == "/api/search_metadata":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode())
            query = body.get("query", "").strip()
            limit = body.get("limit", 8)
            results = search_metadata_online(query, limit)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"results": results}).encode())
            return

        elif parsed.path == "/api/update_metadata":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode())
            filepath = body.get("filepath", "")
            safe_fp = _safe_path(filepath)
            if not safe_fp:
                self.send_response(403)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": "Access denied: path outside allowed directories"}).encode())
                return
            res = update_track_metadata(safe_fp, body)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(res).encode())
            return

        elif parsed.path == "/api/delete_track":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode())
            filepath = body.get("filepath", "")
            safe_fp = _safe_path(filepath)
            if not safe_fp:
                self.send_response(403)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": "Access denied: path outside allowed directories"}).encode())
                return
            res = delete_track_from_library(safe_fp)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(res).encode())
            return

        elif parsed.path == "/api/clean_duplicates_auto":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode()) if length > 0 else {}
            group_keys = body.get("group_keys")
            res = clean_all_duplicates_auto(group_keys)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(res).encode())
            return
        elif parsed.path == "/api/lookup_url_info":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}
            url = body.get("url", "").strip()
            info = get_playlist_expected_info(url)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(info).encode("utf-8"))
            return
        elif parsed.path == "/api/analyze_job_tracks":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}
            url = body.get("url", "").strip()
            job_id = body.get("id")
            res = get_job_track_analysis(url, job_id)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(res).encode("utf-8"))
            return
        elif parsed.path == "/api/redownload_missing_tracks":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}
            playlist_url = body.get("url", "").strip()
            missing_tracks = body.get("tracks", [])
            mode = body.get("mode", "audio")
            
            queued_count = 0
            with job_lock:
                q = get_queue()
                if missing_tracks:
                    for tr in missing_tracks:
                        t_url = tr.get("track_url") or tr.get("url")
                        if not t_url:
                            continue
                        t_title = f"{tr.get('artist', '')} - {tr.get('title', '')}".strip(" - ") or t_url
                        job_id = f"job_{int(time.time())}_{queued_count}"
                        new_job = {
                            "id": job_id,
                            "url": t_url,
                            "title": t_title,
                            "expected_count": 1,
                            "mode": mode,
                            "auto_retry": True,
                            "status": "queued",
                            "added_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "retry_count": 0,
                            "downloaded_count": 0
                        }
                        q.insert(0, new_job)
                        record_job_to_history(new_job, "queued")
                        queued_count += 1
                elif playlist_url:
                    job_id = f"job_{int(time.time())}"
                    meta = get_playlist_expected_info(playlist_url)
                    new_job = {
                        "id": job_id,
                        "url": playlist_url,
                        "title": meta.get("title") or playlist_url,
                        "expected_count": meta.get("expected_count"),
                        "mode": mode,
                        "auto_retry": True,
                        "status": "queued",
                        "added_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "retry_count": 0,
                        "downloaded_count": 0
                    }
                    q.insert(0, new_job)
                    record_job_to_history(new_job, "queued")
                    queued_count = 1
                    
                save_queue(q)
                log(f"⚡ Re-download missing tracks enqueued: {queued_count} tasks")
                
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True, "queued_count": queued_count}).encode("utf-8"))
            return
        elif parsed.path == "/api/toggle_pause_queue":
            with job_lock:
                is_queue_paused = not is_queue_paused
                if is_queue_paused:
                    log("⏸️ Global download queue PAUSED by user.")
                    if active_job and current_process:
                        try:
                            os.killpg(os.getpgid(current_process.pid), signal.SIGSTOP)
                            active_job["status"] = "paused"
                        except Exception as err:
                            _ = err
                else:
                    log("▶️ Global download queue RESUMED by user.")
                    if active_job and current_process:
                        try:
                            os.killpg(os.getpgid(current_process.pid), signal.SIGCONT)
                            active_job["status"] = "downloading"
                        except Exception as err:
                            _ = err
                            
                q = get_queue()
                save_queue(q)
                
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True, "is_queue_paused": is_queue_paused}).encode("utf-8"))
            return
            
        elif parsed.path == "/api/settings":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode())
            new_settings = body.get("settings") if "settings" in body and isinstance(body.get("settings"), dict) else body
            current_s = get_settings()
            current_s.update(new_settings)
            
            # Sanitize and create download directory
            if "download_dir" in new_settings:
                d = os.path.expanduser(str(new_settings["download_dir"]).strip())
                if d:
                    os.makedirs(d, exist_ok=True)
                    current_s["download_dir"] = d
                    
            save_settings(current_s)
            organize_and_sync_library()
            invalidate_library_cache()
            
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True, "settings": current_s}).encode())
            return
            
        elif parsed.path == "/api/test_notification":
            try:
                subprocess.Popen(["notify-send", "-i", "audio-speakers", "-a", "Media Studio", "🔔 Media Studio Test", "Desktop notifications are working perfectly!"])
            except Exception as err:
                _ = err
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True}).encode())
            return
            
        self.send_response(404)
        self.end_headers()

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")

def get_ui_html():
    template_path = os.path.join(TEMPLATES_DIR, "index.html")
    if os.path.exists(template_path):
        with open(template_path, "r", encoding="utf-8") as f:
            return f.read()
    # Fallback to local search
    fallback = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "index.html")
    if os.path.exists(fallback):
        with open(fallback, "r", encoding="utf-8") as f:
            return f.read()
    return "<html><body><h1>Media Studio UI Template not found</h1></body></html>"

if __name__ == "__main__":
    with ReusableThreadingServer(("", PORT), MediaHandler) as httpd:
        print(f"🚀 Media Studio Web Server running on http://localhost:{PORT}")
        httpd.serve_forever()
