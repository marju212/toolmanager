"""Shared test fixtures for dev-utils Python tests.

Provides:
  - setup_test_repo(): creates bare remote + working clone
  - install_git_mock() / uninstall_git_mock(): Python-level git interception
"""

import os
import shutil
import subprocess
import sys
import tempfile

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TESTS_DIR)
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
FAKE_GITLAB_URL = "https://gitlab.example.com/group/test-project.git"


def _run_git(*args, cwd=None, check=True):
    """Run git command helper."""
    env = os.environ.copy()
    env["GIT_CONFIG_GLOBAL"] = ""
    env.setdefault("GIT_PROTOCOL_FILE_ALLOW", "always")
    return subprocess.run(
        ["git", "-c", "protocol.file.allow=always"] + list(args),
        cwd=cwd, capture_output=True, text=True, check=check,
        env=env,
    )


def setup_test_repo(tmpdir=None):
    """Create a bare remote + working clone for testing.

    Returns dict with keys:
        tmpdir: root temp directory
        remote_repo: path to bare remote
        work_repo: path to working clone
    """
    if tmpdir is None:
        tmpdir = tempfile.mkdtemp(prefix="devutils_test_")

    remote_repo = os.path.join(tmpdir, "remote.git")
    work_repo = os.path.join(tmpdir, "work")

    # Create bare remote
    _run_git("init", "--bare", remote_repo, "--initial-branch=main")

    # Create working clone
    _run_git("clone", remote_repo, work_repo)

    # Configure git user
    _run_git("config", "user.email", "test@example.com", cwd=work_repo)
    _run_git("config", "user.name", "Test User", cwd=work_repo)

    # Initial commit
    readme = os.path.join(work_repo, "README.md")
    with open(readme, "w") as f:
        f.write("init\n")
    _run_git("add", "README.md", cwd=work_repo)
    _run_git("commit", "-m", "Initial commit", cwd=work_repo)
    _run_git("push", "origin", "main", cwd=work_repo)

    return {
        "tmpdir": tmpdir,
        "remote_repo": remote_repo,
        "work_repo": work_repo,
    }


def install_git_mock(remote_repo_path, fake_url=FAKE_GITLAB_URL):
    """Install Python-level git mock that intercepts remote get-url and clone.

    Monkeypatches lib.git._run_git so that:
      - 'git remote get-url ...' returns the fake GitLab URL
      - 'git clone ... <fake_url> ...' rewrites to use the real local repo path
      - All other git commands pass through to the real _run_git

    Returns a dict to pass to uninstall_git_mock() for cleanup.
    """
    import lib.git as git_module

    original_run_git = git_module._run_git

    def mock_run_git(*args, cwd=None, check=True, capture=True):
        # Intercept: git remote get-url <remote>
        if len(args) >= 2 and args[0] == "remote" and args[1] == "get-url":
            return subprocess.CompletedProcess(
                args=["git"] + list(args),
                returncode=0,
                stdout=fake_url + "\n",
                stderr="",
            )

        # Intercept: git clone ... <fake_url> ... -> rewrite to local path
        if len(args) >= 1 and args[0] == "clone":
            new_args = tuple(
                remote_repo_path if a == fake_url else a for a in args
            )
            return original_run_git(*new_args, cwd=cwd, check=check,
                                    capture=capture)

        # All other git commands: pass through
        return original_run_git(*args, cwd=cwd, check=check, capture=capture)

    git_module._run_git = mock_run_git

    return {
        "original_run_git": original_run_git,
        "git_module": git_module,
    }


def uninstall_git_mock(mock_info):
    """Restore original _run_git after install_git_mock()."""
    mock_info["git_module"]._run_git = mock_info["original_run_git"]


def add_test_commit(work_repo, msg="Test commit"):
    """Add a commit to the working repo."""
    changes = os.path.join(work_repo, "changes.txt")
    with open(changes, "a") as f:
        f.write(msg + "\n")
    _run_git("add", "changes.txt", cwd=work_repo)
    _run_git("commit", "-m", msg, cwd=work_repo)


def create_test_tag(work_repo, tag, msg=None):
    """Create a tag and push it."""
    if msg is None:
        msg = f"Release {tag}"
    _run_git("tag", "-a", tag, "-m", msg, cwd=work_repo)
    _run_git("push", "origin", tag, cwd=work_repo)


def push_test_commits(work_repo):
    """Push commits to remote."""
    _run_git("push", "origin", "main", cwd=work_repo)
    _run_git("fetch", "origin", "--quiet", cwd=work_repo)
