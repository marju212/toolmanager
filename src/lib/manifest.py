"""tools.json manifest read/write and validation."""

import json
import os
import tempfile

from .log import log_warn, log_error

# Required fields per source type
_REQUIRED_SOURCE_FIELDS = {
    "git": ["url"],
    "disk": ["path"],
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

    # Validate each tool entry
    for name, tool in data["tools"].items():
        if "/" in name or "\\" in name or ".." in name:
            log_error(
                f"Tool name '{name}' contains invalid characters "
                f"(path separators and '..' are not allowed)"
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

    # Validate each toolset entry
    for ts_name in data["toolsets"]:
        if "/" in ts_name or "\\" in ts_name or ".." in ts_name:
            log_error(
                f"Toolset name '{ts_name}' contains invalid characters "
                f"(path separators and '..' are not allowed)"
            )
            raise SystemExit(1)

    # Warn about unknown tool names referenced in toolsets
    for ts_name, ts_tools in data["toolsets"].items():
        for tool_name in ts_tools:
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


def get_toolset(data: dict, name: str) -> list:
    """Return toolset tool list by name; raises SystemExit if not found."""
    toolsets = data.get("toolsets", {})
    if name not in toolsets:
        available = ", ".join(sorted(toolsets.keys())) or "(none)"
        log_error(
            f"Toolset '{name}' not found in manifest. Available: {available}"
        )
        raise SystemExit(1)
    return toolsets[name]


def resolve_manifest_path(config) -> str:
    """Resolve tools.json path from config or fall back to cwd/tools.json."""
    if config.tools_manifest:
        return config.tools_manifest
    return os.path.join(os.getcwd(), "tools.json")
