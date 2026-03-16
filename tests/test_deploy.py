"""Tests for src/deploy.py (subcommand-driven, tools.json manifest)."""

import json
import os
import shutil
import sys
import tempfile
import unittest

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
    _compare_versions,
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
    }
    base.update(overrides)
    return base


def _write_manifest(path, tools, toolsets=None):
    data = {"tools": tools, "toolsets": toolsets or {}}
    with open(path, "w") as f:
        json.dump(data, f)


# ---------------------------------------------------------------------------
# run_bootstrap tests (unchanged from old deploy)
# ---------------------------------------------------------------------------

class TestRunBootstrap(unittest.TestCase):
    def test_no_bootstrap(self):
        tmpdir = tempfile.mkdtemp()
        try:
            self.assertTrue(run_bootstrap(tmpdir))
        finally:
            shutil.rmtree(tmpdir)

    def test_install_sh(self):
        tmpdir = tempfile.mkdtemp()
        try:
            install_sh = os.path.join(tmpdir, "install.sh")
            with open(install_sh, "w") as f:
                f.write("#!/bin/bash\ntouch marker.txt\n")
            os.chmod(install_sh, 0o755)
            self.assertTrue(run_bootstrap(tmpdir))
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "marker.txt")))
        finally:
            shutil.rmtree(tmpdir)

    def test_install_py(self):
        tmpdir = tempfile.mkdtemp()
        try:
            install_py = os.path.join(tmpdir, "install.py")
            with open(install_py, "w") as f:
                f.write("import pathlib\npathlib.Path('marker.txt').touch()\n")
            self.assertTrue(run_bootstrap(tmpdir))
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "marker.txt")))
        finally:
            shutil.rmtree(tmpdir)

    def test_install_sh_priority(self):
        tmpdir = tempfile.mkdtemp()
        try:
            install_sh = os.path.join(tmpdir, "install.sh")
            with open(install_sh, "w") as f:
                f.write("#!/bin/bash\ntouch sh_marker.txt\n")
            os.chmod(install_sh, 0o755)
            install_py = os.path.join(tmpdir, "install.py")
            with open(install_py, "w") as f:
                f.write("import pathlib\npathlib.Path('py_marker.txt').touch()\n")
            self.assertTrue(run_bootstrap(tmpdir))
            self.assertTrue(
                os.path.isfile(os.path.join(tmpdir, "sh_marker.txt"))
            )
            self.assertFalse(
                os.path.isfile(os.path.join(tmpdir, "py_marker.txt"))
            )
        finally:
            shutil.rmtree(tmpdir)

    def test_install_failure(self):
        tmpdir = tempfile.mkdtemp()
        try:
            install_sh = os.path.join(tmpdir, "install.sh")
            with open(install_sh, "w") as f:
                f.write("#!/bin/bash\nexit 1\n")
            os.chmod(install_sh, 0o755)
            self.assertFalse(run_bootstrap(tmpdir))
        finally:
            shutil.rmtree(tmpdir)

    def test_install_py_failure(self):
        tmpdir = tempfile.mkdtemp()
        try:
            install_py = os.path.join(tmpdir, "install.py")
            with open(install_py, "w") as f:
                f.write("raise RuntimeError('install failed')\n")
            self.assertFalse(run_bootstrap(tmpdir))
        finally:
            shutil.rmtree(tmpdir)

    def test_dry_run(self):
        tmpdir = tempfile.mkdtemp()
        try:
            install_sh = os.path.join(tmpdir, "install.sh")
            with open(install_sh, "w") as f:
                f.write("#!/bin/bash\ntouch marker.txt\n")
            os.chmod(install_sh, 0o755)
            self.assertTrue(run_bootstrap(tmpdir, dry_run=True))
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
        self.assertEqual(ctx.exception.code, 1)

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
        """cmd_deploy must error when deploy_base_path is empty."""
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
        """install.sh in cloned repo should run after clone."""
        import subprocess
        # Add install.sh to the repo before tagging v1.1.0
        install_path = os.path.join(self.repo["work_repo"], "install.sh")
        with open(install_path, "w") as f:
            f.write('#!/bin/bash\ntouch "$PWD/bootstrapped.txt"\n')
        os.chmod(install_path, 0o755)
        subprocess.run(
            ["git", "add", "install.sh"],
            cwd=self.repo["work_repo"], capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Add install.sh"],
            cwd=self.repo["work_repo"], capture_output=True,
        )
        push_test_commits(self.repo["work_repo"])
        create_test_tag(self.repo["work_repo"], "v1.1.0")

        config = self._config()
        cmd_deploy("test-tool", "1.1.0", _make_args(), config)

        marker = os.path.join(
            self.deploy_dir, "test-tool", "1.1.0", "bootstrapped.txt"
        )
        self.assertTrue(os.path.isfile(marker))


# ---------------------------------------------------------------------------
# cmd_deploy — disk source
# ---------------------------------------------------------------------------

class TestDeployDiskSource(unittest.TestCase):
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
                "source": {"type": "disk", "path": self.disk_source},
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
        cmd_deploy("tool-b", "1.0.0", _make_args(), config)

        # No clone directory under deploy_dir
        clone_dir = os.path.join(self.deploy_dir, "tool-b", "1.0.0")
        self.assertFalse(os.path.isdir(clone_dir))

        # Modulefile should exist
        mf_file = os.path.join(self.deploy_dir, "mf", "tool-b", "1.0.0")
        self.assertTrue(os.path.isfile(mf_file))

    def test_deploy_updates_manifest(self):
        config = self._config()
        cmd_deploy("tool-b", "1.0.0", _make_args(), config)

        with open(self.manifest_path) as f:
            data = json.load(f)
        self.assertEqual(data["tools"]["tool-b"]["version"], "1.0.0")

    def test_deploy_nonexistent_version_exits(self):
        config = self._config()
        with self.assertRaises(SystemExit):
            cmd_deploy("tool-b", "9.9.9", _make_args(), config)


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
                "source": {"type": "disk", "path": self.disk_source},
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
        args = _make_args(dry_run=True)
        cmd_deploy("tool-c", "2.0.0", args, config)

        # No modulefile
        mf_file = os.path.join(self.deploy_dir, "mf", "tool-c", "2.0.0")
        self.assertFalse(os.path.isfile(mf_file))

    def test_dry_run_manifest_unchanged(self):
        config = self._config()
        args = _make_args(dry_run=True)
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
                "source": {"type": "disk", "path": self.disk_source},
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
                "source": {"type": "disk", "path": self.disk_source},
            }
        })
        config = self._config()
        args = _make_args(non_interactive=True)
        # Should complete without error
        cmd_scan(args, config)


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
                "source": {"type": "disk", "path": self.disk_source},
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
        cmd_upgrade("mytool", _make_args(), config)

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
                "source": {"type": "disk", "path": self.disk_source},
            }
        })
        config = self._config()
        cmd_upgrade("mytool", _make_args(), config)
        # Nothing new deployed
        mf_file = os.path.join(self.deploy_dir, "mf", "mytool", "1.1.0")
        self.assertFalse(os.path.isfile(mf_file))

    def test_upgrade_unknown_tool_exits(self):
        config = self._config()
        with self.assertRaises(SystemExit):
            cmd_upgrade("no-such-tool", _make_args(), config)


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
                    "source": {"type": "disk", "path": "/irrelevant"},
                },
                "tool-b": {
                    "version": "2.0.0",
                    "source": {"type": "disk", "path": "/irrelevant"},
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


if __name__ == "__main__":
    unittest.main()
