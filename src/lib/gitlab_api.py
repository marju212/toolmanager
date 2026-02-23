"""GitLab API operations via urllib.request.

Token is passed via Authorization header (never as CLI arg).
Supports retry on 5xx errors.
"""

import json
import ssl
import time
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .log import log_info, log_warn, log_error, log_success


# Configurable retry delay (seconds) — tests can override this.
RETRY_DELAY = 2


def gitlab_request(
    method: str,
    path: str,
    token: str,
    api_url: str = "https://gitlab.com/api/v4",
    data: Optional[dict] = None,
    verify_ssl: bool = True,
) -> dict:
    """Make a GitLab API request.

    Args:
        method: HTTP method (GET, POST, PUT, etc.)
        path: API endpoint path (e.g. /projects/123)
        token: GitLab private token
        api_url: Base API URL
        data: Request body (will be JSON-encoded)
        verify_ssl: Whether to verify SSL certificates

    Returns:
        Parsed JSON response dict.

    Raises:
        SystemExit: On authentication errors or persistent failures.
        RuntimeError: On non-recoverable HTTP errors.
    """
    if not token:
        log_error("GITLAB_TOKEN is not set. Export it or add it to .release.conf.")
        raise SystemExit(1)

    url = f"{api_url}{path}"
    headers = {
        "PRIVATE-TOKEN": token,
        "Content-Type": "application/json",
    }

    body = json.dumps(data).encode("utf-8") if data else None

    # SSL context
    ctx = None
    if not verify_ssl:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    max_retries = 1
    for attempt in range(max_retries + 1):
        if attempt > 0:
            log_warn(f"Retrying GitLab API request (attempt {attempt + 1})...")
            time.sleep(RETRY_DELAY)

        try:
            req = Request(url, data=body, headers=headers, method=method)
            resp = urlopen(req, context=ctx, timeout=30)
            resp_body = resp.read().decode("utf-8")
            if resp_body:
                return json.loads(resp_body)
            return {}

        except HTTPError as e:
            code = e.code
            resp_body = ""
            try:
                resp_body = e.read().decode("utf-8")
            except Exception:
                pass

            # Retry on 5xx
            if code >= 500 and attempt < max_retries:
                log_warn(f"GitLab API returned HTTP {code}, will retry...")
                continue

            if code == 401:
                log_error(f"GitLab API authentication failed (HTTP 401): {path}")
                raise SystemExit(1)

            log_error(f"GitLab API error (HTTP {code}): {path}")
            if resp_body:
                log_error(f"Response: {resp_body}")
            raise RuntimeError(f"GitLab API error (HTTP {code})")

        except URLError as e:
            if attempt < max_retries:
                continue
            log_error(f"Failed to connect to GitLab API: {url}")
            log_error(f"Error: {e.reason}")
            log_error("Check GITLAB_API_URL, network connectivity, and SSL settings.")
            raise RuntimeError(f"Connection failed: {e.reason}")

    # Should not reach here
    raise RuntimeError("GitLab API request failed after retries")


def get_project_id(
    remote_url: str,
    token: str,
    api_url: str = "https://gitlab.com/api/v4",
    verify_ssl: bool = True,
    dry_run: bool = False,
    project_path: str = "",
) -> str:
    """Get GitLab project ID from remote URL.

    Args:
        remote_url: Git remote URL (used to parse project path if project_path not given)
        token: GitLab private token
        api_url: Base API URL
        verify_ssl: Whether to verify SSL
        dry_run: If True, skip API call
        project_path: Pre-parsed project path (optional)

    Returns:
        Project ID string, or 'DRY_RUN_ID' in dry-run mode.
    """
    from .git import parse_project_path

    if not project_path:
        project_path = parse_project_path(remote_url)
        if not project_path:
            log_error(f"Cannot parse project path from remote URL: {remote_url}")
            raise SystemExit(1)

    log_info(f"Detecting GitLab project ID for: {project_path}")

    if dry_run:
        log_info("[dry-run] Would query GitLab API for project ID")
        return "DRY_RUN_ID"

    encoded_path = quote(project_path, safe="")
    response = gitlab_request("GET", f"/projects/{encoded_path}",
                              token=token, api_url=api_url,
                              verify_ssl=verify_ssl)

    project_id = response.get("id")
    if not project_id:
        log_error("Could not determine GitLab project ID.")
        log_error(f"Check that GITLAB_TOKEN has access to: {project_path}")
        raise SystemExit(1)

    log_success(f"Project ID: {project_id}")
    return str(project_id)


def create_merge_request(
    project_id: str,
    source_branch: str,
    target_branch: str,
    title: str,
    description: str,
    token: str,
    api_url: str = "https://gitlab.com/api/v4",
    verify_ssl: bool = True,
    dry_run: bool = False,
) -> str:
    """Create a merge request on GitLab.

    Returns the MR web URL.
    """
    log_info(f"Creating merge request: {source_branch} \u2192 {target_branch}")

    if dry_run:
        log_info(f"[dry-run] Would create MR: {source_branch} \u2192 {target_branch}")
        return "https://gitlab.com (dry-run)"

    data = {
        "source_branch": source_branch,
        "target_branch": target_branch,
        "title": title,
        "description": description,
        "remove_source_branch": False,
    }

    response = gitlab_request("POST",
                              f"/projects/{project_id}/merge_requests",
                              token=token, data=data, api_url=api_url,
                              verify_ssl=verify_ssl)

    mr_url = response.get("web_url", "")
    if not mr_url:
        log_warn("Merge request created but could not retrieve URL.")
        return "(unknown)"

    log_success(f"Merge request created: {mr_url}")
    return mr_url


def update_default_branch(
    project_id: str,
    branch: str,
    token: str,
    api_url: str = "https://gitlab.com/api/v4",
    verify_ssl: bool = True,
    dry_run: bool = False,
) -> None:
    """Update the default branch of a GitLab project."""
    log_info(f"Updating GitLab default branch to '{branch}'...")

    if dry_run:
        log_info(f"[dry-run] Would update default branch to '{branch}'")
        return

    gitlab_request("PUT", f"/projects/{project_id}",
                   token=token, data={"default_branch": branch},
                   api_url=api_url, verify_ssl=verify_ssl)

    log_success(f"Default branch updated to '{branch}'.")
