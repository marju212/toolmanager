"""Git operations via subprocess."""

import os
import re
import subprocess
from typing import List, Optional, Tuple

from .log import log_info, log_warn, log_error, log_success

_SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def _run_git(*args: str, cwd: Optional[str] = None, check: bool = True,
             capture: bool = True) -> subprocess.CompletedProcess:
    """Run a git command and return the result."""
    cmd = ["git"] + list(args)
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=capture,
        text=True,
        check=check,
    )


# Public alias for use by callers outside this module.
run_git = _run_git


def get_repo_root(path: Optional[str] = None) -> str:
    """Get the repository root directory."""
    try:
        result = _run_git("rev-parse", "--show-toplevel", cwd=path)
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return os.getcwd()


def check_branch(default_branch: str, remote: str, cwd: Optional[str] = None) -> None:
    """Validate repository state: correct branch, clean tree, synced with remote.

    Raises SystemExit on validation failure.
    """
    log_info("Checking repository state...")

    # Must be inside a git repo
    try:
        _run_git("rev-parse", "--is-inside-work-tree", cwd=cwd)
    except subprocess.CalledProcessError:
        log_error("Not inside a git repository.")
        raise SystemExit(1)

    # Check current branch or detached HEAD
    detached_head = False
    try:
        result = _run_git("symbolic-ref", "--short", "HEAD", cwd=cwd)
        current_branch = result.stdout.strip()
        if current_branch != default_branch:
            log_error(f"Must be on '{default_branch}' branch "
                      f"(currently on '{current_branch}').")
            raise SystemExit(1)
    except subprocess.CalledProcessError:
        detached_head = True
        log_info("Detached HEAD detected (common in CI environments).")

    # Working tree must be clean
    result = _run_git("status", "--porcelain", cwd=cwd)
    if result.stdout.strip():
        log_error("Working tree is dirty. Commit or stash changes first.")
        raise SystemExit(1)

    # Fetch latest from remote
    log_info(f"Fetching from {remote}...")
    try:
        _run_git("fetch", remote, "--tags", "--quiet", cwd=cwd)
    except subprocess.CalledProcessError:
        log_error(f"Failed to fetch from '{remote}'. "
                  "Check credentials and network connectivity.")
        raise SystemExit(1)

    # Must be in sync with remote
    result = _run_git("rev-parse", "HEAD", cwd=cwd)
    local_sha = result.stdout.strip()

    try:
        result = _run_git("rev-parse", f"{remote}/{default_branch}", cwd=cwd)
        remote_sha = result.stdout.strip()
    except subprocess.CalledProcessError:
        remote_sha = ""

    if not remote_sha:
        log_warn(f"Remote branch '{remote}/{default_branch}' not found. "
                 "Continuing anyway.")
    elif local_sha != remote_sha:
        if detached_head:
            log_error(f"HEAD is not at the tip of '{remote}/{default_branch}'.")
            log_error(f"Ensure the CI job checks out the latest "
                      f"'{default_branch}' commit.")
        else:
            log_error(f"Local '{default_branch}' is not in sync with "
                      f"'{remote}/{default_branch}'.")
            log_error("Pull or push changes before releasing.")
        raise SystemExit(1)

    log_success("Repository is clean and in sync.")


def get_latest_version(tag_prefix: str, cwd: Optional[str] = None) -> str:
    """Get the latest strict semver version from tags.

    Returns version string without prefix (e.g. '1.2.3'), or '0.0.0' if no tags.
    """
    try:
        result = _run_git("tag", "--list", f"{tag_prefix}*",
                          "--sort=-v:refname", cwd=cwd)
    except subprocess.CalledProcessError:
        log_info("No version tags found — treating as first release.")
        return "0.0.0"

    for line in result.stdout.strip().splitlines():
        tag = line.strip()
        if not tag:
            continue
        version = tag[len(tag_prefix):] if tag.startswith(tag_prefix) else tag
        if _SEMVER_RE.match(version):
            return version

    log_info("No version tags found — treating as first release.")
    return "0.0.0"


def check_version_available(version: str, tag_prefix: str,
                            cwd: Optional[str] = None) -> None:
    """Check that the tag doesn't already exist locally.

    Raises SystemExit if the version is taken.
    """
    tag_name = f"{tag_prefix}{version}"
    result = _run_git("rev-parse", tag_name, cwd=cwd, check=False)
    if result.returncode == 0:
        log_error(f"Tag '{tag_name}' already exists.")
        raise SystemExit(1)


def generate_changelog(from_version: str, tag_prefix: str,
                       cwd: Optional[str] = None) -> str:
    """Generate markdown changelog from commits since the given version tag.

    Returns changelog string.
    """
    log_info("Generating changelog...")
    current_tag = f"{tag_prefix}{from_version}"

    result = _run_git("rev-parse", current_tag, cwd=cwd, check=False)
    if result.returncode == 0:
        result = _run_git("log", f"{current_tag}..HEAD",
                          "--pretty=format:- %s (%h)", "--no-merges", cwd=cwd)
    else:
        result = _run_git("log", "--pretty=format:- %s (%h)", "--no-merges",
                          cwd=cwd)

    changelog = result.stdout.strip()
    if not changelog:
        changelog = "- No changes recorded"

    return changelog


def tag_release(tag_name: str, version: str, changelog: str, remote: str,
                dry_run: bool = False, cwd: Optional[str] = None,
                description: str = "") -> None:
    """Create and push an annotated tag."""
    log_info(f"Creating annotated tag '{tag_name}'...")

    if dry_run:
        log_info(f"[dry-run] Would create and push tag '{tag_name}'")
        return

    if description:
        message = f"Release {version}\n\n{description}\n\nChangelog:\n{changelog}"
    else:
        message = f"Release {version}\n\nChangelog:\n{changelog}"
    _run_git("tag", "-a", tag_name, "-m", message, cwd=cwd)
    _run_git("push", remote, tag_name, cwd=cwd)
    log_success(f"Tag '{tag_name}' created and pushed.")


def get_remote_url(remote: str, cwd: Optional[str] = None) -> str:
    """Get the URL of a git remote."""
    result = _run_git("remote", "get-url", remote, cwd=cwd, check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def parse_project_path(remote_url: str) -> str:
    """Extract project path from a git remote URL.

    Supports SSH and HTTPS formats, including nested groups.
    Returns empty string if parsing fails.
    """
    # SSH: git@gitlab.com:group/subgroup/project.git
    m = re.match(r"^git@[^:]+:(.+?)(?:\.git)?$", remote_url)
    if m:
        return m.group(1)

    # HTTPS: https://gitlab.com/group/subgroup/project.git
    m = re.match(r"^https?://[^/]+/(.+?)(?:\.git)?$", remote_url)
    if m:
        return m.group(1)

    return ""


def extract_tool_name(remote: str, cwd: Optional[str] = None) -> Tuple[str, str]:
    """Extract tool name from the git remote URL.

    Returns (tool_name, remote_url) tuple.
    """
    remote_url = get_remote_url(remote, cwd=cwd)
    if not remote_url:
        log_error(f"Cannot determine remote URL for '{remote}'.")
        raise SystemExit(1)

    project_path = parse_project_path(remote_url)
    if not project_path:
        log_error(f"Cannot parse project path from remote URL: {remote_url}")
        raise SystemExit(1)

    tool_name = project_path.rsplit("/", 1)[-1]
    return tool_name, remote_url


