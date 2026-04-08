# toolmanager — User Guide

This guide covers the full lifecycle of managing tools with toolmanager: configuring a manifest, releasing new versions, deploying them, scanning for updates, handling externally managed tools, and writing toolset modulefiles.

---

## Why toolmanager?

### The problem

Managing scientific and engineering tools on shared infrastructure is messy. Tools come from different sources — some are built from git repos, some arrive as tar archives on a network share, some are installed by IT. Keeping track of what's deployed, at which version, and making it available to users via `module load` involves a lot of manual, error-prone work.

### What toolmanager gives you

**One manifest as source of truth.** `tools.json` declares every tool, where it comes from, and what version is deployed. The manifest is a plain JSON file you can check into version control, review in a PR, and audit over time.

**Unified handling of different sources.** Whether a tool lives in git, ships as a tar archive, or is pre-installed by IT — the workflow is the same: scan, pick a version, deploy. Three adapter types (`git`, `archive`, `external`) handle the differences behind the scenes.

**Version coexistence.** Multiple versions of the same tool live side by side (`/opt/tools/numpy/1.24.0/`, `/opt/tools/numpy/2.0.0/`). Users pick the version they need with `module load numpy/1.24.0`. Nothing is overwritten — old versions stay until you explicitly remove them.

**Toolsets with pinned versions.** A toolset bundles multiple tools at specific versions into one `module load science/1.0.0` command. Different toolsets can pin different versions of the same tool. This gives teams reproducible environments without forcing everyone onto the same version.

**Declarative GitOps workflow.** The `apply` command reconciles the desired state in `tools.json` with what's actually on disk. The workflow is:

1. `scan` — discover what's available, write it to the manifest
2. Edit toolsets — pick versions
3. `apply` — deploy everything that's missing

This is auditable, repeatable, and works well in CI/CD.

**Safe by default.** Dry-run mode on every command. Advisory file locking (with PID tracking) prevents concurrent deploys. Path traversal protection on template substitution. Symlink detection prevents path attacks. External tools are blocked from deploy unless you explicitly `--force`. No files are ever silently overwritten. Distinct exit codes (2=config, 3=source, 4=deploy) let CI pipelines react to specific failure types.

**No external dependencies.** Python 3.12 stdlib only — no pip, no venv, no network calls beyond git. Runs anywhere Python and git are available.

**No API calls.** No GitLab/GitHub API tokens, no webhooks, no CI runners needed. Everything works through plain git tags and local filesystem operations.

**Configurable timeouts.** All git operations have a default 120-second timeout (configurable via `TOOLMANAGER_GIT_TIMEOUT` env var) so a stalled server cannot hang the process.

### When to use it

- You manage tools on a shared HPC cluster, lab server, or build farm
- You use Environment Modules (`module load`/`module avail`)
- Your tools come from a mix of git repos, network shares, and vendor installs
- You want version control over what's deployed and where
- You want reproducible tool environments across teams or projects

### When not to use it

- You need per-user package management 
- Your tools are all containers and not want to use module system
- You don't use Environment Modules

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

Configuration comes from two sources:

1. **`tools.json`** — `deploy_base_path` sets the default root for all deployments.
2. **`.release.conf`** — optional config file for release settings (branch, tag prefix, remote) and other overrides.

Config files use `KEY=VALUE` format. They are loaded in this order — later values win:

| Priority | Source |
|---|---|
| 1 (lowest) | `<repo>/.release.conf` — project-level settings |
| 2 | `--config FILE` — explicit path on the CLI |
| 3 (highest) | Environment variables |

### 1.3 Create tools.json

Create a `tools.json` file describing your tools. The path defaults to `tools.json` in the working directory; set `TOOLS_MANIFEST` in config or pass `--manifest FILE` to use a different location.

```json
{
  "deploy_base_path": "/opt/software",
  "app_root": "custom/apps",
  "tools": {
    "my-tool": {
      "version": "",
      "available": [],
      "source": {
        "type": "git",
        "url": "git@gitlab.com:group/my-tool.git"
      },
      "bootstrap": "bash install.sh",
      "install_path": "{{app_root}}/{{toolname}}/{{version}}",
      "mf_path": "modulefiles/{{toolname}}/{{version}}"
    },
    "another-tool": {
      "version": "",
      "available": [],
      "source": {
        "type": "archive",
        "path": "/nfs/share/another-tool"
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
        "my-tool": "1.0.0",
        "another-tool": "2.0.0",
        "matlab": "2024.1.0"
      }
    },
    "legacy-suite": ["my-tool", "another-tool"]
  }
}
```

#### Top-level fields

| Field | Default | Description |
|---|---|---|
| `deploy_base_path` | `"/"` | Default root directory for deployments and modulefiles. Overridden by `--deploy-path`. |
| `tools` | `{}` | Tool definitions (see below) |
| `toolsets` | `{}` | Named groupings of tools — legacy list format or dict format with version pins |
| *custom string keys* | — | Any additional string field at root level becomes a template variable (e.g. `"app_root": "custom/apps"` → `{{app_root}}`). Non-string values are ignored. |

#### Per-tool fields

| Field | Required | Type | Description |
|---|---|---|---|
| `source` | Yes | object | Source definition (see source types below) |
| `version` | No | string | Current deployed version — updated automatically on deploy |
| `available` | No | list | All available version strings — populated by `scan` |
| `install_path` | No | string | Custom deploy path. Relative paths resolve against `deploy_base_path`. Supports `{{toolname}}`, `{{version}}`, and any custom string variables defined at root or tool level. |
| `mf_path` | No | string | Custom modulefile path. Relative paths resolve against `deploy_base_path`. Supports the same placeholders as `install_path`. |
| `bootstrap` | No | string | Shell command to run after deploy via `sh -c`. Environment variables `INSTALL_PATH`, `TOOL_VERSION`, and `TOOL_NAME` are set automatically (see Part 4). |
| `flatten_archive` | No | boolean | For archive sources: flatten single-root directories after extraction (default: `true`) |
| *custom string keys* | No | string | Any additional string field at tool level becomes a template variable, overriding root-level variables of the same name. |

#### Source types

| Type | Required fields | Description |
|---|---|---|
| `git` | `url` | Clones a tag from any git remote |
| `archive` | `path` | Tools packaged as archives (.tar.gz, .zip, etc.) on a shared disk. Archives are extracted on deploy. |
| `external` | `path` | Tools already installed externally (e.g. by IT). No files are copied. Deploy/upgrade blocked unless `--force`. |

#### `archive` source layout

An `archive` source expects the tool's versions to already be arranged as semver subdirectories containing archive files:

```
/nfs/share/another-tool/
├── 2.0.0/
│   └── another-tool-2.0.0.tar.gz
├── 3.0.0/
│   └── another-tool-3.0.0.tar.gz
└── 3.1.0/
    └── another-tool-3.1.0.tar.gz
```

Archive files (`.tar.gz`, `.tar.bz2`, `.tar.xz`, `.tgz`, `.zip`) are extracted to `deploy_base_path/<tool>/<version>/` (or the custom `install_path`).

#### `external` source layout

An `external` source expects the tool's versions as semver subdirectories at the given path:

```
/opt/external/matlab/
├── 2024.1.0/
└── 2024.2.0/
```

No files are copied on deploy. The source type itself blocks deploy and upgrade unless `--force` is given. This replaces the old `"deploy": false` field.

#### Toolsets

Toolsets support two formats:

**Legacy list format** — a list of tool names. Uses each tool's current `version` field:

```json
"toolsets": {
  "science": ["tool-a", "tool-b"]
}
```

**Dict format** — explicit version pins per tool, plus a toolset version. Required for `apply`:

```json
"toolsets": {
  "science": {
    "version": "1.0.0",
    "tools": {
      "tool-a": "1.2.0",
      "tool-b": "2.0.0"
    }
  }
}
```

Both formats work with `toolset`. The dict format is required for `apply`.

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

`deploy.sh` has five subcommands:

```
deploy.sh deploy  <tool> [--version X.Y.Z]   Deploy a tool version
deploy.sh scan                                Check all tools for newer versions
deploy.sh upgrade <tool>                      Deploy the latest available version
deploy.sh toolset <name> [--version X.Y.Z]    Write a toolset modulefile (version from dict-format toolset if omitted)
deploy.sh apply   [--toolset <name>]           Deploy all versions referenced by toolsets
```

Global options available on all subcommands:

| Option | Description |
|---|---|
| `--deploy-path PATH` | Deploy base path (overrides manifest `deploy_base_path`) |
| `--manifest FILE` | Path to tools.json (default: `TOOLS_MANIFEST` or `./tools.json`) |
| `--mf-path PATH` | Override modulefile directory |
| `--config FILE` | Path to `.release.conf` |
| `--dry-run` | Show what would be done; make no changes |
| `--non-interactive`, `-n` | Auto-confirm all prompts |
| `--force` | Override deploy protection for externally managed tools |
| `--help`, `-h` | Show help (works at top level and per subcommand) |

### 3.1 deploy — Deploy a Specific Version

```bash
deploy.sh deploy my-tool --version 1.3.0 --deploy-path /opt/software
```

What happens:

1. Reads `tools.json` and finds the tool entry.
2. Resolves `deploy_base_path` from `--deploy-path` (CLI) or `deploy_base_path` (manifest).
3. Checks if the tool is externally managed (source type `"external"`); if so, blocks unless `--force` is given.
4. Validates the requested version exists (for git sources, checks that the tag is published before attempting a clone).
5. Resolves `install_path` and `mf_path` — relative paths are joined with `deploy_base_path`.
6. Asks for confirmation: `Deploy my-tool 1.3.0 (git) → /opt/software/my-tool/1.3.0?`
7. Clones the tag (git), extracts archives (archive), or validates the version directory (external). For archive sources, extracts them to the deploy target.
8. Runs the bootstrap command if configured.
9. Writes a modulefile.
10. Updates `version` in `tools.json`.

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
deploy_base_path/
├── my-tool/
│   ├── 1.2.0/               ← cloned tag (shallow, depth 1)
│   │   └── ...
│   └── 1.3.0/
└── mf/
    └── my-tool/
        ├── 1.2.0             ← generated modulefile
        └── 1.3.0
```

If `MF_BASE_PATH` is configured or `--mf-path` is given, modulefiles go there instead:

```
MF_BASE_PATH/
└── my-tool/
    ├── 1.2.0
    └── 1.3.0
```

```bash
# Examples
deploy.sh deploy my-tool --version 1.3.0 --deploy-path /opt/software
deploy.sh deploy my-tool                         # interactive version picker
deploy.sh deploy my-tool --version 1.3.0 -n      # non-interactive
deploy.sh deploy my-tool --version 1.3.0 --dry-run
deploy.sh deploy my-tool --version 1.3.0 --mf-path /opt/modulefiles
```

### 3.2 scan — Check for Updates

```bash
deploy.sh scan
```

Checks every tool in `tools.json` against its source, writes all discovered versions into the `available` field of each tool in the manifest, and prints an upgrade table:

```
  my-tool        1.2.0   →  1.3.0  (minor)
  another-tool   3.0.0   →  3.1.0  (patch)
  stable-tool    2.0.0   (up to date)
  matlab         2024.1  (up to date) (external)
  broken-tool    1.0.0   ⚠ error: Cannot list tags from git@...
```

Externally managed tools (source type `"external"`) are marked with `(external)` and excluded from the interactive upgrade prompt.

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

**Auto-discovery:** When archive or external sources are present, `scan` also checks sibling directories under their parent paths for tools not yet in the manifest and offers to add them.

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

Externally managed tools (source type `"external"`) are blocked unless `--force` is given.

```bash
deploy.sh upgrade my-tool
deploy.sh upgrade my-tool -n     # non-interactive
deploy.sh upgrade my-tool --dry-run
```

### 3.4 toolset — Write a Toolset Modulefile

```bash
deploy.sh toolset science --version 1.0.0
```

Reads the `"science"` toolset from `tools.json`, collects the current deployed version of each tool in the set, generates a modulefile that loads all of them, and writes it to `MF_BASE_PATH/science/1.0.0` (or `deploy_base_path/mf/science/1.0.0`).

`--version` is optional for dict-format toolsets that include a `"version"` key — the toolset version is read from the manifest automatically.

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

### 3.5 apply — Deploy All Toolset Versions

```bash
deploy.sh apply
deploy.sh apply --toolset science
```

Reads all dict-format toolsets from `tools.json`, collects every `(tool, version)` pair they reference, and deploys any that are not already present on disk. After deploying tools, writes toolset modulefiles for each processed toolset.

This is the "reconcile" step in a GitOps workflow:

```bash
# 1. Scan to populate available versions
deploy.sh scan -n

# 2. Edit tools.json — pick versions in toolsets
#    (manually edit the toolsets dict)

# 3. Apply — deploy everything that's missing
deploy.sh apply
```

Behavior:
- **Already deployed**: If the deploy directory already exists on disk, the tool+version is skipped.
- **Externally managed**: Tools with source type `"external"` are skipped with a warning (use `--force` to override).
- **Error handling**: If one tool fails, the rest continue. A summary is printed at the end.
- **Toolset modulefiles**: Written with `overwrite` — re-running apply updates them.
- **Legacy toolsets**: Apply rejects list-format toolsets; only dict format with version pins is accepted.

```bash
deploy.sh apply                          # all toolsets
deploy.sh apply --toolset science        # one toolset
deploy.sh apply --dry-run                # preview only
deploy.sh apply -n                       # non-interactive
deploy.sh apply --force                  # include externally managed tools
```

---

## Part 4 — Bootstrap Commands

After cloning a git source or extracting archive source archives, `deploy.sh` runs the `bootstrap` command defined in the tool entry:

```json
{
  "my-tool": {
    "version": "",
    "source": { "type": "git", "url": "..." },
    "bootstrap": "bash install.sh"
  }
}
```

The command runs with the deploy directory as its working directory and has these environment variables available:

| Variable | Value |
|---|---|
| `INSTALL_PATH` | Full path to the deploy directory |
| `TOOL_VERSION` | Version being deployed |
| `TOOL_NAME` | Tool name from the manifest |

If bootstrap fails, you are prompted to either remove the deployed directory or leave it in place for inspection.

Example bootstrap commands:

```json
"bootstrap": "bash install.sh"
"bootstrap": "python3 install.py"
"bootstrap": "pip install -r requirements.txt --prefix=$INSTALL_PATH"
"bootstrap": "make install PREFIX=$INSTALL_PATH"
```

Bootstrap is skipped for external sources (the tool is already installed). It is also skipped in `--dry-run` mode (logged only).

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

## Part 6 — Externally Managed Tools

Some tools are installed by an external IT department or package manager where you cannot (or should not) deploy normally. toolmanager supports tracking these tools in the manifest without accidentally deploying over them.

### 6.1 Marking a Tool as External

Use source type `"external"` on the tool entry:

```json
{
  "deploy_base_path": "/opt/software",
  "tools": {
    "matlab": {
      "version": "2024.1.0",
      "source": {
        "type": "external",
        "path": "/opt/external/matlab"
      }
    }
  }
}
```

The source type alone controls deploy protection — no separate `"deploy": false` field is needed (that field has been removed).

### 6.2 What Changes

| Command | Behavior |
|---|---|
| `scan` | Shows the tool with an `(external)` marker. Excludes it from the upgrade prompt. |
| `deploy` | Blocked with a clear error message. |
| `upgrade` | Blocked with a clear error message. |
| `toolset` | Works normally — can reference the tool's current version. |

### 6.3 Deploying with --force

When you need to deploy an external tool anyway — for instance into a container or an alternative path — use `--force`:

```bash
deploy.sh deploy matlab --version 2024.1.0 --force --deploy-path /local/export
```

### 6.4 Custom Install Path for External Tools

Combine source type `"external"` with a relative `install_path` to control where forced deployments go:

```json
{
  "deploy_base_path": "/",
  "tools": {
    "matlab": {
      "version": "2024.1.0",
      "source": {
        "type": "external",
        "path": "/opt/external/matlab"
      },
      "install_path": "opt/external/matlab/{{version}}"
    }
  }
}
```

With `--deploy-path /local/export --force`, matlab deploys to `/local/export/opt/external/matlab/2024.1.0`.

The `source.path` (where versions are scanned) and `deploy_base_path` (where deployments land) are completely independent. You can scan from IT's directory and deploy to your container mount.

### 6.5 Updating the Version Manually

When the external IT department deploys a new version, update `tools.json` by hand:

```json
"matlab": {
  "version": "2024.2.0",
  ...
}
```

Or run `scan` to see that a new version is available, then update the version field manually.

---

## Part 7 — Archive Sources

When an archive source version directory contains archive files, they are automatically extracted to the deploy target.

### Supported Formats

`.tar.gz`, `.tar.bz2`, `.tar.xz`, `.tgz`, `.zip`

### How It Works

```
/nfs/share/my-tool/
├── 1.0.0/
│   └── my-tool-1.0.0.tar.gz    ← archive detected
└── 2.0.0/
    └── my-tool-2.0.0.tar.gz
```

When deploying version `1.0.0`:

1. Archives in `/nfs/share/my-tool/1.0.0/` are found.
2. Extracted to a temporary directory.
3. If `flatten_archive` is `true` (default) and extraction produced a single root directory, its contents are moved up one level.
4. The result is moved to `deploy_base_path/my-tool/1.0.0/` (or `install_path`).

### Controlling Flattening

Some archives contain a single top-level directory (`my-tool-1.0.0/bin/...`). By default, this is flattened so the deploy directory contains `bin/...` directly. To disable:

```json
{
  "my-tool": {
    "flatten_archive": false,
    "source": { "type": "archive", "path": "/nfs/share/my-tool" }
  }
}
```

### Security

- Tar archives are extracted with Python's `data` filter (blocks symlink and device attacks).
- Zip archives reject entries with path traversal (`../` or absolute paths).

---

## Part 8 — Path Resolution

### deploy_base_path

The deploy base path determines the root for all deployments and modulefiles. It is resolved in this order:

| Priority | Source |
|---|---|
| 1 (lowest) | `deploy_base_path` in `tools.json` (default: `"/"`) |
| 2 (highest) | `--deploy-path` CLI flag |

### install_path and mf_path

These per-tool fields support two forms:

**Absolute paths** — used as-is:

```json
"install_path": "/opt/custom/my-tool/{{version}}"
```

**Relative paths** — joined with `deploy_base_path`:

```json
"install_path": "opt/custom/my-tool/{{version}}"
```

With `--deploy-path /local/export`, this resolves to `/local/export/opt/custom/my-tool/1.0.0`.

If a relative path cannot resolve to an absolute path (e.g. `deploy_base_path` is empty), the command exits with a clear error.

### Placeholders

Both `install_path` and `mf_path` support:

| Placeholder | Value |
|---|---|
| `{{toolname}}` | Tool name |
| `{{version}}` | Version being deployed |
| `{{<key>}}` | Any custom string variable defined at root or tool level in `tools.json` (e.g. `{{app_root}}`) |

Variables are resolved in priority order: root-level strings → tool-level strings → built-ins (`toolname`, `version`). Later values override earlier ones, so a tool-level variable beats a root-level variable of the same name.

---

## Part 9 — Complete Workflows

### 9.1 First-time Setup

```bash
# 1. Create tools.json
cat > tools.json << 'EOF'
{
  "deploy_base_path": "/opt/software",
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
deploy.sh scan --manifest tools.json

# 3. Deploy the latest
deploy.sh deploy my-tool --manifest tools.json
```

### 9.2 Release + Deploy a Single Tool

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

### 9.3 Routine Upgrade Scan

```bash
# Check everything, then interactively choose what to upgrade
deploy.sh scan

# Upgrade a specific tool to latest
deploy.sh upgrade my-tool

# Upgrade all tools non-interactively (CI/CD)
deploy.sh scan -n              # report only, or:
deploy.sh upgrade my-tool -n
```

### 9.4 Create a Toolset Modulefile

A toolset modulefile loads a named collection of tools with a single `module load` command. The version you pass to `toolset` is the version of the *toolset itself* — it does not need to match any individual tool version.

#### Step 1 — Define the toolset in tools.json

Add a `"toolsets"` entry listing the tool names that belong to the set:

```json
{
  "deploy_base_path": "/opt/software",
  "tools": {
    "tool-a": {
      "version": "1.2.0",
      "source": { "type": "git", "url": "git@gitlab.com:group/tool-a.git" }
    },
    "tool-b": {
      "version": "2.0.0",
      "source": { "type": "archive", "path": "/nfs/share/tool-b" }
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

The modulefile is written to `deploy_base_path/mf/science/1.0.0` (or `MF_BASE_PATH/science/1.0.0` if set). Default output:

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

### 9.5 Deploy an External Tool into a Container

```bash
# tools.json has matlab with source type "external" and install_path: "opt/external/matlab/{{version}}"

# Scan to see what versions IT has published
deploy.sh scan

# Deploy into the container's mount point
deploy.sh deploy matlab --version 2024.1.0 --force --deploy-path /local/export
# → installs to /local/export/opt/external/matlab/2024.1.0
```

### 9.6 Redeploy an Older Version

```bash
# Deploy directories and modulefiles from older versions coexist
deploy.sh deploy my-tool --version 1.2.0 -n

module load my-tool/1.2.0
```

Note: redeploying an older version updates `tools.json` to record it as the current version. Update it back manually or deploy the newer version again when ready.

### 9.7 CI/CD Integration

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

## Part 10 — Exit Codes

`deploy.sh` uses distinct exit codes so CI pipelines can distinguish failure types:

| Code | Category | Examples |
|---|---|---|
| `0` | Success | Deploy completed, scan finished, help printed |
| `1` | General error | Manifest validation failure, modulefile write error |
| `2` | Config / argument error | Missing `--deploy-path`, invalid `--version`, unknown subcommand |
| `3` | Source adapter error | Git clone failed, tag not found, `ls-remote` timed out |
| `4` | Deploy-time error | Lock contention, directory already exists, bootstrap failure |

`release.sh` uses exit code `1` for all errors.

---

## Part 11 — Config Reference

### All Config Keys

| Config Key | Env Variable | Default | Description |
|---|---|---|---|
| `DEFAULT_BRANCH` | `RELEASE_DEFAULT_BRANCH` | `main` | Branch that releases are tagged from |
| `TAG_PREFIX` | `RELEASE_TAG_PREFIX` | `v` | Prefix added to version tags (`v` → `v1.2.3`) |
| `REMOTE` | `RELEASE_REMOTE` | `origin` | Git remote name |
| `TOOLS_MANIFEST` | `TOOLS_MANIFEST` | `./tools.json` | Path to the tools.json manifest |
| `MF_BASE_PATH` | `MF_BASE_PATH` | *(none)* | Override modulefile directory (e.g. separate NFS mount) |
| `MODULEFILE_TEMPLATE` | `MODULEFILE_TEMPLATE` | *(none)* | Path to a custom modulefile template file |

Environment variables are snapshotted at startup — config files cannot override them.

### Runtime Environment Variables

| Variable | Default | Description |
|---|---|---|
| `TOOLMANAGER_GIT_TIMEOUT` | `120` | Timeout in seconds for all git subprocess calls. Applies to `_run_git()`, `git ls-remote`, and `git clone`. Set higher on slow networks. |

### Annotated `.release.conf`

```ini
# .release.conf — site config for release.sh and deploy.sh

# Override git settings if your repo differs from defaults
# DEFAULT_BRANCH=main
# TAG_PREFIX=v
# REMOTE=origin

# Path to the tools.json manifest (default: ./tools.json)
# TOOLS_MANIFEST=/etc/toolmanager/tools.json

# Separate modulefile directory (e.g. NFS share)
# MF_BASE_PATH=/opt/modulefiles

# Custom modulefile template file
# MODULEFILE_TEMPLATE=/opt/templates/my-tool.tcl
```

---

## Part 12 — Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| `Must be on 'main' branch` | You are on a feature branch | `git checkout main` |
| `Working tree is dirty` | Uncommitted changes | `git stash` or commit |
| `Not in sync with remote` | Local ahead or behind | `git pull` or `git push` |
| `Tag 'v1.2.3' already exists` | Version already released | Choose a higher version |
| `deploy_base_path must be an absolute path` | Relative path resolved | Use a full path in `tools.json` or `--deploy-path` |
| `No deploy base path configured` | Neither manifest nor CLI set it | Add `deploy_base_path` to `tools.json` or pass `--deploy-path` |
| `Tool 'x' is externally managed` | Source type is `"external"` | Use `--force` to override, or change the source type in `tools.json` |
| `Resolved install_path is not absolute` | Relative `install_path` with empty `deploy_base_path` | Set `deploy_base_path` in `tools.json` or pass `--deploy-path` |
| Tool deploys but should be blocked | Wrong source type | Change source type to `"external"` to block deploy/upgrade |
| `Version 1.3.0 is not a published tag` | Tag not pushed yet | Run `release.sh` first, or `git push --tags` |
| `Deploy directory already exists` | Already deployed this version | `rm -rf deploy_base_path/tool/version` to reinstall |
| `Modulefile already exists` | Already deployed this version | `rm mf_path/tool/version` to regenerate |
| `Bootstrap failed` | Bootstrap command exited non-zero | Check the command; prompted to clean up the deploy |
| `Another deploy may be in progress (held by PID ...)` | File lock contention | Wait for the other deploy to finish, or remove the stale `.deploy.lock` file |
| `Timed out listing tags` / `Timed out cloning` | Git server slow or unreachable | Increase `TOOLMANAGER_GIT_TIMEOUT` env var (default: 120s) |
| `deploy_base_path is not writable` | Directory exists but no write permission | Fix permissions or use `--deploy-path` to point to a writable location |
| `Resolved path template contains '..'` | Path traversal in `install_path`/`mf_path` template | Remove `..` components from template variables |
| `No versions available` | Source has no semver tags / source path empty | Push a release tag, or check `source.path` |
| `tools.json not found` | Manifest path wrong | Pass `--manifest FILE` or set `TOOLS_MANIFEST` |
| `Tool has no deployed version recorded` | `version` is empty in tools.json | Run `deploy.sh deploy <tool>` first |
| Tool name contains `/` | Manifest has a path-traversal name | Fix the tool name in tools.json |
