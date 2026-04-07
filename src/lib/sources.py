"""Source adapters for the deploy tool.

Three adapters handle different tool distribution methods:

    GitAdapter      — tools hosted in git repos; clones a tagged version
    ArchiveAdapter  — tools packaged as archives on a shared disk; extracts to deploy path
    ExternalAdapter — tools already installed externally; no-op deploy, only modulefiles

All adapters implement the same interface:

    get_available_versions() -> list[str]
        Discover available semver versions (sorted ascending).

    deploy(version, deploy_base_path, tool_name, dry_run, ...) -> str
        Install the given version and return the path to the deployed directory.

Both methods raise SourceError on failure so callers can handle errors
independently (e.g. scan logs and continues; deploy exits).

The factory function build_adapter() creates the right adapter from a
tool's manifest entry based on its source.type field.
"""

import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import zipfile

from .log import log_info, log_success
from .semver import validate_semver

_GIT_TIMEOUT = int(os.environ.get("TOOLMANAGER_GIT_TIMEOUT", "120"))


class SourceError(Exception):
    """Raised when a source adapter cannot complete an operation.

    Callers decide how to handle it: ``deploy`` treats it as fatal,
    ``scan`` logs the error and continues to the next tool.
    """
    pass


_ARCHIVE_EXTENSIONS = ('.tar.gz', '.tar.bz2', '.tar.xz', '.tgz', '.zip')


def _find_archives(directory: str) -> list:
    """Scan *directory* for files with known archive extensions and return their paths.

    Supported extensions: ``.tar.gz``, ``.tar.bz2``, ``.tar.xz``, ``.tgz``, ``.zip``.
    """
    archives = []
    for entry in os.listdir(directory):
        path = os.path.join(directory, entry)
        if os.path.isfile(path):
            lower = entry.lower()
            if any(lower.endswith(ext) for ext in _ARCHIVE_EXTENSIONS):
                archives.append(path)
    return sorted(archives)


def _extract_archive(archive_path: str, dest: str) -> None:
    """Detect archive type by extension and extract into *dest*."""
    lower = archive_path.lower()
    if lower.endswith('.zip'):
        _extract_zip(archive_path, dest)
    else:
        _extract_tar(archive_path, dest)


def _extract_tar(archive_path: str, dest: str) -> None:
    """Extract a tar archive using the ``data`` filter (Python 3.12+).

    The ``data`` filter blocks absolute paths, ``..`` traversal, and
    device/special files — the same protections applied manually for zip.
    """
    with tarfile.open(archive_path) as tf:
        tf.extractall(path=dest, filter='data')


def _extract_zip(archive_path: str, dest: str) -> None:
    """Extract a zip archive with explicit path traversal protection.

    Every entry is checked twice: once for ``..`` components in the raw
    filename, and again by resolving the final path to ensure it stays
    inside *dest* (guards against symlink tricks).
    """
    real_dest = os.path.realpath(dest)
    with zipfile.ZipFile(archive_path) as zf:
        for info in zf.infolist():
            # Reject absolute paths and '..' components
            if info.filename.startswith('/') or any(
                part == '..' for part in info.filename.split('/')
            ):
                raise SourceError(
                    f"Unsafe path in zip archive: {info.filename}"
                )
            # Verify resolved path stays inside destination
            target = os.path.realpath(os.path.join(dest, info.filename))
            if not (target.startswith(real_dest + os.sep) or target == real_dest):
                raise SourceError(
                    f"Zip entry resolves outside target directory: "
                    f"{info.filename} → {target}"
                )
        zf.extractall(path=dest)


def _flatten_single_root(directory: str) -> None:
    """Eliminate a useless wrapper directory after extraction.

    Many archives contain a single top-level directory (e.g.
    ``tool-1.0.0/bin/...``).  This moves the contents up one level so
    the deploy path points directly at the tool files.  Does nothing if
    the directory has multiple entries or contains loose files.
    """
    entries = os.listdir(directory)
    if len(entries) != 1:
        return
    single = os.path.join(directory, entries[0])
    if not os.path.isdir(single):
        return
    # Move all contents of the single subdir up to directory
    for item in os.listdir(single):
        shutil.move(os.path.join(single, item), os.path.join(directory, item))
    os.rmdir(single)


class GitAdapter:
    """Adapter for tools hosted in a git repository.

    Discovers versions by listing remote tags matching the tag prefix
    (e.g. v1.0.0 → "1.0.0"). Deploys by shallow-cloning the tagged
    commit into deploy_base_path/tool_name/version/.

    Manifest config:
        "source": {"type": "git", "url": "<git-remote-url>"}
    """

    def __init__(self, url: str, tag_prefix: str = "v"):
        self.url = url
        self.tag_prefix = tag_prefix

    def get_available_versions(self) -> list:
        """Query the remote for tags and return sorted semver versions.

        Runs ``git ls-remote --tags`` against ``self.url``, strips the
        tag prefix, filters to valid semver, and sorts ascending.
        """
        try:
            result = subprocess.run(
                [
                    "git", "-c", "protocol.file.allow=always",
                    "ls-remote", "--tags", "--sort=v:refname", self.url,
                ],
                capture_output=True, text=True, check=True,
                timeout=_GIT_TIMEOUT,
            )
        except subprocess.TimeoutExpired as e:
            raise SourceError(
                f"Timed out listing tags from {self.url} "
                f"(timeout: {_GIT_TIMEOUT}s)"
            ) from e
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
        # git version or locale, matching the contract of ArchiveAdapter.
        versions.sort(key=lambda v: tuple(int(x) for x in v.split(".")))
        return versions

    def deploy(
        self,
        version: str,
        deploy_base_path: str,
        tool_name: str,
        dry_run: bool = False,
        install_path: str | None = None,
    ) -> str:
        """Shallow-clone the tagged commit into the deploy directory.

        Creates ``<deploy_base_path>/<tool_name>/<version>/`` (or
        *install_path* if given).  Returns the path to the cloned directory.
        """
        tag = f"{self.tag_prefix}{version}"
        deploy_dir = install_path or os.path.join(deploy_base_path, tool_name, version)

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
                timeout=_GIT_TIMEOUT,
            )
        except subprocess.TimeoutExpired as e:
            raise SourceError(
                f"Timed out cloning {tag} (timeout: {_GIT_TIMEOUT}s)"
            ) from e
        except subprocess.CalledProcessError as e:
            detail = e.stderr.strip() if e.stderr else str(e)
            raise SourceError(f"Failed to clone {tag}: {detail}") from e

        log_success(f"Cloned {tag} into {deploy_dir}")
        return deploy_dir


class _DiskVersionScanner:
    """Shared mixin for adapters that scan a directory for semver-named subdirs.

    Both ArchiveAdapter and ExternalAdapter discover versions the same way:
    listing subdirectories of source.path that match the X.Y.Z pattern.
    """

    def _scan_versions(self, path: str) -> list:
        """List subdirectories of *path* whose names are valid semver, sorted ascending."""
        if not os.path.isdir(path):
            raise SourceError(f"Source path does not exist: {path}")
        semver_re = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
        versions = [
            entry for entry in os.listdir(path)
            if semver_re.match(entry) and os.path.isdir(os.path.join(path, entry))
        ]
        versions.sort(key=lambda v: tuple(int(x) for x in v.split(".")))
        return versions


class ArchiveAdapter(_DiskVersionScanner):
    """Adapter for tools distributed as archives on a shared disk.

    Expects source.path to contain semver subdirectories, each holding
    one or more archive files (.tar.gz, .tar.bz2, .tar.xz, .tgz, .zip).
    Deploys by extracting archives to deploy_base_path/tool_name/version/.

    Manifest config:
        "source": {"type": "archive", "path": "/nfs/packages/tool"}

    Disk layout:
        /nfs/packages/tool/
        ├── 1.0.0/
        │   └── tool-1.0.0.tar.gz
        └── 2.0.0/
            └── tool-2.0.0.tar.gz
    """

    def __init__(self, path: str):
        self.path = path

    def get_available_versions(self) -> list:
        return self._scan_versions(self.path)

    def deploy(
        self,
        version: str,
        deploy_base_path: str,
        tool_name: str,
        dry_run: bool = False,
        install_path: str | None = None,
        flatten_archive: bool = True,
    ) -> str:
        """Extract all archives from the version directory into the deploy target.

        Archives are extracted into a temp directory first, optionally
        flattened (see ``_flatten_single_root``), then copied to the
        final location.  On failure the partially-created target is removed.
        """
        version_dir = os.path.join(self.path, version)
        if not os.path.isdir(version_dir):
            raise SourceError(
                f"Version directory does not exist: {version_dir}"
            )

        archives = _find_archives(version_dir)
        if not archives:
            raise SourceError(
                f"No archives found in {version_dir}. "
                f"If the tool is pre-installed, use source type 'external' instead."
            )

        target = install_path or os.path.join(deploy_base_path, tool_name, version)

        if dry_run:
            log_info(
                f"[dry-run] Would extract {len(archives)} archive(s) "
                f"from {version_dir} to {target}"
            )
            return target

        log_info(f"Extracting {len(archives)} archive(s) to {target}...")
        tmp_dir = None
        try:
            tmp_dir = tempfile.mkdtemp(prefix="deploy_extract_")
            for archive_path in archives:
                _extract_archive(archive_path, tmp_dir)

            if flatten_archive:
                _flatten_single_root(tmp_dir)

            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copytree(tmp_dir, target)
            log_success(f"Extracted to {target}")
        except SourceError:
            if os.path.isdir(target):
                shutil.rmtree(target, ignore_errors=True)
            raise
        except (tarfile.TarError, zipfile.BadZipFile, OSError) as e:
            if os.path.isdir(target):
                shutil.rmtree(target, ignore_errors=True)
            raise SourceError(
                f"Failed to extract archives to {target}: {e}"
            ) from e
        finally:
            if tmp_dir and os.path.isdir(tmp_dir):
                shutil.rmtree(tmp_dir, ignore_errors=True)

        return target


class ExternalAdapter(_DiskVersionScanner):
    """Adapter for tools installed externally (e.g. by IT or a package manager).

    The tool is already present on disk — deploy is a no-op that returns the
    existing version directory. Only modulefiles are written. Deploy and
    upgrade commands block external tools unless --force is given.

    Manifest config:
        "source": {"type": "external", "path": "/opt/external/matlab"}

    Disk layout (maintained outside toolmanager):
        /opt/external/matlab/
        ├── 2024.1.0/
        │   └── bin/matlab
        └── 2024.2.0/
            └── bin/matlab
    """

    def __init__(self, path: str):
        self.path = path

    def get_available_versions(self) -> list:
        return self._scan_versions(self.path)

    def deploy(
        self,
        version: str,
        deploy_base_path: str,
        tool_name: str,
        dry_run: bool = False,
        install_path: str | None = None,
    ) -> str:
        """Return the version directory path as-is — no files are copied."""
        version_dir = os.path.join(self.path, version)
        if not os.path.isdir(version_dir):
            raise SourceError(
                f"Version directory does not exist: {version_dir}"
            )
        if dry_run:
            log_info(f"[dry-run] Would use external source: {version_dir}")
        else:
            log_info(f"Using external source: {version_dir}")
        return version_dir


def build_adapter(tool_entry: dict, tag_prefix: str = "v"):
    """Create the correct source adapter for a tool based on its ``source.type``.

    This is the only place that maps the string ``"git"`` / ``"archive"`` /
    ``"external"`` to the corresponding adapter class.  Returns an adapter
    instance ready to call ``get_available_versions()`` or ``deploy()``.

    Raises ``SystemExit`` for unrecognised source types.
    """
    from .log import log_error
    source = tool_entry["source"]
    src_type = source["type"]
    if src_type == "git":
        return GitAdapter(url=source["url"], tag_prefix=tag_prefix)
    elif src_type == "archive":
        return ArchiveAdapter(path=source["path"])
    elif src_type == "external":
        return ExternalAdapter(path=source["path"])
    else:
        log_error(f"Unknown source type: {src_type}")
        raise SystemExit(1)
