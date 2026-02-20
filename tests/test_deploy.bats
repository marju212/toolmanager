#!/usr/bin/env bats

load test_helpers

setup() {
  source_release_functions
  TEST_TMPDIR="$(mktemp -d)"
}

teardown() {
  if [[ -n "${ORIGINAL_DIR:-}" ]]; then
    cd "$ORIGINAL_DIR"
  fi
  if [[ -n "${_ORIGINAL_PATH:-}" ]]; then
    export PATH="$_ORIGINAL_PATH"
    _ORIGINAL_PATH=""
  fi
  rm -rf "$TEST_TMPDIR"
}

# ─── extract_tool_name ──────────────────────────────────────────────────────

@test "extract_tool_name: extracts name from SSH URL with .git" {
  # Create a minimal repo with a fake remote
  local repo="$TEST_TMPDIR/repo"
  git init "$repo" >/dev/null 2>&1
  cd "$repo"
  git remote add origin "git@gitlab.com:org/my-tool.git"
  REMOTE="origin"

  extract_tool_name
  [ "$TOOL_NAME" = "my-tool" ]
}

@test "extract_tool_name: extracts name from HTTPS URL" {
  local repo="$TEST_TMPDIR/repo"
  git init "$repo" >/dev/null 2>&1
  cd "$repo"
  git remote add origin "https://gitlab.com/org/my-app.git"
  REMOTE="origin"

  extract_tool_name
  [ "$TOOL_NAME" = "my-app" ]
}

@test "extract_tool_name: handles nested groups (takes last component)" {
  local repo="$TEST_TMPDIR/repo"
  git init "$repo" >/dev/null 2>&1
  cd "$repo"
  git remote add origin "git@gitlab.com:org/sub/deep/my-lib.git"
  REMOTE="origin"

  extract_tool_name
  [ "$TOOL_NAME" = "my-lib" ]
}

@test "extract_tool_name: handles HTTPS URL without .git suffix" {
  local repo="$TEST_TMPDIR/repo"
  git init "$repo" >/dev/null 2>&1
  cd "$repo"
  git remote add origin "https://gitlab.com/org/no-dot-git"
  REMOTE="origin"

  extract_tool_name
  [ "$TOOL_NAME" = "no-dot-git" ]
}

# ─── deploy_release ─────────────────────────────────────────────────────────

@test "deploy_release: dry-run logs clone and modulefile, creates nothing" {
  setup_test_repo
  DRY_RUN=true
  TAG_PREFIX="v"
  DEPLOY_BASE_PATH="$TEST_TMPDIR/deploy"

  # Create a tag so clone would be valid
  add_test_commit "feature"
  push_test_commits
  create_test_tag "v1.0.0"

  run deploy_release "1.0.0"
  [ "$status" -eq 0 ]
  [[ "$output" == *"[dry-run] Would clone"* ]]
  [[ "$output" == *"[dry-run] Would write modulefile"* ]]

  # Nothing created
  [ ! -d "$TEST_TMPDIR/deploy/test-project/1.0.0" ]
  [ ! -f "$TEST_TMPDIR/deploy/mf/test-project/1.0.0" ]
}

@test "deploy_release: creates clone dir and modulefile with correct content" {
  # Set up repo with a custom git wrapper that returns a fake GitLab URL for
  # 'remote get-url' but translates it back to the real bare repo for 'clone'.
  rm -rf "$TEST_TMPDIR"
  TEST_TMPDIR="$(mktemp -d)"
  REMOTE_REPO="$TEST_TMPDIR/remote.git"
  WORK_REPO="$TEST_TMPDIR/work"
  ORIGINAL_DIR="$(pwd)"

  git init --bare "$REMOTE_REPO" --initial-branch=main >/dev/null 2>&1
  git clone "$REMOTE_REPO" "$WORK_REPO" >/dev/null 2>&1
  cd "$WORK_REPO"
  git config user.email "test@example.com"
  git config user.name "Test User"
  echo "init" > README.md
  git add README.md
  git commit -m "Initial commit" >/dev/null 2>&1
  git push origin main >/dev/null 2>&1

  # Install a git wrapper that fakes remote URL but rewrites clone to use bare repo
  local wrapper_dir="$TEST_TMPDIR/bin"
  local real_git
  real_git="$(command -v git)"
  mkdir -p "$wrapper_dir"
  cat > "$wrapper_dir/git" <<WRAPPER
#!/usr/bin/env bash
if [[ "\${1:-}" == "remote" && "\${2:-}" == "get-url" ]]; then
  echo "https://gitlab.example.com/group/test-project.git"
  exit 0
fi
if [[ "\${1:-}" == "clone" ]]; then
  # Replace fake URL with real bare repo path
  args=()
  for arg in "\$@"; do
    if [[ "\$arg" == "https://gitlab.example.com/group/test-project.git" ]]; then
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

  DRY_RUN=false
  TAG_PREFIX="v"
  REMOTE="origin"
  DEPLOY_BASE_PATH="$TEST_TMPDIR/deploy"

  # Create a tag
  add_test_commit "feature"
  push_test_commits
  create_test_tag "v2.0.0"

  run deploy_release "2.0.0"
  [ "$status" -eq 0 ]

  # Clone directory exists
  [ -d "$TEST_TMPDIR/deploy/test-project/2.0.0" ]
  # Modulefile exists
  [ -f "$TEST_TMPDIR/deploy/mf/test-project/2.0.0" ]
  # Modulefile contains expected content
  grep -q "#%Module1.0" "$TEST_TMPDIR/deploy/mf/test-project/2.0.0"
  grep -q "module-whatis" "$TEST_TMPDIR/deploy/mf/test-project/2.0.0"
  grep -q "conflict test-project" "$TEST_TMPDIR/deploy/mf/test-project/2.0.0"
  grep -q "prepend-path PATH" "$TEST_TMPDIR/deploy/mf/test-project/2.0.0"
}

@test "deploy_release: errors if version dir already exists" {
  setup_test_repo
  DRY_RUN=false
  TAG_PREFIX="v"
  DEPLOY_BASE_PATH="$TEST_TMPDIR/deploy"

  add_test_commit "feature"
  push_test_commits
  create_test_tag "v3.0.0"

  # Pre-create the deploy directory
  mkdir -p "$TEST_TMPDIR/deploy/test-project/3.0.0"

  run deploy_release "3.0.0"
  [ "$status" -ne 0 ]
  [[ "$output" == *"Deploy directory already exists"* ]]
}

@test "deploy_release: errors if modulefile already exists" {
  setup_test_repo
  DRY_RUN=false
  TAG_PREFIX="v"
  DEPLOY_BASE_PATH="$TEST_TMPDIR/deploy"

  add_test_commit "feature"
  push_test_commits
  create_test_tag "v4.0.0"

  # Pre-create the modulefile
  mkdir -p "$TEST_TMPDIR/deploy/mf/test-project"
  echo "existing" > "$TEST_TMPDIR/deploy/mf/test-project/4.0.0"

  run deploy_release "4.0.0"
  [ "$status" -ne 0 ]
  [[ "$output" == *"Modulefile already exists"* ]]

  # Content should be unchanged
  grep -q "existing" "$TEST_TMPDIR/deploy/mf/test-project/4.0.0"
}

@test "deploy_release: copies previous modulefile when one exists" {
  setup_test_repo
  DRY_RUN=false
  TAG_PREFIX="v"
  DEPLOY_BASE_PATH="$TEST_TMPDIR/deploy"

  add_test_commit "feature"
  push_test_commits
  create_test_tag "v5.0.0"

  # Pre-create a modulefile for a previous version with custom content
  mkdir -p "$TEST_TMPDIR/deploy/mf/test-project"
  cat > "$TEST_TMPDIR/deploy/mf/test-project/4.0.0" <<'EOF'
#%Module1.0
## custom modulefile for test-project/4.0.0
module-whatis "test-project version 4.0.0"
conflict test-project
set root /opt/software/test-project/4.0.0
prepend-path PATH $root/bin
prepend-path LD_LIBRARY_PATH $root/lib
EOF

  run deploy_release "5.0.0"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Copying modulefile from"* ]]

  # New modulefile should exist
  [ -f "$TEST_TMPDIR/deploy/mf/test-project/5.0.0" ]
  # Version references should be updated
  grep -q "5.0.0" "$TEST_TMPDIR/deploy/mf/test-project/5.0.0"
  # Custom content (LD_LIBRARY_PATH) should be preserved
  grep -q "LD_LIBRARY_PATH" "$TEST_TMPDIR/deploy/mf/test-project/5.0.0"
  # Old version should NOT appear
  ! grep -q "4.0.0" "$TEST_TMPDIR/deploy/mf/test-project/5.0.0"
}

@test "deploy_release: cleans up clone dir when modulefile already exists" {
  setup_test_repo
  DRY_RUN=false
  TAG_PREFIX="v"
  DEPLOY_BASE_PATH="$TEST_TMPDIR/deploy"

  add_test_commit "feature"
  push_test_commits
  create_test_tag "v4.1.0"

  # Pre-create the modulefile (but not the deploy dir)
  mkdir -p "$TEST_TMPDIR/deploy/mf/test-project"
  echo "existing" > "$TEST_TMPDIR/deploy/mf/test-project/4.1.0"

  run deploy_release "4.1.0"
  [ "$status" -ne 0 ]
  [[ "$output" == *"Modulefile already exists"* ]]

  # Clone directory should have been cleaned up
  [ ! -d "$TEST_TMPDIR/deploy/test-project/4.1.0" ]
}

@test "deploy_release: rejects relative DEPLOY_BASE_PATH" {
  setup_test_repo
  DRY_RUN=false
  TAG_PREFIX="v"
  DEPLOY_BASE_PATH="relative/path"

  add_test_commit "feature"
  push_test_commits
  create_test_tag "v7.0.0"

  run deploy_release "7.0.0"
  [ "$status" -ne 0 ]
  [[ "$output" == *"must be an absolute path"* ]]
}

@test "deploy_release: ignores non-semver files when finding latest modulefile" {
  setup_test_repo
  DRY_RUN=false
  TAG_PREFIX="v"
  DEPLOY_BASE_PATH="$TEST_TMPDIR/deploy"

  add_test_commit "feature"
  push_test_commits
  create_test_tag "v2.0.0"

  # Pre-create a valid modulefile and a non-version file
  mkdir -p "$TEST_TMPDIR/deploy/mf/test-project"
  cat > "$TEST_TMPDIR/deploy/mf/test-project/1.0.0" <<'EOF'
#%Module1.0
module-whatis "test-project version 1.0.0"
set root /opt/test-project/1.0.0
EOF
  echo "junk" > "$TEST_TMPDIR/deploy/mf/test-project/README.md"
  echo "junk" > "$TEST_TMPDIR/deploy/mf/test-project/.backup"

  run deploy_release "2.0.0"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Copying modulefile from"* ]]

  # Should have copied from 1.0.0, not README.md or .backup
  [ -f "$TEST_TMPDIR/deploy/mf/test-project/2.0.0" ]
  grep -q "2.0.0" "$TEST_TMPDIR/deploy/mf/test-project/2.0.0"
  grep -q "#%Module1.0" "$TEST_TMPDIR/deploy/mf/test-project/2.0.0"
}

@test "deploy_release: dry-run with previous modulefile logs copy action" {
  setup_test_repo
  DRY_RUN=true
  TAG_PREFIX="v"
  DEPLOY_BASE_PATH="$TEST_TMPDIR/deploy"

  add_test_commit "feature"
  push_test_commits
  create_test_tag "v6.0.0"

  # Pre-create a modulefile for a previous version
  mkdir -p "$TEST_TMPDIR/deploy/mf/test-project"
  echo "previous" > "$TEST_TMPDIR/deploy/mf/test-project/5.0.0"

  run deploy_release "6.0.0"
  [ "$status" -eq 0 ]
  [[ "$output" == *"[dry-run] Would copy modulefile from"* ]]

  # Nothing created
  [ ! -f "$TEST_TMPDIR/deploy/mf/test-project/6.0.0" ]
}
