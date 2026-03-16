# toolmanager — User Guide

This guide covers the full lifecycle of managing tools with toolmanager: configuring a manifest, releasing new versions, deploying them, scanning for updates, and writing toolset modulefiles.

---

## Concepts

toolmanager is made up of two independent scripts:

| Script | When you use it | What it does |
|---|---|---|
| `release.sh` | After merging to `main` | Creates a semver annotated tag + changelog |
| `deploy.sh` | After a release tag exists | Deploys tools via a central `tools.json` manifest |

**There are no GitLab API calls.** Everything works through plain git — tags, clones, and local directory scanning.

### The tools.json Manifest

`deploy.sh` is driven by a central `tools.json` file that records every managed tool, where it comes from, and what version is currently deployed. You maintain this file manually for source configuration; `deploy.sh` updates only the `version` field automatically on each deploy.

---

## Part 1 — Setup

### 1.1 Install toolmanager

Clone or submodule toolmanager into a shared location:

```bash
# Option A: submodule inside a management repo
git submodule add <toolmanager-repo-url> toolmanager

# Option B: clone to a shared path
git clone <toolmanager-repo-url> /opt/toolmanager
```

Make the wrappers executable if needed:

```bash
chmod +x toolmanager/scripts/*.sh
```

### 1.2 Configuration

Create a `.release.conf` to store your site's deploy paths and preferences:

```bash
cp toolmanager/scripts/.release.conf.example .release.conf
```

Minimum config to use deploy:

```ini
DEPLOY_BASE_PATH=/opt/software
TOOLS_MANIFEST=/etc/toolmanager/tools.json
```

Config is loaded in this order — later values win:

| Priority | Source |
|---|---|
| 1 (lowest) | `~/.release.conf` — personal defaults |
| 2 | `<repo>/.release.conf` — project-level settings |
| 3 | `--config FILE` — explicit path on the CLI |
| 4 (highest) | Environment variables |

### 1.3 Create tools.json

Create a `tools.json` file describing your tools. The path defaults to `tools.json` in the working directory; set `TOOLS_MANIFEST` in config or pass `--manifest FILE` to use a different location.

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
    "another-tool": {
      "version": "3.0.0",
      "source": {
        "type": "disk",
        "path": "/nfs/share/another-tool"
      }
    }
  },
  "toolsets": {
    "science": ["my-tool", "another-tool"]
  }
}
```

#### Source types

| Type | Required fields | Description |
|---|---|---|
| `git` | `url` | Clones a tag from any git remote |
| `disk` | `path` | Tool already lives on disk as semver-named subdirectories |

#### `disk` source layout

A `disk` source expects the tool's versions to already be arranged as semver subdirectories:

```
/nfs/share/another-tool/
├── 2.0.0/
├── 3.0.0/
└── 3.1.0/
```

`deploy.sh` will not copy or clone anything for disk sources — it only validates the version directory exists and writes the modulefile.

#### Toolsets

A toolset is a named list of tool names. Use the `toolset` subcommand to write a combined modulefile that loads all tools in the set at their currently-recorded versions.

---

## Part 2 — Releasing a Tool

### 2.1 How Release Works

`release.sh` creates an **annotated tag on `main`** and pushes it. No branches are created. No API calls are made.

The tag message contains:
- The version number
- An optional free-text description
- A changelog auto-generated from commits since the previous tag

### 2.2 Interactive Release

Navigate to your tool repo and run:

```bash
./toolmanager/scripts/release.sh
```

The flow:

1. Checks you are on `main`, tree is clean, and local is in sync with remote.
2. Reads the latest semver tag to determine the current version.
3. Prompts you to pick a version bump:

```
Current version: v1.2.3

  1) Patch  → v1.2.4
  2) Minor  → v1.3.0
  3) Major  → v2.0.0
  4) Custom
```

4. Shows the generated changelog.
5. Optionally prompts for a release description.
6. Asks for final confirmation.
7. Creates the annotated tag and pushes it.

### 2.3 Non-interactive Release (CI/CD)

```bash
./toolmanager/scripts/release.sh --version 1.3.0 --non-interactive

# With a description in the tag message
./toolmanager/scripts/release.sh --version 1.3.0 \
  --description "Adds widget support" --non-interactive
```

`--version` is required in non-interactive mode.

### 2.4 Dry Run

```bash
./toolmanager/scripts/release.sh --dry-run
```

Runs all validation (branch, clean tree, remote sync, version availability) without making changes.

### 2.5 Tag Message Format

```
Release v1.3.0

Adds widget support

Changelog:
- feat: add widget (a1b2c3d)
- fix: handle nil input (e4f5g6h)
```

### release.sh Options

| Option | Description |
|---|---|
| `--version X.Y.Z` | Set version non-interactively |
| `--description DESC` | Free-text summary prepended to changelog |
| `--dry-run` | Validate without making changes |
| `--non-interactive`, `-n` | Auto-confirm all prompts |
| `--config FILE` | Load configuration from FILE |
| `--help`, `-h` | Show help |

---

## Part 3 — Deploying Tools

`deploy.sh` has four subcommands:

```
deploy.sh deploy  <tool> [--version X.Y.Z]   Deploy a tool version
deploy.sh scan                                Check all tools for newer versions
deploy.sh upgrade <tool>                      Deploy the latest available version
deploy.sh toolset <name> --version X.Y.Z      Write a toolset modulefile
```

Global options available on all subcommands:

```
--manifest FILE       Path to tools.json (default: TOOLS_MANIFEST or ./tools.json)
--config FILE         Path to .release.conf
--deploy-path PATH    Override DEPLOY_BASE_PATH
--mf-path PATH        Override MF_BASE_PATH
--dry-run             Show what would be done; make no changes
--non-interactive, -n Auto-confirm all prompts
--help, -h            Show help (works at top level and per subcommand)
```

### 3.1 deploy — Deploy a Specific Version

```bash
deploy.sh deploy my-tool --version 1.3.0
```

What happens:

1. Reads `tools.json` and finds the tool entry.
2. Validates the requested version exists (for git sources, checks that the tag is published before attempting a clone).
3. Asks for confirmation: `Deploy my-tool 1.3.0 (git) → /opt/software/my-tool/1.3.0?`
4. Clones the tag (git) or validates the version directory (disk).
5. Runs the bootstrap script if present (`install.sh` takes priority over `install.py`).
6. Writes a modulefile.
7. Updates `version` in `tools.json`.

If `--version` is omitted in non-interactive mode, the latest available version is selected automatically. In interactive mode, a numbered list is shown:

```
  Tool:              my-tool
  Currently at:      1.2.0
  Available:         12 versions — showing latest 10:
     1. 1.0.0
     2. 1.1.0
     ...
    10. 1.3.0  ← latest

  Enter a number or version [latest: 1.3.0, Ctrl+C to cancel]:
```

**Deploy directory layout (git source):**

```
DEPLOY_BASE_PATH/
├── my-tool/
│   ├── 1.2.0/               ← cloned tag (shallow, depth 1)
│   │   ├── bin/
│   │   └── install.sh       ← bootstrap ran here after clone
│   └── 1.3.0/
└── mf/
    └── my-tool/
        ├── 1.2.0             ← generated modulefile
        └── 1.3.0
```

If `MF_BASE_PATH` is configured, modulefiles go there instead:

```
MF_BASE_PATH/
└── my-tool/
    ├── 1.2.0
    └── 1.3.0
```

```bash
# Examples
deploy.sh deploy my-tool --version 1.3.0
deploy.sh deploy my-tool                         # interactive version picker
deploy.sh deploy my-tool --version 1.3.0 -n      # non-interactive
deploy.sh deploy my-tool --version 1.3.0 --dry-run
deploy.sh deploy my-tool --version 1.3.0 --mf-path /opt/modulefiles
```

### 3.2 scan — Check for Updates

```bash
deploy.sh scan
```

Checks every tool in `tools.json` against its source and prints an upgrade table:

```
  my-tool        1.2.0   →  1.3.0  (minor)
  another-tool   3.0.0   →  3.1.0  (patch)
  stable-tool    2.0.0   (up to date)
  broken-tool    1.0.0   ⚠ error: Cannot list tags from git@...
```

Bump labels: `patch`, `minor`, `major`, `up-to-date`, `ahead` (current newer than latest), `new` (never deployed), `unknown` (version unparseable).

In **interactive mode**, after the table you are prompted which tools to upgrade:

```
Upgrades available:
   1. my-tool      1.2.0 → 1.3.0
   2. another-tool 3.0.0 → 3.1.0

  Enter numbers to upgrade (space or comma separated),
  "all" to upgrade everything, or blank to skip:
  > 1 2
```

The planned upgrades are echoed and confirmed before any deploy runs. In **non-interactive mode** (`-n`), scan prints the table and exits without deploying.

```bash
deploy.sh scan                   # interactive: prompts to upgrade
deploy.sh scan -n                # non-interactive: report only
deploy.sh scan --dry-run         # show what would be deployed
deploy.sh scan --manifest /etc/tools.json
```

### 3.3 upgrade — Deploy Latest Version

```bash
deploy.sh upgrade my-tool
```

Looks up the latest available version for `my-tool`, compares it with the version recorded in `tools.json`, and deploys it if newer. If already at the latest version, exits successfully with no changes.

```bash
deploy.sh upgrade my-tool
deploy.sh upgrade my-tool -n     # non-interactive
deploy.sh upgrade my-tool --dry-run
```

### 3.4 toolset — Write a Toolset Modulefile

```bash
deploy.sh toolset science --version 1.0.0
```

Reads the `"science"` toolset from `tools.json`, collects the current deployed version of each tool in the set, generates a modulefile that loads all of them, and writes it to `MF_BASE_PATH/science/1.0.0` (or `DEPLOY_BASE_PATH/mf/science/1.0.0`).

All tools in the toolset must have a version recorded in `tools.json`. If any are missing, the command lists them with suggested `deploy` commands and exits.

Generated modulefile (default template):

```tcl
#%Module1.0
##
## science/1.0.0 modulefile
##

proc ModulesHelp { } {
    puts stderr "science version 1.0.0"
}

module-whatis "science version 1.0.0"

conflict science

module load another-tool/3.0.0
module load my-tool/1.3.0
```

```bash
deploy.sh toolset science --version 1.0.0
deploy.sh toolset science --version 1.0.0 --mf-path /opt/modulefiles
deploy.sh toolset science --version 1.0.0 --dry-run
deploy.sh toolset science --version 1.0.0 -n
```

---

## Part 4 — Bootstrap Scripts

After cloning a git source, `deploy.sh` looks for a bootstrap script in the cloned directory root:

1. `install.sh` — run via `bash` (takes priority)
2. `install.py` — run via `python3`

Only one runs. If bootstrap fails, you are prompted to either remove the cloned directory or leave it in place for inspection.

Example `install.sh`:

```bash
#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
pip install -r requirements.txt --prefix="$PREFIX"
```

Example `install.py`:

```python
import subprocess, pathlib
root = pathlib.Path(__file__).parent
subprocess.run(["make", "install", f"PREFIX={root}/install"], cwd=root, check=True)
```

Bootstrap scripts run with the cloned directory as their working directory. They are skipped for `disk` sources (the tool is already installed).

---

## Part 5 — Modulefile System

The modulefile written to `mf/<tool>/<version>` is resolved in this priority chain:

```
Previous version modulefile exists?   → copy it + update version references
  no ↓
Cloned repo has modulefile.tcl?       → substitute placeholders, write
  no ↓
Config has MODULEFILE_TEMPLATE path?  → substitute placeholders, write
  no ↓
Default hardcoded template            → write
```

### Placeholders

| Placeholder | Value |
|---|---|
| `%VERSION%` | Version being deployed (e.g. `1.3.0`) |
| `%ROOT%` | Full path to the deployed directory |
| `%TOOL_NAME%` | Tool name |
| `%DEPLOY_BASE_PATH%` | The deploy base path |

Toolset templates also support:

| Placeholder | Value |
|---|---|
| `%TOOL_LOADS%` | Auto-generated `module load` block for all tools |
| `%<tool-name>%` | Version of the named tool (e.g. `%my-tool%` → `1.3.0`) |

### Example tool `modulefile.tcl`

Place this file in the root of your tool repo:

```tcl
#%Module1.0
proc ModulesHelp { } {
    puts stderr "my-tool version %VERSION%"
}
module-whatis "my-tool %VERSION%"
conflict my-tool
set root %ROOT%
prepend-path PATH            $root/bin
prepend-path LD_LIBRARY_PATH $root/lib
prepend-path MANPATH          $root/share/man
```

### Example custom toolset template

```tcl
#%Module1.0
proc ModulesHelp { } {
    puts stderr "science %VERSION% — my-tool %my-tool%, another-tool %another-tool%"
}
module-whatis "science %VERSION%"
conflict science

%TOOL_LOADS%
```

Set via config:

```ini
MODULEFILE_TEMPLATE=/opt/templates/science.tcl
```

---

## Part 6 — Complete Workflows

### 6.1 First-time Setup

```bash
# 1. Create tools.json
cat > tools.json << 'EOF'
{
  "tools": {
    "my-tool": {
      "version": "",
      "source": {
        "type": "git",
        "url": "git@gitlab.com:group/my-tool.git"
      }
    }
  },
  "toolsets": {}
}
EOF

# 2. Scan to see what's available
deploy.sh scan --manifest tools.json --deploy-path /opt/software

# 3. Deploy the latest
deploy.sh deploy my-tool --manifest tools.json --deploy-path /opt/software
```

### 6.2 Release + Deploy a Single Tool

```bash
# 1. Merge your work to main, then from the tool repo:
cd /path/to/my-tool

# 2. Validate without making changes
./toolmanager/scripts/release.sh --dry-run

# 3. Release
./toolmanager/scripts/release.sh

# 4. Deploy (on the deploy host, from the manifest directory)
deploy.sh deploy my-tool --version 1.3.0

# 5. Users load the tool
module load my-tool/1.3.0
```

### 6.3 Routine Upgrade Scan

```bash
# Check everything, then interactively choose what to upgrade
deploy.sh scan

# Upgrade a specific tool to latest
deploy.sh upgrade my-tool

# Upgrade all tools non-interactively (CI/CD)
deploy.sh scan -n              # report only, or:
deploy.sh upgrade my-tool -n
```

### 6.4 Create a Toolset Modulefile

A toolset modulefile loads a named collection of tools with a single `module load` command. The version you pass to `toolset` is the version of the *toolset itself* — it does not need to match any individual tool version.

#### Step 1 — Define the toolset in tools.json

Add a `"toolsets"` entry listing the tool names that belong to the set:

```json
{
  "tools": {
    "tool-a": {
      "version": "1.2.0",
      "source": { "type": "git", "url": "git@gitlab.com:group/tool-a.git" }
    },
    "tool-b": {
      "version": "2.0.0",
      "source": { "type": "disk", "path": "/nfs/share/tool-b" }
    }
  },
  "toolsets": {
    "science": ["tool-a", "tool-b"]
  }
}
```

Each tool in the list must already have a non-empty `version` field — that version ends up in the `module load` lines. If a tool has not been deployed yet, its `version` will be empty and the `toolset` command will refuse to run.

#### Step 2 — Deploy any tools that have no recorded version

```bash
# Check which tools are missing a version
deploy.sh scan -n

# Deploy missing tools
deploy.sh deploy tool-a --version 1.2.0 -n
deploy.sh deploy tool-b --version 2.0.0 -n
```

After each deploy, `tools.json` is updated with the deployed version automatically.

#### Step 3 — Write the toolset modulefile

```bash
deploy.sh toolset science --version 1.0.0
```

The modulefile is written to `DEPLOY_BASE_PATH/mf/science/1.0.0` (or `MF_BASE_PATH/science/1.0.0` if set). Default output:

```tcl
#%Module1.0
##
## science/1.0.0 modulefile
##

proc ModulesHelp { } {
    puts stderr "science version 1.0.0"
}

module-whatis "science version 1.0.0"

conflict science

module load tool-a/1.2.0
module load tool-b/2.0.0
```

#### Step 4 — Users load the toolset

```bash
module load science/1.0.0
```

This loads `tool-a/1.2.0` and `tool-b/2.0.0` in one command.

---

#### Updating the toolset for new tool versions

When you deploy newer versions of constituent tools, write a new toolset version to record the change:

```bash
# Deploy the updated tool
deploy.sh deploy tool-a --version 1.3.0 -n

# Write a new toolset modulefile referencing the new versions
deploy.sh toolset science --version 1.1.0
```

Old toolset modulefiles (`science/1.0.0`) remain on disk — users pinned to the old set are unaffected.

---

#### Using a custom toolset template

The default template only generates `module load` lines. For sites that need extra environment setup, create a Tcl template and reference it in config:

```ini
# .release.conf
MODULEFILE_TEMPLATE=/opt/templates/science.tcl
```

```tcl
#%Module1.0
proc ModulesHelp { } {
    puts stderr "science %VERSION% — tool-a %tool-a%, tool-b %tool-b%"
}
module-whatis "science %VERSION%"
conflict science

%TOOL_LOADS%

setenv SCIENCE_HOME /opt/software/science/%VERSION%
```

Available placeholders in toolset templates:

| Placeholder | Expands to |
|---|---|
| `%VERSION%` | The version passed to `--version` (e.g. `1.0.0`) |
| `%TOOL_NAME%` | The toolset name (`science`) |
| `%TOOL_LOADS%` | Full `module load` block for all tools in the set |
| `%tool-a%` | Deployed version of `tool-a` (from `tools.json`) |
| `%tool-b%` | Deployed version of `tool-b` (from `tools.json`) |

Any `%name%` placeholder that does not match a tool in the toolset is treated as an error and the command exits before writing.

---

#### Common options

```bash
# Separate modulefile directory
deploy.sh toolset science --version 1.0.0 --mf-path /opt/modulefiles

# Preview without writing anything
deploy.sh toolset science --version 1.0.0 --dry-run

# Non-interactive (no confirmation prompt)
deploy.sh toolset science --version 1.0.0 -n

# Explicit manifest path
deploy.sh toolset science --version 1.0.0 --manifest /etc/tools.json
```

### 6.5 Redeploy an Older Version

```bash
# Deploy directories and modulefiles from older versions coexist
deploy.sh deploy my-tool --version 1.2.0 -n

module load my-tool/1.2.0
```

Note: redeploying an older version updates `tools.json` to record it as the current version. Update it back manually or deploy the newer version again when ready.

### 6.6 CI/CD Integration

```bash
# Non-interactive release
./scripts/release.sh --version "$RELEASE_VERSION" --non-interactive

# Non-interactive deploy
deploy.sh deploy my-tool --version "$DEPLOY_VERSION" \
  --deploy-path /opt/software -n

# Scan and report (no deploy) — suitable for a scheduled check job
deploy.sh scan -n
```

---

## Part 7 — Config Reference

### All Config Keys

| Config Key | Env Variable | Default | Description |
|---|---|---|---|
| `DEFAULT_BRANCH` | `RELEASE_DEFAULT_BRANCH` | `main` | Branch that releases are tagged from |
| `TAG_PREFIX` | `RELEASE_TAG_PREFIX` | `v` | Prefix added to version tags (`v` → `v1.2.3`) |
| `REMOTE` | `RELEASE_REMOTE` | `origin` | Git remote name |
| `DEPLOY_BASE_PATH` | `DEPLOY_BASE_PATH` | *(none)* | Root directory for cloned releases and modulefiles |
| `TOOLS_MANIFEST` | `TOOLS_MANIFEST` | `./tools.json` | Path to the tools.json manifest |
| `MF_BASE_PATH` | `MF_BASE_PATH` | *(none)* | Override modulefile directory (e.g. separate NFS mount) |
| `MODULEFILE_TEMPLATE` | `MODULEFILE_TEMPLATE` | *(none)* | Path to a custom modulefile template file |

Environment variables are snapshotted at startup — config files cannot override them.

### Annotated `.release.conf`

```ini
# .release.conf — site config for release.sh and deploy.sh

# Override git settings if your repo differs from defaults
# DEFAULT_BRANCH=main
# TAG_PREFIX=v
# REMOTE=origin

# Required for deploy.sh — must be an absolute path
DEPLOY_BASE_PATH=/opt/software

# Path to the tools.json manifest (default: ./tools.json)
# TOOLS_MANIFEST=/etc/toolmanager/tools.json

# Separate modulefile directory (e.g. NFS share)
# MF_BASE_PATH=/opt/modulefiles

# Custom modulefile template file
# MODULEFILE_TEMPLATE=/opt/templates/my-tool.tcl
```

---

## Part 8 — Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| `Must be on 'main' branch` | You are on a feature branch | `git checkout main` |
| `Working tree is dirty` | Uncommitted changes | `git stash` or commit |
| `Not in sync with remote` | Local ahead or behind | `git pull` or `git push` |
| `Tag 'v1.2.3' already exists` | Version already released | Choose a higher version |
| `DEPLOY_BASE_PATH must be an absolute path` | Relative path passed | Use a full path: `/opt/software` |
| `DEPLOY_BASE_PATH is not configured` | Path not set | Pass `--deploy-path`, set env var, or add to `.release.conf` |
| `Version 1.3.0 is not a published tag` | Tag not pushed yet | Run `release.sh` first, or `git push --tags` |
| `Deploy directory already exists` | Already deployed this version | `rm -rf DEPLOY_BASE_PATH/tool/version` to reinstall |
| `Modulefile already exists` | Already deployed this version | `rm MF_BASE_PATH/tool/version` to regenerate |
| `Bootstrap failed` | `install.sh` or `install.py` exited non-zero | Check the script; prompted to clean up the clone |
| `No versions available` | Source has no semver tags / disk path empty | Push a release tag, or check `source.path` |
| `tools.json not found` | Manifest path wrong | Pass `--manifest FILE` or set `TOOLS_MANIFEST` |
| `Tool has no deployed version recorded` | `version` is empty in tools.json | Run `deploy.sh deploy <tool>` first |
| Tool name contains `/` | Manifest has a path-traversal name | Fix the tool name in tools.json |
