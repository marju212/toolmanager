"""Tests for src/bundle.py."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from conftest import setup_bundle_test_repo
from bundle import parse_args, detect_submodules, print_manifest, deploy_bundle
from lib.modulefile import generate_bundle_modulefile


class TestBundleParseArgs(unittest.TestCase):
    """Test bundle.py argument parsing."""

    def test_help(self):
        with self.assertRaises(SystemExit) as ctx:
            parse_args(["--help"])
        self.assertEqual(ctx.exception.code, 0)

    def test_deploy_only(self):
        args = parse_args(["--deploy-only"])
        self.assertTrue(args["deploy_only"])

    def test_submodule_dir(self):
        args = parse_args(["--submodule-dir", "tools"])
        self.assertEqual(args["submodule_dir"], "tools")

    def test_version(self):
        args = parse_args(["--version", "1.0.0"])
        self.assertEqual(args["cli_version"], "1.0.0")

    def test_deploy_path(self):
        args = parse_args(["--deploy-path", "/opt/tools"])
        self.assertEqual(args["cli_deploy_path"], "/opt/tools")

    def test_mf_path(self):
        args = parse_args(["--mf-path", "/opt/mf"])
        self.assertEqual(args["cli_mf_path"], "/opt/mf")

    def test_combined(self):
        args = parse_args(["--dry-run", "--version", "1.0.0",
                           "--submodule-dir", "tools", "-n"])
        self.assertTrue(args["dry_run"])
        self.assertTrue(args["non_interactive"])
        self.assertEqual(args["cli_version"], "1.0.0")
        self.assertEqual(args["submodule_dir"], "tools")


class TestDetectSubmodules(unittest.TestCase):
    """Test detect_submodules()."""

    def setUp(self):
        self.repo = setup_bundle_test_repo()
        self.original_dir = os.getcwd()
        os.chdir(self.repo["work_repo"])

    def tearDown(self):
        os.chdir(self.original_dir)
        shutil.rmtree(self.repo["tmpdir"], ignore_errors=True)

    def test_detect_all_submodules(self):
        submodules = detect_submodules("", "v",
                                       cwd=self.repo["work_repo"])
        self.assertEqual(len(submodules), 2)
        names = {s[0] for s in submodules}
        self.assertIn("tool-a", names)
        self.assertIn("tool-b", names)

    def test_detect_with_filter(self):
        submodules = detect_submodules("tools", "v",
                                       cwd=self.repo["work_repo"])
        self.assertEqual(len(submodules), 2)

    def test_versions_extracted(self):
        submodules = detect_submodules("", "v",
                                       cwd=self.repo["work_repo"])
        versions = {s[0]: s[1] for s in submodules}
        self.assertEqual(versions["tool-a"], "1.0.0")
        self.assertEqual(versions["tool-b"], "2.0.0")


class TestDeployBundle(unittest.TestCase):
    """Test deploy_bundle()."""

    def test_deploy_creates_modulefile(self):
        tmpdir = tempfile.mkdtemp()
        try:
            deploy_bundle(
                "1.0.0", "my-toolset", tmpdir,
                {"tool-a": "1.2.0", "tool-b": "2.0.0"},
            )
            mf_file = os.path.join(tmpdir, "mf", "my-toolset", "1.0.0")
            self.assertTrue(os.path.isfile(mf_file))
            with open(mf_file) as f:
                content = f.read()
            self.assertIn("my-toolset", content)
            self.assertIn("module load tool-a/1.2.0", content)
            self.assertIn("module load tool-b/2.0.0", content)
        finally:
            shutil.rmtree(tmpdir)

    def test_deploy_dry_run(self):
        tmpdir = tempfile.mkdtemp()
        try:
            deploy_bundle(
                "1.0.0", "my-toolset", tmpdir,
                {"tool-a": "1.2.0"},
                dry_run=True,
            )
            mf_file = os.path.join(tmpdir, "mf", "my-toolset", "1.0.0")
            self.assertFalse(os.path.isfile(mf_file))
        finally:
            shutil.rmtree(tmpdir)

    def test_deploy_existing_modulefile_error(self):
        tmpdir = tempfile.mkdtemp()
        try:
            mf_dir = os.path.join(tmpdir, "mf", "my-toolset")
            os.makedirs(mf_dir)
            mf_file = os.path.join(mf_dir, "1.0.0")
            with open(mf_file, "w") as f:
                f.write("existing\n")
            with self.assertRaises(SystemExit):
                deploy_bundle(
                    "1.0.0", "my-toolset", tmpdir,
                    {"tool-a": "1.2.0"},
                )
        finally:
            shutil.rmtree(tmpdir)

    def test_custom_template(self):
        tmpdir = tempfile.mkdtemp()
        try:
            template = "# Custom bundle\n%TOOL_LOADS%\nversion=%VERSION%"
            deploy_bundle(
                "1.0.0", "my-toolset", tmpdir,
                {"tool-a": "1.2.0", "tool-b": "2.0.0"},
                template_content=template,
            )
            mf_file = os.path.join(tmpdir, "mf", "my-toolset", "1.0.0")
            with open(mf_file) as f:
                content = f.read()
            self.assertIn("# Custom bundle", content)
            self.assertIn("module load tool-a/1.2.0", content)
            self.assertIn("version=1.0.0", content)
        finally:
            shutil.rmtree(tmpdir)

    def test_per_tool_placeholders(self):
        tmpdir = tempfile.mkdtemp()
        try:
            template = "tool-a: %tool-a%\ntool-b: %tool-b%"
            deploy_bundle(
                "1.0.0", "my-toolset", tmpdir,
                {"tool-a": "1.2.0", "tool-b": "2.0.0"},
                template_content=template,
            )
            mf_file = os.path.join(tmpdir, "mf", "my-toolset", "1.0.0")
            with open(mf_file) as f:
                content = f.read()
            self.assertEqual(content, "tool-a: 1.2.0\ntool-b: 2.0.0")
        finally:
            shutil.rmtree(tmpdir)

    def test_custom_mf_base_path(self):
        """Modulefile should go to mf_base_path/bundle_name/version when set."""
        deploy_dir = tempfile.mkdtemp()
        mf_dir = tempfile.mkdtemp()
        try:
            deploy_bundle(
                "1.0.0", "my-toolset", deploy_dir,
                {"tool-a": "1.2.0", "tool-b": "2.0.0"},
                mf_base_path=mf_dir,
            )
            mf_file = os.path.join(mf_dir, "my-toolset", "1.0.0")
            self.assertTrue(os.path.isfile(mf_file))

            # Should NOT appear under the default location
            default_mf = os.path.join(deploy_dir, "mf", "my-toolset", "1.0.0")
            self.assertFalse(os.path.isfile(default_mf))
        finally:
            shutil.rmtree(deploy_dir)
            shutil.rmtree(mf_dir)


class TestBundleModulefile(unittest.TestCase):
    """Test generate_bundle_modulefile()."""

    def test_default_template(self):
        result = generate_bundle_modulefile(
            "my-toolset", "1.0.0", "/opt/software",
            {"tool-a": "1.2.0", "tool-b": "2.0.0"},
        )
        self.assertIn("#%Module1.0", result)
        self.assertIn("my-toolset", result)
        self.assertIn("module load tool-a/1.2.0", result)
        self.assertIn("module load tool-b/2.0.0", result)

    def test_template_as_manifest(self):
        """Custom template controls which tools and versions are loaded."""
        template = ("# Only load tool-a\n"
                    "module load tool-a/%tool-a%")
        result = generate_bundle_modulefile(
            "my-toolset", "1.0.0", "/opt/software",
            {"tool-a": "1.2.0", "tool-b": "2.0.0"},
            template_content=template,
        )
        self.assertIn("module load tool-a/1.2.0", result)
        self.assertNotIn("tool-b", result)

    def test_invalid_placeholder_error(self):
        template = "load %nonexistent%"
        with self.assertRaises(ValueError):
            generate_bundle_modulefile(
                "my-toolset", "1.0.0", "/opt/software",
                {"tool-a": "1.2.0"},
                template_content=template,
            )


if __name__ == "__main__":
    unittest.main()
