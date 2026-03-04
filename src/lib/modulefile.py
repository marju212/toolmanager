"""Modulefile generation and template substitution."""

import os
import re
from typing import Dict, Optional

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


DEFAULT_BUNDLE_MODULEFILE_TEMPLATE = """\
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
    tool_versions: Optional[Dict[str, str]] = None,
) -> str:
    """Replace placeholders in a modulefile template.

    Placeholders:
        %VERSION%          - The version being deployed
        %ROOT%             - The deploy directory root
        %TOOL_NAME%        - The tool name
        %DEPLOY_BASE_PATH% - The deploy base path
        %<tool-name>%      - Per-tool version (from tool_versions dict)
        %TOOL_LOADS%       - Auto-generated 'module load' block

    Args:
        template: Template string with placeholders.
        version: Version string (e.g. '1.2.0').
        root: Deploy root path for this tool version.
        tool_name: Tool/bundle name.
        deploy_base_path: Base deploy path.
        tool_versions: Dict mapping tool names to versions (for bundles).

    Returns:
        Template with placeholders substituted.
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
    tool_versions: Dict[str, str],
) -> None:
    """Validate that per-tool placeholders in a template reference existing submodules.

    Raises ValueError if a placeholder references a non-existent submodule.
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
) -> Optional[str]:
    """Resolve which modulefile template to use.

    Priority:
        1. repo modulefile.tcl (in deploy_dir)
        2. config MODULEFILE_TEMPLATE path
        3. None (use default)

    Returns template content string, or None for default.
    """
    # Check for modulefile.tcl in the deployed repo
    if deploy_dir:
        repo_template = os.path.join(deploy_dir, "modulefile.tcl")
        if os.path.isfile(repo_template):
            try:
                with open(repo_template, "r") as f:
                    return f.read()
            except OSError as e:
                log_warn(f"Cannot read repo template '{repo_template}': {e} "
                         "— falling through to next template source.")

    # Check config template path
    if config_template_path and os.path.isfile(config_template_path):
        try:
            with open(config_template_path, "r") as f:
                return f.read()
        except OSError as e:
            log_warn(f"Cannot read config template '{config_template_path}': {e} "
                     "— using default template.")

    return None


def generate_default_modulefile(tool_name: str, version: str, root: str) -> str:
    """Generate a default modulefile using the hardcoded template."""
    return DEFAULT_MODULEFILE_TEMPLATE.format(
        tool_name=tool_name,
        version=version,
        root=root,
    )


def generate_bundle_modulefile(
    bundle_name: str,
    version: str,
    deploy_base_path: str,
    tool_versions: Dict[str, str],
    template_path: str = "",
    template_content: Optional[str] = None,
) -> str:
    """Generate a bundle modulefile.

    If a custom template is provided, uses placeholder substitution.
    Otherwise uses the default bundle template.

    Args:
        bundle_name: Bundle/toolset name.
        version: Bundle version.
        deploy_base_path: Deploy base path.
        tool_versions: Dict mapping tool names to versions.
        template_path: Path to custom template file.
        template_content: Pre-loaded template content (overrides template_path).

    Returns:
        Modulefile content string.
    """
    template = template_content
    if template is None and template_path and os.path.isfile(template_path):
        try:
            with open(template_path, "r") as f:
                template = f.read()
        except OSError as e:
            log_warn(f"Cannot read bundle template '{template_path}': {e} "
                     "— using default bundle template.")

    if template is not None:
        # Custom template — substitute placeholders
        validate_template_placeholders(template, tool_versions)
        return substitute_placeholders(
            template,
            version=version,
            tool_name=bundle_name,
            deploy_base_path=deploy_base_path,
            tool_versions=tool_versions,
        )

    # Default bundle template
    load_lines = []
    for name, ver in sorted(tool_versions.items()):
        load_lines.append(f"module load {name}/{ver}")
    tool_loads = "\n".join(load_lines)

    return DEFAULT_BUNDLE_MODULEFILE_TEMPLATE.format(
        tool_name=bundle_name,
        version=version,
        tool_loads=tool_loads,
    )


def write_modulefile(content: str, mf_path: str, dry_run: bool = False) -> None:
    """Write modulefile content to disk.

    Args:
        content: Modulefile content string.
        mf_path: Target file path.
        dry_run: If True, log but don't write.
    """
    if dry_run:
        log_info(f"[dry-run] Would write modulefile to {mf_path}")
        return

    try:
        os.makedirs(os.path.dirname(mf_path), exist_ok=True)
        with open(mf_path, "w") as f:
            f.write(content)
    except OSError as e:
        log_error(f"Cannot write modulefile to '{mf_path}': {e}")
        raise SystemExit(1)
    log_success(f"Modulefile written to {mf_path}")


def copy_and_update_modulefile(
    source_path: str,
    dest_path: str,
    old_version: str,
    new_version: str,
    dry_run: bool = False,
) -> None:
    """Copy an existing modulefile and update version references.

    Args:
        source_path: Path to existing modulefile.
        dest_path: Target path for new modulefile.
        old_version: Version string to replace.
        new_version: New version string.
        dry_run: If True, log but don't write.
    """
    if dry_run:
        log_info(f"[dry-run] Would copy modulefile from {source_path} to "
                 f"{dest_path} (updating version to {new_version})")
        return

    try:
        with open(source_path, "r") as f:
            content = f.read()
    except OSError as e:
        log_error(f"Cannot read modulefile '{source_path}': {e}")
        raise SystemExit(1)

    # Use word-boundary anchors so e.g. "1.1.0" inside "21.1.0" is not replaced.
    content = re.sub(
        r"(?<![.\d])" + re.escape(old_version) + r"(?![.\d])",
        new_version,
        content,
    )

    try:
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        with open(dest_path, "w") as f:
            f.write(content)
    except OSError as e:
        log_error(f"Cannot write modulefile to '{dest_path}': {e}")
        raise SystemExit(1)
    log_success(f"Modulefile copied and updated to {dest_path}")


def find_latest_modulefile(mf_dir: str) -> Optional[str]:
    """Find the latest semver modulefile in a directory.

    Returns full path to the latest modulefile, or None.
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
