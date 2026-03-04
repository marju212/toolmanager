#!/usr/bin/env python3
"""Release tool: tag + changelog.

Usage: release.py [OPTIONS]

Options:
  --dry-run                  Run all checks without making changes
  --config FILE              Path to config file
  --version X.Y.Z            Set version non-interactively
  --description DESC         Release description (prepended to changelog in tag message)
  --non-interactive, -n      Auto-confirm all prompts
  --help, -h                 Show help
"""

import os
import sys

# Ensure src/ is on the path when run directly
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from lib.log import log_info, log_warn, log_error, log_success
from lib.config import load_config
from lib.semver import validate_semver
from lib.git import (
    get_repo_root, check_branch, get_latest_version, check_version_available,
    generate_changelog, tag_release,
)
from lib.prompt import confirm, prompt_version


USAGE = """\
Usage: release.py [OPTIONS]

Tag a release from main. No branches or GitLab API calls needed.

Options:
  --dry-run                  Run all checks without making changes
  --config FILE              Path to config file (default: .release.conf)
  --version X.Y.Z            Set release version non-interactively
  --description DESC         Free-text summary prepended to the changelog in the tag message
  --non-interactive, -n      Auto-confirm all prompts (for CI/CD)
  --help, -h                 Show this help message

CI/CD usage:
  release.py --version 1.2.3 --non-interactive
  release.py --version 1.2.3 --description "Adds widget support" --non-interactive

Environment variables:
  RELEASE_DEFAULT_BRANCH   Branch to release from (default: main)
  RELEASE_TAG_PREFIX       Tag prefix (default: v)
  RELEASE_REMOTE           Git remote name (default: origin)

Config files (loaded in order, later values win):
  ~/.release.conf          User-level config
  <repo>/.release.conf     Repo-level config
  --config FILE            Explicit config file
  Environment variables    Highest priority
"""


def parse_args(argv: list) -> dict:
    """Parse command-line arguments.

    Returns dict with parsed values.
    """
    args = {
        "dry_run": False,
        "config_file": "",
        "cli_version": "",
        "description": "",
        "non_interactive": False,
    }

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--dry-run":
            args["dry_run"] = True
        elif arg == "--config":
            if i + 1 >= len(argv):
                log_error("--config requires a file path argument")
                raise SystemExit(1)
            args["config_file"] = argv[i + 1]
            i += 1
        elif arg == "--version":
            if i + 1 >= len(argv):
                log_error("--version requires a version argument (X.Y.Z)")
                raise SystemExit(1)
            args["cli_version"] = argv[i + 1]
            i += 1
        elif arg == "--description":
            if i + 1 >= len(argv):
                log_error("--description requires a text argument")
                raise SystemExit(1)
            args["description"] = argv[i + 1]
            i += 1
        elif arg in ("--non-interactive", "-n"):
            args["non_interactive"] = True
        elif arg in ("--help", "-h"):
            print(USAGE)
            raise SystemExit(0)
        else:
            log_error(f"Unknown option: {arg}")
            print(USAGE)
            raise SystemExit(1)
        i += 1

    return args


def print_summary(branch: str, tag: str, dry_run: bool = False) -> None:
    """Print a boxed release summary."""
    rows = [
        f"Branch:   {branch}",
        f"Tag:      {tag}",
    ]

    min_width = 42
    inner_width = max(min_width, max(len(r) for r in rows) + 4)
    title = "Release Summary"

    border = "\u2550" * (inner_width + 4)
    title_pad = inner_width - len(title)
    title_left = title_pad // 2
    title_right = title_pad - title_left

    print("", file=sys.stderr)
    print(f"\u2554{border}\u2557", file=sys.stderr)
    print(f"\u2551  {' ' * title_left}{title}{' ' * title_right}  \u2551",
          file=sys.stderr)
    print(f"\u2560{border}\u2563", file=sys.stderr)
    for row in rows:
        print(f"\u2551  {row:<{inner_width}}  \u2551", file=sys.stderr)
    print(f"\u255a{border}\u255d", file=sys.stderr)
    print("", file=sys.stderr)

    if dry_run:
        log_warn("This was a dry run. No changes were made.")


def main(argv: list = None) -> None:
    """Main release flow."""
    if argv is None:
        argv = sys.argv[1:]

    args = parse_args(argv)

    repo_root = get_repo_root()
    config = load_config(
        config_file=args["config_file"],
        repo_root=repo_root,
    )

    dry_run = args["dry_run"]
    non_interactive = args["non_interactive"]

    if dry_run:
        log_warn("Running in dry-run mode \u2014 no changes will be made.")
        print("", file=sys.stderr)

    # Validate repo state
    check_branch(config.default_branch, config.remote)

    # Version selection
    current_version = get_latest_version(config.tag_prefix)

    if args["cli_version"]:
        try:
            validate_semver(args["cli_version"])
        except ValueError:
            log_error(f"Invalid version format: '{args['cli_version']}' "
                      "(expected X.Y.Z)")
            raise SystemExit(1)
        new_version = args["cli_version"]
        check_version_available(new_version, config.tag_prefix, config.remote)
        log_success(f"Will release {config.tag_prefix}{new_version}")
    else:
        new_version = prompt_version(current_version, config.tag_prefix)
        check_version_available(new_version, config.tag_prefix, config.remote)
        log_success(f"Will release {config.tag_prefix}{new_version}")

    release_tag = f"{config.tag_prefix}{new_version}"

    # Generate changelog
    changelog = generate_changelog(current_version, config.tag_prefix)

    print("", file=sys.stderr)
    print("\u2500\u2500 Changelog "
          "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
          "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
          "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
          "\u2500\u2500\u2500\u2500\u2500\u2500", file=sys.stderr)
    print(changelog, file=sys.stderr)
    print("\u2500" * 48, file=sys.stderr)
    print("", file=sys.stderr)

    # Optional description (interactive only, CLI flag takes precedence)
    description = args["description"]
    if not description and not non_interactive and sys.stdin.isatty():
        try:
            description = input("Release description (optional, Enter to skip): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("", file=sys.stderr)
            log_info("Description skipped.")
            description = ""

    # Confirm
    if not confirm(f"Create release {release_tag}?",
                   dry_run=dry_run, non_interactive=non_interactive):
        log_warn("Release cancelled.")
        return

    # Create annotated tag on main and push
    tag_release(release_tag, new_version, changelog, config.remote,
                dry_run, description=description)

    # Print summary
    print_summary(config.default_branch, release_tag, dry_run=dry_run)

    log_success(f"Release {release_tag} completed!")


if __name__ == "__main__":
    main()
