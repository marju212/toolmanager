# PRD: toolman

## Overview

`toolman` is a CLI for bundle management of HPC/LMOD software toolsets. It
replaces `bundle.sh` and `deploy.sh` with a single coherent entry point and
a declarative JSON manifest for bundle composition.

**Two tools, two roles:**

| Tool | Audience | Repo context | Replaces |
|---|---|---|---|
| `release` | Tool developer | Single-tool git repo | `release.sh` (unchanged) |
| `toolman` | Bundle admin | Bundle repo | `bundle.sh` + `deploy.sh` |

`release` is not changed by this project. It continues to work exactly as
today. `toolman` is a new tool that operates exclusively in bundle repos.

---

## Problem Statement

The existing system has three scripts that must be run in sequence, in the right
directories, with manual coordination:

- A bundle of N tools requires N×2 + 1 commands across N+1 directories.
- Git submodules are used to pin tool versions — complex, fragile, and unable
  to express tools that don't live in git.
- There is no way to include externally-managed tools (tar archives, CI
  artifacts, system packages) in a bundle modulefile without hacking templates.
- Modulefile content lives on the deployed filesystem with no version-controlled
  source of truth — it cannot be reproduced on a new machine.

---

## Goals

1. Single entry point for bundle management (`toolman`).
2. Declarative bundle manifest (`bundle.json`) that replaces git submodules and
   supports multiple tool sources.
3. All deployment is bundle-driven — tool developers release, bundle admin deploys.
4. Bundle deploy handles all managed tools automatically (`--deploy-tools`).
5. External tools (not deployed by toolman) appear in bundle modulefiles.
6. Modulefile templates are version-controlled and reproducible.
7. **Any historical bundle version must be fully reproducible at any future
   point** — all tool sources must be persistent and permanently accessible.
8. `release` tool is not touched — it works well and serves a different audience.

## Technology

- **Python 3.12.3** — minimum and target version
- **stdlib only** — no external packages; all functionality uses the Python standard library

## Non-Goals

- Not a package manager or dependency resolver.
- Does not build tools from source.
- Does not manage LMOD itself.
- Does not replace or modify `release` / `release.sh` — that tool is out of scope.
- No backward compatibility with `bundle.sh` or `deploy.sh`.

---

## Core Concepts

### Managed tools
Tools that `toolman` deploys. Each has a **source** that determines how it is
acquired and extracted to the deploy directory. After acquisition, bootstrap
(`install.sh` / `install.py`) runs if present, then a modulefile is written.

### External tools
Tools that `toolman` never touches. Deployed by other means (sysadmin, OS,
vendor installer). `toolman` only emits `module load` lines for them in bundle
modulefiles.

### Bundle
A named collection of managed + external tools described in `bundle.json`.
The bundle repo is an ordinary git repo — no submodules. It tracks
`bundle.json`, modulefile templates, a `releases/` directory of frozen
release snapshots, and has its own version tags. The bundle modulefile is a
composition layer: `module load my-bundle/1.0.0` loads all tools in that
release.

**Release cadence:** The bundle has its own independent release cadence
controlled entirely by the bundle admin. Tool developers release their tools
whenever ready; the bundle admin decides when to pull new tool versions into
a bundle release. There is no automatic coupling between a tool release and
a bundle release. The admin edits `bundle.json` manually to bump tool
versions, then runs `toolman bundle` to release the new bundle.

### Release
Git operations only: create release branch, create annotated tag with
changelog, optionally update GitLab default branch. Handled by the
existing `release` tool (`release.sh → src/release.py`) — unchanged.
Applies to individual tool repos and to the bundle repo itself.

---

## Bundle Repo Layout

```
bundle-repo/
  bundle.json           ← manifest + config: name, config, managed tools, external tools
  releases/
    1.0.0.json          ← written once by toolman on release, never modified
    1.1.0.json
    1.2.0.json
  modulefiles/
    tool-a.tcl          ← per-tool modulefile templates
    tool-b.tcl
    bundle.tcl          ← template for the bundle composition modulefile
```

Everything is tracked in git. Credentials are never stored in any file —
always via environment variables.

---

## `bundle.json` Schema

The manifest file. Contains the bundle configuration, the working state for
the **next** release, and the external tool list. Tracked in git.
Toolman never writes to it except during `toolman manifest set` (deferred).

```json
{
  "name": "my-toolset",

  "config": {
    "gitlab_api_url":        "https://gitlab.self-hosted.com/api/v4",
    "tag_prefix":            "v",
    "remote":                "origin",
    "deploy_base_path":      "/opt/software",
    "mf_base_path":          "/opt/modulefiles",
    "verify_ssl":            false,
    "update_default_branch": true,
    "modulefile_template":   "modulefiles/bundle.tcl"
    // NEVER store credentials here — use environment variables:
    //   GITLAB_TOKEN, ARTIFACTORY_TOKEN
  },

  "managed": {

    "tool-a": {
      "source":     "gitlab",
      "remote":     "https://gitlab.com/myorg/tool-a.git",
      "version":    "1.1.0"
      // optional: "tag_prefix": "release-"  (overrides global tag_prefix for this tool)
      // optional: "modulefile": "{self}/modulefile.tcl"
      // optional: "modulefile": "{bundle}/modulefiles/tool-a.tcl"
    },

    "tool-b": {
      "source":     "disk",
      "path":       "/nfs/archives/tool-b/tool-b-{version}.tar.gz",
      "version":    "2.0.0",
      "modulefile": "modulefiles/tool-b.tcl"
      // {version} in path is substituted before use
      // modulefile path is bundle-repo-relative (no placeholder = bundle root)
    },

    "tool-c": {
      "source":     "gitlab-package",
      "project":    "https://gitlab.com/myorg/tool-c",
      "package":    "tool-c",
      "file":       "tool-c-{version}-linux-x86_64.tar.gz",
      "version":    "3.0.0"
      // optional: "modulefile": "modulefiles/tool-c.tcl"
      // auth: GITLAB_TOKEN (same as release flow)
    },

    "tool-d": {
      "source":     "artifactory",
      "url":        "https://artifacts.example.com/libs-release/tool-d/{version}/tool-d-{version}.tar.gz",
      "version":    "4.0.0"
      // optional: "modulefile": "{self}/modulefile.tcl"
      // auth: ARTIFACTORY_TOKEN env var or config key
    }

  },

  "external": {
    "python": "3.12",
    "gcc":    "11"
  }
}
```

> Note: JSON does not support comments. The `//` lines above are for
> documentation only and must be removed from real `bundle.json` files.

## `releases/{version}.json` Schema

Written by toolman on every `toolman bundle` run. Never edited by hand.
Each file is a frozen snapshot of exactly what was deployed in that release.
Listing `releases/` gives all available bundle versions without parsing JSON.

```json
{
  "version":  "1.0.0",
  "date":     "2024-01-15",
  "managed": {
    "tool-a": { "source": "gitlab",          "remote":  "https://gitlab.com/myorg/tool-a.git",           "version": "1.0.0" },
    "tool-b": { "source": "disk",            "path":    "/nfs/archives/tool-b/tool-b-{version}.tar.gz",  "version": "2.0.0" },
    "tool-c": { "source": "gitlab-package",  "project": "https://gitlab.com/myorg/tool-c",               "version": "3.0.0" },
    "tool-d": { "source": "artifactory",     "url":     "https://artifacts.example.com/.../tool-d-{version}.tar.gz", "version": "4.0.0" }
  },
  "external": { "python": "3.12", "gcc": "11" }
}
```

The snapshot includes the full source spec for each tool (not just the
version), so any release can be reproduced from the file alone — no
cross-referencing with `bundle.json` required.

**Filename convention**: files are named `{version}.json` using the bare
semver version without tag prefix — e.g. `1.0.0.json`, never `v1.0.0.json`.
This matches the `"version"` field inside the file and makes directory
listing unambiguous regardless of the configured `tag_prefix`.

---

## Source Types

### `gitlab`
Acquires via `git clone --branch {tag_prefix}{version} --depth 1 {remote}`.
The `release` action applies to this source type — toolman can create the
branch and tag in the tool's repo.

Required fields: `remote`, `version`

Optional field: `tag_prefix` — overrides the global `tag_prefix` from
`bundle.json` config for this tool only. Useful when tools in the same
bundle use different tag conventions (e.g. `v1.0.0` vs `release-1.0.0`).
Also used by `toolman check` to find the correct tags for this tool.

### `disk`
Acquires from a local or NFS path. `{version}` is substituted into `path`
before use. Supported archive formats (Python 3.12.3 stdlib only):

| Format | Aliases | Library |
|---|---|---|
| `.tar` | — | `tarfile` |
| `.tar.gz` | `.tgz` | `tarfile` |
| `.tar.bz2` | `.tbz2` | `tarfile` |
| `.tar.xz` | `.txz` | `tarfile` |
| `.zip` | — | `zipfile` |

`.tar.zst` is not supported in Python 3.12 stdlib — deferred until
required, at which point toolman will shell out to the system `tar`.

**Plain directories** (already-extracted tool trees on a shared filesystem)
are supported. When `path` points to a directory, toolman copies it
recursively into `{deploy_base_path}/{tool_name}/{version}/` using
`shutil.copytree`. Symlinks are not used — the deploy directory must be
self-contained so it remains valid even if the source directory moves or
is deleted.

**Archive extraction and stripping**: the archive is extracted into
`{deploy_base_path}/{tool_name}/{version}/`. Toolman auto-detects whether
the archive contains a single top-level directory (the common convention for
HPC tarballs, e.g. `tool-a-1.1.0/`) and strips it so the tool contents land
directly in the version directory. If the archive is flat (no top-level dir)
it is extracted as-is. The optional `"strip"` field overrides auto-detection:

```json
"tool-b": {
  "source": "disk",
  "path":   "/nfs/archives/tool-b/tool-b-{version}.tar.gz",
  "version": "2.0.0",
  "strip":  1
}
```

`"strip": 0` disables stripping entirely. `"strip": 1` always strips one
level regardless of archive layout. `"strip"` is ignored when `path` points
to a directory (copy is always flat into the version dir).

**Bootstrap**: after extraction, if `install.sh` or `install.py` exists in
the version directory, it is executed to complete the installation (see
**Bootstrap** below). This applies to all source types.

**Persistence requirement**: the archive must be stored permanently. `disk`
is appropriate for a managed archive store (e.g. a versioned NFS directory
that is never purged), not a temporary staging area. If the archive at
`path` is deleted, that bundle version can no longer be regenerated.
Toolman verifies the path exists at pre-flight but cannot enforce long-term
retention — this is an operational responsibility.

No release step. The archive is assumed to already exist at the path.

Required fields: `path`, `version`

### `gitlab-package` *(future)*

Downloads from the GitLab Generic Package Registry
(`/projects/:id/packages/generic/:name/:version/:file`). Persistent and
versioned — packages do not expire. Reuses `lib/gitlab_api.py` for
authentication (token) and SSL configuration.

The tool's build pipeline must publish the archive to the registry (not as
a CI artifact). Once published the URL is stable indefinitely.

No release step. The package must be published before the bundle is deployed.

Required fields: `project` (URL or numeric project ID), `package` (package
name), `file` (filename with optional `{version}` placeholder), `version`

```json
"tool-a": {
  "source":  "gitlab-package",
  "project": "https://gitlab.com/myorg/tool-a",
  "package": "tool-a",
  "file":    "tool-a-{version}-linux-x86_64.tar.gz",
  "version": "1.1.0"
}
```

### `artifactory` *(future)*

Downloads from JFrog Artifactory via its REST API. Persistent by design.
Credentials are supplied via `ARTIFACTORY_TOKEN` config key or env var —
same pattern as `GITLAB_TOKEN`, never stored in `bundle.json`.

The full artifact URL is specified in `bundle.json` with a `{version}`
placeholder. This makes the resolved URL fully reproducible from the
manifest alone.

No release step. The artifact must exist in Artifactory before the bundle
is deployed.

Required fields: `url` (full artifact URL with `{version}` placeholder),
`version`

```json
"tool-b": {
  "source":  "artifactory",
  "url":     "https://artifacts.example.com/libs-release/tool-b/{version}/tool-b-{version}.tar.gz",
  "version": "2.0.0"
}
```

---

## Bootstrap

After a tool is acquired (cloned, extracted, or downloaded) toolman looks
for a bootstrap script in the version directory and runs it if found.
Applies to **all source types**.

| File | Behaviour |
|---|---|
| `install.sh` | Executed as a shell script. Takes priority over `install.py` if both exist. |
| `install.py` | Executed with the system Python 3 interpreter. |
| Neither | Silently skipped — bootstrap is optional. |

The bootstrap script runs with the version directory as its working
directory. A non-zero exit code aborts the deploy with an error.

In `--dry-run` mode the bootstrap script is not executed but its presence
is reported.

---

## Source Adapter Pattern

All source types implement a common Python interface. Adding a new source
requires only writing one new class and registering it — no changes to the
dispatch logic, pre-flight validation loop, or deploy flow.

```python
# src/lib/sources.py

from abc import ABC, abstractmethod

class SourceAdapter(ABC):

    @abstractmethod
    def validate(self) -> None:
        """Pre-flight check. Raises SystemExit if source is unavailable."""

    @abstractmethod
    def acquire(self, deploy_dir: str, dry_run: bool = False) -> None:
        """Fetch and extract the tool into deploy_dir."""

    @classmethod
    @abstractmethod
    def from_spec(cls, spec: dict, version: str, config) -> "SourceAdapter":
        """Construct adapter from a bundle.json tool spec."""


class GitlabSource(SourceAdapter):
    """git clone --branch {tag}{version} --depth 1"""

class DiskSource(SourceAdapter):
    """Extract local/NFS archive; {version} substituted into path."""


# Registry — the only place to touch when adding a new source type
_ADAPTERS: dict[str, type[SourceAdapter]] = {
    "gitlab": GitlabSource,
    "disk":   DiskSource,
    # "gitlab-package": GitlabPackageSource,  # future — see below
    # "artifactory":    ArtifactorySource,    # future — see below
}

def get_adapter(spec: dict, version: str, config) -> SourceAdapter:
    source_type = spec.get("source")          # "source" is required — no default
    if source_type is None:
        log_error("Tool spec missing required field: 'source'")
        raise SystemExit(1)
    cls = _ADAPTERS.get(source_type)
    if cls is None:
        log_error(f"Unknown source type: '{source_type}'")
        raise SystemExit(1)
    return cls.from_spec(spec, version, config)
```

The deploy flow then becomes uniform across all source types:

```python
adapter = get_adapter(tool_spec, version, config)
adapter.validate()                  # pre-flight (source-specific)
adapter.acquire(deploy_dir, dry_run)# acquisition (source-specific)
run_bootstrap(deploy_dir, dry_run)  # shared for all sources
write_modulefile(...)               # shared for all sources
```

Bootstrap and modulefile writing are intentionally outside the adapter —
they are identical regardless of how the tool was acquired.

### Adapter contract: atomic deploy-dir state

`acquire()` must guarantee that the deploy directory is either **fully
extracted with the `.toolman-ok` sentinel written**, or **completely
absent**. There is no valid intermediate state.

Concretely: `acquire()` wraps the entire extraction in a try/except. On any
exception — including `OSError` (disk full, NFS disappearance, permission
denied), `tarfile.TarError`, `zipfile.BadZipFile`, `KeyboardInterrupt`, or
any other — the implementation must:

1. Call `shutil.rmtree(deploy_dir, ignore_errors=True)` to remove the
   partial directory.
2. Log a human-readable error describing what failed (e.g.
   `"Extraction failed for tool-a/1.1.0: [Errno 28] No space left on device"`).
3. Re-raise the exception (or raise `SystemExit(1)` if re-raising would
   produce an unhelpful traceback for the user).

This guarantee means reruns are always clean: the sentinel-absence detection
that triggers a redeploy will never encounter a partially-written directory
that confuses extraction.

Example skeleton:

```python
def acquire(self, deploy_dir: str, dry_run: bool = False) -> None:
    if dry_run:
        log_info(f"[dry-run] would extract {self.path} → {deploy_dir}")
        return
    os.makedirs(deploy_dir, exist_ok=True)
    try:
        self._extract(deploy_dir)
    except Exception as exc:
        log_error(f"Extraction failed for {deploy_dir}: {exc}")
        shutil.rmtree(deploy_dir, ignore_errors=True)
        raise SystemExit(1)
```

### Adding a new source type

1. Create a class in `src/lib/sources.py` implementing `SourceAdapter`
2. Add it to `_ADAPTERS`
3. Document required fields in `bundle.json` schema

No other files need to change.

### Release and the adapter

The `release` action (git branch + tag) is **not** part of the adapter
interface. It only applies to `gitlab`-sourced tools and is handled
separately by `run_release_flow()`. The adapter is purely an acquisition
concern.

---

### Future: persistent registry sources

The following source types are planned but not in scope for the initial
implementation. The `disk` source covers the interim workflow.

All future sources must satisfy the reproducibility requirement: archives
are stored permanently and retrievable by exact version indefinitely.

Both follow the same pattern as `disk`:
acquire archive → extract to deploy dir → bootstrap → write modulefile.

#### `gitlab-package`

Downloads from the GitLab Package Registry
(`/projects/:id/packages/generic/:name/:version/:file`). Persistent,
versioned, does not expire. Reuses `lib/gitlab_api.py` for auth and SSL.
Requires the package registry to be enabled and the tool's build pipeline
to publish there (the archive lands in the registry, not as a CI artifact).

`bundle.json` required fields: `project` (URL or numeric ID), `package`,
`file`, `version`. Example:

```json
"tool-a": {
  "source":  "gitlab-package",
  "project": "https://gitlab.com/myorg/tool-a",
  "package": "tool-a",
  "file":    "tool-a-{version}-linux-x86_64.tar.gz",
  "version": "1.1.0"
}
```

Adapter sketch:

```python
class GitlabPackageSource(SourceAdapter):
    """Download from GitLab Generic Package Registry."""

    def __init__(self, project: str, package: str,
                 file: str, version: str, config):
        self.url = (
            f"{config.gitlab_api_url}/projects/{_encode(project)}"
            f"/packages/generic/{package}/{version}"
            f"/{file.format(version=version)}"
        )
        self.version = version
        self.config = config

    @classmethod
    def from_spec(cls, spec: dict, version: str, config) -> "GitlabPackageSource":
        return cls(spec["project"], spec["package"],
                   spec["file"], version, config)

    def validate(self) -> None:
        """HEAD request to confirm the package file exists and is reachable."""
        # Uses lib/gitlab_api.py head_request() with token + SSL config
        ...

    def acquire(self, deploy_dir: str, dry_run: bool = False) -> None:
        """Download archive, extract to deploy_dir."""
        # Uses lib/gitlab_api.py download() then tarfile/zipfile extraction
        ...
```

Pre-flight check: HTTP HEAD to the download URL — confirms the package
version exists before any git or filesystem write.

#### `artifactory`

Downloads from JFrog Artifactory via its REST API. Persistent by design.
Requires Artifactory infrastructure. Auth via API key or access token
(stored the same way as `GITLAB_TOKEN` — env var or config file).

`bundle.json` required fields: `url` (full artifact URL with `{version}`
placeholder), `version`. Credentials come from config, not the manifest.
Example:

```json
"tool-b": {
  "source":  "artifactory",
  "url":     "https://artifacts.example.com/libs-release/tool-b/{version}/tool-b-{version}.tar.gz",
  "version": "2.0.0"
}
```

Adapter sketch:

```python
class ArtifactorySource(SourceAdapter):
    """Download from JFrog Artifactory via REST API."""

    def __init__(self, url: str, version: str, config):
        self.url = url.format(version=version)
        self.version = version
        self.config = config   # holds ARTIFACTORY_TOKEN, verify_ssl

    @classmethod
    def from_spec(cls, spec: dict, version: str, config) -> "ArtifactorySource":
        return cls(spec["url"], version, config)

    def validate(self) -> None:
        """HEAD request to confirm artifact URL is reachable."""
        ...

    def acquire(self, deploy_dir: str, dry_run: bool = False) -> None:
        """Download archive, extract to deploy_dir."""
        ...
```

Pre-flight check: HTTP HEAD to the resolved URL — confirms the artifact
exists before any git or filesystem write.

#### Registry with all four adapters

```python
_ADAPTERS: dict[str, type[SourceAdapter]] = {
    "gitlab":          GitlabSource,
    "disk":            DiskSource,
    "gitlab-package":  GitlabPackageSource,   # future
    "artifactory":     ArtifactorySource,     # future
}
```

Adding a fifth source type later requires only a new class + one line here.

---

## Pre-flight Validation

Before creating any git artifacts or writing any files, `toolman bundle`
runs all validation upfront. If any check fails every problem is reported
and toolman exits before touching git or the filesystem.

### Step 0 — Filesystem and repository pre-checks

Performed before schema parsing. All four sub-checks run; all failures are
reported together before toolman exits.

**`deploy_base_path` validation** — toolman requires a concrete, usable
deploy destination before touching anything else:
- `deploy_base_path` is set (error if neither `bundle.json` config nor CLI
  `--deploy-path` provides it — do not silently use the current directory)
- `deploy_base_path` is an absolute path (reject relative paths with a clear
  message; relative paths are ambiguous when scripts are run from different
  directories)
- `deploy_base_path` exists and is a directory (fail with a suggestion to
  `mkdir -p` if the path doesn't exist, or a type error if it's a file)
- `deploy_base_path` is writable by the current process (fail with a
  permission error and the effective user/group if not)

**Git working tree must be clean** — `toolman bundle` will commit
`releases/{version}.json`. If the working tree has uncommitted changes or
staged files, git will pick them up and produce unexpected commits or errors.

Run `git status --porcelain`. If output is non-empty, exit with:
```
Working tree is not clean. Commit or stash the following before running toolman bundle:
  M bundle.json
  ?? scratch.txt
```
This check is skipped for `--deploy-only` (no git writes occur).

### Step 1 — Schema validation
`bundle.json` is validated on startup:
- Required fields present for each source type
- All versions are valid semver strings
- All source types are known
- No duplicate tool names
- `releases/{VERSION}.json` does **not** already exist — if it does, exit
  with: `"version {VERSION} already released — choose a new version or use
  --deploy-only"`. Overwriting a frozen snapshot silently would corrupt
  release history.
- The target git tag (`{tag_prefix}{VERSION}`) does **not** already exist on
  the remote — checked via `git ls-remote --tags`. If it exists, exit with a
  clear error before making any commits. This guards against the scenario
  where a previous partial run committed the snapshot but failed to push the
  tag, leaving the repo in an inconsistent state.

**Retry semantics for partial runs**: if `releases/{VERSION}.json` is already
present *and* committed (i.e. it exists in `git show HEAD:releases/{VERSION}.json`),
this indicates a previous run committed the snapshot but failed before or
during tag creation. In this case toolman skips the write+commit step and
goes straight to tag creation, making the operation idempotent on retry. The
"already exists" guard above only fires when the file exists but is *not*
already committed (i.e. it would be a silent overwrite of a *different*
version's committed snapshot — a genuine collision).

### Step 2 — Source availability checks

| Source | Pre-flight check |
|---|---|
| `gitlab` | Tag `{tag_prefix}{version}` exists on remote (`git ls-remote --tags`) |
| `disk` | Archive path exists and is readable |
| `gitlab-package` *(future)* | HTTP HEAD to package download URL (auth + SSL via `lib/gitlab_api.py`) |
| `artifactory` *(future)* | HTTP HEAD to resolved artifact URL (auth via `ARTIFACTORY_TOKEN`) |

### Step 3 — Release ordering (with `--deploy-tools`)

Once all pre-flight checks pass, the sequence is:

```
1. Deploy each managed tool (acquire → bootstrap → modulefile)
2. Write releases/{version}.json
3. git add + commit releases/{version}.json
4. Create bundle git tag
5. Write bundle modulefile
```

This guarantees no partial releases: the tag is only created after all
tools are successfully deployed and the release snapshot is committed.

### Bootstrap sentinel

To distinguish a fully-bootstrapped deploy dir from a partially-failed
one, toolman writes a `.toolman-ok` sentinel file after a successful
bootstrap (or after extraction if no bootstrap script exists). On rerun,
a deploy dir without this sentinel is treated as failed — the dir is
removed and the tool is redeployed from scratch.

---

## Modulefile Templates

### Principle

Templates are the source of truth for modulefile content. The copy-previous
approach (copying and version-bumping an existing deployed modulefile) is not
used — it makes modulefile content dependent on filesystem history and
non-reproducible on a new machine.

### Bundle repo layout

The bundle repo is the natural home for all modulefile templates. It is
already the administrative centre for a toolset — versions, sources, and
deployment configuration all live here. (Full layout in the **Bundle Repo
Layout** section above; modulefile-relevant paths shown here.)

```
bundle-repo/
  bundle.json
  releases/
    1.0.0.json        ← frozen release snapshots (toolman writes, never edited)
    1.1.0.json
  modulefiles/
    tool-a.tcl        ← per-tool templates (bundle admin owns these)
    tool-b.tcl
    tool-c.tcl
    bundle.tcl        ← template for the bundle composition modulefile
```

### `modulefile` field in tool spec

Each tool in `bundle.json` can declare who owns its modulefile template via
the optional `modulefile` field. Two placeholder variables are available:

| Placeholder | Expands to |
|---|---|
| `{self}` | The tool's deploy directory (`deploy_base_path/tool_name/version`) |
| `{bundle}` | The bundle repo root |

Examples:

```json
"tool-a": { }
  → convention: bundle_root/modulefiles/tool-a.tcl (auto-detected)

"tool-b": { "modulefile": "modulefiles/tool-b.tcl" }
  → explicit bundle-repo-relative path

"tool-c": { "modulefile": "{self}/modulefile.tcl" }
  → tool developer owns it; root of deploy dir (shorthand for {self}/modulefile.tcl)

"tool-d": { "modulefile": "{self}/config/modulefile.tcl" }
  → tool developer owns it; non-standard path within deploy dir

"tool-e": { "modulefile": "{bundle}/shared/generic.tcl" }
  → explicit bundle-root-relative path
```

`{self}` and `{bundle}` use the same `{placeholder}` convention as `{version}`
in the `disk` source `path` field — no new syntax to learn.

### Resolution chain

For each managed tool, toolman resolves the template in this order:

```
1. bundle.json "modulefile" field (explicit path, {self} or {bundle} substituted)
2. bundle_root/modulefiles/{tool-name}.tcl  (convention, auto-detected)
3. deploy_dir/modulefile.tcl                (shipped inside archive or clone)
4. MODULEFILE_TEMPLATE config / env var     (global fallback)
5. generated default                        (always works)
```

For the bundle composition modulefile:

```
1. bundle.json root "modulefile" field      (if present)
2. bundle_root/modulefiles/bundle.tcl       (convention)
3. MODULEFILE_TEMPLATE config / env var     (global fallback)
4. generated default                        (always works)
```

**Fallthrough behaviour differs by step:**

- **Step 1 (explicit `"modulefile"` field)**: if the resolved path does not
  exist → **error**. An explicit path is an intentional declaration; silently
  using a different template would produce a wrong modulefile with no
  warning. Example error:
  `"Modulefile template not found: modulefiles/tool-b.tcl (declared explicitly in bundle.json for tool-b)"`
- **Steps 2–4 (convention paths and global fallback)**: if the resolved path
  does not exist → fall through silently to the next level.
- **Step 5 (generated default)**: always succeeds — no path to check.

### Who should own the template

| Situation | Recommended approach |
|---|---|
| Bundle admin controls site conventions | Default — `modulefiles/{name}.tcl` in bundle repo |
| Tool developer ships a well-maintained modulefile | `"modulefile": "{self}/modulefile.tcl"` |
| `disk` / `gitlab-package` / `artifactory` tool (no git repo) | Default — bundle repo template required |
| Non-standard path in tool repo | `"modulefile": "{self}/path/to/module.tcl"` |

---

## CLI Specification

Single entry point: `scripts/toolman.sh → src/toolman.py`

```
toolman <action> [OPTIONS]
```

All `toolman` actions run from the **bundle repo root**. Toolman exits
immediately with a clear error if `bundle.json` is not found in the
current directory. No `--bundle-dir` flag — always `cd` into the bundle
repo first.

For releasing individual tools, use the existing `release` tool:
```
release.sh → src/release.py   (unchanged, run from a single-tool repo root)
```

### Actions

#### `init` — scaffold a new bundle repo

Creates the standard bundle repo directory structure in the current
directory. Prompts for the bundle name if not supplied as an argument.

```
toolman init [NAME] [--config FILE] [--dry-run] [-n]
```

Creates:
```
bundle.json           ← name, config block with defaults, empty managed/external
releases/             ← empty directory (.gitkeep)
modulefiles/
  bundle.tcl          ← starter bundle composition modulefile template
```

`bundle.json` is pre-populated with the full `"config"` block using
values from environment variables where available, falling back to
documented defaults otherwise. This gives the admin a
ready-to-edit file with no blank required fields.

Fails with a clear error if `bundle.json` already exists — never
overwrites an existing bundle. Does not run `git init` — assumes the
directory is already a git repo or will become one separately.

In `-n` / `--non-interactive` mode, `NAME` is required. If `NAME` is
omitted in interactive mode, toolman prompts; it does not default to the
directory name.

---

#### `check` — report available updates for managed tools

Reads `bundle.json` from the current repo root and queries each source for
the latest available version. Prints a summary table so the bundle admin
can see which tools have updates and contact the relevant developers.

```
toolman check [TOOL ...] [--config FILE]
```

With no arguments checks all managed tools. One or more tool names restrict
the check to those tools only.

Output format:

```
tool-a   1.0.0  →  1.2.0   gitlab          UPDATE AVAILABLE
tool-b   2.0.0         -    disk            cannot check
tool-c   3.0.0  →  3.0.0   gitlab-package  up to date
tool-d   4.0.0         ?    artifactory     cannot check
```

Exit code 0 if all checkable tools are up to date or cannot be checked
(`disk`, `artifactory`). Non-zero only when at least one checkable tool
has a confirmed update available. Useful for scripting / CI notifications.

| Source | Behaviour |
|---|---|
| `gitlab` | Lists remote tags via `git ls-remote --tags`, finds highest semver matching `{tag_prefix}*` using `lib/semver.py` |
| `disk` | Prints `cannot check` — no registry to query |
| `gitlab-package` *(future)* | Queries Package Registry API for all versions of the package |
| `artifactory` *(future)* | Prints `cannot check` — directory structure varies |

No writes. Read-only operation — safe to run at any time.

---

#### `bundle` — bundle release + bundle modulefile
Reads `bundle.json` from the current repo root.

```
toolman bundle [--version X.Y.Z] [--deploy-path PATH] [--mf-path PATH]
               [--deploy-tools] [--external-tool NAME/VER ...]
               [--config FILE] [--dry-run] [-n]

toolman bundle --deploy-only VERSION [--deploy-path PATH] [--mf-path PATH]
               [--config FILE] [--dry-run] [-n]
```

`--deploy-path` is optional if `deploy_base_path` is set in `bundle.json`
config. The CLI flag overrides the config value. Fails with a clear error
if neither is set.

`--deploy-only` and `--deploy-tools` are mutually exclusive.

If `--version` is omitted, toolman reads the last entry in `releases/`
(sorted by semver), suggests the next patch/minor/major increment, and
prompts the admin to confirm or enter a different version. In
`-n` / `--non-interactive` mode `--version` is required.

**First release** (when `releases/` is empty or contains no valid semver
files): toolman suggests `1.0.0` as the default version. The pre-release
summary header becomes "First release" instead of "Changes since X.Y.Z",
and tool versions are listed without a "→" comparison column (there is no
previous version to compare against).

**Pre-release summary**: before any git operation, toolman prints a
confirmation summary and prompts the admin to proceed:

```
Bundle:   my-toolset  →  1.1.0
Changes since 1.0.0:
  tool-a   1.0.0  →  1.1.0   gitlab
  tool-b   2.0.0  (unchanged)
  tool-c   3.0.0  (unchanged)
External:
  python   3.12
  gcc      11

Proceed with release? [y/N]
```

First-release variant (no previous version):

```
Bundle:   my-toolset  →  1.0.0
First release:
  tool-a   1.1.0   gitlab
  tool-b   2.0.0   disk
  tool-c   3.0.0   disk
External:
  python   3.12
  gcc      11

Proceed with release? [y/N]
```

Auto-accepted in `-n` / `--non-interactive` mode.

**Without `--deploy-tools`**: assumes all managed tools are already
deployed at the correct versions (deploy dirs exist with `.toolman-ok`
sentinel). Runs pre-flight schema + source availability checks, then:

```
1. Pre-flight validation (schema + source checks)
2. Print pre-release summary → confirm
3. Write releases/{version}.json
4. git add + commit releases/{version}.json
5. Full bundle release flow: release branch + annotated tag + changelog
   + optional GitLab default branch update (reuses release.py logic)
6. Write bundle modulefile
```

**`--deploy-tools`**: deploy each managed tool first, then release.
Full sequence:

```
1. Pre-flight validation (schema + source checks)
2. Deploy each managed tool (acquire → extract → bootstrap → per-tool modulefile)
   — skips tools with existing .toolman-ok sentinel (already deployed),
     logging: "tool-a/1.1.0 already deployed — skipping"
   — removes and redeploys dirs missing the sentinel (partial failure)
3. Print pre-release summary → confirm
4. Write releases/{version}.json
5. git add + commit releases/{version}.json
6. Full bundle release flow: release branch + annotated tag + changelog
   + optional GitLab default branch update
7. Write bundle modulefile
```

**`--external-tool NAME/VER`**: Repeatable. Adds extra `module load` lines
to the bundle modulefile for tools not in `bundle.json`. Merged with
`external` from JSON; CLI takes precedence on conflicts.

**`--deploy-only VERSION`**: Write the bundle modulefile for an existing
bundle version. Does **not** redeploy tools — assumes they are already on
disk. `VERSION` must match a file in `releases/`. Reads the full source
spec from `releases/{VERSION}.json` — no git checkout, no submodule
operations. Confirms with user before writing.

If `VERSION` does not match any file in `releases/`, exit with an error
that lists available versions:

```
No release found for version '1.5.0'.
Available versions:
  1.0.0
  1.1.0
  1.2.0
```

If `releases/` is empty: `"No release found for version '1.5.0'. (no releases yet)"`

### Common options

| Flag | Config key / env var | Description |
|---|---|---|
| `--deploy-path PATH` | `DEPLOY_BASE_PATH` | Where tools are deployed |
| `--mf-path PATH` | `MF_BASE_PATH` | Separate modulefile base (e.g. NFS) |
| `--config FILE` | — | Path to an additional `KEY=value` override file. Applied after `bundle.json` config, before env vars. |
| `--dry-run` | — | Show what would happen, no writes |
| `-n` / `--non-interactive` | — | Auto-confirm all prompts. `--version` required when used with `toolman bundle`. |

---

## Bundle Modulefile Format

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

## Managed tools:
module load tool-a/1.1.0
module load tool-b/2.0.0
module load tool-c/4.0.0

## External tools:
module load python/3.12
module load gcc/11
```

Custom templates use `MODULEFILE_TEMPLATE` config. Available placeholders:

| Placeholder | Value |
|---|---|
| `%VERSION%` | Bundle version |
| `%TOOL_NAME%` | Bundle name |
| `%TOOL_LOADS%` | `module load` lines for managed tools |
| `%EXTERNAL_LOADS%` | `module load` lines for external tools |

If `%EXTERNAL_LOADS%` is absent from a custom template, external tools are
silently omitted.

---

## Deploy Directory Layout

```
{deploy_base_path}/
  {tool_name}/
    {version}/          ← acquired here (cloned, extracted, or downloaded)
      install.sh        ← bootstrap (optional, runs after acquisition)
      modulefile.tcl    ← tool's own template (optional, used if {self})
      ...

{mf_base_path or deploy_base_path/mf}/
  {tool_name}/
    {version}           ← modulefile (no extension)
```

---

## Configuration

Configuration lives in the `"config"` block of `bundle.json`, tracked in git.
This makes the bundle repo fully self-contained — clone it and all non-sensitive
configuration is available immediately.

### Priority chain (later wins)

```
1. bundle.json "config" block     (bundle repos only — not present in single-tool repos)
2. --config FILE                  (explicit KEY=value override file, optional)
3. Environment variables          (highest priority, always wins)
```

**`release` tool** (separate, unchanged) has its own config via the
existing `.release.conf` / env var mechanism — unrelated to `toolman`.

### Config keys

| `bundle.json` key | Env var | Description |
|---|---|---|
| `gitlab_api_url` | `GITLAB_API_URL` | GitLab API base URL |
| `tag_prefix` | `RELEASE_TAG_PREFIX` | Version tag prefix (e.g. `v`) |
| `remote` | `RELEASE_REMOTE` | Git remote name |
| `deploy_base_path` | `DEPLOY_BASE_PATH` | Where tools are deployed |
| `mf_base_path` | `MF_BASE_PATH` | Separate modulefile base dir |
| `verify_ssl` | `GITLAB_VERIFY_SSL` | SSL certificate verification |
| `update_default_branch` | `RELEASE_UPDATE_DEFAULT_BRANCH` | Update GitLab default branch on release |
| `modulefile_template` | `MODULEFILE_TEMPLATE` | Global fallback modulefile template path |

### Credentials — environment variables only

Credentials are **never** stored in `bundle.json` or any committed file.

| Env var | Description |
|---|---|
| `GITLAB_TOKEN` | GitLab personal access token (release flow + `gitlab-package` source) |
| `ARTIFACTORY_TOKEN` | Artifactory access token (`artifactory` source, future) |

Store credentials in shell profile or a secrets manager:
```bash
export GITLAB_TOKEN=glpat-...
export ARTIFACTORY_TOKEN=...
```

---

## What Is Preserved from the Current Codebase

| Component | Status |
|---|---|
| `src/release.py` + `scripts/release.sh` | **Unchanged** — out of scope |
| `src/lib/git.py` | Preserved — used by `release` and by `toolman check` |
| `src/lib/gitlab_api.py` | Preserved — used by `release` and by `gitlab-package` source |
| `src/lib/semver.py` | Preserved |
| `src/lib/log.py` | Preserved |
| `src/lib/prompt.py` | Preserved |
| `src/lib/config.py` | Extended — reads `bundle.json` config block |
| `src/lib/modulefile.py` | Extended — `external_tools` param, `%EXTERNAL_LOADS%` placeholder |
| `src/bundle.py` | **Deleted** — replaced by `src/toolman.py` + `src/lib/manifest.py` |
| `detect_submodules()` | **Deleted** |
| Copy-previous modulefile logic | **Deleted** — templates are the source of truth |
| Submodule checkout in `bundle_deploy_only_flow()` | **Deleted** — replaced by `releases/{version}.json` lookup |
| `src/deploy.py` | **Deleted** — deployment is bundle-driven via `src/lib/sources.py` |
| `scripts/deploy.sh`, `bundle.sh` | **Deleted** |

New files: `src/toolman.py`, `src/lib/manifest.py`, `src/lib/sources.py`

---

## Concurrency

`toolman bundle` must not be run concurrently on the same bundle repo.

Two simultaneous runs will corrupt each other: both will read the same
`releases/` state, race to write `releases/{version}.json`, produce
duplicate or conflicting commits, and the second `git push --tags` will
fail with a tag-already-exists rejection — leaving the repo in an
inconsistent state.

No file locking is provided. Use OS-level coordination if concurrent runs
are a risk in your CI environment:

```bash
# Example: flock in CI
flock /var/lock/toolman-my-toolset.lock toolman bundle --version 1.2.0 -n
```

Git will catch a tag conflict on push, but the snapshot commit and branch
state may already have diverged unpredictably by that point. Prevention is
the only safe approach.

---

## Deferred

- **`toolman manifest` subcommand** (`toolman manifest set tool-a 1.1.0`) for
  scripted `bundle.json` updates. Deferred — the bundle admin edits
  `bundle.json` manually for now. Future automation (e.g. a CI job that opens
  an MR against the bundle repo after a tool release) can call this once
  implemented.

- **Archive checksum verification** — an optional `"sha256"` field in the
  tool spec (and stored in `releases/{version}.json`) would allow toolman to
  verify archive integrity at deploy time. Deferred — operational controls
  (trusted NFS, package registry) are the primary integrity mechanism for now.

  ```json
  "tool-b": {
    "source":  "disk",
    "path":    "/nfs/archives/tool-b/tool-b-{version}.tar.gz",
    "version": "2.0.0",
    "sha256":  "e3b0c44298fc1c149afb..."
  }
  ```
