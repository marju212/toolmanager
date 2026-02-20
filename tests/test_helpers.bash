#!/usr/bin/env bash
#
# test_helpers.bash - Shared helpers for bats tests of release.sh
#

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$TESTS_DIR/.." && pwd)"
RELEASE_SCRIPT="$PROJECT_ROOT/scripts/release.sh"
MOCK_GITLAB="$TESTS_DIR/mock_gitlab.py"

FAKE_GITLAB_URL="https://gitlab.example.com/group/test-project.git"

# ─── Temporary git repo helpers ─────────────────────────────────────────────────

# Create a bare "remote" repo and a working clone that mimics a real
# GitLab project setup. Sets:
#   TEST_TMPDIR   - root temp directory (cleaned up in teardown)
#   REMOTE_REPO   - path to bare remote
#   WORK_REPO     - path to working clone
#   ORIGINAL_DIR  - original directory before cd
setup_test_repo() {
  TEST_TMPDIR="$(mktemp -d)"
  REMOTE_REPO="$TEST_TMPDIR/remote.git"
  WORK_REPO="$TEST_TMPDIR/work"
  ORIGINAL_DIR="$(pwd)"

  # Create bare remote
  git init --bare "$REMOTE_REPO" --initial-branch=main >/dev/null 2>&1

  # Create working clone
  git clone "$REMOTE_REPO" "$WORK_REPO" >/dev/null 2>&1
  cd "$WORK_REPO"

  # Configure git user for commits
  git config user.email "test@example.com"
  git config user.name "Test User"

  # Initial commit so HEAD exists
  echo "init" > README.md
  git add README.md
  git commit -m "Initial commit" >/dev/null 2>&1
  git push origin main >/dev/null 2>&1

  # Install a git wrapper that intercepts `git remote get-url` to return
  # a GitLab-style HTTPS URL, while all other git commands use the real
  # bare repo as origin. This is needed because `git remote get-url`
  # expands insteadOf configs, making transparent URL rewriting impossible.
  _install_git_wrapper
}

# Tear down the temp repo
teardown_test_repo() {
  if [[ -n "${ORIGINAL_DIR:-}" ]]; then
    cd "$ORIGINAL_DIR"
  fi
  if [[ -n "${TEST_TMPDIR:-}" && -d "$TEST_TMPDIR" ]]; then
    rm -rf "$TEST_TMPDIR"
  fi
  # Restore PATH if wrapper was installed
  if [[ -n "${_ORIGINAL_PATH:-}" ]]; then
    export PATH="$_ORIGINAL_PATH"
    _ORIGINAL_PATH=""
  fi
}

# Create a git wrapper script that fakes the remote URL for get_gitlab_project_id
# while allowing all real git operations to work against the local bare repo.
_install_git_wrapper() {
  local wrapper_dir="$TEST_TMPDIR/bin"
  local real_git
  real_git="$(command -v git)"
  mkdir -p "$wrapper_dir"

  cat > "$wrapper_dir/git" <<WRAPPER
#!/usr/bin/env bash
# Git wrapper for tests: intercepts 'remote get-url' to return fake GitLab URL
# and rewrites clone commands to use the real bare repo.
if [[ "\${1:-}" == "remote" && "\${2:-}" == "get-url" ]]; then
  echo "$FAKE_GITLAB_URL"
  exit 0
fi
if [[ "\${1:-}" == "clone" ]]; then
  args=()
  for arg in "\$@"; do
    if [[ "\$arg" == "$FAKE_GITLAB_URL" ]]; then
      args+=("$REMOTE_REPO")
    else
      args+=("\$arg")
    fi
  done
  exec "$real_git" "\${args[@]}"
fi
exec "$real_git" "\$@"
WRAPPER
  chmod +x "$wrapper_dir/git"

  _ORIGINAL_PATH="$PATH"
  export PATH="$wrapper_dir:$PATH"
}

# Add a commit to the working repo
add_test_commit() {
  local msg="${1:-Test commit}"
  echo "$msg" >> "$WORK_REPO/changes.txt"
  git -C "$WORK_REPO" add changes.txt
  git -C "$WORK_REPO" commit -m "$msg" >/dev/null 2>&1
}

# Create a version tag in the working repo and push it
create_test_tag() {
  local tag="$1"
  local msg="${2:-Release $tag}"
  git -C "$WORK_REPO" tag -a "$tag" -m "$msg"
  git -C "$WORK_REPO" push origin "$tag" >/dev/null 2>&1
}

# Push all commits to the bare remote and update tracking
push_test_commits() {
  git -C "$WORK_REPO" push origin main >/dev/null 2>&1
  git -C "$WORK_REPO" fetch origin --quiet 2>/dev/null
}

# ─── Source release.sh functions without running main ────────────────────────────

# Source the release script in a way that defines all functions but does NOT
# execute main(). We achieve this by sourcing the file with a guard.
source_release_functions() {
  _SOURCED_FOR_TESTING=true
  source "$RELEASE_SCRIPT"
}

# ─── Mock GitLab server helpers ──────────────────────────────────────────────────

MOCK_STATE_DIR=""
MOCK_PID=""
MOCK_PORT=""

start_mock_gitlab() {
  MOCK_STATE_DIR="$(mktemp -d)"

  python3 "$MOCK_GITLAB" \
    --port 0 \
    --state-dir "$MOCK_STATE_DIR" &
  MOCK_PID=$!

  # Wait for the port file to appear (up to 5 seconds)
  local retries=50
  while [[ ! -f "$MOCK_STATE_DIR/port" && $retries -gt 0 ]]; do
    sleep 0.1
    retries=$((retries - 1))
  done

  if [[ ! -f "$MOCK_STATE_DIR/port" ]]; then
    echo "ERROR: Mock GitLab server failed to start" >&2
    return 1
  fi

  MOCK_PORT="$(cat "$MOCK_STATE_DIR/port")"
}

stop_mock_gitlab() {
  if [[ -n "${MOCK_PID:-}" ]]; then
    kill "$MOCK_PID" 2>/dev/null || true
    wait "$MOCK_PID" 2>/dev/null || true
    MOCK_PID=""
  fi
  if [[ -n "${MOCK_STATE_DIR:-}" && -d "$MOCK_STATE_DIR" ]]; then
    rm -rf "$MOCK_STATE_DIR"
  fi
}

# Trigger a mock failure scenario for the next request
mock_trigger_scenario() {
  local scenario="$1"
  touch "$MOCK_STATE_DIR/$scenario"
}

# Read recorded requests from the mock
mock_get_requests() {
  # Ask the server to dump first (it dumps on shutdown, but we may want mid-test)
  if [[ -f "$MOCK_STATE_DIR/requests.json" ]]; then
    cat "$MOCK_STATE_DIR/requests.json"
  else
    echo "[]"
  fi
}
