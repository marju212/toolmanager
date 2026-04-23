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
    get_toolset_tool_versions,
    get_toolset_version,
    set_tool_available,
    resolve_manifest_path,
    collect_string_vars,
)

VALID_MANIFEST = {
    "tools": {
        "tool-a": {
            "version": "1.2.0",
            "source": {"type": "git", "url": "git@example.com:group/tool-a.git"},
        },
        "tool-b": {
            "version": "2.0.0",
            "source": {"type": "external", "path": "/opt/tool-b"},
        },
    },
    "toolsets": {
        "science": ["tool-a", "tool-b"],
        "data": ["tool-b"],
    },
}

VALID_MANIFEST_V2 = {
    "tools": {
        "tool-a": {
            "version": "1.2.0",
            "available": ["1.0.0", "1.2.0", "1.3.0"],
            "source": {"type": "git", "url": "git@example.com:group/tool-a.git"},
        },
        "tool-b": {
            "version": "2.0.0",
            "available": ["1.0.0", "2.0.0"],
            "source": {"type": "external", "path": "/opt/tool-b"},
        },
    },
    "toolsets": {
        "science": {
            "version": "1.0.0",
            "tools": {"tool-a": "1.2.0", "tool-b": "2.0.0"},
        },
        "data": {
            "version": "3.0.0",
            "tools": {"tool-b": "1.0.0"},
        },
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

    def test_external_source_missing_path_exits(self):
        bad = {
            "tools": {
                "tool-x": {
                    "version": "1.0.0",
                    "source": {"type": "external"},
                }
            },
            "toolsets": {},
        }
        path = self._write(bad)
        with self.assertRaises(SystemExit):
            load_manifest(path)

    def test_tool_toolset_name_collision_exits(self):
        """Tool and toolset sharing a name would clobber each other's modulefiles."""
        bad = {
            "tools": {
                "science": {
                    "version": "1.0.0",
                    "source": {"type": "git", "url": "x"},
                }
            },
            "toolsets": {"science": ["science"]},
        }
        path = self._write(bad)
        with self.assertRaises(SystemExit):
            load_manifest(path)

    def test_reserved_tool_name_exits(self):
        """A tool named like a standard modulefile placeholder must be rejected."""
        for reserved in ("VERSION", "ROOT", "TOOL_NAME", "DEPLOY_BASE_PATH",
                         "TOOL_LOADS"):
            bad = {
                "tools": {
                    reserved: {
                        "version": "1.0.0",
                        "source": {"type": "git", "url": "x"},
                    }
                },
                "toolsets": {},
            }
            path = self._write(bad)
            with self.assertRaises(SystemExit, msg=f"{reserved} should be rejected"):
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


class TestLoadManifestV2(unittest.TestCase):
    """Tests for new dict-format toolsets and available field."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _write(self, data):
        path = os.path.join(self.tmpdir, "tools.json")
        with open(path, "w") as f:
            json.dump(data, f)
        return path

    def test_load_dict_toolset(self):
        path = self._write(VALID_MANIFEST_V2)
        data = load_manifest(path)
        self.assertIn("science", data["toolsets"])
        self.assertEqual(data["toolsets"]["science"]["version"], "1.0.0")
        self.assertEqual(data["toolsets"]["science"]["tools"]["tool-a"], "1.2.0")

    def test_load_mixed_formats(self):
        """Manifest with both list and dict toolsets loads fine."""
        mixed = copy.deepcopy(VALID_MANIFEST_V2)
        mixed["toolsets"]["legacy"] = ["tool-a"]
        path = self._write(mixed)
        data = load_manifest(path)
        self.assertIsInstance(data["toolsets"]["science"], dict)
        self.assertIsInstance(data["toolsets"]["legacy"], list)

    def test_dict_toolset_missing_tools_exits(self):
        bad = copy.deepcopy(VALID_MANIFEST_V2)
        bad["toolsets"]["science"] = {"version": "1.0.0"}
        path = self._write(bad)
        with self.assertRaises(SystemExit):
            load_manifest(path)

    def test_dict_toolset_missing_version_exits(self):
        bad = copy.deepcopy(VALID_MANIFEST_V2)
        bad["toolsets"]["science"] = {"tools": {"tool-a": "1.0.0"}}
        path = self._write(bad)
        with self.assertRaises(SystemExit):
            load_manifest(path)

    def test_available_field_valid(self):
        path = self._write(VALID_MANIFEST_V2)
        data = load_manifest(path)
        self.assertEqual(data["tools"]["tool-a"]["available"],
                         ["1.0.0", "1.2.0", "1.3.0"])

    def test_available_field_non_list_exits(self):
        bad = copy.deepcopy(VALID_MANIFEST_V2)
        bad["tools"]["tool-a"]["available"] = "1.0.0"
        path = self._write(bad)
        with self.assertRaises(SystemExit):
            load_manifest(path)

    def test_available_field_non_string_items_exits(self):
        bad = copy.deepcopy(VALID_MANIFEST_V2)
        bad["tools"]["tool-a"]["available"] = [1, 2, 3]
        path = self._write(bad)
        with self.assertRaises(SystemExit):
            load_manifest(path)

    def test_dict_toolset_unknown_tool_warns(self):
        data = copy.deepcopy(VALID_MANIFEST_V2)
        data["toolsets"]["science"]["tools"]["nonexistent"] = "1.0.0"
        path = self._write(data)
        result = load_manifest(path)
        self.assertIn("science", result["toolsets"])


class TestGetToolsetToolVersions(unittest.TestCase):
    def test_dict_format(self):
        data = copy.deepcopy(VALID_MANIFEST_V2)
        versions = get_toolset_tool_versions(data, "science")
        self.assertEqual(versions, {"tool-a": "1.2.0", "tool-b": "2.0.0"})

    def test_legacy_list_format(self):
        data = copy.deepcopy(VALID_MANIFEST)
        versions = get_toolset_tool_versions(data, "science")
        self.assertEqual(versions, {"tool-a": "1.2.0", "tool-b": "2.0.0"})

    def test_legacy_list_missing_version(self):
        data = copy.deepcopy(VALID_MANIFEST)
        del data["tools"]["tool-a"]["version"]
        versions = get_toolset_tool_versions(data, "science")
        self.assertEqual(versions, {"tool-a": "", "tool-b": "2.0.0"})

    def test_unknown_toolset_exits(self):
        data = copy.deepcopy(VALID_MANIFEST)
        with self.assertRaises(SystemExit):
            get_toolset_tool_versions(data, "nonexistent")


class TestGetToolsetVersion(unittest.TestCase):
    def test_dict_format(self):
        data = copy.deepcopy(VALID_MANIFEST_V2)
        self.assertEqual(get_toolset_version(data, "science"), "1.0.0")

    def test_legacy_list_format_returns_empty(self):
        data = copy.deepcopy(VALID_MANIFEST)
        self.assertEqual(get_toolset_version(data, "science"), "")


class TestSetToolAvailable(unittest.TestCase):
    def test_set_available(self):
        data = copy.deepcopy(VALID_MANIFEST)
        set_tool_available(data, "tool-a", ["1.0.0", "1.2.0", "2.0.0"])
        self.assertEqual(data["tools"]["tool-a"]["available"],
                         ["1.0.0", "1.2.0", "2.0.0"])

    def test_set_available_overwrites(self):
        data = copy.deepcopy(VALID_MANIFEST_V2)
        set_tool_available(data, "tool-a", ["3.0.0"])
        self.assertEqual(data["tools"]["tool-a"]["available"], ["3.0.0"])


# ---------------------------------------------------------------------------
# collect_string_vars
# ---------------------------------------------------------------------------

class TestCollectStringVars(unittest.TestCase):
    def test_root_only(self):
        data = {"org": "acme", "env": "prod", "tools": {}}
        result = collect_string_vars(data)
        self.assertEqual(result["org"], "acme")
        self.assertEqual(result["env"], "prod")

    def test_tool_overrides_root(self):
        data = {"org": "acme", "env": "prod"}
        tool = {"env": "dev", "version": "1.0.0"}
        result = collect_string_vars(data, tool)
        self.assertEqual(result["org"], "acme")
        self.assertEqual(result["env"], "dev")

    def test_three_levels(self):
        data = {"org": "acme", "env": "prod", "region": "us"}
        toolset = {"env": "staging"}
        tool = {"region": "eu"}
        result = collect_string_vars(data, toolset, tool)
        self.assertEqual(result["org"], "acme")
        self.assertEqual(result["env"], "staging")
        self.assertEqual(result["region"], "eu")

    def test_no_string_keys(self):
        data = {"tools": {}, "toolsets": {}}
        result = collect_string_vars(data)
        self.assertEqual(result, {})

    def test_skips_non_string_values(self):
        data = {"org": "acme", "tools": {"a": {}}, "count": 5}
        result = collect_string_vars(data)
        self.assertIn("org", result)
        self.assertNotIn("tools", result)
        self.assertNotIn("count", result)

    def test_trims_whitespace(self):
        data = {"org": "  acme  ", "env": " prod\n"}
        result = collect_string_vars(data)
        self.assertEqual(result["org"], "acme")
        self.assertEqual(result["env"], "prod")

    def test_empty_scopes(self):
        data = {"org": "acme"}
        result = collect_string_vars(data, {}, {})
        self.assertEqual(result["org"], "acme")


if __name__ == "__main__":
    unittest.main()
