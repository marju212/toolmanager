#!/usr/bin/env bats

load test_helpers

setup() {
  source_release_functions
  start_mock_gitlab
  GITLAB_API_URL="http://127.0.0.1:${MOCK_PORT}/api/v4"
  GITLAB_TOKEN="test-token-12345"
  _GITLAB_API_RETRY_DELAY=0
}

teardown() {
  stop_mock_gitlab
}

# ─── gitlab_api: basic requests ──────────────────────────────────────────────────

@test "gitlab_api: GET request returns project JSON" {
  run gitlab_api GET "/projects/12345"
  [ "$status" -eq 0 ]
  echo "$output" | jq -e '.id == 12345'
  echo "$output" | jq -e '.name == "test-project"'
}

@test "gitlab_api: GET with URL-encoded path" {
  run gitlab_api GET "/projects/group%2Ftest-project"
  [ "$status" -eq 0 ]
  echo "$output" | jq -e '.id == 12345'
}

@test "gitlab_api: PUT updates project" {
  run gitlab_api PUT "/projects/12345" '{"default_branch":"release/v1.0.0"}'
  [ "$status" -eq 0 ]
  echo "$output" | jq -e '.default_branch == "release/v1.0.0"'
}

@test "gitlab_api: POST creates merge request" {
  local body
  body=$(jq -n '{
    source_branch: "release/v1.0.0",
    target_branch: "main",
    title: "Release v1.0.0"
  }')

  run gitlab_api POST "/projects/12345/merge_requests" "$body"
  [ "$status" -eq 0 ]
  echo "$output" | jq -e '.iid == 1'
  echo "$output" | jq -e '.source_branch == "release/v1.0.0"'
  echo "$output" | jq -e '.web_url' | grep -q "merge_requests/1"
}

# ─── gitlab_api: auth errors ────────────────────────────────────────────────────

@test "gitlab_api: fails without GITLAB_TOKEN" {
  GITLAB_TOKEN=""
  run gitlab_api GET "/projects/12345"
  [ "$status" -ne 0 ]
  [[ "$output" == *"GITLAB_TOKEN is not set"* ]]
}

@test "gitlab_api: fails with expired token (mock scenario)" {
  mock_trigger_scenario "fail_auth"
  run gitlab_api GET "/projects/12345"
  [ "$status" -ne 0 ]
  [[ "$output" == *"401"* ]]
}

# ─── gitlab_api: server errors ───────────────────────────────────────────────────

@test "gitlab_api: handles persistent 500 server error" {
  # Use persistent failure so both initial request and retry get 500
  touch "$MOCK_STATE_DIR/fail_server_always"
  run gitlab_api GET "/projects/12345"
  rm -f "$MOCK_STATE_DIR/fail_server_always"
  [ "$status" -ne 0 ]
  [[ "$output" == *"500"* ]]
}

# ─── gitlab_api: 404 ─────────────────────────────────────────────────────────────

@test "gitlab_api: handles 404 for unknown project" {
  mock_trigger_scenario "fail_not_found"
  run gitlab_api GET "/projects/99999"
  [ "$status" -ne 0 ]
  [[ "$output" == *"404"* ]]
}

# ─── gitlab_api: retry on 500 ─────────────────────────────────────────────────────

@test "gitlab_api: retries on 500 and succeeds on second attempt" {
  # fail_server is one-shot: first request gets 500, retry succeeds
  mock_trigger_scenario "fail_server"
  run gitlab_api GET "/projects/12345"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Retrying"* ]]
  [[ "$output" == *"12345"* ]]
}

# ─── SEC-1: token not exposed in process args ────────────────────────────────────

@test "gitlab_api: token is passed via file not command-line arg" {
  # The gitlab_api function should use --header @file, not --header "PRIVATE-TOKEN: ..."
  # We verify by grepping the script source for the pattern
  run grep -c 'header @' "$RELEASE_SCRIPT"
  [ "$status" -eq 0 ]
  # Should find at least one --header @file usage
  [[ "${lines[0]}" -ge 1 ]]

  # And should NOT have inline PRIVATE-TOKEN header
  run grep -c 'header "PRIVATE-TOKEN' "$RELEASE_SCRIPT"
  # grep returns 1 (no match) when count is 0
  [ "${lines[0]}" = "0" ] || [ "$status" -eq 1 ]
}

@test "gitlab_api: token header file is cleaned up after request" {
  # Make a successful request and verify no temp files are left behind
  local before_count after_count
  before_count=$(ls /tmp/tmp.* 2>/dev/null | wc -l || echo 0)

  gitlab_api GET "/projects/12345" >/dev/null

  after_count=$(ls /tmp/tmp.* 2>/dev/null | wc -l || echo 0)
  # Should not have more temp files after the request
  [ "$after_count" -le "$before_count" ]
}

# ─── get_gitlab_project_id ───────────────────────────────────────────────────────

@test "get_gitlab_project_id: parses HTTPS remote URL and fetches project ID" {
  setup_test_repo
  cd "$WORK_REPO"
  DRY_RUN=false
  REMOTE="origin"
  # The git wrapper returns the fake HTTPS GitLab URL for get-url

  get_gitlab_project_id
  [ "$GITLAB_PROJECT_ID" = "12345" ]

  teardown_test_repo
}

@test "get_gitlab_project_id: dry-run skips API call" {
  setup_test_repo
  cd "$WORK_REPO"
  DRY_RUN=true
  REMOTE="origin"

  get_gitlab_project_id
  [ "$GITLAB_PROJECT_ID" = "DRY_RUN_ID" ]

  teardown_test_repo
}

# ─── URL parsing (unit tests using the regex directly) ───────────────────────────

@test "URL parsing: HTTPS with .git suffix" {
  local url="https://gitlab.com/group/project.git"
  local project_path=""
  if [[ "$url" =~ ^https?://[^/]+/(.+)\.git$ ]]; then
    project_path="${BASH_REMATCH[1]}"
  fi
  [ "$project_path" = "group/project" ]
}

@test "URL parsing: HTTPS without .git suffix" {
  local url="https://gitlab.com/group/project"
  local project_path=""
  if [[ "$url" =~ ^https?://[^/]+/(.+)$ ]]; then
    project_path="${BASH_REMATCH[1]}"
  fi
  [ "$project_path" = "group/project" ]
}

@test "URL parsing: SSH with .git suffix" {
  local url="git@gitlab.com:group/project.git"
  local project_path=""
  if [[ "$url" =~ ^git@[^:]+:(.+)\.git$ ]]; then
    project_path="${BASH_REMATCH[1]}"
  fi
  [ "$project_path" = "group/project" ]
}

@test "URL parsing: SSH without .git suffix" {
  local url="git@gitlab.com:group/project"
  local project_path=""
  if [[ "$url" =~ ^git@[^:]+:(.+)$ ]]; then
    project_path="${BASH_REMATCH[1]}"
  fi
  [ "$project_path" = "group/project" ]
}

@test "URL parsing: nested group HTTPS" {
  local url="https://gitlab.com/group/subgroup/project.git"
  local project_path=""
  if [[ "$url" =~ ^https?://[^/]+/(.+)\.git$ ]]; then
    project_path="${BASH_REMATCH[1]}"
  fi
  [ "$project_path" = "group/subgroup/project" ]
}

@test "URL parsing: nested group SSH" {
  local url="git@gitlab.com:group/subgroup/project.git"
  local project_path=""
  if [[ "$url" =~ ^git@[^:]+:(.+)\.git$ ]]; then
    project_path="${BASH_REMATCH[1]}"
  fi
  [ "$project_path" = "group/subgroup/project" ]
}

@test "URL parsing: self-hosted HTTPS" {
  local url="https://gitlab.mycompany.io/team/repo.git"
  local project_path=""
  if [[ "$url" =~ ^https?://[^/]+/(.+)\.git$ ]]; then
    project_path="${BASH_REMATCH[1]}"
  fi
  [ "$project_path" = "team/repo" ]
}

# ─── update_default_branch ───────────────────────────────────────────────────────

@test "update_default_branch: calls PUT on project" {
  GITLAB_PROJECT_ID="12345"
  DRY_RUN=false
  run update_default_branch "release/v2.0.0"
  [ "$status" -eq 0 ]
  [[ "$output" == *"updated"* ]]
}

@test "update_default_branch: dry-run skips API call" {
  GITLAB_PROJECT_ID="12345"
  DRY_RUN=true
  run update_default_branch "release/v2.0.0"
  [ "$status" -eq 0 ]
  [[ "$output" == *"dry-run"* ]]
}

# ─── create_merge_request ────────────────────────────────────────────────────────

@test "create_merge_request: creates MR and returns URL" {
  GITLAB_PROJECT_ID="12345"
  DEFAULT_BRANCH="main"
  TAG_PREFIX="v"
  CHANGELOG="- Feature X"
  DRY_RUN=false

  create_merge_request "release/v1.0.0" "1.0.0"
  [[ "$MR_URL" == *"merge_requests"* ]]
}

@test "create_merge_request: dry-run skips API call" {
  GITLAB_PROJECT_ID="12345"
  DEFAULT_BRANCH="main"
  TAG_PREFIX="v"
  CHANGELOG="- Feature X"
  DRY_RUN=true

  create_merge_request "release/v1.0.0" "1.0.0"
  [[ "$MR_URL" == *"dry-run"* ]]
}
