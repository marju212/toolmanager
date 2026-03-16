# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

toolmanager is a collection of Python utility scripts for DevOps workflows, with thin Bash wrappers for CLI compatibility. The system consists of two tools:

- **`release.sh` → `src/release.py`** — Release automation (annotated tag from main + changelog; no branches, no GitLab API)
- **`deploy.sh` → `src/deploy.py`** — Manifest-driven deploy tool: subcommand-based, reads `tools.json` for source configuration and version tracking

Both share a common library in `src/lib/` (config, git, log, semver, modulefile, manifest, sources, prompt).

## Technology

- **Python 3.12.3** — minimum and target version
- **stdlib only** — no external packages; all functionality uses the Python standard library

## Commands

### Running Tests

```bash
# Run all tests
python3 -m unittest discover tests/ -p "test_*.py"

# Run a specific test file
python3 -m unittest tests/test_semver.py

# Run a specific test by name
python3 -m unittest tests.test_semver.TestValidateSemver.test_valid_versions

# Run with verbose output
python3 -m unittest discover tests/ -p "test_*.py" -v
```

Test files: `test_config`, `test_semver`, `test_git`, `test_modulefile`, `test_manifest`, `test_release`, `test_deploy`.

### Running the Scripts

```bash
./scripts/release.sh --dry-run                              # validate without side effects
./scripts/release.sh --version 1.2.3 -n                     # non-interactive release
./scripts/release.sh --version 1.2.3 --description "text"   # release with description
./scripts/deploy.sh deploy my-tool --version 1.2.3           # deploy a specific version
./scripts/deploy.sh scan                                     # check all tools for updates
./scripts/deploy.sh upgrade my-tool                          # deploy latest version
./scripts/deploy.sh toolset science --version 1.0.0          # write toolset modulefile
```

## Architecture

### Code Structure

```
src/
├── lib/                       # Shared library (Python package)
│   ├── __init__.py
│   ├── config.py              # Multi-level config loading (.release.conf format)
│   ├── git.py                 # Git operations via subprocess
│   ├── log.py                 # Color-coded logging (log_info, log_warn, log_error, log_success)
│   ├── semver.py              # Semver validation and version suggestion
│   ├── modulefile.py          # Modulefile generation + template substitution
│   ├── manifest.py            # tools.json read/write and validation
│   ├── sources.py             # GitAdapter and DiskAdapter (SourceError exception)
│   └── prompt.py              # Interactive prompts (confirm, version picker)
├── release.py                 # Release tool: annotated tag from main + changelog
└── deploy.py                  # Deploy tool: subcommand-driven (deploy/scan/upgrade/toolset)

scripts/
├── release.sh                 # Thin wrapper: exec python3 src/release.py "$@"
├── deploy.sh                  # Thin wrapper: exec python3 src/deploy.py "$@"
└── .release.conf.example      # Annotated config file template
```

### Key Design Patterns

- Every write operation respects `dry_run` — full validation runs without side effects
- Environment variables are snapshotted at import time so config files cannot override them
- Release flow tags from main only — no release branches, no hotfix MRs, no GitLab API calls
- `deploy.sh` is subcommand-driven: `deploy`, `scan`, `upgrade`, `toolset`
- Source adapters (`GitAdapter`, `DiskAdapter`) raise `SourceError` on failure; callers log and exit
- Bootstrap support: `install.sh` (priority) or `install.py` in tool repos (git source only)
- Modulefile template chain: previous version copy > repo `modulefile.tcl` > config template > default
- Toolset modulefiles support per-tool version placeholders (`%tool-name%`, `%TOOL_LOADS%`)
- `tools.json` manifest: `version` field updated automatically on deploy; source config maintained manually

### Test Infrastructure

Tests use Python `unittest`. Shared helpers in `tests/conftest.py` provide:
- `setup_test_repo()` — creates a bare remote + working clone per test
- `install_git_mock()` / `uninstall_git_mock()` — intercepts `lib.git._run_git` for integration tests
