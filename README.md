# toolmanager

A collection of Python utility scripts for DevOps workflows: release automation, deploy management, and toolset bundling for GitLab repositories.

## Overview

Three scripts, each handling one concern:

| Script | Who runs it | What it does |
|---|---|---|
| `release.sh` | Developers | Version selection, annotated tag on main, changelog |
| `deploy.sh` | DevOps | Clone tagged release, run bootstrap, generate modulefile |
| `bundle.sh` | DevOps | Detect submodules in toolset repo, create bundle release, deploy parent modulefile |

**Technology:** Python 3.12.3, standard library only. Thin Bash wrappers call the Python scripts for CLI compatibility.

---

## Getting Started

### Prerequisites

- Python 3.12+
- git

### Installation

Clone this repository into your tool repo (or add it as a submodule):

```bash
git clone <this-repo-url> toolmanager
```

The `scripts/` directory contains the entry points. Make them executable if needed:

```bash
chmod +x toolmanager/scripts/*.sh
```

### Initial Configuration

Copy the example config to your repo root and edit it:

```bash
cp toolmanager/scripts/.release.conf.example .release.conf
```

At minimum, set:

```ini
DEFAULT_BRANCH=main      # branch you release from
TAG_PREFIX=v             # tags will look like v1.2.3
REMOTE=origin            # git remote name
```

For deploy and bundle, also set:

```ini
DEPLOY_BASE_PATH=/opt/software   # where releases are cloned to
```

Keep the file at `600` permissions if it contains tokens:

```bash
chmod 600 .release.conf
```

---

## release.sh

Automates version tagging for GitLab repositories. Handles the full release lifecycle: version selection, changelog generation, and annotated tag creation on main. No branches, no merge requests, no GitLab token required.

### First Release Walkthrough

```bash
# 1. Confirm you're on main with a clean tree
git status

# 2. Dry run — validates everything without making changes
./scripts/release.sh --dry-run

# 3. Create the release (interactive)
./scripts/release.sh
```

The interactive flow:
1. Confirms repo state (branch, cleanliness, remote sync)
2. Shows current version and suggests patch/minor/major bumps
3. Displays the generated changelog
4. Prompts for an optional release description
5. Asks for final confirmation, then tags and pushes

### Quick Start

```bash
# Interactive release
./scripts/release.sh

# Non-interactive release for CI/CD
./scripts/release.sh --version 1.2.3 --non-interactive

# Release with a description in the tag message
./scripts/release.sh --version 1.2.3 --description "Adds widget support" -n

# Dry run
./scripts/release.sh --dry-run
```

### What It Does

1. Validates repository state — on main branch, clean tree, synced with remote.
2. Detects the current version from semver tags.
3. Prompts for a version bump (patch, minor, major, or custom).
4. Generates a changelog from commit messages since the last tag.
5. Optionally prompts for a release description (interactive mode only).
6. Creates an annotated tag on main and pushes it.
7. Prints a release summary.

### Tag Message Format

```
Release v1.2.3

<optional description>

Changelog:
- feat: add widget (abc1234)
- fix: handle edge case (def5678)
```

### Options

| Option | Description |
|---|---|
| `--dry-run` | Run all validation without making changes |
| `--config FILE` | Load configuration from `FILE` |
| `--version X.Y.Z` | Set version non-interactively |
| `--description DESC` | Free-text summary prepended to changelog in tag message |
| `--non-interactive`, `-n` | Auto-confirm all prompts (for CI/CD) |
| `--help`, `-h` | Show help |

---

## deploy.sh

Deploys a tagged release by cloning it, running an optional bootstrap script, and writing a TCL modulefile.

### First Deploy Walkthrough

```bash
# 1. Ensure the tag exists
git tag | grep v1.2.3

# 2. Dry run to preview what will happen
./scripts/deploy.sh --version 1.2.3 --deploy-path /opt/software --dry-run

# 3. Deploy
./scripts/deploy.sh --version 1.2.3 --deploy-path /opt/software
```

### Quick Start

```bash
# Deploy a specific version
./scripts/deploy.sh --version 1.2.3 --deploy-path /opt/software

# Deploy with a separate modulefile directory
./scripts/deploy.sh --version 1.2.3 --deploy-path /opt/software \
    --mf-path /opt/modulefiles

# Non-interactive (for CI/CD)
./scripts/deploy.sh --version 1.2.3 --deploy-path /opt/software -n

# Dry run
./scripts/deploy.sh --version 1.2.3 --deploy-path /opt/software --dry-run
```

### What It Does

1. Fetches tags and validates the requested version exists.
2. Clones the tag into `DEPLOY_BASE_PATH/<tool>/<version>/`.
3. **Bootstrap** — runs `install.sh` or `install.py` if present in the cloned directory.
4. **Modulefile** — generates a TCL modulefile and writes it to `MF_BASE_PATH/<tool>/<version>` (defaults to `DEPLOY_BASE_PATH/mf/<tool>/<version>`).

### Bootstrap Convention

After cloning, deploy looks for:
1. `install.sh` — run via `bash` (takes priority)
2. `install.py` — run via `python3`

Only one runs. If bootstrap fails, you are prompted to clean up the cloned directory before the script exits.

### Deploy Directory Structure

```
DEPLOY_BASE_PATH/
├── my-tool/
│   ├── 1.1.0/           # cloned tag (bootstrap ran if present)
│   └── 1.2.0/           # newer version
└── mf/
    └── my-tool/
        ├── 1.1.0         # modulefile
        └── 1.2.0         # modulefile
```

If `--mf-path` is set, modulefiles are written there instead:

```
MF_BASE_PATH/
└── my-tool/
    ├── 1.1.0
    └── 1.2.0
```

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

**Placeholders:** `%VERSION%`, `%ROOT%`, `%TOOL_NAME%`, `%DEPLOY_BASE_PATH%`

#### Example Custom Modulefile Template

```tcl
#%Module1.0
proc ModulesHelp { } {
    puts stderr "my-tool version %VERSION%"
}
module-whatis "my-tool version %VERSION%"
conflict my-tool
set root %ROOT%
prepend-path PATH $root/bin
prepend-path LD_LIBRARY_PATH $root/lib
```

Save to a file and reference it in `.release.conf`:

```ini
MODULEFILE_TEMPLATE=/opt/templates/my-tool.tcl
```

### Options

| Option | Description |
|---|---|
| `--version X.Y.Z` | Version to deploy |
| `--deploy-path PATH` | Deploy base path |
| `--mf-path PATH` | Override base directory for modulefiles |
| `--config FILE` | Config file |
| `--dry-run` | Show what would be done without making changes |
| `--non-interactive`, `-n` | Auto-confirm prompts |
| `--help`, `-h` | Show help |

---

## bundle.sh

Bundle tool for toolset repos with git submodules. Creates coordinated releases across multiple tools pinned at their current tags.

### First Bundle Release Walkthrough

```bash
# 1. Ensure all submodules are pinned to a release tag
git submodule status

# 2. Dry run to preview
./scripts/bundle.sh --dry-run

# 3. Create the bundle release
./scripts/bundle.sh --version 1.0.0 --deploy-path /opt/software
```

### Quick Start

```bash
# Full bundle release (tag + optional deploy)
./scripts/bundle.sh --version 1.0.0 --deploy-path /opt/software -n

# Deploy-only (generate bundle modulefile for existing tag)
./scripts/bundle.sh --deploy-only --version 1.0.0 --deploy-path /opt/software -n

# If submodules live in a subdirectory
./scripts/bundle.sh --submodule-dir tools --version 1.0.0 -n
```

### Submodule Detection

Scans the toolset repo for git submodules at the current checkout:

1. Runs `git submodule init && git submodule update`
2. For each submodule: `git describe --tags --exact-match HEAD`
3. Strips the tag prefix to get the version
4. **All submodules must be pinned to a tag** — any unpinned submodule is an error

The detected manifest is printed before you confirm:

```
── Bundle Manifest ──────────────────────────────────────
  tool-a  v1.2.0  (tools/tool-a)
  tool-b  v2.0.0  (tools/tool-b)
────────────────────────────────────────────────────────
```

### Bundle Modulefile

The generated modulefile loads all detected tools:

```tcl
#%Module1.0
module load tool-a/1.2.0
module load tool-b/2.0.0
```

Custom templates support per-tool version placeholders:

| Placeholder | Expands to |
|---|---|
| `%VERSION%` | Bundle version |
| `%TOOL_NAME%` | Bundle name |
| `%TOOL_LOADS%` | Auto-generated `module load` block for all tools |
| `%tool-a%` | Version of submodule named `tool-a` |
| `%tool-b%` | Version of submodule named `tool-b` |

### Options

| Option | Description |
|---|---|
| `--deploy-only` | Deploy bundle modulefile for an existing tag |
| `--submodule-dir DIR` | Subdirectory containing tool submodules |
| `--version X.Y.Z` | Set bundle version |
| `--deploy-path PATH` | Deploy base path |
| `--mf-path PATH` | Override base directory for modulefiles |
| `--config FILE` | Config file |
| `--dry-run` | Show what would be done without making changes |
| `--non-interactive`, `-n` | Auto-confirm prompts |
| `--help`, `-h` | Show help |

---

## Configuration

All three scripts share the same config system. Config files use `KEY=VALUE` format (shell-style comments with `#`, optional quotes around values).

### Config File Locations (loaded in order, later wins)

| Priority | Location | Typical use |
|---|---|---|
| 1 (lowest) | `~/.release.conf` | Personal defaults (token, preferred remote) |
| 2 | `<repo>/.release.conf` | Per-repo settings (branch, tag prefix, deploy path) |
| 3 | `--config FILE` | Explicitly specified override |
| 4 (highest) | Environment variables | CI/CD, secrets |

### Environment Variables

| Variable | Config Key | Default | Description |
|---|---|---|---|
| `GITLAB_TOKEN` | `GITLAB_TOKEN` | *(none)* | GitLab personal access token (deploy/bundle only) |
| `GITLAB_API_URL` | `GITLAB_API_URL` | `https://gitlab.com/api/v4` | GitLab API base URL |
| `RELEASE_DEFAULT_BRANCH` | `DEFAULT_BRANCH` | `main` | Branch to release from |
| `RELEASE_TAG_PREFIX` | `TAG_PREFIX` | `v` | Prefix for version tags (e.g. `v` → `v1.2.3`) |
| `RELEASE_REMOTE` | `REMOTE` | `origin` | Git remote name |
| `GITLAB_VERIFY_SSL` | `VERIFY_SSL` | `false` | Verify SSL certificates |
| `DEPLOY_BASE_PATH` | `DEPLOY_BASE_PATH` | *(none)* | Base path for cloned releases and modulefiles |
| `MF_BASE_PATH` | `MF_BASE_PATH` | *(none)* | Override modulefile directory (separate NFS mount, etc.) |
| `MODULEFILE_TEMPLATE` | `MODULEFILE_TEMPLATE` | *(none)* | Path to a custom modulefile template |
| `BUNDLE_SUBMODULE_DIR` | `BUNDLE_SUBMODULE_DIR` | *(repo root)* | Subdirectory containing tool submodules |
| `BUNDLE_NAME` | `BUNDLE_NAME` | *(auto from remote)* | Override bundle name |

Environment variables are snapshotted at startup — config files cannot override them.

### Token Resolution

Used by `deploy.sh` and `bundle.sh` for GitLab API calls (`release.sh` does not need a token):

1. `GITLAB_TOKEN` environment variable (highest priority)
2. `GITLAB_TOKEN` key in a `.release.conf` file
3. `~/.gitlab_token` plain-text file

> **Security:** Never commit tokens to a config file. Use an env var or `~/.gitlab_token` with `chmod 600`.

### Sample `.release.conf`

```ini
# .release.conf — repo-level config

DEFAULT_BRANCH=main
TAG_PREFIX=v
REMOTE=origin
VERIFY_SSL=false

# Deploy settings
DEPLOY_BASE_PATH=/opt/software
# MF_BASE_PATH=/opt/modulefiles   # optional: separate modulefile directory

# Custom modulefile template
# MODULEFILE_TEMPLATE=/opt/templates/my-tool.tcl
```

---

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
├── bundle.sh                  # Thin wrapper → src/bundle.py
└── .release.conf.example      # Annotated config file template
```

---

## Running Tests

Tests use Python `unittest` and require `python3`.

```bash
# Run all tests
python3 -m unittest discover tests/ -p "test_*.py"

# Run a specific test file
python3 -m unittest tests/test_semver.py

# Run a specific test
python3 -m unittest tests.test_semver.TestValidateSemver.test_valid_versions

# Verbose output
python3 -m unittest discover tests/ -p "test_*.py" -v
```

### Test Coverage

| File | Coverage |
|---|---|
| `test_config.py` | Config loading, priority chain, env override |
| `test_semver.py` | Validation, suggestions, comparison |
| `test_git.py` | Branch check, tag detection, changelog, URL parsing |
| `test_gitlab_api.py` | HTTP requests, auth, retry, project ID |
| `test_modulefile.py` | Template loading, placeholders, default template |
| `test_release.py` | Release flow, description flag, argument parsing |
| `test_deploy.py` | Clone, bootstrap, modulefile generation |
| `test_bundle.py` | Submodule detection, bundle modulefile, deploy |

---

## CI/CD Usage

See [`examples/gitlab-ci-release.yml`](examples/gitlab-ci-release.yml) for ready-to-use GitLab CI job definitions.

### Common Patterns

```bash
# Non-interactive release
./scripts/release.sh --version 1.2.3 --non-interactive

# Non-interactive deploy
./scripts/deploy.sh --version 1.2.3 --deploy-path /opt/software -n

# Non-interactive bundle release + deploy
./scripts/bundle.sh --version 1.0.0 --deploy-path /opt/software -n
```

Detached HEAD is supported for CI runners that checkout specific commits.

### Required CI Variables

| Variable | Used by | Description |
|---|---|---|
| `RELEASE_VERSION` | `release.sh`, `bundle.sh` | Version to tag |
| `DEPLOY_VERSION` | `deploy.sh`, `bundle.sh --deploy-only` | Version to deploy |
| `DEPLOY_BASE_PATH` | `deploy.sh`, `bundle.sh` | Deploy root path |
| `GITLAB_TOKEN` | `deploy.sh`, `bundle.sh` | GitLab API token (api scope) |
