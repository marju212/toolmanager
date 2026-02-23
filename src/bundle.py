#!/usr/bin/env python3
"""Bundle tool: submodule detection + bundle release + bundle deploy.

Usage: bundle.py [OPTIONS]

Options:
  --deploy-only              Deploy bundle modulefile for existing tag
  --submodule-dir DIR        Subdirectory containing tool submodules
  --version X.Y.Z            Set bundle version non-interactively
  --deploy-path PATH         Deploy base path
  --config FILE              Path to config file
  --dry-run                  Show what would be done
  --non-interactive, -n      Auto-confirm all prompts
  --help, -h                 Show help
"""

import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from lib.log import log_info, log_warn, log_error, log_success
from lib.config import load_config
from lib.semver import validate_semver
from lib.git import (
    get_repo_root, check_branch, get_latest_version, check_version_available,
    generate_changelog, create_release_branch, tag_release, cleanup_remote,
    get_remote_url, extract_tool_name, run_git,
)
from lib.gitlab_api import get_project_id, update_default_branch
from lib.modulefile import (
    generate_bundle_modulefile, resolve_template, write_modulefile,
)
from lib.prompt import confirm, prompt_version


USAGE = """\
Usage: bundle.py [OPTIONS]

Bundle tool for toolset repos with submodules.

Options:
  --deploy-only              Deploy bundle modulefile for existing tag
  --submodule-dir DIR        Subdirectory containing tool submodules
  --version X.Y.Z            Set bundle version non-interactively
  --deploy-path PATH         Deploy base path
  --config FILE              Path to config file
  --dry-run                  Show what would be done
  --non-interactive, -n      Auto-confirm all prompts
  --help, -h                 Show this help message
"""


def parse_args(argv: list) -> dict:
    args = {
        "deploy_only": False,
        "submodule_dir": "",
        "dry_run": False,
        "config_file": "",
        "cli_version": "",
        "cli_deploy_path": "",
        "non_interactive": False,
    }

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--deploy-only":
            args["deploy_only"] = True
        elif arg == "--submodule-dir":
            if i + 1 >= len(argv):
                log_error("--submodule-dir requires a directory argument")
                raise SystemExit(1)
            args["submodule_dir"] = argv[i + 1]
            i += 1
        elif arg == "--dry-run":
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
        elif arg == "--deploy-path":
            if i + 1 >= len(argv):
                log_error("--deploy-path requires a directory path argument")
                raise SystemExit(1)
            args["cli_deploy_path"] = argv[i + 1]
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


def detect_submodules(submodule_dir: str, tag_prefix: str,
                      cwd: str = None) -> list:
    """Detect submodules and their pinned versions.

    Args:
        submodule_dir: Subdirectory containing submodules (empty = repo root).
        tag_prefix: Tag prefix for version extraction.
        cwd: Working directory.

    Returns:
        List of (name, version, path) tuples.

    Raises:
        SystemExit if no submodules found or a submodule is not pinned to a tag.
    """
    # Init and update submodules if needed
    run_git("submodule", "init", cwd=cwd, check=False)
    run_git("submodule", "update", cwd=cwd, check=False)

    # Parse git submodule status
    result = run_git("submodule", "status", cwd=cwd)
    if not result.stdout.strip():
        log_error("No submodules found.")
        raise SystemExit(1)

    submodules = []
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        # Strip leading +/- indicator
        if line[0] in "+-":
            line = line[1:]
        parts = line.split()
        if len(parts) < 2:
            continue

        commit = parts[0]
        path = parts[1]

        # Filter by submodule_dir if specified
        if submodule_dir:
            if not path.startswith(submodule_dir.rstrip("/") + "/"):
                continue

        name = os.path.basename(path)

        # Get tag at this commit
        full_path = os.path.join(cwd, path) if cwd else path
        tag_result = run_git("describe", "--tags", "--exact-match", "HEAD",
                             cwd=full_path, check=False)
        if tag_result.returncode != 0:
            log_error(f"Submodule '{name}' at {path} is not pinned to a tag. "
                      f"Commit: {commit}")
            raise SystemExit(1)

        tag = tag_result.stdout.strip()
        # Strip tag prefix to get version
        if tag.startswith(tag_prefix):
            version = tag[len(tag_prefix):]
        else:
            version = tag

        submodules.append((name, version, path))

    if not submodules:
        log_error("No submodules found"
                  + (f" in '{submodule_dir}'." if submodule_dir else "."))
        raise SystemExit(1)

    return submodules


def print_manifest(submodules: list, tag_prefix: str) -> None:
    """Pretty-print the submodule manifest table."""
    if not submodules:
        return

    max_name = max(len(s[0]) for s in submodules)
    max_ver = max(len(s[1]) for s in submodules)

    print("", file=sys.stderr)
    print("\u2500\u2500 Bundle Manifest "
          "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
          "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
          "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500", file=sys.stderr)
    for name, version, path in submodules:
        print(f"  {name:<{max_name}}  {tag_prefix}{version:<{max_ver}}  "
              f"({path})", file=sys.stderr)
    print("\u2500" * 48, file=sys.stderr)
    print("", file=sys.stderr)


def deploy_bundle(
    version: str,
    bundle_name: str,
    deploy_base_path: str,
    tool_versions: dict,
    template_path: str = "",
    template_content: str = None,
    dry_run: bool = False,
) -> None:
    """Deploy a bundle modulefile.

    Args:
        version: Bundle version.
        bundle_name: Bundle name.
        deploy_base_path: Deploy base path.
        tool_versions: Dict mapping tool names to versions.
        template_path: Path to custom template.
        template_content: Pre-loaded template content.
        dry_run: If True, show what would be done.
    """
    mf_dir = os.path.join(deploy_base_path, "mf", bundle_name)
    mf_file = os.path.join(mf_dir, version)

    if os.path.isfile(mf_file):
        log_error(f"Bundle modulefile already exists: {mf_file}")
        raise SystemExit(1)

    content = generate_bundle_modulefile(
        bundle_name=bundle_name,
        version=version,
        deploy_base_path=deploy_base_path,
        tool_versions=tool_versions,
        template_path=template_path,
        template_content=template_content,
    )

    write_modulefile(content, mf_file, dry_run)


def bundle_flow(args: dict, config) -> None:
    """Full bundle release flow: check branch, detect submodules, release + deploy."""
    dry_run = args["dry_run"]
    non_interactive = args["non_interactive"]
    submodule_dir = args["submodule_dir"] or config.bundle_submodule_dir

    # Validate repo state
    check_branch(config.default_branch, config.remote)

    # Detect submodules
    submodules = detect_submodules(submodule_dir, config.tag_prefix)
    print_manifest(submodules, config.tag_prefix)

    tool_versions = {name: ver for name, ver, _ in submodules}

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

    release_branch = f"release/{config.tag_prefix}{new_version}"
    release_tag = f"{config.tag_prefix}{new_version}"

    # Generate changelog + append manifest
    changelog = generate_changelog(current_version, config.tag_prefix)
    manifest_lines = [f"- {name}: {config.tag_prefix}{ver}"
                      for name, ver, _ in submodules]
    changelog += "\n\n### Bundle Contents\n" + "\n".join(manifest_lines)

    print("", file=sys.stderr)
    print("\u2500\u2500 Changelog "
          "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
          "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
          "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
          "\u2500\u2500\u2500\u2500\u2500\u2500", file=sys.stderr)
    print(changelog, file=sys.stderr)
    print("\u2500" * 48, file=sys.stderr)
    print("", file=sys.stderr)

    if not confirm(f"Create bundle release {release_tag}?",
                   dry_run=dry_run, non_interactive=non_interactive):
        log_warn("Bundle release cancelled.")
        return

    # Detect GitLab project
    remote_url = get_remote_url(config.remote)
    if not remote_url:
        log_error(f"Cannot determine URL for remote '{config.remote}'. "
                  f"Check that the remote exists: git remote -v")
        raise SystemExit(1)
    project_id = get_project_id(
        remote_url, config.gitlab_token, config.gitlab_api_url,
        config.verify_ssl, dry_run,
    )

    cleanup_branch = ""
    cleanup_tag = ""

    try:
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

        # Deploy bundle modulefile if deploy path is set
        if config.deploy_base_path:
            if confirm(f"Deploy bundle modulefile to "
                       f"{config.deploy_base_path}?",
                       dry_run=dry_run, non_interactive=non_interactive):
                # Resolve template
                template_content = resolve_template(
                    config_template_path=config.modulefile_template,
                )
                bundle_name = config.bundle_name
                if not bundle_name:
                    bundle_name, _ = extract_tool_name(config.remote)

                deploy_bundle(
                    new_version, bundle_name, config.deploy_base_path,
                    tool_versions,
                    template_content=template_content,
                    dry_run=dry_run,
                )

        log_success(f"Bundle release {release_tag} completed!")

    except SystemExit as e:
        if e.code != 0 and (cleanup_branch or cleanup_tag):
            cleanup_remote(cleanup_branch, cleanup_tag, config.remote,
                           config.default_branch)
        raise
    except Exception:
        if cleanup_branch or cleanup_tag:
            cleanup_remote(cleanup_branch, cleanup_tag, config.remote,
                           config.default_branch)
        raise


def bundle_deploy_only_flow(args: dict, config) -> None:
    """Deploy bundle modulefile for an existing tag."""
    dry_run = args["dry_run"]
    non_interactive = args["non_interactive"]
    submodule_dir = args["submodule_dir"] or config.bundle_submodule_dir

    if not config.deploy_base_path:
        log_error("DEPLOY_BASE_PATH is not configured. "
                  "Set it via config file, environment variable, or "
                  "--deploy-path.")
        raise SystemExit(1)

    # Fetch tags
    log_info(f"Fetching tags from {config.remote}...")
    run_git("fetch", config.remote, "--tags", "--quiet", check=False)

    # Version selection
    if args["cli_version"]:
        try:
            validate_semver(args["cli_version"])
        except ValueError:
            log_error(f"Invalid version format: '{args['cli_version']}' "
                      "(expected X.Y.Z)")
            raise SystemExit(1)
        version = args["cli_version"]
    else:
        if non_interactive:
            log_error("--version is required in non-interactive mode.")
            raise SystemExit(1)
        latest = get_latest_version(config.tag_prefix)
        while True:
            print("", file=sys.stderr)
            print(f"Latest tag: {config.tag_prefix}{latest}", file=sys.stderr)
            try:
                version = input("Enter bundle version to deploy (X.Y.Z): ").strip()
            except (EOFError, KeyboardInterrupt):
                print("", file=sys.stderr)
                raise SystemExit(1)
            if not version:
                log_warn("Version cannot be empty. Try again.")
                continue
            try:
                validate_semver(version)
                break
            except ValueError:
                log_error(f"Invalid semver format: '{version}' (expected X.Y.Z)")

    # Validate tag exists
    release_tag = f"{config.tag_prefix}{version}"
    result = run_git("rev-parse", release_tag, check=False)
    if result.returncode != 0:
        log_error(f"Tag '{release_tag}' does not exist.")
        raise SystemExit(1)

    # Save current branch/HEAD to restore later
    head_result = run_git("symbolic-ref", "--short", "HEAD", check=False)
    original_ref = head_result.stdout.strip() if head_result.returncode == 0 else None

    try:
        # Checkout tag (detached)
        if not dry_run:
            run_git("checkout", release_tag)
            run_git("submodule", "update", "--init")

        # Detect submodules
        submodules = detect_submodules(submodule_dir, config.tag_prefix)
        print_manifest(submodules, config.tag_prefix)

        tool_versions = {name: ver for name, ver, _ in submodules}

        # Resolve template
        template_content = resolve_template(
            config_template_path=config.modulefile_template,
        )

        bundle_name = config.bundle_name
        if not bundle_name:
            bundle_name, _ = extract_tool_name(config.remote)

        if not confirm(f"Deploy bundle modulefile for {release_tag} to "
                       f"{config.deploy_base_path}?",
                       dry_run=dry_run, non_interactive=non_interactive):
            log_warn("Bundle deploy cancelled.")
            return

        deploy_bundle(
            version, bundle_name, config.deploy_base_path, tool_versions,
            template_content=template_content,
            dry_run=dry_run,
        )

        log_success(f"Bundle deploy of {release_tag} completed!")

    finally:
        # Restore original branch
        if not dry_run and original_ref:
            run_git("checkout", original_ref, check=False)


def main(argv: list = None) -> None:
    if argv is None:
        argv = sys.argv[1:]

    args = parse_args(argv)
    repo_root = get_repo_root()
    config = load_config(
        config_file=args["config_file"],
        repo_root=repo_root,
        cli_deploy_path=args["cli_deploy_path"],
    )

    dry_run = args["dry_run"]

    if dry_run:
        log_warn("Running in dry-run mode \u2014 no changes will be made.")
        print("", file=sys.stderr)

    if args["deploy_only"]:
        bundle_deploy_only_flow(args, config)
    else:
        bundle_flow(args, config)


if __name__ == "__main__":
    main()
