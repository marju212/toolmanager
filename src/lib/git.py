"""Git operations via subprocess."""

import os
import subprocess

from .log import log_info, log_warn, log_error, log_success
from .semver import validate_semver


def _run_git(*args: str, cwd: str | None = None, check: bool = True,
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


def get_repo_root(path: str | None = None) -> str:
    """Get the repository root directory."""
    try:
        result = _run_git("rev-parse", "--show-toplevel", cwd=path)
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return os.getcwd()


def check_branch(default_branch: str, remote: str, cwd: str | None = None) -> None:
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


def get_latest_version(tag_prefix: str, cwd: str | None = None) -> str:
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
        try:
            validate_semver(version)
            return version
        except ValueError:
            pass

    log_info("No version tags found — treating as first release.")
    return "0.0.0"


def check_version_available(version: str, tag_prefix: str,
                            cwd: str | None = None) -> None:
    """Check that the tag doesn't already exist locally.

    Raises SystemExit if the version is taken.
    """
    tag_name = f"{tag_prefix}{version}"
    result = _run_git("rev-parse", tag_name, cwd=cwd, check=False)
    if result.returncode == 0:
        log_error(f"Tag '{tag_name}' already exists.")
        raise SystemExit(1)


def generate_changelog(from_version: str, tag_prefix: str,
                       cwd: str | None = None) -> str:
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
                dry_run: bool = False, cwd: str | None = None,
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
