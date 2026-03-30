"""Multi-level configuration loading (.release.conf format).

Priority (highest wins):
  1. Environment variables (snapshotted at import time)
  2. --config FILE (explicit)
  3. <repo>/.release.conf
  4. ~/.release.conf
  5. Defaults
"""

import os
import stat
from dataclasses import dataclass

from .log import log_info, log_warn, log_error

# Snapshot environment variables at import time so config files cannot override.
_ENV_SNAPSHOT = {
    "RELEASE_DEFAULT_BRANCH": os.environ.get("RELEASE_DEFAULT_BRANCH", ""),
    "RELEASE_TAG_PREFIX": os.environ.get("RELEASE_TAG_PREFIX", ""),
    "RELEASE_REMOTE": os.environ.get("RELEASE_REMOTE", ""),
    "MODULEFILE_TEMPLATE": os.environ.get("MODULEFILE_TEMPLATE", ""),
    "MF_BASE_PATH": os.environ.get("MF_BASE_PATH", ""),
    "TOOLS_MANIFEST": os.environ.get("TOOLS_MANIFEST", ""),
}

# Config key -> env var mapping
_CONFIG_KEY_TO_ENV = {
    "DEFAULT_BRANCH": "RELEASE_DEFAULT_BRANCH",
    "TAG_PREFIX": "RELEASE_TAG_PREFIX",
    "REMOTE": "RELEASE_REMOTE",
    "MODULEFILE_TEMPLATE": "MODULEFILE_TEMPLATE",
    "MF_BASE_PATH": "MF_BASE_PATH",
    "TOOLS_MANIFEST": "TOOLS_MANIFEST",
}

# Known config keys
_KNOWN_KEYS = set(_CONFIG_KEY_TO_ENV.keys())


@dataclass
class Config:
    """Resolved configuration values."""

    default_branch: str = "main"
    tag_prefix: str = "v"
    remote: str = "origin"
    deploy_base_path: str = ""
    modulefile_template: str = ""
    mf_base_path: str = ""
    tools_manifest: str = ""


def _warn_file_permissions(filepath: str) -> None:
    """Warn if a config/token file is readable by others."""
    try:
        st = os.stat(filepath)
        mode = stat.S_IMODE(st.st_mode)
        # Check if group or other have any permissions
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            octal = f"{mode:o}"
            log_warn(f"'{filepath}' is accessible by others (mode {octal}). "
                     f"Consider: chmod 600 '{filepath}'")
    except OSError:
        pass


def _parse_conf_file(filepath: str) -> dict:
    """Parse a .release.conf file (KEY=VALUE format).

    Handles comments, blank lines, quoted values, whitespace trimming.
    Returns a dict of key -> value strings.
    """
    result = {}
    try:
        with open(filepath, "r") as f:
            for line in f:
                line = line.strip()
                # Skip comments and blank lines
                if not line or line.startswith("#"):
                    continue
                # Split on first '='
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                if not key or key.startswith("#"):
                    continue
                # Strip surrounding quotes
                value = value.strip()
                if len(value) >= 2:
                    if (value[0] == '"' and value[-1] == '"') or \
                       (value[0] == "'" and value[-1] == "'"):
                        value = value[1:-1]
                value = value.strip()
                if key in _KNOWN_KEYS:
                    result[key] = value
                else:
                    log_warn(f"Unknown config key: {key}")
    except FileNotFoundError:
        pass
    except OSError as e:
        log_warn(f"Cannot read config file '{filepath}': {e}")
    return result


def _apply_conf(config: Config, conf: dict, source_label: str = "config file") -> None:
    """Apply a parsed config dict to a Config object."""
    if "DEFAULT_BRANCH" in conf:
        config.default_branch = conf["DEFAULT_BRANCH"]
    if "TAG_PREFIX" in conf:
        config.tag_prefix = conf["TAG_PREFIX"]
    if "REMOTE" in conf:
        config.remote = conf["REMOTE"]
    if "MODULEFILE_TEMPLATE" in conf:
        config.modulefile_template = conf["MODULEFILE_TEMPLATE"]
    if "MF_BASE_PATH" in conf:
        config.mf_base_path = conf["MF_BASE_PATH"]
    if "TOOLS_MANIFEST" in conf:
        config.tools_manifest = conf["TOOLS_MANIFEST"]


def load_config(
    config_file: str = "",
    repo_root: str = "",
    cli_deploy_path: str = "",
    cli_mf_path: str = "",
    cli_manifest: str = "",
) -> Config:
    """Load configuration with multi-level priority.

    Args:
        config_file: Explicit config file path (from --config).
        repo_root: Repository root directory.
        cli_deploy_path: CLI --deploy-path override.
        cli_mf_path: CLI --mf-path override.
        cli_manifest: CLI --manifest override.

    Returns:
        Resolved Config object.
    """
    config = Config()

    # 1. Repo-level config
    if repo_root:
        repo_conf = os.path.join(repo_root, ".release.conf")
        if os.path.isfile(repo_conf):
            _warn_file_permissions(repo_conf)
            log_info(f"Loading config: {repo_conf}")
            _apply_conf(config, _parse_conf_file(repo_conf))

    # 2. Explicit --config file
    if config_file:
        if not os.path.isfile(config_file):
            log_error(f"Config file not found: {config_file}")
            raise SystemExit(1)
        _warn_file_permissions(config_file)
        log_info(f"Loading config: {config_file}")
        _apply_conf(config, _parse_conf_file(config_file))

    # 3. Env vars override everything (from snapshot)
    if _ENV_SNAPSHOT["RELEASE_DEFAULT_BRANCH"]:
        config.default_branch = _ENV_SNAPSHOT["RELEASE_DEFAULT_BRANCH"]
    if _ENV_SNAPSHOT["RELEASE_TAG_PREFIX"]:
        config.tag_prefix = _ENV_SNAPSHOT["RELEASE_TAG_PREFIX"]
    if _ENV_SNAPSHOT["RELEASE_REMOTE"]:
        config.remote = _ENV_SNAPSHOT["RELEASE_REMOTE"]
    if _ENV_SNAPSHOT["MODULEFILE_TEMPLATE"]:
        config.modulefile_template = _ENV_SNAPSHOT["MODULEFILE_TEMPLATE"]
    if _ENV_SNAPSHOT["MF_BASE_PATH"]:
        config.mf_base_path = _ENV_SNAPSHOT["MF_BASE_PATH"]
    if _ENV_SNAPSHOT["TOOLS_MANIFEST"]:
        config.tools_manifest = _ENV_SNAPSHOT["TOOLS_MANIFEST"]

    # 4. CLI overrides take highest precedence
    if cli_deploy_path:
        config.deploy_base_path = cli_deploy_path
    if cli_mf_path:
        config.mf_base_path = cli_mf_path
    if cli_manifest:
        config.tools_manifest = cli_manifest

    return config
