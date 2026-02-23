"""Semantic versioning validation, suggestion, and comparison."""

import re
from typing import Optional

_SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def validate_semver(version: str) -> bool:
    """Validate strict X.Y.Z semver format.

    Returns True if valid, raises ValueError if invalid.
    """
    if not _SEMVER_RE.match(version):
        raise ValueError(f"Invalid semver format: '{version}' (expected X.Y.Z)")
    return True


def suggest_versions(current: str) -> dict:
    """Suggest patch, minor, and major version bumps.

    Args:
        current: Current version string (X.Y.Z).

    Returns:
        Dict with keys 'patch', 'minor', 'major' containing suggested versions.
    """
    parts = current.split(".")
    major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
    return {
        "patch": f"{major}.{minor}.{patch + 1}",
        "minor": f"{major}.{minor + 1}.0",
        "major": f"{major + 1}.0.0",
    }


def compare_versions(a: str, b: str) -> int:
    """Compare two semver strings.

    Returns:
        -1 if a < b, 0 if a == b, 1 if a > b.
    """
    pa = tuple(int(x) for x in a.split("."))
    pb = tuple(int(x) for x in b.split("."))
    if pa < pb:
        return -1
    elif pa > pb:
        return 1
    return 0
