"""Git operations via subprocess.

Thin wrappers around ``git`` CLI commands used by both the release and
deploy tools.  Every call goes through ``_run_git`` which adds a
configurable timeout (default 120 s, override with
``TOOLMANAGER_GIT_TIMEOUT`` env var) so a stalled server cannot hang
the process indefinitely.

Functions fall into two groups:

**Release helpers** (used by ``release.py``):
    ``check_branch``          — validate branch, clean tree, remote sync
    ``get_latest_version``    — find highest semver tag
    ``check_version_available`` — ensure a tag does not already exist
    ``generate_changelog``    — commit log since last tag
    ``tag_release``           — create + push an annotated tag

**General utilities**:
    ``get_repo_root``         — locate the ``.git`` directory
"""

import os
import subprocess

from .log import log_info, log_warn, log_error, log_success
from .semver import validate_semver

# Seconds before a git subprocess is killed.  Override with env var.
_GIT_TIMEOUT = int(os.environ.get("TOOLMANAGER_GIT_TIMEOUT", "120"))


def _run_git(*args: str, cwd: str | None = None, check: bool = True,
             capture: bool = True) -> subprocess.CompletedProcess:
    """Run ``git <args>`` as a subprocess and return the result.

    All git calls in this module go through here so that timeout, text
    mode, and output capture are applied consistently.

    Args:
        args:    Git sub-command and arguments (e.g. ``"tag", "--list"``).
        cwd:     Working directory for the command (``None`` = inherit).
        check:   If ``True``, raise ``CalledProcessError`` on non-zero exit.
        capture: If ``True``, capture stdout/stderr instead of printing.

    Raises:
        subprocess.CalledProcessError: When *check* is ``True`` and git
            exits with a non-zero status.
        subprocess.TimeoutExpired: When the command exceeds
            ``_GIT_TIMEOUT`` seconds.
    """
    cmd = ["git"] + list(args)
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=capture,
        text=True,
        check=check,
        timeout=_GIT_TIMEOUT,
    )


def get_repo_root(path: str | None = None) -> str:
    """Return the absolute path of the repository root.

    Falls back to the current working directory if ``git rev-parse``
    fails (e.g. not inside a repo).
    """
    try:
        result = _run_git("rev-parse", "--show-toplevel", cwd=path)
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return os.getcwd()


def check_branch(default_branch: str, remote: str, cwd: str | None = None) -> None:
    """Verify the repo is ready for a release.

    Performs four checks in order:

    1. We are inside a git repository.
    2. The current branch is *default_branch* (or a detached HEAD in CI).
    3. The working tree has no uncommitted changes.
    4. The local HEAD matches ``<remote>/<default_branch>`` (fetched first).

    Raises ``SystemExit`` on the first check that fails.
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
    """Find the highest semver version among existing tags.

    Scans tags matching ``<tag_prefix>*`` (e.g. ``v*``), strips the prefix,
    validates each as strict semver, and returns the highest one.

    Returns:
        Version string without prefix (e.g. ``"1.2.3"``), or ``"0.0.0"``
        if no valid version tags exist (first release).
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
    """Ensure the tag ``<tag_prefix><version>`` does not already exist.

    This prevents accidentally re-tagging an existing release.
    Raises ``SystemExit`` if the tag is found in the local repo.
    """
    tag_name = f"{tag_prefix}{version}"
    result = _run_git("rev-parse", tag_name, cwd=cwd, check=False)
    if result.returncode == 0:
        log_error(f"Tag '{tag_name}' already exists.")
        raise SystemExit(1)


def generate_changelog(from_version: str, tag_prefix: str,
                       cwd: str | None = None) -> str:
    """Build a bullet-list changelog from git commits.

    Lists every non-merge commit between ``<tag_prefix><from_version>``
    and ``HEAD``.  If that tag does not exist (first release), all commits
    are included.  Returns ``"- No changes recorded"`` when the log is empty.
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
    """Create an annotated tag and push it to *remote*.

    The tag message includes the version number, an optional free-text
    *description*, and the *changelog*.  In dry-run mode the tag is not
    created — only a log line is printed.
    """
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
