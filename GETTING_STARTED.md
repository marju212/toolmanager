# Getting Started

This guide walks through setting up and using the tool manager from scratch.

## Prerequisites

- Python 3.12+
- git 2.x+
- A GitLab personal access token with `api` scope

## 1. Clone and Set Up

```bash
git clone <this-repo-url> toolmanager
cd toolmanager
```

No dependencies to install — the project uses Python standard library only.

## 2. Configure Your GitLab Token

The token is needed for merge request creation, project detection, and default branch updates.

Pick one method:

```bash
# Option A: environment variable (recommended for CI/CD)
export GITLAB_TOKEN=glpat-xxxxxxxxxxxxxxxxxxxx

# Option B: plain-text file
echo "glpat-xxxxxxxxxxxxxxxxxxxx" > ~/.gitlab_token
chmod 600 ~/.gitlab_token

# Option C: config file (see step 3)
```

For self-hosted GitLab, also set:

```bash
export GITLAB_API_URL=https://gitlab.example.com/api/v4
```

## 3. Create a Config File (Optional)

Config files use `KEY=VALUE` format. The tool loads them in order — later values override earlier ones:

1. `~/.release.conf` — user-level defaults
2. `<repo>/.release.conf` — per-repository overrides
3. `--config FILE` — explicit path on the command line

Environment variables always take highest priority.

**Example `~/.release.conf`:**

```bash
GITLAB_TOKEN=glpat-xxxxxxxxxxxxxxxxxxxx
GITLAB_API_URL=https://gitlab.com/api/v4
DEFAULT_BRANCH=main
TAG_PREFIX=v
REMOTE=origin
DEPLOY_BASE_PATH=/opt/software
```

Protect the file if it contains your token:

```bash
chmod 600 ~/.release.conf
```

**Full config reference:**

| Config Key | Env Variable | Default | Purpose |
|---|---|---|---|
| `GITLAB_TOKEN` | `GITLAB_TOKEN` | *(none)* | GitLab API token |
| `GITLAB_API_URL` | `GITLAB_API_URL` | `https://gitlab.com/api/v4` | API base URL |
| `DEFAULT_BRANCH` | `RELEASE_DEFAULT_BRANCH` | `main` | Branch to release from |
| `TAG_PREFIX` | `RELEASE_TAG_PREFIX` | `v` | Tag prefix (e.g. `v1.2.3`) |
| `REMOTE` | `RELEASE_REMOTE` | `origin` | Git remote name |
| `VERIFY_SSL` | `GITLAB_VERIFY_SSL` | `true` | SSL verification |
| `UPDATE_DEFAULT_BRANCH` | `RELEASE_UPDATE_DEFAULT_BRANCH` | `true` | Update GitLab default branch on release |
| `DEPLOY_BASE_PATH` | `DEPLOY_BASE_PATH` | *(none)* | Where tools get deployed |
| `MODULEFILE_TEMPLATE` | `MODULEFILE_TEMPLATE` | *(none)* | Custom modulefile template path |
| `BUNDLE_SUBMODULE_DIR` | `BUNDLE_SUBMODULE_DIR` | *(repo root)* | Subdirectory containing submodules |
| `BUNDLE_NAME` | `BUNDLE_NAME` | *(auto from remote)* | Override bundle name |

## 4. Release a Tool

Navigate to the git repository of the tool you want to release.

### Validate first

```bash
./scripts/release.sh --dry-run
```

This runs every check (branch, clean tree, remote sync, token) without creating anything.

### Interactive release

```bash
./scripts/release.sh
```

You'll see a menu:

```
What would you like to do?

  1) Release        Create release branch + tag
  2) Hotfix MR      Create MR from a release branch to main

Select an option [1-2]:
```

Choosing **Release** prompts for a version bump:

```
Current version: 1.0.0

  1) Patch  → 1.0.1
  2) Minor  → 1.1.0
  3) Major  → 2.0.0
  4) Custom
```

### Non-interactive release (CI/CD)

```bash
./scripts/release.sh --version 1.2.3 --non-interactive
```

### What happens

1. Validates repo state (correct branch, clean tree, synced with remote)
2. Generates a changelog from commits since the last tag
3. Creates release branch `release/v1.2.3` and annotated tag `v1.2.3`
4. Pushes both to remote
5. Optionally updates the GitLab default branch

If anything fails after pushing, partial artifacts are automatically cleaned up.

## 5. Deploy a Tool

Deploy clones a tagged release into a structured directory and generates a modulefile.

```bash
./scripts/deploy.sh --version 1.2.3 --deploy-path /opt/software
```

### What happens

1. Validates tag `v1.2.3` exists
2. Shallow-clones into `/opt/software/tool-name/1.2.3/`
3. Runs bootstrap if present (`install.sh` first, then `install.py`)
4. Generates a TCL modulefile at `/opt/software/mf/tool-name/1.2.3`

### Resulting directory structure

```
/opt/software/
├── tool-name/
│   └── 1.2.3/                 # cloned repo at tag
│       ├── bin/
│       ├── install.sh          # ran during deploy (if present)
│       └── modulefile.tcl      # custom template (if present)
└── mf/
    └── tool-name/
        └── 1.2.3              # generated modulefile
```

### Bootstrap scripts

If your tool needs a build step, add one of these to the repo root:

- `install.sh` — runs via `bash` (takes priority)
- `install.py` — runs via `python3`

Deploy runs whichever it finds first. If the script fails, the cloned directory is removed.

### Custom modulefiles

The modulefile template is resolved in this order:

1. **Previous version** — if a modulefile for an earlier version exists, it's copied and version references are updated
2. **Repo template** — `modulefile.tcl` in the repository root
3. **Config template** — path set via `MODULEFILE_TEMPLATE`
4. **Default** — hardcoded template that prepends `$root/bin` to `PATH`

Custom templates support these placeholders:

| Placeholder | Value |
|---|---|
| `%VERSION%` | Version being deployed |
| `%ROOT%` | Full path to the deployed tool |
| `%TOOL_NAME%` | Tool name |
| `%DEPLOY_BASE_PATH%` | Base deploy path |

## 6. Bundle Multiple Tools

A bundle is a parent repo with git submodules pointing at individual tool repos. The bundle tool creates a coordinated release and a parent modulefile that loads all tools.

### Set up a bundle repo

```bash
mkdir my-toolset && cd my-toolset
git init

# Add tools as submodules
git submodule add <tool-a-repo-url> tools/tool-a
git submodule add <tool-b-repo-url> tools/tool-b

# Pin each submodule to a tagged version
cd tools/tool-a && git checkout v1.2.0 && cd ../..
cd tools/tool-b && git checkout v2.0.0 && cd ../..

git add -A && git commit -m "Pin tools to release versions"
```

### Create a bundle release

```bash
./scripts/bundle.sh --version 1.0.0 --deploy-path /opt/software --submodule-dir tools -n
```

This will:

1. Detect submodules and verify each is pinned to a tag
2. Display a manifest:
   ```
   ─── Bundle Manifest ───────────────────────────
     tool-a  v1.2.0  (tools/tool-a)
     tool-b  v2.0.0  (tools/tool-b)
   ────────────────────────────────────────────────
   ```
3. Create release branch + tag for the bundle
4. Generate a parent modulefile at `/opt/software/mf/my-toolset/1.0.0`

### Deploy an existing bundle

If the bundle tag already exists and you just need the modulefile:

```bash
./scripts/bundle.sh --deploy-only --version 1.0.0 --deploy-path /opt/software -n
```

### Bundle modulefile

The generated parent modulefile loads all tools:

```tcl
#%Module1.0
##
## my-toolset/1.0.0 modulefile
##

proc ModulesHelp { } {
    puts stderr "my-toolset version 1.0.0"
}

module-whatis "my-toolset version 1.0.0"

conflict my-toolset

module load tool-a/1.2.0
module load tool-b/2.0.0
```

Custom bundle templates support additional placeholders:

| Placeholder | Value |
|---|---|
| `%TOOL_LOADS%` | Auto-generated `module load` lines |
| `%tool-a%` | Version of tool-a (per-tool placeholder) |
| `%tool-b%` | Version of tool-b (per-tool placeholder) |

## 7. Hotfix Workflow

After a release, if you need to push a fix to the release branch and merge it back to main:

```bash
# Fix on the release branch
git checkout release/v1.2.3
# ... make your fix ...
git add -A && git commit -m "Fix critical bug"
git push

# Create a merge request back to main
./scripts/release.sh --hotfix-mr release/v1.2.3
```

Or cherry-pick an existing commit:

```bash
git checkout release/v1.2.3
git cherry-pick <commit-sha>
git push

./scripts/release.sh --hotfix-mr release/v1.2.3
```

## Typical End-to-End Workflow

```bash
# 1. Release a tool
cd tool-repo
./scripts/release.sh --version 1.2.3 -n

# 2. Deploy it
./scripts/deploy.sh --version 1.2.3 --deploy-path /opt/software -n

# 3. Update the bundle
cd toolset-repo
cd tools/tool-repo && git fetch && git checkout v1.2.3 && cd ../..
git add -A && git commit -m "Bump tool-repo to 1.2.3"

# 4. Release the bundle
./scripts/bundle.sh --version 2.0.0 --deploy-path /opt/software -n
```

Users can then load the tools via environment modules:

```bash
module load my-toolset/2.0.0    # loads all tools at their pinned versions
# or individually:
module load tool-repo/1.2.3
```

## CI/CD Integration

All three scripts support `--non-interactive` (`-n`) for automated pipelines. Set `GITLAB_TOKEN` as a masked CI/CD variable.

```bash
# In a CI job
./scripts/release.sh --version $RELEASE_VERSION -n
./scripts/deploy.sh --version $RELEASE_VERSION --deploy-path /opt/software -n
```

A sample pipeline is provided at [`examples/gitlab-ci-release.yml`](examples/gitlab-ci-release.yml).

## Running Tests

```bash
# All tests
python3 -m unittest discover tests/ -p "test_*.py"

# Verbose
python3 -m unittest discover tests/ -p "test_*.py" -v

# Single file
python3 -m unittest tests/test_release.py
```

## Troubleshooting

| Problem | Fix |
|---|---|
| `GITLAB_TOKEN is not set` | Set the token via env var, config file, or `~/.gitlab_token` |
| `Branch must be on main` | Check out the default branch before releasing |
| `Working tree is dirty` | Commit or stash your changes |
| `Not in sync with remote` | `git pull && git push` |
| `Tag already exists` | The version was already released — pick a new version |
| `Submodule not pinned to a tag` | `cd` into the submodule and `git checkout <tag>` |
| Deploy bootstrap fails | Check `install.sh` / `install.py` for errors; the cloned dir is cleaned up automatically |
| SSL errors with self-hosted GitLab | Set `VERIFY_SSL=false` in config or `GITLAB_VERIFY_SSL=false` in env |
