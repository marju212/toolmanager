#!/usr/bin/env bats

load test_helpers

setup() {
  source_release_functions
}

# ─── --help ──────────────────────────────────────────────────────────────────────

@test "parse_args: --help exits 0 and prints usage" {
  run bash -c 'source '"$RELEASE_SCRIPT"' --help'
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage: release.sh"* ]]
}

@test "parse_args: -h exits 0 and prints usage" {
  run bash -c 'source '"$RELEASE_SCRIPT"' -h'
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage: release.sh"* ]]
}

# ─── --dry-run ───────────────────────────────────────────────────────────────────

@test "parse_args: --dry-run sets DRY_RUN=true" {
  DRY_RUN=false
  parse_args --dry-run
  [ "$DRY_RUN" = "true" ]
}

# ─── --hotfix-mr ────────────────────────────────────────────────────────────────

@test "parse_args: --hotfix-mr sets HOTFIX_MR_BRANCH" {
  HOTFIX_MR_BRANCH=""
  parse_args --hotfix-mr release/v1.2.3
  [ "$HOTFIX_MR_BRANCH" = "release/v1.2.3" ]
}

@test "parse_args: --hotfix-mr without value exits with error" {
  run bash -c '
    source "'"$RELEASE_SCRIPT"'" --hotfix-mr 2>&1
  '
  [ "$status" -ne 0 ]
}

@test "parse_args: --hotfix-mr with --dry-run" {
  DRY_RUN=false
  HOTFIX_MR_BRANCH=""
  parse_args --hotfix-mr release/v1.0.0 --dry-run
  [ "$HOTFIX_MR_BRANCH" = "release/v1.0.0" ]
  [ "$DRY_RUN" = "true" ]
}

# ─── --config ────────────────────────────────────────────────────────────────────

@test "parse_args: --config sets CONFIG_FILE" {
  CONFIG_FILE=""
  parse_args --config /tmp/my.conf
  [ "$CONFIG_FILE" = "/tmp/my.conf" ]
}

@test "parse_args: --config without value exits with error" {
  run bash -c '
    source "'"$RELEASE_SCRIPT"'" --config 2>&1
  '
  [ "$status" -ne 0 ]
}

# ─── combined flags ──────────────────────────────────────────────────────────────

@test "parse_args: multiple flags combined" {
  DRY_RUN=false
  HOTFIX_MR_BRANCH=""
  CONFIG_FILE=""
  parse_args --dry-run --hotfix-mr release/v1.0.0 --config /tmp/x.conf
  [ "$DRY_RUN" = "true" ]
  [ "$HOTFIX_MR_BRANCH" = "release/v1.0.0" ]
  [ "$CONFIG_FILE" = "/tmp/x.conf" ]
}

# ─── --update-default-branch ─────────────────────────────────────────────────────

@test "parse_args: --update-default-branch sets UPDATE_DEFAULT_BRANCH=true" {
  UPDATE_DEFAULT_BRANCH=false
  parse_args --update-default-branch
  [ "$UPDATE_DEFAULT_BRANCH" = "true" ]
}

@test "parse_args: UPDATE_DEFAULT_BRANCH defaults to true" {
  [ "$UPDATE_DEFAULT_BRANCH" = "true" ]
}

@test "parse_args: --no-update-default-branch sets UPDATE_DEFAULT_BRANCH=false" {
  UPDATE_DEFAULT_BRANCH=true
  parse_args --no-update-default-branch
  [ "$UPDATE_DEFAULT_BRANCH" = "false" ]
}

# ─── unknown option ──────────────────────────────────────────────────────────────

@test "parse_args: unknown option exits with error" {
  run bash -c '
    source "'"$RELEASE_SCRIPT"'" --bogus 2>&1
  '
  [ "$status" -ne 0 ]
}

# ─── --version ──────────────────────────────────────────────────────────────────

@test "parse_args: --version sets CLI_VERSION" {
  CLI_VERSION=""
  parse_args --version 2.0.0
  [ "$CLI_VERSION" = "2.0.0" ]
}

@test "parse_args: --version without value exits with error" {
  run bash -c '
    source "'"$RELEASE_SCRIPT"'" --version 2>&1
  '
  [ "$status" -ne 0 ]
}

# ─── --non-interactive / -n ───────────────────────────────────────────────────

@test "parse_args: --non-interactive sets NON_INTERACTIVE=true" {
  NON_INTERACTIVE=false
  parse_args --non-interactive
  [ "$NON_INTERACTIVE" = "true" ]
}

@test "parse_args: -n sets NON_INTERACTIVE=true" {
  NON_INTERACTIVE=false
  parse_args -n
  [ "$NON_INTERACTIVE" = "true" ]
}

# ─── combined flags with --version and --non-interactive ─────────────────────────

@test "parse_args: --version --non-interactive --dry-run combined" {
  DRY_RUN=false
  NON_INTERACTIVE=false
  CLI_VERSION=""
  parse_args --version 2.0.0 --non-interactive --dry-run
  [ "$CLI_VERSION" = "2.0.0" ]
  [ "$NON_INTERACTIVE" = "true" ]
  [ "$DRY_RUN" = "true" ]
}

# ─── --deploy-path ──────────────────────────────────────────────────────────

@test "parse_args: --deploy-path sets CLI_DEPLOY_PATH" {
  CLI_DEPLOY_PATH=""
  parse_args --deploy-path /opt/deploy
  [ "$CLI_DEPLOY_PATH" = "/opt/deploy" ]
}

@test "parse_args: --deploy-path without value exits with error" {
  run bash -c '
    source "'"$RELEASE_SCRIPT"'" --deploy-path 2>&1
  '
  [ "$status" -ne 0 ]
}
