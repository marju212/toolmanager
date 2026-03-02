"""Tests for src/lib/config.py."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from lib.config import _parse_conf_file, load_config, Config, _ENV_SNAPSHOT


class TestParseConfFile(unittest.TestCase):
    """Test _parse_conf_file()."""

    def _write_conf(self, content):
        """Write content to a temp config file and return path."""
        fd, path = tempfile.mkstemp(suffix=".conf")
        with os.fdopen(fd, "w") as f:
            f.write(content)
        self.addCleanup(os.unlink, path)
        return path

    def test_basic_key_value(self):
        path = self._write_conf("GITLAB_TOKEN=abc123\nDEFAULT_BRANCH=develop\n")
        result = _parse_conf_file(path)
        self.assertEqual(result["GITLAB_TOKEN"], "abc123")
        self.assertEqual(result["DEFAULT_BRANCH"], "develop")

    def test_comments_skipped(self):
        path = self._write_conf("# Comment\nGITLAB_TOKEN=abc\n# Another\n")
        result = _parse_conf_file(path)
        self.assertEqual(result["GITLAB_TOKEN"], "abc")
        self.assertEqual(len(result), 1)

    def test_blank_lines_skipped(self):
        path = self._write_conf("\n\nGITLAB_TOKEN=abc\n\n")
        result = _parse_conf_file(path)
        self.assertEqual(result["GITLAB_TOKEN"], "abc")

    def test_double_quoted_value(self):
        path = self._write_conf('GITLAB_TOKEN="abc123"\n')
        result = _parse_conf_file(path)
        self.assertEqual(result["GITLAB_TOKEN"], "abc123")

    def test_single_quoted_value(self):
        path = self._write_conf("GITLAB_TOKEN='abc123'\n")
        result = _parse_conf_file(path)
        self.assertEqual(result["GITLAB_TOKEN"], "abc123")

    def test_whitespace_trimming(self):
        path = self._write_conf("GITLAB_TOKEN =  abc123  \n")
        result = _parse_conf_file(path)
        self.assertEqual(result["GITLAB_TOKEN"], "abc123")

    def test_all_known_keys(self):
        content = """
GITLAB_TOKEN=token
GITLAB_API_URL=https://gitlab.self-hosted.com/api/v4
DEFAULT_BRANCH=develop
TAG_PREFIX=release-
REMOTE=upstream
VERIFY_SSL=false
UPDATE_DEFAULT_BRANCH=false
DEPLOY_BASE_PATH=/opt/tools
"""
        path = self._write_conf(content)
        result = _parse_conf_file(path)
        self.assertEqual(result["GITLAB_TOKEN"], "token")
        self.assertEqual(result["GITLAB_API_URL"], "https://gitlab.self-hosted.com/api/v4")
        self.assertEqual(result["DEFAULT_BRANCH"], "develop")
        self.assertEqual(result["TAG_PREFIX"], "release-")
        self.assertEqual(result["REMOTE"], "upstream")
        self.assertEqual(result["VERIFY_SSL"], "false")
        self.assertEqual(result["UPDATE_DEFAULT_BRANCH"], "false")
        self.assertEqual(result["DEPLOY_BASE_PATH"], "/opt/tools")

    def test_nonexistent_file(self):
        result = _parse_conf_file("/nonexistent/path")
        self.assertEqual(result, {})

    def test_value_with_equals(self):
        path = self._write_conf("GITLAB_API_URL=https://host.com/api/v4?foo=bar\n")
        result = _parse_conf_file(path)
        self.assertEqual(result["GITLAB_API_URL"], "https://host.com/api/v4?foo=bar")


class TestLoadConfig(unittest.TestCase):
    """Test load_config()."""

    def setUp(self):
        # Save original env snapshot and clear it for tests
        self._orig_snapshot = dict(_ENV_SNAPSHOT)
        for key in _ENV_SNAPSHOT:
            _ENV_SNAPSHOT[key] = ""

    def tearDown(self):
        # Restore original env snapshot
        _ENV_SNAPSHOT.update(self._orig_snapshot)

    def _write_conf(self, content, suffix=".conf"):
        fd, path = tempfile.mkstemp(suffix=suffix)
        with os.fdopen(fd, "w") as f:
            f.write(content)
        self.addCleanup(os.unlink, path)
        return path

    def test_defaults(self):
        config = load_config()
        self.assertEqual(config.gitlab_api_url, "https://gitlab.com/api/v4")
        self.assertEqual(config.default_branch, "main")
        self.assertEqual(config.tag_prefix, "v")
        self.assertEqual(config.remote, "origin")
        self.assertFalse(config.verify_ssl)
        self.assertTrue(config.update_default_branch)
        self.assertEqual(config.deploy_base_path, "")

    def test_config_file_overrides_defaults(self):
        path = self._write_conf("DEFAULT_BRANCH=develop\nTAG_PREFIX=release-\n")
        config = load_config(config_file=path)
        self.assertEqual(config.default_branch, "develop")
        self.assertEqual(config.tag_prefix, "release-")

    def test_env_overrides_config_file(self):
        path = self._write_conf("DEFAULT_BRANCH=develop\n")
        _ENV_SNAPSHOT["RELEASE_DEFAULT_BRANCH"] = "production"
        config = load_config(config_file=path)
        self.assertEqual(config.default_branch, "production")

    def test_cli_deploy_path_overrides_all(self):
        _ENV_SNAPSHOT["DEPLOY_BASE_PATH"] = "/env/path"
        config = load_config(cli_deploy_path="/cli/path")
        self.assertEqual(config.deploy_base_path, "/cli/path")

    def test_missing_config_file_exits(self):
        with self.assertRaises(SystemExit):
            load_config(config_file="/nonexistent/config")

    def test_verify_ssl_false(self):
        path = self._write_conf("VERIFY_SSL=false\n")
        config = load_config(config_file=path)
        self.assertFalse(config.verify_ssl)

    def test_update_default_branch_false(self):
        path = self._write_conf("UPDATE_DEFAULT_BRANCH=false\n")
        config = load_config(config_file=path)
        self.assertFalse(config.update_default_branch)

    def test_repo_config(self):
        tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmpdir, True)
        conf_path = os.path.join(tmpdir, ".release.conf")
        with open(conf_path, "w") as f:
            f.write("TAG_PREFIX=rel-\n")
        config = load_config(repo_root=tmpdir)
        self.assertEqual(config.tag_prefix, "rel-")

    def test_bundle_config_keys(self):
        path = self._write_conf(
            "BUNDLE_SUBMODULE_DIR=tools\n"
            "BUNDLE_NAME=my-toolset\n"
            "MODULEFILE_TEMPLATE=/path/to/template\n"
        )
        config = load_config(config_file=path)
        self.assertEqual(config.bundle_submodule_dir, "tools")
        self.assertEqual(config.bundle_name, "my-toolset")
        self.assertEqual(config.modulefile_template, "/path/to/template")


if __name__ == "__main__":
    unittest.main()
