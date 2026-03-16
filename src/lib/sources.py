"""Source adapters for the deploy tool.

Each adapter implements:
    get_available_versions() -> list[str]   # semver strings, sorted ascending
    deploy(version, deploy_base_path, tool_name, dry_run) -> str  # deploy root

Both methods raise SourceError on failure so callers can handle the error
message themselves (e.g. scan continues; deploy exits).
"""

import os
import re
import subprocess

from .log import log_info, log_success
from .semver import validate_semver


class SourceError(Exception):
    """Raised when a source adapter cannot complete an operation."""
    pass


class GitAdapter:
    """Adapter for tools hosted in a git repository."""

    def __init__(self, url: str, tag_prefix: str = "v"):
        self.url = url
        self.tag_prefix = tag_prefix

    def get_available_versions(self) -> list:
        """List available semver versions via git ls-remote."""
        try:
            result = subprocess.run(
                [
                    "git", "-c", "protocol.file.allow=always",
                    "ls-remote", "--tags", "--sort=v:refname", self.url,
                ],
                capture_output=True, text=True, check=True,
            )
        except subprocess.CalledProcessError as e:
            detail = e.stderr.strip() if e.stderr else str(e)
            raise SourceError(
                f"Cannot list tags from {self.url}: {detail}"
            ) from e

        versions = []
        prefix = f"refs/tags/{self.tag_prefix}"
        for line in result.stdout.splitlines():
            # Skip peeled tags (<sha>\trefs/tags/v1.0.0^{})
            if line.endswith("^{}"):
                continue
            parts = line.split("\t", 1)
            if len(parts) != 2:
                continue
            ref = parts[1].strip()
            if ref.startswith(prefix):
                tag_version = ref[len(prefix):]
                try:
                    validate_semver(tag_version)
                    versions.append(tag_version)
                except ValueError:
                    pass

        # Explicit Python sort guarantees consistent ordering regardless of
        # git version or locale, matching the contract of DiskAdapter.
        versions.sort(key=lambda v: tuple(int(x) for x in v.split(".")))
        return versions

    def deploy(
        self,
        version: str,
        deploy_base_path: str,
        tool_name: str,
        dry_run: bool = False,
    ) -> str:
        """Clone tagged version into deploy_base_path/tool_name/version/."""
        tag = f"{self.tag_prefix}{version}"
        deploy_dir = os.path.join(deploy_base_path, tool_name, version)

        if dry_run:
            log_info(f"[dry-run] Would clone {tag} into {deploy_dir}")
            return deploy_dir

        log_info(f"Cloning {tag} into {deploy_dir}...")
        try:
            os.makedirs(os.path.dirname(deploy_dir), exist_ok=True)
        except OSError as e:
            raise SourceError(f"Cannot create deploy directory: {e}") from e

        try:
            subprocess.run(
                [
                    "git", "-c", "protocol.file.allow=always",
                    "clone", "--branch", tag, "--depth", "1",
                    self.url, deploy_dir,
                ],
                capture_output=True, text=True, check=True,
            )
        except subprocess.CalledProcessError as e:
            detail = e.stderr.strip() if e.stderr else str(e)
            raise SourceError(f"Failed to clone {tag}: {detail}") from e

        log_success(f"Cloned {tag} into {deploy_dir}")
        return deploy_dir


class DiskAdapter:
    """Adapter for tools already deployed on disk at a known path."""

    def __init__(self, path: str):
        self.path = path

    def get_available_versions(self) -> list:
        """Scan source.path for semver-named subdirectories."""
        if not os.path.isdir(self.path):
            raise SourceError(
                f"Disk source path does not exist: {self.path}"
            )

        semver_re = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
        versions = []
        for entry in os.listdir(self.path):
            if semver_re.match(entry) and os.path.isdir(
                os.path.join(self.path, entry)
            ):
                versions.append(entry)

        versions.sort(key=lambda v: tuple(int(x) for x in v.split(".")))
        return versions

    def deploy(
        self,
        version: str,
        deploy_base_path: str,  # unused: interface compatibility with GitAdapter
        tool_name: str,         # unused: interface compatibility with GitAdapter
        dry_run: bool = False,
    ) -> str:
        """No-op deploy — tool is already on disk. Validates version dir exists."""
        root = os.path.join(self.path, version)
        if not os.path.isdir(root):
            raise SourceError(
                f"Version directory does not exist for disk source: {root}"
            )
        if dry_run:
            log_info(f"[dry-run] Disk source: would use {root}")
        else:
            log_info(f"Using disk source: {root}")
        return root


def build_adapter(tool_entry: dict, tag_prefix: str = "v"):
    """Build the appropriate adapter from a tool manifest entry."""
    from .log import log_error
    source = tool_entry["source"]
    src_type = source["type"]
    if src_type == "git":
        return GitAdapter(url=source["url"], tag_prefix=tag_prefix)
    elif src_type == "disk":
        return DiskAdapter(path=source["path"])
    else:
        log_error(f"Unknown source type: {src_type}")
        raise SystemExit(1)
