"""Modulefile generation and template substitution for Environment Modules.

Generates Tcl modulefiles that let users load tools via 'module load tool/version'.
Supports two kinds of modulefiles:

    Tool modulefiles    — one per tool+version, sets PATH etc.
    Toolset modulefiles — loads multiple tools at pinned versions.

Template resolution priority (for tool modulefiles):
    1. Copy previous version's modulefile and update version references
    2. Use modulefile.tcl from the deployed repo (git sources)
    3. Use config-specified MODULEFILE_TEMPLATE
    4. Fall back to built-in default template

Placeholders in templates: %VERSION%, %ROOT%, %TOOL_NAME%, %DEPLOY_BASE_PATH%
Toolset templates also support: %TOOL_LOADS%, %<tool-name>% (per-tool version)
"""

import os
import re

from .log import log_info, log_warn, log_error, log_success


DEFAULT_MODULEFILE_TEMPLATE = """\
#%Module1.0
##
## {tool_name}/{version} modulefile
##

proc ModulesHelp {{ }} {{
    puts stderr "{tool_name} version {version}"
}}

module-whatis "{tool_name} version {version}"

conflict {tool_name}

set root {root}

prepend-path PATH $root/bin
"""


DEFAULT_TOOLSET_MODULEFILE_TEMPLATE = """\
#%Module1.0
##
## {tool_name}/{version} modulefile
##

proc ModulesHelp {{ }} {{
    puts stderr "{tool_name} version {version}"
}}

module-whatis "{tool_name} version {version}"

conflict {tool_name}

{tool_loads}
"""


def substitute_placeholders(
    template: str,
    version: str,
    root: str = "",
    tool_name: str = "",
    deploy_base_path: str = "",
    tool_versions: dict[str, str] | None = None,
) -> str:
    """Replace ``%PLACEHOLDER%`` tokens in a modulefile template string.

    Standard placeholders (always available):

    ==================== ==========================================
    ``%VERSION%``        Version being deployed (e.g. ``1.2.0``)
    ``%ROOT%``           Absolute path to the deployed tool version
    ``%TOOL_NAME%``      Name of the tool or toolset
    ``%DEPLOY_BASE_PATH%`` Root deploy directory
    ==================== ==========================================

    Toolset-only placeholders (require *tool_versions*):

    ==================== ==========================================
    ``%TOOL_LOADS%``     Auto-generated ``module load name/ver`` block
    ``%<tool-name>%``    Replaced by that tool's pinned version
    ==================== ==========================================

    Returns the template with all recognised placeholders filled in.
    """
    result = template
    result = result.replace("%VERSION%", version)
    result = result.replace("%ROOT%", root)
    result = result.replace("%TOOL_NAME%", tool_name)
    result = result.replace("%DEPLOY_BASE_PATH%", deploy_base_path)

    if tool_versions:
        # Generate TOOL_LOADS block
        load_lines = []
        for name, ver in sorted(tool_versions.items()):
            load_lines.append(f"module load {name}/{ver}")
        tool_loads = "\n".join(load_lines)
        result = result.replace("%TOOL_LOADS%", tool_loads)

        # Per-tool version placeholders
        for name, ver in tool_versions.items():
            result = result.replace(f"%{name}%", ver)

    return result


def validate_template_placeholders(
    template: str,
    tool_versions: dict[str, str],
) -> None:
    """Check that every custom ``%name%`` in *template* matches a known tool.

    Standard placeholders (``VERSION``, ``ROOT``, etc.) are ignored.
    Raises ``ValueError`` if a ``%name%`` token does not correspond to any
    key in *tool_versions*, which usually means a typo in the template.
    """
    # Find all %name% placeholders that aren't standard ones
    standard = {"VERSION", "ROOT", "TOOL_NAME", "DEPLOY_BASE_PATH", "TOOL_LOADS"}
    placeholders = re.findall(r"%([^%]+)%", template)
    for ph in placeholders:
        if ph in standard:
            continue
        if ph not in tool_versions:
            raise ValueError(
                f"Template placeholder '%{ph}%' does not match any submodule. "
                f"Available: {', '.join(sorted(tool_versions.keys()))}"
            )


def resolve_template(
    deploy_dir: str = "",
    config_template_path: str = "",
) -> tuple[str | None, str]:
    """Decide which modulefile template to use and return ``(content, label)``.

    Checks sources in priority order:

    1. ``modulefile.tcl`` inside the deployed repo (*deploy_dir*) — lets
       individual tools ship their own template.
    2. A global template file pointed to by the config
       (``MODULEFILE_TEMPLATE``).
    3. ``(None, "default")`` — signals the caller to fall back to the
       built-in default.

    The *label* is a short human-readable string naming the source
    (``"repo modulefile.tcl"``, ``"config template"``, or ``"default"``)
    so the deploy command can log which template was chosen.
    """
    # Check for modulefile.tcl in the deployed repo
    if deploy_dir:
        repo_template = os.path.join(deploy_dir, "modulefile.tcl")
        if os.path.isfile(repo_template):
            try:
                with open(repo_template, "r") as f:
                    return f.read(), "repo modulefile.tcl"
            except OSError as e:
                log_warn(f"Cannot read repo template '{repo_template}': {e} "
                         "— falling through to next template source.")

    # Check config template path
    if config_template_path and os.path.isfile(config_template_path):
        try:
            with open(config_template_path, "r") as f:
                return f.read(), "config template"
        except OSError as e:
            log_warn(f"Cannot read config template '{config_template_path}': {e} "
                     "— using default template.")

    return None, "default"


def generate_default_modulefile(tool_name: str, version: str, root: str) -> str:
    """Return a modulefile string using the built-in default template.

    The default template sets up ``conflict``, ``module-whatis``, and
    prepends ``$root/bin`` to ``PATH``.
    """
    return DEFAULT_MODULEFILE_TEMPLATE.format(
        tool_name=tool_name,
        version=version,
        root=root,
    )


def generate_toolset_modulefile(
    toolset_name: str,
    version: str,
    deploy_base_path: str,
    tool_versions: dict[str, str],
    template_path: str = "",
    template_content: str | None = None,
) -> str:
    """Build a modulefile that loads an entire toolset at pinned versions.

    The generated file contains one ``module load <tool>/<version>`` line
    per tool.  If a custom template is provided (via *template_content* or
    *template_path*), placeholders are substituted instead.

    Args:
        toolset_name:     Name of the toolset (used in ``module-whatis``).
        version:          The toolset's own version string.
        deploy_base_path: Root deploy directory (for ``%DEPLOY_BASE_PATH%``).
        tool_versions:    ``{tool_name: version}`` mapping.
        template_path:    Optional path to a custom Tcl template file.
        template_content: Optional pre-loaded template string (takes priority
                          over *template_path*).

    Returns:
        The complete modulefile content as a string.
    """
    template = template_content
    if template is None and template_path and os.path.isfile(template_path):
        try:
            with open(template_path, "r") as f:
                template = f.read()
        except OSError as e:
            log_warn(f"Cannot read toolset template '{template_path}': {e} "
                     "— using default toolset template.")

    if template is not None:
        # Custom template — substitute placeholders
        validate_template_placeholders(template, tool_versions)
        return substitute_placeholders(
            template,
            version=version,
            tool_name=toolset_name,
            deploy_base_path=deploy_base_path,
            tool_versions=tool_versions,
        )

    # Default toolset template
    load_lines = []
    for name, ver in sorted(tool_versions.items()):
        load_lines.append(f"module load {name}/{ver}")
    tool_loads = "\n".join(load_lines)

    return DEFAULT_TOOLSET_MODULEFILE_TEMPLATE.format(
        tool_name=toolset_name,
        version=version,
        tool_loads=tool_loads,
    )


def write_modulefile(
    content: str, mf_path: str, dry_run: bool = False, overwrite: bool = False,
) -> None:
    """Write a modulefile string to disk, creating parent directories as needed.

    Safety checks before writing:

    - Refuses to follow symlinks (both the file itself and its parent
      directory) to prevent writing outside the expected tree.
    - Errors if the file already exists unless *overwrite* is ``True``.

    In dry-run mode, logs what would happen without touching the filesystem.
    """
    if dry_run:
        log_info(f"[dry-run] Would write modulefile to {mf_path}")
        return

    # Refuse to follow symlinks to prevent writing outside expected tree
    if os.path.islink(mf_path):
        log_error(f"Modulefile path is a symlink: {mf_path} — refusing to write.")
        raise SystemExit(1)
    mf_dir = os.path.dirname(mf_path)
    if os.path.islink(mf_dir):
        log_error(f"Modulefile directory is a symlink: {mf_dir} — refusing to write.")
        raise SystemExit(1)

    existed = os.path.isfile(mf_path)
    if existed and not overwrite:
        log_error(f"Modulefile already exists: {mf_path}")
        log_error(f"  To replace it, remove it first: rm {mf_path}")
        raise SystemExit(1)

    try:
        os.makedirs(mf_dir, exist_ok=True)
        with open(mf_path, "w") as f:
            f.write(content)
    except OSError as e:
        log_error(f"Cannot write modulefile to '{mf_path}': {e}")
        raise SystemExit(1)
    if existed:
        log_warn(f"Modulefile overwritten: {mf_path}")
    else:
        log_success(f"Modulefile written to {mf_path}")


def copy_and_update_modulefile(
    source_path: str,
    dest_path: str,
    old_version: str,
    new_version: str,
    dry_run: bool = False,
) -> str:
    """Create a new modulefile by copying a previous version and updating it.

    Two substitution strategies, in order of preference:

    1. **Placeholder-preferred** — if the source contains ``%VERSION%``,
       treat it as a template and substitute placeholders.  This is the
       safest path because the modulefile explicitly marks version sites.
    2. **Contextual regex** — otherwise, replace ``old_version`` only in
       contexts that unambiguously refer to the version: as a path
       segment (``/1.0.0/``, ``/1.0.0"``, ``/1.0.0`` at end of line), in
       ``set foo … 1.0.0`` / ``set foo … 1.0.0/…``, or in ``version 1.0.0``
       / ``whatis`` lines, or in ``module load <tool>/1.0.0``.  Bare
       occurrences of the version (e.g. inside the path
       ``/opt/support-libs-1.0.0``) are left alone.

    Returns a short label describing which strategy was used (for logging).
    """
    if dry_run:
        log_info(f"[dry-run] Would copy modulefile from {source_path} to "
                 f"{dest_path} (updating version to {new_version})")
        return f"copy of {old_version}"

    try:
        with open(source_path, "r") as f:
            content = f.read()
    except OSError as e:
        log_error(f"Cannot read modulefile '{source_path}': {e}")
        raise SystemExit(1)

    if "%VERSION%" in content:
        content = content.replace("%VERSION%", new_version)
        strategy = f"copy of {old_version} (placeholder)"
    else:
        esc = re.escape(old_version)
        # Match the version only in contexts that refer to *this* tool's version.
        # - path segment: "/1.0.0/", "/1.0.0\"", "/1.0.0" at EOL/whitespace
        # - after whitespace in "set … 1.0.0", "version 1.0.0", "whatis … 1.0.0"
        # - in "tool-name/1.0.0" inside `module load` etc. (handled by the /seg rule)
        patterns = [
            # /X.Y.Z followed by /, ", end-of-line, or whitespace
            rf"(?<=/){esc}(?=[/\"\s]|$)",
            # space/tab then X.Y.Z at end of line or followed by quote/space
            rf"(?<=[ \t]){esc}(?=[\"\s]|$)",
        ]
        for pat in patterns:
            content = re.sub(pat, new_version, content, flags=re.MULTILINE)
        strategy = f"copy of {old_version}"

    # Refuse to follow symlinks
    if os.path.islink(dest_path):
        log_error(f"Modulefile path is a symlink: {dest_path} — refusing to write.")
        raise SystemExit(1)
    dest_dir = os.path.dirname(dest_path)
    if os.path.islink(dest_dir):
        log_error(f"Modulefile directory is a symlink: {dest_dir} — refusing to write.")
        raise SystemExit(1)

    try:
        os.makedirs(dest_dir, exist_ok=True)
        with open(dest_path, "w") as f:
            f.write(content)
    except OSError as e:
        log_error(f"Cannot write modulefile to '{dest_path}': {e}")
        raise SystemExit(1)
    log_success(f"Modulefile copied and updated to {dest_path}")
    return strategy


def find_latest_modulefile(mf_dir: str) -> str | None:
    """Find the modulefile with the highest semver name in *mf_dir*.

    Modulefile filenames are bare version strings (e.g. ``1.2.3``).
    Scans the directory, filters to valid semver names, sorts
    numerically, and returns the full path to the highest one —
    or ``None`` if the directory is empty or does not exist.
    """
    if not os.path.isdir(mf_dir):
        return None

    semver_re = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
    versions = []
    for entry in os.listdir(mf_dir):
        if semver_re.match(entry):
            parts = tuple(int(x) for x in entry.split("."))
            versions.append((parts, entry))

    if not versions:
        return None

    versions.sort()
    return os.path.join(mf_dir, versions[-1][1])
