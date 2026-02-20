# dev-utils

A collection of utility scripts for DevOps workflows.

## release.sh

Automates version management and release branch creation for GitLab repositories. The script handles the full release lifecycle: version bumping, changelog generation, release branch creation, and tagging. Merge requests are created separately via the `--hotfix-mr` flag after hotfix commits have been pushed to the release branch.

### Prerequisites

The following tools must be installed and available on your `PATH`:

- **git**
- **curl**
- **jq**

A **GitLab personal access token** with `api` scope is required for API operations (merge request creation, project detection, default branch updates).

### Quick Start

```bash
# Dry run — validate everything without making changes
./scripts/release.sh --dry-run

# Create a release (interactive version prompt)
./scripts/release.sh

# Skip the default branch update prompt (on by default)
./scripts/release.sh --no-update-default-branch

# Use a custom config file
./scripts/release.sh --config /path/to/my.conf

# Create a merge request from a release branch back to main
./scripts/release.sh --hotfix-mr release/v1.2.3
```

### Interactive Menu

When run interactively without a mode flag (`--hotfix-mr`, `--deploy-only`) and without `--version`, the script presents an interactive menu:

```
What would you like to do?

  1) Release        Create release branch + tag (+ optional deploy)
  2) Deploy only    Deploy an existing tagged release
  3) Hotfix MR      Create MR from a release branch to main
```

The menu is **skipped** when:
- `--hotfix-mr` or `--deploy-only` CLI flag is given (direct dispatch)
- `--version` is given (implies full release intent)
- stdin is not a TTY (piped input / CI) — preserves backward compatibility

### What It Does

When you run `release.sh`, it performs the following steps in order:

1. **Parses arguments and loads configuration** from config files and environment variables.
2. **Checks prerequisites** — ensures `git`, `curl`, and `jq` are available.
3. **Validates repository state** — confirms you are on the default branch, the working tree is clean, and the local branch is in sync with the remote.
4. **Fetches the latest tags** from the remote.
5. **Detects the current version** by finding the latest semver tag (e.g. `v1.2.3`). Pre-release and non-semver tags are filtered out. If no tags exist, defaults to `0.0.0`.
6. **Prompts for a version bump** — presents patch, minor, and major suggestions, or allows a custom version. Validates that the chosen tag and release branch don't already exist.
7. **Generates a changelog** from commit messages since the last tag, formatted as a markdown list.
8. **Asks for confirmation** before proceeding.
9. **Detects the GitLab project ID** by parsing the git remote URL (supports SSH and HTTPS, including nested groups and self-hosted instances).
10. **Creates a release branch** named `release/<tag>` (e.g. `release/v1.3.0`) and pushes it to the remote.
11. **Creates an annotated tag** with the changelog as the tag message and pushes it to the remote.
12. **Updates the GitLab default branch** to the release branch (enabled by default). The script prompts for confirmation before making the change. Use `--no-update-default-branch` to skip this step entirely, or disable it via `UPDATE_DEFAULT_BRANCH=false` in config. The confirmation prompt is auto-accepted in `--non-interactive` mode.
13. **Switches back** to the default branch and prints a summary.

If any step fails after branches or tags have been pushed, a **cleanup trap** automatically deletes the partial remote branch and tag, then restores you to the default branch.

> **Note:** The release flow does not create a merge request. Use `--hotfix-mr` separately after pushing hotfix commits to the release branch (see [Hotfix Workflow](#hotfix-workflow)).

### Hotfix Workflow

The release flow creates a branch and tag only — no merge request. MRs are created later, after hotfix commits have been pushed to the release branch:

1. **Create a release** (branch + tag):
   ```bash
   ./scripts/release.sh --version 1.2.0 --non-interactive
   ```

2. **Push hotfix commits** to the release branch:
   ```bash
   git checkout release/v1.2.0
   git cherry-pick <commit-sha>
   git push
   ```

3. **Create the merge request** back to the default branch:
   ```bash
   ./scripts/release.sh --hotfix-mr release/v1.2.0
   ```

The `--hotfix-mr` flag:
- Fetches from the remote and verifies the branch exists
- Checks that the branch has commits ahead of the default branch
- Generates a changelog from those commits
- Asks for confirmation (or auto-confirms with `--non-interactive`)
- Creates a merge request via the GitLab API

### Deploy-Only Workflow

The `--deploy-only` flag deploys an existing tagged release without creating a new branch or tag. This is useful when a release has already been created and you need to deploy it to a new environment or re-deploy after infrastructure changes.

```bash
# Interactive — prompts for the version to deploy
./scripts/release.sh --deploy-only

# Non-interactive — specify version directly
./scripts/release.sh --deploy-only --version 1.2.3 --non-interactive

# Dry run — preview what would be deployed
./scripts/release.sh --deploy-only --version 1.2.3 --dry-run
```

The deploy-only flow:
- Validates that `DEPLOY_BASE_PATH` is configured (via config file, env var, or `--deploy-path`)
- Fetches tags from the remote
- Prompts for a version (or uses `--version`)
- Validates the tag exists
- Clones the tag into `DEPLOY_BASE_PATH/<tool>/<version>`
- Creates a modulefile at `DEPLOY_BASE_PATH/mf/<tool>/<version>` — if a modulefile from a previous version exists, it is copied and version references are updated; otherwise a default template is generated
- **Errors if the deploy directory or modulefile already exists** (never overwrites)

### Command-Line Options

| Option | Description |
|---|---|
| `--dry-run` | Run all validation and checks without making any changes. API calls, branch creation, tagging, and MR creation are skipped. |
| `--hotfix-mr BRANCH` | Create a merge request from the specified release branch back to the default branch. The branch must exist on the remote and have commits ahead of the default branch. |
| `--deploy-only` | Deploy an existing tagged release without creating a new branch or tag. Requires `DEPLOY_BASE_PATH` to be configured. Cannot be combined with `--hotfix-mr`. |
| `--update-default-branch` | Update the GitLab project's default branch to the release branch (this is the default behavior). |
| `--no-update-default-branch` | Skip updating the GitLab project's default branch. |
| `--config FILE` | Load configuration from the specified file (in addition to the default config locations). |
| `--version X.Y.Z` | Set the release version directly, bypassing the interactive version prompt. |
| `--deploy-path PATH` | Deploy base path (overrides `DEPLOY_BASE_PATH` from config/env). |
| `--non-interactive`, `-n` | Auto-confirm all prompts (for CI/CD). |
| `--help`, `-h` | Show the help message and exit. |

### Configuration

Settings can be provided through config files and environment variables. Multiple sources are loaded in order, with later values overriding earlier ones.

#### Config File Locations (loaded in order)

| Priority | Location | Description |
|---|---|---|
| 1 (lowest) | `~/.release.conf` | User-level defaults |
| 2 | `<repo>/.release.conf` | Repository-level overrides |
| 3 | `--config FILE` | Explicitly specified file |
| 4 (highest) | Environment variables | Always take precedence |

An example config file is provided at `scripts/.release.conf.example`.

#### Config File Format

Config files use a simple `KEY=VALUE` format. Comments (lines starting with `#`) and blank lines are ignored. Values can be optionally quoted with single or double quotes. Leading and trailing whitespace in values is trimmed.

```bash
# GitLab API base URL (change for self-hosted instances)
GITLAB_API_URL=https://gitlab.com/api/v4

# Branch to release from
DEFAULT_BRANCH=main

# Prefix for version tags (produces tags like v1.2.3)
TAG_PREFIX=v

# Git remote name
REMOTE=origin

# Verify SSL certificates (set to false for self-signed certs)
VERIFY_SSL=true

# Update GitLab default branch to the release branch (true/false)
UPDATE_DEFAULT_BRANCH=true

# Base path for deploy (clone + modulefile). If unset, deploy is skipped.
# DEPLOY_BASE_PATH=/opt/software

# GitLab token (prefer env var or ~/.gitlab_token instead)
# GITLAB_TOKEN=glpat-xxxxxxxxxxxxxxxxxxxx
```

#### Environment Variables

| Variable | Config Key | Default | Description |
|---|---|---|---|
| `GITLAB_TOKEN` | `GITLAB_TOKEN` | *(none)* | GitLab personal access token (required for API calls) |
| `GITLAB_API_URL` | `GITLAB_API_URL` | `https://gitlab.com/api/v4` | GitLab API base URL |
| `RELEASE_DEFAULT_BRANCH` | `DEFAULT_BRANCH` | `main` | Branch to release from |
| `RELEASE_TAG_PREFIX` | `TAG_PREFIX` | `v` | Prefix for version tags |
| `RELEASE_REMOTE` | `REMOTE` | `origin` | Git remote name |
| `GITLAB_VERIFY_SSL` | `VERIFY_SSL` | `true` | Verify SSL certificates (`false` for self-signed certs) |
| `RELEASE_UPDATE_DEFAULT_BRANCH` | `UPDATE_DEFAULT_BRANCH` | `true` | Update GitLab default branch to the release branch |
| `DEPLOY_BASE_PATH` | `DEPLOY_BASE_PATH` | *(none)* | Base path for deploy (clone + modulefile). If unset, deploy is skipped. |

Environment variables are snapshotted at script startup. This means that if a config file sets a value for a variable that was already set in the environment, the environment value is preserved.

#### Token Resolution

The GitLab token is resolved using the first match from this chain:

1. **`GITLAB_TOKEN` environment variable** — highest priority, recommended for CI/CD.
2. **`GITLAB_TOKEN` key in a `.release.conf` file** — loaded from any config file in the chain.
3. **`~/.gitlab_token` file** — a plain-text file containing just the token. Useful for personal machines.

```bash
# Option 1: Environment variable (recommended)
export GITLAB_TOKEN=glpat-xxxxxxxxxxxxxxxxxxxx

# Option 2: Token file
echo "glpat-xxxxxxxxxxxxxxxxxxxx" > ~/.gitlab_token
chmod 600 ~/.gitlab_token
```

### Version Management

The script uses **semantic versioning** (X.Y.Z). Versions are detected from git tags matching the configured prefix (default: `v`). Only strict semver tags are considered — pre-release tags (e.g. `v1.0.0-rc1`) and non-semver tags are filtered out.

When prompted, you can select:

| Choice | Current: 1.2.3 | Result |
|---|---|---|
| **1) Patch** | 1.2.3 → 1.2.4 | Bug fixes, small changes |
| **2) Minor** | 1.2.3 → 1.3.0 | New features, backwards compatible |
| **3) Major** | 1.2.3 → 2.0.0 | Breaking changes |
| **4) Custom** | *(enter manually)* | Any valid X.Y.Z version |

The script rejects versions where the tag or release branch already exists.

### Changelog Generation

The changelog is automatically generated from git commit messages between the last tag and `HEAD`. Merge commits are excluded. The format is a markdown list:

```
- Fix login timeout issue (a1b2c3d)
- Add retry logic for API calls (e4f5g6h)
- Update dependencies (i7j8k9l)
```

If no previous tag exists, all commits are included. If there are no commits since the last tag, the changelog reads `- No changes recorded`.

The changelog is used in:
- The annotated tag message
- The merge request description (when using `--hotfix-mr`)

### Git Remote URL Parsing

The script automatically detects the GitLab project from the git remote URL. Both SSH and HTTPS formats are supported, with or without the `.git` suffix:

| Format | Example |
|---|---|
| SSH | `git@gitlab.com:group/project.git` |
| SSH (nested groups) | `git@gitlab.com:group/subgroup/project.git` |
| HTTPS | `https://gitlab.com/group/project.git` |
| HTTPS (nested groups) | `https://gitlab.com/group/subgroup/project.git` |
| Self-hosted SSH | `git@gitlab.example.com:team/project.git` |
| Self-hosted HTTPS | `https://gitlab.example.com/team/project.git` |

Nested group paths are URL-encoded (slashes become `%2F`) for the GitLab API.

### Security

- The GitLab token is **never passed as a command-line argument** (which would be visible in process listings). Instead, it is written to a temporary file and passed to `curl` via `--header @file`. The file is deleted immediately after the API call.
- The script **warns about file permissions** if `~/.gitlab_token` or `.release.conf` files are readable by group or others. Keep token-bearing files restricted: `chmod 600 ~/.gitlab_token`.
- The script uses `set -euo pipefail` for strict error handling — undefined variables and failed commands cause immediate exit.

### Error Handling and Cleanup

The script sets a `trap` on `EXIT` that runs a cleanup handler on failure. If any step fails after remote artifacts have been created:

- The remote **tag** is deleted (if it was pushed).
- The remote **release branch** is deleted (if it was pushed).
- The local checkout is switched back to the default branch.
- The local release branch is deleted.

The trap is disabled after a successful release to avoid cleaning up valid artifacts.

GitLab API calls include **automatic retry** — a single retry with a 2-second delay on 5xx server errors, which handles transient GitLab issues common in CI environments.

### CI/CD Usage

The script supports fully non-interactive execution for CI/CD pipelines via `--version` and `--non-interactive`:

```bash
# Non-interactive release (no prompts)
./scripts/release.sh --version 1.2.3 --non-interactive

# Create hotfix MR in CI
./scripts/release.sh --hotfix-mr release/v1.2.3 --non-interactive

# Deploy an existing release in CI
./scripts/release.sh --deploy-only --version 1.2.3 --non-interactive
```

| Option | Description |
|---|---|
| `--version X.Y.Z` | Set the release version directly, bypassing the interactive version prompt. |
| `--non-interactive`, `-n` | Auto-confirm all prompts. Without this, confirmation prompts will block in CI. |

**Detached HEAD support:** GitLab CI runners typically check out a specific commit (detached HEAD) rather than a branch. The script detects this and validates that HEAD is at the tip of the remote default branch instead of requiring a named branch checkout.

A sample `.gitlab-ci.yml` job is provided at [`examples/gitlab-ci-release.yml`](examples/gitlab-ci-release.yml).

### Examples

```bash
# Standard release with all defaults
./scripts/release.sh

# Dry run to preview what would happen
./scripts/release.sh --dry-run

# Self-hosted GitLab instance
export GITLAB_API_URL=https://gitlab.mycompany.com/api/v4
export GITLAB_TOKEN=glpat-xxxxxxxxxxxxxxxxxxxx
./scripts/release.sh

# Self-hosted with self-signed certificate
export GITLAB_VERIFY_SSL=false
./scripts/release.sh

# Release from a non-main branch
export RELEASE_DEFAULT_BRANCH=develop
./scripts/release.sh

# Custom tag prefix (produces tags like release-1.0.0)
export RELEASE_TAG_PREFIX=release-
./scripts/release.sh

# Use a project-specific config file
./scripts/release.sh --config ./my-project.conf

# Full options: dry run with custom config
./scripts/release.sh --dry-run --config ./my-project.conf

# Create hotfix MR after pushing fixes to a release branch
./scripts/release.sh --hotfix-mr release/v1.2.3

# Hotfix MR dry run
./scripts/release.sh --hotfix-mr release/v1.2.3 --dry-run

# Deploy an existing release
./scripts/release.sh --deploy-only --version 1.2.3 --non-interactive

# Deploy dry run
./scripts/release.sh --deploy-only --version 1.2.3 --dry-run
```

### Architecture

The script (~1200 lines) is organized into sequential modules, each responsible for one phase of the release workflow:

1. **Logging & utilities** — color-coded output (`log_info`, `log_warn`, `log_error`, `log_success`), `confirm()` prompt, `validate_semver()`, `_warn_file_permissions()`
2. **Argument parsing** — `parse_args()` handles all CLI flags (`--dry-run`, `--hotfix-mr`, `--deploy-only`, `--update-default-branch` / `--no-update-default-branch`, `--config`, `--version`, `--deploy-path`, `--non-interactive`, `--help`)
3. **Configuration loading** — multi-level config resolution with strict priority: env vars (snapshotted at startup) > `--config` file > repo `.release.conf` > user `~/.release.conf` > `~/.gitlab_token`
4. **Repository validation** — `check_branch()` verifies git state (correct branch, clean tree, synced with remote)
5. **Version management** — `get_latest_version()`, `suggest_versions()`, `check_version_available()`, `prompt_version()` with duplicate tag/branch detection
6. **Changelog generation** — markdown-formatted commit list since last tag
7. **GitLab API module** — `gitlab_api()` passes tokens via temp file headers (never CLI args) with automatic retry on 5xx errors; `get_gitlab_project_id()` parses SSH/HTTPS remotes including nested groups; `create_merge_request()` and `update_default_branch()`
8. **Hotfix MR flow** — `hotfix_mr_flow()` validates a release branch, generates a changelog from commits ahead of the default branch, and creates a merge request back to the default branch
9. **Deploy-only flow** — `deploy_only_flow()` deploys an existing tagged release without creating branches or tags
10. **Interactive menu** — `show_main_menu()` presents a choice of Release, Deploy only, or Hotfix MR when run interactively without a mode flag
11. **Git operations** — branch/tag creation with push
12. **Error recovery** — `cleanup_on_failure()` trap handler removes partial remote branches/tags on failure
13. **Main flow** — `main()` orchestrates the full workflow; dispatches to `hotfix_mr_flow()`, `deploy_only_flow()`, or shows the interactive menu

Key design patterns:
- Every write operation respects `$DRY_RUN` — full validation runs without side effects
- Environment variables are snapshotted into `_ENV_*` vars at startup so config files cannot override them
- The `cleanup_on_failure` trap ensures partial releases are rolled back
- Release flow creates branch + tag only (no MR); MR creation is a separate step via `--hotfix-mr`

### Running Tests

Tests use [BATS](https://github.com/bats-core/bats-core) (Bash Automated Testing System) and require `python3` for the mock GitLab API server.

```bash
# Run all tests
bats tests/test_*.bats

# Run a specific test suite
bats tests/test_parse_args.bats    # CLI argument parsing
bats tests/test_config.bats        # Configuration loading
bats tests/test_git_operations.bats # Git operations
bats tests/test_semver.bats        # Semantic versioning
bats tests/test_gitlab_api.bats    # GitLab API integration
bats tests/test_deploy.bats        # Deploy functionality
bats tests/test_deploy_only.bats   # Deploy-only mode
bats tests/test_integration.bats   # End-to-end workflows

# Run a specific test by name
bats tests/test_semver.bats -f "validates correct semver"
```

#### Test Infrastructure

Tests use **BATS** (Bash Automated Testing System). Shared helpers in `tests/test_helpers.bash` provide:
- `setup_test_repo()` — creates a bare remote + working clone per test
- `source_release_functions()` — sources the script without executing `main()`
- `start_mock_gitlab()` / `stop_mock_gitlab()` — manages `tests/mock_gitlab.py`, a Python HTTP server simulating GitLab API endpoints with scenario-based failure injection

The mock server supports request recording for assertions and dynamic port assignment via a state file.
