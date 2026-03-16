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
        path = self._write_conf("TAG_PREFIX=rel-\nDEFAULT_BRANCH=develop\n")
        result = _parse_conf_file(path)
        self.assertEqual(result["TAG_PREFIX"], "rel-")
        self.assertEqual(result["DEFAULT_BRANCH"], "develop")

    def test_comments_skipped(self):
        path = self._write_conf("# Comment\nDEFAULT_BRANCH=develop\n# Another\n")
        result = _parse_conf_file(path)
        self.assertEqual(result["DEFAULT_BRANCH"], "develop")
        self.assertEqual(len(result), 1)

    def test_blank_lines_skipped(self):
        path = self._write_conf("\n\nDEFAULT_BRANCH=develop\n\n")
        result = _parse_conf_file(path)
        self.assertEqual(result["DEFAULT_BRANCH"], "develop")

    def test_double_quoted_value(self):
        path = self._write_conf('TAG_PREFIX="rel-"\n')
        result = _parse_conf_file(path)
        self.assertEqual(result["TAG_PREFIX"], "rel-")

    def test_single_quoted_value(self):
        path = self._write_conf("TAG_PREFIX='rel-'\n")
        result = _parse_conf_file(path)
        self.assertEqual(result["TAG_PREFIX"], "rel-")

    def test_whitespace_trimming(self):
        path = self._write_conf("TAG_PREFIX =  rel-  \n")
        result = _parse_conf_file(path)
        self.assertEqual(result["TAG_PREFIX"], "rel-")

    def test_all_known_keys(self):
        content = """
DEFAULT_BRANCH=develop
TAG_PREFIX=release-
REMOTE=upstream
DEPLOY_BASE_PATH=/opt/tools
"""
        path = self._write_conf(content)
        result = _parse_conf_file(path)
        self.assertEqual(result["DEFAULT_BRANCH"], "develop")
        self.assertEqual(result["TAG_PREFIX"], "release-")
        self.assertEqual(result["REMOTE"], "upstream")
        self.assertEqual(result["DEPLOY_BASE_PATH"], "/opt/tools")

    def test_nonexistent_file(self):
        result = _parse_conf_file("/nonexistent/path")
        self.assertEqual(result, {})

    def test_value_with_equals(self):
        path = self._write_conf("DEPLOY_BASE_PATH=/opt/foo=bar\n")
        result = _parse_conf_file(path)
        self.assertEqual(result["DEPLOY_BASE_PATH"], "/opt/foo=bar")

    def test_removed_update_default_branch_is_unknown(self):
        """UPDATE_DEFAULT_BRANCH was removed; it must not be silently applied."""
        path = self._write_conf("UPDATE_DEFAULT_BRANCH=false\n")
        result = _parse_conf_file(path)
        self.assertNotIn("UPDATE_DEFAULT_BRANCH", result)


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
        self.assertEqual(config.default_branch, "main")
        self.assertEqual(config.tag_prefix, "v")
        self.assertEqual(config.remote, "origin")
        self.assertEqual(config.deploy_base_path, "")
        self.assertEqual(config.mf_base_path, "")

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

    def test_mf_base_path_conf_key(self):
        path = self._write_conf("MF_BASE_PATH=/opt/modulefiles\n")
        config = load_config(config_file=path)
        self.assertEqual(config.mf_base_path, "/opt/modulefiles")

    def test_mf_base_path_env_var(self):
        _ENV_SNAPSHOT["MF_BASE_PATH"] = "/env/modulefiles"
        config = load_config()
        self.assertEqual(config.mf_base_path, "/env/modulefiles")

    def test_cli_mf_path_overrides_all(self):
        _ENV_SNAPSHOT["MF_BASE_PATH"] = "/env/modulefiles"
        config = load_config(cli_mf_path="/cli/modulefiles")
        self.assertEqual(config.mf_base_path, "/cli/modulefiles")


if __name__ == "__main__":
    unittest.main()
