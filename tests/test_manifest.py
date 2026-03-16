"""Tests for src/lib/manifest.py."""

import copy
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from lib.manifest import (
    load_manifest,
    save_manifest,
    get_tool,
    set_tool_version,
    get_toolset,
    resolve_manifest_path,
)

VALID_MANIFEST = {
    "tools": {
        "tool-a": {
            "version": "1.2.0",
            "source": {"type": "git", "url": "git@example.com:group/tool-a.git"},
        },
        "tool-b": {
            "version": "2.0.0",
            "source": {"type": "disk", "path": "/opt/tool-b"},
        },
    },
    "toolsets": {
        "science": ["tool-a", "tool-b"],
        "data": ["tool-b"],
    },
}


class TestLoadManifest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _write(self, data):
        path = os.path.join(self.tmpdir, "tools.json")
        with open(path, "w") as f:
            json.dump(data, f)
        return path

    def test_load_valid(self):
        path = self._write(VALID_MANIFEST)
        data = load_manifest(path)
        self.assertIn("tool-a", data["tools"])
        self.assertIn("tool-b", data["tools"])
        self.assertIn("science", data["toolsets"])
        self.assertIn("data", data["toolsets"])

    def test_load_missing_source_exits(self):
        bad = {
            "tools": {"tool-x": {"version": "1.0.0"}},
            "toolsets": {},
        }
        path = self._write(bad)
        with self.assertRaises(SystemExit):
            load_manifest(path)

    def test_load_missing_source_type_exits(self):
        bad = {
            "tools": {"tool-x": {"version": "1.0.0", "source": {"url": "x"}}},
            "toolsets": {},
        }
        path = self._write(bad)
        with self.assertRaises(SystemExit):
            load_manifest(path)

    def test_load_nonexistent_file_exits(self):
        with self.assertRaises(SystemExit):
            load_manifest(os.path.join(self.tmpdir, "nonexistent.json"))

    def test_load_unknown_source_type_exits(self):
        """Unknown source type must error, not just warn."""
        bad = {
            "tools": {
                "tool-x": {
                    "version": "1.0.0",
                    "source": {"type": "ftp"},
                }
            },
            "toolsets": {},
        }
        path = self._write(bad)
        with self.assertRaises(SystemExit):
            load_manifest(path)

    def test_toolset_unknown_tool_warns_not_exits(self):
        """Unknown tool in toolset should warn but not raise SystemExit."""
        data = {
            "tools": {
                "tool-a": {
                    "version": "1.0.0",
                    "source": {"type": "git", "url": "x"},
                }
            },
            "toolsets": {"bad-set": ["tool-a", "tool-missing"]},
        }
        path = self._write(data)
        result = load_manifest(path)
        self.assertIn("bad-set", result["toolsets"])

    def test_tool_name_with_path_separator_exits(self):
        """Tool name containing '/' must be rejected to prevent path traversal."""
        bad = {
            "tools": {
                "../etc/passwd": {
                    "version": "1.0.0",
                    "source": {"type": "git", "url": "x"},
                }
            },
            "toolsets": {},
        }
        path = self._write(bad)
        with self.assertRaises(SystemExit):
            load_manifest(path)

    def test_toolset_name_with_path_separator_exits(self):
        """Toolset name containing '..' must be rejected to prevent path traversal."""
        bad = {
            "tools": {
                "tool-a": {
                    "version": "1.0.0",
                    "source": {"type": "git", "url": "x"},
                }
            },
            "toolsets": {"../evil": ["tool-a"]},
        }
        path = self._write(bad)
        with self.assertRaises(SystemExit):
            load_manifest(path)

    def test_missing_tools_and_toolsets_default_to_empty(self):
        """Manifest without 'tools'/'toolsets' keys gets empty dicts."""
        path = self._write({})
        data = load_manifest(path)
        self.assertEqual(data["tools"], {})
        self.assertEqual(data["toolsets"], {})

    def test_git_source_missing_url_exits(self):
        bad = {
            "tools": {
                "tool-x": {
                    "version": "1.0.0",
                    "source": {"type": "git"},
                }
            },
            "toolsets": {},
        }
        path = self._write(bad)
        with self.assertRaises(SystemExit):
            load_manifest(path)

    def test_disk_source_missing_path_exits(self):
        bad = {
            "tools": {
                "tool-x": {
                    "version": "1.0.0",
                    "source": {"type": "disk"},
                }
            },
            "toolsets": {},
        }
        path = self._write(bad)
        with self.assertRaises(SystemExit):
            load_manifest(path)


class TestSetToolVersion(unittest.TestCase):
    def test_set_tool_version(self):
        data = copy.deepcopy(VALID_MANIFEST)
        set_tool_version(data, "tool-a", "1.3.0")
        self.assertEqual(data["tools"]["tool-a"]["version"], "1.3.0")

    def test_other_fields_untouched(self):
        data = copy.deepcopy(VALID_MANIFEST)
        set_tool_version(data, "tool-a", "1.3.0")
        self.assertEqual(data["tools"]["tool-a"]["source"]["type"], "git")
        self.assertEqual(
            data["tools"]["tool-a"]["source"]["url"],
            "git@example.com:group/tool-a.git",
        )

    def test_other_tools_untouched(self):
        data = copy.deepcopy(VALID_MANIFEST)
        set_tool_version(data, "tool-a", "1.3.0")
        self.assertEqual(data["tools"]["tool-b"]["version"], "2.0.0")


class TestSaveAndReload(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_save_and_reload(self):
        path = os.path.join(self.tmpdir, "tools.json")
        data = copy.deepcopy(VALID_MANIFEST)
        save_manifest(path, data)
        reloaded = load_manifest(path)
        self.assertEqual(reloaded["tools"]["tool-a"]["version"], "1.2.0")
        self.assertEqual(reloaded["tools"]["tool-b"]["version"], "2.0.0")
        self.assertEqual(reloaded["toolsets"]["science"], ["tool-a", "tool-b"])

    def test_save_is_valid_json(self):
        path = os.path.join(self.tmpdir, "tools.json")
        save_manifest(path, copy.deepcopy(VALID_MANIFEST))
        with open(path) as f:
            parsed = json.load(f)
        self.assertIn("tools", parsed)
        self.assertIn("toolsets", parsed)


class TestGetTool(unittest.TestCase):
    def test_get_existing_tool(self):
        data = copy.deepcopy(VALID_MANIFEST)
        tool = get_tool(data, "tool-a")
        self.assertEqual(tool["version"], "1.2.0")

    def test_get_missing_tool_exits(self):
        data = copy.deepcopy(VALID_MANIFEST)
        with self.assertRaises(SystemExit):
            get_tool(data, "nonexistent")


class TestGetToolset(unittest.TestCase):
    def test_get_existing_toolset(self):
        data = copy.deepcopy(VALID_MANIFEST)
        ts = get_toolset(data, "science")
        self.assertEqual(ts, ["tool-a", "tool-b"])

    def test_get_unknown_toolset_exits(self):
        data = copy.deepcopy(VALID_MANIFEST)
        with self.assertRaises(SystemExit):
            get_toolset(data, "nonexistent")


class TestResolveManifestPath(unittest.TestCase):
    def test_uses_config_tools_manifest(self):
        from lib.config import Config
        config = Config(tools_manifest="/some/path/tools.json")
        self.assertEqual(resolve_manifest_path(config), "/some/path/tools.json")

    def test_falls_back_to_cwd(self):
        from lib.config import Config
        config = Config()
        expected = os.path.join(os.getcwd(), "tools.json")
        self.assertEqual(resolve_manifest_path(config), expected)


if __name__ == "__main__":
    unittest.main()
