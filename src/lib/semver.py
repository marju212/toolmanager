"""Semantic versioning validation, suggestion, and comparison."""

import re

_SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def validate_semver(version: str) -> None:
    """Validate strict X.Y.Z semver format.

    Returns None if valid, raises ValueError if invalid.
    """
    if not _SEMVER_RE.match(version):
        raise ValueError(f"Invalid semver format: '{version}' (expected X.Y.Z)")


def suggest_versions(current: str) -> dict:
    """Suggest patch, minor, and major version bumps.

    Args:
        current: Current version string (X.Y.Z).

    Returns:
        Dict with keys 'patch', 'minor', 'major' containing suggested versions.

    Raises:
        ValueError: If current is not a valid X.Y.Z semver string.
    """
    validate_semver(current)
    parts = current.split(".")
    major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
    return {
        "patch": f"{major}.{minor}.{patch + 1}",
        "minor": f"{major}.{minor + 1}.0",
        "major": f"{major + 1}.0.0",
    }


