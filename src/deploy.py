#!/usr/bin/env python3
"""Deploy tool: clone tag + bootstrap + modulefile.

Usage: deploy.py [OPTIONS]

Options:
  --version X.Y.Z            Version to deploy (required)
  --deploy-path PATH         Deploy base path (required, or from config)
  --mf-path PATH             Override base directory for modulefiles
  --config FILE              Path to config file
  --dry-run                  Show what would be done
  --non-interactive, -n      Auto-confirm all prompts
  --help, -h                 Show help
"""

import os
import shutil
import subprocess
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from lib.log import log_info, log_warn, log_error, log_success
from lib.config import load_config
from lib.semver import validate_semver
from lib.git import (
    get_repo_root, get_remote_url, extract_tool_name, get_latest_version,
    run_git,
)
from lib.modulefile import (
    resolve_template, substitute_placeholders, generate_default_modulefile,
    write_modulefile, copy_and_update_modulefile, find_latest_modulefile,
)
from lib.prompt import confirm


USAGE = """\
Usage: deploy.py [OPTIONS]

Deploy a tagged release: clone, run bootstrap, write modulefile.

Options:
  --version X.Y.Z            Version to deploy (required, or interactive)
  --deploy-path PATH         Deploy base path (required, or from config)
  --mf-path PATH             Override base directory for modulefiles
  --config FILE              Path to config file
  --dry-run                  Show what would be done
  --non-interactive, -n      Auto-confirm all prompts
  --help, -h                 Show this help message
"""


def parse_args(argv: list) -> dict:
    args = {
        "dry_run": False,
        "config_file": "",
        "cli_version": "",
        "cli_deploy_path": "",
        "cli_mf_path": "",
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
        elif arg == "--deploy-path":
            if i + 1 >= len(argv):
                log_error("--deploy-path requires a directory path argument")
                raise SystemExit(1)
            args["cli_deploy_path"] = argv[i + 1]
            i += 1
        elif arg == "--mf-path":
            if i + 1 >= len(argv):
                log_error("--mf-path requires a directory path argument")
                raise SystemExit(1)
            args["cli_mf_path"] = argv[i + 1]
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


def run_bootstrap(deploy_dir: str, dry_run: bool = False) -> bool:
    """Run bootstrap script (install.sh or install.py) if present.

    Returns True if bootstrap ran successfully or was skipped.
    Returns False if bootstrap failed.
    """
    install_sh = os.path.join(deploy_dir, "install.sh")
    install_py = os.path.join(deploy_dir, "install.py")

    bootstrap_script = None
    if os.path.isfile(install_sh):
        bootstrap_script = install_sh
    elif os.path.isfile(install_py):
        bootstrap_script = install_py

    if bootstrap_script is None:
        return True

    log_info(f"Running bootstrap: {os.path.basename(bootstrap_script)}")

    if dry_run:
        log_info(f"[dry-run] Would run bootstrap: {bootstrap_script}")
        return True

    # Make executable
    os.chmod(bootstrap_script, 0o755)

    try:
        if bootstrap_script.endswith(".py"):
            subprocess.run(
                [sys.executable, bootstrap_script],
                cwd=deploy_dir, check=True,
            )
        else:
            subprocess.run(
                ["bash", bootstrap_script],
                cwd=deploy_dir, check=True,
            )
        log_success(f"Bootstrap completed: {os.path.basename(bootstrap_script)}")
        return True
    except subprocess.CalledProcessError as e:
        log_error(f"Bootstrap failed: {os.path.basename(bootstrap_script)} "
                  f"(exit code {e.returncode})")
        return False


def deploy_release(
    version: str,
    config,
    dry_run: bool = False,
    non_interactive: bool = False,
) -> None:
    """Deploy a tagged release: clone + bootstrap + modulefile.

    Args:
        version: Version string (X.Y.Z).
        config: Config object.
        dry_run: If True, show what would be done.
        non_interactive: If True, skip prompts.
    """
    deploy_base_path = config.deploy_base_path
    tag_prefix = config.tag_prefix
    remote = config.remote
    release_tag = f"{tag_prefix}{version}"

    # Validate deploy path is absolute
    if not os.path.isabs(deploy_base_path):
        log_error(f"DEPLOY_BASE_PATH must be an absolute path: "
                  f"{deploy_base_path}")
        raise SystemExit(1)

    tool_name, remote_url = extract_tool_name(remote)

    deploy_dir = os.path.join(deploy_base_path, tool_name, version)
    mf_base = config.mf_base_path or os.path.join(deploy_base_path, "mf")
    mf_dir = os.path.join(mf_base, tool_name)
    mf_file = os.path.join(mf_dir, version)

    # Clone step
    if os.path.isdir(deploy_dir):
        log_error(f"Deploy directory already exists: {deploy_dir}")
        raise SystemExit(1)

    if dry_run:
        log_info(f"[dry-run] Would clone {release_tag} into {deploy_dir}")
    else:
        log_info(f"Cloning {release_tag} into {deploy_dir}...")
        try:
            os.makedirs(os.path.dirname(deploy_dir), exist_ok=True)
        except OSError as e:
            log_error(f"Cannot create deploy directory: {e}")
            raise SystemExit(1)
        try:
            run_git("clone", "--branch", release_tag, "--depth", "1",
                    remote_url, deploy_dir)
        except subprocess.CalledProcessError as e:
            detail = e.stderr.strip() if e.stderr and e.stderr.strip() else str(e)
            log_error(f"Failed to clone {release_tag}: {detail}")
            raise SystemExit(1)
        log_success(f"Cloned {release_tag} into {deploy_dir}")

    # Bootstrap step
    if not dry_run and os.path.isdir(deploy_dir):
        if not run_bootstrap(deploy_dir, dry_run):
            if confirm(f"Remove clone directory due to failed bootstrap: "
                       f"{deploy_dir}?",
                       dry_run=dry_run, non_interactive=non_interactive):
                shutil.rmtree(deploy_dir, ignore_errors=True)
            else:
                log_warn(f"Clone directory left in place: {deploy_dir}")
            raise SystemExit(1)
    elif dry_run:
        run_bootstrap(deploy_dir, dry_run=True)

    # Modulefile step
    if os.path.isfile(mf_file):
        log_error(f"Modulefile already exists: {mf_file}")
        if not dry_run and os.path.isdir(deploy_dir):
            if confirm(f"Remove clone directory due to failed deploy: "
                       f"{deploy_dir}?",
                       dry_run=dry_run, non_interactive=non_interactive):
                shutil.rmtree(deploy_dir, ignore_errors=True)
            else:
                log_warn(f"Clone directory left in place: {deploy_dir}")
        raise SystemExit(1)

    # Check for existing modulefile from previous version
    latest_mf = find_latest_modulefile(mf_dir)

    if latest_mf and os.path.isfile(latest_mf):
        # Copy previous modulefile and update version
        prev_version = os.path.basename(latest_mf)
        copy_and_update_modulefile(latest_mf, mf_file, prev_version, version,
                                   dry_run)
    else:
        # Try template resolution
        template_content = resolve_template(
            deploy_dir=deploy_dir if os.path.isdir(deploy_dir) else "",
            config_template_path=config.modulefile_template,
        )
        root = os.path.join(deploy_base_path, tool_name, version)
        if template_content is not None:
            content = substitute_placeholders(
                template_content, version=version, root=root,
                tool_name=tool_name, deploy_base_path=deploy_base_path,
            )
            write_modulefile(content, mf_file, dry_run)
        else:
            content = generate_default_modulefile(tool_name, version, root)
            write_modulefile(content, mf_file, dry_run)


def main(argv: list = None) -> None:
    if argv is None:
        argv = sys.argv[1:]

    args = parse_args(argv)
    repo_root = get_repo_root()
    config = load_config(
        config_file=args["config_file"],
        repo_root=repo_root,
        cli_deploy_path=args["cli_deploy_path"],
        cli_mf_path=args["cli_mf_path"],
    )

    dry_run = args["dry_run"]
    non_interactive = args["non_interactive"]

    if dry_run:
        log_warn("Running in dry-run mode \u2014 no changes will be made.")
        print("", file=sys.stderr)

    if not config.deploy_base_path:
        log_error("DEPLOY_BASE_PATH is not configured. "
                  "Set it via config file, environment variable, or "
                  "--deploy-path.")
        raise SystemExit(1)

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
        # Interactive version input
        log_info(f"Fetching tags from {config.remote}...")
        run_git("fetch", config.remote, "--tags", "--quiet", check=False)

        latest = get_latest_version(config.tag_prefix)
        while True:
            print("", file=sys.stderr)
            print(f"Latest tag: {config.tag_prefix}{latest}", file=sys.stderr)
            print("", file=sys.stderr)
            try:
                version = input("Enter version to deploy (X.Y.Z, Ctrl+C to cancel): ").strip()
            except (EOFError, KeyboardInterrupt):
                print("", file=sys.stderr)
                raise SystemExit(1)
            if not version:
                log_warn("Version cannot be empty. Try again.")
                continue
            try:
                validate_semver(version)
            except ValueError:
                log_error(f"Invalid semver format: '{version}' (expected X.Y.Z)")
                continue
            # Check tag exists
            result = run_git("rev-parse", f"{config.tag_prefix}{version}",
                             check=False)
            if result.returncode != 0:
                log_warn(f"Tag '{config.tag_prefix}{version}' does not exist. "
                         "Try again.")
                continue
            break

    # Validate tag exists (for CLI mode)
    if args["cli_version"]:
        run_git("fetch", config.remote, "--tags", "--quiet", check=False)
        result = run_git("rev-parse", f"{config.tag_prefix}{version}",
                         check=False)
        if result.returncode != 0:
            log_error(f"Tag '{config.tag_prefix}{version}' does not exist.")
            raise SystemExit(1)

    if not confirm(f"Deploy {config.tag_prefix}{version} to "
                   f"{config.deploy_base_path}?",
                   dry_run=dry_run, non_interactive=non_interactive):
        log_warn("Deploy cancelled.")
        return

    deploy_release(version, config, dry_run, non_interactive)
    log_success(f"Deploy of {config.tag_prefix}{version} completed!")


if __name__ == "__main__":
    main()
