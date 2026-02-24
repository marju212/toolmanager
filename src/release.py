#!/usr/bin/env python3
"""Release tool: branch + tag + changelog + GitLab API.

Usage: release.py [OPTIONS]

Options:
  --dry-run                  Run all checks without making changes
  --hotfix-mr BRANCH         Create MR from release branch to default branch
  --update-default-branch    Change GitLab default branch (default)
  --no-update-default-branch Skip changing default branch
  --config FILE              Path to config file
  --version X.Y.Z            Set version non-interactively
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
    generate_changelog, generate_changelog_range, create_release_branch,
    tag_release, cleanup_remote, get_remote_url, parse_project_path,
    extract_tool_name, count_commits_ahead, run_git,
)
from lib.gitlab_api import get_project_id, create_merge_request, update_default_branch
from lib.prompt import confirm, prompt_version, show_menu


USAGE = """\
Usage: release.py [OPTIONS]

Automate version management and release branch creation for GitLab repos.

Options:
  --dry-run                  Run all checks without making changes
  --hotfix-mr BRANCH         Create MR from a release branch back to the default branch
  --update-default-branch    Change GitLab default branch to the release branch (default: true)
  --no-update-default-branch Skip changing the GitLab default branch
  --config FILE              Path to config file (default: .release.conf)
  --version X.Y.Z            Set release version non-interactively
  --non-interactive, -n      Auto-confirm all prompts (for CI/CD)
  --help, -h                 Show this help message

CI/CD usage:
  release.py --version 1.2.3 --non-interactive
  GITLAB_TOKEN=$TOKEN release.py --version 1.2.3 --non-interactive

Hotfix workflow:
  # 1. Create a release (branch + tag only, no MR)
  release.py --version 1.2.3 --non-interactive
  # 2. Push hotfix commits to the release branch
  git checkout release/v1.2.3 && git cherry-pick <commit> && git push
  # 3. Create MR from the release branch back to the default branch
  release.py --hotfix-mr release/v1.2.3

Environment variables:
  GITLAB_TOKEN             GitLab personal access token (required for API calls)
  GITLAB_API_URL           GitLab API base URL (default: https://gitlab.com/api/v4)
  RELEASE_DEFAULT_BRANCH   Branch to release from (default: main)
  RELEASE_TAG_PREFIX       Tag prefix (default: v)
  RELEASE_REMOTE           Git remote name (default: origin)
  GITLAB_VERIFY_SSL        Verify SSL certificates (default: true)
  RELEASE_UPDATE_DEFAULT_BRANCH  Update GitLab default branch (default: true)

Token resolution (first match wins):
  GITLAB_TOKEN env var     Exported shell variable (highest priority)
  .release.conf            GITLAB_TOKEN key in any config file
  ~/.gitlab_token          Plain-text file containing just the token

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
        "hotfix_mr_branch": "",
        "update_default_branch": True,
        "update_default_branch_set": False,
        "config_file": "",
        "cli_version": "",
        "non_interactive": False,
    }

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--dry-run":
            args["dry_run"] = True
        elif arg == "--hotfix-mr":
            if i + 1 >= len(argv) or argv[i + 1].startswith("--"):
                log_error("--hotfix-mr requires a branch name argument")
                raise SystemExit(1)
            args["hotfix_mr_branch"] = argv[i + 1]
            i += 1
        elif arg == "--update-default-branch":
            args["update_default_branch"] = True
            args["update_default_branch_set"] = True
        elif arg == "--no-update-default-branch":
            args["update_default_branch"] = False
            args["update_default_branch_set"] = True
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


def print_summary(version: str, branch: str, tag: str, tag_prefix: str,
                  mr_url: str = "", dry_run: bool = False) -> None:
    """Print a boxed release summary."""
    rows = [
        f"Version:  {tag_prefix}{version}",
        f"Branch:   {branch}",
        f"Tag:      {tag}",
    ]
    if mr_url:
        rows.append(f"MR:       {mr_url}")

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


def _check_remote_url(remote: str) -> str:
    """Get and validate the remote URL, raising SystemExit with a clear message on failure."""
    remote_url = get_remote_url(remote)
    if not remote_url:
        log_error(f"Cannot determine URL for remote '{remote}'. "
                  f"Check that the remote exists: git remote -v")
        raise SystemExit(1)
    return remote_url


def hotfix_mr_flow(branch: str, config, dry_run: bool, non_interactive: bool) -> None:
    """Create a merge request from a release branch to the default branch."""
    log_info(f"Fetching from {config.remote}...")
    try:
        run_git("fetch", config.remote, "--tags", "--quiet")
    except Exception:
        log_error(f"Failed to fetch from '{config.remote}'. "
                  "Check credentials and network connectivity.")
        raise SystemExit(1)

    # Verify branch exists on remote
    result = run_git("rev-parse", "--verify",
                     f"{config.remote}/{branch}", check=False)
    if result.returncode != 0:
        log_error(f"Branch '{branch}' does not exist on remote "
                  f"'{config.remote}'.")
        raise SystemExit(1)

    # Verify branch has commits ahead
    ahead = count_commits_ahead(
        f"{config.remote}/{config.default_branch}",
        f"{config.remote}/{branch}",
    )
    if ahead == 0:
        log_error(f"Branch '{branch}' has no commits ahead of "
                  f"'{config.default_branch}'. Nothing to merge.")
        raise SystemExit(1)
    log_info(f"Branch '{branch}' is {ahead} commit(s) ahead of "
             f"'{config.default_branch}'.")

    # Extract version from branch name
    prefix = f"release/{config.tag_prefix}"
    if branch.startswith(prefix):
        version = branch[len(prefix):]
    else:
        version = branch

    # Generate changelog
    changelog = generate_changelog_range(
        f"{config.remote}/{config.default_branch}",
        f"{config.remote}/{branch}",
    )

    print("", file=sys.stderr)
    print("\u2500\u2500 Hotfix Changelog "
          "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
          "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
          "\u2500\u2500\u2500\u2500\u2500\u2500\u2500", file=sys.stderr)
    print(changelog, file=sys.stderr)
    print("\u2500" * 48, file=sys.stderr)
    print("", file=sys.stderr)

    if not confirm(f"Create merge request from '{branch}' to "
                   f"'{config.default_branch}'?",
                   dry_run=dry_run, non_interactive=non_interactive):
        log_warn("Hotfix MR cancelled.")
        raise SystemExit(0)

    # Get project ID
    remote_url = _check_remote_url(config.remote)
    project_id = get_project_id(
        remote_url, config.gitlab_token, config.gitlab_api_url,
        config.verify_ssl, dry_run,
    )

    mr_title = (f"Hotfix {config.tag_prefix}{version} merge back to "
                f"{config.default_branch}")
    mr_desc = f"## Hotfix {config.tag_prefix}{version}\n\n{changelog}"

    mr_url = create_merge_request(
        project_id, branch, config.default_branch, mr_title, mr_desc,
        config.gitlab_token, config.gitlab_api_url, config.verify_ssl,
        dry_run,
    )

    print("", file=sys.stderr)
    log_success(f"Hotfix MR created: {mr_url}")


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

    # Apply CLI overrides
    if args["update_default_branch_set"]:
        config.update_default_branch = args["update_default_branch"]

    dry_run = args["dry_run"]
    non_interactive = args["non_interactive"]

    if dry_run:
        log_warn("Running in dry-run mode \u2014 no changes will be made.")
        print("", file=sys.stderr)

    # Dispatch to hotfix MR flow
    if args["hotfix_mr_branch"]:
        hotfix_mr_flow(args["hotfix_mr_branch"], config, dry_run,
                       non_interactive)
        return

    # Show interactive menu when no mode flag, no --version, not non-interactive, and stdin is a TTY
    if not args["cli_version"] and not non_interactive and sys.stdin.isatty():
        menu_options = [
            ("Release", "Create release branch + tag"),
            ("Hotfix MR", f"Create MR from a release branch to "
                          f"{config.default_branch}"),
        ]
        choice = show_menu(menu_options)
        if choice == 1:
            # Hotfix MR
            try:
                branch = input("Enter release branch name "
                               "(e.g. release/v1.2.3): ")
            except (EOFError, KeyboardInterrupt):
                print("", file=sys.stderr)
                raise SystemExit(1)
            if not branch.strip():
                log_error("Branch name cannot be empty.")
                raise SystemExit(1)
            hotfix_mr_flow(branch.strip(), config, dry_run, non_interactive)
            return

    # Set up cleanup state
    cleanup_branch = ""
    cleanup_tag = ""

    try:
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
            check_version_available(new_version, config.tag_prefix,
                                    config.remote)
            log_success(f"Will release {config.tag_prefix}{new_version}")
        else:
            new_version = prompt_version(current_version, config.tag_prefix)
            check_version_available(new_version, config.tag_prefix,
                                    config.remote)
            log_success(f"Will release {config.tag_prefix}{new_version}")

        release_branch = f"release/{config.tag_prefix}{new_version}"
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

        # Confirm
        if not confirm(f"Create release {release_tag}?",
                       dry_run=dry_run, non_interactive=non_interactive):
            log_warn("Release cancelled.")
            return

        # Detect GitLab project
        remote_url = _check_remote_url(config.remote)
        project_id = get_project_id(
            remote_url, config.gitlab_token, config.gitlab_api_url,
            config.verify_ssl, dry_run,
        )

        # Create release branch
        create_release_branch(release_branch, config.remote, dry_run)
        cleanup_branch = release_branch

        # Create annotated tag
        tag_release(release_tag, new_version, changelog, config.remote,
                    dry_run)
        cleanup_tag = release_tag

        # Optionally update default branch
        if config.update_default_branch:
            if confirm(f"Update GitLab default branch to '{release_branch}'?",
                       dry_run=dry_run, non_interactive=non_interactive):
                update_default_branch(project_id, release_branch,
                                      config.gitlab_token,
                                      config.gitlab_api_url,
                                      config.verify_ssl, dry_run)
            else:
                log_info("Skipping default branch update.")

        # Switch back to default branch
        if not dry_run:
            result = run_git("checkout", config.default_branch, check=False)
            if result.returncode != 0:
                result = run_git("checkout",
                                 f"{config.remote}/{config.default_branch}",
                                 check=False)
                if result.returncode != 0:
                    log_warn(f"Could not switch back to '{config.default_branch}'. "
                             f"You may need to run: git checkout {config.default_branch}")

        # Print summary
        print_summary(new_version, release_branch, release_tag,
                      config.tag_prefix, dry_run=dry_run)

        log_success(f"Release {release_tag} completed!")

    except SystemExit as e:
        # Clean up on failure if we created partial artifacts (not on success)
        if e.code != 0 and (cleanup_branch or cleanup_tag):
            cleanup_remote(cleanup_branch, cleanup_tag, config.remote,
                           config.default_branch,
                           non_interactive=non_interactive)
        raise
    except Exception:
        if cleanup_branch or cleanup_tag:
            cleanup_remote(cleanup_branch, cleanup_tag, config.remote,
                           config.default_branch,
                           non_interactive=non_interactive)
        raise


if __name__ == "__main__":
    main()
