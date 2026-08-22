import unittest
import urllib.request
import urllib.parse
import json
import os
import threading
import time
import re

PORT = 8888
BASE_URL = f"http://127.0.0.1:{PORT}"

class TestMediaServerUnits(unittest.TestCase):
    def test_normalize_url(self):
        def normalize_url(u):
            if not u: return ""
            return u.strip().split("?")[0].rstrip("/")
        
        self.assertEqual(normalize_url("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=12345"), "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M")
        self.assertEqual(normalize_url("https://youtube.com/watch?v=dQw4w9WgXcQ&list=123"), "https://youtube.com/watch")
        self.assertEqual(normalize_url(""), "")

    def test_clean_alphanumeric_key(self):
        def clean_alphanumeric_key(s):
            if not s: return ''
            s = re.sub(r'\(.*?\)|\[.*?\]', '', s)
            s = re.sub(r'\b(feat|ft|with|and|single|edit|version|remix|official|audio|video|album|ep|instrumental)\b', '', s, flags=re.IGNORECASE)
            s = re.sub(r'[^a-zA-Z0-9]', '', s)
            return s.lower()

        self.assertEqual(clean_alphanumeric_key("Song Title (Official Video) [320kbps]"), "songtitle")
        self.assertEqual(clean_alphanumeric_key("Artist ft. Other - Track (Remix)"), "artistothertrack")

    def test_history_analytics_math(self):
        def calc_analytics(hist, lib_count):
            total_jobs = len(hist)
            total_expected = sum(h.get("expected_count", 1) for h in hist)
            avg_success = round((lib_count / total_expected * 100), 1) if total_expected > 0 else 100.0
            if avg_success > 100.0: avg_success = 100.0
            return avg_success

        hist = [{"expected_count": 50}, {"expected_count": 20}]
        self.assertEqual(calc_analytics(hist, 35), 50.0)
        self.assertEqual(calc_analytics(hist, 70), 100.0)
        self.assertEqual(calc_analytics(hist, 100), 100.0)

    def test_job_lock_concurrency(self):
        """Verify thread-safety of queue manipulation under lock."""
        lock = threading.Lock()
        shared_list = []
        
        def worker(idx):
            with lock:
                curr = list(shared_list)
                curr.append(idx)
                time.sleep(0.001)
                shared_list.clear()
                shared_list.extend(curr)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads: t.start()
        for t in threads: t.join()

        self.assertEqual(len(shared_list), 10)

class TestMediaServerAPIIntegration(unittest.TestCase):
    def _get(self, path):
        req = urllib.request.Request(f"{BASE_URL}{path}")
        with urllib.request.urlopen(req, timeout=5) as res:
            return json.loads(res.read().decode())

    def _post(self, path, payload):
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(f"{BASE_URL}{path}", data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as res:
            return json.loads(res.read().decode())

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
        payload = {"url": "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"}
        data = self._post("/api/analyze_job_tracks", payload)
        self.assertIn("total_expected", data)
        self.assertIn("total_downloaded", data)
        self.assertIn("total_missing", data)
        self.assertIn("completion_pct", data)
        self.assertEqual(data["total_expected"], 50)
        self.assertEqual(data["total_downloaded"] + data["total_missing"], data["total_expected"])

    def test_api_duplicates_endpoint(self):
        data = self._get("/api/duplicates")
        self.assertIn("groups", data)
        self.assertIn("total_groups", data)
        self.assertIn("total_wasted_mb", data)
        self.assertIsInstance(data["groups"], list)

    def test_api_lookup_url_info(self):
        res = self._post("/api/lookup_url_info", {"url": "https://open.spotify.com/track/60zkEkKVPuuIis9HeHOmlI"})
        self.assertIn(res.get("type"), ["Song", "track", "Album", "Playlist"])
        self.assertTrue(bool(res.get("title")))

    def test_api_settings_contract(self):
        data = self._get("/api/settings")
        self.assertTrue(data["success"])
        self.assertIn("settings", data)
        s = data["settings"]
        self.assertIn("download_dir", s)
        self.assertIn("notifications_enabled", s)
        self.assertIn("default_bitrate", s)

        payload = {"settings": {"default_bitrate": "256k", "notifications_enabled": False}}
        res = self._post("/api/settings", payload)
        self.assertTrue(res["success"])
        self.assertEqual(res["settings"]["default_bitrate"], "256k")
        self.assertEqual(res["settings"]["notifications_enabled"], False)

        payload_restore = {"settings": {"default_bitrate": "320k", "notifications_enabled": True}}
        res_restored = self._post("/api/settings", payload_restore)
        self.assertTrue(res_restored["success"])
        self.assertEqual(res_restored["settings"]["default_bitrate"], "320k")
        self.assertEqual(res_restored["settings"]["notifications_enabled"], True)

if __name__ == "__main__":
    unittest.main(verbosity=2)
