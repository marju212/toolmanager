# toolmanager

Python utility scripts for DevOps workflows: release automation and manifest-driven tool deployment for git-based software environments.

## Overview

Two scripts, each handling one concern:

| Script | Who runs it | What it does |
|---|---|---|
| `release.sh` | Developers | Version selection, annotated tag on main, changelog |
| `deploy.sh` | DevOps | Manifest-driven deploy, version scanning, toolset modulefiles |

**Technology:** Python 3.12, standard library only. Thin Bash wrappers call the Python scripts.

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

```bash
cp /opt/toolmanager/scripts/.release.conf.example .release.conf
# edit .release.conf and set DEPLOY_BASE_PATH at minimum
```

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
deploy.sh deploy my-tool --version 1.3.0
deploy.sh deploy my-tool                     # interactive version picker
deploy.sh deploy my-tool --version 1.3.0 -n  # non-interactive
```

Clones the tag (git source) or validates the version directory (disk source), runs the bootstrap script if present, writes a modulefile, and updates `tools.json`.

#### `scan` — Check all tools for updates

```bash
deploy.sh scan        # interactive: shows table, prompts to upgrade
deploy.sh scan -n     # non-interactive: report only, no deploy
```

Prints an upgrade table for every tool in the manifest:

```
  my-tool      1.2.0   →  1.3.0  (minor)
  stable-tool  2.0.0   (up to date)
```

In interactive mode, prompts which tools to upgrade after the report.

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

### Global Options (all subcommands)

| Option | Description |
|---|---|
| `--manifest FILE` | Path to tools.json |
| `--deploy-path PATH` | Override DEPLOY_BASE_PATH |
| `--mf-path PATH` | Override MF_BASE_PATH (modulefile directory) |
| `--dry-run` | Show what would be done; make no changes |
| `--non-interactive`, `-n` | Auto-confirm all prompts |
| `--config FILE` | Load configuration from FILE |
| `--help`, `-h` | Show help (also works per subcommand) |

### tools.json Schema

```json
{
  "tools": {
    "my-tool": {
      "version": "1.2.0",
      "source": {
        "type": "git",
        "url": "git@gitlab.com:group/my-tool.git"
      }
    },
    "disk-tool": {
      "version": "3.0.0",
      "source": {
        "type": "disk",
        "path": "/nfs/share/disk-tool"
      }
    }
  },
  "toolsets": {
    "science": ["my-tool", "disk-tool"]
  }
}
```

Source types: `git` (requires `url`), `disk` (requires `path`). Only the `version` field is written by the tool; everything else is maintained by hand.

### Deploy Directory Structure

```
DEPLOY_BASE_PATH/
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
| `DEPLOY_BASE_PATH` | `DEPLOY_BASE_PATH` | *(none)* | Root for cloned releases and modulefiles |
| `TOOLS_MANIFEST` | `TOOLS_MANIFEST` | `./tools.json` | Path to the tools.json manifest |
| `MF_BASE_PATH` | `MF_BASE_PATH` | *(none)* | Override modulefile directory |
| `MODULEFILE_TEMPLATE` | `MODULEFILE_TEMPLATE` | *(none)* | Path to a custom modulefile template |

Environment variables take highest priority and are snapshotted at startup.

---

## Architecture

```
src/
├── lib/                       # Shared Python library
│   ├── config.py              # Multi-level config loading
│   ├── git.py                 # Git operations via subprocess
│   ├── log.py                 # Color-coded stderr logging
│   ├── semver.py              # Semver validation + suggestions
│   ├── modulefile.py          # Modulefile generation + templates
│   ├── manifest.py            # tools.json read/write/validation
│   ├── sources.py             # GitAdapter + DiskAdapter
│   └── prompt.py              # Interactive prompts
├── release.py                 # Release tool
└── deploy.py                  # Deploy tool (subcommand-driven)

scripts/
├── release.sh                 # Thin wrapper → src/release.py
├── deploy.sh                  # Thin wrapper → src/deploy.py
└── .release.conf.example      # Annotated config file template
```

---

## Running Tests

```bash
# Run all tests
python3 -m unittest discover tests/ -p "test_*.py"

# Verbose output
python3 -m unittest discover tests/ -p "test_*.py" -v

# Single file
python3 -m unittest tests/test_deploy.py
```

---

## Full Documentation

See **[GUIDE.md](GUIDE.md)** for the complete user guide covering:
- Setting up `tools.json` from scratch
- Full workflow walkthroughs (release → deploy → toolset)
- Bootstrap scripts
- Modulefile template system and placeholders
- CI/CD integration patterns
- Troubleshooting reference
