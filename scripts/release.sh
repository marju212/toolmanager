#!/usr/bin/env bash
#
# release.sh - Automate version management and release branch creation for GitLab repos.
#
# Usage: ./scripts/release.sh [OPTIONS]
#
# Options:
#   --dry-run                  Run all checks without making changes
#   --hotfix-mr BRANCH         Create MR from a release branch back to the default branch
#   --deploy-only              Deploy an existing tagged release (skip branch/tag creation)
#   --update-default-branch    Change GitLab default branch to the release branch (default)
#   --no-update-default-branch Skip changing the GitLab default branch
#   --config FILE              Path to config file
#   --version X.Y.Z            Set release version non-interactively
#   --deploy-path PATH         Deploy base path (overrides DEPLOY_BASE_PATH config)
#   --non-interactive, -n      Auto-confirm all prompts (for CI/CD)
#   --help                     Show this help message
#
set -euo pipefail

# ─── Globals ────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null || pwd)"

DRY_RUN=false
HOTFIX_MR_BRANCH=""
UPDATE_DEFAULT_BRANCH=true
CONFIG_FILE=""
NON_INTERACTIVE=false
CLI_VERSION=""
CLI_DEPLOY_PATH=""
DEPLOY_ONLY=false
CLEANUP_BRANCH=""
CLEANUP_TAG=""

# Snapshot env vars BEFORE config loading so they can override config files.
# _ENV_* vars hold the original environment values (empty string if unset).
_ENV_GITLAB_TOKEN="${GITLAB_TOKEN:-}"
_ENV_GITLAB_API_URL="${GITLAB_API_URL:-}"
_ENV_RELEASE_DEFAULT_BRANCH="${RELEASE_DEFAULT_BRANCH:-}"
_ENV_RELEASE_TAG_PREFIX="${RELEASE_TAG_PREFIX:-}"
_ENV_RELEASE_REMOTE="${RELEASE_REMOTE:-}"
_ENV_GITLAB_VERIFY_SSL="${GITLAB_VERIFY_SSL:-}"
_ENV_RELEASE_UPDATE_DEFAULT_BRANCH="${RELEASE_UPDATE_DEFAULT_BRANCH:-}"
_ENV_DEPLOY_BASE_PATH="${DEPLOY_BASE_PATH:-}"

# Defaults (overridden by config / env)
GITLAB_TOKEN="${GITLAB_TOKEN:-}"
GITLAB_API_URL="${GITLAB_API_URL:-https://gitlab.com/api/v4}"
VERIFY_SSL="${GITLAB_VERIFY_SSL:-true}"
DEFAULT_BRANCH="${RELEASE_DEFAULT_BRANCH:-main}"
TAG_PREFIX="${RELEASE_TAG_PREFIX:-v}"
REMOTE="${RELEASE_REMOTE:-origin}"
DEPLOY_BASE_PATH="${DEPLOY_BASE_PATH:-}"

# ─── Logging ────────────────────────────────────────────────────────────────────

_use_color=false
[[ -t 2 ]] && _use_color=true

_color() {
  if $_use_color; then printf "\e[%sm" "$1"; fi
}
_reset() {
  if $_use_color; then printf "\e[0m"; fi
}

log_info()    { echo -e "$(_color "94")ℹ $*$(_reset)" >&2; }
log_warn()    { echo -e "$(_color "33")⚠ $*$(_reset)" >&2; }
log_error()   { echo -e "$(_color "31")✖ $*$(_reset)" >&2; }
log_success() { echo -e "$(_color "32")✔ $*$(_reset)" >&2; }

# ─── Utility ────────────────────────────────────────────────────────────────────

confirm() {
  local message="${1:-Continue?}"
  if $DRY_RUN; then
    log_info "[dry-run] Would prompt: $message [y/N]"
    return 0
  fi
  if $NON_INTERACTIVE; then
    log_info "[non-interactive] $message [y/N]"
    return 0
  fi
  read -rp "$message [y/N] " answer
  case "$answer" in
    [yY][eE][sS]|[yY]) return 0 ;;
    *) return 1 ;;
  esac
}

validate_semver() {
  local version="$1"
  if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    log_error "Invalid semver format: '$version' (expected X.Y.Z)"
    return 1
  fi
}

# Parse the git remote URL and extract the project path.
# Sets: _PARSED_REMOTE_URL (raw URL), _PARSED_PROJECT_PATH (e.g. group/sub/project)
_parse_remote_url() {
  _PARSED_REMOTE_URL="$(git remote get-url "$REMOTE" 2>/dev/null || true)"

  if [[ -z "$_PARSED_REMOTE_URL" ]]; then
    log_error "Cannot determine remote URL for '$REMOTE'."
    return 1
  fi

  _PARSED_PROJECT_PATH=""

  # SSH: git@gitlab.com:group/subgroup/project.git
  if [[ "$_PARSED_REMOTE_URL" =~ ^git@[^:]+:(.+)\.git$ ]]; then
    _PARSED_PROJECT_PATH="${BASH_REMATCH[1]}"
  elif [[ "$_PARSED_REMOTE_URL" =~ ^git@[^:]+:(.+)$ ]]; then
    _PARSED_PROJECT_PATH="${BASH_REMATCH[1]}"
  # HTTPS: https://gitlab.com/group/subgroup/project.git
  elif [[ "$_PARSED_REMOTE_URL" =~ ^https?://[^/]+/(.+)\.git$ ]]; then
    _PARSED_PROJECT_PATH="${BASH_REMATCH[1]}"
  elif [[ "$_PARSED_REMOTE_URL" =~ ^https?://[^/]+/(.+)$ ]]; then
    _PARSED_PROJECT_PATH="${BASH_REMATCH[1]}"
  fi

  if [[ -z "$_PARSED_PROJECT_PATH" ]]; then
    log_error "Cannot parse project path from remote URL: $_PARSED_REMOTE_URL"
    return 1
  fi
}

_warn_file_permissions() {
  local file="$1"
  if [[ ! -f "$file" ]]; then return; fi
  local mode
  mode="$(stat -c '%a' "$file" 2>/dev/null || stat -f '%Lp' "$file" 2>/dev/null)" || return
  case "$mode" in
    *00) ;;  # Only owner has access — OK
    *)   log_warn "'$file' is accessible by others (mode $mode). Consider: chmod 600 '$file'" ;;
  esac
}

# ─── Prerequisites ──────────────────────────────────────────────────────────────

check_prerequisites() {
  local missing=()
  for cmd in git curl jq; do
    if ! command -v "$cmd" &>/dev/null; then
      missing+=("$cmd")
    fi
  done
  if [[ ${#missing[@]} -gt 0 ]]; then
    log_error "Missing required tools: ${missing[*]}"
    log_error "Install them and try again."
    exit 1
  fi
}

# ─── Argument parsing ───────────────────────────────────────────────────────────

usage() {
  cat <<'EOF'
Usage: release.sh [OPTIONS]

Automate version management and release branch creation for GitLab repos.

Options:
  --dry-run                  Run all checks without making changes
  --hotfix-mr BRANCH         Create MR from a release branch back to the default branch
  --deploy-only              Deploy an existing tagged release (skip branch/tag creation)
  --update-default-branch    Change GitLab default branch to the release branch (default: true)
  --no-update-default-branch Skip changing the GitLab default branch
  --config FILE              Path to config file (default: .release.conf)
  --version X.Y.Z            Set release version non-interactively
  --deploy-path PATH         Deploy base path (overrides DEPLOY_BASE_PATH config)
  --non-interactive, -n      Auto-confirm all prompts (for CI/CD)
  --help                     Show this help message

CI/CD usage:
  ./scripts/release.sh --version 1.2.3 --non-interactive
  GITLAB_TOKEN=\$TOKEN ./scripts/release.sh --version 1.2.3 --non-interactive
  ./scripts/release.sh --deploy-only --version 1.2.3 --non-interactive

Hotfix workflow:
  # 1. Create a release (branch + tag only, no MR)
  ./scripts/release.sh --version 1.2.3 --non-interactive
  # 2. Push hotfix commits to the release branch
  git checkout release/v1.2.3 && git cherry-pick <commit> && git push
  # 3. Create MR from the release branch back to the default branch
  ./scripts/release.sh --hotfix-mr release/v1.2.3

Environment variables:
  GITLAB_TOKEN             GitLab personal access token (required for API calls)
  GITLAB_API_URL           GitLab API base URL (default: https://gitlab.com/api/v4)
  RELEASE_DEFAULT_BRANCH   Branch to release from (default: main)
  RELEASE_TAG_PREFIX       Tag prefix (default: v)
  RELEASE_REMOTE           Git remote name (default: origin)
  GITLAB_VERIFY_SSL        Verify SSL certificates (default: true, set to false for self-signed certs)
  RELEASE_UPDATE_DEFAULT_BRANCH  Update GitLab default branch to release branch (default: true)
  DEPLOY_BASE_PATH           Base path for deploy (clone + modulefile). If unset, deploy is skipped.

Token resolution (first match wins):
  GITLAB_TOKEN env var     Exported shell variable (highest priority)
  .release.conf            GITLAB_TOKEN key in any config file
  ~/.gitlab_token          Plain-text file containing just the token

Config files (loaded in order, later values win):
  ~/.release.conf          User-level config
  <repo>/.release.conf     Repo-level config
  --config FILE            Explicit config file
  Environment variables    Highest priority
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dry-run)
        DRY_RUN=true
        shift
        ;;
      --hotfix-mr)
        if [[ -z "${2:-}" ]]; then
          log_error "--hotfix-mr requires a branch name argument"
          exit 1
        fi
        if $DEPLOY_ONLY; then
          log_error "--hotfix-mr cannot be combined with --deploy-only"
          exit 1
        fi
        HOTFIX_MR_BRANCH="$2"
        shift 2
        ;;
      --update-default-branch)
        UPDATE_DEFAULT_BRANCH=true
        shift
        ;;
      --no-update-default-branch)
        UPDATE_DEFAULT_BRANCH=false
        shift
        ;;
      --config)
        if [[ -z "${2:-}" ]]; then
          log_error "--config requires a file path argument"
          exit 1
        fi
        CONFIG_FILE="$2"
        shift 2
        ;;
      --version)
        if [[ -z "${2:-}" ]]; then
          log_error "--version requires a version argument (X.Y.Z)"
          exit 1
        fi
        CLI_VERSION="$2"
        shift 2
        ;;
      --non-interactive|-n)
        NON_INTERACTIVE=true
        shift
        ;;
      --help|-h)
        usage
        exit 0
        ;;
      --deploy-only)
        if [[ -n "$HOTFIX_MR_BRANCH" ]]; then
          log_error "--deploy-only cannot be combined with --hotfix-mr"
          exit 1
        fi
        DEPLOY_ONLY=true
        shift
        ;;
      --deploy-path)
        if [[ -z "${2:-}" ]]; then
          log_error "--deploy-path requires a directory path argument"
          exit 1
        fi
        CLI_DEPLOY_PATH="$2"
        shift 2
        ;;
      *)
        log_error "Unknown option: $1"
        usage
        exit 1
        ;;
    esac
  done
}

# ─── Configuration ──────────────────────────────────────────────────────────────

_load_conf_file() {
  local file="$1"
  if [[ -f "$file" ]]; then
    _warn_file_permissions "$file"
    log_info "Loading config: $file"
    # Source in a subshell-safe way: only accept known variables
    local line key value
    while IFS='=' read -r key value || [[ -n "$key" ]]; do
      # Skip comments and blank lines
      key="${key// /}"
      [[ -z "$key" || "$key" == \#* ]] && continue
      # Strip surrounding quotes from value
      value="${value#\"}"
      value="${value%\"}"
      value="${value#\'}"
      value="${value%\'}"
      # Trim leading and trailing whitespace from value
      value="${value#"${value%%[![:space:]]*}"}"
      value="${value%"${value##*[![:space:]]}"}"
      case "$key" in
        GITLAB_TOKEN)       GITLAB_TOKEN="$value" ;;
        GITLAB_API_URL)     GITLAB_API_URL="$value" ;;
        DEFAULT_BRANCH)     DEFAULT_BRANCH="$value" ;;
        TAG_PREFIX)         TAG_PREFIX="$value" ;;
        REMOTE)             REMOTE="$value" ;;
        VERIFY_SSL)         VERIFY_SSL="$value" ;;
        UPDATE_DEFAULT_BRANCH) if [[ "$value" == "true" ]]; then UPDATE_DEFAULT_BRANCH=true; elif [[ "$value" == "false" ]]; then UPDATE_DEFAULT_BRANCH=false; fi ;;
        DEPLOY_BASE_PATH)   DEPLOY_BASE_PATH="$value" ;;
        *)                  log_warn "Unknown config key: $key" ;;
      esac
    done < "$file"
  fi
}

load_config() {
  # 0. Load token from ~/.gitlab_token file if it exists and token is not
  #    already set via environment variable
  if [[ -z "$GITLAB_TOKEN" && -f "$HOME/.gitlab_token" ]]; then
    _warn_file_permissions "$HOME/.gitlab_token"
    GITLAB_TOKEN="$(<"$HOME/.gitlab_token")"
    # Strip whitespace/newlines
    GITLAB_TOKEN="${GITLAB_TOKEN%"${GITLAB_TOKEN##*[![:space:]]}"}"
    if [[ -n "$GITLAB_TOKEN" ]]; then
      log_info "Loaded token from ~/.gitlab_token"
    fi
  fi

  # 1. User-level config
  _load_conf_file "$HOME/.release.conf"

  # 2. Repo-level config
  _load_conf_file "$REPO_ROOT/.release.conf"

  # 3. Explicit --config file
  if [[ -n "$CONFIG_FILE" ]]; then
    if [[ ! -f "$CONFIG_FILE" ]]; then
      log_error "Config file not found: $CONFIG_FILE"
      exit 1
    fi
    _load_conf_file "$CONFIG_FILE"
  fi

  # 4. Env vars override everything — re-apply from the snapshots saved at
  #    startup so that config file values don't shadow environment variables.
  if [[ -n "$_ENV_GITLAB_TOKEN" ]]; then           GITLAB_TOKEN="$_ENV_GITLAB_TOKEN"; fi
  if [[ -n "$_ENV_GITLAB_API_URL" ]]; then         GITLAB_API_URL="$_ENV_GITLAB_API_URL"; fi
  if [[ -n "$_ENV_RELEASE_DEFAULT_BRANCH" ]]; then DEFAULT_BRANCH="$_ENV_RELEASE_DEFAULT_BRANCH"; fi
  if [[ -n "$_ENV_RELEASE_TAG_PREFIX" ]]; then     TAG_PREFIX="$_ENV_RELEASE_TAG_PREFIX"; fi
  if [[ -n "$_ENV_RELEASE_REMOTE" ]]; then         REMOTE="$_ENV_RELEASE_REMOTE"; fi
  if [[ -n "$_ENV_GITLAB_VERIFY_SSL" ]]; then     VERIFY_SSL="$_ENV_GITLAB_VERIFY_SSL"; fi
  if [[ -n "$_ENV_RELEASE_UPDATE_DEFAULT_BRANCH" ]]; then
    if [[ "$_ENV_RELEASE_UPDATE_DEFAULT_BRANCH" == "true" ]]; then UPDATE_DEFAULT_BRANCH=true; else UPDATE_DEFAULT_BRANCH=false; fi
  fi
  if [[ -n "$_ENV_DEPLOY_BASE_PATH" ]]; then    DEPLOY_BASE_PATH="$_ENV_DEPLOY_BASE_PATH"; fi

  # CLI --deploy-path overrides everything
  if [[ -n "$CLI_DEPLOY_PATH" ]]; then           DEPLOY_BASE_PATH="$CLI_DEPLOY_PATH"; fi
}

# ─── Branch validation ──────────────────────────────────────────────────────────

check_branch() {
  log_info "Checking repository state..."

  # Must be inside a git repo
  if ! git rev-parse --is-inside-work-tree &>/dev/null; then
    log_error "Not inside a git repository."
    exit 1
  fi

  # Must be on the default branch (or at its tip in detached HEAD for CI)
  local current_branch _detached_head=false
  if current_branch="$(git symbolic-ref --short HEAD 2>/dev/null)"; then
    if [[ "$current_branch" != "$DEFAULT_BRANCH" ]]; then
      log_error "Must be on '$DEFAULT_BRANCH' branch (currently on '$current_branch')."
      exit 1
    fi
  else
    _detached_head=true
    log_info "Detached HEAD detected (common in CI environments)."
  fi

  # Working tree must be clean
  if [[ -n "$(git status --porcelain)" ]]; then
    log_error "Working tree is dirty. Commit or stash changes first."
    exit 1
  fi

  # Fetch latest from remote
  log_info "Fetching from $REMOTE..."
  if ! git fetch "$REMOTE" --tags --quiet; then
    log_error "Failed to fetch from '$REMOTE'. Check credentials and network connectivity."
    exit 1
  fi

  # Must be in sync with remote
  local local_sha remote_sha
  local_sha="$(git rev-parse HEAD)"
  remote_sha="$(git rev-parse "$REMOTE/$DEFAULT_BRANCH" 2>/dev/null || echo "")"

  if [[ -z "$remote_sha" ]]; then
    log_warn "Remote branch '$REMOTE/$DEFAULT_BRANCH' not found. Continuing anyway."
  elif [[ "$local_sha" != "$remote_sha" ]]; then
    if $_detached_head; then
      log_error "HEAD is not at the tip of '$REMOTE/$DEFAULT_BRANCH'."
      log_error "Ensure the CI job checks out the latest '$DEFAULT_BRANCH' commit."
    else
      log_error "Local '$DEFAULT_BRANCH' is not in sync with '$REMOTE/$DEFAULT_BRANCH'."
      log_error "Pull or push changes before releasing."
    fi
    exit 1
  fi

  log_success "Repository is clean and in sync."
}

# ─── Version management ─────────────────────────────────────────────────────────

get_latest_version() {
  local version_stripped
  local all_tags
  all_tags="$(git tag --list "${TAG_PREFIX}*" --sort=-v:refname 2>/dev/null || true)"

  # Filter to only strict semver tags (X.Y.Z after stripping prefix)
  while IFS= read -r tag; do
    [[ -z "$tag" ]] && continue
    version_stripped="${tag#"$TAG_PREFIX"}"
    if [[ "$version_stripped" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
      echo "$version_stripped"
      return 0
    fi
  done <<< "$all_tags"

  echo "0.0.0"
}

suggest_versions() {
  local current="$1"
  local major minor patch
  IFS='.' read -r major minor patch <<< "$current"

  SUGGESTED_PATCH="$major.$minor.$((patch + 1))"
  SUGGESTED_MINOR="$major.$((minor + 1)).0"
  SUGGESTED_MAJOR="$((major + 1)).0.0"
}

check_version_available() {
  local version="$1"
  # Check if tag already exists
  if git rev-parse "${TAG_PREFIX}${version}" &>/dev/null; then
    log_error "Tag '${TAG_PREFIX}${version}' already exists."
    exit 1
  fi
  # Check if release branch already exists
  local branch_name="release/${TAG_PREFIX}${version}"
  if git rev-parse --verify "$branch_name" &>/dev/null || \
     git rev-parse --verify "$REMOTE/$branch_name" &>/dev/null; then
    log_error "Branch '$branch_name' already exists."
    exit 1
  fi
}

prompt_version() {
  local current="$1"
  suggest_versions "$current"

  echo "" >&2
  echo "Current version: ${TAG_PREFIX}${current}" >&2
  echo "" >&2
  echo "  1) Patch  → ${TAG_PREFIX}${SUGGESTED_PATCH}" >&2
  echo "  2) Minor  → ${TAG_PREFIX}${SUGGESTED_MINOR}" >&2
  echo "  3) Major  → ${TAG_PREFIX}${SUGGESTED_MAJOR}" >&2
  echo "  4) Custom" >&2
  echo "" >&2

  local choice
  read -rp "Select version bump [1-4]: " choice

  case "$choice" in
    1) NEW_VERSION="$SUGGESTED_PATCH" ;;
    2) NEW_VERSION="$SUGGESTED_MINOR" ;;
    3) NEW_VERSION="$SUGGESTED_MAJOR" ;;
    4)
      read -rp "Enter version (X.Y.Z): " NEW_VERSION
      validate_semver "$NEW_VERSION"
      ;;
    *)
      log_error "Invalid choice: $choice"
      exit 1
      ;;
  esac

  check_version_available "$NEW_VERSION"
  log_success "Will release ${TAG_PREFIX}${NEW_VERSION}"
}

# ─── Changelog ──────────────────────────────────────────────────────────────────

generate_changelog() {
  local current_tag="${TAG_PREFIX}${1}"
  local changelog

  log_info "Generating changelog..."

  if git rev-parse "$current_tag" &>/dev/null; then
    changelog="$(git log "${current_tag}..HEAD" --pretty=format:"- %s (%h)" --no-merges)"
  else
    changelog="$(git log --pretty=format:"- %s (%h)" --no-merges)"
  fi

  if [[ -z "$changelog" ]]; then
    changelog="- No changes recorded"
  fi

  CHANGELOG="$changelog"
  echo "" >&2
  echo "── Changelog ──────────────────────────────────" >&2
  echo "$CHANGELOG" >&2
  echo "────────────────────────────────────────────────" >&2
  echo "" >&2
}

# ─── GitLab API ─────────────────────────────────────────────────────────────────

gitlab_api() {
  local method="$1"
  local endpoint="$2"
  local data="${3:-}"

  if [[ -z "$GITLAB_TOKEN" ]]; then
    log_error "GITLAB_TOKEN is not set. Export it or add it to .release.conf."
    exit 1
  fi

  local url="${GITLAB_API_URL}${endpoint}"
  local response http_code

  # Write token header to a temp file to avoid exposing it in process args.
  local header_file
  header_file="$(mktemp)"
  printf 'PRIVATE-TOKEN: %s' "$GITLAB_TOKEN" > "$header_file"

  local curl_args=(
    --silent --show-error
    --location
    --connect-timeout 10
    --max-time 30
    --write-out "\n%{http_code}"
    --header @"$header_file"
    --header "Content-Type: application/json"
    --request "$method"
  )

  if [[ "$VERIFY_SSL" == "false" ]]; then
    curl_args+=(--insecure)
  fi

  if [[ -n "$data" ]]; then
    curl_args+=(--data "$data")
  fi

  local max_retries=1
  local attempt
  for (( attempt = 0; attempt <= max_retries; attempt++ )); do
    if (( attempt > 0 )); then
      log_warn "Retrying GitLab API request (attempt $((attempt + 1)))..."
      sleep "${_GITLAB_API_RETRY_DELAY:-2}"
    fi

    if ! response="$(curl "${curl_args[@]}" "$url" 2>&1)"; then
      if (( attempt < max_retries )); then continue; fi
      rm -f "$header_file"
      log_error "Failed to connect to GitLab API: $url"
      log_error "curl error: $response"
      log_error "Check GITLAB_API_URL, network connectivity, and SSL settings (VERIFY_SSL=false for self-signed certs)."
      return 1
    fi

    http_code="$(echo "$response" | tail -n1)"
    response="$(echo "$response" | sed '$d')"

    # Validate http_code is numeric
    if ! [[ "$http_code" =~ ^[0-9]+$ ]]; then
      if (( attempt < max_retries )); then continue; fi
      rm -f "$header_file"
      log_error "Unexpected response from GitLab API (no HTTP status code)"
      return 1
    fi

    # Retry on server errors (5xx)
    if [[ "$http_code" -ge 500 ]] && (( attempt < max_retries )); then
      log_warn "GitLab API returned HTTP $http_code, will retry..."
      continue
    fi

    break
  done

  rm -f "$header_file"

  if [[ "$http_code" -lt 200 || "$http_code" -ge 300 ]]; then
    log_error "GitLab API error (HTTP $http_code): $endpoint"
    log_error "Response: $response"
    return 1
  fi

  echo "$response"
}

get_gitlab_project_id() {
  _parse_remote_url
  local project_path="$_PARSED_PROJECT_PATH"

  # URL-encode the full project path (slashes, spaces, special chars)
  local encoded_path
  encoded_path="$(jq -rn --arg p "$project_path" '$p | @uri')"

  log_info "Detecting GitLab project ID for: $project_path"

  if $DRY_RUN; then
    log_info "[dry-run] Would query GitLab API for project ID"
    GITLAB_PROJECT_ID="DRY_RUN_ID"
    return 0
  fi

  local response
  response="$(gitlab_api GET "/projects/${encoded_path}")"
  GITLAB_PROJECT_ID="$(echo "$response" | jq -r '.id')"

  if [[ -z "$GITLAB_PROJECT_ID" || "$GITLAB_PROJECT_ID" == "null" ]]; then
    log_error "Could not determine GitLab project ID."
    log_error "Check that GITLAB_TOKEN has access to: $project_path"
    exit 1
  fi

  log_success "Project ID: $GITLAB_PROJECT_ID"
}

update_default_branch() {
  local branch_name="$1"

  log_info "Updating GitLab default branch to '$branch_name'..."

  if $DRY_RUN; then
    log_info "[dry-run] Would update default branch to '$branch_name'"
    return 0
  fi

  gitlab_api PUT "/projects/${GITLAB_PROJECT_ID}" \
    "{\"default_branch\": \"${branch_name}\"}" >/dev/null

  log_success "Default branch updated to '$branch_name'."
}

create_merge_request() {
  local source_branch="$1"
  local version="$2"
  local mr_title="${3:-Release ${TAG_PREFIX}${version}}"
  local mr_desc
  mr_desc="${4:-## Release ${TAG_PREFIX}${version}

${CHANGELOG}}"

  log_info "Creating merge request: $source_branch → $DEFAULT_BRANCH"

  if $DRY_RUN; then
    log_info "[dry-run] Would create MR: $source_branch → $DEFAULT_BRANCH"
    MR_URL="https://gitlab.com (dry-run)"
    return 0
  fi

  local body
  body=$(jq -n \
    --arg source "$source_branch" \
    --arg target "$DEFAULT_BRANCH" \
    --arg title "$mr_title" \
    --arg desc "$mr_desc" \
    '{
      source_branch: $source,
      target_branch: $target,
      title: $title,
      description: $desc,
      remove_source_branch: false
    }')

  local response
  response="$(gitlab_api POST "/projects/${GITLAB_PROJECT_ID}/merge_requests" "$body")"
  MR_URL="$(echo "$response" | jq -r '.web_url')"

  if [[ -z "$MR_URL" || "$MR_URL" == "null" ]]; then
    log_warn "Merge request created but could not retrieve URL."
    MR_URL="(unknown)"
  else
    log_success "Merge request created: $MR_URL"
  fi
}

# ─── Hotfix MR flow ─────────────────────────────────────────────────────────────

hotfix_mr_flow() {
  local branch="$HOTFIX_MR_BRANCH"

  log_info "Fetching from $REMOTE..."
  if ! git fetch "$REMOTE" --tags --quiet; then
    log_error "Failed to fetch from '$REMOTE'. Check credentials and network connectivity."
    exit 1
  fi

  # Verify branch exists on remote
  if ! git rev-parse --verify "$REMOTE/$branch" &>/dev/null; then
    log_error "Branch '$branch' does not exist on remote '$REMOTE'."
    exit 1
  fi

  # Verify branch has commits ahead of the default branch
  local ahead_count
  ahead_count="$(git rev-list --count "$REMOTE/$DEFAULT_BRANCH".."$REMOTE/$branch")"
  if [[ "$ahead_count" -eq 0 ]]; then
    log_error "Branch '$branch' has no commits ahead of '$DEFAULT_BRANCH'. Nothing to merge."
    exit 1
  fi
  log_info "Branch '$branch' is $ahead_count commit(s) ahead of '$DEFAULT_BRANCH'."

  # Extract version from branch name (release/${TAG_PREFIX}X.Y.Z pattern)
  local version=""
  if [[ "$branch" =~ ^release/${TAG_PREFIX}(.+)$ ]]; then
    version="${BASH_REMATCH[1]}"
  else
    version="$branch"
  fi

  # Generate changelog from commits ahead of default branch
  local changelog
  changelog="$(git log "$REMOTE/$DEFAULT_BRANCH".."$REMOTE/$branch" --pretty=format:"- %s (%h)" --no-merges)"
  if [[ -z "$changelog" ]]; then
    changelog="- No changes recorded"
  fi
  CHANGELOG="$changelog"

  echo "" >&2
  echo "── Hotfix Changelog ───────────────────────────" >&2
  echo "$CHANGELOG" >&2
  echo "────────────────────────────────────────────────" >&2
  echo "" >&2

  if ! confirm "Create merge request from '$branch' to '$DEFAULT_BRANCH'?"; then
    log_warn "Hotfix MR cancelled."
    exit 0
  fi

  get_gitlab_project_id

  local mr_title="Hotfix ${TAG_PREFIX}${version} merge back to ${DEFAULT_BRANCH}"
  local mr_desc="## Hotfix ${TAG_PREFIX}${version}

${CHANGELOG}"
  create_merge_request "$branch" "$version" "$mr_title" "$mr_desc"

  echo "" >&2
  log_success "Hotfix MR created: ${MR_URL:-n/a}"
}

# ─── Deploy-only flow ────────────────────────────────────────────────────────────

deploy_only_flow() {
  if [[ -z "$DEPLOY_BASE_PATH" ]]; then
    log_error "DEPLOY_BASE_PATH is not configured. Set it via config file, environment variable, or --deploy-path."
    return 1
  fi

  log_info "Fetching tags from $REMOTE..."
  if ! git fetch "$REMOTE" --tags --quiet; then
    log_error "Failed to fetch from '$REMOTE'. Check credentials and network connectivity."
    return 1
  fi

  local version=""
  if [[ -n "$CLI_VERSION" ]]; then
    validate_semver "$CLI_VERSION"
    version="$CLI_VERSION"
  else
    # Interactive version prompt with retry loop
    local latest
    latest="$(get_latest_version)"
    while true; do
      echo "" >&2
      echo "Latest tag: ${TAG_PREFIX}${latest}" >&2
      echo "" >&2
      read -rp "Enter version to deploy (X.Y.Z): " version
      if [[ -z "$version" ]]; then
        log_warn "Version cannot be empty. Try again."
        continue
      fi
      if ! validate_semver "$version"; then
        continue
      fi
      # Check tag exists
      if ! git rev-parse "${TAG_PREFIX}${version}" &>/dev/null; then
        log_warn "Tag '${TAG_PREFIX}${version}' does not exist. Try again."
        continue
      fi
      break
    done
  fi

  # For CLI mode, validate tag exists (no retry)
  if [[ -n "$CLI_VERSION" ]]; then
    if ! git rev-parse "${TAG_PREFIX}${version}" &>/dev/null; then
      log_error "Tag '${TAG_PREFIX}${version}' does not exist."
      return 1
    fi
  fi

  if ! confirm "Deploy ${TAG_PREFIX}${version} to ${DEPLOY_BASE_PATH}?"; then
    log_warn "Deploy cancelled."
    return 0
  fi

  deploy_release "$version"
  log_success "Deploy of ${TAG_PREFIX}${version} completed!"
}

# ─── Interactive menu ────────────────────────────────────────────────────────────

show_main_menu() {
  while true; do
    echo "" >&2
    echo "What would you like to do?" >&2
    echo "" >&2
    echo "  1) Release        Create release branch + tag (+ optional deploy)" >&2
    echo "  2) Deploy only    Deploy an existing tagged release" >&2
    echo "  3) Hotfix MR      Create MR from a release branch to ${DEFAULT_BRANCH}" >&2
    echo "" >&2

    local choice
    read -rp "Select an option [1-3]: " choice

    case "$choice" in
      1)
        # Fall through to full release flow
        return 0
        ;;
      2)
        DEPLOY_ONLY=true
        return 0
        ;;
      3)
        while true; do
          read -rp "Enter release branch name (e.g. release/v1.2.3): " HOTFIX_MR_BRANCH
          if [[ -n "$HOTFIX_MR_BRANCH" ]]; then
            break
          fi
          log_warn "Branch name cannot be empty. Try again."
        done
        return 0
        ;;
      *)
        log_warn "Invalid choice: '$choice'. Please enter 1, 2, or 3."
        ;;
    esac
  done
}

# ─── Git operations ─────────────────────────────────────────────────────────────

create_release_branch() {
  local branch_name="$1"

  log_info "Creating branch '$branch_name'..."

  if $DRY_RUN; then
    log_info "[dry-run] Would create and push branch '$branch_name'"
    return 0
  fi

  git checkout -b "$branch_name"
  CLEANUP_BRANCH="$branch_name"

  git push -u "$REMOTE" "$branch_name"
  log_success "Branch '$branch_name' created and pushed."
}

tag_release() {
  local tag_name="$1"
  local version="$2"

  log_info "Creating annotated tag '$tag_name'..."

  if $DRY_RUN; then
    log_info "[dry-run] Would create and push tag '$tag_name'"
    return 0
  fi

  git tag -a "$tag_name" -m "Release $version

${CHANGELOG}"
  CLEANUP_TAG="$tag_name"

  git push "$REMOTE" "$tag_name"
  log_success "Tag '$tag_name' created and pushed."
}

# ─── Cleanup ────────────────────────────────────────────────────────────────────

cleanup_on_failure() {
  local exit_code=$?
  if [[ $exit_code -eq 0 ]]; then
    return 0
  fi

  log_warn "Release failed — cleaning up partial artifacts..."

  if [[ -n "$CLEANUP_TAG" ]]; then
    log_warn "Deleting remote tag '$CLEANUP_TAG'..."
    git push "$REMOTE" --delete "$CLEANUP_TAG" 2>/dev/null || true
    git tag -d "$CLEANUP_TAG" 2>/dev/null || true
  fi

  if [[ -n "$CLEANUP_BRANCH" ]]; then
    log_warn "Deleting remote branch '$CLEANUP_BRANCH'..."
    git push "$REMOTE" --delete "$CLEANUP_BRANCH" 2>/dev/null || true
    git checkout "$DEFAULT_BRANCH" 2>/dev/null || git checkout "$REMOTE/$DEFAULT_BRANCH" 2>/dev/null || true
    git branch -D "$CLEANUP_BRANCH" 2>/dev/null || true
  fi

  log_error "Release aborted. All partial changes have been cleaned up."
}

# ─── Summary ────────────────────────────────────────────────────────────────────

print_summary() {
  local version="$1"
  local branch="$2"
  local tag="$3"
  local mr_url="${4:-}"

  local rows=(
    "Version:  ${TAG_PREFIX}${version}"
    "Branch:   ${branch}"
    "Tag:      ${tag}"
  )
  if [[ -n "$mr_url" ]]; then
    rows+=("MR:       ${mr_url}")
  fi

  # Determine inner width: minimum 42, or longest row + 4 for padding
  local min_width=42
  local inner_width=$min_width
  local title="Release Summary"
  for row in "${rows[@]}"; do
    local len=${#row}
    if (( len + 4 > inner_width )); then
      inner_width=$(( len + 4 ))
    fi
  done

  local border=""
  for (( i = 0; i < inner_width + 4; i++ )); do border+="═"; done

  local title_pad=$(( inner_width - ${#title} ))
  local title_left=$(( title_pad / 2 ))
  local title_right=$(( title_pad - title_left ))

  echo "" >&2
  printf "╔%s╗\n" "$border" >&2
  printf "║  %*s%s%*s  ║\n" "$title_left" "" "$title" "$title_right" "" >&2
  printf "╠%s╣\n" "$border" >&2
  for row in "${rows[@]}"; do
    printf "║  %-${inner_width}s  ║\n" "$row" >&2
  done
  printf "╚%s╝\n" "$border" >&2
  echo "" >&2

  if $DRY_RUN; then
    log_warn "This was a dry run. No changes were made."
  fi
}

# ─── Deploy ──────────────────────────────────────────────────────────────────

extract_tool_name() {
  _parse_remote_url

  # Extract the last path component as the tool name
  TOOL_NAME="${_PARSED_PROJECT_PATH##*/}"
  # Expose the remote URL so callers don't need a second git call
  REMOTE_URL="$_PARSED_REMOTE_URL"
}

deploy_release() {
  local version="$1"
  local release_tag="${TAG_PREFIX}${version}"

  # Validate that DEPLOY_BASE_PATH is an absolute path
  if [[ "$DEPLOY_BASE_PATH" != /* ]]; then
    log_error "DEPLOY_BASE_PATH must be an absolute path: $DEPLOY_BASE_PATH"
    return 1
  fi

  extract_tool_name
  local remote_url="$REMOTE_URL"

  local deploy_dir="${DEPLOY_BASE_PATH}/${TOOL_NAME}/${version}"
  local mf_dir="${DEPLOY_BASE_PATH}/mf/${TOOL_NAME}"
  local mf_file="${mf_dir}/${version}"

  # Clone step
  if [[ -d "$deploy_dir" ]]; then
    log_error "Deploy directory already exists: $deploy_dir"
    return 1
  elif $DRY_RUN; then
    log_info "[dry-run] Would clone ${release_tag} into ${deploy_dir}"
  else
    log_info "Cloning ${release_tag} into ${deploy_dir}..."
    mkdir -p "$(dirname "$deploy_dir")"
    git clone --branch "$release_tag" --depth 1 "$remote_url" "$deploy_dir"
    log_success "Cloned ${release_tag} into ${deploy_dir}"
  fi

  # Modulefile step
  if [[ -f "$mf_file" ]]; then
    log_error "Modulefile already exists: $mf_file"
    if ! $DRY_RUN && [[ -d "$deploy_dir" ]]; then
      log_warn "Removing clone directory due to failed deploy: $deploy_dir"
      rm -rf "$deploy_dir"
    fi
    return 1
  else
    # Check for an existing modulefile from a previous version to use as base
    local latest_mf=""
    if [[ -d "$mf_dir" ]]; then
      latest_mf="$(ls "$mf_dir" 2>/dev/null | grep -E '^[0-9]+\.[0-9]+\.[0-9]+$' | sort -t. -k1,1n -k2,2n -k3,3n | tail -n1)"
      if [[ -n "$latest_mf" ]]; then
        latest_mf="${mf_dir}/${latest_mf}"
      fi
    fi

    if $DRY_RUN; then
      if [[ -n "$latest_mf" && -f "$latest_mf" ]]; then
        log_info "[dry-run] Would copy modulefile from ${latest_mf} to ${mf_file} (updating version to ${version})"
      else
        log_info "[dry-run] Would write modulefile to ${mf_file}"
      fi
    elif [[ -n "$latest_mf" && -f "$latest_mf" ]]; then
      log_info "Copying modulefile from ${latest_mf} to ${mf_file}..."
      mkdir -p "$mf_dir"
      # Copy previous modulefile and update version references
      local prev_version prev_version_escaped
      prev_version="$(basename "$latest_mf")"
      prev_version_escaped="${prev_version//./\\.}"
      sed "s/${prev_version_escaped}/${version}/g" "$latest_mf" > "$mf_file"
      log_success "Modulefile copied and updated to ${mf_file}"
    else
      log_info "Writing modulefile to ${mf_file}..."
      mkdir -p "$mf_dir"
      cat > "$mf_file" <<MODEOF
#%Module1.0
##
## ${TOOL_NAME}/${version} modulefile
##

proc ModulesHelp { } {
    puts stderr "${TOOL_NAME} version ${version}"
}

module-whatis "${TOOL_NAME} version ${version}"

conflict ${TOOL_NAME}

set root ${DEPLOY_BASE_PATH}/${TOOL_NAME}/${version}

prepend-path PATH \$root/bin
MODEOF
      log_success "Modulefile written to ${mf_file}"
    fi
  fi
}

# ─── Main ───────────────────────────────────────────────────────────────────────

main() {
  parse_args "$@"
  load_config
  check_prerequisites

  if $DRY_RUN; then
    log_warn "Running in dry-run mode — no changes will be made."
    echo "" >&2
  fi

  # Dispatch to hotfix MR flow if requested via CLI
  if [[ -n "$HOTFIX_MR_BRANCH" ]]; then
    hotfix_mr_flow
    return 0
  fi

  # Dispatch to deploy-only flow if requested via CLI
  if $DEPLOY_ONLY; then
    deploy_only_flow
    return 0
  fi

  # Show interactive menu when no mode flag, no --version, and stdin is a TTY
  if [[ -z "$CLI_VERSION" ]] && [[ -t 0 ]]; then
    show_main_menu

    # Re-dispatch based on menu selection
    if [[ -n "$HOTFIX_MR_BRANCH" ]]; then
      hotfix_mr_flow
      return 0
    fi
    if $DEPLOY_ONLY; then
      deploy_only_flow
      return 0
    fi
    # Choice 1 (Release) falls through to the full release flow below
  fi

  # Set up cleanup trap
  trap cleanup_on_failure EXIT

  # Validate repo state
  check_branch

  # Version selection
  local current_version
  current_version="$(get_latest_version)"

  if [[ -n "$CLI_VERSION" ]]; then
    validate_semver "$CLI_VERSION"
    NEW_VERSION="$CLI_VERSION"
    check_version_available "$NEW_VERSION"
    log_success "Will release ${TAG_PREFIX}${NEW_VERSION}"
  else
    prompt_version "$current_version"
  fi

  local release_branch="release/${TAG_PREFIX}${NEW_VERSION}"
  local release_tag="${TAG_PREFIX}${NEW_VERSION}"

  # Generate changelog
  generate_changelog "$current_version"

  # Confirm before proceeding
  if ! confirm "Create release ${release_tag}?"; then
    log_warn "Release cancelled."
    trap - EXIT
    exit 0
  fi

  # Detect GitLab project (needed for API calls)
  get_gitlab_project_id

  # Create release branch
  create_release_branch "$release_branch"

  # Create annotated tag
  tag_release "$release_tag" "$NEW_VERSION"

  # Optionally update default branch on GitLab
  if $UPDATE_DEFAULT_BRANCH; then
    if confirm "Update GitLab default branch to '${release_branch}'?"; then
      update_default_branch "$release_branch"
    else
      log_info "Skipping default branch update."
    fi
  fi

  # Disable cleanup trap — we succeeded
  trap - EXIT

  # Switch back to default branch
  if ! $DRY_RUN; then
    git checkout "$DEFAULT_BRANCH" 2>/dev/null || git checkout "$REMOTE/$DEFAULT_BRANCH" 2>/dev/null || true
  fi

  # Print summary
  print_summary "$NEW_VERSION" "$release_branch" "$release_tag"

  # Deploy prompt (only if DEPLOY_BASE_PATH is configured)
  if [[ -n "$DEPLOY_BASE_PATH" ]]; then
    if confirm "Deploy ${release_tag} to ${DEPLOY_BASE_PATH}?"; then
      deploy_release "$NEW_VERSION" || log_warn "Deploy failed (release itself succeeded)."
    else
      log_info "Deploy skipped."
    fi
  fi

  log_success "Release ${release_tag} completed!"
}

if [[ "${_SOURCED_FOR_TESTING:-}" != "true" ]]; then
    main "$@"
fi
