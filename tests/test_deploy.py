"""Tests for src/deploy.py (subcommand-driven, tools.json manifest)."""

import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from conftest import (
    setup_test_repo,
    add_test_commit,
    create_test_tag,
    push_test_commits,
)
from lib.config import Config
from deploy import (
    parse_global_args,
    run_bootstrap,
    cmd_deploy,
    cmd_scan,
    cmd_upgrade,
    cmd_toolset,
    cmd_apply,
    cmd_toolset_list,
    cmd_toolset_show,
    cmd_toolset_bump,
    cmd_toolset_migrate,
    cmd_prune,
    cmd_remove,
    _compare_versions,
    _resolve_path_template,
    _acquire_deploy_lock,
    _release_deploy_lock,
    EXIT_CONFIG,
    EXIT_SOURCE,
    EXIT_DEPLOY,
)
from lib.sources import (
    ArchiveAdapter, ExternalAdapter, SourceError,
    _find_archives, _flatten_single_root, _extract_zip,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_args(**overrides):
    base = {
        "dry_run": False,
        "config_file": "",
        "cli_manifest": "",
        "cli_deploy_path": "",
        "cli_mf_path": "",
        "non_interactive": True,
        "force": False,
        "overwrite": False,
    }
    base.update(overrides)
    return base


def _write_manifest(path, tools, toolsets=None):
    data = {"tools": tools, "toolsets": toolsets or {}}
    with open(path, "w") as f:
        json.dump(data, f)


# ---------------------------------------------------------------------------
# run_bootstrap tests (explicit command runner)
# ---------------------------------------------------------------------------

class TestRunBootstrap(unittest.TestCase):
    def test_no_bootstrap(self):
        tmpdir = tempfile.mkdtemp()
        try:
            self.assertTrue(run_bootstrap("", tmpdir, "1.0.0", "test-tool"))
        finally:
            shutil.rmtree(tmpdir)

    def test_install_sh(self):
        tmpdir = tempfile.mkdtemp()
        try:
            self.assertTrue(run_bootstrap("touch marker.txt", tmpdir, "1.0.0", "test-tool"))
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "marker.txt")))
        finally:
            shutil.rmtree(tmpdir)

    def test_install_py(self):
        tmpdir = tempfile.mkdtemp()
        try:
            self.assertTrue(run_bootstrap("python3 -c \"import pathlib; pathlib.Path('marker.txt').touch()\"", tmpdir, "1.0.0", "test-tool"))
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "marker.txt")))
        finally:
            shutil.rmtree(tmpdir)

    def test_install_sh_priority(self):
        """Running a shell command creates expected output."""
        tmpdir = tempfile.mkdtemp()
        try:
            self.assertTrue(run_bootstrap("touch sh_marker.txt", tmpdir, "1.0.0", "test-tool"))
            self.assertTrue(
                os.path.isfile(os.path.join(tmpdir, "sh_marker.txt"))
            )
        finally:
            shutil.rmtree(tmpdir)

    def test_install_failure(self):
        tmpdir = tempfile.mkdtemp()
        try:
            self.assertFalse(run_bootstrap("exit 1", tmpdir, "1.0.0", "test-tool"))
        finally:
            shutil.rmtree(tmpdir)

    def test_install_py_failure(self):
        tmpdir = tempfile.mkdtemp()
        try:
            self.assertFalse(run_bootstrap("python3 -c \"raise RuntimeError('install failed')\"", tmpdir, "1.0.0", "test-tool"))
        finally:
            shutil.rmtree(tmpdir)

    def test_dry_run(self):
        tmpdir = tempfile.mkdtemp()
        try:
            self.assertTrue(run_bootstrap("touch marker.txt", tmpdir, "1.0.0", "test-tool", dry_run=True))
            self.assertFalse(
                os.path.isfile(os.path.join(tmpdir, "marker.txt"))
            )
        finally:
            shutil.rmtree(tmpdir)


# ---------------------------------------------------------------------------
# Global arg parsing
# ---------------------------------------------------------------------------

class TestParseGlobalArgs(unittest.TestCase):
    def test_dry_run_flag(self):
        remaining, args = parse_global_args(["--dry-run", "scan"])
        self.assertTrue(args["dry_run"])
        self.assertEqual(remaining, ["scan"])

    def test_non_interactive_short(self):
        remaining, args = parse_global_args(["-n", "deploy", "tool-a"])
        self.assertTrue(args["non_interactive"])
        self.assertEqual(remaining, ["deploy", "tool-a"])

    def test_manifest_flag(self):
        remaining, args = parse_global_args(["--manifest", "/tmp/t.json", "scan"])
        self.assertEqual(args["cli_manifest"], "/tmp/t.json")
        self.assertEqual(remaining, ["scan"])

    def test_deploy_path_flag(self):
        remaining, args = parse_global_args(["--deploy-path", "/opt/sw", "scan"])
        self.assertEqual(args["cli_deploy_path"], "/opt/sw")

    def test_mf_path_flag(self):
        remaining, args = parse_global_args(["--mf-path", "/opt/mf", "scan"])
        self.assertEqual(args["cli_mf_path"], "/opt/mf")

    def test_help_passes_through(self):
        # --help / -h are no longer consumed by parse_global_args;
        # they pass through to remaining so subcommand dispatch can print
        # subcommand-specific help.
        remaining, _ = parse_global_args(["--help"])
        self.assertIn("--help", remaining)

        remaining, _ = parse_global_args(["-h"])
        self.assertIn("-h", remaining)


# ---------------------------------------------------------------------------
# main() — subcommand dispatch edge cases
# ---------------------------------------------------------------------------

class TestMainDispatch(unittest.TestCase):
    """Tests for argument validation and help routing in main()."""

    def _main(self, argv):
        from deploy import main
        main(argv)

    def test_top_level_help(self):
        with self.assertRaises(SystemExit) as ctx:
            self._main(["--help"])
        self.assertEqual(ctx.exception.code, 0)

    def test_top_level_help_short(self):
        with self.assertRaises(SystemExit) as ctx:
            self._main(["-h"])
        self.assertEqual(ctx.exception.code, 0)

    def test_subcommand_deploy_help(self):
        with self.assertRaises(SystemExit) as ctx:
            self._main(["deploy", "--help"])
        self.assertEqual(ctx.exception.code, 0)

    def test_subcommand_scan_help(self):
        with self.assertRaises(SystemExit) as ctx:
            self._main(["scan", "--help"])
        self.assertEqual(ctx.exception.code, 0)

    def test_subcommand_upgrade_help(self):
        with self.assertRaises(SystemExit) as ctx:
            self._main(["upgrade", "--help"])
        self.assertEqual(ctx.exception.code, 0)

    def test_subcommand_toolset_help(self):
        with self.assertRaises(SystemExit) as ctx:
            self._main(["toolset", "--help"])
        self.assertEqual(ctx.exception.code, 0)

    def test_unknown_subcommand_exits(self):
        with self.assertRaises(SystemExit) as ctx:
            self._main(["frobnicate"])
        self.assertEqual(ctx.exception.code, EXIT_CONFIG)

    def test_deploy_unknown_flag_exits(self):
        """Typo'd flag after tool name must error, not be silently ignored."""
        with self.assertRaises(SystemExit) as ctx:
            self._main(["deploy", "tool-a", "--vresion", "1.0.0",
                        "--manifest", "/nonexistent.json"])
        self.assertEqual(ctx.exception.code, 1)

    def test_scan_unexpected_arg_exits(self):
        with self.assertRaises(SystemExit) as ctx:
            self._main(["scan", "extra-arg"])
        self.assertEqual(ctx.exception.code, 1)

    def test_upgrade_extra_arg_exits(self):
        with self.assertRaises(SystemExit) as ctx:
            self._main(["upgrade", "tool-a", "extra"])
        self.assertEqual(ctx.exception.code, 1)

    def test_toolset_unknown_flag_exits(self):
        with self.assertRaises(SystemExit) as ctx:
            self._main(["toolset", "science", "--versoin", "1.0.0"])
        self.assertEqual(ctx.exception.code, 1)


# ---------------------------------------------------------------------------
# _compare_versions
# ---------------------------------------------------------------------------

class TestCompareVersions(unittest.TestCase):
    def test_up_to_date(self):
        _, bump = _compare_versions("1.2.0", ["1.0.0", "1.1.0", "1.2.0"])
        self.assertEqual(bump, "up-to-date")

    def test_patch_upgrade(self):
        latest, bump = _compare_versions("1.2.0", ["1.0.0", "1.2.0", "1.2.1"])
        self.assertEqual(bump, "patch")
        self.assertEqual(latest, "1.2.1")

    def test_minor_upgrade(self):
        _, bump = _compare_versions("1.2.0", ["1.2.0", "1.3.0"])
        self.assertEqual(bump, "minor")

    def test_major_upgrade(self):
        _, bump = _compare_versions("1.2.0", ["1.2.0", "2.0.0"])
        self.assertEqual(bump, "major")

    def test_current_ahead_of_latest(self):
        """Current newer than all available must not be shown as an upgrade."""
        latest, bump = _compare_versions("2.0.0", ["1.0.0", "1.1.0"])
        self.assertEqual(bump, "ahead")
        self.assertEqual(latest, "1.1.0")

    def test_empty_available(self):
        _, bump = _compare_versions("1.0.0", [])
        self.assertEqual(bump, "unknown")

    def test_empty_current_returns_new(self):
        """Tool never deployed (empty current) should be labelled 'new'."""
        latest, bump = _compare_versions("", ["1.0.0", "1.1.0"])
        self.assertEqual(bump, "new")
        self.assertEqual(latest, "1.1.0")


# ---------------------------------------------------------------------------
# cmd_deploy — git source
# ---------------------------------------------------------------------------

class TestDeployCmd(unittest.TestCase):
    def setUp(self):
        self.repo = setup_test_repo()
        self.tmpdir = tempfile.mkdtemp()
        self.deploy_dir = os.path.join(self.tmpdir, "deploy")
        os.makedirs(self.deploy_dir)
        self.manifest_path = os.path.join(self.tmpdir, "tools.json")

        # Create tag v1.0.0 in the local test repo
        add_test_commit(self.repo["work_repo"], "feat: initial")
        push_test_commits(self.repo["work_repo"])
        create_test_tag(self.repo["work_repo"], "v1.0.0")

        # tools.json uses the bare repo path as git URL
        _write_manifest(self.manifest_path, {
            "test-tool": {
                "version": "0.0.0",
                "source": {
                    "type": "git",
                    "url": self.repo["remote_repo"],
                },
            }
        })

    def tearDown(self):
        shutil.rmtree(self.repo["tmpdir"], ignore_errors=True)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _config(self, **overrides):
        defaults = dict(
            deploy_base_path=self.deploy_dir,
            tools_manifest=self.manifest_path,
            tag_prefix="v",
            modulefile_template="",
            mf_base_path="",
        )
        defaults.update(overrides)
        return Config(**defaults)

    def test_deploy_creates_dir_and_modulefile(self):
        config = self._config()
        cmd_deploy("test-tool", "1.0.0", _make_args(), config)

        tool_dir = os.path.join(self.deploy_dir, "test-tool", "1.0.0")
        self.assertTrue(os.path.isdir(tool_dir))

        mf_file = os.path.join(self.deploy_dir, "mf", "test-tool", "1.0.0")
        self.assertTrue(os.path.isfile(mf_file))
        with open(mf_file) as f:
            content = f.read()
        self.assertIn("#%Module1.0", content)
        self.assertIn("test-tool", content)
        self.assertIn("1.0.0", content)

    def test_deploy_updates_manifest(self):
        config = self._config()
        cmd_deploy("test-tool", "1.0.0", _make_args(), config)

        with open(self.manifest_path) as f:
            data = json.load(f)
        self.assertEqual(data["tools"]["test-tool"]["version"], "1.0.0")

    def test_deploy_existing_dir_exits(self):
        os.makedirs(os.path.join(self.deploy_dir, "test-tool", "1.0.0"))
        config = self._config()
        with self.assertRaises(SystemExit):
            cmd_deploy("test-tool", "1.0.0", _make_args(), config)

    def test_deploy_relative_path_exits(self):
        config = self._config(deploy_base_path="relative/path")
        with self.assertRaises(SystemExit):
            cmd_deploy("test-tool", "1.0.0", _make_args(), config)

    def test_deploy_unknown_tool_exits(self):
        config = self._config()
        with self.assertRaises(SystemExit):
            cmd_deploy("no-such-tool", "1.0.0", _make_args(), config)

    def test_deploy_no_path_configured_exits(self):
        """cmd_deploy must error when deploy_base_path is empty and manifest has none."""
        # Override manifest to have empty deploy_base_path
        _write_manifest(self.manifest_path, {
            "test-tool": {
                "version": "0.0.0",
                "source": {"type": "git", "url": self.repo["remote_repo"]},
            }
        })
        # Patch deploy_base_path out of the saved manifest
        with open(self.manifest_path) as f:
            data = json.load(f)
        data["deploy_base_path"] = ""
        with open(self.manifest_path, "w") as f:
            json.dump(data, f)

        config = self._config(deploy_base_path="")
        with self.assertRaises(SystemExit):
            cmd_deploy("test-tool", "1.0.0", _make_args(), config)

    def test_deploy_version_not_in_tags_exits(self):
        """Explicit version that has no git tag must error with a hint."""
        config = self._config()
        with self.assertRaises(SystemExit):
            cmd_deploy("test-tool", "9.9.9", _make_args(), config)

    def test_deploy_custom_mf_path(self):
        mf_dir = os.path.join(self.tmpdir, "custom_mf")
        os.makedirs(mf_dir)
        config = self._config(mf_base_path=mf_dir)
        cmd_deploy("test-tool", "1.0.0", _make_args(), config)

        mf_file = os.path.join(mf_dir, "test-tool", "1.0.0")
        self.assertTrue(os.path.isfile(mf_file))
        default_mf = os.path.join(self.deploy_dir, "mf", "test-tool", "1.0.0")
        self.assertFalse(os.path.isfile(default_mf))

    def test_deploy_with_bootstrap(self):
        """Explicit bootstrap command in manifest should run after deploy."""
        import subprocess
        # Tag v1.1.0
        add_test_commit(self.repo["work_repo"])
        push_test_commits(self.repo["work_repo"])
        create_test_tag(self.repo["work_repo"], "v1.1.0")

        # Re-write manifest with a bootstrap command
        _write_manifest(self.manifest_path, {
            "test-tool": {
                "version": "0.0.0",
                "bootstrap": 'touch "$PWD/bootstrapped.txt"',
                "source": {
                    "type": "git",
                    "url": self.repo["remote_repo"],
                },
            }
        })

        config = self._config()
        cmd_deploy("test-tool", "1.1.0", _make_args(), config)

        marker = os.path.join(
            self.deploy_dir, "test-tool", "1.1.0", "bootstrapped.txt"
        )
        self.assertTrue(os.path.isfile(marker))


# ---------------------------------------------------------------------------
# cmd_deploy — external source (force=True to allow deploy)
# ---------------------------------------------------------------------------

class TestDeployExternalSource(unittest.TestCase):
    """Deploy external sources requires --force; these tests verify the flow."""
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.deploy_dir = os.path.join(self.tmpdir, "deploy")
        os.makedirs(self.deploy_dir)
        self.disk_source = os.path.join(self.tmpdir, "tool-b")
        self.manifest_path = os.path.join(self.tmpdir, "tools.json")

        # Create version dirs on disk
        for v in ("1.0.0", "1.1.0"):
            os.makedirs(os.path.join(self.disk_source, v))

        _write_manifest(self.manifest_path, {
            "tool-b": {
                "version": "0.0.0",
                "source": {"type": "external", "path": self.disk_source},
            }
        })

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _config(self, **overrides):
        defaults = dict(
            deploy_base_path=self.deploy_dir,
            tools_manifest=self.manifest_path,
            tag_prefix="v",
            modulefile_template="",
            mf_base_path="",
        )
        defaults.update(overrides)
        return Config(**defaults)

    def test_deploy_no_clone_modulefile_written(self):
        config = self._config()
        cmd_deploy("tool-b", "1.0.0", _make_args(force=True), config)

        # No clone directory under deploy_dir
        clone_dir = os.path.join(self.deploy_dir, "tool-b", "1.0.0")
        self.assertFalse(os.path.isdir(clone_dir))

        # Modulefile should exist
        mf_file = os.path.join(self.deploy_dir, "mf", "tool-b", "1.0.0")
        self.assertTrue(os.path.isfile(mf_file))

    def test_deploy_updates_manifest(self):
        config = self._config()
        cmd_deploy("tool-b", "1.0.0", _make_args(force=True), config)

        with open(self.manifest_path) as f:
            data = json.load(f)
        self.assertEqual(data["tools"]["tool-b"]["version"], "1.0.0")

    def test_deploy_nonexistent_version_exits(self):
        config = self._config()
        with self.assertRaises(SystemExit):
            cmd_deploy("tool-b", "9.9.9", _make_args(force=True), config)

    def test_deploy_blocked_without_force(self):
        """External tools require --force to deploy."""
        config = self._config()
        with self.assertRaises(SystemExit):
            cmd_deploy("tool-b", "1.0.0", _make_args(), config)

    def test_deploy_relative_install_path(self):
        """Relative install_path is resolved against deploy_base_path."""
        _write_manifest(self.manifest_path, {
            "tool-b": {
                "version": "0.0.0",
                "source": {"type": "external", "path": self.disk_source},
                "install_path": "custom/tool-b/{{version}}",
            }
        })
        config = self._config()
        cmd_deploy("tool-b", "1.0.0", _make_args(force=True), config)

        # Modulefile root should point to resolved path
        expected_root = os.path.join(self.deploy_dir, "custom", "tool-b", "1.0.0")
        mf_file = os.path.join(self.deploy_dir, "mf", "tool-b", "1.0.0")
        self.assertTrue(os.path.isfile(mf_file))
        with open(mf_file) as f:
            content = f.read()
        self.assertIn(expected_root, content)

    def test_deploy_relative_mf_path(self):
        """Relative mf_path is resolved against deploy_base_path."""
        _write_manifest(self.manifest_path, {
            "tool-b": {
                "version": "0.0.0",
                "source": {"type": "external", "path": self.disk_source},
                "mf_path": "custom_mf/tool-b/{{version}}",
            }
        })
        config = self._config()
        cmd_deploy("tool-b", "1.0.0", _make_args(force=True), config)

        expected_mf = os.path.join(self.deploy_dir, "custom_mf", "tool-b", "1.0.0")
        self.assertTrue(os.path.isfile(expected_mf))


# ---------------------------------------------------------------------------
# cmd_deploy — dry-run
# ---------------------------------------------------------------------------

class TestDeployDryRun(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.deploy_dir = os.path.join(self.tmpdir, "deploy")
        os.makedirs(self.deploy_dir)
        self.disk_source = os.path.join(self.tmpdir, "tool-c")
        self.manifest_path = os.path.join(self.tmpdir, "tools.json")

        os.makedirs(os.path.join(self.disk_source, "2.0.0"))

        _write_manifest(self.manifest_path, {
            "tool-c": {
                "version": "1.0.0",
                "source": {"type": "external", "path": self.disk_source},
            }
        })

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _config(self):
        return Config(
            deploy_base_path=self.deploy_dir,
            tools_manifest=self.manifest_path,
            tag_prefix="v",
            modulefile_template="",
            mf_base_path="",
        )

    def test_dry_run_no_files_written(self):
        config = self._config()
        args = _make_args(dry_run=True, force=True)
        cmd_deploy("tool-c", "2.0.0", args, config)

        # No modulefile
        mf_file = os.path.join(self.deploy_dir, "mf", "tool-c", "2.0.0")
        self.assertFalse(os.path.isfile(mf_file))

    def test_dry_run_manifest_unchanged(self):
        config = self._config()
        args = _make_args(dry_run=True, force=True)
        cmd_deploy("tool-c", "2.0.0", args, config)

        with open(self.manifest_path) as f:
            data = json.load(f)
        self.assertEqual(data["tools"]["tool-c"]["version"], "1.0.0")


# ---------------------------------------------------------------------------
# cmd_scan
# ---------------------------------------------------------------------------

class TestScanCmd(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.disk_source = os.path.join(self.tmpdir, "mytool")
        self.manifest_path = os.path.join(self.tmpdir, "tools.json")

        for v in ("1.0.0", "1.1.0", "2.0.0"):
            os.makedirs(os.path.join(self.disk_source, v))

        _write_manifest(self.manifest_path, {
            "mytool": {
                "version": "1.0.0",
                "source": {"type": "external", "path": self.disk_source},
            }
        })

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _config(self):
        return Config(
            tools_manifest=self.manifest_path,
            tag_prefix="v",
            deploy_base_path=self.tmpdir,
        )

    def test_scan_non_interactive_no_deploy(self):
        """Scan in non-interactive mode should not deploy anything."""
        config = self._config()
        args = _make_args(non_interactive=True)
        cmd_scan(args, config)

        # Manifest version should be unchanged
        with open(self.manifest_path) as f:
            data = json.load(f)
        self.assertEqual(data["tools"]["mytool"]["version"], "1.0.0")

    def test_scan_up_to_date_tool(self):
        """Scan with current == latest should note up to date."""
        _write_manifest(self.manifest_path, {
            "mytool": {
                "version": "2.0.0",
                "source": {"type": "external", "path": self.disk_source},
            }
        })
        config = self._config()
        args = _make_args(non_interactive=True)
        # Should complete without error
        cmd_scan(args, config)

    def test_scan_writes_available(self):
        """Scan should persist available versions to the manifest."""
        config = self._config()
        args = _make_args(non_interactive=True)
        cmd_scan(args, config)

        with open(self.manifest_path) as f:
            data = json.load(f)
        self.assertEqual(
            data["tools"]["mytool"]["available"],
            ["1.0.0", "1.1.0", "2.0.0"],
        )


# ---------------------------------------------------------------------------
# cmd_upgrade
# ---------------------------------------------------------------------------

class TestUpgradeCmd(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.deploy_dir = os.path.join(self.tmpdir, "deploy")
        os.makedirs(self.deploy_dir)
        self.disk_source = os.path.join(self.tmpdir, "mytool")
        self.manifest_path = os.path.join(self.tmpdir, "tools.json")

        for v in ("1.0.0", "1.1.0"):
            os.makedirs(os.path.join(self.disk_source, v))

        _write_manifest(self.manifest_path, {
            "mytool": {
                "version": "1.0.0",
                "source": {"type": "external", "path": self.disk_source},
            }
        })

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _config(self):
        return Config(
            deploy_base_path=self.deploy_dir,
            tools_manifest=self.manifest_path,
            tag_prefix="v",
            modulefile_template="",
            mf_base_path="",
        )

    def test_upgrade_deploys_latest(self):
        config = self._config()
        cmd_upgrade("mytool", _make_args(force=True), config)

        mf_file = os.path.join(self.deploy_dir, "mf", "mytool", "1.1.0")
        self.assertTrue(os.path.isfile(mf_file))

        with open(self.manifest_path) as f:
            data = json.load(f)
        self.assertEqual(data["tools"]["mytool"]["version"], "1.1.0")

    def test_upgrade_already_latest_noop(self):
        """When already at latest, upgrade should not fail."""
        _write_manifest(self.manifest_path, {
            "mytool": {
                "version": "1.1.0",
                "source": {"type": "external", "path": self.disk_source},
            }
        })
        config = self._config()
        cmd_upgrade("mytool", _make_args(force=True), config)
        # Nothing new deployed
        mf_file = os.path.join(self.deploy_dir, "mf", "mytool", "1.1.0")
        self.assertFalse(os.path.isfile(mf_file))

    def test_upgrade_unknown_tool_exits(self):
        config = self._config()
        with self.assertRaises(SystemExit):
            cmd_upgrade("no-such-tool", _make_args(force=True), config)


# ---------------------------------------------------------------------------
# cmd_toolset
# ---------------------------------------------------------------------------

class TestToolsetCmd(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.deploy_dir = os.path.join(self.tmpdir, "deploy")
        os.makedirs(self.deploy_dir)
        self.manifest_path = os.path.join(self.tmpdir, "tools.json")

        _write_manifest(
            self.manifest_path,
            tools={
                "tool-a": {
                    "version": "1.2.0",
                    "source": {"type": "external", "path": "/irrelevant"},
                },
                "tool-b": {
                    "version": "2.0.0",
                    "source": {"type": "external", "path": "/irrelevant"},
                },
            },
            toolsets={"science": ["tool-a", "tool-b"]},
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _config(self, **overrides):
        defaults = dict(
            deploy_base_path=self.deploy_dir,
            tools_manifest=self.manifest_path,
            tag_prefix="v",
            modulefile_template="",
            mf_base_path="",
        )
        defaults.update(overrides)
        return Config(**defaults)

    def test_toolset_writes_modulefile(self):
        config = self._config()
        cmd_toolset("science", "1.0.0", _make_args(), config)

        mf_file = os.path.join(self.deploy_dir, "mf", "science", "1.0.0")
        self.assertTrue(os.path.isfile(mf_file))
        with open(mf_file) as f:
            content = f.read()
        self.assertIn("#%Module1.0", content)
        self.assertIn("science", content)

    def test_toolset_includes_tool_versions(self):
        config = self._config()
        cmd_toolset("science", "1.0.0", _make_args(), config)

        mf_file = os.path.join(self.deploy_dir, "mf", "science", "1.0.0")
        with open(mf_file) as f:
            content = f.read()
        self.assertIn("module load tool-a/1.2.0", content)
        self.assertIn("module load tool-b/2.0.0", content)

    def test_toolset_missing_version_exits(self):
        config = self._config()
        with self.assertRaises(SystemExit):
            cmd_toolset("science", "", _make_args(), config)

    def test_toolset_unknown_name_exits(self):
        config = self._config()
        with self.assertRaises(SystemExit):
            cmd_toolset("no-such-set", "1.0.0", _make_args(), config)

    def test_toolset_existing_file_exits(self):
        config = self._config()
        mf_dir = os.path.join(self.deploy_dir, "mf", "science")
        os.makedirs(mf_dir)
        mf_file = os.path.join(mf_dir, "1.0.0")
        with open(mf_file, "w") as f:
            f.write("existing\n")
        with self.assertRaises(SystemExit):
            cmd_toolset("science", "1.0.0", _make_args(), config)

    def test_toolset_existing_file_overwrite_ok(self):
        """With --overwrite, cmd_toolset replaces an existing modulefile."""
        config = self._config()
        mf_dir = os.path.join(self.deploy_dir, "mf", "science")
        os.makedirs(mf_dir)
        mf_file = os.path.join(mf_dir, "1.0.0")
        with open(mf_file, "w") as f:
            f.write("existing\n")
        cmd_toolset("science", "1.0.0", _make_args(overwrite=True), config)
        with open(mf_file) as f:
            content = f.read()
        self.assertNotEqual(content, "existing\n")
        self.assertIn("module load tool-a/1.2.0", content)

    def test_toolset_custom_mf_path(self):
        mf_dir = os.path.join(self.tmpdir, "custom_mf")
        os.makedirs(mf_dir)
        config = self._config(mf_base_path=mf_dir)
        cmd_toolset("science", "1.0.0", _make_args(), config)

        mf_file = os.path.join(mf_dir, "science", "1.0.0")
        self.assertTrue(os.path.isfile(mf_file))

    def test_toolset_no_path_configured_exits(self):
        """cmd_toolset must error when neither deploy_base_path nor mf_base_path is set."""
        config = Config(
            tools_manifest=self.manifest_path,
            deploy_base_path="",
            mf_base_path="",
        )
        with self.assertRaises(SystemExit):
            cmd_toolset("science", "1.0.0", _make_args(), config)

    def test_toolset_dry_run_no_file_written(self):
        config = self._config()
        cmd_toolset("science", "1.0.0", _make_args(dry_run=True), config)

        mf_file = os.path.join(self.deploy_dir, "mf", "science", "1.0.0")
        self.assertFalse(os.path.isfile(mf_file))

    def test_toolset_uses_toolset_template(self):
        """cmd_toolset should honor toolset_modulefile_template."""
        template_path = os.path.join(self.tmpdir, "toolset.tcl")
        with open(template_path, "w") as f:
            f.write("## TOOLSET %TOOL_NAME% v%VERSION%\n%TOOL_LOADS%\n")
        config = self._config(toolset_modulefile_template=template_path)
        cmd_toolset("science", "1.0.0", _make_args(), config)
        mf_file = os.path.join(self.deploy_dir, "mf", "science", "1.0.0")
        with open(mf_file) as f:
            content = f.read()
        self.assertIn("## TOOLSET science v1.0.0", content)
        self.assertIn("module load tool-a/1.2.0", content)

    def test_toolset_falls_back_to_modulefile_template(self):
        """cmd_toolset uses modulefile_template when toolset_modulefile_template is empty."""
        template_path = os.path.join(self.tmpdir, "mf.tcl")
        with open(template_path, "w") as f:
            f.write("## FALLBACK %TOOL_NAME%\n%TOOL_LOADS%\n")
        config = self._config(modulefile_template=template_path)
        cmd_toolset("science", "1.0.0", _make_args(), config)
        mf_file = os.path.join(self.deploy_dir, "mf", "science", "1.0.0")
        with open(mf_file) as f:
            content = f.read()
        self.assertIn("## FALLBACK science", content)


class TestToolsetCmdDictFormat(unittest.TestCase):
    """Tests for cmd_toolset with new dict-format toolsets."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.deploy_dir = os.path.join(self.tmpdir, "deploy")
        os.makedirs(self.deploy_dir)
        self.manifest_path = os.path.join(self.tmpdir, "tools.json")

        _write_manifest(
            self.manifest_path,
            tools={
                "tool-a": {
                    "version": "1.2.0",
                    "source": {"type": "external", "path": "/irrelevant"},
                },
                "tool-b": {
                    "version": "2.0.0",
                    "source": {"type": "external", "path": "/irrelevant"},
                },
            },
            toolsets={
                "science": {
                    "version": "3.0.0",
                    "tools": {"tool-a": "1.2.0", "tool-b": "2.0.0"},
                }
            },
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _config(self, **overrides):
        defaults = dict(
            deploy_base_path=self.deploy_dir,
            tools_manifest=self.manifest_path,
            tag_prefix="v",
            modulefile_template="",
            mf_base_path="",
        )
        defaults.update(overrides)
        return Config(**defaults)

    def test_dict_toolset_uses_manifest_version(self):
        """Dict-format toolset should use its own version field."""
        config = self._config()
        cmd_toolset("science", "", _make_args(), config)

        mf_file = os.path.join(self.deploy_dir, "mf", "science", "3.0.0")
        self.assertTrue(os.path.isfile(mf_file))

    def test_dict_toolset_cli_version_overrides(self):
        """CLI --version should override manifest version."""
        config = self._config()
        cmd_toolset("science", "9.0.0", _make_args(), config)

        mf_file = os.path.join(self.deploy_dir, "mf", "science", "9.0.0")
        self.assertTrue(os.path.isfile(mf_file))

    def test_dict_toolset_uses_pinned_versions(self):
        """Dict-format toolset should use pinned versions, not tool version field."""
        # Change tool-a version in manifest, but toolset still pins 1.2.0
        with open(self.manifest_path) as f:
            data = json.load(f)
        data["tools"]["tool-a"]["version"] = "9.9.9"
        with open(self.manifest_path, "w") as f:
            json.dump(data, f)

        config = self._config()
        cmd_toolset("science", "", _make_args(), config)

        mf_file = os.path.join(self.deploy_dir, "mf", "science", "3.0.0")
        with open(mf_file) as f:
            content = f.read()
        self.assertIn("module load tool-a/1.2.0", content)
        self.assertIn("module load tool-b/2.0.0", content)


# ---------------------------------------------------------------------------
# _resolve_path_template
# ---------------------------------------------------------------------------

class TestResolvePathTemplate(unittest.TestCase):
    def test_both_placeholders(self):
        result = _resolve_path_template(
            "/opt/apps/{{toolname}}/{{version}}", "gcc", "1.2.3"
        )
        self.assertEqual(result, "/opt/apps/gcc/1.2.3")

    def test_no_placeholders(self):
        result = _resolve_path_template("/opt/static/path", "gcc", "1.0.0")
        self.assertEqual(result, "/opt/static/path")

    def test_tool_only(self):
        result = _resolve_path_template("/opt/{{toolname}}/latest", "gcc", "1.0.0")
        self.assertEqual(result, "/opt/gcc/latest")

    def test_version_only(self):
        result = _resolve_path_template("/opt/tool/{{version}}", "gcc", "2.0.0")
        self.assertEqual(result, "/opt/tool/2.0.0")

    def test_user_vars_substituted(self):
        result = _resolve_path_template(
            "/opt/{{org}}/{{toolname}}/{{version}}", "gcc", "1.0.0",
            user_vars={"org": "acme"},
        )
        self.assertEqual(result, "/opt/acme/gcc/1.0.0")

    def test_multiple_user_vars(self):
        result = _resolve_path_template(
            "/{{org}}/{{env}}/{{toolname}}", "gcc", "1.0.0",
            user_vars={"org": "acme", "env": "prod"},
        )
        self.assertEqual(result, "/acme/prod/gcc")

    def test_builtin_overrides_user_var(self):
        result = _resolve_path_template(
            "/opt/{{toolname}}/{{version}}", "gcc", "2.0.0",
            user_vars={"toolname": "should-not-appear", "version": "nope"},
        )
        self.assertEqual(result, "/opt/gcc/2.0.0")

    def test_unresolved_placeholder_exits(self):
        with self.assertRaises(SystemExit):
            _resolve_path_template(
                "/opt/{{unknown}}/{{toolname}}", "gcc", "1.0.0"
            )

    def test_no_user_vars_backward_compat(self):
        result = _resolve_path_template(
            "/opt/{{toolname}}/{{version}}", "gcc", "1.0.0"
        )
        self.assertEqual(result, "/opt/gcc/1.0.0")


# ---------------------------------------------------------------------------
# ArchiveAdapter archive extraction
# ---------------------------------------------------------------------------

class TestArchiveAdapterArchiveExtraction(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.disk_source = os.path.join(self.tmpdir, "tool-archive")
        self.deploy_dir = os.path.join(self.tmpdir, "deploy")
        os.makedirs(self.deploy_dir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _create_tar_gz(self, version, files, single_root=None):
        """Create a .tar.gz in disk_source/version/ with given files.

        If single_root is set, all files are placed under that directory
        inside the archive (to test flatten logic).
        """
        version_dir = os.path.join(self.disk_source, version)
        os.makedirs(version_dir, exist_ok=True)
        archive_path = os.path.join(version_dir, "tool-install.tar.gz")
        with tarfile.open(archive_path, "w:gz") as tar:
            for name, content in files.items():
                if single_root:
                    arcname = f"{single_root}/{name}"
                else:
                    arcname = name
                data = content.encode()
                info = tarfile.TarInfo(name=arcname)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        return archive_path

    def _create_zip(self, version, files, single_root=None):
        """Create a .zip in disk_source/version/ with given files."""
        version_dir = os.path.join(self.disk_source, version)
        os.makedirs(version_dir, exist_ok=True)
        archive_path = os.path.join(version_dir, "tool-install.zip")
        with zipfile.ZipFile(archive_path, "w") as z:
            for name, content in files.items():
                if single_root:
                    arcname = f"{single_root}/{name}"
                else:
                    arcname = name
                z.writestr(arcname, content)
        return archive_path

    def test_tar_gz_extracts_to_install_path(self):
        """Disk deploy with .tar.gz extracts to install_path, auto-strips single root."""
        self._create_tar_gz("1.0.0", {"hello.txt": "world"}, single_root="pkg")
        adapter = ArchiveAdapter(self.disk_source)
        target = os.path.join(self.deploy_dir, "custom-target")

        result = adapter.deploy(
            "1.0.0", self.deploy_dir, "tool-archive",
            install_path=target,
        )

        self.assertEqual(result, target)
        self.assertTrue(os.path.isdir(target))
        # Flatten should have stripped "pkg/" root
        self.assertTrue(os.path.isfile(os.path.join(target, "hello.txt")))
        with open(os.path.join(target, "hello.txt")) as f:
            self.assertEqual(f.read(), "world")

    def test_tar_gz_no_flatten(self):
        """Disk deploy with flatten_archive=False preserves single root dir."""
        self._create_tar_gz("1.0.0", {"hello.txt": "world"}, single_root="pkg")
        adapter = ArchiveAdapter(self.disk_source)
        target = os.path.join(self.deploy_dir, "tool-archive", "1.0.0")

        result = adapter.deploy(
            "1.0.0", self.deploy_dir, "tool-archive",
            flatten_archive=False,
        )

        self.assertEqual(result, target)
        # Without flatten, "pkg/" root should remain
        self.assertTrue(os.path.isdir(os.path.join(target, "pkg")))
        self.assertTrue(os.path.isfile(os.path.join(target, "pkg", "hello.txt")))

    def test_plain_dir_no_op(self):
        """ExternalAdapter deploy returns version dir as-is without copying."""
        version_dir = os.path.join(self.disk_source, "1.0.0")
        os.makedirs(version_dir)
        with open(os.path.join(version_dir, "README.txt"), "w") as f:
            f.write("hello")

        adapter = ExternalAdapter(self.disk_source)
        result = adapter.deploy("1.0.0", self.deploy_dir, "tool-archive")

        self.assertEqual(result, version_dir)
        # No directory created under deploy_dir
        self.assertFalse(
            os.path.isdir(os.path.join(self.deploy_dir, "tool-archive", "1.0.0"))
        )

    def test_archive_adapter_errors_without_archives(self):
        """ArchiveAdapter should error if version dir has no archives."""
        version_dir = os.path.join(self.disk_source, "1.0.0")
        os.makedirs(version_dir)
        with open(os.path.join(version_dir, "README.txt"), "w") as f:
            f.write("hello")

        adapter = ArchiveAdapter(self.disk_source)
        with self.assertRaises(SourceError):
            adapter.deploy("1.0.0", self.deploy_dir, "tool-archive")

    def test_zip_extraction(self):
        """Disk deploy with .zip extracts correctly."""
        self._create_zip("2.0.0", {"data.txt": "zip-content"})
        adapter = ArchiveAdapter(self.disk_source)
        target = os.path.join(self.deploy_dir, "tool-archive", "2.0.0")

        result = adapter.deploy("2.0.0", self.deploy_dir, "tool-archive")

        self.assertEqual(result, target)
        self.assertTrue(os.path.isfile(os.path.join(target, "data.txt")))

    def test_dry_run_no_extraction(self):
        """Dry run with archives logs but does not extract."""
        self._create_tar_gz("1.0.0", {"hello.txt": "world"})
        adapter = ArchiveAdapter(self.disk_source)
        target = os.path.join(self.deploy_dir, "tool-archive", "1.0.0")

        result = adapter.deploy(
            "1.0.0", self.deploy_dir, "tool-archive", dry_run=True
        )

        self.assertEqual(result, target)
        self.assertFalse(os.path.isdir(target))

    def test_extraction_failure_cleans_up(self):
        """If extraction fails, partial install_path is cleaned up."""
        version_dir = os.path.join(self.disk_source, "1.0.0")
        os.makedirs(version_dir)
        # Create a corrupt tar.gz
        corrupt_path = os.path.join(version_dir, "bad.tar.gz")
        with open(corrupt_path, "wb") as f:
            f.write(b"this is not a valid archive")

        adapter = ArchiveAdapter(self.disk_source)
        target = os.path.join(self.deploy_dir, "tool-archive", "1.0.0")

        with self.assertRaises(SourceError):
            adapter.deploy("1.0.0", self.deploy_dir, "tool-archive")

        # Target should not exist (cleaned up)
        self.assertFalse(os.path.isdir(target))


# ---------------------------------------------------------------------------
# Zip path traversal protection
# ---------------------------------------------------------------------------

class TestZipPathTraversal(unittest.TestCase):
    def test_dotdot_in_zip_rejected(self):
        """Zip entries with '..' path component should be rejected."""
        tmpdir = tempfile.mkdtemp()
        try:
            zip_path = os.path.join(tmpdir, "evil.zip")
            with zipfile.ZipFile(zip_path, "w") as z:
                z.writestr("../../../etc/passwd", "hacked")

            dest = os.path.join(tmpdir, "dest")
            os.makedirs(dest)
            with self.assertRaises(SourceError):
                _extract_zip(zip_path, dest)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_absolute_path_in_zip_rejected(self):
        """Zip entries with absolute paths should be rejected."""
        tmpdir = tempfile.mkdtemp()
        try:
            zip_path = os.path.join(tmpdir, "evil.zip")
            with zipfile.ZipFile(zip_path, "w") as z:
                z.writestr("/etc/passwd", "hacked")

            dest = os.path.join(tmpdir, "dest")
            os.makedirs(dest)
            with self.assertRaises(SourceError):
                _extract_zip(zip_path, dest)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_dotdot_in_filename_not_rejected(self):
        """Filenames containing '..' as substring (not path component) are safe."""
        tmpdir = tempfile.mkdtemp()
        try:
            zip_path = os.path.join(tmpdir, "legit.zip")
            with zipfile.ZipFile(zip_path, "w") as z:
                z.writestr("legit..data", "content")

            dest = os.path.join(tmpdir, "dest")
            os.makedirs(dest)
            _extract_zip(zip_path, dest)
            self.assertTrue(os.path.isfile(os.path.join(dest, "legit..data")))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Flatten single root helper
# ---------------------------------------------------------------------------

class TestFlattenSingleRoot(unittest.TestCase):
    def test_single_root_flattened(self):
        tmpdir = tempfile.mkdtemp()
        try:
            root = os.path.join(tmpdir, "myroot")
            os.makedirs(root)
            with open(os.path.join(root, "file.txt"), "w") as f:
                f.write("inner")
            _flatten_single_root(tmpdir)
            # file.txt should now be at tmpdir level
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "file.txt")))
            self.assertFalse(os.path.isdir(root))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_multiple_entries_not_flattened(self):
        tmpdir = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(tmpdir, "dir1"))
            os.makedirs(os.path.join(tmpdir, "dir2"))
            _flatten_single_root(tmpdir)
            # Both should still exist
            self.assertTrue(os.path.isdir(os.path.join(tmpdir, "dir1")))
            self.assertTrue(os.path.isdir(os.path.join(tmpdir, "dir2")))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_single_file_not_flattened(self):
        tmpdir = tempfile.mkdtemp()
        try:
            with open(os.path.join(tmpdir, "file.txt"), "w") as f:
                f.write("hello")
            _flatten_single_root(tmpdir)
            # File should remain as-is
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "file.txt")))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Custom install_path / mf_path in cmd_deploy (disk source)
# ---------------------------------------------------------------------------

class TestDeployCustomPaths(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.deploy_dir = os.path.join(self.tmpdir, "deploy")
        os.makedirs(self.deploy_dir)
        self.disk_source = os.path.join(self.tmpdir, "tool-d")
        self.manifest_path = os.path.join(self.tmpdir, "tools.json")

        for v in ("1.0.0",):
            os.makedirs(os.path.join(self.disk_source, v))

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _config(self, **overrides):
        defaults = dict(
            deploy_base_path=self.deploy_dir,
            tools_manifest=self.manifest_path,
            tag_prefix="v",
            modulefile_template="",
            mf_base_path="",
        )
        defaults.update(overrides)
        return Config(**defaults)

    def test_install_path_substitution(self):
        """install_path with {{toolname}}/{{version}} resolves correctly."""
        install_dir = os.path.join(self.tmpdir, "apps")
        os.makedirs(install_dir)
        _write_manifest(self.manifest_path, {
            "tool-d": {
                "version": "",
                "install_path": os.path.join(install_dir, "{{toolname}}", "{{version}}"),
                "source": {"type": "external", "path": self.disk_source},
            }
        })
        config = self._config()
        # Disk source with no archives — install_path doesn't apply (no extraction)
        # The deploy_root should be the disk source version dir
        cmd_deploy("tool-d", "1.0.0", _make_args(force=True), config)

        with open(self.manifest_path) as f:
            data = json.load(f)
        self.assertEqual(data["tools"]["tool-d"]["version"], "1.0.0")

    def test_mf_path_substitution(self):
        """mf_path with {{toolname}}/{{version}} writes modulefile to custom location."""
        mf_dir = os.path.join(self.tmpdir, "custom_mf")
        os.makedirs(mf_dir)
        mf_template = os.path.join(mf_dir, "{{toolname}}", "{{version}}")
        _write_manifest(self.manifest_path, {
            "tool-d": {
                "version": "",
                "mf_path": mf_template,
                "source": {"type": "external", "path": self.disk_source},
            }
        })
        config = self._config()
        cmd_deploy("tool-d", "1.0.0", _make_args(force=True), config)

        expected_mf = os.path.join(mf_dir, "tool-d", "1.0.0")
        self.assertTrue(os.path.isfile(expected_mf))
        # Default location should NOT have the modulefile
        default_mf = os.path.join(self.deploy_dir, "mf", "tool-d", "1.0.0")
        self.assertFalse(os.path.isfile(default_mf))

    def test_modulefile_root_references_install_path(self):
        """Modulefile %ROOT% should reference resolved install_path, not source dir."""
        # Create archive so install_path is actually used
        version_dir = os.path.join(self.disk_source, "1.0.0")
        # Remove existing dir and recreate with archive
        shutil.rmtree(version_dir)
        os.makedirs(version_dir)
        archive_path = os.path.join(version_dir, "app.tar.gz")
        with tarfile.open(archive_path, "w:gz") as tar:
            data = b"hello"
            info = tarfile.TarInfo(name="bin/app")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

        install_target = os.path.join(self.tmpdir, "apps", "tool-d", "1.0.0")
        _write_manifest(self.manifest_path, {
            "tool-d": {
                "version": "",
                "install_path": install_target,
                "source": {"type": "archive", "path": self.disk_source},
            }
        })
        config = self._config()
        cmd_deploy("tool-d", "1.0.0", _make_args(), config)

        # Modulefile should reference the install_path, not the disk source
        mf_file = os.path.join(self.deploy_dir, "mf", "tool-d", "1.0.0")
        self.assertTrue(os.path.isfile(mf_file))
        with open(mf_file) as f:
            content = f.read()
        self.assertIn(install_target, content)


# ---------------------------------------------------------------------------
# Explicit bootstrap command
# ---------------------------------------------------------------------------

class TestExplicitBootstrap(unittest.TestCase):
    def test_bootstrap_with_env_vars(self):
        """Bootstrap command receives correct env vars."""
        tmpdir = tempfile.mkdtemp()
        try:
            # Command that writes env vars to files
            cmd = (
                'echo "$TOOL_NAME" > tool_name.txt && '
                'echo "$TOOL_VERSION" > tool_version.txt && '
                'echo "$INSTALL_PATH" > install_path.txt'
            )
            self.assertTrue(
                run_bootstrap(cmd, tmpdir, "2.0.0", "my-tool")
            )
            with open(os.path.join(tmpdir, "tool_name.txt")) as f:
                self.assertEqual(f.read().strip(), "my-tool")
            with open(os.path.join(tmpdir, "tool_version.txt")) as f:
                self.assertEqual(f.read().strip(), "2.0.0")
            with open(os.path.join(tmpdir, "install_path.txt")) as f:
                self.assertEqual(f.read().strip(), tmpdir)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_bootstrap_skipped_when_empty(self):
        """Empty bootstrap command is a no-op that returns True."""
        tmpdir = tempfile.mkdtemp()
        try:
            self.assertTrue(run_bootstrap("", tmpdir, "1.0.0", "tool"))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_bootstrap_runs_in_cwd(self):
        """Bootstrap command runs with cwd=deploy_dir."""
        tmpdir = tempfile.mkdtemp()
        try:
            self.assertTrue(
                run_bootstrap("pwd > cwd.txt", tmpdir, "1.0.0", "tool")
            )
            with open(os.path.join(tmpdir, "cwd.txt")) as f:
                self.assertEqual(f.read().strip(), tmpdir)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Bootstrap field in manifest (disk source integration)
# ---------------------------------------------------------------------------

class TestBootstrapManifestField(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.deploy_dir = os.path.join(self.tmpdir, "deploy")
        os.makedirs(self.deploy_dir)
        self.disk_source = os.path.join(self.tmpdir, "tool-bs")
        self.manifest_path = os.path.join(self.tmpdir, "tools.json")

        os.makedirs(os.path.join(self.disk_source, "1.0.0"))

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _config(self):
        return Config(
            deploy_base_path=self.deploy_dir,
            tools_manifest=self.manifest_path,
            tag_prefix="v",
            modulefile_template="",
            mf_base_path="",
        )

    def test_bootstrap_field_absent_skips(self):
        """No bootstrap field → no bootstrap runs."""
        _write_manifest(self.manifest_path, {
            "tool-bs": {
                "version": "",
                "source": {"type": "external", "path": self.disk_source},
            }
        })
        config = self._config()
        # Should succeed without error (no bootstrap to fail)
        cmd_deploy("tool-bs", "1.0.0", _make_args(force=True), config)

        with open(self.manifest_path) as f:
            data = json.load(f)
        self.assertEqual(data["tools"]["tool-bs"]["version"], "1.0.0")


# ---------------------------------------------------------------------------
# Git source with explicit bootstrap field
# ---------------------------------------------------------------------------

class TestGitSourceBootstrapField(unittest.TestCase):
    def setUp(self):
        self.repo = setup_test_repo()
        self.tmpdir = tempfile.mkdtemp()
        self.deploy_dir = os.path.join(self.tmpdir, "deploy")
        os.makedirs(self.deploy_dir)
        self.manifest_path = os.path.join(self.tmpdir, "tools.json")

        add_test_commit(self.repo["work_repo"], "feat: initial")
        push_test_commits(self.repo["work_repo"])
        create_test_tag(self.repo["work_repo"], "v1.0.0")

    def tearDown(self):
        shutil.rmtree(self.repo["tmpdir"], ignore_errors=True)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _config(self):
        return Config(
            deploy_base_path=self.deploy_dir,
            tools_manifest=self.manifest_path,
            tag_prefix="v",
            modulefile_template="",
            mf_base_path="",
        )

    def test_git_with_bootstrap_field(self):
        """Git source with explicit bootstrap field uses the command, not auto-detect."""
        _write_manifest(self.manifest_path, {
            "test-tool": {
                "version": "",
                "bootstrap": "touch bootstrapped_via_field.txt",
                "source": {
                    "type": "git",
                    "url": self.repo["remote_repo"],
                },
            }
        })
        config = self._config()
        cmd_deploy("test-tool", "1.0.0", _make_args(), config)

        marker = os.path.join(
            self.deploy_dir, "test-tool", "1.0.0", "bootstrapped_via_field.txt"
        )
        self.assertTrue(os.path.isfile(marker))


# ---------------------------------------------------------------------------
# Scan auto-discovery
# ---------------------------------------------------------------------------

class TestScanAutoDiscovery(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.repo_root = os.path.join(self.tmpdir, "repo")
        os.makedirs(self.repo_root)
        self.manifest_path = os.path.join(self.tmpdir, "tools.json")

        # Create existing tracked tool
        self.tracked_source = os.path.join(self.repo_root, "tracked-tool")
        for v in ("1.0.0",):
            os.makedirs(os.path.join(self.tracked_source, v))

        # Create untracked tool with semver dirs (should be discovered)
        self.untracked_source = os.path.join(self.repo_root, "untracked-tool")
        for v in ("1.0.0", "2.0.0"):
            os.makedirs(os.path.join(self.untracked_source, v))

        # Create untracked dir without semver subdirs (should NOT be discovered)
        self.no_semver_dir = os.path.join(self.repo_root, "not-a-tool")
        os.makedirs(os.path.join(self.no_semver_dir, "latest"))
        os.makedirs(os.path.join(self.no_semver_dir, "stable"))

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _config(self):
        return Config(
            tools_manifest=self.manifest_path,
            tag_prefix="v",
            deploy_base_path=self.tmpdir,
        )

    def test_discovery_finds_untracked_tools(self):
        """Scan discovers untracked tool dirs with semver subdirs."""
        _write_manifest(self.manifest_path, {
            "tracked-tool": {
                "version": "1.0.0",
                "source": {"type": "external", "path": self.tracked_source},
            }
        })
        config = self._config()
        # Run scan in non-interactive mode — should complete without error
        # and print discovery info to stderr
        import io
        import contextlib
        stderr_capture = io.StringIO()
        with contextlib.redirect_stderr(stderr_capture):
            cmd_scan(_make_args(non_interactive=True), config)

        stderr_output = stderr_capture.getvalue()
        self.assertIn("untracked-tool", stderr_output)

    def test_discovery_skips_no_semver_dirs(self):
        """Scan auto-discovery skips dirs without semver subdirs."""
        _write_manifest(self.manifest_path, {
            "tracked-tool": {
                "version": "1.0.0",
                "source": {"type": "external", "path": self.tracked_source},
            }
        })
        config = self._config()
        import io
        import contextlib
        stderr_capture = io.StringIO()
        with contextlib.redirect_stderr(stderr_capture):
            cmd_scan(_make_args(non_interactive=True), config)

        stderr_output = stderr_capture.getvalue()
        self.assertNotIn("not-a-tool", stderr_output)


# ---------------------------------------------------------------------------
# Toolset update hint after deploy
# ---------------------------------------------------------------------------

class TestToolsetUpdateHint(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.deploy_dir = os.path.join(self.tmpdir, "deploy")
        os.makedirs(self.deploy_dir)
        self.disk_source = os.path.join(self.tmpdir, "tool-ts")
        self.manifest_path = os.path.join(self.tmpdir, "tools.json")

        os.makedirs(os.path.join(self.disk_source, "1.0.0"))

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _config(self):
        return Config(
            deploy_base_path=self.deploy_dir,
            tools_manifest=self.manifest_path,
            tag_prefix="v",
            modulefile_template="",
            mf_base_path="",
        )

    def test_toolset_hint_shown(self):
        """Deploying a tool in a toolset shows a hint about updating toolset."""
        _write_manifest(
            self.manifest_path,
            tools={
                "tool-ts": {
                    "version": "",
                    "source": {"type": "external", "path": self.disk_source},
                },
            },
            toolsets={"science": ["tool-ts"]},
        )
        config = self._config()
        import io
        import contextlib
        stderr_capture = io.StringIO()
        with contextlib.redirect_stderr(stderr_capture):
            cmd_deploy("tool-ts", "1.0.0", _make_args(non_interactive=True, force=True), config)

        stderr_output = stderr_capture.getvalue()
        self.assertIn("toolset", stderr_output.lower())
        self.assertIn("science", stderr_output)


# ---------------------------------------------------------------------------
# _find_archives helper
# ---------------------------------------------------------------------------

class TestFindArchives(unittest.TestCase):
    def test_finds_common_extensions(self):
        tmpdir = tempfile.mkdtemp()
        try:
            for name in ("a.tar.gz", "b.tar.bz2", "c.tar.xz", "d.tgz", "e.zip"):
                with open(os.path.join(tmpdir, name), "w") as f:
                    f.write("")
            # Also create a non-archive file
            with open(os.path.join(tmpdir, "readme.txt"), "w") as f:
                f.write("")
            archives = _find_archives(tmpdir)
            self.assertEqual(len(archives), 5)
            basenames = [os.path.basename(a) for a in archives]
            self.assertNotIn("readme.txt", basenames)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_empty_dir(self):
        tmpdir = tempfile.mkdtemp()
        try:
            self.assertEqual(_find_archives(tmpdir), [])
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Deploy locking
# ---------------------------------------------------------------------------

class TestDeployLocking(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_acquire_returns_fd_and_creates_lockfile(self):
        fd, lock_path = _acquire_deploy_lock("test-tool", self.tmpdir)
        try:
            self.assertIsInstance(fd, int)
            self.assertTrue(os.path.isfile(lock_path))
        finally:
            _release_deploy_lock(fd, lock_path)

    def test_double_acquire_exits(self):
        """Second lock on same tool should fail with SystemExit."""
        fd, lock_path = _acquire_deploy_lock("test-tool", self.tmpdir)
        try:
            with self.assertRaises(SystemExit):
                _acquire_deploy_lock("test-tool", self.tmpdir)
        finally:
            _release_deploy_lock(fd, lock_path)

    def test_release_allows_reacquire(self):
        """After release, another lock can be acquired."""
        fd, lock_path = _acquire_deploy_lock("test-tool", self.tmpdir)
        _release_deploy_lock(fd, lock_path)

        fd2, lock_path2 = _acquire_deploy_lock("test-tool", self.tmpdir)
        _release_deploy_lock(fd2, lock_path2)

    def test_release_cleans_up_lockfile(self):
        fd, lock_path = _acquire_deploy_lock("test-tool", self.tmpdir)
        _release_deploy_lock(fd, lock_path)
        self.assertFalse(os.path.isfile(lock_path))


# ---------------------------------------------------------------------------
# Pre-deploy existence check: disk + archive
# ---------------------------------------------------------------------------

class TestPreDeployDiskArchiveCheck(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.deploy_dir = os.path.join(self.tmpdir, "deploy")
        os.makedirs(self.deploy_dir)
        self.disk_source = os.path.join(self.tmpdir, "tool-precheck")
        self.manifest_path = os.path.join(self.tmpdir, "tools.json")

        # Create version dir with an archive
        version_dir = os.path.join(self.disk_source, "1.0.0")
        os.makedirs(version_dir)
        archive_path = os.path.join(version_dir, "app.tar.gz")
        with tarfile.open(archive_path, "w:gz") as tar:
            data = b"hello"
            info = tarfile.TarInfo(name="file.txt")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _config(self):
        return Config(
            deploy_base_path=self.deploy_dir,
            tools_manifest=self.manifest_path,
            tag_prefix="v",
            modulefile_template="",
            mf_base_path="",
        )

    def test_existing_target_dir_exits(self):
        """Archive deploy to existing target dir should error cleanly."""
        # Pre-create the target directory that extraction would use
        target = os.path.join(self.deploy_dir, "tool-precheck", "1.0.0")
        os.makedirs(target)

        _write_manifest(self.manifest_path, {
            "tool-precheck": {
                "version": "",
                "source": {"type": "archive", "path": self.disk_source},
            }
        })
        config = self._config()
        with self.assertRaises(SystemExit):
            cmd_deploy("tool-precheck", "1.0.0", _make_args(), config)


# ---------------------------------------------------------------------------
# source type: external — externally managed tools
# ---------------------------------------------------------------------------

class TestDeployExternallyManaged(unittest.TestCase):
    """Tools with source type 'external' should be blocked unless --force."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.deploy_dir = os.path.join(self.tmpdir, "deploy")
        os.makedirs(self.deploy_dir)
        self.disk_source = os.path.join(self.tmpdir, "ext-tool")
        self.manifest_path = os.path.join(self.tmpdir, "tools.json")

        for v in ("1.0.0", "1.1.0"):
            os.makedirs(os.path.join(self.disk_source, v))

        _write_manifest(self.manifest_path, {
            "ext-tool": {
                "version": "1.0.0",
                "source": {"type": "external", "path": self.disk_source},
            }
        })

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _config(self, **overrides):
        defaults = dict(
            deploy_base_path=self.deploy_dir,
            tools_manifest=self.manifest_path,
            tag_prefix="v",
            modulefile_template="",
            mf_base_path="",
        )
        defaults.update(overrides)
        return Config(**defaults)

    def test_deploy_blocked_without_force(self):
        config = self._config()
        with self.assertRaises(SystemExit) as ctx:
            cmd_deploy("ext-tool", "1.1.0", _make_args(), config)
        self.assertEqual(ctx.exception.code, EXIT_CONFIG)

    def test_deploy_allowed_with_force(self):
        config = self._config()
        cmd_deploy("ext-tool", "1.1.0", _make_args(force=True), config)

        # Manifest should be updated
        with open(self.manifest_path) as f:
            data = json.load(f)
        self.assertEqual(data["tools"]["ext-tool"]["version"], "1.1.0")

    def test_upgrade_blocked_without_force(self):
        config = self._config()
        with self.assertRaises(SystemExit) as ctx:
            cmd_upgrade("ext-tool", _make_args(), config)
        self.assertEqual(ctx.exception.code, EXIT_CONFIG)

    def test_upgrade_allowed_with_force(self):
        config = self._config()
        cmd_upgrade("ext-tool", _make_args(force=True), config)

        with open(self.manifest_path) as f:
            data = json.load(f)
        self.assertEqual(data["tools"]["ext-tool"]["version"], "1.1.0")

    def test_scan_shows_external_tag(self):
        """Scan should still list external tools with (external) marker."""
        config = self._config()
        import io
        from unittest.mock import patch
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            cmd_scan(_make_args(), config)
        output = mock_out.getvalue()
        self.assertIn("(external)", output)

    def test_scan_excludes_external_from_upgradable(self):
        """External tools should not appear in the upgrade prompt list."""
        # Add a normal tool alongside the external one
        for v in ("1.0.0", "2.0.0"):
            os.makedirs(os.path.join(self.tmpdir, "normal-tool", v), exist_ok=True)
        _write_manifest(self.manifest_path, {
            "ext-tool": {
                "version": "1.0.0",
                "source": {"type": "external", "path": self.disk_source},
            },
            "normal-tool": {
                "version": "1.0.0",
                "source": {"type": "archive", "path": os.path.join(self.tmpdir, "normal-tool")},
            },
        })
        config = self._config()
        import io
        from unittest.mock import patch
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            cmd_scan(_make_args(), config)
        output = mock_out.getvalue()
        # ext-tool should show (external) but normal-tool should show upgrade arrow
        self.assertIn("(external)", output)
        self.assertIn("normal-tool", output)

    def test_parse_force_flag(self):
        remaining, args = parse_global_args(["--force", "deploy", "ext-tool"])
        self.assertTrue(args["force"])
        self.assertEqual(remaining, ["deploy", "ext-tool"])


# ---------------------------------------------------------------------------
# Manifest deploy_base_path default and CLI override
# ---------------------------------------------------------------------------

class TestManifestDeployBasePath(unittest.TestCase):
    """deploy_base_path in tools.json provides a default; --deploy-path overrides."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.disk_source = os.path.join(self.tmpdir, "tool-m")
        self.manifest_path = os.path.join(self.tmpdir, "tools.json")
        os.makedirs(os.path.join(self.disk_source, "1.0.0"))

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _config(self, **overrides):
        defaults = dict(
            deploy_base_path="",
            tools_manifest=self.manifest_path,
            tag_prefix="v",
            modulefile_template="",
            mf_base_path="",
        )
        defaults.update(overrides)
        return Config(**defaults)

    def test_manifest_default_slash(self):
        """Manifest without deploy_base_path defaults to /."""
        from lib.manifest import load_manifest
        _write_manifest(self.manifest_path, {
            "tool-m": {
                "version": "",
                "source": {"type": "external", "path": self.disk_source},
            }
        })
        data = load_manifest(self.manifest_path)
        self.assertEqual(data["deploy_base_path"], "/")

    def test_manifest_custom_deploy_base_path(self):
        """Manifest deploy_base_path is used when CLI does not set one."""
        deploy_dir = os.path.join(self.tmpdir, "deploy")
        os.makedirs(deploy_dir)
        data = {
            "deploy_base_path": deploy_dir,
            "tools": {
                "tool-m": {
                    "version": "",
                    "source": {"type": "external", "path": self.disk_source},
                }
            },
            "toolsets": {},
        }
        with open(self.manifest_path, "w") as f:
            json.dump(data, f)

        config = self._config()  # no deploy_base_path
        cmd_deploy("tool-m", "1.0.0", _make_args(force=True), config)

        mf_file = os.path.join(deploy_dir, "mf", "tool-m", "1.0.0")
        self.assertTrue(os.path.isfile(mf_file))

    def test_cli_overrides_manifest_deploy_base_path(self):
        """--deploy-path takes precedence over manifest deploy_base_path."""
        cli_dir = os.path.join(self.tmpdir, "cli_deploy")
        os.makedirs(cli_dir)
        data = {
            "deploy_base_path": "/should/not/be/used",
            "tools": {
                "tool-m": {
                    "version": "",
                    "source": {"type": "external", "path": self.disk_source},
                }
            },
            "toolsets": {},
        }
        with open(self.manifest_path, "w") as f:
            json.dump(data, f)

        config = self._config(deploy_base_path=cli_dir)
        cmd_deploy("tool-m", "1.0.0", _make_args(force=True), config)

        mf_file = os.path.join(cli_dir, "mf", "tool-m", "1.0.0")
        self.assertTrue(os.path.isfile(mf_file))


# ---------------------------------------------------------------------------
# Resolved path validation (relative install_path without deploy_base_path)
# ---------------------------------------------------------------------------

class TestResolvedPathValidation(unittest.TestCase):
    """Relative install_path/mf_path must fail if they can't resolve to absolute."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.disk_source = os.path.join(self.tmpdir, "tool-rp")
        self.manifest_path = os.path.join(self.tmpdir, "tools.json")
        os.makedirs(os.path.join(self.disk_source, "1.0.0"))

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _config(self, **overrides):
        defaults = dict(
            deploy_base_path="",
            tools_manifest=self.manifest_path,
            tag_prefix="v",
            modulefile_template="",
            mf_base_path="",
        )
        defaults.update(overrides)
        return Config(**defaults)

    def test_relative_install_path_no_base_exits(self):
        data = {
            "deploy_base_path": "",
            "tools": {
                "tool-rp": {
                    "version": "",
                    "source": {"type": "external", "path": self.disk_source},
                    "install_path": "relative/path/{{version}}",
                }
            },
            "toolsets": {},
        }
        with open(self.manifest_path, "w") as f:
            json.dump(data, f)
        config = self._config()
        with self.assertRaises(SystemExit):
            cmd_deploy("tool-rp", "1.0.0", _make_args(force=True), config)

    def test_relative_mf_path_no_base_exits(self):
        deploy_dir = os.path.join(self.tmpdir, "deploy")
        os.makedirs(deploy_dir)
        data = {
            "deploy_base_path": "",
            "tools": {
                "tool-rp": {
                    "version": "",
                    "source": {"type": "external", "path": self.disk_source},
                    "mf_path": "relative/mf/{{version}}",
                }
            },
            "toolsets": {},
        }
        with open(self.manifest_path, "w") as f:
            json.dump(data, f)
        config = self._config()
        with self.assertRaises(SystemExit):
            cmd_deploy("tool-rp", "1.0.0", _make_args(force=True), config)


# ---------------------------------------------------------------------------
# cmd_apply
# ---------------------------------------------------------------------------

class TestApplyCmd(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.deploy_dir = os.path.join(self.tmpdir, "deploy")
        os.makedirs(self.deploy_dir)
        self.manifest_path = os.path.join(self.tmpdir, "tools.json")

        # Create two disk sources with version dirs
        self.source_a = os.path.join(self.tmpdir, "src-a")
        self.source_b = os.path.join(self.tmpdir, "src-b")
        for v in ("1.0.0", "1.1.0"):
            os.makedirs(os.path.join(self.source_a, v))
        for v in ("2.0.0", "3.0.0"):
            os.makedirs(os.path.join(self.source_b, v))

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _config(self, **overrides):
        defaults = dict(
            deploy_base_path=self.deploy_dir,
            tools_manifest=self.manifest_path,
            tag_prefix="v",
            modulefile_template="",
            mf_base_path="",
        )
        defaults.update(overrides)
        return Config(**defaults)

    def _write_v2_manifest(self, toolsets, tools=None):
        if tools is None:
            tools = {
                "tool-a": {
                    "version": "",
                    "source": {"type": "external", "path": self.source_a},
                },
                "tool-b": {
                    "version": "",
                    "source": {"type": "external", "path": self.source_b},
                },
            }
        _write_manifest(self.manifest_path, tools=tools, toolsets=toolsets)

    def test_apply_deploys_missing_versions(self):
        """Apply should deploy tool versions that are not yet on disk."""
        self._write_v2_manifest(toolsets={
            "science": {
                "version": "1.0.0",
                "tools": {"tool-a": "1.0.0", "tool-b": "2.0.0"},
            }
        })
        config = self._config()
        cmd_apply(_make_args(non_interactive=True), config)

        # tool-a 1.0.0 is a no-archive disk source, so deploy_target is the
        # source dir itself — no new dir created under deploy_dir.
        # tool-b 2.0.0 same.
        # But toolset modulefile should be written
        mf_file = os.path.join(self.deploy_dir, "mf", "science", "1.0.0")
        self.assertTrue(os.path.isfile(mf_file))
        with open(mf_file) as f:
            content = f.read()
        self.assertIn("module load tool-a/1.0.0", content)
        self.assertIn("module load tool-b/2.0.0", content)

    def test_apply_skips_already_deployed(self):
        """Apply should skip versions where deploy dir already exists."""
        # Pre-create the deploy dir to simulate already-deployed
        os.makedirs(os.path.join(self.deploy_dir, "tool-a", "1.0.0"))

        self._write_v2_manifest(toolsets={
            "science": {
                "version": "1.0.0",
                "tools": {"tool-a": "1.0.0"},
            }
        })
        # tool-a has install_path to force it into deploy_dir
        tools = {
            "tool-a": {
                "version": "",
                "source": {"type": "external", "path": self.source_a},
                "install_path": "{{toolname}}/{{version}}",
            },
        }
        _write_manifest(self.manifest_path, tools=tools, toolsets={
            "science": {
                "version": "1.0.0",
                "tools": {"tool-a": "1.0.0"},
            }
        })
        config = self._config()
        # Should not error — just skip
        cmd_apply(_make_args(non_interactive=True), config)

    def test_apply_dry_run(self):
        """Apply with dry_run should not create any files."""
        self._write_v2_manifest(toolsets={
            "science": {
                "version": "1.0.0",
                "tools": {"tool-a": "1.0.0"},
            }
        })
        config = self._config()
        cmd_apply(_make_args(dry_run=True, non_interactive=True), config)

        mf_file = os.path.join(self.deploy_dir, "mf", "science", "1.0.0")
        self.assertFalse(os.path.isfile(mf_file))

    def test_apply_toolset_filter(self):
        """Apply with --toolset should only process that toolset."""
        self._write_v2_manifest(toolsets={
            "science": {
                "version": "1.0.0",
                "tools": {"tool-a": "1.0.0"},
            },
            "data": {
                "version": "2.0.0",
                "tools": {"tool-b": "2.0.0"},
            },
        })
        config = self._config()
        cmd_apply(_make_args(non_interactive=True), config, toolset_filter="science")

        # science toolset modulefile should exist
        mf_science = os.path.join(self.deploy_dir, "mf", "science", "1.0.0")
        self.assertTrue(os.path.isfile(mf_science))

        # data toolset modulefile should NOT exist
        mf_data = os.path.join(self.deploy_dir, "mf", "data", "2.0.0")
        self.assertFalse(os.path.isfile(mf_data))

    def test_apply_rejects_legacy_list_toolset(self):
        """Apply must reject legacy list-format toolsets."""
        _write_manifest(self.manifest_path, tools={
            "tool-a": {
                "version": "1.0.0",
                "source": {"type": "external", "path": self.source_a},
            },
        }, toolsets={"science": ["tool-a"]})
        config = self._config()
        with self.assertRaises(SystemExit):
            cmd_apply(_make_args(non_interactive=True), config)

    def test_apply_skips_external_tools(self):
        """Apply should skip tools with source type external and warn."""
        tools = {
            "tool-a": {
                "version": "",
                "source": {"type": "external", "path": self.source_a},
            },
        }
        self._write_v2_manifest(tools=tools, toolsets={
            "science": {
                "version": "1.0.0",
                "tools": {"tool-a": "1.0.0"},
            }
        })
        config = self._config()
        # Should not error, just warn and skip
        cmd_apply(_make_args(non_interactive=True), config)

    def test_apply_unknown_toolset_exits(self):
        """Apply with --toolset for non-existent toolset should error."""
        self._write_v2_manifest(toolsets={
            "science": {
                "version": "1.0.0",
                "tools": {"tool-a": "1.0.0"},
            }
        })
        config = self._config()
        with self.assertRaises(SystemExit):
            cmd_apply(_make_args(non_interactive=True), config, toolset_filter="nope")

    def test_apply_updates_tool_version_for_skipped_already_deployed(self):
        """Apply records tool.version even when a version is already on disk."""
        tools = {
            "tool-a": {
                "version": "",
                "source": {"type": "external", "path": self.source_a},
                "install_path": "{{toolname}}/{{version}}",
            },
        }
        # Pre-create deploy dir so apply skips with "Already deployed"
        os.makedirs(os.path.join(self.deploy_dir, "tool-a", "1.0.0"))
        _write_manifest(self.manifest_path, tools=tools, toolsets={
            "science": {
                "version": "1.0.0",
                "tools": {"tool-a": "1.0.0"},
            }
        })
        config = self._config()
        cmd_apply(_make_args(non_interactive=True, force=True), config)

        with open(self.manifest_path) as f:
            data = json.load(f)
        self.assertEqual(data["tools"]["tool-a"]["version"], "1.0.0")

    def test_apply_records_highest_version_across_toolsets(self):
        """When two toolsets pin different versions, tool.version reflects the highest."""
        tools = {
            "tool-a": {
                "version": "",
                "source": {"type": "external", "path": self.source_a},
                "install_path": "{{toolname}}/{{version}}",
            },
        }
        os.makedirs(os.path.join(self.deploy_dir, "tool-a", "1.0.0"))
        os.makedirs(os.path.join(self.deploy_dir, "tool-a", "1.1.0"))
        _write_manifest(self.manifest_path, tools=tools, toolsets={
            "old-set": {
                "version": "1.0.0",
                "tools": {"tool-a": "1.0.0"},
            },
            "new-set": {
                "version": "2.0.0",
                "tools": {"tool-a": "1.1.0"},
            },
        })
        config = self._config()
        cmd_apply(_make_args(non_interactive=True, force=True), config)

        with open(self.manifest_path) as f:
            data = json.load(f)
        self.assertEqual(data["tools"]["tool-a"]["version"], "1.1.0")

    def test_apply_dry_run_does_not_update_version(self):
        """Dry-run apply must not mutate tool.version."""
        tools = {
            "tool-a": {
                "version": "0.9.0",
                "source": {"type": "external", "path": self.source_a},
                "install_path": "{{toolname}}/{{version}}",
            },
        }
        os.makedirs(os.path.join(self.deploy_dir, "tool-a", "1.0.0"))
        _write_manifest(self.manifest_path, tools=tools, toolsets={
            "science": {
                "version": "1.0.0",
                "tools": {"tool-a": "1.0.0"},
            }
        })
        config = self._config()
        cmd_apply(_make_args(dry_run=True, non_interactive=True, force=True), config)

        with open(self.manifest_path) as f:
            data = json.load(f)
        self.assertEqual(data["tools"]["tool-a"]["version"], "0.9.0")

    def test_apply_no_deploy_path_exits(self):
        """Apply without deploy_base_path should error."""
        # Write manifest without deploy_base_path set
        data = {
            "deploy_base_path": "",
            "tools": {
                "tool-a": {
                    "version": "",
                    "source": {"type": "external", "path": self.source_a},
                },
            },
            "toolsets": {
                "science": {
                    "version": "1.0.0",
                    "tools": {"tool-a": "1.0.0"},
                }
            },
        }
        with open(self.manifest_path, "w") as f:
            json.dump(data, f)
        config = Config(
            tools_manifest=self.manifest_path,
            deploy_base_path="",
        )
        with self.assertRaises(SystemExit):
            cmd_apply(_make_args(non_interactive=True), config)


# ---------------------------------------------------------------------------
# Toolset helper commands: list, show, bump, migrate
# ---------------------------------------------------------------------------

class TestToolsetHelperCmds(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.deploy_dir = os.path.join(self.tmpdir, "deploy")
        os.makedirs(self.deploy_dir)
        self.manifest_path = os.path.join(self.tmpdir, "tools.json")

        _write_manifest(
            self.manifest_path,
            tools={
                "alpha": {
                    "version": "1.0.0",
                    "source": {"type": "external", "path": self.tmpdir},
                },
                "beta": {
                    "version": "2.0.0",
                    "source": {"type": "external", "path": self.tmpdir},
                },
            },
            toolsets={
                "legacy": ["alpha", "beta"],
                "pinned": {
                    "version": "1.0.0",
                    "tools": {"alpha": "1.0.0", "beta": "2.0.0"},
                },
            },
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _config(self, **kw):
        base = dict(
            deploy_base_path=self.deploy_dir,
            tools_manifest=self.manifest_path,
        )
        base.update(kw)
        return Config(**base)

    def test_toolset_list_runs(self):
        cmd_toolset_list(_make_args(), self._config())

    def test_toolset_show_runs(self):
        cmd_toolset_show("pinned", _make_args(), self._config())

    def test_toolset_show_unknown_exits(self):
        with self.assertRaises(SystemExit):
            cmd_toolset_show("nope", _make_args(), self._config())

    def test_toolset_bump_updates_pins(self):
        cmd_toolset_bump(
            "pinned", ["alpha=1.2.0"], "1.1.0", _make_args(), self._config(),
        )
        with open(self.manifest_path) as f:
            data = json.load(f)
        self.assertEqual(data["toolsets"]["pinned"]["tools"]["alpha"], "1.2.0")
        self.assertEqual(data["toolsets"]["pinned"]["version"], "1.1.0")
        # unchanged
        self.assertEqual(data["toolsets"]["pinned"]["tools"]["beta"], "2.0.0")

    def test_toolset_bump_rejects_legacy_format(self):
        with self.assertRaises(SystemExit):
            cmd_toolset_bump(
                "legacy", ["alpha=1.2.0"], "", _make_args(), self._config(),
            )

    def test_toolset_bump_rejects_invalid_version(self):
        with self.assertRaises(SystemExit):
            cmd_toolset_bump(
                "pinned", ["alpha=notsemver"], "", _make_args(), self._config(),
            )

    def test_toolset_bump_requires_updates(self):
        with self.assertRaises(SystemExit):
            cmd_toolset_bump("pinned", [], "", _make_args(), self._config())

    def test_toolset_migrate_converts_legacy(self):
        cmd_toolset_migrate("legacy", "2.0.0", _make_args(), self._config())
        with open(self.manifest_path) as f:
            data = json.load(f)
        self.assertIsInstance(data["toolsets"]["legacy"], dict)
        self.assertEqual(data["toolsets"]["legacy"]["version"], "2.0.0")
        self.assertEqual(
            data["toolsets"]["legacy"]["tools"],
            {"alpha": "1.0.0", "beta": "2.0.0"},
        )

    def test_toolset_migrate_default_version(self):
        cmd_toolset_migrate("legacy", "", _make_args(), self._config())
        with open(self.manifest_path) as f:
            data = json.load(f)
        self.assertEqual(data["toolsets"]["legacy"]["version"], "1.0.0")

    def test_toolset_migrate_already_dict_noop(self):
        cmd_toolset_migrate("pinned", "", _make_args(), self._config())
        # No error, no change
        with open(self.manifest_path) as f:
            data = json.load(f)
        self.assertEqual(data["toolsets"]["pinned"]["version"], "1.0.0")

    def test_toolset_migrate_missing_versions_exits(self):
        # Clear a tool version
        with open(self.manifest_path) as f:
            data = json.load(f)
        data["tools"]["alpha"]["version"] = ""
        with open(self.manifest_path, "w") as f:
            json.dump(data, f)
        with self.assertRaises(SystemExit):
            cmd_toolset_migrate("legacy", "", _make_args(), self._config())


# ---------------------------------------------------------------------------
# cmd_prune
# ---------------------------------------------------------------------------

class TestPruneCmd(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.deploy_dir = os.path.join(self.tmpdir, "deploy")
        os.makedirs(self.deploy_dir)
        self.manifest_path = os.path.join(self.tmpdir, "tools.json")
        # Make deploy dirs for four versions
        for v in ("0.9.0", "1.0.0", "1.1.0", "1.2.0"):
            os.makedirs(os.path.join(self.deploy_dir, "alpha", v))
        # And modulefiles for the same
        mf_dir = os.path.join(self.deploy_dir, "mf", "alpha")
        os.makedirs(mf_dir)
        for v in ("0.9.0", "1.0.0", "1.1.0", "1.2.0"):
            with open(os.path.join(mf_dir, v), "w") as f:
                f.write(f"module v{v}\n")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _config(self, **kw):
        base = dict(
            deploy_base_path=self.deploy_dir,
            tools_manifest=self.manifest_path,
        )
        base.update(kw)
        return Config(**base)

    def _write(self, toolsets=None):
        _write_manifest(
            self.manifest_path,
            tools={
                "alpha": {
                    "version": "1.0.0",
                    "source": {"type": "external", "path": self.tmpdir},
                },
            },
            toolsets=toolsets or {},
        )

    def test_prune_keeps_newest_n(self):
        self._write()
        cmd_prune("alpha", 2, _make_args(non_interactive=True), self._config())
        remaining = sorted(os.listdir(os.path.join(self.deploy_dir, "alpha")))
        self.assertEqual(remaining, ["1.1.0", "1.2.0"])

    def test_prune_keeps_pinned_versions(self):
        """Pinned versions are kept even if older than the -keep window."""
        self._write(toolsets={
            "stable": {
                "version": "1.0.0",
                "tools": {"alpha": "0.9.0"},
            },
        })
        cmd_prune("alpha", 1, _make_args(non_interactive=True), self._config())
        remaining = sorted(os.listdir(os.path.join(self.deploy_dir, "alpha")))
        self.assertIn("0.9.0", remaining)  # pinned
        self.assertIn("1.2.0", remaining)  # newest

    def test_prune_dry_run(self):
        self._write()
        cmd_prune(
            "alpha", 1, _make_args(dry_run=True, non_interactive=True),
            self._config(),
        )
        remaining = sorted(os.listdir(os.path.join(self.deploy_dir, "alpha")))
        self.assertEqual(remaining, ["0.9.0", "1.0.0", "1.1.0", "1.2.0"])

    def test_prune_unknown_tool_exits(self):
        self._write()
        with self.assertRaises(SystemExit):
            cmd_prune(
                "nope", 1, _make_args(non_interactive=True), self._config(),
            )


# ---------------------------------------------------------------------------
# cmd_remove
# ---------------------------------------------------------------------------

class TestRemoveCmd(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.deploy_dir = os.path.join(self.tmpdir, "deploy")
        os.makedirs(self.deploy_dir)
        self.manifest_path = os.path.join(self.tmpdir, "tools.json")
        os.makedirs(os.path.join(self.deploy_dir, "alpha", "1.0.0"))
        os.makedirs(os.path.join(self.deploy_dir, "alpha", "1.1.0"))
        mf_dir = os.path.join(self.deploy_dir, "mf", "alpha")
        os.makedirs(mf_dir)
        for v in ("1.0.0", "1.1.0"):
            with open(os.path.join(mf_dir, v), "w") as f:
                f.write(f"module v{v}\n")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _config(self, **kw):
        base = dict(
            deploy_base_path=self.deploy_dir,
            tools_manifest=self.manifest_path,
        )
        base.update(kw)
        return Config(**base)

    def _write(self, toolsets=None, version="1.1.0"):
        _write_manifest(
            self.manifest_path,
            tools={
                "alpha": {
                    "version": version,
                    "source": {"type": "external", "path": self.tmpdir},
                },
            },
            toolsets=toolsets or {},
        )

    def test_remove_unpinned_version(self):
        self._write()
        cmd_remove(
            "alpha", "1.0.0", _make_args(non_interactive=True), self._config(),
        )
        self.assertFalse(os.path.isdir(
            os.path.join(self.deploy_dir, "alpha", "1.0.0")
        ))
        self.assertFalse(os.path.isfile(
            os.path.join(self.deploy_dir, "mf", "alpha", "1.0.0")
        ))

    def test_remove_pinned_refuses_without_force(self):
        self._write(toolsets={
            "stable": {
                "version": "1.0.0",
                "tools": {"alpha": "1.0.0"},
            },
        })
        with self.assertRaises(SystemExit):
            cmd_remove(
                "alpha", "1.0.0", _make_args(non_interactive=True),
                self._config(),
            )

    def test_remove_pinned_with_force(self):
        self._write(toolsets={
            "stable": {
                "version": "1.0.0",
                "tools": {"alpha": "1.0.0"},
            },
        })
        cmd_remove(
            "alpha", "1.0.0",
            _make_args(non_interactive=True, force=True),
            self._config(),
        )
        self.assertFalse(os.path.isdir(
            os.path.join(self.deploy_dir, "alpha", "1.0.0")
        ))

    def test_remove_clears_manifest_version(self):
        self._write(version="1.0.0")
        cmd_remove(
            "alpha", "1.0.0", _make_args(non_interactive=True), self._config(),
        )
        with open(self.manifest_path) as f:
            data = json.load(f)
        self.assertEqual(data["tools"]["alpha"]["version"], "")

    def test_remove_invalid_version_exits(self):
        self._write()
        with self.assertRaises(SystemExit):
            cmd_remove(
                "alpha", "notsemver", _make_args(non_interactive=True),
                self._config(),
            )

    def test_remove_unknown_tool_exits(self):
        self._write()
        with self.assertRaises(SystemExit):
            cmd_remove(
                "nope", "1.0.0", _make_args(non_interactive=True),
                self._config(),
            )


if __name__ == "__main__":
    unittest.main()
