#!/usr/bin/env python3
"""
BDD / TDD Full API Contract & Endpoint Test Suite for Media Studio
Comprehensive coverage for:
- /api/stream (Streaming, byte headers, MIME negotiation, error sandboxing)
- /api/cover (Cover retrieval, SVG fallback)
- /api/update_metadata (ID3 tag updates, file renaming & reorganization)
- /api/delete_track (Safe track deletion, directory cleanup, cache eviction)
- /api/job_control (Pause, resume, restart, cancel)
- /api/delete_job (Queue job removal)
- /api/clear_completed, /api/delete_history_item, /api/clear_history, /api/redownload_history_item
- /api/get_playlist_tracks (M3U parsing and track metadata)
- /api/clean_duplicates_auto (Duplicate cleanup execution)
"""

import unittest
import urllib.request
import urllib.parse
import json
import os
import sys
import shutil
import tempfile
import threading
import time
import socketserver
import contextlib

# Dynamically resolve media_server
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

import media_server

TEST_PORT = 8911
SERVER_URL = f"http://127.0.0.1:{TEST_PORT}"

class ReusableServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

class TestApiEndpointsBDD(unittest.TestCase):
    """BDD Specifications for Media Studio HTTP Endpoints and Handlers."""

    @classmethod
    def setUpClass(cls):
        # Create dedicated isolated temp test sandbox
        cls.test_dir = tempfile.mkdtemp(prefix="media_studio_test_sandbox_")
        cls.music_dir = os.path.join(cls.test_dir, "Music")
        cls.data_dir = os.path.join(cls.test_dir, "data")
        os.makedirs(cls.music_dir, exist_ok=True)
        os.makedirs(cls.data_dir, exist_ok=True)

        # Patch media_server storage paths for complete test isolation
        cls.orig_data_dir = media_server.DATA_DIR
        cls.orig_queue_file = media_server.QUEUE_FILE
        cls.orig_history_file = media_server.HISTORY_FILE
        cls.orig_settings_file = media_server.SETTINGS_FILE
        cls.orig_covers_dir = media_server.COVERS_DIR

        media_server.DATA_DIR = cls.data_dir
        media_server.QUEUE_FILE = os.path.join(cls.data_dir, "queue.json")
        media_server.HISTORY_FILE = os.path.join(cls.data_dir, "history.json")
        media_server.SETTINGS_FILE = os.path.join(cls.data_dir, "settings.json")
        media_server.COVERS_DIR = os.path.join(cls.data_dir, "covers")
        os.makedirs(media_server.COVERS_DIR, exist_ok=True)

        # Initialize mock settings with our test download_dir
        media_server.save_settings({"download_dir": cls.music_dir, "notifications_enabled": False})

        # Start live test HTTP server
        def run_test_server():
            with contextlib.suppress(Exception):
                server_address = ("127.0.0.1", TEST_PORT)
                cls.httpd = ReusableServer(server_address, media_server.MediaHandler)
                cls.httpd.serve_forever()

        cls.server_thread = threading.Thread(target=run_test_server, daemon=True)
        cls.server_thread.start()

        # Wait up to 5 seconds for port to bind
        for _ in range(50):
            try:
                with urllib.request.urlopen(f"{SERVER_URL}/api/status", timeout=1):
                    break
            except Exception:
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "httpd"):
            with contextlib.suppress(Exception):
                cls.httpd.shutdown()
                cls.httpd.server_close()

        # Restore media_server globals
        media_server.DATA_DIR = cls.orig_data_dir
        media_server.QUEUE_FILE = cls.orig_queue_file
        media_server.HISTORY_FILE = cls.orig_history_file
        media_server.SETTINGS_FILE = cls.orig_settings_file
        media_server.COVERS_DIR = cls.orig_covers_dir

        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def _get(self, endpoint):
        req = urllib.request.Request(f"{SERVER_URL}{endpoint}", headers={"User-Agent": "BDD-Tester/1.0"})
        with urllib.request.urlopen(req, timeout=5) as res:
            return res.status, res.headers, res.read()

    def _post(self, endpoint, payload):
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{SERVER_URL}{endpoint}",
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "BDD-Tester/1.0"}
        )
        with urllib.request.urlopen(req, timeout=5) as res:
            return res.status, res.headers, json.loads(res.read().decode("utf-8"))

    # -------------------------------------------------------------------------
    # Feature 1: Streaming & Cover Serving
    # -------------------------------------------------------------------------
    def test_given_media_file_when_streaming_then_serves_valid_audio_response(self):
        """
        Scenario: Stream legitimate audio track with byte range headers
        Given a valid dummy audio file in the music directory
        When requesting /api/stream?path=<path>
        Then status is 200, Content-Type is audio/mpeg, and length matches file size
        """
        test_file = os.path.join(self.music_dir, "test_stream.mp3")
        with open(test_file, "wb") as f:
            f.write(b"ID3\x03\x00\x00\x00\x00\x00\x00MockMP3DataForStreaming1234567890")

        status, headers, body = self._get(f"/api/stream?path={urllib.parse.quote(test_file)}")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "audio/mpeg")
        self.assertEqual(headers.get("Accept-Ranges"), "bytes")
        self.assertEqual(int(headers.get("Content-Length")), len(body))

    def test_given_invalid_stream_path_when_requested_then_returns_404(self):
        """
        Scenario: Return 404 for nonexistent files or path traversal attempts
        """
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/api/stream?path=/nonexistent/file.mp3")
        self.assertEqual(ctx.exception.code, 404)

        with self.assertRaises(urllib.error.HTTPError) as ctx2:
            self._get("/api/stream?path=../../../../etc/passwd")
        self.assertEqual(ctx2.exception.code, 404)

    def test_given_track_when_cover_requested_then_returns_valid_cover_or_svg(self):
        """
        Scenario: Return cover or SVG fallback when album cover is requested
        """
        test_file = os.path.join(self.music_dir, "no_cover.mp3")
        with open(test_file, "wb") as f:
            f.write(b"DummyAudioWithoutAPIC")

        status, headers, body = self._get(f"/api/cover?path={urllib.parse.quote(test_file)}")
        self.assertEqual(status, 200)
        self.assertIn(headers.get("Content-Type", ""), ["image/jpeg", "image/png", "image/svg+xml"])
        self.assertGreater(len(body), 0)

    # -------------------------------------------------------------------------
    # Feature 2: ID3 Metadata Update & Track Deletion
    # -------------------------------------------------------------------------
    def test_given_audio_track_when_metadata_updated_then_saved_and_organized(self):
        """
        Scenario: Update track tags and organize file into Artist/Album directory structure
        Given a media file in the library
        When /api/update_metadata is called with new artist, album, and title
        Then ID3 tags are updated and file is moved to organized directory
        """
        test_file = os.path.join(self.music_dir, "raw_sample.mp3")
        import subprocess
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "1", "-c:a", "libmp3lame", test_file],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

        payload = {
            "filepath": test_file,
            "artist": "BDD Artist",
            "album": "BDD Album",
            "title": "BDD Song",
            "track_number": "03",
            "year": "2026"
        }
        status, _, data = self._post("/api/update_metadata", payload)
        self.assertEqual(status, 200)
        self.assertTrue(data.get("success"), f"Metadata update failed: {data}")

        new_path = data.get("new_path")
        self.assertTrue(os.path.exists(new_path))
        self.assertIn("BDD Artist", new_path)
        self.assertIn("BDD Album", new_path)

        # Now test deleting the track
        del_status, _, del_data = self._post("/api/delete_track", {"filepath": new_path})
        self.assertEqual(del_status, 200)
        self.assertTrue(del_data.get("success"))
        self.assertFalse(os.path.exists(new_path))

    # -------------------------------------------------------------------------
    # Feature 3: Job Queue Lifecycle (Pause, Resume, Restart, Cancel, Delete)
    # -------------------------------------------------------------------------
    def test_given_active_job_when_controlled_then_state_transitions_correctly(self):
        """
        Scenario: Execute pause, resume, restart, cancel, and delete on queue jobs
        """
        job_id = "bdd_test_job_101"
        job = {
            "id": job_id,
            "url": "https://example.com/track/101",
            "title": "BDD Job Test",
            "status": "queued",
            "auto_retry": True,
            "added_at": "2026-08-29 12:00:00"
        }
        with media_server.job_lock:
            q = media_server.get_queue()
            q.append(job)
            media_server.save_queue(q)

        # 1. Pause job
        _, _, res_pause = self._post("/api/job_control", {"id": job_id, "action": "pause"})
        self.assertTrue(res_pause["success"])
        q = media_server.get_queue()
        target = next((j for j in q if j["id"] == job_id), None)
        self.assertEqual(target["status"], "paused")

        # 2. Resume job
        _, _, res_resume = self._post("/api/job_control", {"id": job_id, "action": "resume"})
        self.assertTrue(res_resume["success"])
        q = media_server.get_queue()
        target = next((j for j in q if j["id"] == job_id), None)
        self.assertEqual(target["status"], "queued")

        # 3. Cancel job
        _, _, res_cancel = self._post("/api/job_control", {"id": job_id, "action": "cancel"})
        self.assertTrue(res_cancel["success"])
        q = media_server.get_queue()
        target = next((j for j in q if j["id"] == job_id), None)
        self.assertEqual(target["status"], "cancelled")

        # 4. Restart job
        _, _, res_restart = self._post("/api/job_control", {"id": job_id, "action": "restart"})
        self.assertTrue(res_restart["success"])
        q = media_server.get_queue()
        target = next((j for j in q if j["id"] == job_id), None)
        self.assertEqual(target["status"], "queued")

        # 5. Delete job from queue
        _, _, res_del = self._post("/api/delete_job", {"id": job_id})
        self.assertTrue(res_del["success"])
        q = media_server.get_queue()
        self.assertFalse(any(j["id"] == job_id for j in q))

    # -------------------------------------------------------------------------
    # Feature 4: History Management (Redownload, Delete, Clear)
    # -------------------------------------------------------------------------
    def test_given_history_items_when_manipulated_then_updates_accurately(self):
        """
        Scenario: Re-download from history, delete specific history item, and clear history
        """
        hist_id = "bdd_hist_item_202"
        hist_item = {
            "id": hist_id,
            "url": "https://example.com/song/202",
            "title": "History Song Test",
            "status": "completed",
            "completed_at": "2026-08-29 12:15:00"
        }
        with open(media_server.HISTORY_FILE, "w") as f:
            json.dump([hist_item], f)

        # 1. Redownload history item
        _, _, redown_res = self._post("/api/redownload_history_item", {"url": hist_item["url"], "title": hist_item["title"]})
        self.assertTrue(redown_res["success"])
        q = media_server.get_queue()
        self.assertTrue(any(j.get("url") == hist_item["url"] for j in q))

        # 2. Delete history item
        _, _, del_res = self._post("/api/delete_history_item", {"id": hist_id})
        self.assertTrue(del_res["success"])
        hist = media_server.get_history()
        self.assertFalse(any(h.get("id") == hist_id for h in hist))

        # 3. Clear completed and clear history
        _, _, clr_comp = self._post("/api/clear_completed", {})
        self.assertTrue(clr_comp["success"])

        _, _, clr_hist = self._post("/api/clear_history", {})
        self.assertTrue(clr_hist["success"])
        self.assertEqual(len(media_server.get_history()), 0)

    # -------------------------------------------------------------------------
    # Feature 5: Playlist M3U Track Parsing
    # -------------------------------------------------------------------------
    def test_given_m3u_playlist_when_queried_then_returns_parsed_track_list(self):
        """
        Scenario: Parse M3U playlist file into structured track objects
        Given an M3U8 file referencing media tracks
        When /api/get_playlist_tracks is called
        Then returns structured list containing file names and paths
        """
        dummy_track = os.path.join(self.music_dir, "TrackA.mp3")
        with open(dummy_track, "wb") as f:
            f.write(b"AudioDataA")

        m3u_file = os.path.join(self.music_dir, "TestPlaylist.m3u8")
        with open(m3u_file, "w", encoding="utf-8") as f:
            f.write(f"#EXTM3U\n#EXTINF:180,Artist - Track A\n{dummy_track}\n")

        status, _, body = self._get(f"/api/get_playlist_tracks?path={urllib.parse.quote(m3u_file)}")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        tracks = data.get("tracks", [])
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0]["name"], "TrackA.mp3")

    # -------------------------------------------------------------------------
    # Feature 6: Auto-Deduplication Execution
    # -------------------------------------------------------------------------
    def test_given_duplicate_tracks_when_cleaned_then_reclaims_disk_space(self):
        """
        Scenario: Find duplicate tracks and auto-clean redundant copies
        Given duplicate audio files with identical acoustic title keys
        When /api/clean_duplicates_auto is called
        Then redundant copy is removed and freed megabytes reported
        """
        dup_dir = os.path.join(self.music_dir, "TestDups")
        os.makedirs(dup_dir, exist_ok=True)
        file1 = os.path.join(dup_dir, "Song - Track.mp3")
        file2 = os.path.join(dup_dir, "Song - Track (Copy).mp3")

        with open(file1, "wb") as f:
            f.write(b"OriginalQualityAudioData" * 1000)
        with open(file2, "wb") as f:
            f.write(b"DuplicateCopyAudioData" * 500)

        # Invalidate cache to force scan
        media_server.invalidate_library_cache()

        status, _, data = self._post("/api/clean_duplicates_auto", {})
        self.assertEqual(status, 200)
        self.assertTrue(data.get("success"))
        self.assertIn("deleted_count", data)

    # -------------------------------------------------------------------------
    # Feature 7: Download Enqueuing & Advanced Job Orchestration
    # -------------------------------------------------------------------------
    def test_given_media_url_when_download_requested_then_job_enqueued(self):
        """
        Scenario: Enqueue new download job via /api/download
        """
        payload = {
            "url": "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT",
            "mode": "audio"
        }
        status, _, res = self._post("/api/download", payload)
        self.assertEqual(status, 200)
        self.assertTrue(res.get("success"))
        self.assertIn("job_id", res)

    def test_given_incomplete_jobs_when_restarted_then_status_reset_to_queued(self):
        """
        Scenario: Restart incomplete or cancelled download jobs
        """
        with media_server.job_lock:
            q = media_server.get_queue()
            q.append({"id": "inc_1", "url": "https://example.com/inc1", "status": "failed", "auto_retry": False})
            q.append({"id": "inc_2", "url": "https://example.com/inc2", "status": "cancelled", "auto_retry": False})
            media_server.save_queue(q)

        status, _, res = self._post("/api/restart_incomplete_jobs", {})
        self.assertEqual(status, 200)
        self.assertTrue(res.get("success"))
        self.assertGreaterEqual(res.get("restarted", 0), 2)

    def test_given_single_track_query_when_retried_then_job_prepended(self):
        """
        Scenario: Retry single track query directly
        """
        status, _, res = self._post("/api/retry_single_track", {"query": "Daft Punk - One More Time"})
        self.assertEqual(status, 200)
        self.assertTrue(res.get("success"))

    def test_given_missing_tracks_when_redownload_requested_then_jobs_created(self):
        """
        Scenario: Batch redownload missing tracks from a playlist
        """
        payload = {
            "url": "https://example.com/playlist/test",
            "tracks": [
                {"title": "Track 1", "artist": "Artist A", "track_url": "https://example.com/t1"},
                {"title": "Track 2", "artist": "Artist B", "track_url": "https://example.com/t2"}
            ]
        }
        status, _, res = self._post("/api/redownload_missing_tracks", payload)
        self.assertEqual(status, 200)
        self.assertTrue(res.get("success"))
        self.assertEqual(res.get("queued_count"), 2)

    def test_given_query_when_metadata_searched_then_returns_candidates(self):
        """
        Scenario: Search official metadata candidates online via /api/search_metadata
        """
        status, _, res = self._post("/api/search_metadata", {"query": "Imagine Dragons Bones"})
        self.assertEqual(status, 200)
        self.assertIn("results", res)

    def test_given_local_track_when_open_local_invoked_then_executes_safely(self):
        """
        Scenario: Launch local player on safe path with action=vlc
        """
        test_file = os.path.join(self.music_dir, "local_test.mp3")
        with open(test_file, "wb") as f:
            f.write(b"LocalMockData")

        status, _, res = self._post("/api/open_local", {"path": test_file, "action": "vlc"})
        self.assertEqual(status, 200)
        self.assertTrue(res.get("success"))

        # Test path traversal prevention on /api/open_local returns 403
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._post("/api/open_local", {"path": "/etc/shadow", "action": "vlc"})
        self.assertEqual(ctx.exception.code, 403)

    def test_given_notification_request_when_triggered_then_succeeds(self):
        """
        Scenario: Test desktop notification endpoint /api/test_notification
        """
        status, _, res = self._post("/api/test_notification", {})
        self.assertEqual(status, 200)
        self.assertTrue(res.get("success"))

if __name__ == "__main__":
    unittest.main()

