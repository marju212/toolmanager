"""Semantic versioning helpers.

This module enforces **strict** semver: exactly three dot-separated
integers (``X.Y.Z``).  Pre-release suffixes, build metadata, and leading
zeros are intentionally rejected to keep version comparisons simple — the
rest of the codebase sorts versions with ``tuple(int(x) for x in v.split("."))``.
"""

import re

# Matches exactly "1.2.3" — no leading zeros, no pre-release, no build metadata.
_SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def validate_semver(version: str) -> None:
    """Check that *version* is a valid ``X.Y.Z`` string.

    Raises ``ValueError`` with a descriptive message if the format is wrong.
    Does nothing (returns ``None``) when valid.
    """
    if not _SEMVER_RE.match(version):
        raise ValueError(f"Invalid semver format: '{version}' (expected X.Y.Z)")


def suggest_versions(current: str) -> dict:
    """Given a current version, return the three possible next versions.

    Example::

        >>> suggest_versions("1.2.3")
        {"patch": "1.2.4", "minor": "1.3.0", "major": "2.0.0"}

    Args:
        current: The current version string (must pass ``validate_semver``).

    Returns:
        Dict with keys ``patch``, ``minor``, ``major`` — each a version string.

    Raises:
        ValueError: If *current* is not a valid ``X.Y.Z`` string.
    """
    validate_semver(current)
    parts = current.split(".")
    major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
    return {
        "patch": f"{major}.{minor}.{patch + 1}",
        "minor": f"{major}.{minor + 1}.0",
        "major": f"{major + 1}.0.0",
    }


