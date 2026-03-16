"""Tests for src/lib/git.py."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# Import conftest helpers
sys.path.insert(0, os.path.dirname(__file__))
from conftest import setup_test_repo, add_test_commit, create_test_tag, push_test_commits

from lib.git import (
    get_latest_version,
    check_branch,
    generate_changelog,
    tag_release,
    check_version_available,
    get_remote_url,
    parse_project_path,
    extract_tool_name,
)


class GitTestCase(unittest.TestCase):
    """Base test case with test repo setup/teardown."""

    def setUp(self):
        self.repo = setup_test_repo()
        self.original_dir = os.getcwd()
        os.chdir(self.repo["work_repo"])

    def tearDown(self):
        os.chdir(self.original_dir)
        shutil.rmtree(self.repo["tmpdir"], ignore_errors=True)


class TestGetLatestVersion(GitTestCase):
    """Test get_latest_version()."""

    def test_no_tags(self):
        result = get_latest_version("v")
        self.assertEqual(result, "0.0.0")

    def test_single_tag(self):
        add_test_commit(self.repo["work_repo"], "feat: add feature")
        push_test_commits(self.repo["work_repo"])
        create_test_tag(self.repo["work_repo"], "v1.0.0")
        result = get_latest_version("v")
        self.assertEqual(result, "1.0.0")

    def test_multiple_tags(self):
        add_test_commit(self.repo["work_repo"], "feat: one")
        push_test_commits(self.repo["work_repo"])
        create_test_tag(self.repo["work_repo"], "v1.0.0")
        add_test_commit(self.repo["work_repo"], "feat: two")
        push_test_commits(self.repo["work_repo"])
        create_test_tag(self.repo["work_repo"], "v1.1.0")
        result = get_latest_version("v")
        self.assertEqual(result, "1.1.0")

    def test_custom_prefix(self):
        add_test_commit(self.repo["work_repo"], "feat: add feature")
        push_test_commits(self.repo["work_repo"])
        create_test_tag(self.repo["work_repo"], "release-1.0.0")
        result = get_latest_version("release-")
        self.assertEqual(result, "1.0.0")

    def test_prerelease_filtered(self):
        add_test_commit(self.repo["work_repo"], "feat: one")
        push_test_commits(self.repo["work_repo"])
        create_test_tag(self.repo["work_repo"], "v1.0.0")
        add_test_commit(self.repo["work_repo"], "feat: two")
        push_test_commits(self.repo["work_repo"])
        create_test_tag(self.repo["work_repo"], "v1.1.0-beta")
        result = get_latest_version("v")
        self.assertEqual(result, "1.0.0")


class TestCheckBranch(GitTestCase):
    """Test check_branch()."""

    def test_clean_main_branch(self):
        # Should not raise
        check_branch("main", "origin", cwd=self.repo["work_repo"])

    def test_wrong_branch(self):
        import subprocess
        subprocess.run(["git", "checkout", "-b", "feature"],
                       cwd=self.repo["work_repo"], capture_output=True)
        with self.assertRaises(SystemExit):
            check_branch("main", "origin", cwd=self.repo["work_repo"])

    def test_dirty_tree(self):
        with open(os.path.join(self.repo["work_repo"], "dirty.txt"), "w") as f:
            f.write("dirty\n")
        import subprocess
        subprocess.run(["git", "add", "dirty.txt"],
                       cwd=self.repo["work_repo"], capture_output=True)
        with self.assertRaises(SystemExit):
            check_branch("main", "origin", cwd=self.repo["work_repo"])


class TestTagRelease(GitTestCase):
    """Test tag_release()."""

    def test_creates_tag(self):
        add_test_commit(self.repo["work_repo"], "feat: add feature")
        push_test_commits(self.repo["work_repo"])

        tag_release("v1.0.0", "1.0.0", "- feat: add feature (abc1234)",
                    "origin", cwd=self.repo["work_repo"])

        import subprocess
        result = subprocess.run(
            ["git", "tag", "--list", "v1.0.0"],
            cwd=self.repo["work_repo"], capture_output=True, text=True,
        )
        self.assertIn("v1.0.0", result.stdout)

    def test_tag_message_contains_changelog(self):
        add_test_commit(self.repo["work_repo"], "feat: widget")
        push_test_commits(self.repo["work_repo"])

        tag_release("v1.0.0", "1.0.0", "- feat: widget (abc1234)",
                    "origin", cwd=self.repo["work_repo"])

        import subprocess
        result = subprocess.run(
            ["git", "tag", "-l", "--format=%(contents)", "v1.0.0"],
            cwd=self.repo["work_repo"], capture_output=True, text=True,
        )
        self.assertIn("Changelog:", result.stdout)
        self.assertIn("feat: widget", result.stdout)

    def test_tag_message_with_description(self):
        add_test_commit(self.repo["work_repo"], "feat: widget")
        push_test_commits(self.repo["work_repo"])

        tag_release("v1.0.0", "1.0.0", "- feat: widget (abc1234)",
                    "origin", cwd=self.repo["work_repo"],
                    description="Adds widget support")

        import subprocess
        result = subprocess.run(
            ["git", "tag", "-l", "--format=%(contents)", "v1.0.0"],
            cwd=self.repo["work_repo"], capture_output=True, text=True,
        )
        self.assertIn("Adds widget support", result.stdout)
        self.assertIn("Changelog:", result.stdout)

    def test_dry_run_creates_no_tag(self):
        add_test_commit(self.repo["work_repo"], "feat: widget")
        push_test_commits(self.repo["work_repo"])

        tag_release("v1.0.0", "1.0.0", "- feat: widget",
                    "origin", dry_run=True, cwd=self.repo["work_repo"])

        import subprocess
        result = subprocess.run(
            ["git", "tag", "--list", "v1.0.0"],
            cwd=self.repo["work_repo"], capture_output=True, text=True,
        )
        self.assertEqual(result.stdout.strip(), "")


class TestGenerateChangelog(GitTestCase):
    """Test generate_changelog()."""

    def test_changelog_since_tag(self):
        add_test_commit(self.repo["work_repo"], "feat: first")
        push_test_commits(self.repo["work_repo"])
        create_test_tag(self.repo["work_repo"], "v1.0.0")
        add_test_commit(self.repo["work_repo"], "fix: bug fix")
        push_test_commits(self.repo["work_repo"])

        result = generate_changelog("1.0.0", "v", cwd=self.repo["work_repo"])
        self.assertIn("fix: bug fix", result)
        self.assertNotIn("feat: first", result)

    def test_all_commits_when_no_tag(self):
        add_test_commit(self.repo["work_repo"], "feat: first")
        push_test_commits(self.repo["work_repo"])

        result = generate_changelog("0.0.0", "v", cwd=self.repo["work_repo"])
        self.assertIn("feat: first", result)

    def test_no_changes(self):
        add_test_commit(self.repo["work_repo"], "feat: first")
        push_test_commits(self.repo["work_repo"])
        create_test_tag(self.repo["work_repo"], "v1.0.0")

        result = generate_changelog("1.0.0", "v", cwd=self.repo["work_repo"])
        self.assertIn("No changes recorded", result)


class TestCheckVersionAvailable(GitTestCase):
    """Test check_version_available()."""

    def test_available(self):
        # Should not raise
        check_version_available("1.0.0", "v",
                                cwd=self.repo["work_repo"])

    def test_tag_exists(self):
        add_test_commit(self.repo["work_repo"], "feat")
        push_test_commits(self.repo["work_repo"])
        create_test_tag(self.repo["work_repo"], "v1.0.0")
        with self.assertRaises(SystemExit):
            check_version_available("1.0.0", "v",
                                    cwd=self.repo["work_repo"])

    def test_only_checks_tag_not_branch(self):
        """Branch with release/ prefix should not block version availability."""
        import subprocess
        add_test_commit(self.repo["work_repo"], "feat")
        push_test_commits(self.repo["work_repo"])
        subprocess.run(["git", "checkout", "-b", "release/v1.0.0"],
                       cwd=self.repo["work_repo"], capture_output=True)
        subprocess.run(["git", "push", "origin", "release/v1.0.0"],
                       cwd=self.repo["work_repo"], capture_output=True)
        subprocess.run(["git", "checkout", "main"],
                       cwd=self.repo["work_repo"], capture_output=True)
        # Should not raise — only the tag matters now
        check_version_available("1.0.0", "v",
                                cwd=self.repo["work_repo"])


class TestParseProjectPath(unittest.TestCase):
    """Test parse_project_path()."""

    def test_ssh_with_git(self):
        result = parse_project_path("git@gitlab.com:group/project.git")
        self.assertEqual(result, "group/project")

    def test_ssh_without_git(self):
        result = parse_project_path("git@gitlab.com:group/project")
        self.assertEqual(result, "group/project")

    def test_https_with_git(self):
        result = parse_project_path("https://gitlab.com/group/project.git")
        self.assertEqual(result, "group/project")

    def test_https_without_git(self):
        result = parse_project_path("https://gitlab.com/group/project")
        self.assertEqual(result, "group/project")

    def test_nested_groups(self):
        result = parse_project_path(
            "git@gitlab.com:group/subgroup/project.git")
        self.assertEqual(result, "group/subgroup/project")

    def test_self_hosted(self):
        result = parse_project_path(
            "https://gitlab.company.com/team/tool.git")
        self.assertEqual(result, "team/tool")

    def test_invalid(self):
        result = parse_project_path("not-a-url")
        self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main()
