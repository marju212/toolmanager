"""tools.json manifest — read, write, and validation.

The manifest (tools.json) is the central configuration file that defines:

    tools      — each tool's source type, location, and current version
    toolsets   — named groups of tools with pinned versions
    deploy_base_path — default root for deployments

Source types (mapped to adapters in sources.py):

    git        — tool hosted in a git repo; cloned on deploy
    archive    — tool packaged as archives on disk; extracted on deploy
    external   — tool already installed externally; no-op deploy

Toolset formats:

    Legacy list:  ["tool-a", "tool-b"]
        Uses each tool's current version field. Works with 'toolset' command.

    Dict format:  {"version": "1.0.0", "tools": {"tool-a": "1.2.0", ...}}
        Explicit version pins. Required for the 'apply' command.
"""

import json
import os
import re
import tempfile

from .log import log_warn, log_error

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")

# Required fields per source type
_REQUIRED_SOURCE_FIELDS = {
    "git": ["url"],
    "archive": ["path"],
    "external": ["path"],
}


def load_manifest(path: str) -> dict:
    """Parse and validate tools.json.

    Raises SystemExit on missing file, invalid JSON, or missing required fields.
    Unknown tool names in toolset lists are flagged as warnings.
    """
    if not os.path.isfile(path):
        log_error(f"Manifest file not found: {path}")
        if os.path.basename(path) == "tools.json":
            log_error(
                "Create a tools.json manifest or use --manifest to specify "
                "an existing one."
            )
        raise SystemExit(1)

    try:
        with open(path, "r") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        log_error(f"Invalid JSON in manifest '{path}': {e}")
        raise SystemExit(1)
    except OSError as e:
        log_error(f"Cannot read manifest '{path}': {e}")
        raise SystemExit(1)

    if "tools" not in data:
        data["tools"] = {}
    if "toolsets" not in data:
        data["toolsets"] = {}
    if "deploy_base_path" not in data:
        data["deploy_base_path"] = "/"
    elif not isinstance(data["deploy_base_path"], str):
        log_error("Top-level 'deploy_base_path' must be a string")
        raise SystemExit(1)

    # Validate each tool entry
    _UNSAFE_NAME_CHARS = set('/\\;|&$`()"\'<>!')
    for name, tool in data["tools"].items():
        if "/" in name or "\\" in name or ".." in name:
            log_error(
                f"Tool name '{name}' contains invalid characters "
                f"(path separators and '..' are not allowed)"
            )
            raise SystemExit(1)
        bad = _UNSAFE_NAME_CHARS.intersection(name)
        if bad:
            log_error(
                f"Tool name '{name}' contains unsafe characters: "
                f"{' '.join(sorted(bad))}  "
                f"— only alphanumerics, hyphens, underscores, and dots are allowed."
            )
            raise SystemExit(1)
        if "source" not in tool:
            log_error(f"Tool '{name}' is missing 'source' field in manifest")
            raise SystemExit(1)
        source = tool["source"]
        if "type" not in source:
            log_error(
                f"Tool '{name}' source is missing 'type' field in manifest"
            )
            raise SystemExit(1)
        src_type = source["type"]
        if src_type not in _REQUIRED_SOURCE_FIELDS:
            known = ", ".join(sorted(_REQUIRED_SOURCE_FIELDS.keys()))
            log_error(
                f"Tool '{name}' has unknown source type: {src_type!r}. "
                f"Known types: {known}"
            )
            raise SystemExit(1)
        for field in _REQUIRED_SOURCE_FIELDS[src_type]:
            if field not in source:
                log_error(
                    f"Tool '{name}' source type '{src_type}' "
                    f"is missing required field '{field}'"
                )
                raise SystemExit(1)
        if "available" in tool:
            if not isinstance(tool["available"], list) or not all(
                isinstance(v, str) for v in tool["available"]
            ):
                log_error(
                    f"Tool '{name}' has invalid 'available' field: "
                    f"expected a list of version strings"
                )
                raise SystemExit(1)

    # Validate each toolset entry
    for ts_name in data["toolsets"]:
        if "/" in ts_name or "\\" in ts_name or ".." in ts_name:
            log_error(
                f"Toolset name '{ts_name}' contains invalid characters "
                f"(path separators and '..' are not allowed)"
            )
            raise SystemExit(1)
        bad = _UNSAFE_NAME_CHARS.intersection(ts_name)
        if bad:
            log_error(
                f"Toolset name '{ts_name}' contains unsafe characters: "
                f"{' '.join(sorted(bad))}  "
                f"— only alphanumerics, hyphens, underscores, and dots are allowed."
            )
            raise SystemExit(1)

    # Validate toolset format and warn about unknown tool names
    for ts_name, ts_tools in data["toolsets"].items():
        if isinstance(ts_tools, dict):
            # New dict format: {"version": "X.Y.Z", "tools": {"name": "ver"}}
            if "tools" not in ts_tools or not isinstance(ts_tools["tools"], dict):
                log_error(
                    f"Toolset '{ts_name}' dict format requires a 'tools' "
                    f"mapping (e.g. {{\"tools\": {{\"name\": \"1.0.0\"}}}})"
                )
                raise SystemExit(1)
            if "version" not in ts_tools or not isinstance(ts_tools["version"], str):
                log_error(
                    f"Toolset '{ts_name}' dict format requires a 'version' "
                    f"string (e.g. {{\"version\": \"1.0.0\"}})"
                )
                raise SystemExit(1)
            if not _SEMVER_RE.match(ts_tools["version"]):
                log_error(
                    f"Toolset '{ts_name}' version '{ts_tools['version']}' "
                    f"is not valid semver (expected X.Y.Z)"
                )
                raise SystemExit(1)
            for tname, tver in ts_tools["tools"].items():
                if not isinstance(tver, str) or not _SEMVER_RE.match(tver):
                    log_error(
                        f"Toolset '{ts_name}' has invalid version "
                        f"for tool '{tname}': {tver!r} (expected X.Y.Z)"
                    )
                    raise SystemExit(1)
            tool_names = ts_tools["tools"].keys()
        elif isinstance(ts_tools, list):
            # Legacy list format: ["tool1", "tool2"]
            tool_names = ts_tools
        else:
            log_error(
                f"Toolset '{ts_name}' must be a list or dict, "
                f"got {type(ts_tools).__name__}"
            )
            raise SystemExit(1)
        for tool_name in tool_names:
            if tool_name not in data["tools"]:
                log_warn(
                    f"Toolset '{ts_name}' references unknown tool: {tool_name}"
                )

    return data


def save_manifest(path: str, data: dict) -> None:
    """Atomically write manifest JSON to disk (tmp + rename)."""
    dir_ = os.path.dirname(os.path.abspath(path))
    try:
        fd, tmp_path = tempfile.mkstemp(dir=dir_, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
                f.write("\n")
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        os.replace(tmp_path, path)
    except OSError as e:
        log_error(f"Cannot write manifest '{path}': {e}")
        raise SystemExit(1)


def get_tool(data: dict, name: str) -> dict:
    """Return tool entry by name; raises SystemExit if not found."""
    tools = data.get("tools", {})
    if name not in tools:
        available = ", ".join(sorted(tools.keys())) or "(none)"
        log_error(
            f"Tool '{name}' not found in manifest. Available: {available}"
        )
        raise SystemExit(1)
    return tools[name]


def set_tool_version(data: dict, name: str, version: str) -> None:
    """Update the version field for a tool in-place."""
    data["tools"][name]["version"] = version


def get_toolset(data: dict, name: str) -> list | dict:
    """Return toolset entry by name (list or dict); raises SystemExit if not found."""
    toolsets = data.get("toolsets", {})
    if name not in toolsets:
        available = ", ".join(sorted(toolsets.keys())) or "(none)"
        log_error(
            f"Toolset '{name}' not found in manifest. Available: {available}"
        )
        raise SystemExit(1)
    return toolsets[name]


def get_toolset_tool_versions(data: dict, ts_name: str) -> dict:
    """Normalize a toolset into {tool_name: version} regardless of format.

    - Legacy list format: pulls each tool's 'version' field from the manifest.
    - Dict format: returns the 'tools' mapping directly.
    Raises SystemExit if toolset not found.
    """
    ts = get_toolset(data, ts_name)
    if isinstance(ts, dict):
        return dict(ts["tools"])
    # Legacy list — look up each tool's current version
    result = {}
    for tool_name in ts:
        if tool_name in data.get("tools", {}):
            result[tool_name] = data["tools"][tool_name].get("version", "")
    return result


def get_toolset_version(data: dict, ts_name: str) -> str:
    """Return the toolset's own version string, or '' for legacy list format."""
    ts = get_toolset(data, ts_name)
    if isinstance(ts, dict):
        return ts.get("version", "")
    return ""


def set_tool_available(data: dict, name: str, versions: list) -> None:
    """Update the available versions list for a tool in-place."""
    data["tools"][name]["available"] = versions


def collect_string_vars(data: dict, *scopes: dict) -> dict:
    """Collect string-valued keys from root through nested scopes (closest wins).

    Walks *data* (the root manifest dict) first, then each element of *scopes*
    in order.  Later scopes override earlier ones.  Non-string values (dicts,
    lists, booleans, numbers) are silently skipped.  All values are stripped of
    leading/trailing whitespace.
    """
    merged: dict[str, str] = {}
    for k, v in data.items():
        if isinstance(v, str):
            merged[k] = v.strip()
    for scope in scopes:
        if isinstance(scope, dict):
            for k, v in scope.items():
                if isinstance(v, str):
                    merged[k] = v.strip()
    return merged


def resolve_manifest_path(config) -> str:
    """Resolve tools.json path from config or fall back to cwd/tools.json."""
    if config.tools_manifest:
        return config.tools_manifest
    return os.path.join(os.getcwd(), "tools.json")
