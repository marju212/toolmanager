#!/usr/bin/env bats

load test_helpers

setup() {
  source_release_functions
  setup_test_repo
}

teardown() {
  teardown_test_repo
}

# ─── get_latest_version ─────────────────────────────────────────────────────────

@test "get_latest_version: returns 0.0.0 when no tags exist" {
  cd "$WORK_REPO"
  TAG_PREFIX="v"
  run get_latest_version
  [ "$status" -eq 0 ]
  [ "$output" = "0.0.0" ]
}

@test "get_latest_version: returns latest semver tag" {
  cd "$WORK_REPO"
  TAG_PREFIX="v"
  add_test_commit "first"
  git tag -a "v1.0.0" -m "v1.0.0"
  add_test_commit "second"
  git tag -a "v1.1.0" -m "v1.1.0"
  add_test_commit "third"
  git tag -a "v1.0.1" -m "v1.0.1"

  run get_latest_version
  [ "$status" -eq 0 ]
  [ "$output" = "1.1.0" ]
}

@test "get_latest_version: handles custom tag prefix" {
  cd "$WORK_REPO"
  TAG_PREFIX="release-"
  add_test_commit "first"
  git tag -a "release-2.0.0" -m "release-2.0.0"

  run get_latest_version
  [ "$status" -eq 0 ]
  [ "$output" = "2.0.0" ]
}

# ─── BUG-3: non-semver tag filtering ────────────────────────────────────────────

@test "get_latest_version: skips pre-release tags like v1.2.3-beta" {
  cd "$WORK_REPO"
  TAG_PREFIX="v"
  add_test_commit "first"
  git tag -a "v1.0.0" -m "v1.0.0"
  add_test_commit "second"
  git tag -a "v1.1.0-beta" -m "v1.1.0-beta"
  add_test_commit "third"
  git tag -a "v1.1.0-rc1" -m "v1.1.0-rc1"

  run get_latest_version
  [ "$status" -eq 0 ]
  [ "$output" = "1.0.0" ]
}

@test "get_latest_version: skips malformed tags" {
  cd "$WORK_REPO"
  TAG_PREFIX="v"
  add_test_commit "first"
  git tag -a "v1.2.3" -m "v1.2.3"
  add_test_commit "second"
  git tag -a "v-broken" -m "broken"
  add_test_commit "third"
  git tag -a "v2.0" -m "incomplete"

  run get_latest_version
  [ "$status" -eq 0 ]
  [ "$output" = "1.2.3" ]
}

@test "get_latest_version: returns 0.0.0 when only non-semver tags exist" {
  cd "$WORK_REPO"
  TAG_PREFIX="v"
  add_test_commit "first"
  git tag -a "v1.0.0-alpha" -m "alpha"

  run get_latest_version
  [ "$status" -eq 0 ]
  [ "$output" = "0.0.0" ]
}

# ─── check_branch ───────────────────────────────────────────────────────────────

@test "check_branch: succeeds on clean main branch in sync" {
  cd "$WORK_REPO"
  DEFAULT_BRANCH="main"
  REMOTE="origin"
  run check_branch
  [ "$status" -eq 0 ]
  [[ "$output" == *"clean and in sync"* ]]
}

@test "check_branch: fails on wrong branch" {
  cd "$WORK_REPO"
  git checkout -b feature-x >/dev/null 2>&1
  DEFAULT_BRANCH="main"
  REMOTE="origin"
  run check_branch
  [ "$status" -ne 0 ]
  [[ "$output" == *"Must be on"* ]]
}

@test "check_branch: fails on dirty working tree" {
  cd "$WORK_REPO"
  echo "dirty" > "$WORK_REPO/untracked.txt"
  DEFAULT_BRANCH="main"
  REMOTE="origin"
  run check_branch
  [ "$status" -ne 0 ]
  [[ "$output" == *"dirty"* ]]
}

@test "check_branch: fails when local is behind remote" {
  cd "$WORK_REPO"

  # Create a commit directly in the bare remote via a temp clone
  local tmp_clone="$TEST_TMPDIR/tmp_clone"
  git clone "$REMOTE_REPO" "$tmp_clone" >/dev/null 2>&1
  git -C "$tmp_clone" config user.email "other@example.com"
  git -C "$tmp_clone" config user.name "Other"
  echo "remote change" > "$tmp_clone/remote.txt"
  git -C "$tmp_clone" add remote.txt
  git -C "$tmp_clone" commit -m "Remote commit" >/dev/null 2>&1
  git -C "$tmp_clone" push origin main >/dev/null 2>&1

  DEFAULT_BRANCH="main"
  REMOTE="origin"
  run check_branch
  [ "$status" -ne 0 ]
  [[ "$output" == *"not in sync"* ]]
}

# ─── generate_changelog ─────────────────────────────────────────────────────────

@test "generate_changelog: lists commits since tag" {
  cd "$WORK_REPO"
  TAG_PREFIX="v"
  add_test_commit "First feature"
  git tag -a "v1.0.0" -m "v1.0.0"
  add_test_commit "Second feature"
  add_test_commit "Third feature"

  generate_changelog "1.0.0"
  [[ "$CHANGELOG" == *"Second feature"* ]]
  [[ "$CHANGELOG" == *"Third feature"* ]]
  # Should NOT contain the commit before the tag
  [[ "$CHANGELOG" != *"First feature"* ]]
}

@test "generate_changelog: lists all commits when no tag exists" {
  cd "$WORK_REPO"
  TAG_PREFIX="v"
  add_test_commit "My commit"

  generate_changelog "0.0.0"
  [[ "$CHANGELOG" == *"My commit"* ]]
}

@test "generate_changelog: shows placeholder when no commits since tag" {
  cd "$WORK_REPO"
  TAG_PREFIX="v"
  add_test_commit "Tagged commit"
  git tag -a "v1.0.0" -m "v1.0.0"

  generate_changelog "1.0.0"
  [[ "$CHANGELOG" == *"No changes recorded"* ]]
}

# ─── create_release_branch (dry-run) ────────────────────────────────────────────

@test "create_release_branch: dry-run does not create branch" {
  cd "$WORK_REPO"
  DRY_RUN=true
  REMOTE="origin"
  run create_release_branch "release/v1.0.0"
  [ "$status" -eq 0 ]
  [[ "$output" == *"dry-run"* ]]
  # Branch should NOT exist
  run git branch --list "release/v1.0.0"
  [ -z "$output" ]
}

# ─── create_release_branch (real) ────────────────────────────────────────────────

@test "create_release_branch: creates and pushes branch" {
  cd "$WORK_REPO"
  DRY_RUN=false
  REMOTE="origin"

  create_release_branch "release/v9.9.9"

  # Local branch exists
  run git branch --list "release/v9.9.9"
  [[ "$output" == *"release/v9.9.9"* ]]

  # Remote branch exists
  run git ls-remote --heads "$REMOTE_REPO" "release/v9.9.9"
  [[ "$output" == *"release/v9.9.9"* ]]

  # Cleanup var was set
  [ "$CLEANUP_BRANCH" = "release/v9.9.9" ]
}

# ─── tag_release (dry-run) ──────────────────────────────────────────────────────

@test "tag_release: dry-run does not create tag" {
  cd "$WORK_REPO"
  DRY_RUN=true
  REMOTE="origin"
  CHANGELOG="test changelog"
  run tag_release "v2.0.0" "2.0.0"
  [ "$status" -eq 0 ]
  [[ "$output" == *"dry-run"* ]]
  # Tag should NOT exist
  run git tag --list "v2.0.0"
  [ -z "$output" ]
}

# ─── tag_release (real) ──────────────────────────────────────────────────────────

@test "tag_release: creates annotated tag and pushes" {
  cd "$WORK_REPO"
  DRY_RUN=false
  REMOTE="origin"
  CHANGELOG="- Feature A\n- Feature B"

  tag_release "v3.0.0" "3.0.0"

  # Local tag exists
  run git tag --list "v3.0.0"
  [[ "$output" == *"v3.0.0"* ]]

  # Tag is annotated
  run git cat-file -t "v3.0.0"
  [ "$output" = "tag" ]

  # Tag message contains changelog
  run git tag -l --format='%(contents)' "v3.0.0"
  [[ "$output" == *"Feature A"* ]]

  # Remote has the tag
  run git ls-remote --tags "$REMOTE_REPO" "v3.0.0"
  [[ "$output" == *"v3.0.0"* ]]

  # Cleanup var was set
  [ "$CLEANUP_TAG" = "v3.0.0" ]
}

# ─── confirm ─────────────────────────────────────────────────────────────────────

# ─── check_version_available ─────────────────────────────────────────────────

@test "check_version_available: succeeds when tag and branch do not exist" {
  cd "$WORK_REPO"
  TAG_PREFIX="v"
  REMOTE="origin"
  run check_version_available "9.9.9"
  [ "$status" -eq 0 ]
}

@test "check_version_available: fails when tag already exists" {
  cd "$WORK_REPO"
  TAG_PREFIX="v"
  REMOTE="origin"
  add_test_commit "first"
  git tag -a "v1.0.0" -m "v1.0.0"

  run check_version_available "1.0.0"
  [ "$status" -ne 0 ]
  [[ "$output" == *"Tag 'v1.0.0' already exists"* ]]
}

@test "check_version_available: fails when branch already exists" {
  cd "$WORK_REPO"
  TAG_PREFIX="v"
  REMOTE="origin"
  git checkout -b "release/v2.0.0" >/dev/null 2>&1
  git checkout main >/dev/null 2>&1

  run check_version_available "2.0.0"
  [ "$status" -ne 0 ]
  [[ "$output" == *"Branch 'release/v2.0.0' already exists"* ]]
}

# ─── confirm ─────────────────────────────────────────────────────────────────────

@test "confirm: dry-run always succeeds" {
  DRY_RUN=true
  run confirm "Proceed?"
  [ "$status" -eq 0 ]
  [[ "$output" == *"dry-run"* ]]
}

@test "confirm: 'y' returns 0" {
  DRY_RUN=false
  run bash -c '
    confirm() {
      read -rp "$1 [y/N] " answer
      case "$answer" in
        [yY][eE][sS]|[yY]) return 0 ;;
        *) return 1 ;;
      esac
    }
    echo "y" | confirm "Go?"
  '
  [ "$status" -eq 0 ]
}

@test "confirm: 'n' returns 1" {
  DRY_RUN=false
  run bash -c '
    confirm() {
      read -rp "$1 [y/N] " answer
      case "$answer" in
        [yY][eE][sS]|[yY]) return 0 ;;
        *) return 1 ;;
      esac
    }
    echo "n" | confirm "Go?"
  '
  [ "$status" -ne 0 ]
}

@test "confirm: empty input returns 1" {
  DRY_RUN=false
  run bash -c '
    confirm() {
      read -rp "$1 [y/N] " answer
      case "$answer" in
        [yY][eE][sS]|[yY]) return 0 ;;
        *) return 1 ;;
      esac
    }
    echo "" | confirm "Go?"
  '
  [ "$status" -ne 0 ]
}

@test "confirm: NON_INTERACTIVE=true succeeds and logs non-interactive" {
  DRY_RUN=false
  NON_INTERACTIVE=true
  run confirm "Proceed?"
  [ "$status" -eq 0 ]
  [[ "$output" == *"non-interactive"* ]]
}

# ─── check_branch: detached HEAD ────────────────────────────────────────────────

@test "check_branch: succeeds in detached HEAD at default branch tip" {
  cd "$WORK_REPO"
  DEFAULT_BRANCH="main"
  REMOTE="origin"

  # Detach HEAD at main tip
  git checkout --detach HEAD >/dev/null 2>&1

  run check_branch
  [ "$status" -eq 0 ]
  [[ "$output" == *"Detached HEAD"* ]]
  [[ "$output" == *"clean and in sync"* ]]
}

@test "check_branch: fails in detached HEAD at wrong commit" {
  cd "$WORK_REPO"
  DEFAULT_BRANCH="main"
  REMOTE="origin"

  # Create a new commit and push it to remote, then detach at the old commit
  local old_sha
  old_sha="$(git rev-parse HEAD)"
  add_test_commit "new commit"
  push_test_commits

  git checkout --detach "$old_sha" >/dev/null 2>&1

  run check_branch
  [ "$status" -ne 0 ]
  [[ "$output" == *"Detached HEAD"* ]]
  [[ "$output" == *"not at the tip"* ]]
}
