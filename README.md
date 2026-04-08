# toolmanager

Python utility scripts for DevOps workflows: release automation and manifest-driven tool deployment for git-based software environments.

## Overview

Two scripts, each handling one concern:

| Script | Who runs it | What it does |
|---|---|---|
| `release.sh` | Developers | Version selection, annotated tag on main, changelog |
| `deploy.sh` | DevOps | Manifest-driven deploy, version scanning, toolset modulefiles |

**Technology:** Python 3.12+, standard library only. Thin Bash wrappers call the Python scripts.

---

## Getting Started

### Prerequisites

- Python 3.12+
- git

### Installation

```bash
git clone <this-repo-url> /opt/toolmanager
chmod +x /opt/toolmanager/scripts/*.sh
```

### Initial Configuration

Create a `tools.json` manifest:

```json
{
  "deploy_base_path": "/opt/software",
  "tools": {},
  "toolsets": {}
}
```

`deploy_base_path` sets the default root directory for deployments. It can always be overridden with `--deploy-path` on the command line.

---

## release.sh

Automates version tagging. Handles version selection, changelog generation, and annotated tag creation on main. No branches, no merge requests, no GitLab token required.

```bash
# Interactive release
./scripts/release.sh

# Non-interactive (CI/CD)
./scripts/release.sh --version 1.2.3 --non-interactive

# With a description in the tag message
./scripts/release.sh --version 1.2.3 --description "Adds widget support" -n

# Dry run — validates everything, no changes
./scripts/release.sh --dry-run
```

### What It Does

1. Validates repo state (on main, clean tree, synced with remote).
2. Detects the current version from semver tags.
3. Prompts for a version bump (patch / minor / major / custom).
4. Generates a changelog from commits since the last tag.
5. Optionally prompts for a release description.
6. Creates an annotated tag on main and pushes it.

### Options

| Option | Description |
|---|---|
| `--version X.Y.Z` | Set version non-interactively |
| `--description DESC` | Free-text summary prepended to changelog in tag message |
| `--dry-run` | Validate without making changes |
| `--non-interactive`, `-n` | Auto-confirm all prompts |
| `--config FILE` | Load configuration from FILE |
| `--help`, `-h` | Show help |

---

## deploy.sh

Manifest-driven deployment tool. Reads `tools.json` to know what tools exist, where they come from, and what version is currently deployed.

```bash
deploy.sh <subcommand> [OPTIONS]
```

### Subcommands

#### `deploy` — Deploy a specific version

```bash
deploy.sh deploy my-tool --version 1.3.0 --deploy-path /opt/software
deploy.sh deploy my-tool                     # interactive version picker
deploy.sh deploy my-tool --version 1.3.0 -n  # non-interactive
```

Clones the tag (git source) or validates the version directory (disk source), runs the bootstrap command if configured, writes a modulefile, and updates `tools.json`.

#### `scan` — Check all tools for updates

```bash
deploy.sh scan        # interactive: shows table, prompts to upgrade
deploy.sh scan -n     # non-interactive: report only, no deploy
```

Prints an upgrade table for every tool in the manifest:

```
  my-tool      1.2.0   →  1.3.0  (minor)
  stable-tool  2.0.0   (up to date)
  matlab       2024.1  (up to date) (external)
```

Externally managed tools (source type `"external"`) are shown with an `(external)` marker and excluded from the upgrade prompt.

#### `upgrade` — Deploy the latest available version

```bash
deploy.sh upgrade my-tool
deploy.sh upgrade my-tool -n
```

Fetches the latest version from the source, deploys it if newer than what's recorded in `tools.json`, and exits successfully if already up to date.

#### `toolset` — Write a toolset modulefile

```bash
deploy.sh toolset science --version 1.0.0
```

Reads the named toolset from `tools.json`, collects current deployed versions of all member tools, and writes a combined modulefile that loads them all.

#### `apply` — Declarative deploy from toolset version pins

```bash
deploy.sh apply                          # deploy all missing tool versions
deploy.sh apply --toolset science        # only one toolset
deploy.sh apply --dry-run                # preview
```

Reads dict-format toolsets from `tools.json`, deploys every tool+version pair not already on disk, and writes toolset modulefiles. This is the "reconcile" step for a GitOps workflow.

### Global Options (all subcommands)

| Option | Description |
|---|---|
| `--deploy-path PATH` | Deploy base path (overrides manifest `deploy_base_path`) |
| `--manifest FILE` | Path to tools.json |
| `--mf-path PATH` | Override modulefile directory |
| `--config FILE` | Load configuration from FILE |
| `--dry-run` | Show what would be done; make no changes |
| `--non-interactive`, `-n` | Auto-confirm all prompts |
| `--force` | Override deploy protection for externally managed tools |
| `--help`, `-h` | Show help (also works per subcommand) |

### Exit Codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | General error (manifest validation, modulefile write, etc.) |
| `2` | Configuration or argument error (bad flag, missing path, invalid version) |
| `3` | Source adapter error (git clone failed, tag not found, timeout) |
| `4` | Deploy-time error (lock contention, directory exists, bootstrap failure) |

### tools.json Schema

```json
{
  "deploy_base_path": "/opt/software",
  "app_root": "custom/apps",
  "tools": {
    "my-tool": {
      "version": "1.2.0",
      "available": ["1.0.0", "1.1.0", "1.2.0"],
      "source": {
        "type": "git",
        "url": "git@gitlab.com:group/my-tool.git"
      },
      "bootstrap": "./install.sh",
      "install_path": "{{app_root}}/{{toolname}}/{{version}}",
      "mf_path": "modulefiles/{{toolname}}/{{version}}"
    },
    "packaged-tool": {
      "version": "3.0.0",
      "source": {
        "type": "archive",
        "path": "/nfs/share/packaged-tool"
      },
      "flatten_archive": false
    },
    "matlab": {
      "version": "2024.1.0",
      "source": {
        "type": "external",
        "path": "/opt/external/matlab"
      }
    }
  },
  "toolsets": {
    "science": {
      "version": "1.0.0",
      "tools": {
        "my-tool": "1.2.0",
        "packaged-tool": "3.0.0",
        "matlab": "2024.1.0"
      }
    },
    "legacy-suite": ["my-tool", "packaged-tool"]
  }
}
```

#### Top-level fields

| Field | Default | Description |
|---|---|---|
| `deploy_base_path` | `"/"` | Default root for deployments; overridden by `--deploy-path` |
| `tools` | `{}` | Tool definitions |
| `toolsets` | `{}` | Named groups of tools for combined modulefiles |
| *custom string keys* | — | Any additional string field at root level becomes a template variable (e.g. `"app_root": "custom/apps"` → `{{app_root}}`). Non-string values are ignored. |

#### Per-tool fields

| Field | Required | Description |
|---|---|---|
| `source` | Yes | Source definition (see **Source types** below) |
| `version` | No | Current deployed version (updated automatically on deploy) |
| `available` | No | List of available version strings (populated by `scan`) |
| `install_path` | No | Custom deploy path; relative paths resolve against `deploy_base_path`. Supports `{{toolname}}`, `{{version}}`, and any custom string variables defined at root or tool level. |
| `mf_path` | No | Custom modulefile path; relative paths resolve against `deploy_base_path`. Supports the same placeholders as `install_path`. |
| `bootstrap` | No | Shell command to run after deploy via `sh -c`. Environment variables `INSTALL_PATH`, `TOOL_VERSION`, and `TOOL_NAME` are set automatically. |
| `flatten_archive` | No | For archive sources: flatten single-root directories after extraction (default: `true`) |
| *custom string keys* | No | Any additional string field at tool level becomes a template variable, overriding root-level variables of the same name. |

#### Source types

| Type | Required fields | Description |
|---|---|---|
| `git` | `url` | Git repository URL. Clones the version tag on deploy. |
| `archive` | `path` | Directory containing version subdirectories with archive files (`.tar.gz`, `.tar.bz2`, `.tar.xz`, `.tgz`, `.zip`). Extracted on deploy. |
| `external` | `path` | Directory containing version subdirectories. No files are copied — tool is assumed already installed. Deploy and upgrade blocked unless `--force` is used. |

#### Toolset formats

Toolsets support two formats:

**Dict format** (version-pinned, required for `apply`):

```json
"science": {
  "version": "1.0.0",
  "tools": {
    "my-tool": "1.2.0",
    "packaged-tool": "3.0.0"
  }
}
```

| Field | Required | Description |
|---|---|---|
| `version` | Yes | The toolset's own version (must be valid semver X.Y.Z) |
| `tools` | Yes | Mapping of tool names to pinned version strings (each must be valid semver) |

**Legacy list format** (uses each tool's current `version` from the manifest):

```json
"legacy-suite": ["my-tool", "packaged-tool"]
```

### Deploy Directory Structure

```
deploy_base_path/
├── my-tool/
│   ├── 1.2.0/           # cloned tag (git source)
│   └── 1.3.0/
└── mf/
    └── my-tool/
        ├── 1.2.0         # generated modulefile
        └── 1.3.0
```

---

## Configuration

Config files use `KEY=VALUE` format with `#` comments and optional quotes.

### Config Keys

| Config Key | Env Variable | Default | Description |
|---|---|---|---|
| `DEFAULT_BRANCH` | `RELEASE_DEFAULT_BRANCH` | `main` | Branch to release from |
| `TAG_PREFIX` | `RELEASE_TAG_PREFIX` | `v` | Tag prefix (e.g. `v` → `v1.2.3`) |
| `REMOTE` | `RELEASE_REMOTE` | `origin` | Git remote name |
| `TOOLS_MANIFEST` | `TOOLS_MANIFEST` | `./tools.json` | Path to the tools.json manifest |
| `MF_BASE_PATH` | `MF_BASE_PATH` | *(none)* | Override modulefile directory |
| `MODULEFILE_TEMPLATE` | `MODULEFILE_TEMPLATE` | *(none)* | Path to a custom modulefile template |

### Environment Variables (runtime)

| Variable | Default | Description |
|---|---|---|
| `TOOLMANAGER_GIT_TIMEOUT` | `120` | Seconds before git operations are killed (applies to all git subprocess calls) |

---

## Architecture

```
src/
├── lib/                       # Shared Python library
│   ├── config.py              # Multi-level config loading (file → env → CLI)
│   ├── git.py                 # Git operations with configurable timeout
│   ├── log.py                 # Color-coded stderr logging (info/warn/error/success)
│   ├── semver.py              # Strict X.Y.Z validation + bump suggestions
│   ├── modulefile.py          # Modulefile generation, templates, placeholder substitution
│   ├── manifest.py            # tools.json read/write/validation (atomic writes)
│   ├── sources.py             # GitAdapter, ArchiveAdapter, ExternalAdapter (with timeouts)
│   └── prompt.py              # Interactive y/n and version prompts (auto-skip in CI)
├── release.py                 # Release tool: tag from main + changelog
└── deploy.py                  # Deploy tool: subcommand-driven with file locking

scripts/
├── release.sh                 # Thin wrapper → src/release.py
├── deploy.sh                  # Thin wrapper → src/deploy.py
└── .release.conf.example      # Annotated config file template
```

---

## Running Tests

No external dependencies — tests use the Python standard library only.

```bash
# Run all tests
python3 -m unittest discover tests/ -p "test_*.py"

# Verbose output
python3 -m unittest discover tests/ -p "test_*.py" -v

# Single file
python3 -m unittest tests/test_deploy.py

# Single test case
python3 -m unittest tests.test_semver.TestValidateSemver.test_valid_versions
```

Requirements: Python 3.12+, Git, Bash. No `pip install` needed.

---

## Example Workspace

A self-contained demo environment lives in `examples/workspace/`. It
creates two local git repos (hello-cli and calculator) with multiple
tagged versions, deploys them via the manifest, and generates
modulefiles — all without network access.

```bash
cd examples/workspace
./setup.sh                    # creates repos, resolves paths, deploys v1.0.0

# Tools are deployed and ready
deploy/hello-cli/1.0.0/bin/hello          # => Hello, World!
deploy/calculator/1.0.0/bin/calc add 2 3  # => 5

# Modulefiles are generated
ls modulefiles/hello-cli/
ls modulefiles/calculator/
```

The workspace demonstrates all four subcommands:

```bash
DEPLOY="../../scripts/deploy.sh"
OPTS="--manifest manifest/tools.json --config manifest/.release.conf -n"

$DEPLOY scan $OPTS                                      # check for updates
$DEPLOY upgrade hello-cli $OPTS                         # deploy latest version
$DEPLOY deploy calculator --version 1.1.0 $OPTS         # deploy specific version
$DEPLOY toolset demo-suite --version 1.0.0 $OPTS        # combined modulefile
```

See [`examples/workspace/README.md`](examples/workspace/README.md) for
the full walkthrough, CI/CD automation patterns, and customisation
guide. Run `./teardown.sh` to clean up.

---

## Full Documentation

See **[GUIDE.md](GUIDE.md)** for the complete user guide covering:
- Setting up `tools.json` from scratch
- Full workflow walkthroughs (release → deploy → toolset)
- Externally managed tools and container deployments
- Bootstrap commands
- Modulefile template system and placeholders
- Disk source archive extraction
- CI/CD integration patterns
- Troubleshooting reference
