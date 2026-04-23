"""Tests for src/lib/modulefile.py."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from lib.modulefile import (
    substitute_placeholders,
    validate_template_placeholders,
    resolve_template,
    generate_default_modulefile,
    generate_toolset_modulefile,
    write_modulefile,
    copy_and_update_modulefile,
    find_latest_modulefile,
)


class TestSubstitutePlaceholders(unittest.TestCase):
    """Test substitute_placeholders()."""

    def test_basic_substitution(self):
        template = "version=%VERSION% root=%ROOT% name=%TOOL_NAME%"
        result = substitute_placeholders(template, version="1.2.3",
                                         root="/opt/tool/1.2.3",
                                         tool_name="my-tool")
        self.assertEqual(result,
                         "version=1.2.3 root=/opt/tool/1.2.3 name=my-tool")

    def test_deploy_base_path(self):
        template = "base=%DEPLOY_BASE_PATH%"
        result = substitute_placeholders(template, version="1.0.0",
                                         deploy_base_path="/opt/software")
        self.assertEqual(result, "base=/opt/software")

    def test_tool_versions(self):
        template = "tool-a=%tool-a% tool-b=%tool-b%"
        result = substitute_placeholders(
            template, version="1.0.0",
            tool_versions={"tool-a": "1.2.0", "tool-b": "2.0.0"},
        )
        self.assertEqual(result, "tool-a=1.2.0 tool-b=2.0.0")

    def test_tool_loads(self):
        template = "loads:\n%TOOL_LOADS%"
        result = substitute_placeholders(
            template, version="1.0.0",
            tool_versions={"tool-a": "1.2.0", "tool-b": "2.0.0"},
        )
        self.assertIn("module load tool-a/1.2.0", result)
        self.assertIn("module load tool-b/2.0.0", result)

    def test_no_tool_versions(self):
        template = "version=%VERSION%"
        result = substitute_placeholders(template, version="1.0.0")
        self.assertEqual(result, "version=1.0.0")


class TestValidateTemplatePlaceholders(unittest.TestCase):
    """Test validate_template_placeholders()."""

    def test_valid_placeholders(self):
        template = "%VERSION% %tool-a% %tool-b% %TOOL_LOADS%"
        # Should not raise
        validate_template_placeholders(
            template, {"tool-a": "1.0.0", "tool-b": "2.0.0"},
        )

    def test_invalid_placeholder(self):
        template = "%VERSION% %nonexistent%"
        with self.assertRaises(ValueError) as ctx:
            validate_template_placeholders(template, {"tool-a": "1.0.0"})
        self.assertIn("nonexistent", str(ctx.exception))

    def test_standard_placeholders_are_ok(self):
        template = "%VERSION% %ROOT% %TOOL_NAME% %DEPLOY_BASE_PATH% %TOOL_LOADS%"
        # Should not raise — all are standard
        validate_template_placeholders(template, {})


class TestResolveTemplate(unittest.TestCase):
    """Test resolve_template()."""

    def test_repo_template_priority(self):
        tmpdir = tempfile.mkdtemp()
        try:
            tcl = os.path.join(tmpdir, "modulefile.tcl")
            with open(tcl, "w") as f:
                f.write("repo template\n")

            config_template = os.path.join(tmpdir, "config.tcl")
            with open(config_template, "w") as f:
                f.write("config template\n")

            # Repo template should win
            content, label = resolve_template(deploy_dir=tmpdir,
                                              config_template_path=config_template)
            self.assertEqual(content, "repo template\n")
            self.assertEqual(label, "repo modulefile.tcl")
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_config_template_fallback(self):
        tmpdir = tempfile.mkdtemp()
        try:
            config_template = os.path.join(tmpdir, "config.tcl")
            with open(config_template, "w") as f:
                f.write("config template\n")

            # No repo template → config template
            content, label = resolve_template(deploy_dir=tmpdir,
                                              config_template_path=config_template)
            self.assertEqual(content, "config template\n")
            self.assertEqual(label, "config template")
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_no_templates(self):
        content, label = resolve_template()
        self.assertIsNone(content)
        self.assertEqual(label, "default")


class TestGenerateDefaultModulefile(unittest.TestCase):
    """Test generate_default_modulefile()."""

    def test_contains_tool_name(self):
        result = generate_default_modulefile("my-tool", "1.2.3",
                                             "/opt/my-tool/1.2.3")
        self.assertIn("my-tool", result)
        self.assertIn("1.2.3", result)
        self.assertIn("/opt/my-tool/1.2.3", result)
        self.assertIn("#%Module1.0", result)
        self.assertIn("conflict my-tool", result)
        self.assertIn("prepend-path PATH", result)


class TestGenerateToolsetModulefile(unittest.TestCase):
    """Test generate_toolset_modulefile()."""

    def test_default_toolset_template(self):
        result = generate_toolset_modulefile(
            "my-toolset", "1.0.0", "/opt/software",
            {"tool-a": "1.2.0", "tool-b": "2.0.0"},
        )
        self.assertIn("my-toolset", result)
        self.assertIn("1.0.0", result)
        self.assertIn("module load tool-a/1.2.0", result)
        self.assertIn("module load tool-b/2.0.0", result)

    def test_custom_template(self):
        template = "bundle=%TOOL_NAME%/%VERSION%\n%TOOL_LOADS%"
        result = generate_toolset_modulefile(
            "my-toolset", "1.0.0", "/opt/software",
            {"tool-a": "1.2.0"},
            template_content=template,
        )
        self.assertEqual(result, "bundle=my-toolset/1.0.0\nmodule load tool-a/1.2.0")

    def test_custom_template_with_per_tool(self):
        template = "tool-a version: %tool-a%"
        result = generate_toolset_modulefile(
            "my-toolset", "1.0.0", "/opt/software",
            {"tool-a": "1.2.0"},
            template_content=template,
        )
        self.assertEqual(result, "tool-a version: 1.2.0")

    def test_invalid_placeholder_in_template(self):
        template = "bad: %nonexistent%"
        with self.assertRaises(ValueError):
            generate_toolset_modulefile(
                "my-toolset", "1.0.0", "/opt/software",
                {"tool-a": "1.2.0"},
                template_content=template,
            )


class TestWriteModulefile(unittest.TestCase):
    """Test write_modulefile()."""

    def test_write(self):
        tmpdir = tempfile.mkdtemp()
        try:
            mf_path = os.path.join(tmpdir, "mf", "tool", "1.0.0")
            write_modulefile("content", mf_path)
            self.assertTrue(os.path.isfile(mf_path))
            with open(mf_path) as f:
                self.assertEqual(f.read(), "content")
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_dry_run(self):
        mf_path = "/tmp/should_not_exist_modulefile_test"
        write_modulefile("content", mf_path, dry_run=True)
        self.assertFalse(os.path.exists(mf_path))


class TestCopyAndUpdateModulefile(unittest.TestCase):
    """Test copy_and_update_modulefile()."""

    def test_copy_and_update(self):
        tmpdir = tempfile.mkdtemp()
        try:
            src = os.path.join(tmpdir, "1.0.0")
            dst = os.path.join(tmpdir, "1.1.0")
            with open(src, "w") as f:
                f.write("set root /opt/tool/1.0.0\nversion 1.0.0\n")

            copy_and_update_modulefile(src, dst, "1.0.0", "1.1.0")

            with open(dst) as f:
                content = f.read()
            self.assertIn("1.1.0", content)
            self.assertNotIn("1.0.0", content)
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_dry_run(self):
        tmpdir = tempfile.mkdtemp()
        try:
            src = os.path.join(tmpdir, "1.0.0")
            dst = os.path.join(tmpdir, "1.1.0")
            with open(src, "w") as f:
                f.write("content\n")

            copy_and_update_modulefile(src, dst, "1.0.0", "1.1.0",
                                       dry_run=True)
            self.assertFalse(os.path.exists(dst))
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_does_not_rewrite_unrelated_version_substring(self):
        """A path like /opt/support-libs-1.0.0/ must not be touched."""
        tmpdir = tempfile.mkdtemp()
        try:
            src = os.path.join(tmpdir, "1.0.0")
            dst = os.path.join(tmpdir, "1.5.0")
            with open(src, "w") as f:
                f.write(
                    "prepend-path LD_LIBRARY_PATH /opt/support-libs-1.0.0/lib\n"
                    "set root /opt/tool/1.0.0\n"
                    "version 1.0.0\n"
                )
            copy_and_update_modulefile(src, dst, "1.0.0", "1.5.0")
            with open(dst) as f:
                content = f.read()
            self.assertIn("support-libs-1.0.0", content)
            self.assertIn("/opt/tool/1.5.0", content)
            self.assertIn("version 1.5.0", content)
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_prefers_placeholder_when_present(self):
        """Sources with %VERSION% should use the placeholder pass, not regex."""
        tmpdir = tempfile.mkdtemp()
        try:
            src = os.path.join(tmpdir, "1.0.0")
            dst = os.path.join(tmpdir, "1.5.0")
            with open(src, "w") as f:
                # The literal "1.0.0" appears in a context regex would miss,
                # but %VERSION% must be rewritten. And 1.0.0 in the comment
                # should be preserved because we took the placeholder path.
                f.write(
                    "# pinned against libfoo 1.0.0\n"
                    "set root /opt/tool/%VERSION%\n"
                )
            result = copy_and_update_modulefile(src, dst, "1.0.0", "1.5.0")
            with open(dst) as f:
                content = f.read()
            self.assertIn("/opt/tool/1.5.0", content)
            self.assertIn("libfoo 1.0.0", content)
            self.assertIn("placeholder", result)

    # ---------- end of test_prefers_placeholder_when_present ----------
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_returns_strategy_label(self):
        tmpdir = tempfile.mkdtemp()
        try:
            src = os.path.join(tmpdir, "1.0.0")
            dst = os.path.join(tmpdir, "1.1.0")
            with open(src, "w") as f:
                f.write("set root /opt/tool/1.0.0\n")
            label = copy_and_update_modulefile(src, dst, "1.0.0", "1.1.0")
            self.assertIn("1.0.0", label)
        finally:
            import shutil
            shutil.rmtree(tmpdir)


class TestFindLatestModulefile(unittest.TestCase):
    """Test find_latest_modulefile()."""

    def test_finds_latest(self):
        tmpdir = tempfile.mkdtemp()
        try:
            for v in ("1.0.0", "1.1.0", "2.0.0", "1.5.0"):
                with open(os.path.join(tmpdir, v), "w") as f:
                    f.write(f"version {v}\n")
            result = find_latest_modulefile(tmpdir)
            self.assertEqual(os.path.basename(result), "2.0.0")
        finally:
            import shutil
            shutil.rmtree(tmpdir)

    def test_empty_dir(self):
        tmpdir = tempfile.mkdtemp()
        try:
            result = find_latest_modulefile(tmpdir)
            self.assertIsNone(result)
        finally:
            os.rmdir(tmpdir)

    def test_nonexistent_dir(self):
        result = find_latest_modulefile("/nonexistent/dir")
        self.assertIsNone(result)

    def test_ignores_non_semver(self):
        tmpdir = tempfile.mkdtemp()
        try:
            for name in ("readme.txt", ".hidden", "1.0.0-beta"):
                with open(os.path.join(tmpdir, name), "w") as f:
                    f.write("ignored\n")
            result = find_latest_modulefile(tmpdir)
            self.assertIsNone(result)
        finally:
            import shutil
            shutil.rmtree(tmpdir)


if __name__ == "__main__":
    unittest.main()
