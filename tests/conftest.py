"""Shared test fixtures for dev-utils Python tests.

Provides:
  - setup_test_repo(): creates bare remote + working clone
  - setup_bundle_test_repo(): creates parent + 2 sub-tool repos with submodules
  - MockGitLabServer: wraps tests/mock_gitlab.py for use in tests
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TESTS_DIR)
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
MOCK_GITLAB_SCRIPT = os.path.join(TESTS_DIR, "mock_gitlab.py")
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
        git_wrapper_dir: path to directory with git wrapper script
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

    # Install git wrapper that intercepts remote get-url
    git_wrapper_dir = _install_git_wrapper(tmpdir, remote_repo)

    return {
        "tmpdir": tmpdir,
        "remote_repo": remote_repo,
        "work_repo": work_repo,
        "git_wrapper_dir": git_wrapper_dir,
    }


def _install_git_wrapper(tmpdir, remote_repo):
    """Create a git wrapper that fakes the remote URL."""
    wrapper_dir = os.path.join(tmpdir, "bin")
    os.makedirs(wrapper_dir, exist_ok=True)

    real_git = shutil.which("git")

    wrapper_script = os.path.join(wrapper_dir, "git")
    with open(wrapper_script, "w") as f:
        f.write(f"""#!/usr/bin/env bash
# Git wrapper for tests: intercepts 'remote get-url' to return fake GitLab URL
# and rewrites clone commands to use the real bare repo.
if [[ "${{1:-}}" == "remote" && "${{2:-}}" == "get-url" ]]; then
  echo "{FAKE_GITLAB_URL}"
  exit 0
fi
if [[ "${{1:-}}" == "clone" ]]; then
  args=()
  for arg in "$@"; do
    if [[ "$arg" == "{FAKE_GITLAB_URL}" ]]; then
      args+=("{remote_repo}")
    else
      args+=("$arg")
    fi
  done
  exec "{real_git}" "${{args[@]}}"
fi
exec "{real_git}" "$@"
""")
    os.chmod(wrapper_script, 0o755)
    return wrapper_dir


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


def setup_bundle_test_repo(tmpdir=None):
    """Create a bundle test repo with 2 tool submodules.

    Returns dict with keys:
        tmpdir, remote_repo, work_repo, git_wrapper_dir,
        tool_a_remote, tool_b_remote, tool_a_work, tool_b_work
    """
    if tmpdir is None:
        tmpdir = tempfile.mkdtemp(prefix="devutils_bundle_test_")

    # Create tool A repo
    tool_a_remote = os.path.join(tmpdir, "tool-a.git")
    tool_a_work = os.path.join(tmpdir, "tool-a-work")
    _run_git("init", "--bare", tool_a_remote, "--initial-branch=main")
    _run_git("clone", tool_a_remote, tool_a_work)
    _run_git("config", "user.email", "test@example.com", cwd=tool_a_work)
    _run_git("config", "user.name", "Test User", cwd=tool_a_work)
    with open(os.path.join(tool_a_work, "README.md"), "w") as f:
        f.write("tool-a\n")
    _run_git("add", "README.md", cwd=tool_a_work)
    _run_git("commit", "-m", "Initial commit", cwd=tool_a_work)
    _run_git("push", "origin", "main", cwd=tool_a_work)
    _run_git("tag", "-a", "v1.0.0", "-m", "Release v1.0.0", cwd=tool_a_work)
    _run_git("push", "origin", "v1.0.0", cwd=tool_a_work)

    # Create tool B repo
    tool_b_remote = os.path.join(tmpdir, "tool-b.git")
    tool_b_work = os.path.join(tmpdir, "tool-b-work")
    _run_git("init", "--bare", tool_b_remote, "--initial-branch=main")
    _run_git("clone", tool_b_remote, tool_b_work)
    _run_git("config", "user.email", "test@example.com", cwd=tool_b_work)
    _run_git("config", "user.name", "Test User", cwd=tool_b_work)
    with open(os.path.join(tool_b_work, "README.md"), "w") as f:
        f.write("tool-b\n")
    _run_git("add", "README.md", cwd=tool_b_work)
    _run_git("commit", "-m", "Initial commit", cwd=tool_b_work)
    _run_git("push", "origin", "main", cwd=tool_b_work)
    _run_git("tag", "-a", "v2.0.0", "-m", "Release v2.0.0", cwd=tool_b_work)
    _run_git("push", "origin", "v2.0.0", cwd=tool_b_work)

    # Create parent (bundle) repo
    parent_remote = os.path.join(tmpdir, "bundle.git")
    parent_work = os.path.join(tmpdir, "bundle-work")
    _run_git("init", "--bare", parent_remote, "--initial-branch=main")
    _run_git("clone", parent_remote, parent_work)
    _run_git("config", "user.email", "test@example.com", cwd=parent_work)
    _run_git("config", "user.name", "Test User", cwd=parent_work)

    # Add submodules
    tools_dir = os.path.join(parent_work, "tools")
    os.makedirs(tools_dir, exist_ok=True)
    _run_git("submodule", "add", tool_a_remote, "tools/tool-a", cwd=parent_work)
    _run_git("submodule", "add", tool_b_remote, "tools/tool-b", cwd=parent_work)

    # Checkout specific tags in submodules
    _run_git("checkout", "v1.0.0", cwd=os.path.join(parent_work, "tools/tool-a"))
    _run_git("checkout", "v2.0.0", cwd=os.path.join(parent_work, "tools/tool-b"))
    _run_git("add", "-A", cwd=parent_work)
    _run_git("commit", "-m", "Add tool submodules", cwd=parent_work)
    _run_git("push", "origin", "main", cwd=parent_work)

    # Install git wrapper for parent
    git_wrapper_dir = _install_git_wrapper(tmpdir, parent_remote)

    return {
        "tmpdir": tmpdir,
        "remote_repo": parent_remote,
        "work_repo": parent_work,
        "git_wrapper_dir": git_wrapper_dir,
        "tool_a_remote": tool_a_remote,
        "tool_b_remote": tool_b_remote,
        "tool_a_work": tool_a_work,
        "tool_b_work": tool_b_work,
    }


class MockGitLabServer:
    """Wrapper around mock_gitlab.py for use in Python tests."""

    def __init__(self):
        self.state_dir = None
        self.process = None
        self.port = None

    def start(self):
        """Start the mock server."""
        self.state_dir = tempfile.mkdtemp(prefix="mock_gitlab_")
        self.process = subprocess.Popen(
            [sys.executable, MOCK_GITLAB_SCRIPT,
             "--port", "0", "--state-dir", self.state_dir],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

        # Wait for port file
        port_file = os.path.join(self.state_dir, "port")
        for _ in range(50):
            if os.path.exists(port_file):
                with open(port_file) as f:
                    self.port = int(f.read().strip())
                return
            time.sleep(0.1)

        raise RuntimeError("Mock GitLab server failed to start")

    def stop(self):
        """Stop the mock server."""
        if self.process:
            self.process.terminate()
            try:
                self.process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.communicate()
            self.process = None

        if self.state_dir and os.path.isdir(self.state_dir):
            shutil.rmtree(self.state_dir, ignore_errors=True)

    @property
    def api_url(self):
        """Get the API URL for this mock server."""
        return f"http://127.0.0.1:{self.port}/api/v4"

    def trigger_scenario(self, scenario):
        """Trigger a one-shot failure scenario."""
        path = os.path.join(self.state_dir, scenario)
        with open(path, "w") as f:
            f.write("")

    def get_requests(self):
        """Get recorded requests."""
        path = os.path.join(self.state_dir, "requests.json")
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
        return []

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()
