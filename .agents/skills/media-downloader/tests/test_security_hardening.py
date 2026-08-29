#!/usr/bin/env python3
"""
BDD / TDD Security Hardening Test Suite for Media Studio
Validates defense-in-depth against:
- Path Injection & Traversal (CWE-22 / CWE-23 / CWE-73)
- Incomplete URL Substring Sanitization (CWE-20)
- Server-Side Request Forgery / SSRF (CWE-918)
- Command-Line Injection (CWE-78 / CWE-88)
- Functional Invariance (Zero breaking regressions for legitimate media workflows)
"""

import unittest
import os
import sys
import urllib.parse

# Ensure media_server is importable
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

import media_server

class TestSecurityHardeningBDD(unittest.TestCase):
    """
    BDD Specification: Security Controls & Invariant Protections
    """

    # -------------------------------------------------------------------------
    # Feature: Path Traversal & Injection Prevention
    # -------------------------------------------------------------------------
    def test_given_malicious_paths_when_validated_then_rejected(self):
        """
        Scenario: Rejecting Directory Traversal & System File Access
        Given untrusted path inputs from API requests or client payloads
        When checked against _safe_path
        Then all out-of-sandbox and traversal attempts MUST resolve to None
        """
        malicious_vectors = [
            "/etc/passwd",
            "/etc/shadow",
            "../../../../etc/passwd",
            os.path.expanduser("~/.ssh/id_rsa"),
            os.path.expanduser("~/.bashrc"),
            os.path.expanduser("~/Music/../../.ssh/id_rsa"),
            "/DATA/Media/Music/../../../../etc/passwd",
            "C:\\Windows\\System32\\cmd.exe",
            "\\\\attacker\\share\\payload.mp3",
            None,
            "",
            "   ",
            "../" * 10 + "etc/passwd"
        ]
        for vector in malicious_vectors:
            with self.subTest(vector=vector):
                safe = media_server._safe_path(vector)
                self.assertIsNone(safe, f"Security Breach: Path {vector} was not blocked!")

    def test_given_valid_library_paths_when_validated_then_allowed(self):
        """
        Scenario: Allowing Legitimate In-Sandbox Media Paths
        Given valid audio file paths located inside allowed root directories (~/Music, AppData)
        When checked against _safe_path
        Then the canonical normalized path MUST be returned safely
        """
        allowed_roots = [
            os.path.expanduser("~/Music/Artist/Album/01 - Track.mp3"),
            os.path.expanduser("~/.agents/media_downloader/covers/album_art.jpg"),
            os.path.expanduser("~/Music/_PLAYLISTS_/Favorites.m3u")
        ]
        for path in allowed_roots:
            with self.subTest(path=path):
                safe = media_server._safe_path(path)
                self.assertIsNotNone(safe, f"Regression: Valid media path {path} was blocked!")
                self.assertTrue(os.path.isabs(safe))

    # -------------------------------------------------------------------------
    # Feature: Incomplete URL Substring Sanitization Prevention
    # -------------------------------------------------------------------------
    def test_given_spoofed_or_attacker_urls_when_checked_then_rejected(self):
        """
        Scenario: Preventing Domain Spoofing & Substring Bypass
        Given attacker-crafted URLs containing 'spotify.com' or 'youtube.com' as subdomains/paths
        When checked by safe domain validators
        Then spoofed hosts MUST be identified as untrusted
        """
        spoofed_urls = [
            "https://spotify.com.attacker.com/track/123",
            "https://evil-spotify.com/track/123",
            "https://attacker.org/open.spotify.com/track/123",
            "https://youtube.com.attacker.com/watch?v=123",
            "https://not-youtube.com/watch?v=123",
            "https://attacker.com/fake?url=https://youtube.com",
            "http://192.168.0.1/spotify.com",
            "javascript:alert(1)//https://open.spotify.com",
            "file:///etc/passwd#https://youtube.com"
        ]
        for url in spoofed_urls:
            with self.subTest(url=url):
                # Verify that hostname parsing strictly rejects these spoofed domains
                parsed = urllib.parse.urlparse(url)
                is_valid = media_server.is_valid_media_service_url(url)
                self.assertFalse(is_valid, f"Security Breach: Spoofed URL was accepted: {url}")

    def test_given_legitimate_platform_urls_when_checked_then_accepted(self):
        """
        Scenario: Permitting Real Media Platform URLs
        Given authentic Spotify, YouTube, SoundCloud, or Bandcamp URLs
        When checked by is_valid_media_service_url
        Then all valid URLs MUST be recognized as trusted
        """
        valid_urls = [
            "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT",
            "https://open.spotify.com/album/1DFixLWuPkv3KT3TnV35m3",
            "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ",
            "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://soundcloud.com/artist/track-name",
            "https://bandcamp.com/track/example"
        ]
        for url in valid_urls:
            with self.subTest(url=url):
                self.assertTrue(media_server.is_valid_media_service_url(url), f"Regression: Valid URL rejected: {url}")

    # -------------------------------------------------------------------------
    # Feature: SSRF (Server-Side Request Forgery) Prevention
    # -------------------------------------------------------------------------
    def test_given_internal_or_malicious_ssrf_urls_when_fetching_cover_then_blocked(self):
        """
        Scenario: Blocking SSRF to Localhost, Private LAN, and Cloud Metadata
        Given an external cover art URL pointing to loopback, private IPs, or non-HTTP schemes
        When checked against is_safe_remote_resource_url
        Then the request MUST be strictly rejected
        """
        ssrf_targets = [
            "http://127.0.0.1:8080/admin",
            "http://localhost:8123/api",
            "http://192.168.0.1/status",
            "http://192.168.0.25/v1/users",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/secret",
            "file:///etc/passwd",
            "gopher://127.0.0.1:6379/_INFO",
            "ftp://ftp.local/test.jpg",
            "http://10.0.0.1/router",
            "http://172.16.0.1/private"
        ]
        for url in ssrf_targets:
            with self.subTest(url=url):
                self.assertFalse(
                    media_server.is_safe_remote_resource_url(url),
                    f"SSRF Vulnerability: Malicious internal URL permitted: {url}"
                )

    def test_given_legitimate_cdn_cover_urls_when_fetching_cover_then_allowed(self):
        """
        Scenario: Permitting Trusted Cover Art CDNs
        Given verified Apple Music, Spotify, or YouTube image CDNs
        When checked against is_safe_remote_resource_url
        Then legitimate HTTPS image URLs MUST be allowed
        """
        allowed_cdns = [
            "https://is1-ssl.mzstatic.com/image/thumb/Music112/v4/cover.jpg",
            "https://is2-ssl.mzstatic.com/image/thumb/Music122/v4/album.jpg",
            "https://i.scdn.co/image/ab67616d0000b273e8b066f70c206551210d902b",
            "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg",
            "https://itunes.apple.com/search?term=test&media=music"
        ]
        for url in allowed_cdns:
            with self.subTest(url=url):
                self.assertTrue(
                    media_server.is_safe_remote_resource_url(url),
                    f"Regression: Valid CDN image URL rejected: {url}"
                )

    # -------------------------------------------------------------------------
    # Feature: Command-Line Injection Prevention
    # -------------------------------------------------------------------------
    def test_given_shell_metacharacters_in_job_url_when_validated_then_rejected(self):
        """
        Scenario: Blocking Shell Injection Payload Characters
        Given a job submission containing command chaining operators or shell metacharacters
        When sanitized before subprocess invocation
        Then unsafe commands MUST be rejected
        """
        injection_urls = [
            "https://youtube.com/watch?v=123; rm -rf /",
            "https://youtube.com/watch?v=123 && touch /tmp/pwned",
            "https://youtube.com/watch?v=123 | cat /etc/passwd",
            "https://youtube.com/watch?v=123`whoami`",
            "https://youtube.com/watch?v=$(id)",
            "https://youtube.com/watch?v=123\nid",
            "https://youtube.com/watch?v=123\r\nrm -rf *"
        ]
        for url in injection_urls:
            with self.subTest(url=url):
                self.assertFalse(
                    media_server.is_valid_media_service_url(url),
                    f"Command Injection Vector Accepted: {url}"
                )

if __name__ == "__main__":
    unittest.main()
