#!/usr/bin/env python3
"""Deploy tool: subcommand-driven deploy via tools.json manifest."""

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
from lib.manifest import (
    load_manifest, save_manifest, get_tool, set_tool_version,
    get_toolset, resolve_manifest_path,
)
from lib.sources import build_adapter, SourceError
from lib.modulefile import (
    resolve_template, substitute_placeholders, generate_default_modulefile,
    write_modulefile, copy_and_update_modulefile, find_latest_modulefile,
    generate_toolset_modulefile,
)
from lib.prompt import confirm


USAGE = """\
Usage: deploy.py <subcommand> [OPTIONS] [ARGS]

Subcommands:
  deploy  <tool> [--version X.Y.Z]    Deploy a tool; update tools.json
  scan                                 Check all tools for newer versions
  upgrade <tool>                       Deploy latest version; update tools.json
  toolset <name> --version X.Y.Z       Write modulefile for a named toolset

Global options:
  --manifest FILE        Path to tools.json manifest
  --config FILE          Path to config file
  --deploy-path PATH     Deploy base path override
  --mf-path PATH         Modulefile base path override
  --dry-run              Show what would be done, no changes
  --non-interactive, -n  Auto-confirm all prompts
  --help, -h             Show this help
"""

USAGE_DEPLOY = """\
Usage: deploy.py deploy <tool> [--version X.Y.Z] [OPTIONS]

Deploy a tool version from its source and update tools.json.
If --version is omitted, uses latest in non-interactive mode or prompts.
"""

USAGE_SCAN = """\
Usage: deploy.py scan [OPTIONS]

Check all tools in tools.json for newer available versions.
In interactive mode, prompts to upgrade selected tools.
"""

USAGE_UPGRADE = """\
Usage: deploy.py upgrade <tool> [OPTIONS]

Deploy the latest available version of a tool and update tools.json.
"""

USAGE_TOOLSET = """\
Usage: deploy.py toolset <name> --version X.Y.Z [OPTIONS]

Write a modulefile for a named toolset using current tool versions from tools.json.
"""


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

    if dry_run:
        log_info(f"[dry-run] Would run bootstrap: {bootstrap_script}")
        return True

    log_info(f"Running bootstrap: {os.path.basename(bootstrap_script)}")

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
        log_error(
            f"Bootstrap failed: {os.path.basename(bootstrap_script)} "
            f"(exit code {e.returncode})"
        )
        log_error(
            f"  To investigate, run it manually: "
            f"{'python3' if bootstrap_script.endswith('.py') else 'bash'} "
            f"{bootstrap_script}"
        )
        return False


def _write_tool_modulefile(
    tool_name: str,
    version: str,
    deploy_root: str,
    config,
    dry_run: bool,
) -> None:
    """Write modulefile for a single deployed tool."""
    mf_base = config.mf_base_path or os.path.join(config.deploy_base_path, "mf")
    mf_dir = os.path.join(mf_base, tool_name)
    mf_file = os.path.join(mf_dir, version)

    if os.path.isfile(mf_file):
        log_error(f"Modulefile already exists: {mf_file}")
        log_error(f"  To replace it, remove it first: rm {mf_file}")
        raise SystemExit(1)

    latest_mf = find_latest_modulefile(mf_dir)
    if latest_mf and os.path.isfile(latest_mf):
        prev_version = os.path.basename(latest_mf)
        copy_and_update_modulefile(
            latest_mf, mf_file, prev_version, version, dry_run
        )
    else:
        template_content = resolve_template(
            deploy_dir=deploy_root if os.path.isdir(deploy_root) else "",
            config_template_path=config.modulefile_template,
        )
        if template_content is not None:
            content = substitute_placeholders(
                template_content,
                version=version,
                root=deploy_root,
                tool_name=tool_name,
                deploy_base_path=config.deploy_base_path,
            )
            write_modulefile(content, mf_file, dry_run)
        else:
            content = generate_default_modulefile(tool_name, version, deploy_root)
            write_modulefile(content, mf_file, dry_run)


def _prompt_version_interactive(
    tool_name: str,
    current_version: str,
    available: list,
) -> str:
    """Present a numbered version menu and return the chosen version string."""
    total = len(available)
    shown = available[-10:]  # show up to 10 most recent

    print("", file=sys.stderr)
    current_label = current_version if current_version else "(none)"
    print(f"  Tool:              {tool_name}", file=sys.stderr)
    print(f"  Currently at:      {current_label}", file=sys.stderr)
    if total > len(shown):
        print(
            f"  Available:         {total} versions — showing latest {len(shown)}:",
            file=sys.stderr,
        )
    else:
        print(
            f"  Available:         {total} version(s):",
            file=sys.stderr,
        )

    for i, v in enumerate(shown, 1):
        marker = " ← latest" if i == len(shown) else ""
        current_marker = " ← current" if v == current_version else ""
        print(f"    {i:2}. {v}{marker}{current_marker}", file=sys.stderr)
    print("", file=sys.stderr)

    while True:
        hint = f"latest: {shown[-1]}"
        try:
            raw = input(
                f"  Enter a number or version [{hint}, Ctrl+C to cancel]: "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            print("", file=sys.stderr)
            raise SystemExit(1)

        if not raw:
            # Default to latest
            version = shown[-1]
            log_info(f"Defaulting to latest: {version}")
            return version

        # Try to interpret as a list index
        try:
            idx = int(raw)
            if 1 <= idx <= len(shown):
                return shown[idx - 1]
            log_warn(
                f"  {idx} is out of range — enter 1–{len(shown)} "
                f"or a version string."
            )
            continue
        except ValueError:
            pass

        # Treat as a literal version string
        try:
            validate_semver(raw)
        except ValueError:
            log_error(f"  '{raw}' is not a valid semver string (expected X.Y.Z).")
            continue
        if raw not in available:
            # Be helpful: show what's close
            log_warn(f"  Version {raw} is not in the available list.")
            log_warn(f"  Available: {', '.join(available[-5:])}")
            continue
        return raw


def cmd_deploy(
    tool_name: str,
    version_arg: str,
    args: dict,
    config,
) -> None:
    """Deploy subcommand: deploy a tool version and update tools.json."""
    manifest_path = resolve_manifest_path(config)
    data = load_manifest(manifest_path)
    tool_entry = get_tool(data, tool_name)

    if not config.deploy_base_path:
        log_error(
            "DEPLOY_BASE_PATH is not configured. "
            "Set it via --deploy-path, the DEPLOY_BASE_PATH env var, "
            "or DEPLOY_BASE_PATH=/opt/tools in ~/.release.conf."
        )
        raise SystemExit(1)

    if not os.path.isabs(config.deploy_base_path):
        log_error(
            f"DEPLOY_BASE_PATH must be an absolute path: {config.deploy_base_path}"
        )
        raise SystemExit(1)

    source_type = tool_entry["source"]["type"]
    adapter = build_adapter(tool_entry, tag_prefix=config.tag_prefix)
    dry_run = args["dry_run"]
    non_interactive = args["non_interactive"]
    current_version = tool_entry.get("version", "")

    # ---------------------------------------------------------------- version
    if version_arg:
        try:
            validate_semver(version_arg)
        except ValueError:
            log_error(f"Invalid version: '{version_arg}' (expected X.Y.Z)")
            raise SystemExit(1)

        # For git sources, validate the tag exists before attempting a clone
        if source_type == "git":
            log_info(f"Checking available tags for {tool_name}...")
            try:
                available = adapter.get_available_versions()
            except SourceError as e:
                log_error(str(e))
                raise SystemExit(1)
            if version_arg not in available:
                avail_str = (
                    ", ".join(available[-5:]) if available else "(none)"
                )
                log_error(
                    f"Version {version_arg} is not a published tag for "
                    f"{tool_name}."
                )
                log_error(f"  Latest available: {avail_str}")
                raise SystemExit(1)

        version = version_arg

    else:
        try:
            available = adapter.get_available_versions()
        except SourceError as e:
            log_error(str(e))
            raise SystemExit(1)

        if not available:
            log_error(f"No versions available for tool '{tool_name}'")
            raise SystemExit(1)

        if non_interactive:
            version = available[-1]
            log_info(f"Selecting latest available version: {version}")
        else:
            version = _prompt_version_interactive(
                tool_name, current_version, available
            )

    # --------------------------------------------------- pre-deploy checks
    if source_type == "git":
        deploy_root_expected = os.path.join(
            config.deploy_base_path, tool_name, version
        )
        if os.path.isdir(deploy_root_expected):
            log_error(
                f"Deploy directory already exists: {deploy_root_expected}"
            )
            log_error(
                f"  To reinstall, remove it first: rm -rf {deploy_root_expected}"
            )
            raise SystemExit(1)
        dest_label = deploy_root_expected
    else:
        dest_label = os.path.join(tool_entry["source"]["path"], version)

    if not confirm(
        f"Deploy {tool_name} {version} ({source_type}) → {dest_label}?",
        dry_run=dry_run,
        non_interactive=non_interactive,
    ):
        log_warn("Deploy cancelled.")
        return

    # ------------------------------------------------------------- deploy
    try:
        deploy_root = adapter.deploy(
            version, config.deploy_base_path, tool_name, dry_run
        )
    except SourceError as e:
        log_error(str(e))
        raise SystemExit(1)

    # --------------------------------------------------------- bootstrap
    if source_type == "git" and not dry_run and os.path.isdir(deploy_root):
        if not run_bootstrap(deploy_root, dry_run):
            if confirm(
                f"Remove clone directory due to failed bootstrap: {deploy_root}?",
                dry_run=dry_run,
                non_interactive=non_interactive,
            ):
                shutil.rmtree(deploy_root, ignore_errors=True)
            else:
                log_warn(f"Clone directory left in place: {deploy_root}")
            raise SystemExit(1)
    elif source_type == "git" and dry_run:
        run_bootstrap(deploy_root, dry_run=True)

    # ------------------------------------------------------ modulefile
    try:
        _write_tool_modulefile(tool_name, version, deploy_root, config, dry_run)
    except SystemExit:
        if source_type == "git" and not dry_run and os.path.isdir(deploy_root):
            if confirm(
                f"Remove clone directory due to failed deploy: {deploy_root}?",
                dry_run=dry_run,
                non_interactive=non_interactive,
            ):
                shutil.rmtree(deploy_root, ignore_errors=True)
            else:
                log_warn(f"Clone directory left in place: {deploy_root}")
        raise

    # -------------------------------------------------- update manifest
    if not dry_run:
        set_tool_version(data, tool_name, version)
        save_manifest(manifest_path, data)
        log_success(f"Updated {tool_name} version to {version} in manifest")

    log_success(f"Deployed {tool_name} {version}")


def _compare_versions(current: str, available: list) -> tuple:
    """Return (latest_version, bump_type) comparing current against available.

    bump_type values:
        "up-to-date"  current is the latest available
        "ahead"       current is newer than latest available (e.g. after rollback)
        "patch"       patch-level upgrade available
        "minor"       minor-level upgrade available
        "major"       major-level upgrade available
        "unknown"     version strings could not be parsed
    """
    if not available:
        return current, "unknown"
    latest = available[-1]
    if not current:
        return latest, "new"
    if latest == current:
        return latest, "up-to-date"
    try:
        cur = tuple(int(x) for x in current.split("."))
        lat = tuple(int(x) for x in latest.split("."))
    except ValueError:
        return latest, "unknown"
    if lat < cur:
        return latest, "ahead"
    if lat[0] > cur[0]:
        bump = "major"
    elif lat[1] > cur[1]:
        bump = "minor"
    else:
        bump = "patch"
    return latest, bump


def cmd_scan(args: dict, config) -> None:
    """Scan subcommand: check all tools for newer versions."""
    manifest_path = resolve_manifest_path(config)
    data = load_manifest(manifest_path)
    tools = data.get("tools", {})

    if not tools:
        log_info("No tools in manifest.")
        return

    # Collect rows — note errors as strings so they appear in the table
    rows = []  # (name, current, latest, bump, error_detail)
    for name in sorted(tools.keys()):
        tool_entry = tools[name]
        current = tool_entry.get("version", "")
        adapter = build_adapter(tool_entry, tag_prefix=config.tag_prefix)
        try:
            available = adapter.get_available_versions()
        except SourceError as e:
            rows.append((name, current, "?", "error", str(e)))
            continue
        latest, bump = _compare_versions(current, available)
        rows.append((name, current, latest, bump, ""))

    # ----------------------------------------------------------------- table
    # Printed to stdout so it can be piped / grepped.
    print("")
    w_name = max(len(r[0]) for r in rows)
    w_ver  = max(len(r[1]) if r[1] else len("(none)") for r in rows)
    for name, current, latest, bump, err in rows:
        cur_label = current if current else "(none)"
        pad_name  = f"{name:<{w_name}}"
        pad_cur   = f"{cur_label:<{w_ver}}"
        if bump == "up-to-date":
            print(f"  {pad_name}  {pad_cur}  (up to date)")
        elif bump == "ahead":
            print(f"  {pad_name}  {pad_cur}  (ahead of latest: {latest})")
        elif bump == "error":
            print(f"  {pad_name}  {pad_cur}  \u26a0 error: {err}")
        else:
            print(f"  {pad_name}  {pad_cur}  \u2192  {latest}  ({bump})")
    print("")

    if args["non_interactive"]:
        return

    # -------------------------------------------------------- upgrade prompt
    upgradable = [
        (name, current, latest)
        for name, current, latest, bump, _ in rows
        if bump not in ("up-to-date", "ahead", "error", "unknown")
    ]
    if not upgradable:
        log_info("All tools are up to date.")
        return

    print("Upgrades available:", file=sys.stderr)
    for i, (name, current, latest) in enumerate(upgradable, 1):
        cur_label = current if current else "(none)"
        print(
            f"  {i:2}. {name}  {cur_label} \u2192 {latest}",
            file=sys.stderr,
        )
    print(
        "\n  Enter numbers to upgrade (space or comma separated),",
        file=sys.stderr,
    )
    print(
        '  "all" to upgrade everything, or blank to skip:',
        file=sys.stderr,
    )

    try:
        selection = input("  > ").strip()
    except (EOFError, KeyboardInterrupt):
        print("", file=sys.stderr)
        return

    if not selection:
        log_info("No upgrades selected.")
        return

    # Resolve selection → set of 0-based indices
    if selection.lower() == "all":
        selected_indices = set(range(len(upgradable)))
    else:
        selected_indices = set()
        invalid = []
        for part in selection.replace(",", " ").split():
            try:
                idx = int(part)
                if 1 <= idx <= len(upgradable):
                    selected_indices.add(idx - 1)
                else:
                    invalid.append(part)
            except ValueError:
                invalid.append(part)
        if invalid:
            log_warn(
                f"Ignoring unrecognised selection(s): {', '.join(invalid)} "
                f"(valid range: 1\u2013{len(upgradable)})"
            )

    if not selected_indices:
        log_info("No valid upgrades selected.")
        return

    # Echo planned upgrades and confirm before starting
    print("", file=sys.stderr)
    print("Will upgrade:", file=sys.stderr)
    planned = [upgradable[i] for i in sorted(selected_indices)]
    for name, current, latest in planned:
        cur_label = current if current else "(none)"
        print(f"  {name}  {cur_label} \u2192 {latest}", file=sys.stderr)

    if not confirm(
        "Proceed with the upgrades above?",
        dry_run=args["dry_run"],
        non_interactive=args["non_interactive"],
    ):
        log_warn("Upgrades cancelled.")
        return

    for name, _, latest in planned:
        cmd_deploy(name, latest, args, config)


def cmd_upgrade(tool_name: str, args: dict, config) -> None:
    """Upgrade subcommand: deploy the latest available version."""
    manifest_path = resolve_manifest_path(config)
    data = load_manifest(manifest_path)
    tool_entry = get_tool(data, tool_name)

    adapter = build_adapter(tool_entry, tag_prefix=config.tag_prefix)
    try:
        available = adapter.get_available_versions()
    except SourceError as e:
        log_error(str(e))
        raise SystemExit(1)

    if not available:
        log_error(f"No versions available for tool '{tool_name}'")
        raise SystemExit(1)

    latest = available[-1]
    current = tool_entry.get("version", "")
    if current == latest:
        log_info(f"{tool_name} is already at the latest version ({latest})")
        return

    current_label = current if current else "(none)"
    log_info(f"Upgrading {tool_name}: {current_label} \u2192 {latest}")
    cmd_deploy(tool_name, latest, args, config)


def cmd_toolset(name: str, version: str, args: dict, config) -> None:
    """Toolset subcommand: write modulefile for a named toolset."""
    if not version:
        log_error("--version is required for the toolset subcommand")
        raise SystemExit(1)

    try:
        validate_semver(version)
    except ValueError:
        log_error(f"Invalid version: '{version}' (expected X.Y.Z)")
        raise SystemExit(1)

    deploy_base_path = config.deploy_base_path or ""
    if not config.mf_base_path and not deploy_base_path:
        log_error(
            "Either --mf-path or --deploy-path (or DEPLOY_BASE_PATH) must be "
            "set to determine where to write the toolset modulefile."
        )
        raise SystemExit(1)

    manifest_path = resolve_manifest_path(config)
    data = load_manifest(manifest_path)
    ts_tools = get_toolset(data, name)

    # Build tool_versions dict; warn loudly about missing or empty versions
    tool_versions = {}
    missing_versions = []
    for tool_name in ts_tools:
        if tool_name not in data["tools"]:
            log_warn(f"Toolset tool '{tool_name}' not found in manifest — skipping")
            continue
        ver = data["tools"][tool_name].get("version", "")
        if not ver:
            missing_versions.append(tool_name)
        else:
            tool_versions[tool_name] = ver

    if missing_versions:
        log_error(
            f"The following tools in toolset '{name}' have no deployed "
            f"version recorded: {', '.join(missing_versions)}"
        )
        log_error(
            "Deploy them first with: "
            + "  ".join(f"deploy.sh deploy {t}" for t in missing_versions)
        )
        raise SystemExit(1)

    content = generate_toolset_modulefile(
        toolset_name=name,
        version=version,
        deploy_base_path=deploy_base_path,
        tool_versions=tool_versions,
    )

    mf_base = config.mf_base_path or os.path.join(deploy_base_path, "mf")
    mf_file = os.path.join(mf_base, name, version)

    dry_run = args["dry_run"]
    if not dry_run and os.path.isfile(mf_file):
        log_error(f"Toolset modulefile already exists: {mf_file}")
        log_error(f"  To replace it, remove it first: rm {mf_file}")
        raise SystemExit(1)

    write_modulefile(content, mf_file, dry_run)
    if not dry_run:
        log_success(f"Toolset modulefile written: {mf_file}")


def parse_global_args(argv: list) -> tuple:
    """Parse global flags, returning (remaining_args, global_args_dict).

    --help / -h are intentionally left in remaining so that subcommand
    dispatch can print subcommand-specific help text.
    """
    global_args = {
        "dry_run": False,
        "config_file": "",
        "cli_manifest": "",
        "cli_deploy_path": "",
        "cli_mf_path": "",
        "non_interactive": False,
    }

    remaining = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--dry-run":
            global_args["dry_run"] = True
        elif arg == "--config":
            i += 1
            if i >= len(argv):
                log_error("--config requires a file path")
                raise SystemExit(1)
            global_args["config_file"] = argv[i]
        elif arg == "--manifest":
            i += 1
            if i >= len(argv):
                log_error("--manifest requires a file path")
                raise SystemExit(1)
            global_args["cli_manifest"] = argv[i]
        elif arg == "--deploy-path":
            i += 1
            if i >= len(argv):
                log_error("--deploy-path requires a path")
                raise SystemExit(1)
            global_args["cli_deploy_path"] = argv[i]
        elif arg == "--mf-path":
            i += 1
            if i >= len(argv):
                log_error("--mf-path requires a path")
                raise SystemExit(1)
            global_args["cli_mf_path"] = argv[i]
        elif arg in ("--non-interactive", "-n"):
            global_args["non_interactive"] = True
        else:
            remaining.append(arg)
        i += 1

    return remaining, global_args


def _parse_subcommand_version(sub_remaining: list) -> tuple:
    """Parse --version from sub-command remaining args; return (version, rest).

    rest contains any args that were not --version <val>.  Callers should
    reject non-empty rest as unexpected arguments.
    """
    version = ""
    rest = []
    i = 0
    while i < len(sub_remaining):
        if sub_remaining[i] == "--version":
            i += 1
            if i >= len(sub_remaining):
                log_error("--version requires a value")
                raise SystemExit(1)
            version = sub_remaining[i]
        else:
            rest.append(sub_remaining[i])
        i += 1
    return version, rest


def main(argv: list = None) -> None:
    if argv is None:
        argv = sys.argv[1:]

    if not argv or argv[0] in ("--help", "-h"):
        print(USAGE)
        raise SystemExit(0)

    remaining, args = parse_global_args(argv)

    if not remaining:
        log_error("No subcommand given.")
        print(USAGE)
        raise SystemExit(1)

    subcommand = remaining[0]
    sub_remaining = remaining[1:]

    # Handle help flags that appear after other global flags
    # e.g. deploy.py --dry-run --help
    if subcommand in ("--help", "-h"):
        print(USAGE)
        raise SystemExit(0)

    # Load config (repo_root is best-effort; tools.json doesn't need a git repo)
    from lib.git import get_repo_root
    try:
        repo_root = get_repo_root()
    except Exception:
        repo_root = ""

    config = load_config(
        config_file=args["config_file"],
        repo_root=repo_root,
        cli_deploy_path=args["cli_deploy_path"],
        cli_mf_path=args["cli_mf_path"],
        cli_manifest=args["cli_manifest"],
    )

    if args["dry_run"]:
        log_warn("Running in dry-run mode \u2014 no changes will be made.")
        print("", file=sys.stderr)

    # ------------------------------------------------------------------ deploy
    if subcommand == "deploy":
        if "--help" in sub_remaining or "-h" in sub_remaining:
            print(USAGE_DEPLOY)
            raise SystemExit(0)
        if not sub_remaining:
            log_error("deploy requires a tool name")
            print(USAGE_DEPLOY)
            raise SystemExit(1)
        tool_name = sub_remaining[0]
        if tool_name.startswith("-"):
            log_error(f"Expected a tool name, got option: {tool_name}")
            print(USAGE_DEPLOY)
            raise SystemExit(1)
        version_arg, rest = _parse_subcommand_version(sub_remaining[1:])
        if rest:
            log_error(f"Unexpected argument(s): {' '.join(rest)}")
            print(USAGE_DEPLOY)
            raise SystemExit(1)
        cmd_deploy(tool_name, version_arg, args, config)

    # ------------------------------------------------------------------- scan
    elif subcommand == "scan":
        if "--help" in sub_remaining or "-h" in sub_remaining:
            print(USAGE_SCAN)
            raise SystemExit(0)
        if sub_remaining:
            log_error(f"scan takes no arguments, got: {' '.join(sub_remaining)}")
            print(USAGE_SCAN)
            raise SystemExit(1)
        cmd_scan(args, config)

    # ----------------------------------------------------------------- upgrade
    elif subcommand == "upgrade":
        if "--help" in sub_remaining or "-h" in sub_remaining:
            print(USAGE_UPGRADE)
            raise SystemExit(0)
        if not sub_remaining:
            log_error("upgrade requires a tool name")
            print(USAGE_UPGRADE)
            raise SystemExit(1)
        tool_name = sub_remaining[0]
        if tool_name.startswith("-"):
            log_error(f"Expected a tool name, got option: {tool_name}")
            print(USAGE_UPGRADE)
            raise SystemExit(1)
        if len(sub_remaining) > 1:
            log_error(f"Unexpected argument(s): {' '.join(sub_remaining[1:])}")
            print(USAGE_UPGRADE)
            raise SystemExit(1)
        cmd_upgrade(tool_name, args, config)

    # ----------------------------------------------------------------- toolset
    elif subcommand == "toolset":
        if "--help" in sub_remaining or "-h" in sub_remaining:
            print(USAGE_TOOLSET)
            raise SystemExit(0)
        if not sub_remaining:
            log_error("toolset requires a name")
            print(USAGE_TOOLSET)
            raise SystemExit(1)
        ts_name = sub_remaining[0]
        if ts_name.startswith("-"):
            log_error(f"Expected a toolset name, got option: {ts_name}")
            print(USAGE_TOOLSET)
            raise SystemExit(1)
        version_arg, rest = _parse_subcommand_version(sub_remaining[1:])
        if rest:
            log_error(f"Unexpected argument(s): {' '.join(rest)}")
            print(USAGE_TOOLSET)
            raise SystemExit(1)
        cmd_toolset(ts_name, version_arg, args, config)

    else:
        log_error(f"Unknown subcommand: {subcommand}")
        print(USAGE)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
