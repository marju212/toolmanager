# PRD: Release, Deploy & Bundle Toolset (Implemented)

## 1. Problem Statement

Teams managing multiple internal CLI tools face three problems:

1. **Release + deploy are coupled.** The current `release.sh` bundles release (branch/tag creation) and deploy (clone + modulefile) in a single 1200-line Bash script. Developers who create releases don't need deploy logic; DevOps who deploy don't need release logic.

2. **No post-deploy setup.** Tools that need compilation, dependency installation, or configuration after cloning have no supported mechanism — deploy just clones and writes a generic modulefile.

3. **No coordinated toolset.** Each tool is versioned independently, but end users need a consistent, reproducible set of tools loaded together. There's no way to say "give me the Q1-2026 toolset."

## 2. Solution Overview

Rewrite the system as **three separate Python scripts** with a shared library, each config-driven and handling one concern:

| Script | Who runs it | What it does |
|---|---|---|
| `release.sh` → `release.py` | Developers | Version selection, release branch + tag, changelog, GitLab API, hotfix MR |
| `deploy.sh` → `deploy.py` | DevOps | Clone tagged release, run bootstrap, generate modulefile from template |
| `bundle.sh` → `bundle.py` | DevOps | Detect submodules in toolset repo, create bundle release, deploy parent modulefile |

Thin Bash wrappers (`scripts/*.sh`) call the Python scripts for CLI compatibility.

**Technology:** Python 3.12.3, standard library only. No external packages.

## 3. Architecture

### 3.1 Code Structure

```
dev-utils/
├── src/
│   ├── lib/                       # Shared Python library
│   │   ├── __init__.py
│   │   ├── config.py              # Config loading (.release.conf format, multi-level priority)
│   │   ├── git.py                 # Git operations via subprocess
│   │   ├── gitlab_api.py          # GitLab API via urllib.request
│   │   ├── log.py                 # Color-coded stderr logging
│   │   ├── semver.py              # Semver validation + version suggestions
│   │   ├── modulefile.py          # Modulefile generation + template substitution
│   │   └── prompt.py              # Interactive prompts (confirm, menu, version picker)
│   ├── release.py                 # Release tool
│   ├── deploy.py                  # Deploy tool
│   └── bundle.py                  # Bundle tool
├── scripts/
│   ├── release.sh                 # Wrapper: exec python3 src/release.py "$@"
│   ├── deploy.sh                  # Wrapper: exec python3 src/deploy.py "$@"
│   └── bundle.sh                  # Wrapper: exec python3 src/bundle.py "$@"
├── tests/
│   ├── test_config.py
│   ├── test_semver.py
│   ├── test_git.py
│   ├── test_gitlab_api.py
│   ├── test_modulefile.py
│   ├── test_release.py
│   ├── test_deploy.py
│   ├── test_bundle.py
│   └── mock_gitlab.py            # Existing mock server (reused)
└── docs/
    └── prd-toolset-bundle.md     # This document
```

### 3.2 Repository Structure

Two layers of repositories:

```
TOOL REPOS (individual)                         TOOLSET REPO (parent)
┌──────────────────────┐                        ┌─────────────────────────────────┐
│ tool-a/               │                        │ my-toolset/                     │
│ ├── bin/              │                        │ ├── .gitmodules                 │
│ ├── lib/              │◄──── submodule ───────│ ├── .release.conf               │
│ ├── modulefile.tcl    │                        │ ├── modulefile.tcl              │
│ ├── install.sh        │                        │ ├── tools/                      │
│ └── ...               │                        │ │   ├── tool-a/  (→ v1.2.0)    │
│ tags: v1.0, v1.2     │                        │ │   ├── tool-b/  (→ v2.0.0)    │
└──────────────────────┘                        │ │   └── tool-c/  (→ v3.2.1)    │
┌──────────────────────┐                        │ └── README.md                   │
│ tool-b/               │◄──── submodule ───────│ tags: v1.0.0 (bundle version)   │
└──────────────────────┘                        └─────────────────────────────────┘
```

Key files in a **tool repo** (all optional):
- `install.sh` or `install.py` — bootstrap script, runs after deploy clone
- `modulefile.tcl` — custom modulefile template with placeholders

Key files in a **toolset repo** (all optional):
- `modulefile.tcl` — custom bundle modulefile template with per-tool version placeholders
- `.release.conf` — configuration for bundle name, submodule dir, deploy path, etc.

### 3.3 Deployed File Structure

```
DEPLOY_BASE_PATH/
├── tool-a/
│   ├── 1.0.0/                      # Cloned repo (install.sh ran if present)
│   └── 1.2.0/
├── tool-b/
│   └── 2.0.0/
├── tool-c/
│   └── 3.2.1/
└── mf/                             # TCL Modulefiles
    ├── tool-a/
    │   ├── 1.0.0                   # Per-tool (from modulefile.tcl or default)
    │   └── 1.2.0
    ├── tool-b/
    │   └── 2.0.0
    ├── tool-c/
    │   └── 3.2.1
    └── my-toolset/                 # Bundle modulefiles
        ├── 1.0.0                   #   → module load tool-a/1.2.0 ...
        └── 1.1.0
```

## 4. Configuration

All three scripts share the same config system. Configuration is loaded with strict priority:

```
Environment variables (snapshotted at startup)
  ↓ overrides
--config FILE (explicit CLI flag)
  ↓ overrides
<repo>/.release.conf
  ↓ overrides
~/.release.conf
  ↓ overrides
~/.gitlab_token (token only)
  ↓ overrides
Built-in defaults
```

### 4.1 Config Keys

**Shared (all three scripts):**

| Key | Env Var | Default | Description |
|---|---|---|---|
| `GITLAB_TOKEN` | `GITLAB_TOKEN` | *(none)* | GitLab personal access token |
| `GITLAB_API_URL` | `GITLAB_API_URL` | `https://gitlab.com/api/v4` | GitLab API base URL |
| `DEFAULT_BRANCH` | `RELEASE_DEFAULT_BRANCH` | `main` | Branch to release from |
| `TAG_PREFIX` | `RELEASE_TAG_PREFIX` | `v` | Prefix for version tags |
| `REMOTE` | `RELEASE_REMOTE` | `origin` | Git remote name |
| `VERIFY_SSL` | `GITLAB_VERIFY_SSL` | `true` | SSL certificate verification |

**Deploy-specific (`deploy.sh`):**

| Key | Env Var | Default | Description |
|---|---|---|---|
| `DEPLOY_BASE_PATH` | `DEPLOY_BASE_PATH` | *(none, required)* | Base path for deploy (clone + modulefile) |
| `MODULEFILE_TEMPLATE` | `MODULEFILE_TEMPLATE` | *(none)* | Path to external modulefile template |

**Bundle-specific (`bundle.sh`):**

| Key | Env Var | Default | Description |
|---|---|---|---|
| `DEPLOY_BASE_PATH` | `DEPLOY_BASE_PATH` | *(none, required for deploy)* | Base path for bundle modulefile |
| `BUNDLE_SUBMODULE_DIR` | `BUNDLE_SUBMODULE_DIR` | *(repo root)* | Subdirectory containing tool submodules |
| `BUNDLE_NAME` | `BUNDLE_NAME` | *(auto from remote URL)* | Override toolset name in modulefile |
| `MODULEFILE_TEMPLATE` | `MODULEFILE_TEMPLATE` | *(none)* | Path to external bundle modulefile template |

**Release-specific (`release.sh`):**

| Key | Env Var | Default | Description |
|---|---|---|---|
| `UPDATE_DEFAULT_BRANCH` | `RELEASE_UPDATE_DEFAULT_BRANCH` | `true` | Update GitLab default branch after release |

### 4.2 Config File Format

```bash
# .release.conf — same KEY=VALUE format as today
GITLAB_API_URL=https://gitlab.example.com/api/v4
DEFAULT_BRANCH=main
TAG_PREFIX=v
REMOTE=origin
VERIFY_SSL=true

# Deploy settings
DEPLOY_BASE_PATH=/opt/software
MODULEFILE_TEMPLATE=/opt/templates/my-tool.tcl

# Bundle settings (toolset repo only)
BUNDLE_SUBMODULE_DIR=tools
BUNDLE_NAME=my-toolset
```

## 5. Scripts

### 5.1 `release.sh` — Release Tool

**Audience:** Developers
**Runs in:** Individual tool repos

```
release.sh [OPTIONS]

Options:
  --dry-run                    Run all checks without making changes
  --hotfix-mr BRANCH           Create MR from release branch to default branch
  --update-default-branch      Update GitLab default branch (default)
  --no-update-default-branch   Skip updating default branch
  --config FILE                Path to config file
  --version X.Y.Z              Set version non-interactively
  --non-interactive, -n        Auto-confirm all prompts
  --help, -h                   Show help
```

**Flow:**
1. Parse args → load config → check prerequisites (git, curl/urllib, jq)
2. Validate repo state (correct branch, clean tree, synced with remote)
3. Version selection (detect current version, suggest bumps, prompt)
4. Generate changelog (commits since last tag)
5. Confirm → create release branch → create annotated tag → push
6. Optionally update GitLab default branch
7. Print summary

**Hotfix MR flow** (`--hotfix-mr BRANCH`):
1. Validate branch exists on remote and has commits ahead of default
2. Generate changelog from commits ahead
3. Create merge request via GitLab API

### 5.2 `deploy.sh` — Deploy Tool

**Audience:** DevOps
**Runs in:** Individual tool repos

```
deploy.sh [OPTIONS]

Options:
  --version X.Y.Z              Version to deploy (required)
  --deploy-path PATH           Deploy base path (or from config)
  --config FILE                Path to config file
  --dry-run                    Show what would be done
  --non-interactive, -n        Auto-confirm all prompts
  --help, -h                   Show help
```

**Flow:**
1. Parse args → load config → validate deploy path
2. Validate version tag exists
3. **Clone** tagged release into `DEPLOY_BASE_PATH/<tool>/<version>/`
4. **Bootstrap** — run `install.sh` or `install.py` if present in cloned directory
5. **Modulefile** — generate TCL modulefile from template or default

#### 5.2.1 Bootstrap Convention

After cloning, `deploy.py` checks the cloned directory for:

1. `install.sh` — run via `bash install.sh` (takes priority)
2. `install.py` — run via `python3 install.py`

Only one runs. Executed from within the deploy directory (`cwd = deploy_dir`). If the script fails, deploy fails and the cloned directory is cleaned up.

Example `install.sh` in a tool repo:
```bash
#!/usr/bin/env bash
set -euo pipefail
pip install -r requirements.txt --target ./lib
make build
```

#### 5.2.2 Modulefile Template System

Modulefile generation follows a priority chain:

```
Previous version modulefile exists?   →  copy + sed version update
  no ↓
Repo has modulefile.tcl?              →  substitute placeholders, write
  no ↓
Config has MODULEFILE_TEMPLATE?       →  substitute placeholders, write
  no ↓
Default hardcoded template            →  write
```

**Placeholders** (for per-tool modulefiles):
- `%VERSION%` → deployed version (e.g., `1.2.0`)
- `%ROOT%` → deploy directory path (e.g., `/opt/software/tool-a/1.2.0`)
- `%TOOL_NAME%` → tool name (e.g., `tool-a`)

Example `modulefile.tcl` in a tool repo:
```tcl
#%Module1.0
## %TOOL_NAME%/%VERSION% modulefile
proc ModulesHelp { } { puts stderr "%TOOL_NAME% version %VERSION%" }
module-whatis "%TOOL_NAME% version %VERSION%"
conflict %TOOL_NAME%
set root %ROOT%
prepend-path PATH $root/bin
prepend-path PYTHONPATH $root/lib
setenv TOOL_A_HOME $root
```

Default template (when no custom template exists):
```tcl
#%Module1.0
## %TOOL_NAME%/%VERSION% modulefile
proc ModulesHelp { } { puts stderr "%TOOL_NAME% version %VERSION%" }
module-whatis "%TOOL_NAME% version %VERSION%"
conflict %TOOL_NAME%
set root %ROOT%
prepend-path PATH $root/bin
```

### 5.3 `bundle.sh` — Bundle Tool

**Audience:** DevOps
**Runs in:** Toolset repos (parent repo with submodules)

```
bundle.sh [OPTIONS]

Options:
  --deploy-only                Deploy bundle modulefile for existing tag (skip release)
  --submodule-dir DIR          Subdirectory containing tool submodules
  --version X.Y.Z              Set bundle version non-interactively
  --deploy-path PATH           Deploy base path (or from config)
  --config FILE                Path to config file
  --dry-run                    Show what would be done
  --non-interactive, -n        Auto-confirm all prompts
  --help, -h                   Show help
```

#### 5.3.1 Submodule Detection

`bundle.py` scans the toolset repo for git submodules:

1. Run `git submodule init && git submodule update` if directories are empty
2. Parse `git submodule status` output
3. For each submodule: `git -C <path> describe --tags --exact-match HEAD`
4. Strip tag prefix → version
5. Build mapping: `{tool-name: version, ...}`

Constraints:
- All submodules must be pinned to a tag (error if not)
- At least one submodule must exist
- If `--submodule-dir` is set, only scan that subdirectory

#### 5.3.2 Bundle Release Flow

1. Validate repo state (correct branch, clean tree, synced)
2. Detect submodules → display manifest for confirmation
3. Version selection (semver, same as release.sh)
4. Generate changelog + append submodule manifest
5. Create release branch + tag
6. Optionally update GitLab default branch
7. If deploy path configured → deploy bundle modulefile

#### 5.3.3 Bundle Deploy-Only Flow (`--deploy-only`)

1. Validate deploy path is configured
2. Fetch tags, select version, validate tag exists
3. Checkout tag (detached HEAD)
4. `git submodule update --init`
5. Detect submodules at that tag's state
6. Deploy bundle modulefile
7. Restore original branch

#### 5.3.4 Bundle Modulefile Template

The bundle modulefile template supports **per-tool version placeholders**:

**Placeholders:**
- `%VERSION%` → bundle version
- `%TOOL_NAME%` → bundle name
- `%DEPLOY_BASE_PATH%` → deploy base path
- `%tool-a%` → version of tool-a submodule (per-tool, from detection)
- `%tool-b%` → version of tool-b submodule
- `%TOOL_LOADS%` → auto-generated `module load` block for all detected submodules

**The custom template IS the manifest.** It controls:
- **Which tools** are included (only tools referenced via `%tool-name%` placeholders)
- **Load order** (determined by template order)
- **Versions** (auto-resolved from submodule tags, not hardcoded)

If the template references a `%tool-x%` placeholder but no such submodule exists → error.

Template priority:
```
Repo has modulefile.tcl?          →  substitute placeholders, write
  no ↓
Config has MODULEFILE_TEMPLATE?   →  substitute placeholders, write
  no ↓
Default auto-generated template   →  all submodules alphabetically
```

Example custom template (`modulefile.tcl` in toolset repo):
```tcl
#%Module1.0
## %TOOL_NAME%/%VERSION% modulefile

proc ModulesHelp { } {
    puts stderr "%TOOL_NAME% version %VERSION%"
    puts stderr "Includes: tool-a/%tool-a%, tool-c/%tool-c%"
}

module-whatis "%TOOL_NAME% version %VERSION%"
conflict %TOOL_NAME%

setenv TOOLSET_VERSION %VERSION%

prepend-path MODULEPATH %DEPLOY_BASE_PATH%/mf

# Load in dependency order — tool-a first, then tool-c
module load tool-a/%tool-a%
module load tool-c/%tool-c%
# tool-b intentionally excluded from this bundle
```

Default auto-generated template (when no custom template):
```tcl
#%Module1.0
## my-toolset/1.0.0 modulefile
proc ModulesHelp { } {
    puts stderr "my-toolset version 1.0.0"
    puts stderr "Loads: tool-a/1.2.0, tool-b/2.0.0, tool-c/3.2.1"
}
module-whatis "my-toolset version 1.0.0"
conflict my-toolset
prepend-path MODULEPATH /opt/software/mf
module load tool-a/1.2.0
module load tool-b/2.0.0
module load tool-c/3.2.1
```

## 6. Workflow

### 6.1 End-to-End Lifecycle

```
 ┌─────────────────────────────────────────────────────────────────┐
 │  STEP 1: TOOL RELEASE (developer, in tool-a repo)              │
 │                                                                 │
 │  release.sh --version 1.2.0          ← creates branch + tag    │
 └──────────────────────────────┬──────────────────────────────────┘
                                ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │  STEP 2: TOOL DEPLOY (devops, in tool-a repo)                  │
 │                                                                 │
 │  deploy.sh --version 1.2.0 --deploy-path /opt/software         │
 │       ├── Clones v1.2.0 into /opt/software/tool-a/1.2.0/       │
 │       ├── Runs install.sh (if present)                          │
 │       └── Writes modulefile to /opt/software/mf/tool-a/1.2.0   │
 └──────────────────────────────┬──────────────────────────────────┘
                                ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │  STEP 3: UPDATE TOOLSET (developer, in my-toolset repo)        │
 │                                                                 │
 │  cd tools/tool-a && git fetch --tags && git checkout v1.2.0     │
 │  cd ../.. && git add tools/tool-a                               │
 │  git commit -m "Bump tool-a to v1.2.0" && git push             │
 └──────────────────────────────┬──────────────────────────────────┘
                                ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │  STEP 4: BUNDLE RELEASE (devops, in my-toolset repo)           │
 │                                                                 │
 │  bundle.sh --version 1.1.0 --deploy-path /opt/software         │
 │       ├── Detects: tool-a → v1.2.0, tool-b → v2.0.0            │
 │       ├── Creates release/v1.1.0 branch + v1.1.0 tag           │
 │       └── Deploys bundle modulefile:                            │
 │             /opt/software/mf/my-toolset/1.1.0                   │
 └──────────────────────────────┬──────────────────────────────────┘
                                ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │  END USER                                                       │
 │                                                                 │
 │  module load my-toolset/1.1.0                                   │
 │  # tool-a 1.2.0 + tool-b 2.0.0 loaded automatically            │
 │                                                                 │
 │  module swap my-toolset/1.1.0 my-toolset/1.0.0                 │
 │  # roll back to previous curated set                            │
 └─────────────────────────────────────────────────────────────────┘
```

### 6.2 Script Summary

| Script | Audience | Repo type | Key operations |
|---|---|---|---|
| `release.sh` | Developers | Tool repo | Branch + tag + changelog + GitLab API + hotfix MR |
| `deploy.sh` | DevOps | Tool repo | Clone + bootstrap + modulefile |
| `bundle.sh` | DevOps | Toolset repo | Submodule detection + bundle release + bundle modulefile |

## 7. CI/CD Integration

### 7.1 GitLab CI Example — Tool Repo

```yaml
release:
  stage: release
  when: manual
  variables:
    RELEASE_VERSION: ""
    GIT_DEPTH: 0
  script:
    - ./scripts/release.sh --version "$RELEASE_VERSION" --non-interactive

deploy:
  stage: deploy
  when: manual
  variables:
    DEPLOY_VERSION: ""
  script:
    - ./scripts/deploy.sh --version "$DEPLOY_VERSION"
        --deploy-path /opt/software --non-interactive
```

### 7.2 GitLab CI Example — Toolset Repo

```yaml
bundle-release:
  stage: release
  when: manual
  variables:
    RELEASE_VERSION: ""
    GIT_DEPTH: 0
    GIT_SUBMODULE_STRATEGY: recursive
  script:
    - ./scripts/bundle.sh --version "$RELEASE_VERSION"
        --deploy-path /opt/software --non-interactive

bundle-deploy:
  stage: deploy
  when: manual
  variables:
    DEPLOY_VERSION: ""
    GIT_DEPTH: 0
    GIT_SUBMODULE_STRATEGY: recursive
  script:
    - ./scripts/bundle.sh --deploy-only --version "$DEPLOY_VERSION"
        --deploy-path /opt/software --non-interactive
```

## 8. Design Decisions

### 8.1 Why three separate scripts?

**Role separation.** Developers create releases; DevOps deploy them. The toolset bundle is a separate concern from individual tool management. Three scripts means each has a focused CLI, clear ownership, and no mode-dispatch complexity.

**Shared library.** Common code (config, git, GitLab API, logging, semver, modulefile generation) lives in `src/lib/` and is imported by all three scripts. No duplication.

### 8.2 Why Python rewrite?

- **Maintainability.** Python's data structures (dicts, lists, dataclasses) handle config, submodule mappings, and template substitution more cleanly than Bash arrays and string manipulation.
- **Testing.** `unittest` provides proper unit testing with fixtures, mocking, and assertions — replacing BATS.
- **Error handling.** Python's `try/except` is more robust than Bash's `set -e` + trap.
- **Stdlib only.** Python 3.12.3 standard library covers everything: `argparse`, `subprocess`, `urllib.request`, `json`, `pathlib`, `tempfile`, `unittest`.

### 8.3 Why bootstrap via convention file?

A `install.sh`/`install.py` at the tool repo root is:
- **Self-contained** — travels with the tool, version-controlled
- **No config needed** — deploy.py auto-detects it
- **Flexible** — any setup logic (compile, pip install, make, etc.)

### 8.4 Why template-based modulefiles?

- **Per-tool templates** (`modulefile.tcl`) let tool developers define their own environment (PATH, LD_LIBRARY_PATH, env vars) without relying on deployers.
- **Config templates** (`MODULEFILE_TEMPLATE`) let DevOps override when the deploy target differs from what the tool developer assumed.
- **Default template** ensures zero-config deploys still work.

### 8.5 Why custom template = manifest for bundles?

The bundle template with per-tool placeholders (`%tool-a%`) gives full control over:
- **Which tools** are in the bundle (only referenced tools are included)
- **Load order** (template order = load order)
- **Custom setup** (env vars, paths, messages)

This avoids the need for a separate manifest file or ordering mechanism.

### 8.6 Why require submodules pinned to tags?

Tags are immutable. A submodule pointing to a branch HEAD would make the bundle non-reproducible — the same bundle version could resolve to different tool code at different times.

## 9. Test Plan

**Framework:** Python `unittest` (stdlib, no external packages)
**Runner:** `python3 -m unittest discover tests/`
**Mock server:** Reuse existing `tests/mock_gitlab.py`

| Test file | Coverage |
|---|---|
| `test_config.py` | Config loading, priority chain, env override, quoted values, token file |
| `test_semver.py` | Validation, suggestions, comparison, edge cases |
| `test_git.py` | Branch check, tag detection, changelog, remote URL parsing |
| `test_gitlab_api.py` | HTTP requests, auth, retry, project ID detection, MR creation |
| `test_modulefile.py` | Template loading, placeholder substitution, priority chain, default template |
| `test_release.py` | Release flow (dry-run, real), hotfix MR, interactive menu |
| `test_deploy.py` | Clone, bootstrap (install.sh, install.py, priority, failure), modulefile gen |
| `test_bundle.py` | Submodule detection, manifest, bundle modulefile, per-tool placeholders, `%TOOL_LOADS%`, template-as-manifest, bundle flow, deploy-only flow |

## 10. Scope Boundaries

### In scope
- Python rewrite of all release.sh functionality
- Three separate scripts with shared library
- Bootstrap support (`install.sh` / `install.py`)
- Custom modulefile templates with placeholder substitution
- Bundle workflow (submodule detection, release, deploy)
- Per-tool version placeholders in bundle templates
- Full test suite using `unittest`
- CLI compatibility (same flags, same config format)
- Documentation

### Out of scope (future work)
- Automated submodule update helper
- CI webhook that auto-triggers bundle release
- Lmod `.lua` modulefile format
- Cross-toolset dependency resolution
- Rollback / uninstall of deployed modulefiles
- Package distribution (pip install)
