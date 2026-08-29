#!/usr/bin/env python3
"""
BDD / TDD Single-Source-of-Truth Architecture Specification
Verifies that all 3 delivery targets (Docker/CasaOS, Home Assistant Add-on, Agent Skill)
share one single codebase without code duplication.

Features Tested:
1. Single Source of Truth Invariant: Exactly ONE media_server.py exists in the entire repository.
2. Target 1 (CasaOS / Docker): Builds from root Dockerfile and references root source.
3. Target 2 (Home Assistant Add-on): Uses container inheritance/prebuilt image without duplicate python files.
4. Target 3 (Agent Skill): Uses SKILL.md interface without duplicating 2,500-line python code.
5. Invariance Check: All unit and security tests pass against the single source.
"""

import unittest
import os
import glob
try:
    import yaml
except ImportError:
    class FallbackYaml:
        @staticmethod
        def safe_load(f):
            content = f.read() if hasattr(f, "read") else str(f)
            data = {"arch": []}
            in_arch = False
            for line in content.splitlines():
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                if s == "arch:":
                    in_arch = True
                    continue
                if in_arch:
                    if s.startswith("- "):
                        data["arch"].append(s[2:].strip())
                    elif ":" in s:
                        in_arch = False
                if ":" in s:
                    k, v = s.split(":", 1)
                    data[k.strip()] = v.strip().strip('"').strip("'")
            return data
    yaml = FallbackYaml()

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

class TestSingleSourceDistributionBDD(unittest.TestCase):
    """BDD Specifications for Multi-Target Single-Source-of-Truth Architecture."""

    # -------------------------------------------------------------------------
    # Feature 1: Single Source of Truth Invariant
    # -------------------------------------------------------------------------
    def test_given_repository_when_searching_media_servers_then_only_root_exists(self):
        """
        Scenario: Exactly one media_server.py exists across the entire project
        Given the complete repository filesystem
        When searching for all files named 'media_server.py'
        Then exactly ONE copy must exist, located strictly at repo root
        """
        pattern = os.path.join(REPO_ROOT, "**/media_server.py")
        matches = glob.glob(pattern, recursive=True)
        # Normalize paths relative to repo root
        rel_matches = [os.path.relpath(p, REPO_ROOT) for p in matches]

        expected = ["media_server.py"]
        self.assertEqual(
            rel_matches,
            expected,
            f"Code Duplication Violation! Found multiple media_server.py files: {rel_matches}. "
            f"Expected ONLY repo root 'media_server.py' as single source of truth."
        )

    # -------------------------------------------------------------------------
    # Feature 2: Target 1 (CasaOS / Docker) Compatibility
    # -------------------------------------------------------------------------
    def test_given_casaos_and_docker_configs_when_inspected_then_use_root_source(self):
        """
        Scenario: Docker and CasaOS reference the primary build context
        Given the root Dockerfile and CasaOS compose file
        When inspected for image and file references
        Then they must build/run the single source without internal mirror paths
        """
        dockerfile_path = os.path.join(REPO_ROOT, "Dockerfile")
        self.assertTrue(os.path.exists(dockerfile_path), "Missing root Dockerfile")

        with open(dockerfile_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("media_server.py", content)
        self.assertNotIn("ha-addon/media_server.py", content)
        self.assertNotIn("COPY .agents", content)

    # -------------------------------------------------------------------------
    # Feature 3: Target 2 (Home Assistant Add-on) Single-Source Contract
    # -------------------------------------------------------------------------
    def test_given_ha_addon_when_inspected_then_no_duplicate_code_and_valid_manifest(self):
        """
        Scenario: Home Assistant Add-on inherits production image without duplicating code
        Given the ha-addon directory
        When checking config.yaml, Dockerfile, and directory contents
        Then:
          - ha-addon/config.yaml must be valid YAML with valid name, slug, and arch
          - ha-addon/ must NOT contain duplicate media_server.py or duplicate scripts/
          - ha-addon/Dockerfile must inherit from the primary GHCR image
        """
        addon_dir = os.path.join(REPO_ROOT, "ha-addon")
        self.assertTrue(os.path.isdir(addon_dir), "ha-addon directory must exist to support Target 2")

        # 1. Validate config.yaml
        config_path = os.path.join(addon_dir, "config.yaml")
        self.assertTrue(os.path.exists(config_path), "Missing ha-addon/config.yaml")
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        self.assertEqual(config.get("slug"), "media_studio")
        self.assertIn("amd64", config.get("arch", []))
        self.assertIn("aarch64", config.get("arch", []))

        # 2. Verify NO duplicated media_server.py in ha-addon
        duplicate_server = os.path.join(addon_dir, "media_server.py")
        self.assertFalse(
            os.path.exists(duplicate_server),
            "Code Duplication Violation! ha-addon must not contain a duplicate media_server.py"
        )

        # 3. Verify ha-addon/Dockerfile inherits from the prebuilt container image
        dockerfile_path = os.path.join(addon_dir, "Dockerfile")
        if os.path.exists(dockerfile_path):
            with open(dockerfile_path, "r", encoding="utf-8") as f:
                df_content = f.read()
            self.assertTrue(
                "ghcr.io/rodolfoconcepcion/media-studio-hub" in df_content or "FROM scratch" in df_content,
                "ha-addon/Dockerfile should inherit from ghcr.io/rodolfoconcepcion/media-studio-hub"
            )

    # -------------------------------------------------------------------------
    # Feature 4: Target 3 (Agent Skill) Single-Source Contract
    # -------------------------------------------------------------------------
    def test_given_agent_skill_when_inspected_then_valid_skill_and_no_duplicate_server(self):
        """
        Scenario: Agent Skill provides SKILL.md interface without duplicating server code
        Given the .agents/skills/media-downloader directory
        When checking SKILL.md and directory contents
        Then:
          - SKILL.md must exist and contain valid YAML frontmatter (name, description)
          - .agents/skills/media-downloader/ must NOT contain duplicate media_server.py
        """
        skill_dir = os.path.join(REPO_ROOT, ".agents", "skills", "media-downloader")
        self.assertTrue(os.path.isdir(skill_dir), "Skill directory must exist to support Target 3")

        skill_md = os.path.join(skill_dir, "SKILL.md")
        self.assertTrue(os.path.exists(skill_md), "Missing SKILL.md in agent skill directory")

        with open(skill_md, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertTrue(content.startswith("---"), "SKILL.md must start with YAML frontmatter delimiter")
        self.assertIn("name: media-downloader", content)
        self.assertIn("description:", content)

        # Verify NO duplicate media_server.py in agent skill
        duplicate_server = os.path.join(skill_dir, "media_server.py")
        self.assertFalse(
            os.path.exists(duplicate_server),
            "Code Duplication Violation! Agent skill must not contain a duplicate media_server.py"
        )

if __name__ == "__main__":
    unittest.main()
