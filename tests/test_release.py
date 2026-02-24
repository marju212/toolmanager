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
    MockGitLabServer,
)
from lib.config import _ENV_SNAPSHOT
import lib.gitlab_api as gitlab_api_mod
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

    def test_hotfix_mr(self):
        args = parse_args(["--hotfix-mr", "release/v1.0.0"])
        self.assertEqual(args["hotfix_mr_branch"], "release/v1.0.0")

    def test_hotfix_mr_missing_branch(self):
        with self.assertRaises(SystemExit):
            parse_args(["--hotfix-mr"])

    def test_version(self):
        args = parse_args(["--version", "1.2.3"])
        self.assertEqual(args["cli_version"], "1.2.3")

    def test_version_missing_arg(self):
        with self.assertRaises(SystemExit):
            parse_args(["--version"])

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

    def test_update_default_branch(self):
        args = parse_args(["--update-default-branch"])
        self.assertTrue(args["update_default_branch"])
        self.assertTrue(args["update_default_branch_set"])

    def test_no_update_default_branch(self):
        args = parse_args(["--no-update-default-branch"])
        self.assertFalse(args["update_default_branch"])
        self.assertTrue(args["update_default_branch_set"])

    def test_unknown_option(self):
        with self.assertRaises(SystemExit):
            parse_args(["--unknown"])

    def test_combined_flags(self):
        args = parse_args(["--dry-run", "--version", "1.0.0", "-n",
                           "--no-update-default-branch"])
        self.assertTrue(args["dry_run"])
        self.assertEqual(args["cli_version"], "1.0.0")
        self.assertTrue(args["non_interactive"])
        self.assertFalse(args["update_default_branch"])


class TestReleaseIntegration(unittest.TestCase):
    """Integration tests for the full release flow."""

    def setUp(self):
        self.repo = setup_test_repo()
        self.original_dir = os.getcwd()
        os.chdir(self.repo["work_repo"])
        self.git_mock = install_git_mock(self.repo["remote_repo"])

        self.mock = MockGitLabServer()
        self.mock.start()

        # Save and clear env snapshot
        self._orig_snapshot = dict(_ENV_SNAPSHOT)
        for key in _ENV_SNAPSHOT:
            _ENV_SNAPSHOT[key] = ""

        self._orig_delay = gitlab_api_mod.RETRY_DELAY
        gitlab_api_mod.RETRY_DELAY = 0.01

    def tearDown(self):
        uninstall_git_mock(self.git_mock)
        os.chdir(self.original_dir)
        shutil.rmtree(self.repo["tmpdir"], ignore_errors=True)
        self.mock.stop()
        _ENV_SNAPSHOT.update(self._orig_snapshot)
        gitlab_api_mod.RETRY_DELAY = self._orig_delay

    def test_dry_run(self):
        """Full dry-run should not create branches or tags."""
        add_test_commit(self.repo["work_repo"], "feat: new feature")
        push_test_commits(self.repo["work_repo"])

        release_main([
            "--dry-run", "--version", "1.0.0", "-n",
            "--no-update-default-branch",
        ])

        # No tag should exist
        result = subprocess.run(
            ["git", "tag", "--list", "v1.0.0"],
            cwd=self.repo["work_repo"], capture_output=True, text=True,
        )
        self.assertEqual(result.stdout.strip(), "")

    def test_release_creates_branch_and_tag(self):
        """Full release should create branch and tag."""
        add_test_commit(self.repo["work_repo"], "feat: new feature")
        push_test_commits(self.repo["work_repo"])

        # Set token for GitLab API
        _ENV_SNAPSHOT["GITLAB_TOKEN"] = "test-token"
        _ENV_SNAPSHOT["GITLAB_API_URL"] = self.mock.api_url

        release_main([
            "--version", "1.0.0", "-n",
            "--no-update-default-branch",
        ])

        # Check tag exists on remote
        result = subprocess.run(
            ["git", "ls-remote", "--tags", "origin", "v1.0.0"],
            cwd=self.repo["work_repo"], capture_output=True, text=True,
        )
        self.assertIn("v1.0.0", result.stdout)

        # Check branch exists on remote
        result = subprocess.run(
            ["git", "ls-remote", "--heads", "origin", "release/v1.0.0"],
            cwd=self.repo["work_repo"], capture_output=True, text=True,
        )
        self.assertIn("release/v1.0.0", result.stdout)

    def test_duplicate_tag_rejected(self):
        """Should reject version if tag already exists."""
        add_test_commit(self.repo["work_repo"], "feat: one")
        push_test_commits(self.repo["work_repo"])
        create_test_tag(self.repo["work_repo"], "v1.0.0")

        with self.assertRaises(SystemExit):
            release_main([
                "--version", "1.0.0", "-n",
                "--no-update-default-branch",
            ])

    def test_invalid_semver_rejected(self):
        """Should reject invalid semver with a user-friendly error."""
        with self.assertRaises(SystemExit) as cm:
            release_main([
                "--version", "invalid", "-n",
                "--no-update-default-branch",
            ])
        self.assertEqual(cm.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
