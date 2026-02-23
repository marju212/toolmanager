"""Tests for src/lib/gitlab_api.py."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from conftest import MockGitLabServer
import lib.gitlab_api as gitlab_api_mod
from lib.gitlab_api import (
    gitlab_request,
    get_project_id,
    create_merge_request,
    update_default_branch,
)


class TestGitLabRequest(unittest.TestCase):
    """Test gitlab_request()."""

    @classmethod
    def setUpClass(cls):
        cls.mock = MockGitLabServer()
        cls.mock.start()
        # Speed up retries for tests
        cls._orig_delay = gitlab_api_mod.RETRY_DELAY
        gitlab_api_mod.RETRY_DELAY = 0.01

    @classmethod
    def tearDownClass(cls):
        cls.mock.stop()
        gitlab_api_mod.RETRY_DELAY = cls._orig_delay

    def test_get_project(self):
        result = gitlab_request(
            "GET", "/projects/12345",
            token="test-token",
            api_url=self.mock.api_url,
        )
        self.assertEqual(result["id"], 12345)
        self.assertEqual(result["name"], "test-project")

    def test_get_project_by_path(self):
        result = gitlab_request(
            "GET", "/projects/group%2Ftest-project",
            token="test-token",
            api_url=self.mock.api_url,
        )
        self.assertEqual(result["id"], 12345)

    def test_missing_token(self):
        with self.assertRaises(SystemExit):
            gitlab_request("GET", "/projects/12345", token="",
                           api_url=self.mock.api_url)

    def test_auth_failure(self):
        self.mock.trigger_scenario("fail_auth")
        with self.assertRaises(SystemExit):
            gitlab_request("GET", "/projects/12345", token="test-token",
                           api_url=self.mock.api_url)

    def test_not_found(self):
        self.mock.trigger_scenario("fail_not_found")
        with self.assertRaises(RuntimeError):
            gitlab_request("GET", "/projects/12345", token="test-token",
                           api_url=self.mock.api_url)

    def test_server_error_retry(self):
        # One-shot server error should be retried
        self.mock.trigger_scenario("fail_server")
        result = gitlab_request(
            "GET", "/projects/12345",
            token="test-token",
            api_url=self.mock.api_url,
        )
        self.assertEqual(result["id"], 12345)

    def test_create_merge_request(self):
        result = gitlab_request(
            "POST", "/projects/12345/merge_requests",
            token="test-token",
            api_url=self.mock.api_url,
            data={
                "source_branch": "release/v1.0.0",
                "target_branch": "main",
                "title": "Release v1.0.0",
            },
        )
        self.assertIn("web_url", result)
        self.assertEqual(result["source_branch"], "release/v1.0.0")

    def test_update_project(self):
        result = gitlab_request(
            "PUT", "/projects/12345",
            token="test-token",
            api_url=self.mock.api_url,
            data={"default_branch": "release/v1.0.0"},
        )
        self.assertEqual(result["default_branch"], "release/v1.0.0")


class TestGetProjectId(unittest.TestCase):
    """Test get_project_id()."""

    @classmethod
    def setUpClass(cls):
        cls.mock = MockGitLabServer()
        cls.mock.start()
        cls._orig_delay = gitlab_api_mod.RETRY_DELAY
        gitlab_api_mod.RETRY_DELAY = 0.01

    @classmethod
    def tearDownClass(cls):
        cls.mock.stop()
        gitlab_api_mod.RETRY_DELAY = cls._orig_delay

    def test_get_project_id(self):
        result = get_project_id(
            "https://gitlab.example.com/group/test-project.git",
            token="test-token",
            api_url=self.mock.api_url,
        )
        self.assertEqual(result, "12345")

    def test_dry_run(self):
        result = get_project_id(
            "https://gitlab.example.com/group/test-project.git",
            token="test-token",
            api_url=self.mock.api_url,
            dry_run=True,
        )
        self.assertEqual(result, "DRY_RUN_ID")

    def test_invalid_url(self):
        with self.assertRaises(SystemExit):
            get_project_id("not-a-url", token="test-token",
                           api_url=self.mock.api_url)


class TestCreateMergeRequest(unittest.TestCase):
    """Test create_merge_request()."""

    @classmethod
    def setUpClass(cls):
        cls.mock = MockGitLabServer()
        cls.mock.start()
        cls._orig_delay = gitlab_api_mod.RETRY_DELAY
        gitlab_api_mod.RETRY_DELAY = 0.01

    @classmethod
    def tearDownClass(cls):
        cls.mock.stop()
        gitlab_api_mod.RETRY_DELAY = cls._orig_delay

    def test_create_mr(self):
        url = create_merge_request(
            "12345", "release/v1.0.0", "main",
            "Release v1.0.0", "Changelog here",
            token="test-token",
            api_url=self.mock.api_url,
        )
        self.assertIn("merge_requests", url)

    def test_dry_run(self):
        url = create_merge_request(
            "12345", "release/v1.0.0", "main",
            "Release v1.0.0", "Changelog here",
            token="test-token",
            api_url=self.mock.api_url,
            dry_run=True,
        )
        self.assertIn("dry-run", url)


class TestUpdateDefaultBranch(unittest.TestCase):
    """Test update_default_branch()."""

    @classmethod
    def setUpClass(cls):
        cls.mock = MockGitLabServer()
        cls.mock.start()
        cls._orig_delay = gitlab_api_mod.RETRY_DELAY
        gitlab_api_mod.RETRY_DELAY = 0.01

    @classmethod
    def tearDownClass(cls):
        cls.mock.stop()
        gitlab_api_mod.RETRY_DELAY = cls._orig_delay

    def test_update(self):
        # Should not raise
        update_default_branch(
            "12345", "release/v1.0.0",
            token="test-token",
            api_url=self.mock.api_url,
        )

    def test_dry_run(self):
        # Should not raise
        update_default_branch(
            "12345", "release/v1.0.0",
            token="test-token",
            api_url=self.mock.api_url,
            dry_run=True,
        )


if __name__ == "__main__":
    unittest.main()
