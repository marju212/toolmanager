# Example Workspace

A self-contained demo environment for toolmanager's manifest-driven
deploy workflow. Demonstrates how a central manifest (`tools.json`)
combined with Environment Modules drives repeatable, version-controlled
software deployment — locally, on shared infrastructure, or in CI/CD
pipelines.

Uses two local git repos as tool sources. No network access required.

## Why This Approach

Traditional software deployment on shared systems (HPC clusters, dev
servers, build farms) often relies on ad-hoc scripts, manual `rsync`
commands, or custom Ansible plays per tool. This leads to:

- No single source of truth for what is deployed and at which version
- Drift between environments (test vs. production)
- Modulefiles maintained by hand, out of sync with actual installs
- No audit trail of what changed and when

toolmanager solves this with a **manifest-driven** model:

1. **`tools.json`** is the single source of truth — every tool, its
   source, and its currently deployed version are tracked in one file.
2. **Deploys are reproducible** — the same manifest + version produces
   the same result on any machine.
3. **Modulefiles are generated automatically** — from repo templates or
   defaults, always in sync with the installed version.
4. **Toolsets** group related tools into a single `module load` command.
5. **Everything is scriptable** — `--non-interactive` and `--dry-run`
   flags make CI/CD integration straightforward.

## Concepts

### Manifest (`tools.json`)

The manifest defines all managed tools, their sources, and deployment
metadata:

```json
{
  "deploy_base_path": "/opt/software",
  "tools": {
    "hello-cli": {
      "source": { "type": "git", "url": "git@example.com:tools/hello-cli.git" },
      "bootstrap": "./install.sh",
      "version": "1.0.0"
    },
    "calculator": {
      "source": { "type": "git", "url": "git@example.com:tools/calculator.git" },
      "version": "1.0.0"
    }
  },
  "toolsets": {
    "demo-suite": ["hello-cli", "calculator"]
  }
}
```

Key fields per tool:

| Field | Required | Description |
|-------|----------|-------------|
| `source` | Yes | `{"type": "git", "url": "..."}`, `{"type": "archive", "path": "..."}`, or `{"type": "external", "path": "..."}` |
| `version` | No | Current deployed version (auto-updated on deploy) |
| `available` | No | List of available versions (populated by `scan`) |
| `bootstrap` | No | Shell command to run after deploy (receives env vars, see below) |
| `install_path` | No | Custom install path (supports `%tool%` and `%version%` placeholders) |
| `mf_path` | No | Custom modulefile path (supports `%tool%` and `%version%` placeholders) |
| `flatten_archive` | No | For archive sources: flatten single-root dirs after extraction (default: `true`) |

### Source Types

**Git** — clones a semver tag from any git remote (local, SSH, HTTPS):

```json
{ "type": "git", "url": "git@example.com:group/tool.git" }
```

Tags are expected in the format `v1.2.3` (prefix configurable via
`TAG_PREFIX` in config). `deploy.sh` uses `git ls-remote --tags` to
discover available versions and `git clone --depth 1 --branch <tag>` to
deploy.

**Archive** — tool distributed as archives on a shared disk:

```json
{ "type": "archive", "path": "/nfs/share/tool" }
```

Expected layout: `/nfs/share/tool/1.0.0/tool.tar.gz`, etc.
Archives (`.tar.gz`, `.tar.bz2`, `.tar.xz`, `.tgz`, `.zip`) are
extracted to the deploy directory automatically.

**External** — tool already installed by an external system (e.g. IT):

```json
{ "type": "external", "path": "/opt/vendor/tool" }
```

Expected layout: `/opt/vendor/tool/1.0.0/`, `/opt/vendor/tool/2.0.0/`.
No files are copied — only a modulefile is written. Deploy and upgrade
are blocked unless `--force` is given.

### Bootstrap

A shell command that runs after the tool is installed. The working
directory is the deploy directory, and these environment variables are
set:

| Variable | Value |
|----------|-------|
| `INSTALL_PATH` | Absolute path to the deployed version directory |
| `TOOL_VERSION` | The version being deployed (e.g. `1.0.0`) |
| `TOOL_NAME` | The tool name from the manifest |

Example bootstrap script (`install.sh`):

```bash
#!/usr/bin/env bash
chmod +x "${INSTALL_PATH}/bin/hello"
```

### Modulefiles

[Environment Modules](https://modules.readthedocs.io/) files are
generated automatically on each deploy. The template is resolved in this
priority order:

1. **Previous version** — if a modulefile for an earlier version exists,
   it is copied and version references are updated.
2. **Repo template** — if the deployed repo contains `modulefile.tcl`,
   it is used with placeholder substitution.
3. **Config template** — if `MODULEFILE_TEMPLATE` is set in
   `.release.conf`, that file is used.
4. **Default template** — a built-in template that prepends `bin/` to
   `PATH`.

Available placeholders in templates:

| Placeholder | Value |
|-------------|-------|
| `%VERSION%` | Version being deployed |
| `%ROOT%` | Deploy directory for this version |
| `%TOOL_NAME%` | Tool name |
| `%DEPLOY_BASE_PATH%` | Deploy base path |

Example custom `modulefile.tcl`:

```tcl
#%Module1.0
proc ModulesHelp { } {
    puts stderr "hello-cli version %VERSION%"
    puts stderr "A friendly greeting tool"
}
module-whatis "hello-cli version %VERSION%"
conflict hello-cli
set root %ROOT%
prepend-path PATH $root/bin
```

### Toolsets

A toolset groups multiple tools into a single modulefile. When users
load the toolset, all member tools are loaded at their currently
deployed versions:

```json
{
  "toolsets": {
    "demo-suite": ["hello-cli", "calculator"]
  }
}
```

The generated modulefile contains `module load <tool>/<version>` for
each member. Additional placeholders are available in toolset templates:

| Placeholder | Value |
|-------------|-------|
| `%TOOL_LOADS%` | Auto-generated `module load` block |
| `%<tool-name>%` | Per-tool version (e.g. `%hello-cli%` becomes `2.0.0`) |

### Configuration (`.release.conf`)

Key/value config file loaded in priority order (lowest to highest):

1. `~/.release.conf` (user-level)
2. `<repo>/.release.conf` (repo-level)
3. `--config FILE` (CLI flag)
4. Environment variables (highest priority)

| Config Key | Env Variable | Default | Description |
|------------|--------------|---------|-------------|
| `DEFAULT_BRANCH` | `RELEASE_DEFAULT_BRANCH` | `main` | Branch for release tagging |
| `TAG_PREFIX` | `RELEASE_TAG_PREFIX` | `v` | Tag prefix (`v` means `v1.2.3`) |
| `REMOTE` | `RELEASE_REMOTE` | `origin` | Git remote name |
| `MF_BASE_PATH` | `MF_BASE_PATH` | — | Modulefile output directory |
| `MODULEFILE_TEMPLATE` | `MODULEFILE_TEMPLATE` | — | Path to custom modulefile template |
| `TOOLS_MANIFEST` | `TOOLS_MANIFEST` | — | Default path to `tools.json` |

## What's Included

```
examples/workspace/
├── manifest/
│   ├── tools.json              # Deploy manifest (tools, sources, toolsets)
│   └── .release.conf           # Config (tag prefix, modulefile path, etc.)
├── tool-repos/
│   ├── hello-cli/              # Example tool #1: Bash CLI
│   │   ├── bin/hello           # The tool itself
│   │   ├── modulefile.tcl      # Custom modulefile template
│   │   └── install.sh          # Bootstrap script
│   └── calculator/             # Example tool #2: Python library
│       ├── bin/calc            # CLI wrapper
│       └── lib/calculator.py   # The library
├── setup.sh                    # Initialise repos, resolve paths, deploy v1.0.0
├── teardown.sh                 # Remove generated artifacts, restore templates
├── README.md                   # This file
└── .gitignore                  # Ignores generated dirs
```

After `./setup.sh`, these directories are created (gitignored):

```
├── repos/                      # Bare git repos (simulating remote origins)
│   ├── hello-cli.git           # Tags: v1.0.0, v1.1.0, v2.0.0
│   └── calculator.git          # Tags: v1.0.0, v1.0.1, v1.1.0
├── deploy/                     # Tool installations
│   ├── hello-cli/1.0.0/        # Deployed by setup.sh
│   └── calculator/1.0.0/       # Deployed by setup.sh
└── modulefiles/                # Environment Modules files
    ├── hello-cli/1.0.0         # From repo's modulefile.tcl
    └── calculator/1.0.0        # From default template
```

## Prerequisites

- Python 3.12+
- Git
- Bash

## Quick Start

```bash
cd examples/workspace

# Initialise: creates repos, resolves paths, deploys v1.0.0 of each tool
./setup.sh

# Both tools are deployed and ready to use
deploy/hello-cli/1.0.0/bin/hello          # => Hello, World!
deploy/calculator/1.0.0/bin/calc add 2 3  # => 5

# Modulefiles are already generated
ls modulefiles/hello-cli/
ls modulefiles/calculator/
```

## Subcommands Walkthrough

All commands assume you are in the `examples/workspace/` directory.

For brevity, set these shell variables:

```bash
DEPLOY="../../scripts/deploy.sh"
OPTS="--manifest manifest/tools.json --config manifest/.release.conf -n"
```

### scan — Check All Tools for Updates

```bash
$DEPLOY scan $OPTS
```

Compares each tool's deployed version against available versions from
its source. Output:

```
  calculator  1.0.0  ->  1.1.0  (minor)
  hello-cli   1.0.0  ->  2.0.0  (major)
```

Upgrade types: `new` (no version deployed), `patch`, `minor`, `major`,
`up to date`, `ahead` (deployed version is newer than source).

### deploy — Deploy a Specific Version

```bash
$DEPLOY deploy hello-cli --version 1.1.0 $OPTS
```

What happens step by step:

1. Validates the version tag exists in the source repo
2. Clones the tag into `deploy/hello-cli/1.1.0/`
3. Runs the bootstrap command (`./install.sh`) if configured
4. Generates a modulefile at `modulefiles/hello-cli/1.1.0`
5. Updates `version` in `manifest/tools.json` to `"1.1.0"`

Deploy calculator (no bootstrap, uses default modulefile):

```bash
$DEPLOY deploy calculator --version 1.1.0 $OPTS
```

### upgrade — Deploy the Latest Version

```bash
$DEPLOY upgrade hello-cli $OPTS
```

Queries the source for available versions, compares with the currently
deployed version, and deploys the latest if it is newer. If already
up-to-date, exits successfully with no changes.

### toolset — Create a Combined Modulefile

```bash
$DEPLOY toolset demo-suite --version 1.0.0 $OPTS
```

Generates `modulefiles/demo-suite/1.0.0` that loads all member tools
at their currently deployed versions:

```tcl
module load calculator/1.1.0
module load hello-cli/2.0.0
```

Users can then load everything with a single command:

```bash
module load demo-suite/1.0.0
```

### --dry-run — Preview Without Changes

Append `--dry-run` to any command to see what would happen without
making any changes:

```bash
$DEPLOY deploy calculator --version 1.1.0 $OPTS --dry-run
$DEPLOY upgrade hello-cli $OPTS --dry-run
$DEPLOY toolset demo-suite --version 2.0.0 $OPTS --dry-run
```

## Global CLI Options

| Option | Description |
|--------|-------------|
| `--manifest FILE` | Path to `tools.json` (default: `tools.json` in cwd) |
| `--config FILE` | Path to `.release.conf` config file |
| `--deploy-path PATH` | Override `deploy_base_path` from manifest |
| `--mf-path PATH` | Override modulefile base path from config |
| `--dry-run` | Show what would happen, make no changes |
| `--non-interactive`, `-n` | Auto-confirm all prompts |
| `--force` | Override protection for externally managed tools |
| `--help`, `-h` | Show help |

## Directory Layout After Full Walkthrough

After deploying both tools, upgrading, and creating a toolset:

```
deploy/                                # tool installations
├── hello-cli/
│   ├── 1.0.0/                         # initial deploy
│   │   ├── bin/hello
│   │   ├── install.sh
│   │   └── modulefile.tcl
│   ├── 1.1.0/                         # deployed specific version
│   └── 2.0.0/                         # upgraded to latest
│       ├── bin/hello
│       ├── install.sh
│       └── modulefile.tcl
├── calculator/
│   ├── 1.0.0/                         # initial deploy
│   │   ├── bin/calc
│   │   └── lib/calculator.py
│   └── 1.1.0/                         # upgraded to latest
│       ├── bin/calc
│       └── lib/calculator.py

modulefiles/                           # Environment Modules files
├── hello-cli/
│   ├── 1.0.0                          # from repo's modulefile.tcl
│   ├── 1.1.0                          # copied from 1.0.0, version updated
│   └── 2.0.0                          # copied from 1.1.0, version updated
├── calculator/
│   ├── 1.0.0                          # from default template
│   └── 1.1.0                          # copied from 1.0.0, version updated
└── demo-suite/
    └── 1.0.0                          # toolset: loads calculator + hello-cli
```

Note how tool installations and modulefiles are kept in **separate
directory trees**. This mirrors production setups where software lives
on one filesystem (e.g. `/opt/software/`) and modulefiles on another
(e.g. `/opt/modulefiles/`). The separation is controlled by
`MF_BASE_PATH` in `.release.conf`.

## Example Tools

### hello-cli

A Bash CLI tool demonstrating:
- **Bootstrap** (`./install.sh`) — post-deploy setup (sets permissions)
- **Custom modulefile** (`modulefile.tcl`) — with `%VERSION%` and
  `%ROOT%` placeholders

| Version | Changes |
|---------|---------|
| v1.0.0 | Basic "Hello, World!" output |
| v1.1.0 | Add `--name` flag |
| v2.0.0 | Add `--greeting` flag (breaking change) |

### calculator

A Python library + CLI wrapper demonstrating:
- **No bootstrap** — simpler deploy path
- **Default modulefile** — auto-generated from built-in template

| Version | Changes |
|---------|---------|
| v1.0.0 | Add and subtract operations |
| v1.0.1 | Input validation fix |
| v1.1.0 | Add multiply and divide operations |

## Automation and CI/CD

The `--non-interactive` (`-n`) and `--dry-run` flags make toolmanager
suitable for automated pipelines. All commands use exit codes for
success/failure and produce structured output.

### Nightly Upgrade Check

```bash
#!/usr/bin/env bash
# cron: 0 2 * * *
deploy.sh scan \
    --manifest /etc/toolmanager/tools.json \
    --config /etc/toolmanager/.release.conf -n
```

### Automated Upgrade Pipeline

```bash
#!/usr/bin/env bash
# Upgrade all tools to latest, then regenerate toolset modulefiles
OPTS="--manifest /etc/toolmanager/tools.json --config /etc/toolmanager/.release.conf -n"

for tool in $(jq -r '.tools | keys[]' /etc/toolmanager/tools.json); do
    deploy.sh upgrade "$tool" $OPTS
done

for ts in $(jq -r '.toolsets | keys[]' /etc/toolmanager/tools.json); do
    VERSION=$(date +%Y.%m.%d)
    deploy.sh toolset "$ts" --version "$VERSION" $OPTS
done
```

### Dry-Run in CI (Pull Request Check)

```bash
#!/usr/bin/env bash
# Run in CI to validate that a manifest change is deployable
deploy.sh scan --manifest tools.json --config .release.conf -n --dry-run
```

### GitOps Workflow

1. `tools.json` lives in a Git repo (the "manifest repo")
2. A developer opens a PR to add/update a tool entry
3. CI runs `deploy.sh scan --dry-run` to validate the change
4. After merge, a pipeline runs `deploy.sh deploy` or `deploy.sh upgrade`
5. The pipeline commits the updated `version` field back to the repo
6. Full audit trail via `git log` on the manifest repo

### Key Properties for Automation

- **Idempotent** — deploying the same version twice is detected and
  skipped (deploy dir already exists)
- **Atomic version tracking** — `tools.json` is updated only after a
  successful deploy
- **Lock-based concurrency** — deploy acquires a per-tool file lock to
  prevent parallel deploys of the same tool
- **Externally managed tools** — `"deploy": false` allows tracking
  version info for tools deployed by other systems, without toolmanager
  touching them (use `--force` to override)

## Cleanup

```bash
./teardown.sh
```

Removes `repos/`, `deploy/`, and `modulefiles/`. Restores `tools.json`
and `.release.conf` to their template form with `__WORKSPACE__`
placeholders and no version fields. Idempotent — safe to run multiple
times.

## Extending This Workspace

### Add a Third Tool

1. Create source files under `tool-repos/<name>/`
2. Add an entry in `manifest/tools.json`:
   ```json
   "my-tool": {
     "source": { "type": "git", "url": "__WORKSPACE__/repos/my-tool.git" }
   }
   ```
3. Add repo creation + tagging logic in `setup.sh` (follow the
   existing `hello-cli` or `calculator` blocks)
4. Optionally add the tool to a toolset

### Use a Disk Source

Instead of cloning from git, point to a directory with version
subdirectories:

```json
"my-tool": {
  "source": { "type": "archive", "path": "/nfs/share/my-tool" }
}
```

Expected layout: `/nfs/share/my-tool/1.0.0/`, `1.1.0/`, etc. Archive
files in version directories are extracted automatically.

### Custom Install and Modulefile Paths

Override where tools are installed or modulefiles are written on a
per-tool basis:

```json
"my-tool": {
  "source": { "type": "git", "url": "..." },
  "install_path": "custom/my-tool/%version%",
  "mf_path": "custom_mf/my-tool/%version%"
}
```

Relative paths are resolved against `deploy_base_path`. Supported
placeholders: `%tool%`, `%version%`.

### Custom Modulefile Template

Point to a shared template in `.release.conf`:

```
MODULEFILE_TEMPLATE=/opt/templates/standard.tcl
```

Or place a `modulefile.tcl` in the tool's source repo for per-tool
customisation.

### Mark a Tool as Externally Managed

Track a tool in the manifest without toolmanager deploying it:

```json
"my-tool": {
  "source": { "type": "external", "path": "/opt/vendor/my-tool" },
  "version": "3.2.1"
}
```

`scan` still checks for updates, but `deploy` and `upgrade` are blocked
unless `--force` is used.
