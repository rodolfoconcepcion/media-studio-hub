#!/usr/bin/env python3
import http.server
import socketserver
import json
import os
import re
import signal
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import hashlib

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
        except:
            return DEFAULT_SETTINGS.copy()
    return DEFAULT_SETTINGS.copy()

def save_settings(s):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2)
    except:
        pass

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
        except:
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
        except:
            return []
    return []

def save_history(hist):
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(hist, f, indent=2)
    except:
        pass

active_job = None
current_process = None
job_lock = threading.Lock()
is_queue_paused = False

def normalize_url(u):
    if not u:
        return ""
    return u.strip().split("?")[0].rstrip("/")

def record_job_to_history(job, final_status=None):
    if not job or not job.get("url"):
        return
    history = get_history()
    job_id = job.get("id") or f"job_{int(time.time())}"
    
    expected = job.get("expected_count") or 1
    downloaded = job.get("downloaded_count", 0)
    status = final_status or job.get("status") or "completed"
    if status == "completed" and expected and downloaded < expected:
        status = "partial"
    success_pct = round((downloaded / expected) * 100, 1) if expected else 100.0
    
    start_time_str = job.get("added_at") or time.strftime("%Y-%m-%d %H:%M:%S")
    completed_time_str = job.get("last_run") or time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        t_start = time.mktime(time.strptime(start_time_str, "%Y-%m-%d %H:%M:%S"))
        t_end = time.time()
        dur_secs = max(1, int(t_end - t_start))
        mins, secs = divmod(dur_secs, 60)
        dur_display = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"
    except:
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
    
    return {
        "total_jobs": total_jobs,
        "completed_jobs": completed_jobs,
        "cancelled_jobs": cancelled_jobs,
        "partial_jobs": partial_jobs,
        "total_tracks_downloaded": total_tracks_downloaded,
        "total_expected": total_expected,
        "avg_success_rate": avg_success_rate
    }

def init_history_from_queue_and_library():
    sync_history_with_queue()

init_history_from_queue_and_library()

def get_playlist_expected_info(url):
    info = {"expected_count": None, "title": None, "type": "Media", "artist": None}
    if not url:
        return info
    try:
        if "spotify.com" in url:
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
        elif "youtube.com" in url or "youtu.be" in url:
            if "list=" in url:
                cmd = ["uvx", "yt-dlp", "--flat-playlist", "--dump-single-json", "--no-warnings", url]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=6)
                if res.returncode == 0:
                    data = json.loads(res.stdout)
                    info["expected_count"] = len(data.get("entries", []))
                    info["title"] = data.get("title") or "YouTube Playlist"
                    info["type"] = "Playlist"
            else:
                cmd = ["uvx", "yt-dlp", "--dump-single-json", "--no-warnings", url]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                if res.returncode == 0:
                    data = json.loads(res.stdout)
                    info["expected_count"] = 1
                    info["title"] = data.get("title") or "YouTube Video"
                    info["type"] = "Video / Audio"
                else:
                    info["expected_count"] = 1
    except Exception as e:
        pass
    return info

meta_cache = {}
if os.path.exists(CACHE_FILE):
    try:
        with open(CACHE_FILE, "r") as f:
            meta_cache = json.load(f)
    except:
        meta_cache = {}

def save_cache():
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(meta_cache, f)
    except:
        pass

def log(msg):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"[{timestamp}] {msg}\n")

def cleanup_stale_queue_on_boot():
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
    music_dir = get_music_dir()
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
    except:
        return None

def get_cover_path(filepath):
    if not filepath or not os.path.exists(filepath):
        return None
    file_hash = hashlib.md5(filepath.encode("utf-8")).hexdigest()
    cover_file = os.path.join(COVERS_DIR, f"{file_hash}.jpg")
    
    if os.path.exists(cover_file) and os.path.getsize(cover_file) > 0:
        return cover_file
        
    try:
        cmd = ["ffmpeg", "-y", "-i", filepath, "-an", "-vcodec", "copy", cover_file]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
        if os.path.exists(cover_file) and os.path.getsize(cover_file) > 0:
            return cover_file
    except:
        pass

    parent_dir = os.path.dirname(filepath)
    if os.path.exists(parent_dir):
        for name in ["cover.jpg", "folder.jpg", "album.jpg", "cover.png", "artwork.jpg"]:
            local_art = os.path.join(parent_dir, name)
            if os.path.exists(local_art) and os.path.getsize(local_art) > 0:
                return local_art

        try:
            for f in os.listdir(parent_dir):
                if f.endswith((".mp3", ".m4a", ".flac")) and f != os.path.basename(filepath):
                    sib_path = os.path.join(parent_dir, f)
                    sib_hash = hashlib.md5(sib_path.encode("utf-8")).hexdigest()
                    sib_cover = os.path.join(COVERS_DIR, f"{sib_hash}.jpg")
                    if os.path.exists(sib_cover) and os.path.getsize(sib_cover) > 0:
                        return sib_cover
        except:
            pass

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
    except:
        pass
        
    meta_cache[filepath] = info
    return info

print("Starting server...")
EOF
