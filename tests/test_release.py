"""Integration tests for src/release.py."""

import os
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from conftest import (
    setup_test_repo, add_test_commit, create_test_tag,
    push_test_commits, install_git_mock, uninstall_git_mock,
)
from lib.config import _ENV_SNAPSHOT
from release import parse_args, main as release_main


class TestParseArgs(unittest.TestCase):
    """Test release.py argument parsing."""

    def test_help(self):
        with self.assertRaises(SystemExit) as ctx:
            parse_args(["--help"])
        self.assertEqual(ctx.exception.code, 0)

    def test_dry_run(self):
        args = parse_args(["--dry-run"])
        self.assertTrue(args["dry_run"])

    def test_version(self):
        args = parse_args(["--version", "1.2.3"])
        self.assertEqual(args["cli_version"], "1.2.3")

    def test_version_missing_arg(self):
        with self.assertRaises(SystemExit):
            parse_args(["--version"])

    def test_description(self):
        args = parse_args(["--description", "Adds widget support"])
        self.assertEqual(args["description"], "Adds widget support")

    def test_description_missing_arg(self):
        with self.assertRaises(SystemExit):
            parse_args(["--description"])

    def test_config(self):
        args = parse_args(["--config", "/path/to/config"])
        self.assertEqual(args["config_file"], "/path/to/config")

    def test_config_missing_arg(self):
        with self.assertRaises(SystemExit):
            parse_args(["--config"])

    def test_non_interactive(self):
        args = parse_args(["--non-interactive"])
        self.assertTrue(args["non_interactive"])

    def test_non_interactive_short(self):
        args = parse_args(["-n"])
        self.assertTrue(args["non_interactive"])

    def test_unknown_option(self):
        with self.assertRaises(SystemExit):
            parse_args(["--unknown"])

    def test_combined_flags(self):
        args = parse_args(["--dry-run", "--version", "1.0.0", "-n",
                           "--description", "my release"])
        self.assertTrue(args["dry_run"])
        self.assertEqual(args["cli_version"], "1.0.0")
        self.assertTrue(args["non_interactive"])
        self.assertEqual(args["description"], "my release")


class TestReleaseIntegration(unittest.TestCase):
    """Integration tests for the full release flow."""

    def setUp(self):
        self.repo = setup_test_repo()
        self.original_dir = os.getcwd()
        os.chdir(self.repo["work_repo"])
        self.git_mock = install_git_mock(self.repo["remote_repo"])

        # Save and clear env snapshot
        self._orig_snapshot = dict(_ENV_SNAPSHOT)
        for key in _ENV_SNAPSHOT:
            _ENV_SNAPSHOT[key] = ""

    def tearDown(self):
        uninstall_git_mock(self.git_mock)
        os.chdir(self.original_dir)
        shutil.rmtree(self.repo["tmpdir"], ignore_errors=True)
        _ENV_SNAPSHOT.update(self._orig_snapshot)

    def test_dry_run(self):
        """Full dry-run should not create tags."""
        add_test_commit(self.repo["work_repo"], "feat: new feature")
        push_test_commits(self.repo["work_repo"])

        release_main(["--dry-run", "--version", "1.0.0", "-n"])

        # No tag should exist
        result = subprocess.run(
            ["git", "tag", "--list", "v1.0.0"],
            cwd=self.repo["work_repo"], capture_output=True, text=True,
        )
        self.assertEqual(result.stdout.strip(), "")

    def test_release_creates_tag(self):
        """Full release should create and push the tag only (no branch)."""
        add_test_commit(self.repo["work_repo"], "feat: new feature")
        push_test_commits(self.repo["work_repo"])

        release_main(["--version", "1.0.0", "-n"])

        # Tag must exist on remote
        result = subprocess.run(
            ["git", "ls-remote", "--tags", "origin", "v1.0.0"],
            cwd=self.repo["work_repo"], capture_output=True, text=True,
        )
        self.assertIn("v1.0.0", result.stdout)

        # No release branch should exist
        result = subprocess.run(
            ["git", "ls-remote", "--heads", "origin", "release/v1.0.0"],
            cwd=self.repo["work_repo"], capture_output=True, text=True,
        )
        self.assertNotIn("release/v1.0.0", result.stdout)

    def test_release_with_description(self):
        """Tag message should include the --description text."""
        add_test_commit(self.repo["work_repo"], "feat: widget")
        push_test_commits(self.repo["work_repo"])

        release_main(["--version", "1.0.0", "-n",
                      "--description", "Adds widget support"])

        result = subprocess.run(
            ["git", "tag", "-l", "--format=%(contents)", "v1.0.0"],
            cwd=self.repo["work_repo"], capture_output=True, text=True,
        )
        self.assertIn("Adds widget support", result.stdout)
        self.assertIn("Changelog:", result.stdout)

    def test_duplicate_tag_rejected(self):
        """Should reject version if tag already exists."""
        add_test_commit(self.repo["work_repo"], "feat: one")
        push_test_commits(self.repo["work_repo"])
        create_test_tag(self.repo["work_repo"], "v1.0.0")

        with self.assertRaises(SystemExit) as cm:
            release_main(["--version", "1.0.0", "-n"])
        self.assertEqual(cm.exception.code, 1)

    def test_invalid_semver_rejected(self):
        """Should reject invalid semver with a user-friendly error."""
        with self.assertRaises(SystemExit) as cm:
            release_main(["--version", "invalid", "-n"])
        self.assertEqual(cm.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
