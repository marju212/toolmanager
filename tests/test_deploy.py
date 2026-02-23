"""Tests for src/deploy.py."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from conftest import setup_test_repo, add_test_commit, create_test_tag, push_test_commits
from lib.config import Config
from deploy import parse_args, deploy_release, run_bootstrap


class TestDeployParseArgs(unittest.TestCase):
    """Test deploy.py argument parsing."""

    def test_help(self):
        with self.assertRaises(SystemExit) as ctx:
            parse_args(["--help"])
        self.assertEqual(ctx.exception.code, 0)

    def test_version(self):
        args = parse_args(["--version", "1.2.3"])
        self.assertEqual(args["cli_version"], "1.2.3")

    def test_deploy_path(self):
        args = parse_args(["--deploy-path", "/opt/tools"])
        self.assertEqual(args["cli_deploy_path"], "/opt/tools")

    def test_dry_run(self):
        args = parse_args(["--dry-run"])
        self.assertTrue(args["dry_run"])

    def test_non_interactive(self):
        args = parse_args(["-n"])
        self.assertTrue(args["non_interactive"])


class TestRunBootstrap(unittest.TestCase):
    """Test run_bootstrap()."""

    def test_no_bootstrap(self):
        tmpdir = tempfile.mkdtemp()
        try:
            result = run_bootstrap(tmpdir)
            self.assertTrue(result)
        finally:
            shutil.rmtree(tmpdir)

    def test_install_sh(self):
        tmpdir = tempfile.mkdtemp()
        try:
            install_sh = os.path.join(tmpdir, "install.sh")
            with open(install_sh, "w") as f:
                f.write("#!/bin/bash\ntouch marker.txt\n")
            os.chmod(install_sh, 0o755)

            result = run_bootstrap(tmpdir)
            self.assertTrue(result)
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "marker.txt")))
        finally:
            shutil.rmtree(tmpdir)

    def test_install_py(self):
        tmpdir = tempfile.mkdtemp()
        try:
            install_py = os.path.join(tmpdir, "install.py")
            with open(install_py, "w") as f:
                f.write("import pathlib\n"
                        "pathlib.Path('marker.txt').touch()\n")

            result = run_bootstrap(tmpdir)
            self.assertTrue(result)
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "marker.txt")))
        finally:
            shutil.rmtree(tmpdir)

    def test_install_sh_priority(self):
        """install.sh should take priority over install.py."""
        tmpdir = tempfile.mkdtemp()
        try:
            install_sh = os.path.join(tmpdir, "install.sh")
            with open(install_sh, "w") as f:
                f.write("#!/bin/bash\ntouch sh_marker.txt\n")
            os.chmod(install_sh, 0o755)

            install_py = os.path.join(tmpdir, "install.py")
            with open(install_py, "w") as f:
                f.write("import pathlib\n"
                        "pathlib.Path('py_marker.txt').touch()\n")

            result = run_bootstrap(tmpdir)
            self.assertTrue(result)
            self.assertTrue(os.path.isfile(
                os.path.join(tmpdir, "sh_marker.txt")))
            self.assertFalse(os.path.isfile(
                os.path.join(tmpdir, "py_marker.txt")))
        finally:
            shutil.rmtree(tmpdir)

    def test_install_failure(self):
        tmpdir = tempfile.mkdtemp()
        try:
            install_sh = os.path.join(tmpdir, "install.sh")
            with open(install_sh, "w") as f:
                f.write("#!/bin/bash\nexit 1\n")
            os.chmod(install_sh, 0o755)

            result = run_bootstrap(tmpdir)
            self.assertFalse(result)
        finally:
            shutil.rmtree(tmpdir)

    def test_dry_run(self):
        tmpdir = tempfile.mkdtemp()
        try:
            install_sh = os.path.join(tmpdir, "install.sh")
            with open(install_sh, "w") as f:
                f.write("#!/bin/bash\ntouch marker.txt\n")
            os.chmod(install_sh, 0o755)

            result = run_bootstrap(tmpdir, dry_run=True)
            self.assertTrue(result)
            self.assertFalse(os.path.isfile(
                os.path.join(tmpdir, "marker.txt")))
        finally:
            shutil.rmtree(tmpdir)


class TestDeployRelease(unittest.TestCase):
    """Test deploy_release()."""

    def setUp(self):
        self.repo = setup_test_repo()
        self.original_dir = os.getcwd()
        self.original_path = os.environ.get("PATH", "")
        os.chdir(self.repo["work_repo"])
        os.environ["PATH"] = (self.repo["git_wrapper_dir"] + ":" +
                              self.original_path)
        self.deploy_dir = tempfile.mkdtemp()

    def tearDown(self):
        os.chdir(self.original_dir)
        os.environ["PATH"] = self.original_path
        shutil.rmtree(self.repo["tmpdir"], ignore_errors=True)
        shutil.rmtree(self.deploy_dir, ignore_errors=True)

    def _make_config(self, **overrides):
        defaults = {
            "deploy_base_path": self.deploy_dir,
            "tag_prefix": "v",
            "remote": "origin",
            "modulefile_template": "",
        }
        defaults.update(overrides)
        return Config(**defaults)

    def test_dry_run(self):
        add_test_commit(self.repo["work_repo"], "feat: one")
        push_test_commits(self.repo["work_repo"])
        create_test_tag(self.repo["work_repo"], "v1.0.0")

        config = self._make_config()
        deploy_release("1.0.0", config, dry_run=True)

        # Nothing should be created
        tool_dir = os.path.join(self.deploy_dir, "test-project", "1.0.0")
        self.assertFalse(os.path.isdir(tool_dir))

    def test_clone_and_modulefile(self):
        add_test_commit(self.repo["work_repo"], "feat: one")
        push_test_commits(self.repo["work_repo"])
        create_test_tag(self.repo["work_repo"], "v1.0.0")

        config = self._make_config()
        deploy_release("1.0.0", config)

        # Check clone dir exists
        tool_dir = os.path.join(self.deploy_dir, "test-project", "1.0.0")
        self.assertTrue(os.path.isdir(tool_dir))

        # Check modulefile exists
        mf_file = os.path.join(self.deploy_dir, "mf", "test-project", "1.0.0")
        self.assertTrue(os.path.isfile(mf_file))
        with open(mf_file) as f:
            content = f.read()
        self.assertIn("test-project", content)
        self.assertIn("1.0.0", content)
        self.assertIn("#%Module1.0", content)

    def test_existing_dir_error(self):
        add_test_commit(self.repo["work_repo"], "feat: one")
        push_test_commits(self.repo["work_repo"])
        create_test_tag(self.repo["work_repo"], "v1.0.0")

        tool_dir = os.path.join(self.deploy_dir, "test-project", "1.0.0")
        os.makedirs(tool_dir)

        config = self._make_config()
        with self.assertRaises(SystemExit):
            deploy_release("1.0.0", config)

    def test_relative_path_error(self):
        config = self._make_config(deploy_base_path="relative/path")
        with self.assertRaises(SystemExit):
            deploy_release("1.0.0", config)

    def test_previous_modulefile_copy(self):
        """Should copy and update from previous modulefile."""
        add_test_commit(self.repo["work_repo"], "feat: one")
        push_test_commits(self.repo["work_repo"])
        create_test_tag(self.repo["work_repo"], "v1.0.0")

        config = self._make_config()
        deploy_release("1.0.0", config)

        # Now deploy v1.1.0
        add_test_commit(self.repo["work_repo"], "feat: two")
        push_test_commits(self.repo["work_repo"])
        create_test_tag(self.repo["work_repo"], "v1.1.0")

        deploy_release("1.1.0", config)

        mf_file = os.path.join(self.deploy_dir, "mf", "test-project", "1.1.0")
        self.assertTrue(os.path.isfile(mf_file))
        with open(mf_file) as f:
            content = f.read()
        self.assertIn("1.1.0", content)
        self.assertNotIn("1.0.0", content)

    def test_custom_template(self):
        """Should use modulefile.tcl from repo."""
        # Add modulefile.tcl to the repo
        tcl_path = os.path.join(self.repo["work_repo"], "modulefile.tcl")
        with open(tcl_path, "w") as f:
            f.write("# Custom template\nset version %VERSION%\n"
                    "set root %ROOT%\n")
        subprocess.run(["git", "add", "modulefile.tcl"],
                       cwd=self.repo["work_repo"], capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add modulefile.tcl"],
                       cwd=self.repo["work_repo"], capture_output=True)
        push_test_commits(self.repo["work_repo"])
        create_test_tag(self.repo["work_repo"], "v1.0.0")

        config = self._make_config()
        deploy_release("1.0.0", config)

        mf_file = os.path.join(self.deploy_dir, "mf", "test-project", "1.0.0")
        with open(mf_file) as f:
            content = f.read()
        self.assertIn("# Custom template", content)
        self.assertIn("1.0.0", content)
        self.assertNotIn("%VERSION%", content)

    def test_bootstrap_with_install_sh(self):
        """Should run install.sh after clone."""
        install_path = os.path.join(self.repo["work_repo"], "install.sh")
        with open(install_path, "w") as f:
            f.write("#!/bin/bash\ntouch \"$PWD/bootstrapped.txt\"\n")
        os.chmod(install_path, 0o755)
        subprocess.run(["git", "add", "install.sh"],
                       cwd=self.repo["work_repo"], capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add install.sh"],
                       cwd=self.repo["work_repo"], capture_output=True)
        push_test_commits(self.repo["work_repo"])
        create_test_tag(self.repo["work_repo"], "v1.0.0")

        config = self._make_config()
        deploy_release("1.0.0", config)

        bootstrapped = os.path.join(self.deploy_dir, "test-project", "1.0.0",
                                     "bootstrapped.txt")
        self.assertTrue(os.path.isfile(bootstrapped))


if __name__ == "__main__":
    unittest.main()
