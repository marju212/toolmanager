# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

dev-utils is a collection of Python utility scripts for DevOps workflows, with thin Bash wrappers for CLI compatibility. The system consists of three tools:

- **`release.sh` → `src/release.py`** — Release automation (tag from main + changelog; no branches, no GitLab API)
- **`deploy.sh` → `src/deploy.py`** — Deploy tagged releases (clone + bootstrap + modulefile)
- **`bundle.sh` → `src/bundle.py`** — Toolset bundle management (submodule detection + bundle release + bundle deploy)

All three share a common library in `src/lib/` (config, git, log, semver, modulefile, prompt).

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

Test files: `test_config`, `test_semver`, `test_git`, `test_modulefile`, `test_release`, `test_deploy`, `test_bundle`.

### Running the Scripts

```bash
./scripts/release.sh --dry-run                              # validate without side effects
./scripts/release.sh --version 1.2.3 -n                     # non-interactive release
./scripts/release.sh --version 1.2.3 --description "text"   # release with description
./scripts/deploy.sh --version 1.2.3 --deploy-path /opt/software
./scripts/bundle.sh --version 1.0.0 --deploy-path /opt/software -n
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
│   └── prompt.py              # Interactive prompts (confirm, version picker)
├── release.py                 # Release tool: annotated tag from main + changelog
├── deploy.py                  # Deploy tool: clone + bootstrap + modulefile
└── bundle.py                  # Bundle tool: submodule detection + bundle release + deploy

scripts/
├── release.sh                 # Thin wrapper: exec python3 src/release.py "$@"
├── deploy.sh                  # Thin wrapper: exec python3 src/deploy.py "$@"
└── bundle.sh                  # Thin wrapper: exec python3 src/bundle.py "$@"
```

### Key Design Patterns

- Every write operation respects `dry_run` — full validation runs without side effects
- Environment variables are snapshotted at import time so config files cannot override them
- Release flow tags from main only — no release branches, no hotfix MRs, no GitLab API calls
- Bootstrap support: `install.sh` (priority) or `install.py` in tool repos
- Modulefile template chain: repo `modulefile.tcl` > config template > default
- Bundle modulefiles support per-tool version placeholders (`%tool-name%`, `%TOOL_LOADS%`)

### Test Infrastructure

Tests use Python `unittest`. Shared helpers in `tests/conftest.py` provide:
- `setup_test_repo()` — creates a bare remote + working clone per test
- `setup_bundle_test_repo()` — creates parent + 2 sub-tool repos with submodules
