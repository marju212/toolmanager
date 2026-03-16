"""Tests for src/lib/semver.py."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from lib.semver import validate_semver, suggest_versions


class TestValidateSemver(unittest.TestCase):
    """Test validate_semver()."""

    def test_valid_versions(self):
        for v in ("0.0.0", "1.2.3", "10.20.30", "0.0.1", "9.99.999"):
            validate_semver(v)  # must not raise

    def test_invalid_prefix(self):
        with self.assertRaises(ValueError):
            validate_semver("v1.2.3")

    def test_invalid_two_parts(self):
        with self.assertRaises(ValueError):
            validate_semver("1.2")

    def test_invalid_one_part(self):
        with self.assertRaises(ValueError):
            validate_semver("1")

    def test_invalid_prerelease(self):
        with self.assertRaises(ValueError):
            validate_semver("1.2.3-beta")

    def test_invalid_prerelease_rc(self):
        with self.assertRaises(ValueError):
            validate_semver("1.2.3-rc1")

    def test_invalid_empty(self):
        with self.assertRaises(ValueError):
            validate_semver("")

    def test_invalid_alpha(self):
        with self.assertRaises(ValueError):
            validate_semver("abc")

    def test_invalid_leading_v(self):
        with self.assertRaises(ValueError):
            validate_semver("v0.0.0")

    def test_invalid_four_parts(self):
        with self.assertRaises(ValueError):
            validate_semver("1.2.3.4")


class TestSuggestVersions(unittest.TestCase):
    """Test suggest_versions()."""

    def test_basic_suggestions(self):
        result = suggest_versions("1.2.3")
        self.assertEqual(result["patch"], "1.2.4")
        self.assertEqual(result["minor"], "1.3.0")
        self.assertEqual(result["major"], "2.0.0")

    def test_zero_version(self):
        result = suggest_versions("0.0.0")
        self.assertEqual(result["patch"], "0.0.1")
        self.assertEqual(result["minor"], "0.1.0")
        self.assertEqual(result["major"], "1.0.0")

    def test_large_version(self):
        result = suggest_versions("9.99.999")
        self.assertEqual(result["patch"], "9.99.1000")
        self.assertEqual(result["minor"], "9.100.0")
        self.assertEqual(result["major"], "10.0.0")



if __name__ == "__main__":
    unittest.main()
