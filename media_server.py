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

def normalize_url(u):
    if not u:
        return ""
    return u.strip().split("?")[0].rstrip("/")

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
    except:
        return None

def get_cover_path(filepath):
    if not filepath or not os.path.exists(filepath):
        return None
    file_hash = hashlib.md5(filepath.encode("utf-8")).hexdigest()
    cover_file = os.path.join(COVERS_DIR, f"{file_hash}.jpg")
    
    if os.path.exists(cover_file) and os.path.getsize(cover_file) > 0:
        return cover_file
        
    # 1. Try extracting embedded APIC from the file with ffmpeg
    try:
        cmd = ["ffmpeg", "-y", "-i", filepath, "-an", "-vcodec", "copy", cover_file]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
        if os.path.exists(cover_file) and os.path.getsize(cover_file) > 0:
            return cover_file
    except:
        pass

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
        except:
            pass

    # 4. Auto-Fetch & Auto-Heal from online database (iTunes / Apple Music)
    try:
        info = get_file_audio_info(filepath, os.path.getmtime(filepath))
        title = info.get("title") or os.path.basename(filepath).replace(".mp3", "")
        artist = info.get("artist") or ""
        q = f"{artist} {title}".strip()
        if q:
            url = f"https://itunes.apple.com/search?term={urllib.parse.quote(q)}&media=music&entity=song&limit=1"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=4) as res:
                data = json.loads(res.read().decode())
                results = data.get("results", [])
                if results:
                    art_url = results[0].get("artworkUrl100", "").replace("100x100bb.jpg", "1000x1000bb.jpg")
                    if art_url:
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
                            except:
                                pass
                            return cover_file
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
            
            raw_bpm = tags.get("bpm", tags.get("TBPM", ""))
            if raw_bpm:
                val = raw_bpm[0] if isinstance(raw_bpm, list) else str(raw_bpm)
                info["bpm"] = f"{val} BPM" if not val.endswith("BPM") else val
            else:
                calc = calculate_bpm(filepath)
                info["bpm"] = f"{calc} BPM" if calc else "N/A"
    except:
        pass
        
    meta_cache[filepath] = info
    return info

import shutil, re
try:
    import numpy as np
    import scipy.signal as signal
except ImportError:
    np = None
    signal = None

from mutagen.easyid3 import EasyID3
from mutagen.mp3 import MP3

try:
    EasyID3.RegisterTextKey('bpm', 'TBPM')
except:
    pass

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
    except:
        pass

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
            except:
                pass
                
        return final_bpm
    except:
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
    except:
        pass
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
    except:
        pass
    return results

def update_track_metadata(filepath, meta):
    if not os.path.exists(filepath):
        return {"success": False, "error": "File not found"}
    try:
        try:
            audio = EasyID3(filepath)
        except:
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
    if cover_url and cover_url.startswith("http"):
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
        except Exception:
            pass

    artist = clean_metadata_name(meta.get("artist") or "Unknown Artist")
    album = clean_metadata_name(meta.get("album") or "Single")
    title = clean_metadata_name(meta.get("title") or os.path.splitext(os.path.basename(filepath))[0])
    
    raw_track = str(meta.get("track_number", "1")).split("/")[0].strip()
    try:
        track_num = f"{int(raw_track):02d}"
    except:
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
                except:
                    break

    organize_and_sync_library()
    return {"success": True, "new_path": final_path}

def delete_track_from_library(filepath):
    if not os.path.exists(filepath):
        return {"success": False, "error": "File not found"}
    try:
        os.remove(filepath)
        if filepath in meta_cache:
            del meta_cache[filepath]
        base = os.path.expanduser("~/Music")
        parent = os.path.dirname(filepath)
        while parent and parent != base and not os.listdir(parent):
            try:
                os.rmdir(parent)
                parent = os.path.dirname(parent)
            except:
                break
        organize_and_sync_library()
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
                except:
                    pass

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
            fp = it['path']
            if os.path.exists(fp):
                try:
                    freed_bytes += os.path.getsize(fp)
                    os.remove(fp)
                    if fp in meta_cache:
                        del meta_cache[fp]
                    deleted_count += 1
                except:
                    pass
                    
    # Clean empty directories
    base = os.path.expanduser("~/Music")
    for root, dirs, files in os.walk(base, topdown=False):
        if root == base or root.endswith("_PLAYLISTS_") or root.endswith("_UNKNOWN_"):
            continue
        if not os.listdir(root):
            try: os.rmdir(root)
            except: pass
            
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
        except:
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
    except:
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
            except: pass

    for leg_m in ["[Misc]", "Misc", "Unknown", "Unknown Artist"]:
        legacy_m_dir = os.path.join(base, leg_m)
        if os.path.exists(legacy_m_dir):
            os.makedirs(misc_dir, exist_ok=True)
            for root, dirs, files in os.walk(legacy_m_dir):
                for f in files:
                    if f.endswith((".mp3", ".m4a", ".flac")):
                        shutil.move(os.path.join(root, f), os.path.join(misc_dir, f))
            try: shutil.rmtree(legacy_m_dir)
            except: pass
        
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
                    if not artist or artist.lower() in ["unknown", "unknown artist"] or not album or album.lower() in ["single", "unknown"]:
                        query = f"{artist} {title}".strip() if artist and artist.lower() not in ["unknown", "unknown artist"] else title
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
                        actual_path = target_file
                    else:
                        actual_path = full_path
                        
                    all_tracks.append(actual_path)
                    if genre and genre != "Unknown":
                        genres_map.setdefault(genre, []).append(actual_path)
                    if album and album not in ["Single", "Unknown"]:
                        albums_map.setdefault(f"{artist} - {album}", []).append(actual_path)
                except:
                    pass

    # Clean empty directories
    for root, dirs, files in os.walk(base, topdown=False):
        if root == base or root == playlists_dir or root == misc_dir:
            continue
        if not os.listdir(root):
            try: os.rmdir(root)
            except: pass

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

def get_media_library():
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
                    except:
                        pass
                        
    if cache_dirty:
        save_cache()
        
    items.sort(key=lambda x: x["mtime_raw"], reverse=True)
    return items

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
                except:
                    pass
                    
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
    if "spotify.com" in url:
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
            except:
                pass
    
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
        status = "queued"
        status_note = "In queue"
        
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
    if not url:
        return {"error": "Invalid URL"}
    
    expected_tracks = []
    meta = get_playlist_expected_info(url)
    
    if "spotify.com" in url:
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
                pass
    elif "youtube.com" in url or "youtu.be" in url:
        try:
            if "list=" in url:
                cmd = ["uvx", "yt-dlp", "--flat-playlist", "--dump-single-json", "--no-warnings", url]
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
            pass

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
            
            initial_count = count_music_files()
            
            base_dir = os.path.dirname(os.path.abspath(__file__))
            script_spotify = os.path.join(base_dir, "scripts", "download_spotify.sh")
            if not os.path.exists(script_spotify):
                script_spotify = "/home/rodolfo/.agents/skills/media-downloader/scripts/download_spotify.sh"

            script_yt = os.path.join(base_dir, "scripts", "download_youtube.sh")
            if not os.path.exists(script_yt):
                script_yt = "/home/rodolfo/.agents/skills/media-downloader/scripts/download_youtube.sh"

            if "spotify.com" in url:
                cmd = [script_spotify, url, get_music_dir()]
            else:
                cmd = [script_yt, url, "--video" if mode == "video" else "--audio", get_music_dir()]
            
            try:
                current_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
                for line in current_process.stdout:
                    log(line.strip())
                    # Periodic progress sync with fresh queue check
                    curr_c = count_music_files()
                    with job_lock:
                        fresh_q = get_queue()
                        target_in_q = next((j for j in fresh_q if j["id"] == job["id"]), None)
                        if target_in_q and target_in_q.get("status") == "cancelled":
                            break
                        if target_in_q:
                            target_in_q["downloaded_count"] = max(target_in_q.get("downloaded_count", 0), curr_c)
                            save_queue(fresh_q)
                if current_process:
                    current_process.wait()
            except Exception as e:
                log(f"❌ Process error / cancelled: {e}")
            finally:
                current_process = None
            
            # Sync M3U playlists and organize library
            organize_and_sync_library()
            get_media_library()
            final_count = count_music_files()
            
            with job_lock:
                fresh_queue = get_queue()
                target_job = next((j for j in fresh_queue if j["id"] == job["id"]), None)
                if target_job:
                    if target_job.get("status") in ["cancelled", "paused"]:
                        pass
                    else:
                        expected = target_job.get("expected_count") or 1
                        if expected == 1 or "/track/" in url:
                            actual_downloaded = 1
                        else:
                            try:
                                analysis = get_job_track_analysis(url, target_job["id"])
                                actual_downloaded = analysis.get("total_downloaded", 0)
                            except:
                                actual_downloaded = min(expected, final_count)
                        target_job["downloaded_count"] = actual_downloaded
                        
                        if "playlist" in url and auto_retry and target_job.get("retry_count", 0) < 5 and (expected is None or actual_downloaded < expected):
                            target_job["retry_count"] = target_job.get("retry_count", 0) + 1
                            target_job["status"] = "retry_scheduled"
                            target_job["retry_epoch"] = time.time() + 120
                            target_job["next_retry"] = time.strftime("%H:%M:%S", time.localtime(time.time() + 120))
                            log(f"🔁 Auto-Schedule: Next retry for missing tracks ({actual_downloaded}/{expected}) at {target_job['next_retry']}")
                        else:
                            target_job["status"] = "completed"
                            log(f"✅ Download completed ({actual_downloaded}/{expected} tracks) for: {url}")
                            
                    record_job_to_history(target_job)
                save_queue(fresh_queue)
                active_job = None
        
        time.sleep(3)

threading.Thread(target=run_download_loop, daemon=True).start()

class ReusableThreadingServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True

class MediaHandler(http.server.SimpleHTTPRequestHandler):
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
            if os.path.exists(filepath) and os.path.isfile(filepath):
                self.send_response(200)
                mime = "video/mp4" if filepath.endswith((".mp4", ".mkv")) else "audio/mpeg"
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(os.path.getsize(filepath)))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                with open(filepath, "rb") as f:
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
            cover = get_cover_path(filepath)
            
            if cover and os.path.exists(cover):
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
            tracks = []
            if os.path.exists(m3u_path):
                try:
                    with open(m3u_path, "r", encoding="utf-8", errors="ignore") as f:
                        lines = [l.strip() for l in f.readlines() if l.strip() and not l.startswith("#")]
                    for l in lines:
                        if os.path.exists(l):
                            stat = os.stat(l)
                            info = get_file_audio_info(l, stat.st_mtime)
                            tracks.append({
                                "name": os.path.basename(l),
                                "display_title": info.get("title") or os.path.basename(l),
                                "artist": info.get("artist", ""),
                                "album": info.get("album", ""),
                                "duration": info.get("duration", "N/A"),
                                "bitrate": info.get("bitrate", "N/A"),
                                "full_path": l
                            })
                except:
                    pass
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
            self.wfile.write(HTML_UI.encode())
            return
            
        super().do_GET()

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
                            except:
                                pass
                                
                    elif action == "resume":
                        if active_job and active_job["id"] == job_id and current_process:
                            try:
                                os.killpg(os.getpgid(current_process.pid), signal.SIGCONT)
                                target["status"] = "downloading"
                                log(f"▶️ Job resumed: {target['url']}")
                            except:
                                target["status"] = "queued"
                        else:
                            target["status"] = "queued"
                            log(f"▶️ Queued job resumed: {target['url']}")
                            
                    elif action == "restart":
                        if active_job and active_job["id"] == job_id and current_process:
                            try:
                                os.killpg(os.getpgid(current_process.pid), signal.SIGKILL)
                            except:
                                pass
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
                            except:
                                pass
                            active_job = None
                            current_process = None
                        try:
                            subprocess.run(["pkill", "-9", "-f", "spotdl|yt-dlp|download_spotify"], capture_output=True)
                        except Exception:
                            pass
                            
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
                    except:
                        pass
                    active_job = None
                    current_process = None
                    try:
                        subprocess.run(["pkill", "-9", "-f", "spotdl|yt-dlp|download_spotify"], capture_output=True)
                    except Exception:
                        pass
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
            
            if os.path.exists(filepath):
                env = os.environ.copy()
                if "DISPLAY" not in env:
                    env["DISPLAY"] = ":0"
                if "WAYLAND_DISPLAY" not in env:
                    env["WAYLAND_DISPLAY"] = "wayland-0"
                if "XDG_RUNTIME_DIR" not in env:
                    env["XDG_RUNTIME_DIR"] = f"/run/user/{os.getuid()}"
                    
                if action == "vlc":
                    subprocess.Popen(["vlc", filepath], env=env, start_new_session=True)
                elif action == "folder":
                    folder = os.path.dirname(filepath) if os.path.isfile(filepath) else filepath
                    subprocess.Popen(["xdg-open", folder], env=env, start_new_session=True)
                    
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
                    if item.get("status") in ["cancelled", "paused", "retry_scheduled"]:
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
            res = update_track_metadata(filepath, body)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(res).encode())
            return

        elif parsed.path == "/api/delete_track":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode())
            filepath = body.get("filepath", "")
            res = delete_track_from_library(filepath)
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
                        except:
                            pass
                else:
                    log("▶️ Global download queue RESUMED by user.")
                    if active_job and current_process:
                        try:
                            os.killpg(os.getpgid(current_process.pid), signal.SIGCONT)
                            active_job["status"] = "downloading"
                        except:
                            pass
                            
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
            
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True, "settings": current_s}).encode())
            return
            
        elif parsed.path == "/api/test_notification":
            try:
                subprocess.Popen(["notify-send", "-i", "audio-speakers", "-a", "Media Studio", "🔔 Media Studio Test", "Desktop notifications are working perfectly!"])
            except:
                pass
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True}).encode())
            return
            
        self.send_response(404)
        self.end_headers()

HTML_UI = """<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate" />
    <meta http-equiv="Pragma" content="no-cache" />
    <meta http-equiv="Expires" content="0" />
    <title>Media Studio • YouTube & Spotify Control Center</title>
    <style>
        :root {
            --bg-body: #090d16;
            --bg-card: #111827;
            --bg-item: #1e293b;
            --bg-input: #030712;
            --border: #1f2937;
            --border-item: #334155;
            --text-main: #f1f5f9;
            --text-sub: #94a3b8;
            --text-accent: #38bdf8;
            --btn-primary: linear-gradient(135deg, #0284c7, #0369a1);
            --term-bg: #030712;
            --term-text: #a7f3d0;
            --pill-bg: #0f172a;
            --player-bg: #0f172a;
            --btn-active-bg: #0284c7;
            --btn-active-text: #ffffff;
        }

        [data-theme="light"] {
            --bg-body: #f1f5f9;
            --bg-card: #ffffff;
            --bg-item: #f8fafc;
            --bg-input: #ffffff;
            --border: #cbd5e1;
            --border-item: #94a3b8;
            --text-main: #0f172a;
            --text-sub: #475569;
            --text-accent: #0284c7;
            --btn-primary: linear-gradient(135deg, #0284c7, #0369a1);
            --term-bg: #0f172a;
            --term-text: #34d399;
            --pill-bg: #e2e8f0;
            --player-bg: #ffffff;
            --btn-active-bg: #0284c7;
            --btn-active-text: #ffffff;
        }

        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: var(--bg-body);
            color: var(--text-main);
            padding: 24px;
            padding-bottom: 120px;
            transition: background-color 0.25s ease, color 0.25s ease;
        }
        .container { max-width: 1280px; margin: 0 auto; }
        
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding-bottom: 20px;
            border-bottom: 2px solid var(--border);
            margin-bottom: 24px;
            flex-wrap: wrap;
            gap: 16px;
        }
        .header h1 { font-size: 1.6rem; color: var(--text-accent); display: flex; align-items: center; gap: 10px; }
        
        .controls-header {
            display: flex;
            align-items: center;
            gap: 14px;
        }
        .segmented-group {
            display: flex;
            background: var(--bg-item);
            border: 1px solid var(--border-item);
            border-radius: 10px;
            padding: 3px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.15);
        }
        .seg-btn {
            background: transparent;
            border: none;
            color: var(--text-sub);
            padding: 7px 14px;
            border-radius: 7px;
            font-size: 0.85rem;
            font-weight: 700;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 5px;
            transition: all 0.2s ease;
        }
        .seg-btn.active {
            background: var(--btn-active-bg);
            color: var(--btn-active-text);
            box-shadow: 0 2px 6px rgba(2, 132, 199, 0.4);
        }
        .seg-btn:hover:not(.active) { color: var(--text-main); }

        .pulse {
            display: inline-block;
            width: 10px;
            height: 10px;
            background: #10b981;
            border-radius: 50%;
            margin-right: 6px;
            box-shadow: 0 0 10px #10b981;
        }
        
        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 16px;
            margin-bottom: 24px;
        }
        .metric-card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 16px 20px;
            box-shadow: 0 4px 14px rgba(0,0,0,0.08);
            display: flex;
            align-items: center;
            gap: 16px;
        }
        .metric-icon {
            font-size: 2rem;
            width: 48px;
            height: 48px;
            background: var(--bg-item);
            border: 1px solid var(--border-item);
            border-radius: 12px;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .metric-value { font-size: 1.4rem; font-weight: 800; color: var(--text-main); }
        .metric-label { font-size: 0.82rem; color: var(--text-sub); margin-top: 2px; text-transform: uppercase; font-weight: 600; letter-spacing: 0.5px; }

        .toast {
            position: fixed;
            top: 24px;
            right: 24px;
            background: var(--bg-card);
            border: 1px solid var(--border-item);
            padding: 14px 20px;
            border-radius: 10px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.5);
            display: none;
            z-index: 1000;
            font-weight: 500;
            animation: slide-in 0.3s ease-out;
        }
        .toast.warning { border-color: #f59e0b; color: #fbbf24; background: #1c1917; }
        .toast.success { border-color: #10b981; color: #34d399; background: #064e3b; }

        @keyframes slide-in {
            from { transform: translateY(-20px); opacity: 0; }
            to { transform: translateY(0); opacity: 1; }
        }

        .control-panel {
            background: var(--bg-card);
            border: 1px solid var(--border);
            padding: 22px;
            border-radius: 14px;
            margin-bottom: 24px;
            box-shadow: 0 8px 30px rgba(0,0,0,0.15);
        }
        .input-group {
            display: flex;
            gap: 12px;
            margin-top: 14px;
        }
        input[type="text"] {
            flex: 1;
            background: var(--bg-input);
            border: 1px solid var(--border-item);
            padding: 14px 18px;
            border-radius: 10px;
            color: var(--text-main);
            font-size: 1rem;
            outline: none;
        }
        input[type="text"]:focus { border-color: var(--text-accent); }
        select {
            background: var(--bg-item);
            border: 1px solid var(--border-item);
            color: var(--text-main);
            padding: 0 16px;
            border-radius: 10px;
            font-size: 0.95rem;
            outline: none;
            cursor: pointer;
        }
        button.btn-download {
            background: var(--btn-primary);
            color: #fff;
            border: none;
            padding: 14px 28px;
            border-radius: 10px;
            font-size: 1rem;
            font-weight: 600;
            cursor: pointer;
        }
        button.btn-download:hover { opacity: 0.9; }
        .btn-clear {
            background: var(--bg-item);
            color: var(--text-sub);
            border: 1px solid var(--border-item);
            padding: 6px 12px;
            font-size: 0.8rem;
            border-radius: 6px;
            cursor: pointer;
        }
        .btn-clear:hover { color: var(--text-main); }
        
        .options-row {
            display: flex;
            align-items: center;
            gap: 20px;
            margin-top: 14px;
            font-size: 0.9rem;
            color: var(--text-sub);
        }
        .checkbox-label {
            display: flex;
            align-items: center;
            gap: 8px;
            cursor: pointer;
            user-select: none;
        }

        /* Playlists Section */
        .playlist-header-card {
            background: var(--bg-item);
            border: 1px solid var(--border-item);
            border-radius: 12px;
            padding: 16px 20px;
            margin-bottom: 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 12px;
        }
        .playlist-title-box { display: flex; align-items: center; gap: 12px; }
        .playlist-title { font-size: 1.15rem; font-weight: 700; color: var(--text-main); }
        .playlist-sub { font-size: 0.82rem; color: var(--text-sub); margin-top: 2px; }
        .playlist-actions { display: flex; align-items: center; gap: 8px; }

        .grid {
            display: grid;
            grid-template-columns: 1.15fr 0.85fr;
            gap: 24px;
        }
        .card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 14px;
            padding: 20px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.1);
        }
        .card h2 {
            font-size: 1.15rem;
            color: #f59e0b;
            margin-bottom: 16px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .list { max-height: 540px; overflow-y: auto; }
        
        .item {
            background: var(--bg-item);
            border: 1px solid var(--border);
            padding: 12px 16px;
            border-radius: 10px;
            margin-bottom: 12px;
            border-left: 4px solid #10b981;
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 12px;
        }
        .item.video { border-left-color: #f59e0b; }
        
        .item-main-content {
            display: flex;
            align-items: center;
            gap: 14px;
            max-width: 68%;
        }
        .cover-art-thumb {
            width: 50px;
            height: 50px;
            border-radius: 8px;
            object-fit: cover;
            background: #000;
            box-shadow: 0 4px 10px rgba(0,0,0,0.3);
            flex-shrink: 0;
        }
        .item-info { flex: 1; overflow: hidden; }
        .item-title { font-weight: 600; font-size: 0.95rem; color: var(--text-main); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .item-artist { font-size: 0.85rem; color: var(--text-accent); margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .item-meta { font-size: 0.8rem; color: var(--text-sub); margin-top: 6px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
        
        .pill {
            background: var(--pill-bg);
            border: 1px solid var(--border-item);
            padding: 2px 7px;
            border-radius: 4px;
            font-size: 0.75rem;
            color: var(--text-main);
        }
        .pill-bitrate {
            background: #064e3b;
            border-color: #059669;
            color: #6ee7b7;
            font-weight: 600;
        }
        .pill-bpm {
            background: rgba(234, 179, 8, 0.18);
            border-color: rgba(234, 179, 8, 0.4);
            color: #facc15;
            font-weight: 700;
        }
        .pill-count {
            background: #0369a1;
            border-color: #0284c7;
            color: #e0f2fe;
            font-weight: 700;
            font-size: 0.8rem;
            padding: 3px 8px;
        }
        .pill-m3u {
            background: #4c1d95;
            border-color: #7c3aed;
            color: #c4b5fd;
            font-weight: 700;
            font-size: 0.75rem;
        }
        
        .actions {
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .btn-act {
            background: var(--bg-card);
            border: 1px solid var(--border-item);
            color: var(--text-main);
            padding: 7px 12px;
            border-radius: 7px;
            font-size: 0.8rem;
            font-weight: 600;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 5px;
        }
        .btn-act.play { background: #0284c7; color: #fff; border: none; }
        .btn-act.play:hover { background: #0369a1; }
        .btn-act.vlc { background: #ea580c; color: #fff; border: none; }
        .btn-act.vlc:hover { background: #c2410c; }
        .btn-act.playlist-vlc { background: #7c3aed; color: #fff; border: none; padding: 8px 16px; font-size: 0.88rem; }
        .btn-act.playlist-vlc:hover { background: #6d28d9; }
        
        .btn-job {
            background: var(--bg-card);
            border: 1px solid var(--border-item);
            color: var(--text-main);
            padding: 5px 10px;
            border-radius: 6px;
            font-size: 0.75rem;
            font-weight: bold;
            cursor: pointer;
        }
        .btn-job.pause { border-color: #f59e0b; color: #fbbf24; }
        .btn-job.resume { border-color: #10b981; color: #34d399; }
        .btn-job.restart { border-color: #3b82f6; color: #60a5fa; }
        .btn-job.cancel { border-color: #ef4444; color: #f87171; }
        .btn-job.del { background: transparent; border: none; color: var(--text-sub); font-size: 0.9rem; }
        .btn-job.del:hover { color: #ef4444; }

        .terminal {
            background: var(--term-bg);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 16px;
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
            font-size: 0.82rem;
            color: var(--term-text);
            height: 380px;
            overflow-y: auto;
            white-space: pre-wrap;
            line-height: 1.45;
        }

        .sticky-player {
            position: fixed;
            bottom: 0;
            left: 0;
            right: 0;
            background: var(--player-bg);
            border-top: 1px solid var(--border);
            padding: 12px 24px;
            box-shadow: 0 -4px 30px rgba(0,0,0,0.3);
            display: flex;
            align-items: center;
            justify-content: space-between;
            z-index: 999;
            transition: all 0.3s ease-in-out;
            gap: 16px;
        }
        .sticky-player.minimized {
            width: auto;
            left: auto;
            right: 24px;
            bottom: 24px;
            border: 1px solid var(--border-item);
            border-radius: 40px;
            padding: 8px 16px;
            gap: 14px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.5);
        }
        .sticky-player.minimized .player-center-controls,
        .sticky-player.minimized .player-btn-vlc,
        .sticky-player.minimized .player-btn-queue {
            display: none;
        }
        .sticky-player.minimized .player-cover-art {
            width: 34px;
            height: 34px;
            border-radius: 50%;
        }
        .player-track-info {
            display: flex;
            align-items: center;
            gap: 14px;
            max-width: 28%;
            min-width: 200px;
        }
        .player-cover-art {
            width: 52px;
            height: 52px;
            border-radius: 8px;
            object-fit: cover;
            background: #000;
            box-shadow: 0 4px 12px rgba(0,0,0,0.4);
            flex-shrink: 0;
        }
        .player-track-title { font-weight: 600; font-size: 0.92rem; color: var(--text-main); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .player-track-sub { font-size: 0.78rem; color: var(--text-sub); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        
        .player-center-controls {
            display: flex;
            align-items: center;
            gap: 14px;
            flex: 1;
            max-width: 550px;
        }
        .player-transport-buttons {
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .btn-transport {
            background: var(--bg-item);
            border: 1px solid var(--border-item);
            color: var(--text-main);
            width: 36px;
            height: 36px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            font-size: 1.05rem;
            transition: all 0.15s ease;
        }
        .btn-transport:hover {
            background: var(--border-item);
            color: var(--text-accent);
            transform: scale(1.05);
        }
        audio { flex: 1; outline: none; }

        .player-controls-right {
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .player-btn-queue {
            background: var(--bg-item);
            border: 1px solid var(--border-item);
            color: var(--text-main);
            padding: 7px 12px;
            font-size: 0.82rem;
            font-weight: 600;
            border-radius: 7px;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .player-btn-queue:hover {
            background: var(--border-item);
            color: var(--text-accent);
        }
        .player-ctrl-btn {
            background: var(--bg-item);
            border: 1px solid var(--border-item);
            color: var(--text-main);
            width: 32px;
            height: 32px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            font-size: 0.85rem;
            font-weight: bold;
        }
        .player-ctrl-btn:hover {
            background: var(--border-item);
            color: var(--text-accent);
        }
        .player-ctrl-btn.close:hover {
            background: #ef4444;
            color: #fff;
            border-color: #ef4444;
        }

        /* Floating Queue Drawer */
        .queue-drawer {
            position: fixed;
            bottom: 84px;
            right: 24px;
            width: 390px;
            max-height: 480px;
            background: var(--bg-card);
            border: 1px solid var(--border-item);
            border-radius: 14px;
            box-shadow: 0 16px 40px rgba(0,0,0,0.55);
            z-index: 1001;
            display: none;
            flex-direction: column;
            overflow: hidden;
            animation: slide-up 0.25s cubic-bezier(0.16, 1, 0.3, 1);
        }
        @keyframes slide-up {
            from { transform: translateY(20px); opacity: 0; }
            to { transform: translateY(0); opacity: 1; }
        }
        .queue-header {
            padding: 12px 16px;
            background: var(--bg-item);
            border-bottom: 1px solid var(--border);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .queue-head-btn {
            background: var(--bg-card);
            border: 1px solid var(--border-item);
            color: var(--text-sub);
            padding: 4px 8px;
            border-radius: 6px;
            font-size: 0.75rem;
            cursor: pointer;
        }
        .queue-head-btn:hover { color: var(--text-main); border-color: var(--text-accent); }
        .queue-body {
            padding: 10px;
            overflow-y: auto;
            max-height: 400px;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }
        .queue-item {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 8px 10px;
            background: var(--bg-item);
            border: 1px solid var(--border);
            border-radius: 8px;
            transition: all 0.2s ease;
        }
        .queue-item:hover { border-color: var(--text-accent); }
        .queue-item.active {
            border-color: #0284c7;
            background: rgba(2, 132, 199, 0.18);
        }
        .queue-thumb {
            width: 36px;
            height: 36px;
            border-radius: 6px;
            object-fit: cover;
            flex-shrink: 0;
            background: #000;
        }
        .queue-item-info {
            flex: 1;
            overflow: hidden;
            cursor: pointer;
        }
        .queue-item-title {
            font-size: 0.85rem;
            font-weight: 600;
            color: var(--text-main);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .queue-item-sub {
            font-size: 0.75rem;
            color: var(--text-sub);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .queue-item-del {
            background: transparent;
            border: none;
            color: var(--text-sub);
            cursor: pointer;
            padding: 4px;
            font-size: 0.85rem;
        }
        .queue-item-del:hover { color: #ef4444; }

        .status-badge {
            padding: 4px 10px;
            border-radius: 20px;
            font-size: 0.78rem;
            font-weight: 700;
        }
        .status-downloading { background: #0284c7; color: #fff; animation: pulse-anim 1.5s infinite; }
        .status-retry_scheduled { background: #d97706; color: #fff; }
        .status-completed { background: #059669; color: #fff; }
        .status-queued { background: #475569; color: #fff; }
        .status-paused { background: #eab308; color: #000; }
        .status-cancelled { background: #dc2626; color: #fff; }

        @keyframes pulse-anim {
            0% { opacity: 1; }
            50% { opacity: 0.6; }
            100% { opacity: 1; }
        }

        /* Triage System Styles */
        .triage-item {
            background: var(--bg-item);
            border: 1px solid var(--border);
            padding: 10px 14px;
            border-radius: 9px;
            margin-bottom: 8px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            cursor: pointer;
            transition: all 0.18s ease;
        }
        .triage-item:hover {
            border-color: var(--text-accent);
            transform: translateX(2px);
        }
        .triage-item.downloaded { border-left: 3px solid #10b981; }
        .triage-item.retrying { border-left: 3px solid #f59e0b; }
        .triage-item.downloading { border-left: 3px solid #0284c7; }
        .triage-item.queued { border-left: 3px solid #64748b; }
        
        .triage-badge {
            font-size: 0.72rem;
            font-weight: 700;
            padding: 3px 8px;
            border-radius: 12px;
            white-space: nowrap;
        }
        .triage-badge.downloaded { background: #064e3b; color: #6ee7b7; border: 1px solid #059669; }
        .triage-badge.retrying { background: #78350f; color: #fde68a; border: 1px solid #d97706; }
        .triage-badge.downloading { background: #0c4a6e; color: #7dd3fc; border: 1px solid #0284c7; }
        .triage-badge.queued { background: var(--bg-card); color: var(--text-sub); border: 1px solid var(--border-item); }

        /* Modal Styles */
        .modal-backdrop {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0, 0, 0, 0.75);
            backdrop-filter: blur(4px);
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 2000;
            animation: fadeIn 0.2s ease-out;
        }
        .modal-card {
            background: var(--bg-card);
            border: 1px solid var(--border-item);
            border-radius: 16px;
            width: 90%;
            max-width: 560px;
            box-shadow: 0 20px 50px rgba(0,0,0,0.6);
            overflow: hidden;
            animation: scaleIn 0.25s cubic-bezier(0.16, 1, 0.3, 1);
        }
        @keyframes scaleIn {
            from { transform: scale(0.95); opacity: 0; }
            to { transform: scale(1); opacity: 1; }
        }
        @keyframes fadeIn {
            from { opacity: 0; }
            to { opacity: 1; }
        }
        .modal-header {
            padding: 16px 20px;
            background: var(--bg-item);
            border-bottom: 1px solid var(--border);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .modal-close-btn {
            background: transparent;
            border: none;
            color: var(--text-sub);
            font-size: 1.2rem;
            cursor: pointer;
            width: 32px;
            height: 32px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .modal-close-btn:hover { background: var(--border-item); color: var(--text-main); }
        .modal-body { padding: 20px; }
        .modal-actions {
            display: flex;
            align-items: center;
            justify-content: flex-end;
            gap: 10px;
        }

        /* Main View Navigation & Explorer Styles */
        .view-nav-bar {
            display: flex;
            gap: 12px;
            margin-bottom: 22px;
            flex-wrap: wrap;
        }
        .view-tab-btn {
            background: var(--bg-card);
            border: 1px solid var(--border);
            color: var(--text-sub);
            padding: 11px 22px;
            border-radius: 12px;
            font-size: 0.95rem;
            font-weight: 700;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 8px;
            transition: all 0.2s ease;
            box-shadow: 0 2px 6px rgba(0, 0, 0, 0.15);
        }
        .view-tab-btn:hover {
            color: var(--text-main);
            border-color: var(--text-accent);
            transform: translateY(-1px);
        }
        .view-tab-btn.active {
            background: var(--btn-active-bg);
            color: #ffffff;
            border-color: var(--btn-active-bg);
            box-shadow: 0 4px 14px rgba(2, 132, 199, 0.4);
        }

        .search-box {
            display: flex;
            align-items: center;
            background: var(--bg-input);
            border: 1px solid var(--border-item);
            border-radius: 10px;
            padding: 10px 16px;
            gap: 10px;
            margin-bottom: 16px;
        }
        .search-box input {
            background: transparent;
            border: none;
            outline: none;
            color: var(--text-main);
            font-size: 0.95rem;
            width: 100%;
        }

        .chips-row {
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
            margin-bottom: 20px;
        }
        .stat-chip {
            background: var(--bg-card);
            border: 1px solid var(--border-item);
            padding: 8px 16px;
            border-radius: 20px;
            font-size: 0.85rem;
            font-weight: 600;
            color: var(--text-sub);
            display: flex;
            align-items: center;
            gap: 7px;
            box-shadow: 0 2px 5px rgba(0, 0, 0, 0.1);
        }
        .stat-chip strong { color: var(--text-main); }

        .explorer-filters-bar {
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
            align-items: center;
            padding-top: 14px;
            margin-top: 14px;
            border-top: 1px solid var(--border);
        }
        .filter-select {
            background: var(--bg-card);
            border: 1px solid var(--border-item);
            color: var(--text-main);
            padding: 8px 14px;
            border-radius: 8px;
            font-size: 0.84rem;
            font-weight: 600;
            cursor: pointer;
            outline: none;
            transition: all 0.2s ease;
            max-width: 190px;
        }
        .filter-select:focus, .filter-select:hover {
            border-color: var(--text-accent);
        }
        .btn-reset-filters {
            background: transparent;
            border: 1px solid #ef4444;
            color: #ef4444;
            padding: 7px 14px;
            border-radius: 8px;
            font-size: 0.82rem;
            font-weight: 700;
            cursor: pointer;
            transition: all 0.2s ease;
            display: inline-flex;
            align-items: center;
            gap: 5px;
        }
        .btn-reset-filters:hover {
            background: rgba(239, 68, 68, 0.18);
        }

        .table-wrap {
            overflow-x: auto;
            border-radius: 10px;
            border: 1px solid var(--border-item);
            background: var(--bg-card);
        }
        .explorer-table {
            width: 100%;
            border-collapse: separate;
            border-spacing: 0;
            font-size: 0.88rem;
        }
        .explorer-table thead {
            background: var(--bg-item);
        }
        .explorer-table th {
            text-align: left;
            padding: 14px 16px;
            color: var(--text-main);
            font-size: 0.8rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.6px;
            border-bottom: 2px solid var(--border-item);
            background: var(--bg-item);
            white-space: nowrap;
        }
        .sortable-th {
            cursor: pointer;
            user-select: none;
            transition: all 0.15s ease;
        }
        .sortable-th:hover {
            color: var(--text-accent);
            background: rgba(56, 189, 248, 0.14) !important;
        }
        .sort-icon {
            font-size: 0.82rem;
            opacity: 0.6;
            margin-left: 5px;
        }
        .th-sorted {
            background: rgba(56, 189, 248, 0.16) !important;
            color: #38bdf8 !important;
            border-bottom: 2px solid #38bdf8 !important;
        }
        .td-sorted {
            background: rgba(56, 189, 248, 0.055) !important;
            font-weight: 600;
        }
        .sort-badge-pill {
            background: rgba(56, 189, 248, 0.25);
            color: #38bdf8;
            padding: 2px 7px;
            border-radius: 6px;
            font-size: 0.72rem;
            font-weight: 800;
            margin-left: 6px;
            display: inline-flex;
            align-items: center;
            border: 1px solid rgba(56, 189, 248, 0.45);
            box-shadow: 0 0 6px rgba(56, 189, 248, 0.3);
        }
        .sort-active {
            color: #38bdf8;
            font-weight: 900;
            text-shadow: 0 0 8px rgba(56, 189, 248, 0.45);
        }
        .explorer-table td {
            padding: 13px 16px;
            border-bottom: 1px solid var(--border);
            color: var(--text-main);
            vertical-align: middle;
            transition: background-color 0.15s ease;
        }
        .explorer-table tbody tr {
            transition: background-color 0.15s ease, box-shadow 0.15s ease;
        }
        .explorer-table tbody tr:nth-child(even) {
            background: rgba(255, 255, 255, 0.015);
        }
        .explorer-table tbody tr:hover {
            background: rgba(56, 189, 248, 0.11) !important;
            box-shadow: inset 4px 0 0 #38bdf8;
        }
        .explorer-table tbody tr:hover .td-sorted {
            background: rgba(56, 189, 248, 0.18) !important;
        }
        .explorer-table tbody tr:hover td {
            color: #ffffff;
        }

        .grid-cards {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
            gap: 14px;
        }
        .entity-card {
            background: var(--bg-item);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 14px;
            display: flex;
            flex-direction: column;
            gap: 8px;
            transition: all 0.2s ease;
        }
        .entity-card:hover {
            border-color: var(--text-accent);
            transform: translateY(-2px);
            box-shadow: 0 6px 16px rgba(0,0,0,0.25);
        }
        .entity-card-title {
            font-size: 0.95rem;
            font-weight: 700;
            color: var(--text-main);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .entity-card-sub {
            font-size: 0.8rem;
            color: var(--text-sub);
        }

        /* Duplicates & Cleaner Styles */
        .dup-card {
            background: var(--bg-card);
            border: 1px solid var(--border-item);
            border-radius: 14px;
            padding: 18px;
            margin-bottom: 18px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            transition: all 0.2s ease;
        }
        .dup-card:hover {
            border-color: rgba(56, 189, 248, 0.4);
        }
        .dup-card-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 14px;
            padding-bottom: 10px;
            border-bottom: 1px solid var(--border);
            flex-wrap: wrap;
            gap: 8px;
        }
        .dup-group-title {
            font-size: 1.05rem;
            font-weight: 700;
            color: var(--text-main);
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .dup-items-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
            gap: 12px;
        }
        .dup-item {
            background: var(--bg-item);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 14px;
            display: flex;
            flex-direction: column;
            gap: 10px;
            position: relative;
        }
        .dup-item.is-best {
            border: 1.5px solid #10b981;
            background: rgba(16, 185, 129, 0.05);
        }
        .dup-item.is-duplicate {
            border: 1px dashed rgba(239, 68, 68, 0.6);
            background: rgba(239, 68, 68, 0.03);
        }
        .dup-badge-best {
            background: #064e3b;
            color: #6ee7b7;
            font-size: 0.72rem;
            font-weight: 800;
            padding: 3px 8px;
            border-radius: 6px;
            border: 1px solid #059669;
            display: inline-flex;
            align-items: center;
            gap: 4px;
        }
        .dup-badge-redundant {
            background: #7f1d1d;
            color: #fca5a5;
            font-size: 0.72rem;
            font-weight: 800;
            padding: 3px 8px;
            border-radius: 6px;
            border: 1px solid #dc2626;
            display: inline-flex;
            align-items: center;
            gap: 4px;
        }
        .dup-item-main {
            display: flex;
            gap: 12px;
            align-items: center;
        }
        .dup-item-img {
            width: 48px;
            height: 48px;
            border-radius: 8px;
            object-fit: cover;
            background: #000;
            flex-shrink: 0;
        }
        .dup-meta-chips {
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
            font-size: 0.76rem;
            color: var(--text-sub);
        }
        .dup-actions {
            display: flex;
            gap: 8px;
            margin-top: auto;
            padding-top: 10px;
            border-top: 1px solid var(--border);
            justify-content: flex-end;
            align-items: center;
            flex-wrap: wrap;
        }

        /* Online Metadata Results */
        .meta-candidate-list {
            display: flex;
            flex-direction: column;
            gap: 8px;
            max-height: 220px;
            overflow-y: auto;
            margin-top: 10px;
            padding-right: 4px;
        }
        .meta-candidate-card {
            background: var(--bg-card);
            border: 1px solid var(--border-item);
            border-radius: 8px;
            padding: 8px 12px;
            display: flex;
            align-items: center;
            gap: 12px;
            cursor: pointer;
            transition: all 0.15s ease;
        }
        .meta-candidate-card:hover {
            border-color: #38bdf8;
            background: rgba(56, 189, 248, 0.08);
            transform: translateX(2px);
        }
        .meta-candidate-art {
            width: 42px;
            height: 42px;
            border-radius: 6px;
            object-fit: cover;
            flex-shrink: 0;
            background: #000;
        }
        .form-grid-2 {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 12px;
        }
        .form-group {
            display: flex;
            flex-direction: column;
            gap: 4px;
            margin-bottom: 12px;
        }
        .form-group label {
            font-size: 0.78rem;
            font-weight: 700;
            color: var(--text-sub);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .form-control {
            background: var(--bg-input);
            border: 1px solid var(--border-item);
            color: var(--text-main);
            padding: 9px 12px;
            border-radius: 8px;
            font-size: 0.88rem;
            outline: none;
            transition: border-color 0.2s ease;
            width: 100%;
            box-sizing: border-box;
        }
        .form-control:focus {
            border-color: var(--text-accent);
        }
    </style>
</head>
<body>
    <div id="toast" class="toast"></div>

    <div class="container">
        <div class="header">
            <div>
                <h1><span>🎵</span> Media Studio</h1>
                <div id="hdrSub" style="font-size: 0.92rem; color: var(--text-sub); margin-top: 4px;">YouTube & Spotify Automated Control Center</div>
            </div>
            
            <div class="controls-header">
                <div class="segmented-group">
                    <button class="seg-btn" id="btnThemeDark" onclick="setTheme('dark')">🌙 Dark</button>
                    <button class="seg-btn" id="btnThemeLight" onclick="setTheme('light')">☀️ Light</button>
                </div>

                <div class="segmented-group">
                    <button class="seg-btn" id="btnLangEn" onclick="setLanguage('en')">🇺🇸 English</button>
                    <button class="seg-btn" id="btnLangEs" onclick="setLanguage('es')">🇪🇸 Español</button>
                </div>

                <div style="font-size: 0.85rem; color: var(--text-sub); margin-left: 6px;"><span class="pulse"></span> Port 8888</div>
            </div>
        </div>

        <!-- View Navigation Bar -->
        <div class="view-nav-bar">
            <button class="view-tab-btn active" id="tabBtnStudio" onclick="switchMainView('studio')">⚡ <span id="lblNavStudio">Studio & Queue</span></button>
            <button class="view-tab-btn" id="tabBtnExplorer" onclick="switchMainView('explorer')">📁 <span id="lblNavExplorer">Music Folder Explorer</span></button>
            <button class="view-tab-btn" id="tabBtnDuplicates" onclick="switchMainView('duplicates')">👯 <span id="lblNavDuplicates">Duplicates & Cleaner</span> <span id="badgeDupCount" style="background:#ef4444; color:#fff; font-size:0.75rem; padding:2px 7px; border-radius:10px; font-weight:800; display:none; margin-left:4px;">0</span></button>
            <button class="view-tab-btn" id="tabBtnHistory" onclick="switchMainView('history')">📜 <span id="lblNavHistory">Download History & Statistics</span></button>
            <button class="view-tab-btn" id="tabBtnSettings" onclick="switchMainView('settings')">⚙️ <span id="lblNavSettings">Settings & Preferences</span></button>
        </div>

        <!-- VIEW 1: STUDIO & QUEUE -->
        <div id="viewStudio">
            <!-- Metrics Cards Bar -->
            <div class="metrics-grid">
                <div class="metric-card">
                    <div class="metric-icon">🎵</div>
                    <div>
                        <div class="metric-value" id="statTracks">0</div>
                        <div class="metric-label" id="lblStatTracks">Tracks Downloaded</div>
                    </div>
                </div>
                <div class="metric-card">
                    <div class="metric-icon">💾</div>
                    <div>
                        <div class="metric-value" id="statStorage">0 MB</div>
                        <div class="metric-label" id="lblStatStorage">Library Size</div>
                    </div>
                </div>
                <div class="metric-card">
                    <div class="metric-icon">⚡</div>
                    <div>
                        <div class="metric-value" id="statActive">0</div>
                        <div class="metric-label" id="lblStatActive">Active Jobs</div>
                    </div>
                </div>
                <div class="metric-card">
                    <div class="metric-icon">✅</div>
                    <div>
                        <div class="metric-value" id="statCompleted">0</div>
                        <div class="metric-label" id="lblStatCompleted">Completed Tasks</div>
                    </div>
                </div>
            </div>

            <div class="control-panel">
                <h3 style="color: var(--text-main); font-size: 1.1rem;" id="lblNewDl">🚀 Submit New Download</h3>
                <div class="input-group">
                    <input type="text" id="urlInput" oninput="onUrlInputChanged()" onkeydown="if(event.key==='Enter') submitDownload()" placeholder="Paste Spotify (Track, Album, Playlist) or YouTube URL here...">
                    <select id="modeSelect">
                        <option value="audio">🎵 Audio MP3 320k</option>
                        <option value="video">📺 Video MP4 (Max Res)</option>
                    </select>
                    <button class="btn-download" onclick="submitDownload()" id="btnDl">Download</button>
                </div>
                <!-- Live Name Resolution Preview -->
                <div id="urlPreviewPill" style="display: none; margin-top: 10px; background: rgba(56, 189, 248, 0.08); border: 1px solid rgba(56, 189, 248, 0.3); border-radius: 8px; padding: 9px 14px; font-size: 0.88rem; color: var(--text-main); align-items: center; justify-content: space-between; gap: 10px;">
                    <div style="display: flex; align-items: center; gap: 10px; overflow: hidden;">
                        <span id="urlPreviewIcon" style="font-size: 1.15rem;">🎵</span>
                        <div style="overflow: hidden;">
                            <span id="urlPreviewTitle" style="font-weight: 700; color: #38bdf8;">Resolving name...</span>
                            <span id="urlPreviewSub" style="font-size: 0.78rem; color: var(--text-sub); margin-left: 6px;"></span>
                        </div>
                    </div>
                    <span class="pill pill-m3u" id="urlPreviewType" style="flex-shrink: 0;">Spotify</span>
                </div>
                <div class="options-row">
                    <label class="checkbox-label">
                        <input type="checkbox" id="autoRetryCheck" checked>
                        <span id="lblAutoRetry">🔁 <strong>Auto-Schedule & Smart Retry</strong> (Automatically retries in background until 100% of playlist completes)</span>
                    </label>
                </div>
            </div>

            <div class="grid">
                <div>
                    <!-- Queue Box -->
                    <div class="card" style="margin-bottom: 24px;">
                        <h2>
                            <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
                                <span id="lblQueue">⚡ Download Queue & Job Controls</span>
                                <span id="queuePauseBadge" class="pill" style="display: none; background: rgba(234, 179, 8, 0.18); color: #eab308; border: 1px solid rgba(234, 179, 8, 0.35); font-size: 0.72rem; font-weight: 800;">⏸️ PAUSED</span>
                            </div>
                            <div style="display: flex; gap: 8px; flex-wrap: wrap;">
                                <button class="btn-clear" onclick="togglePauseQueue()" id="btnTogglePauseQueue" style="border-color: #eab308; color: #eab308; font-weight: 700; transition: all 0.2s ease;">⏸️ Pause Queue</button>
                                <button class="btn-clear" onclick="restartIncomplete()" id="btnRestartIncomplete" style="border-color: #3b82f6; color: #60a5fa;">🔄 Restart Incomplete</button>
                                <button class="btn-clear" onclick="clearCompleted()" id="btnClear">Clear completed</button>
                            </div>
                        </h2>
                        <div class="list" id="queueList" style="max-height: 220px;">
                            <p style="color: var(--text-sub);" id="lblNoTasks">No pending tasks in queue.</p>
                        </div>
                    </div>

                    <!-- Playlists & Library Tracks Box -->
                    <div class="card">
                        <h2>
                            <div style="display: flex; align-items: center; gap: 8px;">
                                <span id="lblLib">📁 Library & Playlists</span>
                                <span style="color: var(--text-sub); font-size: 0.95rem; font-weight: 500;">(<span id="totalCount">0</span>)</span>
                            </div>
                        </h2>
                        
                        <!-- Search Box for Library -->
                        <div class="search-box" style="margin: 12px 0 14px 0;">
                            <span>🔍</span>
                            <input type="text" id="librarySearchInput" oninput="renderLibrary()" placeholder="Search downloaded MP3s by title, artist, album, bitrate...">
                            <button class="queue-head-btn" id="btnClearLibSearch" onclick="clearLibSearch()" style="display:none;" title="Clear search">✕</button>
                        </div>

                        <!-- Dynamic Playlist Header / Launcher Banner -->
                        <div id="playlistBannerContainer"></div>

                        <div class="list" id="mediaList">
                            <p style="color: var(--text-sub);" id="lblLoadingLib">Loading library...</p>
                        </div>
                    </div>
                </div>

                <div class="card">
                    <h2 style="display: flex; justify-content: space-between; align-items: center;">
                        <span id="lblTriage">🎯 Track Download Status & Triage</span>
                        <button class="btn-clear" onclick="toggleRawTerminal()" id="btnToggleTerminal">📜 View Raw Logs</button>
                    </h2>

                    <!-- Triage Filter Segmented Bar -->
                    <div class="segmented-group" style="margin-bottom: 14px; width: 100%; display: flex;">
                        <button class="seg-btn active" id="fltAll" onclick="setTriageFilter('all')" style="flex: 1; justify-content: center;"><span id="txtFltAll">All</span>&nbsp;(<span id="cntAll">0</span>)</button>
                        <button class="seg-btn" id="fltRetrying" onclick="setTriageFilter('retrying')" style="flex: 1; justify-content: center;"><span id="txtFltRetrying">⚠️ Retrying</span>&nbsp;(<span id="cntRetrying">0</span>)</button>
                        <button class="seg-btn" id="fltDownloaded" onclick="setTriageFilter('downloaded')" style="flex: 1; justify-content: center;"><span id="txtFltDownloaded">✅ Ready</span>&nbsp;(<span id="cntDownloaded">0</span>)</button>
                    </div>

                    <!-- Triage Track List -->
                    <div class="list" id="triageList" style="max-height: 480px; overflow-y: auto;">
                        <p style="color: var(--text-sub);" id="lblTriageEmpty">No active playlist tracks in queue.</p>
                    </div>

                    <!-- Collapsible Raw Terminal Logs -->
                    <div id="rawTerminalContainer" style="display: none; margin-top: 16px;">
                        <div style="font-size: 0.85rem; font-weight: 700; color: var(--text-sub); margin-bottom: 8px;">📜 Raw Process Output</div>
                        <div class="terminal" id="terminalLog">Connecting to daemon server...</div>
                    </div>
                </div>
            </div>
        </div>

        <!-- VIEW 2: MUSIC FOLDER EXPLORER -->
        <div id="viewExplorer" style="display: none;">
            <!-- Folder Chips Stats -->
            <div class="chips-row">
                <div class="stat-chip">📂 <span id="lblExpPath">Folder:</span> <strong>~/Music</strong></div>
                <div class="stat-chip">🎵 <span id="lblExpTracks">Tracks:</span> <strong id="expStatTracks">0</strong></div>
                <div class="stat-chip">🎤 <span id="lblExpArtists">Artists:</span> <strong id="expStatArtists">0</strong></div>
                <div class="stat-chip">💽 <span id="lblExpAlbums">Albums:</span> <strong id="expStatAlbums">0</strong></div>
                <div class="stat-chip">💾 <span id="lblExpSize">Storage:</span> <strong id="expStatSize">0 MB</strong></div>
                <div class="stat-chip">⚡ 100% 320kbps MP3</div>
            </div>

            <!-- Search & Sub-tabs Filter -->
            <div class="card" style="margin-bottom: 20px;">
                <div style="display: flex; gap: 14px; align-items: center; justify-content: space-between; flex-wrap: wrap;">
                    <div class="search-box" style="flex: 1; min-width: 260px; margin-bottom: 0;">
                        <span>🔍</span>
                        <input type="text" id="explorerSearchInput" oninput="renderExplorerView()" placeholder="Search songs, artists, albums, or folders...">
                    </div>
                    <div class="segmented-group">
                        <button class="seg-btn active" id="expTabAll" onclick="setExplorerSubTab('all')">🎵 <span id="lblExpTabAll">All Tracks</span></button>
                        <button class="seg-btn" id="expTabArtists" onclick="setExplorerSubTab('artists')">🎤 <span id="lblExpTabArtists">By Artist</span></button>
                        <button class="seg-btn" id="expTabAlbums" onclick="setExplorerSubTab('albums')">💽 <span id="lblExpTabAlbums">By Album</span></button>
                        <button class="seg-btn" id="expTabPlaylists" onclick="setExplorerSubTab('playlists')">⚡ <span id="lblExpTabPlaylists">Playlists</span></button>
                    </div>
                </div>

                <!-- Column Filters Bar -->
                <div class="explorer-filters-bar" id="explorerFiltersBar">
                    <span style="font-size: 0.8rem; font-weight: 700; color: var(--text-sub);">🎯 Filter:</span>
                    <select class="filter-select" id="fltArtistSelect" onchange="onFilterChange('artist', this.value)">
                        <option value="">🎤 All Artists</option>
                    </select>
                    <select class="filter-select" id="fltAlbumSelect" onchange="onFilterChange('album', this.value)">
                        <option value="">💽 All Albums</option>
                    </select>
                    <select class="filter-select" id="fltBitrateSelect" onchange="onFilterChange('bitrate', this.value)">
                        <option value="">🎚️ All Quality</option>
                    </select>
                    <select class="filter-select" id="fltDurationSelect" onchange="onFilterChange('duration', this.value)">
                        <option value="">⏱️ All Lengths</option>
                        <option value="short">⏱️ Short (&lt; 3 mins)</option>
                        <option value="medium">⏱️ Medium (3 - 5 mins)</option>
                        <option value="long">⏱️ Long (&gt; 5 mins)</option>
                    </select>
                    <select class="filter-select" id="fltFormatSelect" onchange="onFilterChange('format', this.value)">
                        <option value="">📁 All Formats</option>
                        <option value=".mp3">🎵 MP3 Audio</option>
                        <option value=".flac">🎼 Lossless FLAC</option>
                        <option value=".m4a">🎧 M4A Audio</option>
                        <option value="Video">📺 Video MP4</option>
                    </select>
                    <button class="btn-reset-filters" id="btnResetFilters" onclick="resetExplorerFilters()" style="display: none;">✕ Reset Filters</button>
                </div>
            </div>

            <!-- Explorer Content Area -->
            <div class="card">
                <div id="explorerContentArea">
                    <!-- Dynamic rendering by JS -->
                </div>
            </div>
        </div>

        <!-- VIEW 3: DOWNLOAD HISTORY & STATISTICS -->
        <div id="viewHistory" style="display: none;">
            <!-- History Analytics Metrics Grid -->
            <div class="metrics-grid">
                <div class="metric-card">
                    <div class="metric-icon">📥</div>
                    <div>
                        <div class="metric-value" id="histStatJobs">0</div>
                        <div class="metric-label" id="lblHistJobs">All-Time Downloads</div>
                    </div>
                </div>
                <div class="metric-card">
                    <div class="metric-icon">🎵</div>
                    <div>
                        <div class="metric-value" id="histStatTracks">0</div>
                        <div class="metric-label" id="lblHistTracks">Songs Downloaded</div>
                    </div>
                </div>
                <div class="metric-card">
                    <div class="metric-icon">🎯</div>
                    <div>
                        <div class="metric-value" id="histStatSuccess">100%</div>
                        <div class="metric-label" id="lblHistSuccess">Success Rate</div>
                    </div>
                </div>
                <div class="metric-card">
                    <div class="metric-icon">💾</div>
                    <div>
                        <div class="metric-value" id="histStatSize">0 MB</div>
                        <div class="metric-label" id="lblHistSize">Total Acquired</div>
                    </div>
                </div>
            </div>

            <div class="card">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; flex-wrap: wrap; gap: 12px;">
                    <div class="search-box" style="flex: 1; min-width: 240px; margin-bottom: 0;">
                        <span>🔍</span>
                        <input type="text" id="historySearchInput" oninput="renderHistoryView()" placeholder="Search previous download history...">
                    </div>
                    <button class="btn-clear" onclick="clearAllHistory()" id="btnClearHistory">🗑️ Clear History</button>
                </div>

                <div id="historyTableContainer" style="overflow-x: auto;">
                    <table class="explorer-table" id="historyTable">
                        <thead>
                            <tr>
                                <th>Date & Time</th>
                                <th>Source / Playlist Title</th>
                                <th>Status</th>
                                <th>Tracks & Progress</th>
                                <th>Duration</th>
                                <th>Retries</th>
                                <th>Actions</th>
                            </tr>
                        </thead>
                        <tbody id="historyTableBody">
                            <!-- Dynamic rows -->
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- VIEW 4: DUPLICATES & CLEANER -->
        <div id="viewDuplicates" style="display: none;">
            <div class="metrics-grid">
                <div class="metric-card">
                    <div class="metric-icon">👯</div>
                    <div>
                        <div class="metric-value" id="statDupGroups">0</div>
                        <div class="metric-label" id="lblStatDupGroups">Duplicate Song Groups</div>
                    </div>
                </div>
                <div class="metric-card">
                    <div class="metric-icon">💾</div>
                    <div>
                        <div class="metric-value" id="statDupSpace">0 MB</div>
                        <div class="metric-label" id="lblStatDupSpace">Recoverable Storage</div>
                    </div>
                </div>
                <div class="metric-card">
                    <div class="metric-icon">🎵</div>
                    <div>
                        <div class="metric-value" id="statDupSongs">0</div>
                        <div class="metric-label" id="lblStatDupSongs">Redundant Tracks</div>
                    </div>
                </div>
                <div class="metric-card" style="cursor: pointer; background: rgba(239, 68, 68, 0.08); border-color: rgba(239, 68, 68, 0.3);" onclick="cleanAllDuplicates()">
                    <div class="metric-icon">⚡</div>
                    <div>
                        <div class="metric-value" style="color: #ef4444; font-size: 1.25rem;" id="lblCleanAllBtn">Auto-Clean All</div>
                        <div class="metric-label" id="lblCleanAllSub">Keep Best & Free Space</div>
                    </div>
                </div>
            </div>

            <div class="card">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 18px; flex-wrap: wrap; gap: 12px;">
                    <div>
                        <h2 style="margin-bottom: 4px; display: flex; align-items: center; gap: 8px;">
                            <span>👯</span> <span id="lblDupTitle">Duplicate & Similar Tracks Cleaner</span>
                        </h2>
                        <div style="font-size: 0.84rem; color: var(--text-sub);" id="lblDupSub">
                            Listen side-by-side to compare, keep the highest quality version, or auto-clean with 1-click.
                        </div>
                    </div>
                    <div style="display: flex; gap: 8px;">
                        <button class="btn-clear" onclick="loadDuplicatesView()" style="border-color: #38bdf8; color: #38bdf8;">🔄 <span id="btnRescanDups">Rescan Duplicates</span></button>
                        <button class="btn-clear" onclick="cleanAllDuplicates()" style="border-color: #ef4444; color: #ef4444; font-weight: 700;">⚡ <span id="btnCleanAllDups">Auto-Clean All (Keep Best)</span></button>
                    </div>
                </div>

                <div class="search-box">
                    <span>🔍</span>
                    <input type="text" id="dupSearchInput" oninput="renderDuplicatesView()" placeholder="Filter duplicate tracks by title, artist, or album...">
                </div>

                <div id="duplicatesContentArea">
                    <!-- Dynamic duplicate cards -->
                </div>
            </div>
        </div>

        <!-- VIEW 5: SETTINGS & PREFERENCES -->
        <div id="viewSettings" style="display: none;">
            <div class="card" style="margin-bottom: 24px;">
                <div style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; border-bottom: 1px solid var(--border-item); padding-bottom: 16px; margin-bottom: 20px;">
                    <div>
                        <h2 style="margin: 0; font-size: 1.25rem; display: flex; align-items: center; gap: 10px;">
                            <span>⚙️</span> <span id="lblSettingsTitle">Settings & Studio Configuration</span>
                        </h2>
                        <div style="font-size: 0.84rem; color: var(--text-sub); margin-top: 4px;" id="lblSettingsSub">
                            Configure download locations, desktop notifications, audio bitrates, and automation rules.
                        </div>
                    </div>
                    <div style="display: flex; gap: 10px;">
                        <button class="btn-clear" onclick="resetSettingsToDefaults()" style="font-size: 0.84rem;">🔄 Reset Defaults</button>
                        <button class="btn-act play" onclick="saveAppSettings()" style="padding: 9px 20px; font-weight: 700; font-size: 0.88rem; background: linear-gradient(135deg, #0284c7, #0ea5e9);">💾 Save & Apply</button>
                    </div>
                </div>

                <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr)); gap: 20px;">
                    <!-- Card 1: Storage & Paths -->
                    <div style="background: var(--bg-card); border: 1px solid var(--border-item); border-radius: 12px; padding: 18px;">
                        <h3 style="margin-top: 0; font-size: 1rem; color: var(--text-main); display: flex; align-items: center; gap: 8px;">
                            <span>📁</span> <span id="lblSetStorage">Target Music Storage Directory</span>
                        </h3>
                        <p style="font-size: 0.8rem; color: var(--text-sub); margin-bottom: 12px;">
                            All MP3s, albums, artist folders, and synced playlists will be organized here.
                        </p>
                        <div style="display: flex; gap: 8px;">
                            <input type="text" id="settingDownloadDir" class="form-control" style="font-family: monospace; font-size: 0.85rem;" placeholder="/home/rodolfo/Music">
                        </div>
                        <div style="margin-top: 8px; display: flex; gap: 6px; flex-wrap: wrap;">
                            <span class="pill pill-count" onclick="setPresetDir('~/Music')" style="cursor: pointer;" title="Default Linux ~/Music">📁 ~/Music</span>
                            <span class="pill pill-count" onclick="setPresetDir('~/Music/Downloads')" style="cursor: pointer;">📁 ~/Music/Downloads</span>
                            <span class="pill pill-count" onclick="setPresetDir('/media/music')" style="cursor: pointer;" title="Home Assistant Media Directory">🏠 /media/music</span>
                            <span class="pill pill-count" onclick="setPresetDir('/DATA/Media/Music')" style="cursor: pointer;" title="CasaOS Media Folder">📦 /DATA/Media/Music</span>
                        </div>
                    </div>

                    <!-- Card 2: Desktop Notifications -->
                    <div style="background: var(--bg-card); border: 1px solid var(--border-item); border-radius: 12px; padding: 18px;">
                        <h3 style="margin-top: 0; font-size: 1rem; color: var(--text-main); display: flex; align-items: center; gap: 8px;">
                            <span>🔔</span> <span id="lblSetNotif">Desktop & Browser Notifications</span>
                        </h3>
                        <p style="font-size: 0.8rem; color: var(--text-sub); margin-bottom: 14px;">
                            Get instant desktop alerts when downloads finish or playlist retries complete.
                        </p>
                        <label style="display: flex; align-items: center; gap: 10px; cursor: pointer; margin-bottom: 14px;">
                            <input type="checkbox" id="settingNotifEnabled" style="width: 18px; height: 18px; accent-color: #0ea5e9;">
                            <span style="font-weight: 600; font-size: 0.9rem; color: var(--text-main);" id="lblEnableNotifs">Enable Browser Desktop Notifications</span>
                        </label>
                        <div style="display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap;">
                            <span id="notifPermStatus" class="pill" style="font-size: 0.75rem;">Status: Checking...</span>
                            <button class="btn-clear" onclick="testDesktopNotification()" style="font-size: 0.78rem;">🔔 Send Test Notification</button>
                        </div>
                    </div>

                    <!-- Card 3: Audio Bitrate & Quality -->
                    <div style="background: var(--bg-card); border: 1px solid var(--border-item); border-radius: 12px; padding: 18px;">
                        <h3 style="margin-top: 0; font-size: 1rem; color: var(--text-main); display: flex; align-items: center; gap: 8px;">
                            <span>🎵</span> <span id="lblSetQuality">Audio Quality & Bitrate</span>
                        </h3>
                        <p style="font-size: 0.8rem; color: var(--text-sub); margin-bottom: 12px;">
                            Preferred encoding format for all YouTube Music & Spotify conversions.
                        </p>
                        <select id="settingDefaultBitrate" class="form-control" style="font-size: 0.88rem;">
                            <option value="320k">💎 320 kbps (Maximum High Definition MP3)</option>
                            <option value="256k">🎵 256 kbps (High Quality MP3)</option>
                            <option value="flac">💽 FLAC (Lossless Studio Audio)</option>
                            <option value="192k">⚡ 192 kbps (Compact MP3)</option>
                        </select>
                    </div>

                    <!-- Card 4: Automation & Retries -->
                    <div style="background: var(--bg-card); border: 1px solid var(--border-item); border-radius: 12px; padding: 18px;">
                        <h3 style="margin-top: 0; font-size: 1rem; color: var(--text-main); display: flex; align-items: center; gap: 8px;">
                            <span>🔁</span> <span id="lblSetAutomation">Smart Retries & Auto-Sync</span>
                        </h3>
                        <div style="display: flex; flex-direction: column; gap: 12px; margin-top: 10px;">
                            <label style="display: flex; align-items: center; gap: 10px; cursor: pointer;">
                                <input type="checkbox" id="settingAutoRetry" style="width: 18px; height: 18px; accent-color: #0ea5e9;">
                                <span style="font-size: 0.86rem; color: var(--text-main);">Default to Auto-Schedule & Smart Retry for new downloads</span>
                            </label>
                            <label style="display: flex; align-items: center; gap: 10px; cursor: pointer;">
                                <input type="checkbox" id="settingAutoM3u" style="width: 18px; height: 18px; accent-color: #0ea5e9;">
                                <span style="font-size: 0.86rem; color: var(--text-main);">Auto-generate .m3u8 playlists in <code>_PLAYLISTS_</code> folder</span>
                            </label>
                            <label style="display: flex; align-items: center; gap: 10px; cursor: pointer;">
                                <input type="checkbox" id="settingAutoCleanDups" style="width: 18px; height: 18px; accent-color: #0ea5e9;">
                                <span style="font-size: 0.86rem; color: var(--text-main);">Auto-clean duplicate lower-quality tracks on download finish</span>
                            </label>
                        </div>
                    </div>

                    <!-- Card 5: Appearance & Regional Defaults -->
                    <div style="background: var(--bg-card); border: 1px solid var(--border-item); border-radius: 12px; padding: 18px;">
                        <h3 style="margin-top: 0; font-size: 1rem; color: var(--text-main); display: flex; align-items: center; gap: 8px;">
                            <span>🎨</span> <span id="lblSetAppearance">Default Theme & Language</span>
                        </h3>
                        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 12px;">
                            <div>
                                <label style="font-size: 0.78rem; font-weight: 700; color: var(--text-sub); display: block; margin-bottom: 4px;">THEME</label>
                                <select id="settingDefaultTheme" class="form-control" style="font-size: 0.85rem;">
                                    <option value="dark">🌙 Dark Glass</option>
                                    <option value="light">☀️ Light Glass</option>
                                </select>
                            </div>
                            <div>
                                <label style="font-size: 0.78rem; font-weight: 700; color: var(--text-sub); display: block; margin-bottom: 4px;">LANGUAGE</label>
                                <select id="settingDefaultLang" class="form-control" style="font-size: 0.85rem;">
                                    <option value="en">🇺🇸 English</option>
                                    <option value="es">🇪🇸 Español</option>
                                </select>
                            </div>
                        </div>
                    </div>

                    <!-- Card 6: Ecosystem & Deployment info -->
                    <div style="background: var(--bg-card); border: 1px solid var(--border-item); border-radius: 12px; padding: 18px;">
                        <h3 style="margin-top: 0; font-size: 1rem; color: var(--text-main); display: flex; align-items: center; gap: 8px;">
                            <span>🏠</span> <span>Home Assistant & CasaOS Support</span>
                        </h3>
                        <p style="font-size: 0.8rem; color: var(--text-sub); margin-bottom: 12px;">
                            Media Studio runs natively or containerized in Home Assistant Add-ons and CasaOS App stores.
                        </p>
                        <div style="display: flex; gap: 8px; flex-wrap: wrap;">
                            <span class="pill" style="background: rgba(56, 189, 248, 0.15); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.3);">🏠 HA Ingress Compatible</span>
                            <span class="pill" style="background: rgba(16, 185, 129, 0.15); color: #10b981; border: 1px solid rgba(16, 185, 129, 0.3);">📦 CasaOS Docker App</span>
                            <span class="pill" style="background: rgba(234, 179, 8, 0.15); color: #eab308; border: 1px solid rgba(234, 179, 8, 0.3);">⚡ Multi-Arch (amd64 / arm64)</span>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Track Triage Inspector Modal -->
    <div id="triageModal" class="modal-backdrop" style="display: none;">
        <div class="modal-card">
            <div class="modal-header">
                <div>
                    <h3 id="triageModalTitle" style="font-size: 1.1rem; color: var(--text-main);">Track Details</h3>
                    <div id="triageModalArtist" style="font-size: 0.85rem; color: var(--text-accent); margin-top: 2px;">Artist</div>
                </div>
                <button class="modal-close-btn" onclick="closeTriageModal()">✕</button>
            </div>
            <div class="modal-body">
                <div style="margin-bottom: 14px;">
                    <div style="font-size: 0.8rem; text-transform: uppercase; color: var(--text-sub); font-weight: 700; margin-bottom: 4px;">Status</div>
                    <div id="triageModalStatus" style="font-size: 0.95rem; font-weight: 600;">Status text</div>
                </div>

                <div style="margin-bottom: 14px;">
                    <div style="font-size: 0.8rem; text-transform: uppercase; color: var(--text-sub); font-weight: 700; margin-bottom: 4px;">Isolated Logs for this Track</div>
                    <div id="triageModalLogs" class="terminal" style="height: 150px; font-size: 0.78rem;">No specific logs recorded yet.</div>
                </div>

                <div class="modal-actions" id="triageModalActions">
                    <!-- Dynamic action buttons -->
                </div>
            </div>
        </div>
    </div>

    <!-- Edit Metadata & Cover Art Modal -->
    <div id="editMetadataModal" class="modal-backdrop" style="display: none;">
        <div class="modal-card" style="max-width: 620px;">
            <div class="modal-header">
                <div>
                    <h3 style="font-size: 1.1rem; color: var(--text-main);" id="lblEditModalTitle">✏️ Edit Song Metadata & Tags</h3>
                    <div id="editModalFilepath" style="font-size: 0.78rem; color: var(--text-sub); margin-top: 2px; word-break: break-all;">/path/to/song.mp3</div>
                </div>
                <button class="modal-close-btn" onclick="closeEditMetadataModal()">✕</button>
            </div>
            <div class="modal-body" style="max-height: 80vh; overflow-y: auto;">
                <!-- Online Lookup Accordion -->
                <div style="background: rgba(56, 189, 248, 0.06); border: 1px solid rgba(56, 189, 248, 0.3); border-radius: 10px; padding: 12px; margin-bottom: 16px;">
                    <div style="font-size: 0.85rem; font-weight: 700; color: var(--text-accent); margin-bottom: 8px; display: flex; align-items: center; gap: 6px;">
                        <span>🌐</span> <span id="lblAutoFetchTitle">Auto-Fetch Metadata from Internet</span>
                    </div>
                    <div style="display: flex; gap: 8px;">
                        <input type="text" id="onlineMetaQuery" class="form-control" style="font-size: 0.85rem;" placeholder="Search Song Title or Artist on iTunes / Apple Music..." onkeydown="if(event.key==='Enter') searchOnlineMetadata()">
                        <button class="btn-act play" onclick="searchOnlineMetadata()" style="white-space: nowrap; padding: 8px 14px;" id="btnSearchOnline">🔍 Search</button>
                    </div>
                    <div id="onlineMetaLoading" style="display: none; text-align: center; padding: 12px; color: var(--text-sub); font-size: 0.82rem;">
                        <span class="pulse"></span> Searching official databases...
                    </div>
                    <div id="onlineMetaResults" class="meta-candidate-list" style="display: none;">
                        <!-- Dynamic online candidates -->
                    </div>
                </div>

                <!-- Form Fields -->
                <div style="display: flex; gap: 14px; margin-bottom: 14px; align-items: center;">
                    <img id="editCoverPreview" src="" style="width: 80px; height: 80px; border-radius: 8px; object-fit: cover; background: #000; border: 1px solid var(--border-item);" alt="Cover">
                    <div style="flex: 1;">
                        <div class="form-group" style="margin-bottom: 0;">
                            <label id="lblCoverUrl">Cover Artwork URL</label>
                            <input type="text" id="editCoverUrl" class="form-control" placeholder="https://..." oninput="updateCoverPreview(this.value)">
                        </div>
                    </div>
                </div>

                <div class="form-grid-2">
                    <div class="form-group">
                        <label id="lblTrackTitle">Track Title</label>
                        <input type="text" id="editTitle" class="form-control" placeholder="Song Title">
                    </div>
                    <div class="form-group">
                        <label id="lblTrackArtist">Artist</label>
                        <input type="text" id="editArtist" class="form-control" placeholder="Artist Name">
                    </div>
                </div>

                <div class="form-grid-2">
                    <div class="form-group">
                        <label id="lblTrackAlbum">Album</label>
                        <input type="text" id="editAlbum" class="form-control" placeholder="Album Name">
                    </div>
                    <div class="form-group">
                        <label id="lblTrackGenre">Genre</label>
                        <input type="text" id="editGenre" class="form-control" placeholder="Pop, Rock, Latin...">
                    </div>
                </div>

                <div class="form-grid-2">
                    <div class="form-group">
                        <label id="lblTrackNumber">Track #</label>
                        <input type="text" id="editTrackNum" class="form-control" placeholder="01">
                    </div>
                    <div class="form-group">
                        <label id="lblTrackYear">Year</label>
                        <input type="text" id="editYear" class="form-control" placeholder="2026">
                    </div>
                </div>

                <div class="modal-actions" style="margin-top: 16px;">
                    <button class="btn-clear" onclick="closeEditMetadataModal()" id="btnCancelEdit">Cancel</button>
                    <button class="btn-act play" onclick="saveTrackMetadata()" style="padding: 10px 20px; font-size: 0.92rem;" id="btnSaveEdit">💾 Save & Apply Tags</button>
                </div>
            </div>
        </div>
    </div>

    <!-- Beautiful Modern Confirmation Modal -->
    <div class="modal-overlay" id="customConfirmModal" style="display: none; z-index: 10000; backdrop-filter: blur(12px);">
        <div class="modal-card" style="max-width: 440px; text-align: center; padding: 28px 24px; border: 1px solid rgba(239, 68, 68, 0.25); box-shadow: 0 20px 40px -10px rgba(0, 0, 0, 0.6), 0 0 30px rgba(239, 68, 68, 0.15); border-radius: 16px;">
            <div id="confirmIconBox" style="width: 56px; height: 56px; border-radius: 50%; background: rgba(239, 68, 68, 0.12); border: 1px solid rgba(239, 68, 68, 0.3); display: flex; align-items: center; justify-content: center; font-size: 1.7rem; margin: 0 auto 16px auto;">
                🗑️
            </div>
            <h3 id="confirmTitle" style="color: var(--text-main); font-size: 1.25rem; margin-bottom: 8px; font-weight: 800;">Delete Duplicate Track?</h3>
            <p id="confirmMessage" style="color: var(--text-sub); font-size: 0.88rem; line-height: 1.5; margin-bottom: 16px;">Are you sure you want to permanently delete this redundant copy from disk?</p>
            <div id="confirmFilenameChip" style="display: inline-block; background: var(--bg-input); border: 1px solid var(--border-color); border-radius: 8px; padding: 7px 14px; font-family: monospace; font-size: 0.82rem; color: var(--text-main); margin-bottom: 22px; max-width: 100%; word-break: break-all;">
                01 - Loser.mp3
            </div>
            <div style="display: flex; gap: 12px; justify-content: center;">
                <button class="btn-clear" id="confirmCancelBtn" style="flex: 1; padding: 11px; font-size: 0.92rem; border-radius: 8px;">Cancel</button>
                <button id="confirmActionBtn" style="flex: 1; padding: 11px; font-size: 0.92rem; font-weight: 700; border-radius: 8px; background: linear-gradient(135deg, #ef4444, #dc2626); color: #ffffff; border: none; cursor: pointer; box-shadow: 0 4px 14px rgba(239, 68, 68, 0.4); transition: transform 0.15s ease, opacity 0.15s ease;">🗑️ Delete</button>
            </div>
        </div>
    </div>

    <!-- Job & Track Analysis Diagnostic Modal -->
    <div id="jobInspectModal" class="modal-overlay" style="display: none; z-index: 9999; backdrop-filter: blur(12px);">
        <div class="modal-card" style="max-width: 860px; width: 92%; max-height: 88vh; display: flex; flex-direction: column; padding: 0; overflow: hidden; border-radius: 16px; border: 1px solid rgba(56, 189, 248, 0.3); box-shadow: 0 25px 60px rgba(0, 0, 0, 0.8);">
            <!-- Modal Header -->
            <div class="modal-header" style="padding: 18px 24px; border-bottom: 1px solid var(--border-color); display: flex; align-items: center; justify-content: space-between; background: var(--bg-card);">
                <div style="overflow: hidden; padding-right: 12px;">
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <span style="font-size: 1.25rem;">📊</span>
                        <h3 style="font-size: 1.15rem; color: var(--text-main); margin: 0; font-weight: 800; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;" id="inspectJobTitle">Playlist Diagnostic & Track Inspector</h3>
                    </div>
                    <div style="font-size: 0.78rem; color: var(--text-sub); margin-top: 3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;" id="inspectJobUrl">https://open.spotify.com/...</div>
                </div>
                <button class="modal-close-btn" onclick="closeJobInspectModal()" style="font-size: 1.2rem; cursor: pointer;">✕</button>
            </div>

            <!-- Modal Body (Scrollable) -->
            <div class="modal-body" style="padding: 20px 24px; overflow-y: auto; flex: 1;">
                <!-- Loading State -->
                <div id="inspectLoading" style="display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 45px 0;">
                    <div class="pulse" style="width: 28px; height: 28px; background: var(--text-accent); border-radius: 50%; margin-bottom: 14px;"></div>
                    <div style="color: var(--text-sub); font-size: 0.88rem;">Inspecting tracks & comparing library files...</div>
                </div>

                <!-- Analysis Content -->
                <div id="inspectContent" style="display: none;">
                    <!-- Metrics Row (4 Cards) -->
                    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; margin-bottom: 18px;">
                        <div class="metric-card" style="padding: 12px 14px; text-align: left;">
                            <div class="metric-val" id="metricCompletion" style="color: #38bdf8; font-size: 1.3rem;">0%</div>
                            <div class="metric-lbl" id="metricCompletionSub">Downloaded: 0 / 0</div>
                        </div>
                        <div class="metric-card" style="padding: 12px 14px; text-align: left;">
                            <div class="metric-val" id="metricStorage" style="color: #10b981; font-size: 1.3rem;">0 MB</div>
                            <div class="metric-lbl" id="metricStorageSub">Acquired • Missing: 0 MB</div>
                        </div>
                        <div class="metric-card" style="padding: 12px 14px; text-align: left;">
                            <div class="metric-val" id="metricQuality" style="color: #a855f7; font-size: 1.3rem;">320 kbps</div>
                            <div class="metric-lbl" id="metricQualitySub">Quality • Tagged: 100%</div>
                        </div>
                        <div class="metric-card" style="padding: 12px 14px; text-align: left;">
                            <div class="metric-val" id="metricRetries" style="color: #f59e0b; font-size: 1.3rem;">0</div>
                            <div class="metric-lbl" id="metricRetriesSub">Retries • Last: Recent</div>
                        </div>
                    </div>

                    <!-- Batch Action Buttons -->
                    <div style="display: flex; gap: 10px; margin-bottom: 18px; flex-wrap: wrap;" id="inspectActionsBar">
                        <!-- Dynamic Action Buttons -->
                    </div>

                    <!-- Subtabs (Missing vs Downloaded) -->
                    <div style="display: flex; gap: 8px; border-bottom: 1px solid var(--border-color); padding-bottom: 10px; margin-bottom: 14px;">
                        <button class="seg-btn active" id="inspectTabMissing" onclick="switchInspectSubTab('missing')">⚠️ Missing Tracks (<span id="inspectMissingCount">0</span>)</button>
                        <button class="seg-btn" id="inspectTabDownloaded" onclick="switchInspectSubTab('downloaded')">✅ Acquired Tracks (<span id="inspectDownloadedCount">0</span>)</button>
                    </div>

                    <!-- Track Table -->
                    <div style="overflow-x: auto; border: 1px solid var(--border-color); border-radius: 10px; background: var(--bg-card);">
                        <table style="width: 100%; border-collapse: collapse; font-size: 0.86rem;">
                            <thead>
                                <tr style="border-bottom: 1px solid var(--border-color); color: var(--text-sub); text-align: left; background: var(--bg-input);">
                                    <th style="padding: 10px 14px; width: 40px;">#</th>
                                    <th style="padding: 10px 14px;">Title & Artist</th>
                                    <th style="padding: 10px 14px;">Status / Info</th>
                                    <th style="padding: 10px 14px;">Duration</th>
                                    <th style="padding: 10px 14px; text-align: right;">Action</th>
                                </tr>
                            </thead>
                            <tbody id="inspectTableBody">
                                <!-- Dynamic rows -->
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Floating Playback Queue Drawer -->
    <div class="queue-drawer" id="queueDrawer">
        <div class="queue-header">
            <div style="font-weight: 700; font-size: 0.95rem; color: var(--text-main); display: flex; align-items: center; gap: 8px;">
                <span>🎶</span> <span id="lblQueueHeader">Playback Queue</span> (<span id="queueHeaderCount">0</span>)
            </div>
            <div style="display: flex; gap: 6px;">
                <button class="queue-head-btn" onclick="shuffleQueue()" title="Shuffle Queue" id="btnShuffle">🔀 Shuffle</button>
                <button class="queue-head-btn" onclick="clearPlayerQueue()" title="Clear Queue" id="btnClearQ">🗑️ Clear</button>
                <button class="queue-head-btn" onclick="toggleQueueDrawer()" style="font-weight: bold;">✕</button>
            </div>
        </div>
        <div class="queue-body" id="queueListContainer">
            <!-- Dynamic Queue Items -->
        </div>
    </div>

    <!-- Sticky Audio Player with Album Cover Art & Transport Controls -->
    <div class="sticky-player" id="stickyPlayer" style="display: none;">
        <div class="player-track-info">
            <img class="player-cover-art" id="playerCover" src="" alt="Cover">
            <div>
                <div class="player-track-title" id="playerTitle">Track Name</div>
                <div class="player-track-sub" id="playerSub">Ready to play</div>
            </div>
        </div>

        <div class="player-center-controls">
            <div class="player-transport-buttons">
                <button class="btn-transport" onclick="playPrevTrack()" title="Previous Track (⏮)">⏮</button>
                <button class="btn-transport" onclick="playNextTrack()" title="Next Track (⏭)">⏭</button>
            </div>
            <audio id="audioPlayer" controls autoplay></audio>
        </div>
        
        <div class="player-controls-right">
            <button class="player-btn-queue" onclick="toggleQueueDrawer()" title="View Queue" id="btnQueueToggle">📑 <span id="lblQueueBtn">Queue</span> (<span id="queueBadge">0</span>)</button>
            <button class="btn-act vlc player-btn-vlc" onclick="playInVlcCurrent()" title="Open in VLC">🎬 VLC</button>
            <button class="player-ctrl-btn" onclick="togglePlayerMinimize()" id="minBtn" title="Minimize / Expand">▼</button>
            <button class="player-ctrl-btn close" onclick="closePlayer()" title="Close Player">✕</button>
        </div>
    </div>

    <script>
        function toB64(str) {
            try {
                return btoa(unescape(encodeURIComponent(str || '')));
            } catch (e) {
                return '';
            }
        }
        function fromB64(str) {
            try {
                return decodeURIComponent(escape(atob(str || '')));
            } catch (e) {
                return str || '';
            }
        }

        const validViews = ['studio', 'explorer', 'duplicates', 'history', 'settings'];
        let initialHash = (window.location.hash || '').replace('#', '');
        let currentMainView = validViews.includes(initialHash) ? initialHash : (localStorage.getItem('media_active_view') || 'studio');
        if (!validViews.includes(currentMainView)) currentMainView = 'studio';

        let currentLang = localStorage.getItem('media_lang') || 'en';
        let currentTheme = localStorage.getItem('media_theme') || 'dark';
        let currentExplorerSubTab = localStorage.getItem('media_explorer_subtab') || 'all';
        let explorerSortKey = 'date';
        let explorerSortAsc = false;
        let filterArtist = '';
        let filterAlbum = '';
        let filterBitrate = '';
        let filterDuration = '';
        let filterFormat = '';
        let currentPlayingPath = '';
        let currentQueueIndex = -1;
        let isPlayerMinimized = false;
        let libraryData = [];
        let playlistsData = [];
        let historyData = [];
        let historyAnalytics = {};
        let explorerData = {};
        let playerQueue = [];
        let triageData = null;
        let currentTriageFilter = 'all';
        let currentInspectedTrack = null;
        let isRawTerminalVisible = false;
        let lastJobStatus = '';
        let appSettings = {};

        const i18n = {
            en: {
                hdrSub: "YouTube & Spotify Automated Control Center",
                lblNavStudio: "Studio & Queue",
                lblNavExplorer: "Music Folder Explorer",
                lblNavHistory: "Download History & Statistics",
                lblNavSettings: "Settings & Preferences",
                lblStatTracks: "Tracks Downloaded",
                lblStatStorage: "Library Size",
                lblStatActive: "Active Jobs",
                lblStatCompleted: "Completed Tasks",
                lblNewDl: "🚀 Submit New Download",
                placeholder: "Paste Spotify (Track, Album, Playlist) or YouTube URL here...",
                btnDl: "Download",
                lblAutoRetry: "🔁 <strong>Auto-Schedule & Smart Retry</strong> (Automatically retries in background until 100% of playlist completes)",
                lblQueue: "⚡ Download Queue & Job Controls",
                btnClear: "Clear completed",
                btnRestartIncomplete: "🔄 Restart Incomplete",
                lblNoTasks: "No pending tasks in queue.",
                lblLib: "📁 Library & Playlists",
                lblLoadingLib: "Loading library...",
                lblTriage: "🎯 Track Download Status & Triage",
                btnToggleTerminal: "📜 View Raw Logs",
                btnHideTerminal: "✕ Hide Raw Logs",
                lblTriageEmpty: "No active playlist tracks in queue.",
                lblExpPath: "Folder:",
                lblExpTracks: "Tracks:",
                lblExpArtists: "Artists:",
                lblExpAlbums: "Albums:",
                lblExpSize: "Storage:",
                lblExpTabAll: "All Tracks",
                lblExpTabArtists: "By Artist",
                lblExpTabAlbums: "By Album",
                lblExpTabPlaylists: "Playlists",
                lblHistJobs: "All-Time Downloads",
                lblHistTracks: "Songs Downloaded",
                lblHistSuccess: "Success Rate",
                lblHistSize: "Total Acquired",
                btnClearHistory: "🗑️ Clear History",
                libSearchPlaceholder: "Search downloaded MP3s by title, artist, album, bitrate...",
                optAudio: "🎵 Audio MP3 320k",
                optVideo: "📺 Video MP4 (Max Res)",
                nextRetry: "⏱️ Next retry: ",
                added: "Added: ",
                status: "Status: ",
                openVlc: "🎬 Open in VLC",
                btnPause: "⏸ Pause",
                btnResume: "▶ Resume",
                btnRestart: "🔄 Restart",
                btnCancel: "🛑 Cancel",
                playAllVlc: "🎬 Play Playlist in VLC",
                playAllBrowser: "▶ Play All in Browser",
                m3uSynced: "⚡ .m3u8 Auto-Synced",
                downloadedBadge: "📥 MP3s in library: ",
                lblQueueHeader: "Playback Queue",
                lblQueueBtn: "Queue",
                btnShuffle: "🔀 Shuffle",
                btnClearQ: "🗑️ Clear",
                emptyQueue: "Queue is empty. Add songs from your library using ➕ Queue.",
                toastAddedQueue: "➕ Added track to playback queue.",
                toastSuccess: "✅ Download added to queue.",
                toastEmpty: "Please enter a valid URL.",
                toastVlc: "🎬 Opening in VLC Media Player...",
                toastVlcPl: "🎬 Launching full playlist in VLC Media Player...",
                toastFolder: "📂 Opening local folder...",
                toastPaused: "⏸ Job paused.",
                toastResumed: "▶ Job resumed.",
                toastCancelled: "🛑 Job cancelled.",
                toastRestarted: "🔄 Job restarted.",
                toastRestartAll: "🔄 Restarted all incomplete downloads.",
                toastSingleRetry: "⚡ Targeted single track download scheduled.",
                toastHistReDownloaded: "🔄 Re-download queued from history.",
                toastHistCleared: "🗑️ History cleared.",
                toastHistDeleted: "🗑️ History record deleted.",
                lblNavDuplicates: "Duplicates & Cleaner",
                lblStatDupGroups: "Duplicate Song Groups",
                lblStatDupSpace: "Recoverable Storage",
                lblStatDupSongs: "Redundant Tracks",
                lblCleanAllBtn: "Auto-Clean All",
                lblCleanAllSub: "Keep Best & Free Space",
                lblDupTitle: "Duplicate & Similar Tracks Cleaner",
                lblDupSub: "Listen side-by-side to compare, keep the highest quality version, or auto-clean with 1-click.",
                btnRescanDups: "Rescan Duplicates",
                btnCleanAllDups: "Auto-Clean All (Keep Best)",
                lblEditModalTitle: "✏️ Edit Song Metadata & Tags",
                lblAutoFetchTitle: "Auto-Fetch Metadata from Internet",
                btnSearchOnline: "🔍 Search",
                lblCoverUrl: "Cover Artwork URL",
                lblTrackTitle: "Track Title",
                lblTrackArtist: "Artist",
                lblTrackAlbum: "Album",
                lblTrackGenre: "Genre",
                lblTrackNumber: "Track #",
                lblTrackYear: "Year",
                btnCancelEdit: "Cancel",
                btnSaveEdit: "💾 Save & Apply Tags",
                toastDupCleaned: "⚡ Cleaned duplicate tracks successfully.",
                toastMetaSaved: "✅ Metadata & ID3 tags updated."
            },
            es: {
                hdrSub: "Centro de Control Automatizado YouTube & Spotify",
                lblNavStudio: "Estudio & Cola",
                lblNavExplorer: "Explorador de Música",
                lblNavHistory: "Historial & Estadísticas",
                lblNavSettings: "Ajustes & Configuración",
                lblStatTracks: "Canciones en Biblioteca",
                lblStatStorage: "Espacio en Disco",
                lblStatActive: "Descargas Activas",
                lblStatCompleted: "Tareas Completadas",
                lblNewDl: "🚀 Enviar Nueva Descarga",
                placeholder: "Pega aquí el link de Spotify (Canción, Álbum, Playlist) o YouTube...",
                btnDl: "Descargar",
                lblAutoRetry: "🔁 <strong>Auto-Schedule & Reintento Automático</strong> (Reintenta en segundo plano hasta bajar el 100% de la playlist)",
                lblQueue: "⚡ Cola de Descargas & Controles",
                btnClear: "Limpiar completados",
                btnRestartIncomplete: "🔄 Reintentar Incompletas",
                lblNoTasks: "No hay tareas pendientes en la cola.",
                lblLib: "📁 Biblioteca & Playlists",
                lblLoadingLib: "Cargando biblioteca...",
                lblTriage: "🎯 Estado de Descargas & Triaje",
                btnToggleTerminal: "📜 Ver Logs Crudos",
                btnHideTerminal: "✕ Ocultar Logs",
                lblTriageEmpty: "No hay canciones de playlist en proceso.",
                lblExpPath: "Carpeta:",
                lblExpTracks: "Canciones:",
                lblExpArtists: "Artistas:",
                lblExpAlbums: "Álbumes:",
                lblExpSize: "Almacenamiento:",
                lblExpTabAll: "Todas las Canciones",
                lblExpTabArtists: "Por Artista",
                lblExpTabAlbums: "Por Álbum",
                lblExpTabPlaylists: "Playlists",
                lblHistJobs: "Descargas Totales",
                lblHistTracks: "Canciones Descargadas",
                lblHistSuccess: "Tasa de Éxito",
                lblHistSize: "Total Adquirido",
                btnClearHistory: "🗑️ Limpiar Historial",
                libSearchPlaceholder: "Buscar MP3s descargados por título, artista, álbum, bitrate...",
                optAudio: "🎵 Audio MP3 320k",
                optVideo: "📺 Video MP4 (Max Res)",
                nextRetry: "⏱️ Próximo reintento: ",
                added: "Agregado: ",
                status: "Estado: ",
                openVlc: "🎬 Abrir en VLC",
                btnPause: "⏸ Pausar",
                btnResume: "▶ Reanudar",
                btnRestart: "🔄 Reiniciar",
                btnCancel: "🛑 Cancelar",
                playAllVlc: "🎬 Reproducir Playlist en VLC",
                playAllBrowser: "▶ Reproducir Todo en Navegador",
                m3uSynced: "⚡ .m3u8 Auto-Sincronizado",
                downloadedBadge: "📥 MP3s en biblioteca: ",
                lblQueueHeader: "Cola de Reproducción",
                lblQueueBtn: "Cola",
                btnShuffle: "🔀 Aleatorio",
                btnClearQ: "🗑️ Limpiar",
                emptyQueue: "La cola está vacía. Añade canciones desde tu biblioteca usando ➕ Cola.",
                toastAddedQueue: "➕ Canción añadida a la cola de reproducción.",
                toastSuccess: "✅ Descarga agregada a la cola.",
                toastEmpty: "Por favor pega una URL válida.",
                toastVlc: "🎬 Abriendo en reproductor VLC...",
                toastVlcPl: "🎬 Abriendo playlist completa en VLC Media Player...",
                toastFolder: "📂 Abriendo carpeta local...",
                toastPaused: "⏸ Descarga pausada.",
                toastResumed: "▶ Descarga reanudada.",
                toastCancelled: "🛑 Descarga cancelada.",
                toastRestarted: "🔄 Descarga reiniciada.",
                toastRestartAll: "🔄 Descargas incompletas reiniciadas.",
                toastSingleRetry: "⚡ Descarga individual dirigida encolada.",
                toastHistReDownloaded: "🔄 Re-descarga encolada desde el historial.",
                toastHistCleared: "🗑️ Historial limpiado.",
                toastHistDeleted: "🗑️ Registro de historial eliminado.",
                lblNavDuplicates: "Duplicados & Limpieza",
                lblStatDupGroups: "Grupos de Canciones Repetidas",
                lblStatDupSpace: "Espacio Recuperable",
                lblStatDupSongs: "Pistas Redundantes",
                lblCleanAllBtn: "Auto-Limpiar Todo",
                lblCleanAllSub: "Conservar Mejor & Liberar Espacio",
                lblDupTitle: "Limpiador de Canciones Duplicadas & Similares",
                lblDupSub: "Escucha lado a lado para comparar, conserva la versión de mayor calidad o auto-limpia con 1 clic.",
                btnRescanDups: "Re-escanear Duplicados",
                btnCleanAllDups: "Auto-Limpiar Todo (Mejor Calidad)",
                lblEditModalTitle: "✏️ Editar Metadatos & Etiquetas de Canción",
                lblAutoFetchTitle: "Auto-Obtener Metadatos de Internet",
                btnSearchOnline: "🔍 Buscar",
                lblCoverUrl: "URL de Portada de Álbum",
                lblTrackTitle: "Título de Canción",
                lblTrackArtist: "Artista",
                lblTrackAlbum: "Álbum",
                lblTrackGenre: "Género",
                lblTrackNumber: "N° Pista",
                lblTrackYear: "Año",
                btnCancelEdit: "Cancelar",
                btnSaveEdit: "💾 Guardar & Aplicar Etiquetas",
                toastDupCleaned: "⚡ Canciones duplicadas eliminadas con éxito.",
                toastMetaSaved: "✅ Metadatos y etiquetas ID3 actualizadas."
            }
        };

        function setTheme(theme) {
            document.documentElement.setAttribute('data-theme', theme);
            localStorage.setItem('media_theme', theme);
            currentTheme = theme;
            
            document.getElementById('btnThemeDark').classList.toggle('active', theme === 'dark');
            document.getElementById('btnThemeLight').classList.toggle('active', theme === 'light');
        }

        function setLanguage(lang) {
            currentLang = lang;
            localStorage.setItem('media_lang', lang);
            const t = i18n[lang];

            document.getElementById('btnLangEn').classList.toggle('active', lang === 'en');
            document.getElementById('btnLangEs').classList.toggle('active', lang === 'es');

            document.getElementById('hdrSub').innerText = t.hdrSub;
            document.getElementById('lblNavStudio').innerText = t.lblNavStudio;
            document.getElementById('lblNavExplorer').innerText = t.lblNavExplorer;
            if (document.getElementById('lblNavDuplicates')) document.getElementById('lblNavDuplicates').innerText = t.lblNavDuplicates;
            document.getElementById('lblNavHistory').innerText = t.lblNavHistory;
            
            document.getElementById('lblStatTracks').innerText = t.lblStatTracks;
            document.getElementById('lblStatStorage').innerText = t.lblStatStorage;
            document.getElementById('lblStatActive').innerText = t.lblStatActive;
            document.getElementById('lblStatCompleted').innerText = t.lblStatCompleted;
            document.getElementById('lblNewDl').innerText = t.lblNewDl;
            document.getElementById('urlInput').placeholder = t.placeholder;
            document.getElementById('btnDl').innerText = t.btnDl;
            document.getElementById('lblAutoRetry').innerHTML = t.lblAutoRetry;
            document.getElementById('lblQueue').innerText = t.lblQueue;
            document.getElementById('btnClear').innerText = t.btnClear;
            const bRestartInc = document.getElementById('btnRestartIncomplete');
            if (bRestartInc) bRestartInc.innerText = t.btnRestartIncomplete;
            document.getElementById('lblLib').innerText = t.lblLib;
            document.getElementById('lblTriage').innerText = t.lblTriage;
            document.getElementById('btnToggleTerminal').innerText = isRawTerminalVisible ? t.btnHideTerminal : t.btnToggleTerminal;

            const libSearch = document.getElementById('librarySearchInput');
            if (libSearch) libSearch.placeholder = t.libSearchPlaceholder;

            // Explorer & History Translations
            if (document.getElementById('lblExpPath')) document.getElementById('lblExpPath').innerText = t.lblExpPath;
            if (document.getElementById('lblExpTracks')) document.getElementById('lblExpTracks').innerText = t.lblExpTracks;
            if (document.getElementById('lblExpArtists')) document.getElementById('lblExpArtists').innerText = t.lblExpArtists;
            if (document.getElementById('lblExpAlbums')) document.getElementById('lblExpAlbums').innerText = t.lblExpAlbums;
            if (document.getElementById('lblExpSize')) document.getElementById('lblExpSize').innerText = t.lblExpSize;
            if (document.getElementById('lblExpTabAll')) document.getElementById('lblExpTabAll').innerText = t.lblExpTabAll;
            if (document.getElementById('lblExpTabArtists')) document.getElementById('lblExpTabArtists').innerText = t.lblExpTabArtists;
            if (document.getElementById('lblExpTabAlbums')) document.getElementById('lblExpTabAlbums').innerText = t.lblExpTabAlbums;
            if (document.getElementById('lblExpTabPlaylists')) document.getElementById('lblExpTabPlaylists').innerText = t.lblExpTabPlaylists;

            // Duplicates Translations
            if (document.getElementById('lblStatDupGroups')) document.getElementById('lblStatDupGroups').innerText = t.lblStatDupGroups;
            if (document.getElementById('lblStatDupSpace')) document.getElementById('lblStatDupSpace').innerText = t.lblStatDupSpace;
            if (document.getElementById('lblStatDupSongs')) document.getElementById('lblStatDupSongs').innerText = t.lblStatDupSongs;
            if (document.getElementById('lblCleanAllBtn')) document.getElementById('lblCleanAllBtn').innerText = t.lblCleanAllBtn;
            if (document.getElementById('lblCleanAllSub')) document.getElementById('lblCleanAllSub').innerText = t.lblCleanAllSub;
            if (document.getElementById('lblDupTitle')) document.getElementById('lblDupTitle').innerText = t.lblDupTitle;
            if (document.getElementById('lblDupSub')) document.getElementById('lblDupSub').innerText = t.lblDupSub;
            if (document.getElementById('btnRescanDups')) document.getElementById('btnRescanDups').innerText = t.btnRescanDups;
            if (document.getElementById('btnCleanAllDups')) document.getElementById('btnCleanAllDups').innerText = t.btnCleanAllDups;

            // Edit Metadata Translations
            if (document.getElementById('lblEditModalTitle')) document.getElementById('lblEditModalTitle').innerText = t.lblEditModalTitle;
            if (document.getElementById('lblAutoFetchTitle')) document.getElementById('lblAutoFetchTitle').innerText = t.lblAutoFetchTitle;
            if (document.getElementById('btnSearchOnline')) document.getElementById('btnSearchOnline').innerText = t.btnSearchOnline;
            if (document.getElementById('lblCoverUrl')) document.getElementById('lblCoverUrl').innerText = t.lblCoverUrl;
            if (document.getElementById('lblTrackTitle')) document.getElementById('lblTrackTitle').innerText = t.lblTrackTitle;
            if (document.getElementById('lblTrackArtist')) document.getElementById('lblTrackArtist').innerText = t.lblTrackArtist;
            if (document.getElementById('lblTrackAlbum')) document.getElementById('lblTrackAlbum').innerText = t.lblTrackAlbum;
            if (document.getElementById('lblTrackGenre')) document.getElementById('lblTrackGenre').innerText = t.lblTrackGenre;
            if (document.getElementById('lblTrackNumber')) document.getElementById('lblTrackNumber').innerText = t.lblTrackNumber;
            if (document.getElementById('lblTrackYear')) document.getElementById('lblTrackYear').innerText = t.lblTrackYear;
            if (document.getElementById('btnCancelEdit')) document.getElementById('btnCancelEdit').innerText = t.btnCancelEdit;
            if (document.getElementById('btnSaveEdit')) document.getElementById('btnSaveEdit').innerText = t.btnSaveEdit;

            if (document.getElementById('lblHistJobs')) document.getElementById('lblHistJobs').innerText = t.lblHistJobs;
            if (document.getElementById('lblHistTracks')) document.getElementById('lblHistTracks').innerText = t.lblHistTracks;
            if (document.getElementById('lblHistSuccess')) document.getElementById('lblHistSuccess').innerText = t.lblHistSuccess;
            if (document.getElementById('lblHistSize')) document.getElementById('lblHistSize').innerText = t.lblHistSize;
            if (document.getElementById('btnClearHistory')) document.getElementById('btnClearHistory').innerText = t.btnClearHistory;

            const qHeader = document.getElementById('lblQueueHeader');
            if (qHeader) qHeader.innerText = t.lblQueueHeader;
            const qBtnLabel = document.getElementById('lblQueueBtn');
            if (qBtnLabel) qBtnLabel.innerText = t.lblQueueBtn;
            const bShuffle = document.getElementById('btnShuffle');
            if (bShuffle) bShuffle.innerText = t.btnShuffle;
            const bClearQ = document.getElementById('btnClearQ');
            if (bClearQ) bClearQ.innerText = t.btnClearQ;

            const modeSel = document.getElementById('modeSelect');
            modeSel.options[0].text = t.optAudio;
            modeSel.options[1].text = t.optVideo;

            renderLibrary();
            renderQueueDrawer();
            renderTriage();
            renderExplorerView();
            renderHistoryView();
        }

        function showToast(msg, type = 'info') {
            const toast = document.getElementById('toast');
            toast.className = `toast ${type}`;
            toast.innerText = msg;
            toast.style.display = 'block';
            setTimeout(() => { toast.style.display = 'none'; }, 4000);
        }

        function toggleRawTerminal() {
            const container = document.getElementById('rawTerminalContainer');
            const btn = document.getElementById('btnToggleTerminal');
            const t = i18n[currentLang];
            isRawTerminalVisible = !isRawTerminalVisible;
            if (isRawTerminalVisible) {
                container.style.display = 'block';
                btn.innerText = t.btnHideTerminal;
            } else {
                container.style.display = 'none';
                btn.innerText = t.btnToggleTerminal;
            }
        }

        function setTriageFilter(filter) {
            currentTriageFilter = filter;
            document.getElementById('fltAll').classList.toggle('active', filter === 'all');
            document.getElementById('fltRetrying').classList.toggle('active', filter === 'retrying');
            document.getElementById('fltDownloaded').classList.toggle('active', filter === 'downloaded');
            renderTriage();
        }

        function renderTriage() {
            const container = document.getElementById('triageList');
            const t = i18n[currentLang];
            
            if (!triageData || !triageData.tracks || triageData.tracks.length === 0) {
                container.innerHTML = `<p style="color: var(--text-sub); padding: 10px;">${t.lblTriageEmpty}</p>`;
                document.getElementById('cntAll').innerText = '0';
                document.getElementById('cntRetrying').innerText = '0';
                document.getElementById('cntDownloaded').innerText = '0';
                return;
            }

            document.getElementById('cntAll').innerText = triageData.total_tracks || triageData.tracks.length;
            document.getElementById('cntRetrying').innerText = triageData.retrying_count || 0;
            document.getElementById('cntDownloaded').innerText = triageData.downloaded_count || 0;

            const filtered = triageData.tracks.filter(track => {
                if (currentTriageFilter === 'all') return true;
                if (currentTriageFilter === 'retrying') return track.status === 'retrying' || track.status === 'downloading';
                if (currentTriageFilter === 'downloaded') return track.status === 'downloaded';
                return true;
            });

            if (filtered.length === 0) {
                container.innerHTML = `<p style="color: var(--text-sub); padding: 10px;">No tracks matching filter "${currentTriageFilter}".</p>`;
                return;
            }

            container.innerHTML = filtered.map((track, idx) => {
                const realIdx = triageData.tracks.indexOf(track);
                let badgeLabel = track.status.toUpperCase();
                if (track.status === 'downloaded') badgeLabel = '✅ READY';
                else if (track.status === 'retrying') badgeLabel = `🔁 RETRY #${track.retry_count || 1}`;
                else if (track.status === 'downloading') badgeLabel = '⚡ DOWNLOADING';
                else if (track.status === 'queued') badgeLabel = '⏳ QUEUED';

                return `
                    <div class="triage-item ${track.status}" onclick="openTriageModal(${realIdx})">
                        <div style="flex: 1; overflow: hidden;">
                            <div style="font-weight: 600; font-size: 0.9rem; color: var(--text-main); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
                                ${track.title}
                            </div>
                            <div style="font-size: 0.78rem; color: var(--text-sub); margin-top: 2px;">
                                ${track.artist ? '👤 ' + track.artist + ' • ' : ''}<span>${track.status_note}</span>
                            </div>
                        </div>
                        <div style="display: flex; align-items: center; gap: 8px;">
                            <span class="triage-badge ${track.status}">${badgeLabel}</span>
                            <span style="font-size: 0.85rem; color: var(--text-sub);">ℹ️</span>
                        </div>
                    </div>
                `;
            }).join('');
        }

        function openTriageModal(index) {
            if (!triageData || !triageData.tracks || !triageData.tracks[index]) return;
            const track = triageData.tracks[index];
            currentInspectedTrack = track;

            document.getElementById('triageModalTitle').innerText = track.title;
            document.getElementById('triageModalArtist').innerText = track.artist ? `👤 ${track.artist}` : 'Unknown Artist';
            
            const statusEl = document.getElementById('triageModalStatus');
            statusEl.innerHTML = `<span class="triage-badge ${track.status}" style="font-size: 0.85rem; padding: 4px 10px;">${track.status.toUpperCase()}</span> <span style="margin-left: 8px; color: var(--text-main);">${track.status_note}</span>`;

            const logsEl = document.getElementById('triageModalLogs');
            if (track.logs && track.logs.length > 0) {
                logsEl.innerText = track.logs.join('\\n');
            } else {
                logsEl.innerText = track.status === 'downloaded' ? 
                    '✅ Track successfully downloaded and tagged with 320kbps MP3 audio.' : 
                    '⏳ No error logs recorded yet. Track is waiting for automated worker queue.';
            }

            const actionsEl = document.getElementById('triageModalActions');
            actionsEl.innerHTML = '';

            if (track.status !== 'downloaded') {
                const retryBtn = document.createElement('button');
                retryBtn.className = 'btn-act play';
                retryBtn.textContent = '⚡ Force Retry Track';
                retryBtn.onclick = () => forceRetrySingleTrack(track.query || `${track.artist} - ${track.title}`);
                actionsEl.appendChild(retryBtn);
            } else if (track.matched_file) {
                const playBtn = document.createElement('button');
                playBtn.className = 'btn-act play';
                playBtn.textContent = '▶ Play';
                playBtn.onclick = () => {
                    playInBrowser(track.matched_file, track.title, track.artist);
                    closeTriageModal();
                };
                actionsEl.appendChild(playBtn);

                const vlcBtn = document.createElement('button');
                vlcBtn.className = 'btn-act vlc';
                vlcBtn.textContent = '🎬 VLC';
                vlcBtn.onclick = () => openLocal(track.matched_file, 'vlc');
                actionsEl.appendChild(vlcBtn);
            }

            const closeBtn = document.createElement('button');
            closeBtn.className = 'btn-act';
            closeBtn.textContent = 'Close';
            closeBtn.onclick = closeTriageModal;
            actionsEl.appendChild(closeBtn);

            document.getElementById('triageModal').style.display = 'flex';
        }

        function closeTriageModal() {
            document.getElementById('triageModal').style.display = 'none';
            currentInspectedTrack = null;
        }

        async function forceRetrySingleTrack(query) {
            const t = i18n[currentLang];
            try {
                await fetch('/api/retry_single_track', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ query })
                });
                showToast(t.toastSingleRetry, 'success');
                closeTriageModal();
                fetchStatus();
            } catch (e) {
                showToast('Error scheduling single track download', 'warning');
            }
        }

        let duplicatesData = null;
        let onlineCandidatesList = [];
        let currentEditingTrackPath = null;

        function switchMainView(view) {
            if (!validViews.includes(view)) view = 'studio';
            currentMainView = view;
            localStorage.setItem('media_active_view', view);
            try { history.replaceState(null, '', '#' + view); } catch (e) {}

            document.getElementById('tabBtnStudio').classList.toggle('active', view === 'studio');
            document.getElementById('tabBtnExplorer').classList.toggle('active', view === 'explorer');
            if (document.getElementById('tabBtnDuplicates')) document.getElementById('tabBtnDuplicates').classList.toggle('active', view === 'duplicates');
            document.getElementById('tabBtnHistory').classList.toggle('active', view === 'history');
            if (document.getElementById('tabBtnSettings')) document.getElementById('tabBtnSettings').classList.toggle('active', view === 'settings');

            document.getElementById('viewStudio').style.display = view === 'studio' ? 'block' : 'none';
            document.getElementById('viewExplorer').style.display = view === 'explorer' ? 'block' : 'none';
            if (document.getElementById('viewDuplicates')) document.getElementById('viewDuplicates').style.display = view === 'duplicates' ? 'block' : 'none';
            document.getElementById('viewHistory').style.display = view === 'history' ? 'block' : 'none';
            if (document.getElementById('viewSettings')) document.getElementById('viewSettings').style.display = view === 'settings' ? 'block' : 'none';

            if (view === 'explorer') renderExplorerView();
            if (view === 'duplicates') loadDuplicatesView();
            if (view === 'history') renderHistoryView();
            if (view === 'settings') loadSettingsView();
        }

        async function loadSettingsView() {
            try {
                const res = await fetch('/api/settings');
                const data = await res.json();
                if (data.settings) {
                    appSettings = data.settings;
                    renderSettingsForm();
                }
            } catch (e) {
                console.error("Error loading settings:", e);
            }
        }

        function renderSettingsForm() {
            const dirInput = document.getElementById('settingDownloadDir');
            const notifCheck = document.getElementById('settingNotifEnabled');
            const bitrateSel = document.getElementById('settingDefaultBitrate');
            const autoRetryCheck = document.getElementById('settingAutoRetry');
            const autoM3uCheck = document.getElementById('settingAutoM3u');
            const autoCleanCheck = document.getElementById('settingAutoCleanDups');
            const langSel = document.getElementById('settingDefaultLang');
            const themeSel = document.getElementById('settingDefaultTheme');

            if (dirInput) dirInput.value = appSettings.download_dir || '~/Music';
            if (notifCheck) notifCheck.checked = appSettings.notifications_enabled !== false;
            if (bitrateSel) bitrateSel.value = appSettings.default_bitrate || '320k';
            if (autoRetryCheck) autoRetryCheck.checked = appSettings.auto_retry_enabled !== false;
            if (autoM3uCheck) autoM3uCheck.checked = appSettings.auto_m3u_sync !== false;
            if (autoCleanCheck) autoCleanCheck.checked = !!appSettings.auto_clean_duplicates;
            if (langSel) langSel.value = appSettings.default_language || 'en';
            if (themeSel) themeSel.value = appSettings.default_theme || 'dark';

            updateNotifStatusPill();
        }

        function setPresetDir(path) {
            const dirInput = document.getElementById('settingDownloadDir');
            if (dirInput) dirInput.value = path;
        }

        function updateNotifStatusPill() {
            const pill = document.getElementById('notifPermStatus');
            if (!pill) return;
            if (!("Notification" in window)) {
                pill.innerText = "❌ Not Supported in this browser";
                pill.style.color = "#ef4444";
            } else if (Notification.permission === "granted") {
                pill.innerText = "✅ Browser Permission Granted";
                pill.style.color = "#10b981";
            } else if (Notification.permission === "denied") {
                pill.innerText = "⛔ Permission Blocked in Browser";
                pill.style.color = "#ef4444";
            } else {
                pill.innerText = "⚠️ Permission Not Requested Yet";
                pill.style.color = "#f59e0b";
            }
        }

        async function testDesktopNotification() {
            if ("Notification" in window && Notification.permission !== "granted") {
                const perm = await Notification.requestPermission();
                updateNotifStatusPill();
                if (perm !== "granted") {
                    showToast("Please allow browser notifications in your browser address bar.", "warning");
                    return;
                }
            }
            if ("Notification" in window && Notification.permission === "granted") {
                const notif = new Notification("🎵 Media Studio Test", {
                    body: "Desktop notifications are enabled and functioning properly!",
                    icon: "/api/cover"
                });
                notif.onclick = () => { window.focus(); notif.close(); };
                showToast("🔔 Notification triggered!", "success");
            }
            await fetch('/api/test_notification', { method: 'POST' });
        }

        async function saveAppSettings() {
            const newSettings = {
                download_dir: document.getElementById('settingDownloadDir')?.value || '~/Music',
                notifications_enabled: document.getElementById('settingNotifEnabled')?.checked ?? true,
                default_bitrate: document.getElementById('settingDefaultBitrate')?.value || '320k',
                auto_retry_enabled: document.getElementById('settingAutoRetry')?.checked ?? true,
                auto_m3u_sync: document.getElementById('settingAutoM3u')?.checked ?? true,
                auto_clean_duplicates: document.getElementById('settingAutoCleanDups')?.checked ?? false,
                default_language: document.getElementById('settingDefaultLang')?.value || currentLang,
                default_theme: document.getElementById('settingDefaultTheme')?.value || currentTheme
            };

            try {
                const res = await fetch('/api/settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ settings: newSettings })
                });
                const data = await res.json();
                if (data.success) {
                    appSettings = data.settings;
                    showToast("⚙️ Settings saved & applied successfully!", "success");
                    if (newSettings.default_language !== currentLang) {
                        setLanguage(newSettings.default_language);
                    }
                    if (newSettings.default_theme !== currentTheme) {
                        setTheme(newSettings.default_theme);
                    }
                    fetchStatus();
                }
            } catch (e) {
                showToast("Error saving settings", "warning");
            }
        }

        async function resetSettingsToDefaults() {
            const ok = await showConfirmDialog({
                title: "Reset to Default Settings?",
                message: "This will reset all download paths, notification options, and bitrate defaults back to standard defaults.",
                confirmText: "Reset Defaults",
                cancelText: "Cancel",
                icon: "⚙️",
                isDanger: false
            });
            if (!ok) return;

            const res = await fetch('/api/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    settings: {
                        download_dir: '~/Music',
                        default_bitrate: '320k',
                        notifications_enabled: true,
                        auto_retry_enabled: true,
                        auto_m3u_sync: true,
                        default_language: 'en',
                        default_theme: 'dark',
                        max_retries: 5,
                        auto_clean_duplicates: false
                    }
                })
            });
            const data = await res.json();
            appSettings = data.settings || {};
            renderSettingsForm();
            showToast("⚙️ Settings reset to default values.", "info");
        }

        async function loadDuplicatesView() {
            try {
                const res = await fetch('/api/duplicates');
                duplicatesData = await res.json();
                
                const totalGroups = duplicatesData.total_groups || 0;
                const totalWasted = duplicatesData.total_wasted_mb || 0;
                let redundantTracksCount = 0;
                (duplicatesData.groups || []).forEach(g => {
                    redundantTracksCount += (g.duplicate_count || 0);
                });

                if (document.getElementById('statDupGroups')) document.getElementById('statDupGroups').innerText = totalGroups;
                if (document.getElementById('statDupSpace')) document.getElementById('statDupSpace').innerText = `${totalWasted} MB`;
                if (document.getElementById('statDupSongs')) document.getElementById('statDupSongs').innerText = redundantTracksCount;

                const badge = document.getElementById('badgeDupCount');
                if (badge) {
                    badge.innerText = totalGroups;
                    badge.style.display = totalGroups > 0 ? 'inline-block' : 'none';
                }

                renderDuplicatesView();
            } catch (e) {
                console.error("Error loading duplicates:", e);
            }
        }

        function renderDuplicatesView() {
            const container = document.getElementById('duplicatesContentArea');
            if (!container) return;
            if (!duplicatesData || !duplicatesData.groups || duplicatesData.groups.length === 0) {
                container.innerHTML = `
                    <div style="text-align: center; padding: 48px 20px; color: var(--text-sub);">
                        <div style="font-size: 3rem; margin-bottom: 12px;">🎉</div>
                        <h3 style="color: var(--text-main); margin-bottom: 6px;">No Duplicates Found!</h3>
                        <p style="font-size: 0.9rem;">Your music library is clean and perfectly organized.</p>
                    </div>
                `;
                return;
            }

            const searchInput = document.getElementById('dupSearchInput');
            const q = searchInput ? searchInput.value.toLowerCase().trim() : '';

            let filteredGroups = duplicatesData.groups;
            if (q) {
                filteredGroups = filteredGroups.filter(g => {
                    const str = `${g.song_name || ''} ${g.artist || ''} ${g.items.map(it => it.album + ' ' + it.folder).join(' ')}`.toLowerCase();
                    return str.includes(q);
                });
            }

            if (filteredGroups.length === 0) {
                container.innerHTML = `<p style="color: var(--text-sub); text-align: center; padding: 24px;">No duplicate groups match "${q}".</p>`;
                return;
            }

            container.innerHTML = filteredGroups.map(grp => `
                <div class="dup-card">
                    <div class="dup-card-header">
                        <div class="dup-group-title">
                            <span>🎵</span>
                            <span>${grp.song_name}</span>
                            <span style="font-size: 0.85rem; color: var(--text-sub); font-weight: normal;">• 👤 ${grp.artist}</span>
                        </div>
                        <div style="display: flex; align-items: center; gap: 8px;">
                            <span style="font-size: 0.8rem; color: #ef4444; font-weight: 700;">💾 ${grp.wasted_mb} MB redundant</span>
                        </div>
                    </div>
                    <div class="dup-items-grid">
                        ${grp.items.map(it => `
                            <div class="dup-item ${it.is_best ? 'is-best' : 'is-duplicate'}">
                                <div style="display: flex; justify-content: space-between; align-items: center;">
                                    ${it.is_best ? '<span class="dup-badge-best">⭐ BEST QUALITY (KEEP)</span>' : '<span class="dup-badge-redundant">⚠️ REDUNDANT COPY</span>'}
                                    <span class="pill pill-bitrate">${it.bitrate || '320 kbps'}</span>
                                </div>
                                <div class="dup-item-main">
                                    <img src="/api/cover?path=${encodeURIComponent(it.path)}" class="dup-item-img" alt="Cover">
                                    <div style="overflow: hidden; flex: 1;">
                                        <div style="font-weight: 700; color: var(--text-main); font-size: 0.92rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${it.title}</div>
                                        <div style="font-size: 0.8rem; color: var(--text-sub); margin-top: 2px;">📁 ${it.folder}</div>
                                    </div>
                                </div>
                                <div class="dup-meta-chips">
                                    <span>⏱️ ${it.duration}</span>
                                    <span>💾 ${it.size_mb} MB</span>
                                    <span>📅 ${new Date(it.mtime * 1000).toLocaleDateString()}</span>
                                </div>
                                <div class="dup-actions">
                                    <button class="btn-act play" onclick="playInBrowser(fromB64('${toB64(it.path)}'), fromB64('${toB64(it.title)}'), fromB64('${toB64(it.artist)}'))" title="Play & Listen">▶ Listen</button>
                                    <button class="btn-act" onclick="openEditMetadataModal(fromB64('${toB64(it.path)}'))" title="Edit Metadata">✏️ Edit</button>
                                    <button class="btn-act vlc" onclick="openLocal(fromB64('${toB64(it.path)}'), 'vlc')" title="Play in VLC">🎬</button>
                                    ${!it.is_best ? `<button class="btn-act del" onclick="deleteDuplicateTrack(fromB64('${toB64(it.path)}'))" title="Delete this duplicate">🗑️ Delete</button>` : ''}
                                </div>
                            </div>
                        `).join('')}
                    </div>
                </div>
            `).join('');
        }

        let confirmResolveHook = null;

        function showConfirmDialog({
            title = "Are you sure?",
            message = "",
            filename = "",
            confirmText = "Delete",
            cancelText = "Cancel",
            icon = "🗑️",
            isDanger = true
        } = {}) {
            return new Promise((resolve) => {
                confirmResolveHook = resolve;
                const modal = document.getElementById('customConfirmModal');
                const titleEl = document.getElementById('confirmTitle');
                const msgEl = document.getElementById('confirmMessage');
                const chipEl = document.getElementById('confirmFilenameChip');
                const iconEl = document.getElementById('confirmIconBox');
                const actionBtn = document.getElementById('confirmActionBtn');
                const cancelBtn = document.getElementById('confirmCancelBtn');

                if (titleEl) titleEl.innerText = title;
                if (msgEl) msgEl.innerText = message;
                if (iconEl) iconEl.innerText = icon;
                if (chipEl) {
                    if (filename) {
                        chipEl.innerText = filename;
                        chipEl.style.display = 'inline-block';
                    } else {
                        chipEl.style.display = 'none';
                    }
                }
                if (actionBtn) {
                    actionBtn.innerText = confirmText;
                    if (isDanger) {
                        actionBtn.style.background = 'linear-gradient(135deg, #ef4444, #dc2626)';
                        actionBtn.style.boxShadow = '0 4px 14px rgba(239, 68, 68, 0.4)';
                    } else {
                        actionBtn.style.background = 'linear-gradient(135deg, #38bdf8, #0284c7)';
                        actionBtn.style.boxShadow = '0 4px 14px rgba(56, 189, 248, 0.4)';
                    }
                    actionBtn.onclick = () => {
                        modal.style.display = 'none';
                        if (confirmResolveHook) confirmResolveHook(true);
                        confirmResolveHook = null;
                    };
                }
                if (cancelBtn) {
                    cancelBtn.innerText = cancelText;
                    cancelBtn.onclick = () => {
                        modal.style.display = 'none';
                        if (confirmResolveHook) confirmResolveHook(false);
                        confirmResolveHook = null;
                    };
                }
                modal.style.display = 'flex';
            });
        }

        async function cleanAllDuplicates() {
            const ok = await showConfirmDialog({
                title: "⚡ Auto-Clean All Duplicates?",
                message: "This will automatically consolidate your library by keeping the single highest quality album version of each song and deleting all redundant copies to free up storage.",
                confirmText: "⚡ Auto-Clean All",
                icon: "⚡",
                isDanger: true
            });
            if (!ok) return;

            try {
                const res = await fetch('/api/clean_duplicates_auto', { method: 'POST' });
                const data = await res.json();
                if (data.success) {
                    showToast(`⚡ Cleaned ${data.deleted_count} duplicates and freed ${data.freed_mb} MB!`, 'success');
                    await loadDuplicatesView();
                    fetchStatus();
                } else {
                    showToast(data.error || "Failed to clean duplicates", "warning");
                }
            } catch (e) {
                showToast("Error cleaning duplicates: " + e.message, "warning");
            }
        }

        async function deleteDuplicateTrack(filepath) {
            if (!filepath) return;
            const fname = filepath.split('/').pop();
            const ok = await showConfirmDialog({
                title: "Delete Redundant Duplicate?",
                message: "Permanently delete this redundant copy from disk? The best quality version will remain safe in your library.",
                filename: fname,
                confirmText: "🗑️ Delete Copy",
                icon: "🗑️",
                isDanger: true
            });
            if (!ok) return;

            try {
                if (duplicatesData && duplicatesData.groups) {
                    duplicatesData.groups.forEach(g => {
                        g.items = g.items.filter(it => it.path !== filepath);
                    });
                    duplicatesData.groups = duplicatesData.groups.filter(g => g.items.length > 1);
                    renderDuplicatesView();
                }
                const res = await fetch('/api/delete_track', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ filepath })
                });
                const data = await res.json();
                if (data.success) {
                    showToast("🗑️ Duplicate track deleted.", "info");
                    await loadDuplicatesView();
                    fetchStatus();
                } else {
                    showToast(data.error || "Failed to delete duplicate track", "warning");
                    await loadDuplicatesView();
                }
            } catch (e) {
                showToast("Error deleting track: " + e.message, "warning");
            }
        }

        async function deleteSingleTrack(filepath) {
            if (!filepath) return;
            const fname = filepath.split('/').pop();
            const ok = await showConfirmDialog({
                title: "Delete Track from Library?",
                message: "Are you sure you want to permanently delete this track from your music folder?",
                filename: fname,
                confirmText: "🗑️ Delete Track",
                icon: "🗑️",
                isDanger: true
            });
            if (!ok) return;

            try {
                if (libraryData) libraryData = libraryData.filter(m => m.full_path !== filepath);
                if (explorerData && explorerData.tracks) explorerData.tracks = explorerData.tracks.filter(m => m.full_path !== filepath);
                renderLibrary();
                renderExplorerView();
                
                const res = await fetch('/api/delete_track', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ filepath })
                });
                const data = await res.json();
                if (data.success) {
                    showToast("🗑️ Track deleted from library.", "info");
                    fetchStatus();
                } else {
                    showToast(data.error || "Failed to delete track", "warning");
                    fetchStatus();
                }
            } catch (e) {
                showToast("Error deleting track: " + e.message, "warning");
            }
        }

        // ================= EDIT METADATA & ONLINE FETCH =================
        function openEditMetadataModal(filepath) {
            currentEditingTrackPath = filepath;
            let tr = (libraryData || []).find(it => it.full_path === filepath);
            if (!tr) {
                tr = {
                    display_title: filepath.split('/').pop().replace(/\\.[^/.]+$/, ""),
                    artist: "",
                    album: "Single",
                    genre: "",
                    track_num: "01",
                    full_path: filepath
                };
            }

            document.getElementById('editModalFilepath').innerText = filepath;
            document.getElementById('editTitle').value = tr.display_title || tr.name || '';
            document.getElementById('editArtist').value = tr.artist || '';
            document.getElementById('editAlbum').value = tr.album || '';
            document.getElementById('editGenre').value = tr.genre || '';
            document.getElementById('editTrackNum').value = (tr.track_num || '01').split('/')[0];
            document.getElementById('editYear').value = '';
            document.getElementById('editCoverUrl').value = '';
            document.getElementById('editCoverPreview').src = `/api/cover?path=${encodeURIComponent(filepath)}`;

            const queryInput = document.getElementById('onlineMetaQuery');
            queryInput.value = `${tr.artist || ''} ${tr.display_title || tr.name || ''}`.trim();
            document.getElementById('onlineMetaResults').style.display = 'none';
            document.getElementById('onlineMetaResults').innerHTML = '';

            document.getElementById('editMetadataModal').style.display = 'flex';
        }

        function closeEditMetadataModal() {
            document.getElementById('editMetadataModal').style.display = 'none';
            currentEditingTrackPath = null;
        }

        function updateCoverPreview(url) {
            const preview = document.getElementById('editCoverPreview');
            if (url && url.startsWith('http')) {
                preview.src = url;
            } else if (currentEditingTrackPath) {
                preview.src = `/api/cover?path=${encodeURIComponent(currentEditingTrackPath)}`;
            }
        }

        async function searchOnlineMetadata() {
            const q = document.getElementById('onlineMetaQuery').value.trim();
            if (!q) return;
            const loading = document.getElementById('onlineMetaLoading');
            const resultsContainer = document.getElementById('onlineMetaResults');
            loading.style.display = 'block';
            resultsContainer.style.display = 'none';

            try {
                const res = await fetch('/api/search_metadata', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ query: q, limit: 6 })
                });
                const data = await res.json();
                onlineCandidatesList = data.results || [];
                loading.style.display = 'none';

                if (onlineCandidatesList.length === 0) {
                    resultsContainer.innerHTML = `<div style="padding: 8px; color: var(--text-sub); font-size: 0.82rem;">No matching metadata found. Try editing the search terms above.</div>`;
                    resultsContainer.style.display = 'block';
                    return;
                }

                resultsContainer.innerHTML = onlineCandidatesList.map((c, idx) => `
                    <div class="meta-candidate-card" onclick="selectOnlineCandidate(${idx})">
                        <img src="${c.cover_url || ''}" class="meta-candidate-art" alt="Art">
                        <div style="flex: 1; overflow: hidden;">
                            <div style="font-weight: 700; color: var(--text-main); font-size: 0.88rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${c.title}</div>
                            <div style="font-size: 0.78rem; color: var(--text-accent);">${c.artist} • ${c.album} (${c.year || 'N/A'})</div>
                            <div style="font-size: 0.74rem; color: var(--text-sub);">${c.genre || 'Music'} • Track #${c.track_number}</div>
                        </div>
                        <div style="font-size: 0.78rem; color: #38bdf8; font-weight: 700; white-space: nowrap;">⚡ Apply</div>
                    </div>
                `).join('');
                resultsContainer.style.display = 'flex';
            } catch (e) {
                loading.style.display = 'none';
                showToast("Error querying online database", "warning");
            }
        }

        function selectOnlineCandidate(idx) {
            const c = onlineCandidatesList[idx];
            if (!c) return;
            if (c.title) document.getElementById('editTitle').value = c.title;
            if (c.artist) document.getElementById('editArtist').value = c.artist;
            if (c.album) document.getElementById('editAlbum').value = c.album;
            if (c.genre) document.getElementById('editGenre').value = c.genre;
            if (c.track_number) document.getElementById('editTrackNum').value = c.track_number;
            if (c.year) document.getElementById('editYear').value = c.year;
            if (c.cover_url) {
                document.getElementById('editCoverUrl').value = c.cover_url;
                document.getElementById('editCoverPreview').src = c.cover_url;
            }
            showToast("✨ Applied online metadata to fields!", "success");
        }

        async function saveTrackMetadata() {
            const t = i18n[currentLang];
            if (!currentEditingTrackPath) return;

            const payload = {
                filepath: currentEditingTrackPath,
                title: document.getElementById('editTitle').value.trim(),
                artist: document.getElementById('editArtist').value.trim(),
                album: document.getElementById('editAlbum').value.trim(),
                genre: document.getElementById('editGenre').value.trim(),
                track_number: document.getElementById('editTrackNum').value.trim(),
                year: document.getElementById('editYear').value.trim(),
                cover_url: document.getElementById('editCoverUrl').value.trim()
            };

            try {
                const res = await fetch('/api/update_metadata', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (data.success) {
                    showToast(t.toastMetaSaved, "success");
                    closeEditMetadataModal();
                    fetchStatus();
                } else {
                    showToast(data.error || "Failed to update tags", "warning");
                }
            } catch (e) {
                showToast("Error updating metadata", "warning");
            }
        }

        function setExplorerSubTab(subTab) {
            currentExplorerSubTab = subTab;
            localStorage.setItem('media_explorer_subtab', subTab);
            document.getElementById('expTabAll').classList.toggle('active', subTab === 'all');
            document.getElementById('expTabArtists').classList.toggle('active', subTab === 'artists');
            document.getElementById('expTabAlbums').classList.toggle('active', subTab === 'albums');
            document.getElementById('expTabPlaylists').classList.toggle('active', subTab === 'playlists');
            renderExplorerView();
        }

        function parseDurationSecs(dur) {
            if (!dur || dur === 'N/A') return 0;
            const parts = dur.split(':').map(Number);
            if (parts.length === 2) return (parts[0] || 0) * 60 + (parts[1] || 0);
            if (parts.length === 3) return (parts[0] || 0) * 3600 + (parts[1] || 0) * 60 + (parts[2] || 0);
            return 0;
        }

        function parseBitrateNum(br) {
            if (!br || br === 'N/A') return 0;
            const m = br.match(/\\d+/);
            return m ? parseInt(m[0], 10) : 0;
        }

        function onFilterChange(type, val) {
            if (type === 'artist') filterArtist = val;
            else if (type === 'album') filterAlbum = val;
            else if (type === 'bitrate') filterBitrate = val;
            else if (type === 'duration') filterDuration = val;
            else if (type === 'format') filterFormat = val;
            renderExplorerView();
        }

        function populateFilterDropdowns() {
            const artistSel = document.getElementById('fltArtistSelect');
            const albumSel = document.getElementById('fltAlbumSelect');
            const bitrateSel = document.getElementById('fltBitrateSelect');

            if (!artistSel || !albumSel || !bitrateSel) return;

            // Unique artists
            const artists = Array.from(new Set((libraryData || []).map(i => i.artist || 'Unknown Artist'))).filter(Boolean).sort();
            const currArtist = filterArtist || artistSel.value;
            artistSel.innerHTML = '<option value="">🎤 All Artists</option>' + artists.map(a => `<option value="${a.replace(/"/g, '&quot;')}">${a}</option>`).join('');
            artistSel.value = currArtist;

            // Unique albums
            const albums = Array.from(new Set((libraryData || []).map(i => i.album || i.folder || 'Single'))).filter(Boolean).sort();
            const currAlbum = filterAlbum || albumSel.value;
            albumSel.innerHTML = '<option value="">💽 All Albums</option>' + albums.map(a => `<option value="${a.replace(/"/g, '&quot;')}">${a}</option>`).join('');
            albumSel.value = currAlbum;

            // Unique bitrates
            const bitrates = Array.from(new Set((libraryData || []).map(i => i.bitrate).filter(Boolean))).sort((a,b) => parseBitrateNum(b) - parseBitrateNum(a));
            const currBr = filterBitrate || bitrateSel.value;
            bitrateSel.innerHTML = '<option value="">🎚️ All Quality</option>' + bitrates.map(b => `<option value="${b}">${b}</option>`).join('');
            bitrateSel.value = currBr;
        }

        function resetExplorerFilters() {
            filterArtist = '';
            filterAlbum = '';
            filterBitrate = '';
            filterDuration = '';
            filterFormat = '';

            const aSel = document.getElementById('fltArtistSelect');
            const albSel = document.getElementById('fltAlbumSelect');
            const bSel = document.getElementById('fltBitrateSelect');
            const dSel = document.getElementById('fltDurationSelect');
            const fSel = document.getElementById('fltFormatSelect');
            const sInput = document.getElementById('explorerSearchInput');

            if (aSel) aSel.value = '';
            if (albSel) albSel.value = '';
            if (bSel) bSel.value = '';
            if (dSel) dSel.value = '';
            if (fSel) fSel.value = '';
            if (sInput) sInput.value = '';

            renderExplorerView();
        }

        function filterExplorerByTerm(term) {
            const input = document.getElementById('explorerSearchInput');
            if (input) input.value = term;
            setExplorerSubTab('all');
        }

        function sortExplorer(key) {
            if (explorerSortKey === key) {
                explorerSortAsc = !explorerSortAsc;
            } else {
                explorerSortKey = key;
                explorerSortAsc = (key === 'title' || key === 'artist' || key === 'album');
            }
            renderExplorerView();
        }

        function renderExplorerView() {
            if (!explorerData) return;
            const t = i18n[currentLang];
            const searchInput = document.getElementById('explorerSearchInput');
            const q = searchInput ? searchInput.value.toLowerCase().trim() : '';

            populateFilterDropdowns();

            const isFiltered = Boolean(q || filterArtist || filterAlbum || filterBitrate || filterDuration || filterFormat);
            const resetBtn = document.getElementById('btnResetFilters');
            if (resetBtn) resetBtn.style.display = isFiltered ? 'inline-flex' : 'none';

            // Update stats chips
            const totalTracks = explorerData.total_tracks || libraryData.length || 0;
            const totalArtists = explorerData.total_artists || 0;
            const totalAlbums = explorerData.total_albums || 0;
            const totalMb = explorerData.total_size_mb || 0;

            if (document.getElementById('expStatTracks')) document.getElementById('expStatTracks').innerText = totalTracks;
            if (document.getElementById('expStatArtists')) document.getElementById('expStatArtists').innerText = totalArtists;
            if (document.getElementById('expStatAlbums')) document.getElementById('expStatAlbums').innerText = totalAlbums;
            if (document.getElementById('expStatSize')) document.getElementById('expStatSize').innerText = totalMb > 1000 ? `${(totalMb/1024).toFixed(2)} GB` : `${totalMb} MB`;

            const container = document.getElementById('explorerContentArea');
            if (!container) return;

            if (currentExplorerSubTab === 'all') {
                let tracks = (libraryData || []).filter(item => {
                    if (q) {
                        const searchStr = `${item.name} ${item.display_title || ''} ${item.artist || ''} ${item.album || ''} ${item.folder || ''}`.toLowerCase();
                        if (!searchStr.includes(q)) return false;
                    }
                    if (filterArtist && (item.artist || 'Unknown Artist') !== filterArtist) return false;
                    if (filterAlbum && (item.album || item.folder || 'Single') !== filterAlbum) return false;
                    if (filterBitrate && item.bitrate !== filterBitrate) return false;
                    if (filterFormat) {
                        const extMatch = (item.full_path || '').toLowerCase().endsWith(filterFormat.toLowerCase());
                        const typeMatch = item.type === filterFormat;
                        if (!extMatch && !typeMatch) return false;
                    }
                    if (filterDuration) {
                        const secs = parseDurationSecs(item.duration);
                        if (filterDuration === 'short' && secs >= 180) return false;
                        if (filterDuration === 'medium' && (secs < 180 || secs > 300)) return false;
                        if (filterDuration === 'long' && secs <= 300) return false;
                    }
                    return true;
                });

                tracks.sort((a, b) => {
                    let vA, vB;
                    if (explorerSortKey === 'title') {
                        vA = (a.display_title || a.name || '').toLowerCase();
                        vB = (b.display_title || b.name || '').toLowerCase();
                    } else if (explorerSortKey === 'artist') {
                        vA = (a.artist || '').toLowerCase();
                        vB = (b.artist || '').toLowerCase();
                    } else if (explorerSortKey === 'album') {
                        vA = (a.album || a.folder || '').toLowerCase();
                        vB = (b.album || b.folder || '').toLowerCase();
                    } else if (explorerSortKey === 'bitrate') {
                        vA = parseBitrateNum(a.bitrate);
                        vB = parseBitrateNum(b.bitrate);
                    } else if (explorerSortKey === 'duration') {
                        vA = parseDurationSecs(a.duration);
                        vB = parseDurationSecs(b.duration);
                    } else if (explorerSortKey === 'size') {
                        vA = a.size_bytes || 0;
                        vB = b.size_bytes || 0;
                    } else { // 'date'
                        vA = a.mtime_raw || 0;
                        vB = b.mtime_raw || 0;
                    }

                    if (vA < vB) return explorerSortAsc ? -1 : 1;
                    if (vA > vB) return explorerSortAsc ? 1 : -1;
                    return 0;
                });

                if (tracks.length === 0) {
                    container.innerHTML = `<p style="color: var(--text-sub); text-align: center; padding: 24px;">No tracks matching the current search/filters.</p>`;
                    return;
                }

                const sortArrow = (key) => {
                    if (explorerSortKey === key) {
                        return `<span class="sort-badge-pill">${explorerSortAsc ? '▲ ASC' : '▼ DESC'}</span>`;
                    }
                    return ' <span class="sort-icon">⇅</span>';
                };
                const thClass = (key) => `sortable-th ${explorerSortKey === key ? 'th-sorted' : ''}`;

                container.innerHTML = `
                    <div class="table-wrap">
                        <table class="explorer-table">
                            <thead>
                                <tr>
                                    <th style="width: 50px;"></th>
                                    <th onclick="sortExplorer('title')" class="${thClass('title')}" title="Click to sort by Title">Track Title & Artist ${sortArrow('title')}</th>
                                    <th onclick="sortExplorer('album')" class="${thClass('album')}" title="Click to sort by Album">Album / Folder ${sortArrow('album')}</th>
                                    <th onclick="sortExplorer('bitrate')" class="${thClass('bitrate')}" title="Click to sort by Quality">Quality ${sortArrow('bitrate')}</th>
                                    <th onclick="sortExplorer('duration')" class="${thClass('duration')}" title="Click to sort by Duration">Duration ${sortArrow('duration')}</th>
                                    <th onclick="sortExplorer('size')" class="${thClass('size')}" title="Click to sort by Size">Size ${sortArrow('size')}</th>
                                    <th onclick="sortExplorer('date')" class="${thClass('date')}" title="Click to sort by Date Added">Date Added ${sortArrow('date')}</th>
                                    <th style="text-align: right;">Actions</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${tracks.map((tr, idx) => `
                                    <tr>
                                        <td>
                                            <img src="/api/cover?path=${encodeURIComponent(tr.full_path)}" style="width: 38px; height: 38px; border-radius: 6px; object-fit: cover; background: #000;" alt="Art">
                                        </td>
                                        <td class="${explorerSortKey === 'title' ? 'td-sorted' : ''}">
                                            <div style="font-weight: 700; color: var(--text-main);">${tr.display_title || tr.name}</div>
                                            <div style="font-size: 0.78rem; color: var(--text-sub); margin-top: 2px;">${tr.artist ? '👤 ' + tr.artist : 'Unknown Artist'}</div>
                                        </td>
                                        <td class="${explorerSortKey === 'album' ? 'td-sorted' : ''}">
                                            <div style="color: var(--text-main); font-weight: 500;">${tr.album || 'Single'}</div>
                                            <div style="font-size: 0.75rem; color: var(--text-sub);">${tr.folder}</div>
                                        </td>
                                        <td class="${explorerSortKey === 'bitrate' ? 'td-sorted' : ''}"><span class="pill pill-bitrate">${tr.bitrate || '320 kbps'}</span></td>
                                        <td class="${explorerSortKey === 'duration' ? 'td-sorted' : ''}">${tr.duration || 'N/A'}</td>
                                        <td class="${explorerSortKey === 'size' ? 'td-sorted' : ''}">${tr.size || 'N/A'}</td>
                                        <td class="${explorerSortKey === 'date' ? 'td-sorted' : ''}" style="font-size: 0.78rem; color: var(--text-sub); white-space: nowrap;">${tr.date || 'N/A'}</td>
                                        <td style="text-align: right;">
                                            <div style="display: inline-flex; gap: 6px;">
                                                <button class="btn-act play" onclick="playInBrowser(fromB64('${toB64(tr.full_path)}'), fromB64('${toB64(tr.display_title || tr.name)}'), fromB64('${toB64(tr.artist || '')}'), ${idx})">▶ Play</button>
                                                <button class="btn-act" onclick="addToPlayerQueue(libraryData[${idx}])" title="Add to Queue">➕</button>
                                                <button class="btn-act" onclick="openEditMetadataModal(fromB64('${toB64(tr.full_path)}'))" title="Edit Metadata & Cover Art">✏️ Edit</button>
                                                <button class="btn-act vlc" onclick="openLocal(fromB64('${toB64(tr.full_path)}'), 'vlc')" title="Play in VLC">🎬</button>
                                                <button class="btn-act" onclick="openLocal(fromB64('${toB64(tr.full_path)}'), 'folder')" title="Open Local Folder">📂</button>
                                                <button class="btn-act" onclick="deleteSingleTrack(fromB64('${toB64(tr.full_path)}'))" title="Delete Track" style="color: #ef4444; border-color: rgba(239,68,68,0.3);">🗑️</button>
                                            </div>
                                        </td>
                                    </tr>
                                `).join('')}
                            </tbody>
                        </table>
                    </div>
                `;
            } else if (currentExplorerSubTab === 'artists') {
                const artists = (explorerData.artists || []).filter(a => !q || a.name.toLowerCase().includes(q));
                if (artists.length === 0) {
                    container.innerHTML = `<p style="color: var(--text-sub); text-align: center; padding: 24px;">No artists matching "${q}".</p>`;
                    return;
                }

                container.innerHTML = `
                    <div class="grid-cards">
                        ${artists.map(a => `
                            <div class="entity-card">
                                <div style="display: flex; align-items: center; gap: 12px;">
                                    <img src="/api/cover?path=${encodeURIComponent(a.sample_track)}" style="width: 52px; height: 52px; border-radius: 10px; object-fit: cover; background: #000;" alt="Art">
                                    <div style="overflow: hidden; flex: 1;">
                                        <div class="entity-card-title">${a.name}</div>
                                        <div class="entity-card-sub">🎵 ${a.track_count} tracks • ${a.album_count} albums</div>
                                        <div class="entity-card-sub">💾 ${a.total_size_mb} MB</div>
                                    </div>
                                </div>
                                <div style="display: flex; gap: 8px; margin-top: 6px;">
                                    <button class="btn-act play" style="flex: 1; font-size: 0.78rem;" onclick="filterExplorerByTerm('${a.name.replace(/'/g, "\\'")}')">🔍 View Songs</button>
                                </div>
                            </div>
                        `).join('')}
                    </div>
                `;
            } else if (currentExplorerSubTab === 'albums') {
                const albums = (explorerData.albums || []).filter(alb => !q || `${alb.title} ${alb.artist}`.toLowerCase().includes(q));
                if (albums.length === 0) {
                    container.innerHTML = `<p style="color: var(--text-sub); text-align: center; padding: 24px;">No albums matching "${q}".</p>`;
                    return;
                }

                container.innerHTML = `
                    <div class="grid-cards">
                        ${albums.map(alb => `
                            <div class="entity-card">
                                <div style="display: flex; align-items: center; gap: 12px;">
                                    <img src="/api/cover?path=${encodeURIComponent(alb.sample_track)}" style="width: 52px; height: 52px; border-radius: 10px; object-fit: cover; background: #000;" alt="Art">
                                    <div style="overflow: hidden; flex: 1;">
                                        <div class="entity-card-title">${alb.title}</div>
                                        <div class="entity-card-sub">👤 ${alb.artist}</div>
                                        <div class="entity-card-sub">🎵 ${alb.track_count} tracks • ${alb.total_size_mb} MB</div>
                                    </div>
                                </div>
                                <div style="display: flex; gap: 6px; margin-top: 6px;">
                                    <button class="btn-act play" style="flex: 1; font-size: 0.78rem;" onclick="openLocal('${alb.folder_path.replace(/'/g, "\\'")}', 'vlc')">🎬 Play Album</button>
                                    <button class="btn-act" style="font-size: 0.78rem;" onclick="openLocal('${alb.folder_path.replace(/'/g, "\\'")}', 'folder')">📂</button>
                                </div>
                            </div>
                        `).join('')}
                    </div>
                `;
            } else if (currentExplorerSubTab === 'playlists') {
                const playlists = (playlistsData || []).filter(p => !q || p.name.toLowerCase().includes(q));
                if (playlists.length === 0) {
                    container.innerHTML = `<p style="color: var(--text-sub); text-align: center; padding: 24px;">No playlists found.</p>`;
                    return;
                }

                container.innerHTML = `
                    <div class="grid-cards">
                        ${playlists.map(p => `
                            <div class="entity-card">
                                <div style="display: flex; align-items: center; gap: 12px;">
                                    <div style="width: 52px; height: 52px; border-radius: 10px; background: var(--bg-card); display: flex; align-items: center; justify-content: center; font-size: 1.5rem; border: 1px solid var(--border-item);">
                                        ${p.badge_type === 'BPM' ? '⚡' : '💽'}
                                    </div>
                                    <div style="overflow: hidden; flex: 1;">
                                        <div class="entity-card-title">${p.name}</div>
                                        <div class="entity-card-sub">🎵 ${p.track_count} tracks • ${p.total_size_mb} MB</div>
                                        <div class="entity-card-sub">⚡ .m3u8 auto-synced</div>
                                    </div>
                                </div>
                                <div style="display: flex; gap: 6px; margin-top: 6px;">
                                    <button class="btn-act play" style="flex: 1; font-size: 0.78rem;" onclick="playPlaylistInBrowser('${encodeURIComponent(p.m3u_path)}')">▶ Browser</button>
                                    <button class="btn-act vlc" style="flex: 1; font-size: 0.78rem;" onclick="openPlaylistVlc('${encodeURIComponent(p.folder_path)}')">🎬 VLC</button>
                                </div>
                            </div>
                        `).join('')}
                    </div>
                `;
            }
        }

        function filterExplorerByTerm(term) {
            currentExplorerSubTab = 'all';
            document.getElementById('expTabAll').classList.add('active');
            document.getElementById('expTabArtists').classList.remove('active');
            document.getElementById('expTabAlbums').classList.remove('active');
            document.getElementById('expTabPlaylists').classList.remove('active');
            const searchInput = document.getElementById('explorerSearchInput');
            if (searchInput) searchInput.value = term;
            renderExplorerView();
        }

        function renderHistoryView() {
            const histList = historyData || [];
            const analytics = historyAnalytics || {};
            const q = (document.getElementById('historySearchInput')?.value || '').toLowerCase().trim();

            if (document.getElementById('histStatJobs')) document.getElementById('histStatJobs').innerText = analytics.total_jobs || histList.length || 0;
            if (document.getElementById('histStatTracks')) document.getElementById('histStatTracks').innerText = analytics.total_tracks_downloaded || 0;
            if (document.getElementById('histStatSuccess')) document.getElementById('histStatSuccess').innerText = `${analytics.avg_success_rate || 100}%`;
            
            const totalBytes = libraryData.reduce((acc, m) => acc + (m.size_bytes || 0), 0);
            const totalMb = (totalBytes / (1024 * 1024)).toFixed(1);
            if (document.getElementById('histStatSize')) document.getElementById('histStatSize').innerText = `${totalMb} MB`;

            const tbody = document.getElementById('historyTableBody');
            if (!tbody) return;

            const filtered = histList.filter(h => {
                if (!q) return true;
                return `${h.title} ${h.url} ${h.status}`.toLowerCase().includes(q);
            });

            if (filtered.length === 0) {
                tbody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: var(--text-sub); padding: 24px;">No download history recorded yet.</td></tr>`;
                return;
            }

            tbody.innerHTML = filtered.map(h => {
                const expected = h.expected_count || 1;
                const downloaded = h.downloaded_count || 0;
                const pct = Math.min(100, Math.round((downloaded / expected) * 100));
                const isSuccess = h.status === 'completed' || h.status === 'ready';

                return `
                    <tr>
                        <td style="white-space: nowrap; font-size: 0.8rem; color: var(--text-sub);">
                            ${h.completed_at || h.added_at || 'Recent'}
                        </td>
                        <td>
                            <div style="font-weight: 700; font-size: 0.94rem;">
                                <a href="${h.url}" target="_blank" rel="noopener noreferrer" style="color: var(--text-main); text-decoration: underline; text-underline-offset: 3px; display: inline-flex; align-items: center; gap: 6px;">
                                    <span>${h.mode === 'video' ? '📺' : '🎵'}</span>
                                    <span>${h.title || 'Spotify Release'}</span>
                                    <span style="font-size: 0.72rem; color: var(--text-accent); opacity: 0.85;">↗</span>
                                </a>
                            </div>
                        </td>
                        <td>
                            <span class="status-badge status-${h.status}">${h.status.toUpperCase()}</span>
                        </td>
                        <td>
                            <div onclick="inspectHistoryJob(fromB64('${toB64(h.url)}'), fromB64('${toB64(h.title || '')}'), '${h.id || h.job_id}')" style="cursor: pointer; display: inline-block; padding: 4px 8px; border-radius: 8px; background: rgba(56, 189, 248, 0.08); border: 1px solid rgba(56, 189, 248, 0.25); transition: all 0.2s ease;" onmouseover="this.style.borderColor='rgba(56,189,248,0.6)'; this.style.background='rgba(56,189,248,0.14)'" onmouseout="this.style.borderColor='rgba(56,189,248,0.25)'; this.style.background='rgba(56,189,248,0.08)'" title="Click to inspect missing & downloaded tracks">
                                <div style="font-weight: 700; font-size: 0.82rem; color: var(--text-main); display: flex; align-items: center; justify-content: space-between; gap: 8px;">
                                    <span>${downloaded} / ${expected} tracks (${pct}%)</span>
                                    <span style="font-size: 0.72rem; color: #38bdf8; font-weight: 800;">🔍 Details</span>
                                </div>
                                <div style="width: 130px; background: var(--bg-input); height: 5px; border-radius: 3px; overflow: hidden; margin-top: 4px;">
                                    <div style="width: ${pct}%; background: ${isSuccess ? '#10b981' : '#0284c7'}; height: 100%;"></div>
                                </div>
                            </div>
                        </td>
                        <td style="font-size: 0.82rem; color: var(--text-sub);">${h.duration_display || 'N/A'}</td>
                        <td style="font-size: 0.82rem; color: var(--text-sub); font-weight: 700;">
                            <span class="pill" style="font-size: 0.75rem; background: ${h.retry_count > 0 ? 'rgba(245,158,11,0.15)' : 'var(--bg-input)'}; color: ${h.retry_count > 0 ? '#f59e0b' : 'var(--text-sub)'};">
                                ${h.retry_count || 0} ${h.retry_count === 1 ? 'try' : 'tries'}
                            </span>
                        </td>
                        <td>
                            <div style="display: flex; gap: 6px;">
                                <button class="btn-act restart" onclick="smartReDownload(fromB64('${toB64(h.url)}'), '${h.mode || 'audio'}', fromB64('${toB64(h.title || '')}'), '${h.id || h.job_id}', ${downloaded}, ${expected})" title="Re-download">🔄 Re-Download</button>
                                <button class="btn-act del" onclick="deleteHistoryItem('${h.id || h.job_id}')" title="Delete record">✕</button>
                            </div>
                        </td>
                    </tr>
                `;
            }).join('');
        }

        let currentInspectData = null;
        let currentInspectSubTab = 'missing';

        async function inspectHistoryJob(url, title, id) {
            const modal = document.getElementById('jobInspectModal');
            const loading = document.getElementById('inspectLoading');
            const content = document.getElementById('inspectContent');
            const titleEl = document.getElementById('inspectJobTitle');
            const urlEl = document.getElementById('inspectJobUrl');

            if (titleEl) titleEl.innerText = title || "Playlist Diagnostic";
            if (urlEl) urlEl.innerText = url;
            if (loading) loading.style.display = 'flex';
            if (content) content.style.display = 'none';
            if (modal) modal.style.display = 'flex';

            try {
                const res = await fetch('/api/analyze_job_tracks', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url, id })
                });
                const data = await res.json();
                currentInspectData = data;

                if (loading) loading.style.display = 'none';
                if (content) content.style.display = 'block';

                renderInspectModalContent();
            } catch (e) {
                if (loading) loading.innerHTML = `<p style="color: #ef4444; padding: 20px;">Failed to inspect tracks: ${e.message}</p>`;
            }
        }

        function closeJobInspectModal() {
            const modal = document.getElementById('jobInspectModal');
            if (modal) modal.style.display = 'none';
            currentInspectData = null;
        }

        function switchInspectSubTab(subTab) {
            currentInspectSubTab = subTab;
            const btnMissing = document.getElementById('inspectTabMissing');
            const btnDl = document.getElementById('inspectTabDownloaded');
            if (btnMissing) btnMissing.classList.toggle('active', subTab === 'missing');
            if (btnDl) btnDl.classList.toggle('active', subTab === 'downloaded');
            renderInspectTrackTable();
        }

        function renderInspectModalContent() {
            if (!currentInspectData) return;
            const d = currentInspectData;

            // Metrics
            document.getElementById('metricCompletion').innerText = `${d.completion_pct}%`;
            document.getElementById('metricCompletionSub').innerText = `Acquired: ${d.total_downloaded} / ${d.total_expected}`;

            document.getElementById('metricStorage').innerText = `${d.downloaded_mb} MB`;
            document.getElementById('metricStorageSub').innerText = `Acquired • Missing: ~${d.estimated_missing_mb} MB`;

            document.getElementById('metricQuality').innerText = d.avg_bitrate || '320 kbps';
            document.getElementById('metricQualitySub').innerText = `Quality • Tagged: ${d.id3_health || '100%'}`;

            document.getElementById('metricRetries').innerText = `${d.total_retries} tries`;
            document.getElementById('metricRetriesSub').innerText = `Status: ${d.status.toUpperCase()} • ${d.last_attempt}`;

            document.getElementById('inspectMissingCount').innerText = d.total_missing;
            document.getElementById('inspectDownloadedCount').innerText = d.total_downloaded;

            // Actions Bar
            const actBar = document.getElementById('inspectActionsBar');
            if (actBar) {
                let btns = '';
                if (d.total_missing > 0) {
                    btns += `
                        <button class="btn-act play" onclick="reDownloadMissingOnly()" style="padding: 10px 20px; font-weight: 700; font-size: 0.9rem; background: linear-gradient(135deg, #0284c7, #0ea5e9); box-shadow: 0 4px 14px rgba(14,165,233,0.4); border: none; color: #fff;">
                            ⚡ Re-Download Missing Tracks Only (${d.total_missing})
                        </button>
                    `;
                }
                btns += `
                    <button class="btn-clear" onclick="reDownloadFullPlaylist()" style="padding: 10px 18px; font-size: 0.88rem;">
                        🔄 Full Playlist Re-Download (${d.total_expected})
                    </button>
                `;
                actBar.innerHTML = btns;
            }

            // Default subtab
            currentInspectSubTab = d.total_missing > 0 ? 'missing' : 'downloaded';
            switchInspectSubTab(currentInspectSubTab);
        }

        function renderInspectTrackTable() {
            if (!currentInspectData) return;
            const tbody = document.getElementById('inspectTableBody');
            if (!tbody) return;

            const isMissing = currentInspectSubTab === 'missing';
            const tracks = isMissing ? (currentInspectData.missing_tracks || []) : (currentInspectData.downloaded_tracks || []);

            if (tracks.length === 0) {
                tbody.innerHTML = `
                    <tr>
                        <td colspan="5" style="text-align: center; color: var(--text-sub); padding: 28px;">
                            ${isMissing ? '🎉 No missing tracks! All songs in this release have been acquired.' : 'No downloaded tracks yet.'}
                        </td>
                    </tr>
                `;
                return;
            }

            tbody.innerHTML = tracks.map(tr => {
                if (isMissing) {
                    return `
                        <tr style="border-bottom: 1px solid var(--border-color);">
                            <td style="padding: 10px 14px; font-weight: 700; color: var(--text-sub);">${tr.index}</td>
                            <td style="padding: 10px 14px;">
                                <div style="font-weight: 700; color: var(--text-main);">${tr.title}</div>
                                <div style="font-size: 0.78rem; color: var(--text-sub); margin-top: 2px;">👤 ${tr.artist || 'Unknown Artist'}</div>
                            </td>
                            <td style="padding: 10px 14px;">
                                <span class="pill" style="background: rgba(239, 68, 68, 0.15); color: #ef4444; border: 1px solid rgba(239, 68, 68, 0.3); font-weight: 700; font-size: 0.75rem;">⚠️ Missing</span>
                                <div style="font-size: 0.74rem; color: var(--text-sub); margin-top: 3px;">Tried: ${tr.retry_count || 0} times</div>
                            </td>
                            <td style="padding: 10px 14px; color: var(--text-sub);">${tr.duration || 'N/A'}</td>
                            <td style="padding: 10px 14px; text-align: right;">
                                <div style="display: inline-flex; gap: 6px;">
                                    <button class="btn-act play" onclick="reDownloadSingleTrack('${(tr.track_url || '').replace(/'/g, "\\'")}', '${(tr.title || '').replace(/'/g, "\\'")}', '${(tr.artist || '').replace(/'/g, "\\'")}')" style="padding: 5px 12px; font-size: 0.78rem;" title="Retry this single track">🔁 Retry</button>
                                    ${tr.track_url ? `<a href="${tr.track_url}" target="_blank" rel="noopener" class="btn-clear" style="padding: 5px 10px; font-size: 0.78rem; text-decoration: none;" title="Open on Spotify">🔗</a>` : ''}
                                </div>
                            </td>
                        </tr>
                    `;
                } else {
                    return `
                        <tr style="border-bottom: 1px solid var(--border-color);">
                            <td style="padding: 10px 14px; font-weight: 700; color: var(--text-sub);">${tr.index}</td>
                            <td style="padding: 10px 14px;">
                                <div style="font-weight: 700; color: var(--text-main);">${tr.title}</div>
                                <div style="font-size: 0.78rem; color: var(--text-sub); margin-top: 2px;">👤 ${tr.artist || 'Unknown Artist'} • 💽 ${tr.album || 'Single'}</div>
                            </td>
                            <td style="padding: 10px 14px;">
                                <span class="pill pill-bitrate">${tr.bitrate || '320 kbps'}</span>
                                <span class="pill" style="background: rgba(16, 185, 129, 0.15); color: #10b981; border: 1px solid rgba(16, 185, 129, 0.3); font-weight: 700; font-size: 0.75rem; margin-left: 4px;">✅ Acquired</span>
                            </td>
                            <td style="padding: 10px 14px; color: var(--text-sub);">${tr.duration || 'N/A'}</td>
                            <td style="padding: 10px 14px; text-align: right;">
                                <div style="display: inline-flex; gap: 6px;">
                                    ${tr.full_path ? `
                                        <button class="btn-act play" onclick="playInBrowser(fromB64('${toB64(tr.full_path)}'), fromB64('${toB64(tr.title)}'), fromB64('${toB64(tr.artist)}'))" style="padding: 5px 10px; font-size: 0.78rem;">▶</button>
                                        <button class="btn-act" onclick="openLocal(fromB64('${toB64(tr.full_path)}'), 'folder')" style="padding: 5px 10px; font-size: 0.78rem;" title="Open Folder">📂</button>
                                    ` : ''}
                                </div>
                            </td>
                        </tr>
                    `;
                }
            }).join('');
        }

        async function reDownloadMissingOnly() {
            if (!currentInspectData || !currentInspectData.missing_tracks || currentInspectData.missing_tracks.length === 0) {
                showToast("No missing tracks to download.", "info");
                return;
            }
            const count = currentInspectData.missing_tracks.length;
            const res = await fetch('/api/redownload_missing_tracks', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    url: currentInspectData.url,
                    tracks: currentInspectData.missing_tracks,
                    mode: 'audio'
                })
            });
            const data = await res.json();
            showToast(`⚡ Enqueued ${data.queued_count || count} missing tracks!`, 'success');
            closeJobInspectModal();
            switchMainView('studio');
            fetchStatus();
        }

        async function reDownloadSingleTrack(trackUrl, title, artist) {
            if (!trackUrl) return;
            const res = await fetch('/api/redownload_missing_tracks', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    tracks: [{ track_url: trackUrl, title, artist }],
                    mode: 'audio'
                })
            });
            const data = await res.json();
            showToast(`⚡ Enqueued single track: "${title}"`, 'success');
            fetchStatus();
        }

        async function reDownloadFullPlaylist() {
            if (!currentInspectData) return;
            await reDownloadHistory(currentInspectData.url, 'audio', currentInspectData.title);
            closeJobInspectModal();
        }

        async function smartReDownload(url, mode, title, id, downloaded, expected) {
            if (expected > 1 && downloaded < expected && (expected - downloaded) > 0) {
                const missingCount = expected - downloaded;
                const ok = await showConfirmDialog({
                    title: "⚡ Smart Re-Download",
                    message: `This playlist is missing ${missingCount} tracks out of ${expected}.\n\nWould you like to inspect & re-download ONLY the missing tracks, or re-download everything from scratch?`,
                    confirmText: `⚡ Inspect Missing (${missingCount})`,
                    cancelText: "🔄 Full Re-Download",
                    icon: "⚡",
                    isDanger: false
                });
                if (ok) {
                    inspectHistoryJob(url, title, id);
                    return;
                }
            }
            await reDownloadHistory(url, mode, title);
        }

        async function reDownloadHistory(url, mode, title) {
            const t = i18n[currentLang];
            await fetch('/api/redownload_history_item', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url, mode, title })
            });
            showToast(t.toastHistReDownloaded, 'success');
            switchMainView('studio');
            fetchStatus();
        }

        async function deleteHistoryItem(id) {
            const t = i18n[currentLang];
            await fetch('/api/delete_history_item', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id })
            });
            showToast(t.toastHistDeleted, 'info');
            fetchStatus();
        }

        async function clearAllHistory() {
            const t = i18n[currentLang];
            const ok = await showConfirmDialog({
                title: "Clear Download History?",
                message: "Are you sure you want to clear all download history records? (Your downloaded music files will not be touched).",
                confirmText: "🧹 Clear Records",
                icon: "🧹",
                isDanger: false
            });
            if (!ok) return;
            await fetch('/api/clear_history', { method: 'POST' });
            showToast(t.toastHistCleared, 'info');
            fetchStatus();
        }

        function clearLibSearch() {
            const input = document.getElementById('librarySearchInput');
            if (input) {
                input.value = '';
                renderLibrary();
                input.focus();
            }
        }

        function renderLibrary() {
            const mList = document.getElementById('mediaList');
            const pContainer = document.getElementById('playlistBannerContainer');
            const t = i18n[currentLang];
            const searchInput = document.getElementById('librarySearchInput');
            const q = searchInput ? searchInput.value.toLowerCase().trim() : '';
            const clearBtn = document.getElementById('btnClearLibSearch');
            if (clearBtn) clearBtn.style.display = q ? 'inline-block' : 'none';

            let filteredLib = libraryData || [];
            if (q) {
                filteredLib = (libraryData || []).filter(m => {
                    const str = `${m.name || ''} ${m.display_title || ''} ${m.artist || ''} ${m.album || ''} ${m.folder || ''} ${m.bitrate || ''} ${m.bpm || ''}`.toLowerCase();
                    return str.includes(q);
                });
            }

            // Render Playlist Header Cards (hide if actively searching)
            if (!q && playlistsData && playlistsData.length > 0) {
                pContainer.innerHTML = '';
                playlistsData.forEach(p => {
                    let badgeIcon = '💽';
                    if (p.badge_type === 'BPM') badgeIcon = '⚡';
                    else if (p.badge_type === 'Genre') badgeIcon = '🏷️';
                    else if (p.badge_type === 'Album') badgeIcon = '🎵';

                    const card = document.createElement('div');
                    card.className = 'playlist-header-card';
                    card.style.marginBottom = '10px';

                    const titleBox = document.createElement('div');
                    titleBox.className = 'playlist-title-box';

                    const iconSpan = document.createElement('span');
                    iconSpan.style.fontSize = '1.8rem';
                    iconSpan.textContent = badgeIcon;
                    titleBox.appendChild(iconSpan);

                    const textWrap = document.createElement('div');
                    const titleEl = document.createElement('div');
                    titleEl.className = 'playlist-title';
                    titleEl.textContent = p.name;
                    textWrap.appendChild(titleEl);

                    const subEl = document.createElement('div');
                    subEl.className = 'playlist-sub';
                    subEl.innerHTML = `${p.track_count} tracks • ${p.total_size_mb} MB • <span class="pill pill-m3u">⚡ .m3u8</span>`;
                    textWrap.appendChild(subEl);
                    titleBox.appendChild(textWrap);
                    card.appendChild(titleBox);

                    const actBox = document.createElement('div');
                    actBox.className = 'playlist-actions';

                    const vlcBtn = document.createElement('button');
                    vlcBtn.className = 'btn-act playlist-vlc';
                    vlcBtn.textContent = t.playAllVlc;
                    vlcBtn.onclick = () => openPlaylistVlc(p.m3u_path);
                    actBox.appendChild(vlcBtn);

                    const playBtn = document.createElement('button');
                    playBtn.className = 'btn-act play';
                    playBtn.textContent = t.playAllBrowser;
                    playBtn.onclick = () => playPlaylistInBrowser(p.m3u_path);
                    actBox.appendChild(playBtn);

                    const fldBtn = document.createElement('button');
                    fldBtn.className = 'btn-act';
                    fldBtn.textContent = '📂';
                    fldBtn.title = 'Open Folder';
                    fldBtn.onclick = () => openLocal(p.m3u_path, 'folder');
                    actBox.appendChild(fldBtn);

                    card.appendChild(actBox);
                    pContainer.appendChild(card);
                });
            } else {
                pContainer.innerHTML = '';
            }

            if (!filteredLib || filteredLib.length === 0) {
                mList.innerHTML = `<p style="color: var(--text-sub); padding: 16px; text-align: center;">${q ? `No downloaded MP3s matching "${q}".` : t.lblNoTasks}</p>`;
                return;
            }

            mList.innerHTML = '';
            filteredLib.forEach((m, idx) => {
                const itemDiv = document.createElement('div');
                itemDiv.className = `item ${m.type === 'Video' ? 'video' : ''}`;

                const mainDiv = document.createElement('div');
                mainDiv.className = 'item-main-content';

                const coverImg = document.createElement('img');
                coverImg.className = 'cover-art-thumb';
                coverImg.src = `/api/cover?path=${encodeURIComponent(m.full_path)}`;
                coverImg.alt = 'Cover';
                coverImg.loading = 'lazy';
                mainDiv.appendChild(coverImg);

                const infoDiv = document.createElement('div');
                infoDiv.className = 'item-info';

                const titleDiv = document.createElement('div');
                titleDiv.className = 'item-title';
                titleDiv.textContent = m.display_title;
                infoDiv.appendChild(titleDiv);

                if (m.artist) {
                    const artDiv = document.createElement('div');
                    artDiv.className = 'item-artist';
                    artDiv.textContent = '👤 ' + m.artist;
                    infoDiv.appendChild(artDiv);
                }

                const metaDiv = document.createElement('div');
                metaDiv.className = 'item-meta';
                const bpmPill = (m.bpm && m.bpm !== 'N/A') ? `<span class="pill pill-bpm">⚡ ${m.bpm}</span>` : '';
                metaDiv.innerHTML = `
                    ${bpmPill}
                    <span class="pill pill-bitrate">🎚️ ${m.bitrate}</span>
                    <span class="pill">⏱️ ${m.duration}</span>
                    <span class="pill">📻 ${m.sample_rate}</span>
                    <span class="pill">📂 ${m.folder}</span>
                    <span class="pill">💾 ${m.size}</span>
                `;
                infoDiv.appendChild(metaDiv);
                mainDiv.appendChild(infoDiv);
                itemDiv.appendChild(mainDiv);

                const actDiv = document.createElement('div');
                actDiv.className = 'actions';

                const playBtn = document.createElement('button');
                playBtn.className = 'btn-act play';
                playBtn.textContent = '▶ Play';
                playBtn.onclick = () => playInBrowser(m.full_path, m.display_title, m.artist || m.folder, idx);
                actDiv.appendChild(playBtn);

                const qBtn = document.createElement('button');
                qBtn.className = 'btn-act';
                qBtn.textContent = '➕ Queue';
                qBtn.title = 'Add to playback queue';
                qBtn.onclick = () => addToPlayerQueue(m);
                actDiv.appendChild(qBtn);

                const vlcBtn = document.createElement('button');
                vlcBtn.className = 'btn-act vlc';
                vlcBtn.textContent = '🎬 VLC';
                vlcBtn.title = 'Open in VLC';
                vlcBtn.onclick = () => openLocal(m.full_path, 'vlc');
                actDiv.appendChild(vlcBtn);

                const editBtn = document.createElement('button');
                editBtn.className = 'btn-act';
                editBtn.textContent = '✏️';
                editBtn.title = 'Edit Metadata & Tags';
                editBtn.onclick = () => openEditMetadataModal(m.full_path);
                actDiv.appendChild(editBtn);

                const fldBtn = document.createElement('button');
                fldBtn.className = 'btn-act';
                fldBtn.textContent = '📂';
                fldBtn.title = 'Open Folder';
                fldBtn.onclick = () => openLocal(m.full_path, 'folder');
                actDiv.appendChild(fldBtn);

                const delBtn = document.createElement('button');
                delBtn.className = 'btn-act del';
                delBtn.textContent = '🗑️';
                delBtn.title = 'Delete Track';
                delBtn.style.color = '#ef4444';
                delBtn.style.borderColor = 'rgba(239,68,68,0.3)';
                delBtn.onclick = () => deleteSingleTrack(m.full_path);
                actDiv.appendChild(delBtn);

                itemDiv.appendChild(actDiv);
                mList.appendChild(itemDiv);
            });
        }

        async function fetchStatus() {
            try {
                const res = await fetch('/api/status');
                const data = await res.json();
                const t = i18n[currentLang];
                
                if (data.metrics) {
                    document.getElementById('statTracks').innerText = data.metrics.total_tracks;
                    document.getElementById('statStorage').innerText = data.metrics.total_size_mb > 1000 ? `${data.metrics.total_size_gb} GB` : `${data.metrics.total_size_mb} MB`;
                    document.getElementById('statActive').innerText = data.metrics.active_jobs;
                    document.getElementById('statCompleted').innerText = data.metrics.completed_jobs;
                }
                
                document.getElementById('totalCount').innerText = (data.metrics ? data.metrics.total_tracks : (data.library ? data.library.length : (data.total_media || 0)));

                const dupBadge = document.getElementById('badgeDupCount');
                if (dupBadge && typeof data.duplicates_count === 'number') {
                    dupBadge.innerText = data.duplicates_count;
                    dupBadge.style.display = data.duplicates_count > 0 ? 'inline-block' : 'none';
                }

                const queuePauseBadge = document.getElementById('queuePauseBadge');
                const queuePauseBtn = document.getElementById('btnTogglePauseQueue');
                const isQPaused = !!(data.metrics && data.metrics.is_queue_paused);

                if (queuePauseBadge) {
                    queuePauseBadge.style.display = isQPaused ? 'inline-block' : 'none';
                }
                if (queuePauseBtn) {
                    queuePauseBtn.innerHTML = isQPaused ? '▶️ Resume Queue' : '⏸️ Pause Queue';
                    queuePauseBtn.style.borderColor = isQPaused ? '#10b981' : '#eab308';
                    queuePauseBtn.style.color = isQPaused ? '#10b981' : '#eab308';
                }

                const qList = document.getElementById('queueList');
                if (data.queue.length === 0) {
                    qList.innerHTML = `<p style="color: var(--text-sub); padding: 10px;">${t.lblNoTasks}</p>`;
                } else {
                    qList.innerHTML = data.queue.map(j => {
                        const isDownloading = j.status === 'downloading';
                        const isPaused = j.status === 'paused';
                        const isCompleted = j.status === 'completed' || j.status === 'cancelled';
                        const dlCount = typeof j.downloaded_count === 'number' ? j.downloaded_count : (isCompleted ? (j.expected_count || 1) : 0);
                        const hasExpected = typeof j.expected_count === 'number' && j.expected_count > 0;
                        const pct = hasExpected ? Math.min(100, Math.round((dlCount / j.expected_count) * 100)) : 0;

                        return `
                        <div class="item" style="border-left-color: ${isDownloading ? '#38bdf8' : (isPaused ? '#eab308' : (j.status === 'retry_scheduled' ? '#f59e0b' : '#10b981'))}">
                            <div class="item-info">
                                <div class="item-title">
                                    <a href="${j.url}" target="_blank" rel="noopener noreferrer" style="color: var(--text-main); text-decoration: underline; text-underline-offset: 3px; font-weight: 700; display: inline-flex; align-items: center; gap: 6px;">
                                        <span>${j.mode === 'video' ? '📺' : '🎵'}</span>
                                        <span>${j.title || 'Spotify Release'}</span>
                                        <span style="font-size: 0.72rem; color: var(--text-accent); opacity: 0.85;">↗</span>
                                    </a>
                                </div>
                                <div class="item-meta" style="flex-direction: column; align-items: flex-start; gap: 4px;">
                                    <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
                                        ${hasExpected ? 
                                            `<span class="pill pill-count">📥 <strong>${dlCount} / ${j.expected_count} tracks</strong> (${pct}%)</span>` : 
                                            `<span class="pill pill-count">${t.downloadedBadge}<strong>${dlCount} MP3s</strong></span>`
                                        }
                                        ${j.status === 'retry_scheduled' ? `<span>${t.nextRetry}<strong>${j.next_retry}</strong> (Retry #${j.retry_count})</span>` : `<span>${t.status}<strong>${j.status}</strong> • ${t.added}${j.added_at}</span>`}
                                    </div>
                                    ${hasExpected ? `
                                    <div style="width: 100%; background: var(--bg-input); height: 5px; border-radius: 3px; overflow: hidden; margin-top: 4px;">
                                        <div style="width: ${pct}%; background: linear-gradient(90deg, #0284c7, #10b981); height: 100%; border-radius: 3px; transition: width 0.3s ease;"></div>
                                    </div>` : ''}
                                </div>
                            </div>
                            <div style="display:flex; align-items:center; gap:8px;">
                                <span class="status-badge status-${j.status}">${j.status.toUpperCase()}</span>
                                
                                ${isDownloading ? `<button class="btn-job pause" onclick="jobAction('${j.id}', 'pause')">${t.btnPause}</button>` : ''}
                                ${isPaused ? `<button class="btn-job resume" onclick="jobAction('${j.id}', 'resume')">${t.btnResume}</button>` : ''}
                                <button class="btn-job restart" onclick="jobAction('${j.id}', 'restart')" title="Restart download">${t.btnRestart}</button>
                                ${!isCompleted ? `<button class="btn-job cancel" onclick="jobAction('${j.id}', 'cancel')">${t.btnCancel}</button>` : ''}
                                
                                <button class="btn-job del" onclick="deleteJob('${j.id}')" title="Delete record">✕</button>
                            </div>
                        </div>
                    `;}).join('');
                }

                libraryData = data.library || [];
                playlistsData = data.playlists || [];
                historyData = data.history || [];
                historyAnalytics = data.history_analytics || {};
                explorerData = data.explorer || {};

                renderLibrary();
                renderTriage();
                renderExplorerView();
                renderHistoryView();

                const term = document.getElementById('terminalLog');
                term.innerText = data.logs.join('\\n');
                term.scrollTop = term.scrollHeight;

                // Check for completion notification
                const currentJobStatus = data.active_job ? data.active_job.status : '';
                if (lastJobStatus === 'downloading' && !data.active_job) {
                    if ("Notification" in window && Notification.permission === "granted") {
                        const notif = new Notification("🎵 Media Studio: Descarga Finalizada", {
                            body: `Se descargaron las canciones en tu biblioteca. Haz clic para verlas.`,
                            icon: "/api/cover"
                        });
                        notif.onclick = () => { window.focus(); notif.close(); };
                    }
                }
                lastJobStatus = currentJobStatus;

            } catch (err) {
                console.error("Error fetching status:", err);
            }
        }

        async function jobAction(id, action) {
            const t = i18n[currentLang];
            await fetch('/api/job_control', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id, action })
            });
            if (action === 'pause') showToast(t.toastPaused, "warning");
            else if (action === 'resume') showToast(t.toastResumed, "success");
            else if (action === 'restart') showToast(t.toastRestarted, "success");
            else if (action === 'cancel') showToast(t.toastCancelled, "warning");
            fetchStatus();
        }

        async function deleteJob(id) {
            await fetch('/api/delete_job', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id })
            });
            fetchStatus();
        }

        async function openPlaylistVlc(encodedPath) {
            const folder_path = decodeURIComponent(encodedPath);
            const t = i18n[currentLang];
            await fetch('/api/open_local', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: folder_path, action: 'vlc' })
            });
            showToast(t.toastVlcPl, "info");
        }

        async function playPlaylistInBrowser(encodedPath) {
            const path = decodeURIComponent(encodedPath);
            try {
                const res = await fetch(`/api/get_playlist_tracks?path=${encodeURIComponent(path)}`);
                const data = await res.json();
                if (data.tracks && data.tracks.length > 0) {
                    playerQueue = data.tracks;
                    playTrackObject(playerQueue[0], 0);
                    showToast(`▶ Loaded ${data.tracks.length} tracks into player queue.`, "success");
                }
            } catch (e) {
                showToast("Error loading playlist", "warning");
            }
        }

        function playTrackObject(track, indexInQueue = -1) {
            if (!track) return;
            currentPlayingPath = track.full_path;
            currentQueueIndex = indexInQueue;
            
            const player = document.getElementById('audioPlayer');
            const sticky = document.getElementById('stickyPlayer');
            const cover = document.getElementById('playerCover');
            
            document.getElementById('playerTitle').textContent = track.display_title || track.name;
            document.getElementById('playerSub').textContent = track.artist ? `${track.artist} • ${track.folder}` : track.folder;
            cover.src = `/api/cover?path=${encodeURIComponent(track.full_path)}`;
            
            player.src = `/api/stream?path=${encodeURIComponent(track.full_path)}`;
            sticky.style.display = 'flex';
            
            if (isPlayerMinimized) {
                togglePlayerMinimize();
            }
            
            player.play().catch(e => console.log("Autoplay check:", e));
            renderQueueDrawer();
            updateMediaSession(track);

            player.onended = () => {
                playNextTrack();
            };
        }

        function playInBrowser(path, title, sub, idx = -1) {
            if (libraryData && libraryData.length > 0) {
                playerQueue = [...libraryData];
                let foundIdx = libraryData.findIndex(t => t.full_path === path);
                if (foundIdx === -1) foundIdx = idx >= 0 ? idx : 0;
                playTrackObject(playerQueue[foundIdx], foundIdx);
            } else {
                playerQueue = [{ full_path: path, display_title: title, artist: sub, folder: '' }];
                playTrackObject(playerQueue[0], 0);
            }
        }

        function addToPlayerQueue(track) {
            const t = i18n[currentLang];
            playerQueue.push(track);
            renderQueueDrawer();
            showToast(t.toastAddedQueue, "success");
            
            const player = document.getElementById('audioPlayer');
            if (!currentPlayingPath || (player.paused && player.src === '')) {
                playTrackObject(track, playerQueue.length - 1);
            }
        }

        function playNextTrack() {
            if (playerQueue.length === 0) return;
            if (currentQueueIndex + 1 < playerQueue.length) {
                currentQueueIndex++;
                playTrackObject(playerQueue[currentQueueIndex], currentQueueIndex);
            } else {
                currentQueueIndex = 0;
                playTrackObject(playerQueue[0], 0);
            }
        }

        function playPrevTrack() {
            const player = document.getElementById('audioPlayer');
            if (player.currentTime > 3) {
                player.currentTime = 0;
                return;
            }
            if (playerQueue.length === 0) return;
            if (currentQueueIndex > 0) {
                currentQueueIndex--;
                playTrackObject(playerQueue[currentQueueIndex], currentQueueIndex);
            } else {
                player.currentTime = 0;
            }
        }

        function playAllInBrowser(startIndex = 0) {
            if (libraryData.length > startIndex) {
                playerQueue = [...libraryData];
                playTrackObject(playerQueue[startIndex], startIndex);
            }
        }

        function shuffleQueue() {
            if (playerQueue.length <= 1) return;
            const current = currentQueueIndex >= 0 ? playerQueue[currentQueueIndex] : null;
            let remaining = playerQueue.filter((_, idx) => idx !== currentQueueIndex);
            for (let i = remaining.length - 1; i > 0; i--) {
                const j = Math.floor(Math.random() * (i + 1));
                [remaining[i], remaining[j]] = [remaining[j], remaining[i]];
            }
            if (current) {
                playerQueue = [current, ...remaining];
                currentQueueIndex = 0;
            } else {
                playerQueue = remaining;
                currentQueueIndex = -1;
            }
            renderQueueDrawer();
        }

        function clearPlayerQueue() {
            const current = currentQueueIndex >= 0 ? playerQueue[currentQueueIndex] : null;
            if (current) {
                playerQueue = [current];
                currentQueueIndex = 0;
            } else {
                playerQueue = [];
                currentQueueIndex = -1;
            }
            renderQueueDrawer();
        }

        function removeFromQueue(index) {
            if (index === currentQueueIndex) {
                playNextTrack();
            }
            playerQueue.splice(index, 1);
            if (currentQueueIndex > index) {
                currentQueueIndex--;
            }
            renderQueueDrawer();
        }

        function toggleQueueDrawer() {
            const drawer = document.getElementById('queueDrawer');
            if (drawer.style.display === 'flex') {
                drawer.style.display = 'none';
            } else {
                drawer.style.display = 'flex';
                renderQueueDrawer();
            }
        }

        function renderQueueDrawer() {
            const badge = document.getElementById('queueBadge');
            const headCount = document.getElementById('queueHeaderCount');
            const container = document.getElementById('queueListContainer');
            const t = i18n[currentLang];
            
            if (badge) badge.innerText = playerQueue.length;
            if (headCount) headCount.innerText = playerQueue.length;
            if (!container) return;
            
            if (playerQueue.length === 0) {
                container.innerHTML = `<p style="color: var(--text-sub); font-size: 0.85rem; text-align: center; padding: 20px;">${t.emptyQueue}</p>`;
                return;
            }
            
            container.innerHTML = playerQueue.map((track, idx) => {
                const isCurrent = idx === currentQueueIndex;
                return `
                    <div class="queue-item ${isCurrent ? 'active' : ''}">
                        <img class="queue-thumb" src="/api/cover?path=${encodeURIComponent(track.full_path)}" alt="Cover">
                        <div class="queue-item-info" onclick="playTrackObject(playerQueue[${idx}], ${idx})">
                            <div class="queue-item-title">${isCurrent ? '▶ ' : ''}${track.display_title || track.name}</div>
                            <div class="queue-item-sub">${track.artist || track.folder || ''} ${track.duration ? '• ' + track.duration : ''}</div>
                        </div>
                        <button class="queue-item-del" onclick="removeFromQueue(${idx})" title="Remove from queue">✕</button>
                    </div>
                `;
            }).join('');
        }

        function updateMediaSession(track) {
            if ('mediaSession' in navigator) {
                navigator.mediaSession.metadata = new MediaMetadata({
                    title: track.display_title || track.name,
                    artist: track.artist || track.folder || 'Media Studio',
                    album: track.folder || 'Media Library',
                    artwork: [
                        { src: `/api/cover?path=${encodeURIComponent(track.full_path)}`, sizes: '512x512', type: 'image/jpeg' }
                    ]
                });
                navigator.mediaSession.setActionHandler('previoustrack', playPrevTrack);
                navigator.mediaSession.setActionHandler('nexttrack', playNextTrack);
            }
        }

        function togglePlayerMinimize() {
            const sticky = document.getElementById('stickyPlayer');
            const btn = document.getElementById('minBtn');
            isPlayerMinimized = !isPlayerMinimized;
            
            if (isPlayerMinimized) {
                sticky.classList.add('minimized');
                btn.innerText = '▲';
                btn.title = 'Expand Player';
                document.getElementById('queueDrawer').style.display = 'none';
            } else {
                sticky.classList.remove('minimized');
                btn.innerText = '▼';
                btn.title = 'Minimize Player';
            }
        }

        function closePlayer() {
            const player = document.getElementById('audioPlayer');
            const sticky = document.getElementById('stickyPlayer');
            player.pause();
            player.src = '';
            sticky.style.display = 'none';
            isPlayerMinimized = false;
            sticky.classList.remove('minimized');
            document.getElementById('minBtn').innerText = '▼';
            document.getElementById('queueDrawer').style.display = 'none';
        }

        async function openLocal(path, action) {
            const t = i18n[currentLang];
            try {
                await fetch('/api/open_local', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ path, action })
                });
                showToast(action === 'vlc' ? t.toastVlc : t.toastFolder, "info");
            } catch (e) {
                showToast("Action error", "warning");
            }
        }

        function playInVlcCurrent() {
            if (currentPlayingPath) {
                openLocal(currentPlayingPath, 'vlc');
            }
        }

        let urlLookupTimer = null;
        function onUrlInputChanged() {
            const val = document.getElementById('urlInput').value.trim();
            const pill = document.getElementById('urlPreviewPill');
            if (!val || (!val.includes('spotify.com') && !val.includes('youtu'))) {
                pill.style.display = 'none';
                return;
            }
            pill.style.display = 'flex';
            document.getElementById('urlPreviewTitle').innerText = "Resolving name on Spotify...";
            document.getElementById('urlPreviewSub').innerText = "";
            clearTimeout(urlLookupTimer);
            urlLookupTimer = setTimeout(async () => {
                try {
                    const res = await fetch('/api/lookup_url_info', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ url: val })
                    });
                    const info = await res.json();
                    if (info.title) {
                        document.getElementById('urlPreviewTitle').innerText = info.title;
                        document.getElementById('urlPreviewSub').innerText = info.expected_count ? `(${info.expected_count} tracks)` : '';
                        document.getElementById('urlPreviewType').innerText = info.type || 'Spotify';
                    } else {
                        document.getElementById('urlPreviewTitle').innerText = "Ready to download";
                    }
                } catch (e) {
                    pill.style.display = 'none';
                }
            }, 300);
        }

        async function submitDownload() {
            const urlInput = document.getElementById('urlInput');
            const url = urlInput.value.trim();
            const mode = document.getElementById('modeSelect').value;
            const autoRetry = document.getElementById('autoRetryCheck').checked;
            const t = i18n[currentLang];

            if (!url) {
                showToast(t.toastEmpty, "warning");
                return;
            }

            if ("Notification" in window && Notification.permission === "default") {
                Notification.requestPermission();
            }

            try {
                const res = await fetch('/api/download', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url, mode, auto_retry: autoRetry })
                });
                const data = await res.json();
                
                if (data.duplicate) {
                    const msg = currentLang === 'es' ? data.message_es : data.message_en;
                    showToast(msg, "warning");
                } else if (data.success) {
                    showToast(t.toastSuccess, "success");
                    urlInput.value = '';
                    const pill = document.getElementById('urlPreviewPill');
                    if (pill) pill.style.display = 'none';
                    fetchStatus();
                }
            } catch (e) {
                showToast("Error sending download request.", "warning");
            }
        }

        async function clearCompleted() {
            await fetch('/api/clear_completed', { method: 'POST' });
            fetchStatus();
        }

        async function togglePauseQueue() {
            try {
                const res = await fetch('/api/toggle_pause_queue', { method: 'POST' });
                const data = await res.json();
                if (data.is_queue_paused) {
                    showToast("⏸️ Download queue PAUSED.", "warning");
                } else {
                    showToast("▶️ Download queue RESUMED.", "success");
                }
                fetchStatus();
            } catch (e) {
                showToast("Error toggling queue state", "warning");
            }
        }

        async function restartIncomplete() {
            const t = i18n[currentLang];
            const res = await fetch('/api/restart_incomplete_jobs', { method: 'POST' });
            const data = await res.json();
            showToast(t.toastRestartAll, "success");
            fetchStatus();
        }

        // Close modal on click outside
        window.onclick = function(event) {
            const modal = document.getElementById('triageModal');
            if (event.target === modal) {
                closeTriageModal();
            }
        };

        loadSettingsView();
        setTheme(currentTheme);
        setLanguage(currentLang);
        switchMainView(currentMainView);
        if (currentMainView === 'explorer') {
            setExplorerSubTab(currentExplorerSubTab);
        }

        window.addEventListener('hashchange', () => {
            const h = (window.location.hash || '').replace('#', '');
            if (validViews.includes(h) && h !== currentMainView) {
                switchMainView(h);
            }
        });

        setInterval(fetchStatus, 3000);
        fetchStatus();
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    with ReusableThreadingServer(("", PORT), MediaHandler) as httpd:
        print(f"🚀 Media Studio Web Server running on http://localhost:{PORT}")
        httpd.serve_forever()
