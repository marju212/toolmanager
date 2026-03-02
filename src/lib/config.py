"""Multi-level configuration loading (.release.conf format).

Priority (highest wins):
  1. Environment variables (snapshotted at import time)
  2. --config FILE (explicit)
  3. <repo>/.release.conf
  4. ~/.release.conf
  5. ~/.gitlab_token (token only)
  6. Defaults
"""

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .log import log_info, log_warn, log_error

# Snapshot environment variables at import time so config files cannot override.
_ENV_SNAPSHOT = {
    "GITLAB_TOKEN": os.environ.get("GITLAB_TOKEN", ""),
    "GITLAB_API_URL": os.environ.get("GITLAB_API_URL", ""),
    "RELEASE_DEFAULT_BRANCH": os.environ.get("RELEASE_DEFAULT_BRANCH", ""),
    "RELEASE_TAG_PREFIX": os.environ.get("RELEASE_TAG_PREFIX", ""),
    "RELEASE_REMOTE": os.environ.get("RELEASE_REMOTE", ""),
    "GITLAB_VERIFY_SSL": os.environ.get("GITLAB_VERIFY_SSL", ""),
    "RELEASE_UPDATE_DEFAULT_BRANCH": os.environ.get("RELEASE_UPDATE_DEFAULT_BRANCH", ""),
    "DEPLOY_BASE_PATH": os.environ.get("DEPLOY_BASE_PATH", ""),
    "BUNDLE_SUBMODULE_DIR": os.environ.get("BUNDLE_SUBMODULE_DIR", ""),
    "BUNDLE_NAME": os.environ.get("BUNDLE_NAME", ""),
    "MODULEFILE_TEMPLATE": os.environ.get("MODULEFILE_TEMPLATE", ""),
}

# Config key -> env var mapping
_CONFIG_KEY_TO_ENV = {
    "GITLAB_TOKEN": "GITLAB_TOKEN",
    "GITLAB_API_URL": "GITLAB_API_URL",
    "DEFAULT_BRANCH": "RELEASE_DEFAULT_BRANCH",
    "TAG_PREFIX": "RELEASE_TAG_PREFIX",
    "REMOTE": "RELEASE_REMOTE",
    "VERIFY_SSL": "GITLAB_VERIFY_SSL",
    "UPDATE_DEFAULT_BRANCH": "RELEASE_UPDATE_DEFAULT_BRANCH",
    "DEPLOY_BASE_PATH": "DEPLOY_BASE_PATH",
    "BUNDLE_SUBMODULE_DIR": "BUNDLE_SUBMODULE_DIR",
    "BUNDLE_NAME": "BUNDLE_NAME",
    "MODULEFILE_TEMPLATE": "MODULEFILE_TEMPLATE",
}

# Known config keys
_KNOWN_KEYS = set(_CONFIG_KEY_TO_ENV.keys())


@dataclass
class Config:
    """Resolved configuration values."""

    gitlab_token: str = ""
    gitlab_api_url: str = "https://gitlab.com/api/v4"
    default_branch: str = "main"
    tag_prefix: str = "v"
    remote: str = "origin"
    verify_ssl: bool = False
    update_default_branch: bool = True
    deploy_base_path: str = ""
    bundle_submodule_dir: str = ""
    bundle_name: str = ""
    modulefile_template: str = ""


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


def _apply_conf(config: Config, conf: dict) -> None:
    """Apply a parsed config dict to a Config object."""
    if "GITLAB_TOKEN" in conf:
        config.gitlab_token = conf["GITLAB_TOKEN"]
    if "GITLAB_API_URL" in conf:
        config.gitlab_api_url = conf["GITLAB_API_URL"]
    if "DEFAULT_BRANCH" in conf:
        config.default_branch = conf["DEFAULT_BRANCH"]
    if "TAG_PREFIX" in conf:
        config.tag_prefix = conf["TAG_PREFIX"]
    if "REMOTE" in conf:
        config.remote = conf["REMOTE"]
    if "VERIFY_SSL" in conf:
        config.verify_ssl = conf["VERIFY_SSL"].lower() in ("true", "1", "yes")
    if "UPDATE_DEFAULT_BRANCH" in conf:
        config.update_default_branch = conf["UPDATE_DEFAULT_BRANCH"].lower() in ("true", "1", "yes")
    if "DEPLOY_BASE_PATH" in conf:
        config.deploy_base_path = conf["DEPLOY_BASE_PATH"]
    if "BUNDLE_SUBMODULE_DIR" in conf:
        config.bundle_submodule_dir = conf["BUNDLE_SUBMODULE_DIR"]
    if "BUNDLE_NAME" in conf:
        config.bundle_name = conf["BUNDLE_NAME"]
    if "MODULEFILE_TEMPLATE" in conf:
        config.modulefile_template = conf["MODULEFILE_TEMPLATE"]


def load_config(
    config_file: str = "",
    repo_root: str = "",
    cli_deploy_path: str = "",
) -> Config:
    """Load configuration with multi-level priority.

    Args:
        config_file: Explicit config file path (from --config).
        repo_root: Repository root directory.
        cli_deploy_path: CLI --deploy-path override.

    Returns:
        Resolved Config object.
    """
    config = Config()
    home = Path.home()

    # 0. Load token from ~/.gitlab_token if not set via env
    if not _ENV_SNAPSHOT["GITLAB_TOKEN"]:
        token_file = home / ".gitlab_token"
        if token_file.is_file():
            _warn_file_permissions(str(token_file))
            token = token_file.read_text().strip()
            if token:
                config.gitlab_token = token
                log_info("Loaded token from ~/.gitlab_token")

    # 1. User-level config
    user_conf = home / ".release.conf"
    if user_conf.is_file():
        _warn_file_permissions(str(user_conf))
        log_info(f"Loading config: {user_conf}")
        _apply_conf(config, _parse_conf_file(str(user_conf)))

    # 2. Repo-level config
    if repo_root:
        repo_conf = os.path.join(repo_root, ".release.conf")
        if os.path.isfile(repo_conf):
            _warn_file_permissions(repo_conf)
            log_info(f"Loading config: {repo_conf}")
            _apply_conf(config, _parse_conf_file(repo_conf))

    # 3. Explicit --config file
    if config_file:
        if not os.path.isfile(config_file):
            log_error(f"Config file not found: {config_file}")
            raise SystemExit(1)
        _warn_file_permissions(config_file)
        log_info(f"Loading config: {config_file}")
        _apply_conf(config, _parse_conf_file(config_file))

    # 4. Env vars override everything (from snapshot)
    if _ENV_SNAPSHOT["GITLAB_TOKEN"]:
        config.gitlab_token = _ENV_SNAPSHOT["GITLAB_TOKEN"]
    if _ENV_SNAPSHOT["GITLAB_API_URL"]:
        config.gitlab_api_url = _ENV_SNAPSHOT["GITLAB_API_URL"]
    if _ENV_SNAPSHOT["RELEASE_DEFAULT_BRANCH"]:
        config.default_branch = _ENV_SNAPSHOT["RELEASE_DEFAULT_BRANCH"]
    if _ENV_SNAPSHOT["RELEASE_TAG_PREFIX"]:
        config.tag_prefix = _ENV_SNAPSHOT["RELEASE_TAG_PREFIX"]
    if _ENV_SNAPSHOT["RELEASE_REMOTE"]:
        config.remote = _ENV_SNAPSHOT["RELEASE_REMOTE"]
    if _ENV_SNAPSHOT["GITLAB_VERIFY_SSL"]:
        config.verify_ssl = _ENV_SNAPSHOT["GITLAB_VERIFY_SSL"].lower() in ("true", "1", "yes")
    if _ENV_SNAPSHOT["RELEASE_UPDATE_DEFAULT_BRANCH"]:
        val = _ENV_SNAPSHOT["RELEASE_UPDATE_DEFAULT_BRANCH"]
        config.update_default_branch = val.lower() in ("true", "1", "yes")
    if _ENV_SNAPSHOT["DEPLOY_BASE_PATH"]:
        config.deploy_base_path = _ENV_SNAPSHOT["DEPLOY_BASE_PATH"]
    if _ENV_SNAPSHOT["BUNDLE_SUBMODULE_DIR"]:
        config.bundle_submodule_dir = _ENV_SNAPSHOT["BUNDLE_SUBMODULE_DIR"]
    if _ENV_SNAPSHOT["BUNDLE_NAME"]:
        config.bundle_name = _ENV_SNAPSHOT["BUNDLE_NAME"]
    if _ENV_SNAPSHOT["MODULEFILE_TEMPLATE"]:
        config.modulefile_template = _ENV_SNAPSHOT["MODULEFILE_TEMPLATE"]

    # 5. CLI --deploy-path overrides everything
    if cli_deploy_path:
        config.deploy_base_path = cli_deploy_path

    return config
