#!/usr/bin/env python3
"""Deploy tool: manifest-driven tool deployment with Environment Modules support.

Subcommands:

    deploy <tool> [--version X.Y.Z]
        Deploy a single tool version. Clones (git), extracts (archive), or
        references in place (external). Runs bootstrap if configured, writes
        a modulefile, and updates tools.json.

    scan
        Query all sources for available versions, write them to the manifest's
        'available' field, and print an upgrade table. Interactive mode offers
        to upgrade selected tools.

    upgrade <tool>
        Shortcut: deploy the latest available version of a tool.

    toolset <name> [--version X.Y.Z]
        Write a combined modulefile that loads all tools in a named toolset.
        Supports both legacy list format and dict format with version pins.

    apply [--toolset <name>]
        Declarative deploy: read dict-format toolsets, deploy every tool+version
        pair not already on disk, and write toolset modulefiles. This is the
        "reconcile" step in a GitOps workflow.

Source adapters (see lib/sources.py) handle the actual version discovery
and deployment for each source type (git, archive, external).
"""

import fcntl
import os
import re
import signal
import shutil
import subprocess
import sys
import time

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from lib.log import log_info, log_warn, log_error, log_success
from lib.config import load_config
from lib.semver import validate_semver
from lib.manifest import (
    load_manifest, save_manifest, get_tool, set_tool_version,
    get_toolset, get_toolset_tool_versions, get_toolset_version,
    set_tool_available, resolve_manifest_path, collect_string_vars,
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
  toolset <name> [--version X.Y.Z]    Write modulefile for a named toolset
  apply   [--toolset <name>]           Deploy all versions referenced by toolsets

Global options:
  --manifest FILE        Path to tools.json manifest
  --config FILE          Path to config file
  --deploy-path PATH     Deploy base path override
  --mf-path PATH         Modulefile base path override
  --dry-run              Show what would be done, no changes
  --non-interactive, -n  Auto-confirm all prompts
  --force                Override deploy protection for external source tools
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

USAGE_APPLY = """\
Usage: deploy.py apply [--toolset <name>] [OPTIONS]

Deploy all tool versions referenced by toolsets that are not yet installed.
Reads version pins from dict-format toolsets in tools.json, checks which
versions are already on disk, and deploys the missing ones.

Options:
  --toolset <name>       Apply only this toolset (default: all)
"""


def _resolve_path_template(
    template: str,
    tool_name: str,
    version: str,
    user_vars: dict[str, str] | None = None,
) -> str:
    """Resolve ``{{...}}`` placeholders in a path template.

    *user_vars* carries custom string-valued keys collected from the manifest
    via :func:`collect_string_vars`.  Built-in variables (``toolname`` and
    ``version``) always take precedence over user-defined ones.

    Raises :class:`SystemExit` if any ``{{...}}`` placeholders remain
    unresolved after substitution.
    """
    merged = dict(user_vars) if user_vars else {}
    merged["toolname"] = tool_name
    merged["version"] = version

    def _replacer(match: re.Match) -> str:
        key = match.group(1)
        if key in merged:
            return merged[key]
        return match.group(0)

    result = re.sub(r"\{\{(\w+)\}\}", _replacer, template)

    unresolved = re.findall(r"\{\{(\w+)\}\}", result)
    if unresolved:
        log_error(
            f"Unresolved placeholders in path template: "
            f"{', '.join('{{' + p + '}}' for p in unresolved)}"
        )
        raise SystemExit(1)

    return result


def _check_no_symlink(path: str, label: str) -> None:
    """Abort if *path* or any ancestor inside the deploy tree is a symlink."""
    p = path
    while p and p != "/":
        if os.path.islink(p):
            log_error(
                f"{label} path contains a symlink: {p}  "
                f"— refusing to continue."
            )
            raise SystemExit(1)
        p = os.path.dirname(p)


def _is_inside(path: str, base: str) -> bool:
    """Return True if *path* resolves to a location inside *base*."""
    real_path = os.path.realpath(path)
    real_base = os.path.realpath(base)
    return real_path.startswith(real_base + os.sep) or real_path == real_base


def _safe_cleanup(
    deploy_root: str,
    source_version_dir: str,
    deploy_base_path: str,
    reason: str,
    dry_run: bool,
    non_interactive: bool,
) -> None:
    """Remove a deploy directory only if it is safe to do so.

    Refuses to remove the source version directory itself or any path
    that resolves outside deploy_base_path.
    """
    if dry_run or not os.path.isdir(deploy_root):
        return
    if deploy_root == source_version_dir:
        return
    if not _is_inside(deploy_root, deploy_base_path):
        log_warn(
            f"Refusing to remove {deploy_root}: "
            f"resolves outside deploy base path ({deploy_base_path})"
        )
        return
    if confirm(
        f"Remove deploy directory due to {reason}: {deploy_root}?",
        dry_run=dry_run,
        non_interactive=non_interactive,
    ):
        shutil.rmtree(deploy_root, ignore_errors=True)
    else:
        log_warn(f"Deploy directory left in place: {deploy_root}")


_LOCK_STALE_SECONDS = 3600  # 1 hour


# Global state for signal-handler cleanup
_active_lock_fd = None
_active_lock_path = None


def _lock_signal_handler(signum, frame):
    """Clean up lock file on SIGTERM/SIGINT before exiting."""
    _release_deploy_lock_if_active()
    sys.exit(128 + signum)


def _release_deploy_lock_if_active():
    """Release global lock if one is held (for signal handler use)."""
    global _active_lock_fd, _active_lock_path
    if _active_lock_fd is not None:
        _release_deploy_lock(_active_lock_fd, _active_lock_path)
        _active_lock_fd = None
        _active_lock_path = None


def _acquire_deploy_lock(tool_name: str, base_path: str) -> int:
    """Acquire an advisory file lock for deploying a tool.

    Returns (file_descriptor, lock_path).  Warns if the lock file
    appears stale (mtime older than _LOCK_STALE_SECONDS).
    """
    global _active_lock_fd, _active_lock_path
    lock_dir = os.path.join(base_path, tool_name)
    os.makedirs(lock_dir, exist_ok=True)
    lock_path = os.path.join(lock_dir, ".deploy.lock")

    # Warn about possibly stale lock before blocking
    if os.path.exists(lock_path):
        try:
            age = time.time() - os.path.getmtime(lock_path)
            if age > _LOCK_STALE_SECONDS:
                log_warn(
                    f"Lock file is {int(age // 3600)}h old and may be stale: "
                    f"{lock_path}  — remove manually if no deploy is running."
                )
        except OSError:
            pass

    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        log_error(
            f"Another deploy of {tool_name} may be in progress "
            f"(lock: {lock_path})"
        )
        raise SystemExit(1)

    # Update mtime so staleness detection works for the next caller
    try:
        os.utime(lock_path)
    except OSError:
        pass

    # Register for signal-based cleanup
    _active_lock_fd = fd
    _active_lock_path = lock_path
    signal.signal(signal.SIGTERM, _lock_signal_handler)
    signal.signal(signal.SIGINT, _lock_signal_handler)

    return fd, lock_path


def _release_deploy_lock(fd: int, lock_path: str) -> None:
    """Release an advisory deploy lock and remove the lock file."""
    global _active_lock_fd, _active_lock_path
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
    try:
        os.unlink(lock_path)
    except OSError:
        pass
    _active_lock_fd = None
    _active_lock_path = None


def run_bootstrap(
    command: str,
    deploy_dir: str,
    version: str,
    tool_name: str,
    dry_run: bool = False,
) -> bool:
    """Run an explicit bootstrap command in the deploy directory.

    Returns True if bootstrap ran successfully.
    Returns False if bootstrap failed.
    """
    if not command:
        return True

    if dry_run:
        log_info(f"[dry-run] Would run bootstrap: {command}")
        return True

    log_info(f"Running bootstrap: {command}")
    env = os.environ.copy()
    env["INSTALL_PATH"] = deploy_dir
    env["TOOL_VERSION"] = version
    env["TOOL_NAME"] = tool_name

    try:
        subprocess.run(
            command, shell=True, cwd=deploy_dir, check=True, env=env,
        )
        log_success(f"Bootstrap completed: {command}")
        return True
    except subprocess.CalledProcessError as e:
        log_error(f"Bootstrap failed: {command} (exit code {e.returncode})")
        return False


def _write_tool_modulefile(
    tool_name: str,
    version: str,
    deploy_root: str,
    config,
    dry_run: bool,
    install_path: str | None = None,
    mf_path: str | None = None,
    overwrite: bool = False,
) -> None:
    """Write modulefile for a single deployed tool."""
    if mf_path:
        mf_file = mf_path
        mf_dir = os.path.dirname(mf_file)
    else:
        mf_base = config.mf_base_path or os.path.join(config.deploy_base_path, "mf")
        mf_dir = os.path.join(mf_base, tool_name)
        mf_file = os.path.join(mf_dir, version)

    # Use install_path as root for modulefile placeholders if provided
    root = install_path or deploy_root

    latest_mf = find_latest_modulefile(mf_dir)
    if latest_mf and os.path.isfile(latest_mf) and not overwrite:
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
                root=root,
                tool_name=tool_name,
                deploy_base_path=config.deploy_base_path,
            )
            write_modulefile(content, mf_file, dry_run, overwrite=overwrite)
        else:
            content = generate_default_modulefile(tool_name, version, root)
            write_modulefile(content, mf_file, dry_run, overwrite=overwrite)


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


def _apply_manifest_deploy_base(config, data: dict) -> None:
    """Fall back to manifest deploy_base_path when CLI did not set one.

    Root-level custom string vars are resolved in the path.  ``{{toolname}}``
    and ``{{version}}`` are **not** available at this scope.
    """
    if not config.deploy_base_path:
        manifest_path = data.get("deploy_base_path", "")
        if manifest_path:
            root_vars = {k: v.strip() for k, v in data.items()
                         if isinstance(v, str)}

            def _replacer(match: re.Match) -> str:
                return root_vars.get(match.group(1), match.group(0))

            manifest_path = re.sub(r"\{\{(\w+)\}\}", _replacer, manifest_path)
            unresolved = re.findall(r"\{\{(\w+)\}\}", manifest_path)
            if unresolved:
                log_error(
                    f"deploy_base_path contains unresolved placeholders: "
                    f"{', '.join('{{' + p + '}}' for p in unresolved)}. "
                    f"Only root-level string keys are available here."
                )
                raise SystemExit(1)
            config.deploy_base_path = manifest_path


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
    _apply_manifest_deploy_base(config, data)

    if tool_entry["source"]["type"] == "external" and not args["force"]:
        log_error(
            f"Tool '{tool_name}' is externally managed (source type: external). "
            f"Use --force to deploy anyway."
        )
        raise SystemExit(1)

    if not config.deploy_base_path:
        log_error(
            "No deploy base path configured. "
            "Set 'deploy_base_path' in tools.json or pass --deploy-path."
        )
        raise SystemExit(1)

    if not os.path.isabs(config.deploy_base_path):
        log_error(
            f"deploy_base_path must be an absolute path: {config.deploy_base_path}"
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

    # ------------------------------------------------- resolve custom paths
    user_vars = collect_string_vars(data, tool_entry)
    raw_install_path = tool_entry.get("install_path")
    if raw_install_path:
        resolved_install_path = _resolve_path_template(
            raw_install_path, tool_name, version, user_vars=user_vars
        )
        if not os.path.isabs(resolved_install_path):
            resolved_install_path = os.path.join(
                config.deploy_base_path, resolved_install_path
            )
        if not os.path.isabs(resolved_install_path):
            log_error(
                f"Resolved install_path is not absolute: {resolved_install_path}. "
                f"Provide --deploy-path or set deploy_base_path in tools.json."
            )
            raise SystemExit(1)
    else:
        resolved_install_path = None

    raw_mf_path = tool_entry.get("mf_path")
    if raw_mf_path:
        resolved_mf_path = _resolve_path_template(
            raw_mf_path, tool_name, version, user_vars=user_vars
        )
        if not os.path.isabs(resolved_mf_path):
            resolved_mf_path = os.path.join(
                config.deploy_base_path, resolved_mf_path
            )
        if not os.path.isabs(resolved_mf_path):
            log_error(
                f"Resolved mf_path is not absolute: {resolved_mf_path}. "
                f"Provide --deploy-path or set deploy_base_path in tools.json."
            )
            raise SystemExit(1)
    else:
        resolved_mf_path = None

    flatten_archive = tool_entry.get("flatten_archive", True)

    # --------------------------------------------------- pre-deploy checks
    if resolved_install_path:
        deploy_target = resolved_install_path
    elif source_type == "external":
        # External: no new directory created — tool is already in place
        deploy_target = None
    elif source_type in ("git", "archive"):
        deploy_target = os.path.join(
            config.deploy_base_path, tool_name, version
        )
    else:
        deploy_target = None

    if deploy_target:
        dest_label = deploy_target
    else:
        dest_label = os.path.join(tool_entry["source"]["path"], version)

    if not confirm(
        f"Deploy {tool_name} {version} ({source_type}) → {dest_label}?",
        dry_run=dry_run,
        non_interactive=non_interactive,
    ):
        log_warn("Deploy cancelled.")
        return

    lock_fd = None
    lock_path = None
    if not dry_run and config.deploy_base_path:
        lock_fd, lock_path = _acquire_deploy_lock(tool_name, config.deploy_base_path)
    try:
        # Re-check directory existence *inside* the lock to avoid TOCTOU
        if deploy_target and not dry_run:
            if os.path.islink(deploy_target):
                log_error(
                    f"Deploy target is a symlink: {deploy_target}  "
                    f"— refusing to continue."
                )
                raise SystemExit(1)
            if os.path.isdir(deploy_target):
                log_error(f"Deploy directory already exists: {deploy_target}")
                log_error(
                    f"  To reinstall, remove it first: rm -rf {deploy_target}"
                )
                raise SystemExit(1)
        # ------------------------------------------------------------- deploy
        try:
            if source_type == "archive":
                deploy_root = adapter.deploy(
                    version, config.deploy_base_path, tool_name, dry_run,
                    install_path=resolved_install_path,
                    flatten_archive=flatten_archive,
                )
            else:
                deploy_root = adapter.deploy(
                    version, config.deploy_base_path, tool_name, dry_run,
                    install_path=resolved_install_path,
                )
        except SourceError as e:
            log_error(str(e))
            raise SystemExit(1)

        # --------------------------------------------------------- bootstrap
        # Guard: never offer to remove the external source version dir itself
        source_version_dir = os.path.join(
            tool_entry.get("source", {}).get("path", ""), version
        )
        bootstrap_cmd = tool_entry.get("bootstrap", "")
        if bootstrap_cmd and not dry_run and os.path.isdir(deploy_root):
            if not run_bootstrap(bootstrap_cmd, deploy_root, version, tool_name, dry_run):
                _safe_cleanup(deploy_root, source_version_dir,
                              config.deploy_base_path, "failed bootstrap",
                              dry_run, non_interactive)
                raise SystemExit(1)
        elif bootstrap_cmd and dry_run:
            run_bootstrap(bootstrap_cmd, deploy_root, version, tool_name, dry_run=True)

        # ------------------------------------------------------ modulefile
        try:
            _write_tool_modulefile(
                tool_name, version, deploy_root, config, dry_run,
                install_path=resolved_install_path,
                mf_path=resolved_mf_path,
            )
        except SystemExit:
            _safe_cleanup(deploy_root, source_version_dir,
                          config.deploy_base_path, "failed deploy",
                          dry_run, non_interactive)
            raise

        # -------------------------------------------------- update manifest
        if not dry_run:
            set_tool_version(data, tool_name, version)
            save_manifest(manifest_path, data)
            log_success(f"Updated {tool_name} version to {version} in manifest")

        log_success(f"Deployed {tool_name} {version}")

        # ------------------------------------------------ toolset update hint
        toolsets = data.get("toolsets", {})
        matching_toolsets = [
            ts_name for ts_name, ts_tools in toolsets.items()
            if tool_name in ts_tools
        ]
        if matching_toolsets:
            ts_list = ", ".join(matching_toolsets)
            log_info(
                f"Tool {tool_name} is in toolset(s): {ts_list}. "
                f"Toolset modulefiles may need updating."
            )
            if not non_interactive:
                for ts_name in matching_toolsets:
                    if confirm(
                        f"Update toolset '{ts_name}' modulefile?",
                        dry_run=dry_run,
                        non_interactive=non_interactive,
                    ):
                        try:
                            ts_version = input(
                                f"  Enter version for toolset '{ts_name}': "
                            ).strip()
                        except (EOFError, KeyboardInterrupt):
                            print("", file=sys.stderr)
                            continue
                        if ts_version:
                            try:
                                cmd_toolset(ts_name, ts_version, args, config)
                            except SystemExit:
                                log_warn(f"Toolset '{ts_name}' update failed — continuing.")
    finally:
        if lock_fd is not None:
            _release_deploy_lock(lock_fd, lock_path)


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
    _apply_manifest_deploy_base(config, data)
    tools = data.get("tools", {})

    if not tools:
        log_info("No tools in manifest.")
        return

    # Collect rows — note errors as strings so they appear in the table
    rows = []  # (name, current, latest, bump, error_detail, external)
    for name in sorted(tools.keys()):
        tool_entry = tools[name]
        current = tool_entry.get("version", "")
        external = tool_entry["source"]["type"] == "external"
        adapter = build_adapter(tool_entry, tag_prefix=config.tag_prefix)
        try:
            available = adapter.get_available_versions()
        except SourceError as e:
            rows.append((name, current, "?", "error", str(e), external))
            continue
        tool_entry["available"] = available
        latest, bump = _compare_versions(current, available)
        rows.append((name, current, latest, bump, "", external))

    # ----------------------------------------------------------------- table
    # Printed to stdout so it can be piped / grepped.
    print("")
    w_name = max(len(r[0]) for r in rows)
    w_ver  = max(len(r[1]) if r[1] else len("(none)") for r in rows)
    for name, current, latest, bump, err, external in rows:
        cur_label = current if current else "(none)"
        ext_tag = " (external)" if external else ""
        pad_name  = f"{name:<{w_name}}"
        pad_cur   = f"{cur_label:<{w_ver}}"
        if bump == "up-to-date":
            print(f"  {pad_name}  {pad_cur}  (up to date){ext_tag}")
        elif bump == "ahead":
            print(f"  {pad_name}  {pad_cur}  (ahead of latest: {latest}){ext_tag}")
        elif bump == "error":
            print(f"  {pad_name}  {pad_cur}  \u26a0 error: {err}")
        else:
            print(f"  {pad_name}  {pad_cur}  \u2192  {latest}  ({bump}){ext_tag}")
    print("")

    # ----------------------------------------------------- auto-discovery
    # Collect unique source parent directories for auto-discovery
    disk_parents = set()
    manifest_tools = set(tools.keys())
    for tname, tentry in tools.items():
        src_type = tentry.get("source", {}).get("type", "")
        if src_type in ("archive", "external"):
            disk_parents.add(tentry["source"]["path"])

    if disk_parents:
        discovered = []  # (name, path, versions)
        parent_dirs = set()
        for dp in disk_parents:
            parent = os.path.dirname(dp)
            if parent and os.path.isdir(parent):
                parent_dirs.add(parent)

        semver_re = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
        for parent in sorted(parent_dirs):
            try:
                entries = os.listdir(parent)
            except OSError:
                continue
            for entry in entries:
                candidate_path = os.path.join(parent, entry)
                if not os.path.isdir(candidate_path):
                    continue
                if entry in manifest_tools:
                    continue
                # Check for tools already tracked by a different name
                # (their source path matches this candidate)
                already_tracked = any(
                    t.get("source", {}).get("path") == candidate_path
                    for t in tools.values()
                )
                if already_tracked:
                    continue
                # Check if candidate has semver subdirs
                try:
                    sub_entries = os.listdir(candidate_path)
                except OSError:
                    continue
                versions = [
                    s for s in sub_entries
                    if semver_re.match(s) and os.path.isdir(
                        os.path.join(candidate_path, s)
                    )
                ]
                if versions:
                    versions.sort(
                        key=lambda v: tuple(int(x) for x in v.split("."))
                    )
                    discovered.append((entry, candidate_path, versions))

        if discovered:
            print("  Discovered (not in manifest):", file=sys.stderr)
            for dname, dpath, dversions in discovered:
                dlatest = dversions[-1]
                print(
                    f"    {dname:<{w_name}}  {dpath}  "
                    f"(versions: {len(dversions)}, latest: {dlatest})",
                    file=sys.stderr,
                )
            print("", file=sys.stderr)

            if not args["non_interactive"]:
                if confirm(
                    "Add discovered tools to manifest?",
                    dry_run=args["dry_run"],
                    non_interactive=args["non_interactive"],
                ):
                    for dname, dpath, dversions in discovered:
                        data["tools"][dname] = {
                            "version": "",
                            "available": dversions,
                            "source": {"type": "external", "path": dpath},
                        }
                    if not args["dry_run"]:
                        save_manifest(manifest_path, data)
                        log_success(
                            f"Added {len(discovered)} tool(s) to manifest"
                        )

    # Persist available versions discovered during scan
    if not args["dry_run"]:
        save_manifest(manifest_path, data)

    if args["non_interactive"]:
        return

    # -------------------------------------------------------- upgrade prompt
    upgradable = [
        (name, current, latest)
        for name, current, latest, bump, _, external in rows
        if bump not in ("up-to-date", "ahead", "error", "unknown")
        and not external
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
    _apply_manifest_deploy_base(config, data)
    tool_entry = get_tool(data, tool_name)

    if tool_entry["source"]["type"] == "external" and not args["force"]:
        log_error(
            f"Tool '{tool_name}' is externally managed (source type: external). "
            f"Use --force to upgrade anyway."
        )
        raise SystemExit(1)

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
    manifest_path = resolve_manifest_path(config)
    data = load_manifest(manifest_path)
    _apply_manifest_deploy_base(config, data)

    # Resolve version: CLI --version takes precedence, then manifest dict
    ts_version = get_toolset_version(data, name)
    if version:
        try:
            validate_semver(version)
        except ValueError:
            log_error(f"Invalid version: '{version}' (expected X.Y.Z)")
            raise SystemExit(1)
    elif ts_version:
        version = ts_version
    else:
        log_error("--version is required for the toolset subcommand")
        raise SystemExit(1)

    deploy_base_path = config.deploy_base_path or ""
    if not config.mf_base_path and not deploy_base_path:
        log_error(
            "Either --mf-path or --deploy-path must be set, "
            "or set deploy_base_path in tools.json."
        )
        raise SystemExit(1)

    # Build tool_versions dict from toolset (handles both list and dict format)
    tool_versions = get_toolset_tool_versions(data, name)
    missing_versions = [t for t, v in tool_versions.items() if not v]

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
    write_modulefile(content, mf_file, dry_run)


def cmd_apply(args: dict, config, toolset_filter: str = "") -> None:
    """Apply subcommand: deploy all tool versions referenced by toolsets."""
    manifest_path = resolve_manifest_path(config)
    data = load_manifest(manifest_path)
    _apply_manifest_deploy_base(config, data)

    dry_run = args["dry_run"]
    force = args["force"]

    if not config.deploy_base_path:
        log_error(
            "No deploy base path configured. "
            "Set 'deploy_base_path' in tools.json or pass --deploy-path."
        )
        raise SystemExit(1)

    if not os.path.isabs(config.deploy_base_path):
        log_error(
            f"deploy_base_path must be an absolute path: {config.deploy_base_path}"
        )
        raise SystemExit(1)

    toolsets = data.get("toolsets", {})
    if toolset_filter:
        if toolset_filter not in toolsets:
            log_error(
                f"Toolset '{toolset_filter}' not found in manifest. "
                f"Available: {', '.join(sorted(toolsets.keys())) or '(none)'}"
            )
            raise SystemExit(1)
        selected_toolsets = {toolset_filter: toolsets[toolset_filter]}
    else:
        selected_toolsets = toolsets

    if not selected_toolsets:
        log_info("No toolsets in manifest.")
        return

    # Validate all selected toolsets use dict format and collect required versions
    required = {}  # (tool_name, version) -> set of toolset names
    for ts_name, ts_entry in selected_toolsets.items():
        if not isinstance(ts_entry, dict):
            log_error(
                f"Toolset '{ts_name}' uses legacy list format. "
                f"Apply requires dict format with version pins."
            )
            raise SystemExit(1)
        tool_versions = get_toolset_tool_versions(data, ts_name)
        for tool_name, version in tool_versions.items():
            if not version:
                log_error(
                    f"Toolset '{ts_name}' has empty version for tool '{tool_name}'"
                )
                raise SystemExit(1)
            key = (tool_name, version)
            required.setdefault(key, set()).add(ts_name)

    if not required:
        log_info("No tool versions to deploy.")
        return

    # Deploy missing tool+version pairs
    deployed_count = 0
    skipped_count = 0
    errors = []

    for (tool_name, version), ts_names in sorted(required.items()):
        tool_entry = data["tools"].get(tool_name)
        if not tool_entry:
            errors.append((tool_name, version, f"not found in manifest"))
            continue

        if tool_entry["source"]["type"] == "external" and not force:
            log_warn(
                f"Skipping {tool_name} {version} — externally managed "
                f"(use --force to override)"
            )
            skipped_count += 1
            continue

        # Resolve install path
        ts_name_for_vars = sorted(ts_names)[0]
        ts_entry_for_vars = selected_toolsets[ts_name_for_vars]
        user_vars = collect_string_vars(data, ts_entry_for_vars, tool_entry)
        raw_install_path = tool_entry.get("install_path")
        if raw_install_path:
            deploy_target = _resolve_path_template(
                raw_install_path, tool_name, version, user_vars=user_vars
            )
            if not os.path.isabs(deploy_target):
                deploy_target = os.path.join(
                    config.deploy_base_path, deploy_target
                )
        else:
            deploy_target = os.path.join(
                config.deploy_base_path, tool_name, version
            )

        # External sources: the version dir IS the deploy location
        source_type = tool_entry["source"]["type"]
        if source_type == "external" and not raw_install_path:
            deploy_target = os.path.join(
                tool_entry["source"]["path"], version
            )

        # Quick pre-lock check (authoritative check is inside the lock)
        if os.path.isdir(deploy_target) and not os.path.islink(deploy_target):
            log_info(f"Already deployed: {tool_name} {version}")
            skipped_count += 1
            continue

        # Deploy
        adapter = build_adapter(tool_entry, tag_prefix=config.tag_prefix)
        flatten_archive = tool_entry.get("flatten_archive", True)

        lock_fd = None
        lock_path = None
        if not dry_run:
            lock_fd, lock_path = _acquire_deploy_lock(
                tool_name, config.deploy_base_path
            )
        try:
            # Re-check inside lock to avoid TOCTOU
            if not dry_run:
                if os.path.islink(deploy_target):
                    log_error(
                        f"Deploy target is a symlink: {deploy_target}  "
                        f"— refusing to continue."
                    )
                    errors.append((tool_name, version, "symlink detected"))
                    continue
                if os.path.isdir(deploy_target):
                    log_info(f"Already deployed: {tool_name} {version}")
                    skipped_count += 1
                    continue

            log_info(f"Deploying {tool_name} {version} → {deploy_target}")
            try:
                if source_type == "archive":
                    deploy_root = adapter.deploy(
                        version, config.deploy_base_path, tool_name, dry_run,
                        install_path=raw_install_path and deploy_target or None,
                        flatten_archive=flatten_archive,
                    )
                else:
                    deploy_root = adapter.deploy(
                        version, config.deploy_base_path, tool_name, dry_run,
                        install_path=raw_install_path and deploy_target or None,
                    )
            except SourceError as e:
                errors.append((tool_name, version, str(e)))
                continue

            # Bootstrap
            bootstrap_cmd = tool_entry.get("bootstrap", "")
            if bootstrap_cmd and not dry_run and os.path.isdir(deploy_root):
                if not run_bootstrap(
                    bootstrap_cmd, deploy_root, version, tool_name, dry_run
                ):
                    errors.append((tool_name, version, "bootstrap failed"))
                    continue
            elif bootstrap_cmd and dry_run:
                run_bootstrap(
                    bootstrap_cmd, deploy_root, version, tool_name, dry_run=True
                )

            # Modulefile for tool
            raw_mf_path = tool_entry.get("mf_path")
            resolved_mf_path = None
            if raw_mf_path:
                resolved_mf_path = _resolve_path_template(
                    raw_mf_path, tool_name, version, user_vars=user_vars
                )
                if not os.path.isabs(resolved_mf_path):
                    resolved_mf_path = os.path.join(
                        config.deploy_base_path, resolved_mf_path
                    )
            resolved_install_path = (
                deploy_target if raw_install_path else None
            )

            try:
                _write_tool_modulefile(
                    tool_name, version, deploy_root, config, dry_run,
                    install_path=resolved_install_path,
                    mf_path=resolved_mf_path,
                    overwrite=True,
                )
            except SystemExit:
                errors.append((tool_name, version, "modulefile write failed"))
                continue

            deployed_count += 1

        finally:
            if lock_fd is not None:
                _release_deploy_lock(lock_fd, lock_path)

    # Write toolset modulefiles
    for ts_name, ts_entry in selected_toolsets.items():
        ts_version = get_toolset_version(data, ts_name)
        if not ts_version:
            log_warn(f"Toolset '{ts_name}' has no version — skipping modulefile")
            continue

        tool_versions = get_toolset_tool_versions(data, ts_name)
        content = generate_toolset_modulefile(
            toolset_name=ts_name,
            version=ts_version,
            deploy_base_path=config.deploy_base_path or "",
            tool_versions=tool_versions,
        )

        mf_base = config.mf_base_path or os.path.join(
            config.deploy_base_path, "mf"
        )
        mf_file = os.path.join(mf_base, ts_name, ts_version)
        write_modulefile(content, mf_file, dry_run, overwrite=True)
        if not dry_run:
            log_success(f"Toolset modulefile written: {mf_file}")

    # Summary
    if errors:
        log_warn(f"Apply finished with {len(errors)} error(s):")
        for tname, tver, reason in errors:
            log_error(f"  {tname} {tver}: {reason}")
    if deployed_count or skipped_count:
        log_info(
            f"Deployed: {deployed_count}, skipped: {skipped_count}, "
            f"errors: {len(errors)}"
        )
    if not errors:
        log_success("Apply completed successfully.")


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
        "force": False,
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
        elif arg == "--force":
            global_args["force"] = True
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

    # ------------------------------------------------------------------ apply
    elif subcommand == "apply":
        if "--help" in sub_remaining or "-h" in sub_remaining:
            print(USAGE_APPLY)
            raise SystemExit(0)
        toolset_filter = ""
        rest = []
        i = 0
        while i < len(sub_remaining):
            if sub_remaining[i] == "--toolset":
                i += 1
                if i >= len(sub_remaining):
                    log_error("--toolset requires a name")
                    raise SystemExit(1)
                toolset_filter = sub_remaining[i]
            else:
                rest.append(sub_remaining[i])
            i += 1
        if rest:
            log_error(f"Unexpected argument(s): {' '.join(rest)}")
            print(USAGE_APPLY)
            raise SystemExit(1)
        cmd_apply(args, config, toolset_filter=toolset_filter)

    else:
        log_error(f"Unknown subcommand: {subcommand}")
        print(USAGE)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
