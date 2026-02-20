#!/usr/bin/env bats

load test_helpers

setup() {
  source_release_functions
  TEST_TMPDIR="$(mktemp -d)"
}

teardown() {
  rm -rf "$TEST_TMPDIR"
}

# ─── _load_conf_file ────────────────────────────────────────────────────────────

@test "load_config: reads key=value pairs" {
  cat > "$TEST_TMPDIR/test.conf" <<'EOF'
GITLAB_API_URL=https://self-hosted.example.com/api/v4
DEFAULT_BRANCH=develop
TAG_PREFIX=release-v
REMOTE=upstream
EOF

  REPO_ROOT="$TEST_TMPDIR"
  cp "$TEST_TMPDIR/test.conf" "$TEST_TMPDIR/.release.conf"
  CONFIG_FILE=""
  load_config

  [ "$GITLAB_API_URL" = "https://self-hosted.example.com/api/v4" ]
  [ "$DEFAULT_BRANCH" = "develop" ]
  [ "$TAG_PREFIX" = "release-v" ]
  [ "$REMOTE" = "upstream" ]
}

@test "load_config: ignores comments and blank lines" {
  cat > "$TEST_TMPDIR/.release.conf" <<'EOF'
# This is a comment
GITLAB_API_URL=https://example.com/api/v4

# Another comment
DEFAULT_BRANCH=staging
EOF

  REPO_ROOT="$TEST_TMPDIR"
  CONFIG_FILE=""
  load_config
  [ "$GITLAB_API_URL" = "https://example.com/api/v4" ]
  [ "$DEFAULT_BRANCH" = "staging" ]
}

@test "load_config: strips quotes from values" {
  cat > "$TEST_TMPDIR/.release.conf" <<'EOF'
GITLAB_API_URL="https://quoted.example.com/api/v4"
DEFAULT_BRANCH='develop'
EOF

  REPO_ROOT="$TEST_TMPDIR"
  CONFIG_FILE=""
  load_config
  [ "$GITLAB_API_URL" = "https://quoted.example.com/api/v4" ]
  [ "$DEFAULT_BRANCH" = "develop" ]
}

@test "load_config: explicit --config overrides repo config" {
  # Repo-level config
  cat > "$TEST_TMPDIR/.release.conf" <<'EOF'
DEFAULT_BRANCH=from-repo
EOF

  # Explicit config
  cat > "$TEST_TMPDIR/explicit.conf" <<'EOF'
DEFAULT_BRANCH=from-explicit
EOF

  REPO_ROOT="$TEST_TMPDIR"
  CONFIG_FILE="$TEST_TMPDIR/explicit.conf"
  load_config
  [ "$DEFAULT_BRANCH" = "from-explicit" ]
}

@test "load_config: warns on unknown keys" {
  cat > "$TEST_TMPDIR/.release.conf" <<'EOF'
UNKNOWN_KEY=hello
EOF

  REPO_ROOT="$TEST_TMPDIR"
  CONFIG_FILE=""
  run load_config
  [[ "$output" == *"Unknown config key"* ]]
}

@test "load_config: NO_MR in config warns as unknown key" {
  cat > "$TEST_TMPDIR/.release.conf" <<'EOF'
NO_MR=true
EOF

  REPO_ROOT="$TEST_TMPDIR"
  CONFIG_FILE=""
  run load_config
  [[ "$output" == *"Unknown config key: NO_MR"* ]]
}

@test "load_config: UPDATE_DEFAULT_BRANCH=false in config disables it" {
  cat > "$TEST_TMPDIR/.release.conf" <<'EOF'
UPDATE_DEFAULT_BRANCH=false
EOF

  UPDATE_DEFAULT_BRANCH=true
  REPO_ROOT="$TEST_TMPDIR"
  CONFIG_FILE=""
  _ENV_RELEASE_UPDATE_DEFAULT_BRANCH=""
  load_config
  [ "$UPDATE_DEFAULT_BRANCH" = "false" ]
}

@test "load_config: UPDATE_DEFAULT_BRANCH=true in config enables it" {
  cat > "$TEST_TMPDIR/.release.conf" <<'EOF'
UPDATE_DEFAULT_BRANCH=true
EOF

  UPDATE_DEFAULT_BRANCH=false
  REPO_ROOT="$TEST_TMPDIR"
  CONFIG_FILE=""
  _ENV_RELEASE_UPDATE_DEFAULT_BRANCH=""
  load_config
  [ "$UPDATE_DEFAULT_BRANCH" = "true" ]
}

@test "load_config: RELEASE_UPDATE_DEFAULT_BRANCH env var overrides config" {
  cat > "$TEST_TMPDIR/.release.conf" <<'EOF'
UPDATE_DEFAULT_BRANCH=true
EOF

  UPDATE_DEFAULT_BRANCH=true
  REPO_ROOT="$TEST_TMPDIR"
  CONFIG_FILE=""
  _ENV_RELEASE_UPDATE_DEFAULT_BRANCH="false"
  load_config
  [ "$UPDATE_DEFAULT_BRANCH" = "false" ]
}

@test "load_config: missing --config file exits with error" {
  CONFIG_FILE="$TEST_TMPDIR/nonexistent.conf"
  REPO_ROOT="$TEST_TMPDIR"
  run load_config
  [ "$status" -ne 0 ]
  [[ "$output" == *"Config file not found"* ]]
}

@test "load_config: env vars override config file values" {
  cat > "$TEST_TMPDIR/.release.conf" <<'EOF'
DEFAULT_BRANCH=from-config
TAG_PREFIX=cfg-
EOF

  REPO_ROOT="$TEST_TMPDIR"
  CONFIG_FILE=""
  # Simulate the env var having been present at script startup by setting the
  # snapshot variable that load_config checks for overrides.
  _ENV_RELEASE_DEFAULT_BRANCH="from-env"
  load_config
  [ "$DEFAULT_BRANCH" = "from-env" ]
}

# ─── BUG-4: env var override for GITLAB_TOKEN and GITLAB_API_URL ─────────────

@test "load_config: GITLAB_TOKEN env var overrides config file" {
  cat > "$TEST_TMPDIR/.release.conf" <<'EOF'
GITLAB_TOKEN=config-token
EOF

  REPO_ROOT="$TEST_TMPDIR"
  CONFIG_FILE=""
  _ENV_GITLAB_TOKEN="env-token"
  load_config
  [ "$GITLAB_TOKEN" = "env-token" ]
}

@test "load_config: GITLAB_API_URL env var overrides config file" {
  cat > "$TEST_TMPDIR/.release.conf" <<'EOF'
GITLAB_API_URL=https://from-config.example.com/api/v4
EOF

  REPO_ROOT="$TEST_TMPDIR"
  CONFIG_FILE=""
  _ENV_GITLAB_API_URL="https://from-env.example.com/api/v4"
  load_config
  [ "$GITLAB_API_URL" = "https://from-env.example.com/api/v4" ]
}

# ─── ~/.gitlab_token file support ─────────────────────────────────────────────

@test "load_config: loads token from ~/.gitlab_token file" {
  # Create a fake home with a token file
  local fake_home="$TEST_TMPDIR/fakehome"
  mkdir -p "$fake_home"
  printf 'glpat-test-token-from-file\n' > "$fake_home/.gitlab_token"

  GITLAB_TOKEN=""
  _ENV_GITLAB_TOKEN=""
  REPO_ROOT="$TEST_TMPDIR"
  CONFIG_FILE=""
  HOME="$fake_home" load_config
  [ "$GITLAB_TOKEN" = "glpat-test-token-from-file" ]
}

@test "load_config: env var GITLAB_TOKEN takes priority over ~/.gitlab_token" {
  local fake_home="$TEST_TMPDIR/fakehome"
  mkdir -p "$fake_home"
  printf 'token-from-file\n' > "$fake_home/.gitlab_token"

  GITLAB_TOKEN=""
  _ENV_GITLAB_TOKEN="token-from-env"
  REPO_ROOT="$TEST_TMPDIR"
  CONFIG_FILE=""
  HOME="$fake_home" load_config
  [ "$GITLAB_TOKEN" = "token-from-env" ]
}

@test "load_config: config file GITLAB_TOKEN overrides ~/.gitlab_token" {
  local fake_home="$TEST_TMPDIR/fakehome"
  mkdir -p "$fake_home"
  printf 'token-from-file\n' > "$fake_home/.gitlab_token"

  cat > "$TEST_TMPDIR/.release.conf" <<'EOF'
GITLAB_TOKEN=token-from-conf
EOF

  GITLAB_TOKEN=""
  _ENV_GITLAB_TOKEN=""
  REPO_ROOT="$TEST_TMPDIR"
  CONFIG_FILE=""
  HOME="$fake_home" load_config
  [ "$GITLAB_TOKEN" = "token-from-conf" ]
}

@test "load_config: config file values persist when env var was not set" {
  cat > "$TEST_TMPDIR/.release.conf" <<'EOF'
GITLAB_TOKEN=config-only-token
GITLAB_API_URL=https://config-only.example.com/api/v4
DEFAULT_BRANCH=develop
EOF

  REPO_ROOT="$TEST_TMPDIR"
  CONFIG_FILE=""
  # Clear all snapshots to simulate no env vars at startup
  _ENV_GITLAB_TOKEN=""
  _ENV_GITLAB_API_URL=""
  _ENV_RELEASE_DEFAULT_BRANCH=""
  _ENV_RELEASE_TAG_PREFIX=""
  _ENV_RELEASE_REMOTE=""
  load_config
  [ "$GITLAB_TOKEN" = "config-only-token" ]
  [ "$GITLAB_API_URL" = "https://config-only.example.com/api/v4" ]
  [ "$DEFAULT_BRANCH" = "develop" ]
}

# ─── DEPLOY_BASE_PATH ──────────────────────────────────────────────────────

@test "load_config: DEPLOY_BASE_PATH loaded from conf file" {
  cat > "$TEST_TMPDIR/.release.conf" <<'EOF'
DEPLOY_BASE_PATH=/opt/software
EOF

  REPO_ROOT="$TEST_TMPDIR"
  CONFIG_FILE=""
  _ENV_DEPLOY_BASE_PATH=""
  CLI_DEPLOY_PATH=""
  load_config
  [ "$DEPLOY_BASE_PATH" = "/opt/software" ]
}

@test "load_config: DEPLOY_BASE_PATH env var overrides config" {
  cat > "$TEST_TMPDIR/.release.conf" <<'EOF'
DEPLOY_BASE_PATH=/opt/from-config
EOF

  REPO_ROOT="$TEST_TMPDIR"
  CONFIG_FILE=""
  _ENV_DEPLOY_BASE_PATH="/opt/from-env"
  CLI_DEPLOY_PATH=""
  load_config
  [ "$DEPLOY_BASE_PATH" = "/opt/from-env" ]
}

@test "load_config: trims whitespace from config values" {
  cat > "$TEST_TMPDIR/.release.conf" <<'EOF'
GITLAB_API_URL=https://example.com/api/v4
DEFAULT_BRANCH=  develop
EOF

  REPO_ROOT="$TEST_TMPDIR"
  CONFIG_FILE=""
  _ENV_GITLAB_API_URL=""
  _ENV_RELEASE_DEFAULT_BRANCH=""
  load_config
  [ "$GITLAB_API_URL" = "https://example.com/api/v4" ]
  [ "$DEFAULT_BRANCH" = "develop" ]
}

@test "load_config: CLI --deploy-path overrides env var and config" {
  cat > "$TEST_TMPDIR/.release.conf" <<'EOF'
DEPLOY_BASE_PATH=/opt/from-config
EOF

  REPO_ROOT="$TEST_TMPDIR"
  CONFIG_FILE=""
  _ENV_DEPLOY_BASE_PATH="/opt/from-env"
  CLI_DEPLOY_PATH="/opt/from-cli"
  load_config
  [ "$DEPLOY_BASE_PATH" = "/opt/from-cli" ]
}
