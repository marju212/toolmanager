#!/usr/bin/env bats

load test_helpers

setup() {
  setup_test_repo
  start_mock_gitlab
}

teardown() {
  stop_mock_gitlab
  teardown_test_repo
}

# Helper: set env vars that the script needs
_export_env() {
  export GITLAB_TOKEN="test-token-12345"
  export GITLAB_API_URL="http://127.0.0.1:${MOCK_PORT}/api/v4"
}

# ─── Full dry-run integration ────────────────────────────────────────────────────

@test "integration: dry-run completes full flow" {
  cd "$WORK_REPO"
  _export_env

  add_test_commit "Feature one"
  push_test_commits

  # Pipe "1" to select patch bump, then expect dry-run to complete
  run bash -c '
    cd "'"$WORK_REPO"'"
    echo "1" | "'"$RELEASE_SCRIPT"'" --dry-run 2>&1
  '
  echo "OUTPUT: $output"
  [ "$status" -eq 0 ]
  [[ "$output" == *"dry-run"* ]]
  [[ "$output" == *"Release Summary"* ]]
  [[ "$output" == *"v0.0.1"* ]]
}

@test "integration: dry-run with existing tags suggests correct bump" {
  cd "$WORK_REPO"

  add_test_commit "v1 release"
  git tag -a "v1.2.3" -m "v1.2.3"
  git push origin "v1.2.3" >/dev/null 2>&1
  add_test_commit "Post-release feature"
  push_test_commits

  _export_env

  # Select "2" for minor bump: should suggest v1.3.0
  run bash -c '
    cd "'"$WORK_REPO"'"
    echo "2" | "'"$RELEASE_SCRIPT"'" --dry-run 2>&1
  '
  echo "OUTPUT: $output"
  [ "$status" -eq 0 ]
  [[ "$output" == *"v1.3.0"* ]]
}

# ─── Full real integration (with mock GitLab) ────────────────────────────────────

@test "integration: real release creates branch and tag" {
  cd "$WORK_REPO"
  _export_env

  add_test_commit "Feature for release"
  push_test_commits

  # Pipe "1" for patch bump, then "y" to confirm
  run bash -c '
    cd "'"$WORK_REPO"'"
    printf "1\ny\n" | "'"$RELEASE_SCRIPT"'" 2>&1
  '
  echo "OUTPUT: $output"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Release Summary"* ]]
  [[ "$output" == *"v0.0.1"* ]]

  # Verify branch was created in remote
  run git ls-remote --heads "$REMOTE_REPO" "release/v0.0.1"
  [[ "$output" == *"release/v0.0.1"* ]]

  # Verify tag was created in remote
  run git ls-remote --tags "$REMOTE_REPO" "v0.0.1"
  [[ "$output" == *"v0.0.1"* ]]

  # Verify we're back on main
  local current_branch
  current_branch="$(git -C "$WORK_REPO" symbolic-ref --short HEAD)"
  [ "$current_branch" = "main" ]
}

# ─── Normal release does not create MR ───────────────────────────────────────────

@test "integration: normal release does not create MR" {
  cd "$WORK_REPO"
  _export_env

  add_test_commit "No MR feature"
  push_test_commits

  run bash -c '
    cd "'"$WORK_REPO"'"
    printf "1\ny\n" | "'"$RELEASE_SCRIPT"'" 2>&1
  '
  echo "OUTPUT: $output"
  [ "$status" -eq 0 ]
  [[ "$output" != *"Merge request created"* ]]
  [[ "$output" != *"merge_requests"* ]]
  # Summary should NOT have an MR row
  [[ "$output" != *"MR:"* ]]
}

# ─── update_default_branch defaults to true ──────────────────────────────────────

@test "integration: default branch updated by default" {
  cd "$WORK_REPO"
  _export_env

  add_test_commit "Default branch update by default"
  push_test_commits

  run bash -c '
    cd "'"$WORK_REPO"'"
    printf "1\ny\ny\n" | "'"$RELEASE_SCRIPT"'" 2>&1
  '
  echo "OUTPUT: $output"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Default branch updated"* ]]
}

@test "integration: default branch NOT updated when disabled via env var" {
  cd "$WORK_REPO"
  _export_env
  export RELEASE_UPDATE_DEFAULT_BRANCH=false

  add_test_commit "No default branch update via env"
  push_test_commits

  run bash -c '
    cd "'"$WORK_REPO"'"
    printf "1\ny\n" | "'"$RELEASE_SCRIPT"'" 2>&1
  '
  echo "OUTPUT: $output"
  [ "$status" -eq 0 ]
  # Should NOT contain any mention of updating default branch
  [[ "$output" != *"Updating GitLab default branch"* ]]
}

@test "integration: default branch updated with --update-default-branch flag" {
  cd "$WORK_REPO"
  _export_env

  add_test_commit "With default branch update"
  push_test_commits

  run bash -c '
    cd "'"$WORK_REPO"'"
    printf "1\ny\ny\n" | "'"$RELEASE_SCRIPT"'" --update-default-branch 2>&1
  '
  echo "OUTPUT: $output"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Default branch updated"* ]]
}

@test "integration: --no-update-default-branch prevents default branch update" {
  cd "$WORK_REPO"
  _export_env

  add_test_commit "No update flag test"
  push_test_commits

  run bash -c '
    cd "'"$WORK_REPO"'"
    printf "1\ny\n" | "'"$RELEASE_SCRIPT"'" --no-update-default-branch 2>&1
  '
  echo "OUTPUT: $output"
  [ "$status" -eq 0 ]
  [[ "$output" != *"Updating GitLab default branch"* ]]
  [[ "$output" != *"Default branch updated"* ]]
}

# ─── Failure scenarios ───────────────────────────────────────────────────────────

@test "integration: fails on dirty working tree" {
  cd "$WORK_REPO"
  _export_env

  echo "uncommitted" > "$WORK_REPO/dirty.txt"

  run bash -c '
    cd "'"$WORK_REPO"'"
    echo "1" | "'"$RELEASE_SCRIPT"'" --dry-run 2>&1
  '
  [ "$status" -ne 0 ]
  [[ "$output" == *"dirty"* ]]
}

@test "integration: fails on wrong branch" {
  cd "$WORK_REPO"
  _export_env

  git checkout -b not-main >/dev/null 2>&1

  run bash -c '
    cd "'"$WORK_REPO"'"
    echo "1" | "'"$RELEASE_SCRIPT"'" --dry-run 2>&1
  '
  [ "$status" -ne 0 ]
  [[ "$output" == *"Must be on"* ]]
}

@test "integration: user cancels at confirmation prompt" {
  cd "$WORK_REPO"
  _export_env

  add_test_commit "Will cancel"
  push_test_commits

  # Select patch version "1", then "n" to cancel
  run bash -c '
    cd "'"$WORK_REPO"'"
    printf "1\nn\n" | "'"$RELEASE_SCRIPT"'" 2>&1
  '
  [ "$status" -eq 0 ]
  [[ "$output" == *"cancelled"* ]]

  # No branch should have been created
  run git ls-remote --heads "$REMOTE_REPO" "release/v0.0.1"
  [ -z "$output" ]
}

@test "integration: rejects duplicate tag" {
  cd "$WORK_REPO"
  _export_env

  add_test_commit "Already tagged"
  git tag -a "v0.0.1" -m "v0.0.1"
  git push origin "v0.0.1" >/dev/null 2>&1
  add_test_commit "After tag"
  push_test_commits

  # Select custom (choice 4) and enter the existing version
  run bash -c '
    cd "'"$WORK_REPO"'"
    printf "4\n0.0.1\n" | "'"$RELEASE_SCRIPT"'" --dry-run 2>&1
  '
  [ "$status" -ne 0 ]
  [[ "$output" == *"already exists"* ]]
}

# ─── Config file integration ─────────────────────────────────────────────────────

# ─── CI mode (--version --non-interactive) ───────────────────────────────────────────────

@test "integration: --version --non-interactive completes without interaction" {
  cd "$WORK_REPO"
  _export_env

  add_test_commit "CI release feature"
  push_test_commits

  run bash -c '
    cd "'"$WORK_REPO"'"
    "'"$RELEASE_SCRIPT"'" --version 1.0.0 --non-interactive 2>&1
  '
  echo "OUTPUT: $output"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Release Summary"* ]]
  [[ "$output" == *"v1.0.0"* ]]
  [[ "$output" == *"non-interactive"* ]]

  # Verify branch was created in remote
  run git ls-remote --heads "$REMOTE_REPO" "release/v1.0.0"
  [[ "$output" == *"release/v1.0.0"* ]]

  # Verify tag was created in remote
  run git ls-remote --tags "$REMOTE_REPO" "v1.0.0"
  [[ "$output" == *"v1.0.0"* ]]
}

@test "integration: --version with invalid semver fails" {
  cd "$WORK_REPO"
  _export_env

  add_test_commit "Bad version test"
  push_test_commits

  run bash -c '
    cd "'"$WORK_REPO"'"
    "'"$RELEASE_SCRIPT"'" --version abc --non-interactive 2>&1
  '
  [ "$status" -ne 0 ]
  [[ "$output" == *"Invalid semver"* ]]
}

@test "integration: --version with duplicate tag fails" {
  cd "$WORK_REPO"
  _export_env

  add_test_commit "Tag this"
  git tag -a "v0.0.1" -m "v0.0.1"
  git push origin "v0.0.1" >/dev/null 2>&1
  add_test_commit "After tag"
  push_test_commits

  run bash -c '
    cd "'"$WORK_REPO"'"
    "'"$RELEASE_SCRIPT"'" --version 0.0.1 --non-interactive 2>&1
  '
  [ "$status" -ne 0 ]
  [[ "$output" == *"already exists"* ]]
}

@test "integration: --version --non-interactive --dry-run in detached HEAD succeeds" {
  cd "$WORK_REPO"
  _export_env

  add_test_commit "Detached HEAD CI test"
  push_test_commits

  # Detach HEAD at main tip
  git checkout --detach HEAD >/dev/null 2>&1

  run bash -c '
    cd "'"$WORK_REPO"'"
    "'"$RELEASE_SCRIPT"'" --version 1.0.0 --non-interactive --dry-run 2>&1
  '
  echo "OUTPUT: $output"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Detached HEAD"* ]]
  [[ "$output" == *"Release Summary"* ]]
  [[ "$output" == *"v1.0.0"* ]]
}

# ─── Config file integration ─────────────────────────────────────────────────────

@test "integration: reads config from --config flag" {
  cd "$WORK_REPO"

  local conf="$TEST_TMPDIR/custom.conf"
  cat > "$conf" <<EOF
GITLAB_TOKEN=test-token-12345
GITLAB_API_URL=http://127.0.0.1:${MOCK_PORT}/api/v4
DEFAULT_BRANCH=main
EOF

  add_test_commit "Config test"
  push_test_commits

  run bash -c '
    cd "'"$WORK_REPO"'"
    echo "1" | "'"$RELEASE_SCRIPT"'" --dry-run --config "'"$conf"'" 2>&1
  '
  echo "OUTPUT: $output"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Loading config"* ]]
  [[ "$output" == *"v0.0.1"* ]]
}

# ─── Hotfix MR integration ──────────────────────────────────────────────────────

@test "integration: --hotfix-mr creates MR for branch with commits ahead" {
  cd "$WORK_REPO"
  _export_env

  add_test_commit "Initial feature"
  push_test_commits

  # Create a release branch and push it
  git checkout -b release/v1.0.0 >/dev/null 2>&1
  add_test_commit "Hotfix commit"
  git push origin release/v1.0.0 >/dev/null 2>&1
  git checkout main >/dev/null 2>&1

  run bash -c '
    cd "'"$WORK_REPO"'"
    printf "y\n" | "'"$RELEASE_SCRIPT"'" --hotfix-mr release/v1.0.0 2>&1
  '
  echo "OUTPUT: $output"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Hotfix MR created"* ]]
}

@test "integration: --hotfix-mr dry-run does not create MR" {
  cd "$WORK_REPO"
  _export_env

  add_test_commit "Initial feature"
  push_test_commits

  # Create a release branch with a hotfix commit
  git checkout -b release/v1.0.0 >/dev/null 2>&1
  add_test_commit "Hotfix commit"
  git push origin release/v1.0.0 >/dev/null 2>&1
  git checkout main >/dev/null 2>&1

  run bash -c '
    cd "'"$WORK_REPO"'"
    "'"$RELEASE_SCRIPT"'" --hotfix-mr release/v1.0.0 --dry-run --non-interactive 2>&1
  '
  echo "OUTPUT: $output"
  [ "$status" -eq 0 ]
  [[ "$output" == *"dry-run"* ]]
  [[ "$output" == *"Hotfix MR created"* ]]
}

@test "integration: --hotfix-mr fails when branch does not exist on remote" {
  cd "$WORK_REPO"
  _export_env

  add_test_commit "Initial feature"
  push_test_commits

  run bash -c '
    cd "'"$WORK_REPO"'"
    "'"$RELEASE_SCRIPT"'" --hotfix-mr release/v9.9.9 --non-interactive 2>&1
  '
  echo "OUTPUT: $output"
  [ "$status" -ne 0 ]
  [[ "$output" == *"does not exist"* ]]
}

@test "integration: --hotfix-mr fails when branch has no commits ahead" {
  cd "$WORK_REPO"
  _export_env

  add_test_commit "Initial feature"
  push_test_commits

  # Create a release branch at exactly the same point as main (no commits ahead)
  git checkout -b release/v1.0.0 >/dev/null 2>&1
  git push origin release/v1.0.0 >/dev/null 2>&1
  git checkout main >/dev/null 2>&1

  run bash -c '
    cd "'"$WORK_REPO"'"
    "'"$RELEASE_SCRIPT"'" --hotfix-mr release/v1.0.0 --non-interactive 2>&1
  '
  echo "OUTPUT: $output"
  [ "$status" -ne 0 ]
  [[ "$output" == *"no commits ahead"* ]]
}
