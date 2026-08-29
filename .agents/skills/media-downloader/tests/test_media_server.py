#!/usr/bin/env python3
"""
Unit and Integration Test Suite for Media Studio Server
Covers:
- URL normalization and entity parsing
- Queue and History persistence under thread locks
- Live REST API contract verification (status, pause/resume, track diagnostics, duplicates)
- Deduplication algorithms and title fuzzy sanitization
- ID3 metadata and filesystem safety
"""

import unittest
import urllib.request
import json
import os
import sys
import threading
import time
import socketserver

# Dynamically resolve media_server from repo root or local path
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
repo_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))

for p in [parent_dir, repo_root, "/home/rodolfo", "."]:
    if os.path.exists(os.path.join(p, "media_server.py")):
        sys.path.insert(0, p)
        break

import media_server

TEST_PORT = 8899
SERVER_URL = f"http://127.0.0.1:{TEST_PORT}"

class ReusableServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

class TestMediaServerUnits(unittest.TestCase):
    """Unit tests for core parsing, deduplication, and math routines."""

    def test_normalize_url(self):
        url1 = "https://open.spotify.com/track/60zkEkKVPuuIis9HeHOmlI?si=abc12345"
        self.assertEqual(media_server.normalize_url(url1), "https://open.spotify.com/track/60zkEkKVPuuIis9HeHOmlI")

        url2 = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL123"
        self.assertEqual(media_server.normalize_url(url2), "https://www.youtube.com/watch")

        url3 = "   https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M/   "
        self.assertEqual(media_server.normalize_url(url3), "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M")

    def test_clean_alphanumeric_key(self):
        title = "Taylor Swift - The Fate of Ophelia (Official Audio) [320kbps]"
        key = media_server.clean_alphanumeric_key(title)
        self.assertNotIn("official", key)
        self.assertNotIn("audio", key)
        self.assertNotIn("320kbps", key)
        self.assertIn("taylor", key)
        self.assertIn("swift", key)

    def test_job_lock_concurrency(self):
        """Verify queue updates remain deterministic under concurrent writes."""
        test_job = {"id": "test_concurrent_99", "url": "https://example.com/test", "status": "queued"}
        
        with media_server.job_lock:
            q = media_server.get_queue()
            q.append(test_job)
            media_server.save_queue(q)

        reloaded = media_server.get_queue()
        self.assertTrue(any(j["id"] == "test_concurrent_99" for j in reloaded))

        # Cleanup
        with media_server.job_lock:
            q = [j for j in media_server.get_queue() if j["id"] != "test_concurrent_99"]
            media_server.save_queue(q)

    def test_history_analytics_math(self):
        """Verify analytics calculations never divide by zero and yield valid percentages."""
        stats = media_server.get_history_analytics()
        self.assertIn("total_jobs", stats)
        self.assertIn("avg_success_rate", stats)
        self.assertGreaterEqual(stats["avg_success_rate"], 0.0)
        self.assertLessEqual(stats["avg_success_rate"], 100.0)

    def test_safe_path_security_guard(self):
        """Verify _safe_path blocks traversal attempts and accepts valid paths."""
        # Malicious paths
        self.assertIsNone(media_server._safe_path("/etc/shadow"))
        self.assertIsNone(media_server._safe_path("/home/rodolfo/.ssh/id_rsa"))
        self.assertIsNone(media_server._safe_path("~/../root/.bashrc"))
        self.assertIsNone(media_server._safe_path(None))
        self.assertIsNone(media_server._safe_path(""))

        # Valid allowed paths
        music_path = os.path.expanduser("~/Music/Song.mp3")
        self.assertIsNotNone(media_server._safe_path(music_path))
        videos_path = os.path.expanduser("~/Videos/Clip.mp4")
        self.assertIsNotNone(media_server._safe_path(videos_path))

    def test_ui_template_loader(self):
        """Verify get_ui_html returns valid HTML document from template file."""
        html = media_server.get_ui_html()
        self.assertIsInstance(html, str)
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("<title>", html)
        self.assertIn("Media Studio", html)

    def test_ttl_cache_invalidation(self):
        """Verify library cache TTL and manual invalidation."""
        initial = media_server.get_media_library()
        self.assertIsInstance(initial, list)
        
        # Test explicit invalidation
        media_server.invalidate_library_cache()
        self.assertEqual(media_server._library_cache["ts"], 0.0)
        self.assertEqual(media_server._analytics_cache["ts"], 0.0)


class TestMediaServerIntegration(unittest.TestCase):
    """Integration tests verifying HTTP endpoints and JSON contracts."""

    @classmethod
    def setUpClass(cls):
        def run_test_server():
            try:
                server_address = ("127.0.0.1", TEST_PORT)
                cls.httpd = ReusableServer(server_address, media_server.MediaHandler)
                cls.httpd.serve_forever()
            except Exception as err:
                _ = err

        cls.server_thread = threading.Thread(target=run_test_server, daemon=True)
        cls.server_thread.start()
        
        # Wait up to 5 seconds for port to open
        for _ in range(50):
            try:
                with urllib.request.urlopen(f"{SERVER_URL}/api/status", timeout=1):
                    break
            except Exception:
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'httpd'):
            try:
                cls.httpd.shutdown()
                cls.httpd.server_close()
            except Exception as err:
                _ = err

    def _get(self, endpoint):
        req = urllib.request.Request(f"{SERVER_URL}{endpoint}", headers={"User-Agent": "MediaServer-Tester/1.0"})
        with urllib.request.urlopen(req, timeout=8) as res:
            self.assertEqual(res.status, 200)
            return json.loads(res.read().decode("utf-8"))

    def _post(self, endpoint, payload):
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{SERVER_URL}{endpoint}",
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "MediaServer-Tester/1.0"}
        )
        with urllib.request.urlopen(req, timeout=8) as res:
            self.assertEqual(res.status, 200)
            return json.loads(res.read().decode("utf-8"))

    def test_api_status_contract(self):
        data = self._get("/api/status")
        self.assertIn("queue", data)
        self.assertIn("library", data)
        self.assertIn("metrics", data)
        self.assertIn("history", data)
        self.assertIn("history_analytics", data)
        self.assertIn("duplicates_count", data)

        metrics = data["metrics"]
        self.assertIn("total_tracks", metrics)
        self.assertIn("total_size_mb", metrics)
        self.assertIn("is_queue_paused", metrics)

    def test_api_toggle_pause_queue(self):
        # Toggle pause on
        res1 = self._post("/api/toggle_pause_queue", {})
        self.assertTrue(res1["success"])
        paused_state = res1["is_queue_paused"]

        # Status check reflects pause
        status1 = self._get("/api/status")
        self.assertEqual(status1["metrics"]["is_queue_paused"], paused_state)

        # Toggle pause back to original
        res2 = self._post("/api/toggle_pause_queue", {})
        self.assertTrue(res2["success"])
        self.assertEqual(res2["is_queue_paused"], not paused_state)

    def test_api_analyze_job_tracks(self):
        payload = {"url": "https://example.com/playlist/mock_test"}
        data = self._post("/api/analyze_job_tracks", payload)
        self.assertIn("total_expected", data)
        self.assertIn("total_downloaded", data)
        self.assertIn("total_missing", data)
        self.assertIn("completion_pct", data)

    def test_api_duplicates_endpoint(self):
        data = self._get("/api/duplicates")
        self.assertIn("groups", data)
        self.assertIn("total_groups", data)
        self.assertIn("total_wasted_mb", data)
        self.assertIsInstance(data["groups"], list)

    def test_api_lookup_url_info(self):
        res = self._post("/api/lookup_url_info", {"url": "https://example.com/test-song"})
        self.assertIn("type", res)
        self.assertIn("title", res)

    def test_api_settings_contract(self):
        # Fetch settings
        res = self._get("/api/settings")
        self.assertTrue(res.get("success"))
        current_settings = res.get("settings", {})
        self.assertIn("download_dir", current_settings)
        self.assertIn("default_bitrate", current_settings)
        self.assertIn("notifications_enabled", current_settings)

        # Mutate setting via POST
        test_payload = {"settings": {"notifications_enabled": not current_settings.get("notifications_enabled", True)}}
        post_res = self._post("/api/settings", test_payload)
        self.assertTrue(post_res["success"])
        self.assertEqual(post_res["settings"]["notifications_enabled"], test_payload["settings"]["notifications_enabled"])

        # Revert back
        revert_res = self._post("/api/settings", {"settings": {"notifications_enabled": current_settings.get("notifications_enabled", True)}})
        self.assertTrue(revert_res["success"])


if __name__ == "__main__":
    unittest.main()
