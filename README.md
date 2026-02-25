# toolmanager

A collection of Python utility scripts for DevOps workflows: release automation, deploy management, and toolset bundling for GitLab repositories.

## Overview

Three scripts, each handling one concern:

| Script | Who runs it | What it does |
|---|---|---|
| `release.sh` | Developers | Version selection, release branch + tag, changelog, GitLab API, hotfix MR |
| `deploy.sh` | DevOps | Clone tagged release, run bootstrap, generate modulefile from template |
| `bundle.sh` | DevOps | Detect submodules in toolset repo, create bundle release, deploy parent modulefile |

**Technology:** Python 3.12.3, standard library only. Thin Bash wrappers call the Python scripts for CLI compatibility.

### Prerequisites

- **Python 3.12+**
- **git**

A **GitLab personal access token** with `api` scope is required for API operations (merge request creation, project detection, default branch updates).

## release.sh

Automates version management and release branch creation for GitLab repositories. Handles the full release lifecycle: version bumping, changelog generation, release branch creation, and tagging.

### Quick Start

```bash
# Dry run — validate everything without making changes
./scripts/release.sh --dry-run

# Create a release (interactive version prompt)
./scripts/release.sh

# Non-interactive release for CI/CD
./scripts/release.sh --version 1.2.3 --non-interactive

# Skip the default branch update
./scripts/release.sh --no-update-default-branch

# Create a merge request from a release branch back to main
./scripts/release.sh --hotfix-mr release/v1.2.3
```

### What It Does

1. Parses arguments and loads configuration from config files and environment variables.
2. Validates repository state — correct branch, clean tree, synced with remote.
3. Detects the current version from semver tags (pre-release tags are filtered out).
4. Prompts for a version bump (patch, minor, major, or custom).
5. Generates a changelog from commit messages since the last tag.
6. Creates a release branch (`release/<tag>`) and annotated tag, pushes both.
7. Optionally updates the GitLab default branch.
8. Prints a summary and switches back to the default branch.

If any step fails after artifacts have been pushed, a cleanup handler automatically deletes partial remote branches and tags.

### Interactive Menu

When run interactively without `--version`, the script presents a menu:

```
What would you like to do?

  1) Release        Create release branch + tag
  2) Hotfix MR      Create MR from a release branch to main
```

The menu is skipped when `--version` is given or stdin is not a TTY.

### Hotfix Workflow

Both scenarios start with creating a release and end with merging the fix back to main.

**Fix directly on the release branch** — the bug is found in production and you write the fix on the release branch:

1. Create a release: `./scripts/release.sh --version 1.2.0 --non-interactive`
2. Fix and push: `git checkout release/v1.2.0 && git add -A && git commit -m "Fix the bug" && git push`
3. Create MR: `./scripts/release.sh --hotfix-mr release/v1.2.0`

**Cherry-pick from another branch** — the fix already exists as a commit on `main` (or another branch):

1. Create a release: `./scripts/release.sh --version 1.2.0 --non-interactive`
2. Cherry-pick and push: `git checkout release/v1.2.0 && git cherry-pick <sha> && git push`
3. Create MR: `./scripts/release.sh --hotfix-mr release/v1.2.0`

### Options

| Option | Description |
|---|---|
| `--dry-run` | Run validation without making changes |
| `--hotfix-mr BRANCH` | Create MR from release branch to default branch |
| `--update-default-branch` | Update GitLab default branch (default) |
| `--no-update-default-branch` | Skip updating GitLab default branch |
| `--config FILE` | Load configuration from file |
| `--version X.Y.Z` | Set version non-interactively |
| `--non-interactive`, `-n` | Auto-confirm all prompts |
| `--help`, `-h` | Show help |

## deploy.sh

Deploys a tagged release: clone, run bootstrap, write modulefile.

### Quick Start

```bash
# Deploy a specific version
./scripts/deploy.sh --version 1.2.3 --deploy-path /opt/software

# Dry run
./scripts/deploy.sh --version 1.2.3 --deploy-path /opt/software --dry-run
```

### What It Does

1. Validates the version tag exists.
2. Clones the tag into `DEPLOY_BASE_PATH/<tool>/<version>/`.
3. **Bootstrap** — runs `install.sh` or `install.py` if present in the cloned directory.
4. **Modulefile** — generates a TCL modulefile from template or default.

### Bootstrap Convention

After cloning, deploy checks for:
1. `install.sh` — run via `bash` (takes priority)
2. `install.py` — run via `python3`

Only one runs. If bootstrap fails, the cloned directory is cleaned up.

### Modulefile Template System

Modulefile generation follows a priority chain:

```
Previous version modulefile exists?   →  copy + update version references
  no ↓
Repo has modulefile.tcl?              →  substitute placeholders, write
  no ↓
Config has MODULEFILE_TEMPLATE?       →  substitute placeholders, write
  no ↓
Default hardcoded template            →  write
```

**Placeholders:** `%VERSION%`, `%ROOT%`, `%TOOL_NAME%`

### Options

| Option | Description |
|---|---|
| `--version X.Y.Z` | Version to deploy |
| `--deploy-path PATH` | Deploy base path |
| `--config FILE` | Config file |
| `--dry-run` | Show what would be done |
| `--non-interactive`, `-n` | Auto-confirm prompts |
| `--help`, `-h` | Show help |

## bundle.sh

Bundle tool for toolset repos with git submodules. Creates coordinated releases across multiple tools.

### Quick Start

```bash
# Full bundle release (release + optional deploy)
./scripts/bundle.sh --version 1.0.0 --deploy-path /opt/software -n

# Deploy-only (deploy bundle modulefile for existing tag)
./scripts/bundle.sh --deploy-only --version 1.0.0 --deploy-path /opt/software -n
```

### Submodule Detection

Scans the toolset repo for git submodules:
1. Runs `git submodule init && git submodule update`
2. For each submodule: `git describe --tags --exact-match HEAD`
3. Strips tag prefix → version
4. All submodules must be pinned to a tag

### Bundle Modulefile

The bundle modulefile loads all detected tools:

```tcl
#%Module1.0
module load tool-a/1.2.0
module load tool-b/2.0.0
```

Custom templates support per-tool version placeholders: `%tool-a%`, `%tool-b%`, `%TOOL_LOADS%`.

### Options

| Option | Description |
|---|---|
| `--deploy-only` | Deploy bundle modulefile for existing tag |
| `--submodule-dir DIR` | Subdirectory with tool submodules |
| `--version X.Y.Z` | Set bundle version |
| `--deploy-path PATH` | Deploy base path |
| `--config FILE` | Config file |
| `--dry-run` | Show what would be done |
| `--non-interactive`, `-n` | Auto-confirm prompts |
| `--help`, `-h` | Show help |

## Configuration

All three scripts share the same config system. Config files use `KEY=VALUE` format.

### Config File Locations (loaded in order)

| Priority | Location | Description |
|---|---|---|
| 1 (lowest) | `~/.release.conf` | User-level defaults |
| 2 | `<repo>/.release.conf` | Repository-level overrides |
| 3 | `--config FILE` | Explicitly specified file |
| 4 (highest) | Environment variables | Always take precedence |

### Environment Variables

| Variable | Config Key | Default | Description |
|---|---|---|---|
| `GITLAB_TOKEN` | `GITLAB_TOKEN` | *(none)* | GitLab personal access token |
| `GITLAB_API_URL` | `GITLAB_API_URL` | `https://gitlab.com/api/v4` | GitLab API base URL |
| `RELEASE_DEFAULT_BRANCH` | `DEFAULT_BRANCH` | `main` | Branch to release from |
| `RELEASE_TAG_PREFIX` | `TAG_PREFIX` | `v` | Prefix for version tags |
| `RELEASE_REMOTE` | `REMOTE` | `origin` | Git remote name |
| `GITLAB_VERIFY_SSL` | `VERIFY_SSL` | `true` | Verify SSL certificates |
| `RELEASE_UPDATE_DEFAULT_BRANCH` | `UPDATE_DEFAULT_BRANCH` | `true` | Update GitLab default branch |
| `DEPLOY_BASE_PATH` | `DEPLOY_BASE_PATH` | *(none)* | Base path for deploy |
| `MODULEFILE_TEMPLATE` | `MODULEFILE_TEMPLATE` | *(none)* | Path to modulefile template |
| `BUNDLE_SUBMODULE_DIR` | `BUNDLE_SUBMODULE_DIR` | *(repo root)* | Subdirectory with tool submodules |
| `BUNDLE_NAME` | `BUNDLE_NAME` | *(auto from remote)* | Override bundle name |

Environment variables are snapshotted at startup so config files cannot override them.

### Token Resolution

1. `GITLAB_TOKEN` environment variable (highest priority)
2. `GITLAB_TOKEN` key in a `.release.conf` file
3. `~/.gitlab_token` plain-text file

### Deploy Structure

```
DEPLOY_BASE_PATH/
├── tool-a/1.2.0/              # cloned tool (install.sh ran if present)
├── tool-b/2.0.0/
├── mf/
│   ├── tool-a/1.2.0           # per-tool modulefile
│   ├── tool-b/2.0.0
│   └── my-toolset/1.0.0       # bundle modulefile
```

## Architecture

```
src/
├── lib/                       # Shared Python library
│   ├── __init__.py
│   ├── config.py              # Multi-level config loading
│   ├── git.py                 # Git operations via subprocess
│   ├── gitlab_api.py          # GitLab API via urllib.request
│   ├── log.py                 # Color-coded stderr logging
│   ├── semver.py              # Semver validation + suggestions
│   ├── modulefile.py          # Modulefile generation + templates
│   └── prompt.py              # Interactive prompts
├── release.py                 # Release tool
├── deploy.py                  # Deploy tool
└── bundle.py                  # Bundle tool

scripts/
├── release.sh                 # Thin wrapper → src/release.py
├── deploy.sh                  # Thin wrapper → src/deploy.py
└── bundle.sh                  # Thin wrapper → src/bundle.py
```

## Running Tests

Tests use Python `unittest` and require `python3`.

```bash
# Run all tests
python3 -m unittest discover tests/ -p "test_*.py"

# Run a specific test file
python3 -m unittest tests/test_semver.py

# Run a specific test
python3 -m unittest tests.test_semver.TestValidateSemver.test_valid_versions

# Run with verbose output
python3 -m unittest discover tests/ -p "test_*.py" -v
```

### Test Files

| File | Coverage |
|---|---|
| `test_config.py` | Config loading, priority chain, env override |
| `test_semver.py` | Validation, suggestions, comparison |
| `test_git.py` | Branch check, tag detection, changelog, URL parsing |
| `test_gitlab_api.py` | HTTP requests, auth, retry, project ID, MR creation |
| `test_modulefile.py` | Template loading, placeholders, default template |
| `test_release.py` | Release flow, hotfix MR, argument parsing |
| `test_deploy.py` | Clone, bootstrap, modulefile generation |
| `test_bundle.py` | Submodule detection, bundle modulefile, deploy |

## CI/CD Usage

```bash
# Non-interactive release
./scripts/release.sh --version 1.2.3 --non-interactive

# Non-interactive deploy
./scripts/deploy.sh --version 1.2.3 --deploy-path /opt/software -n

# Non-interactive bundle release
./scripts/bundle.sh --version 1.0.0 --deploy-path /opt/software -n
```

Detached HEAD is supported for CI runners that checkout specific commits.

A sample `.gitlab-ci.yml` is provided at [`examples/gitlab-ci-release.yml`](examples/gitlab-ci-release.yml).
