"""Interactive prompts for CLI tools.

Provides two prompts used throughout the release and deploy workflows:

    confirm()         — yes/no confirmation; auto-accepts in dry-run or
                        non-interactive mode so CI pipelines can run unattended.
    prompt_version()  — interactive version picker that shows patch/minor/major
                        suggestions derived from the current version.
"""

import sys

from .log import log_info, log_error
from .semver import validate_semver, suggest_versions


def confirm(message: str = "Continue?", dry_run: bool = False,
            non_interactive: bool = False) -> bool:
    """Ask the user a yes/no question and return their answer.

    Behaviour in special modes:

    - **dry_run** — logs the question with a ``[dry-run]`` prefix and
      returns ``True`` without waiting for input.
    - **non_interactive** — logs with ``[non-interactive]`` and returns
      ``True`` (used in CI where stdin is not a terminal).

    Returns ``False`` on EOF/Ctrl-C so callers can abort gracefully.
    """
    if dry_run:
        log_info(f"[dry-run] Would prompt: {message} [y/N]")
        return True
    if non_interactive:
        log_info(f"[non-interactive] Auto-confirming: {message}")
        return True

    try:
        answer = input(f"{message} [y/N] ")
    except (EOFError, KeyboardInterrupt):
        print("", file=sys.stderr)
        return False

    return answer.strip().lower() in ("y", "yes")


def prompt_version(
    current: str,
    tag_prefix: str,
    non_interactive: bool = False,
    cli_version: str = "",
) -> str:
    """Let the user pick the next release version interactively.

    Shows a numbered menu of suggested bumps (patch / minor / major) plus
    a "custom" option where the user can type any valid ``X.Y.Z`` string.

    Short-circuits:

    - If *cli_version* is provided, validates and returns it immediately
      (no prompt shown).
    - If *non_interactive* is ``True`` and no *cli_version* was given,
      raises ``SystemExit`` because there is no way to choose.

    Returns:
        The chosen version string **without** the tag prefix.
    """
    if cli_version:
        validate_semver(cli_version)
        return cli_version

    if non_interactive:
        log_error("--version is required in non-interactive mode.")
        raise SystemExit(1)

    suggestions = suggest_versions(current)

    print("", file=sys.stderr)
    print(f"Current version: {tag_prefix}{current}", file=sys.stderr)
    print("", file=sys.stderr)
    print(f"  1) Patch  \u2192 {tag_prefix}{suggestions['patch']}", file=sys.stderr)
    print(f"  2) Minor  \u2192 {tag_prefix}{suggestions['minor']}", file=sys.stderr)
    print(f"  3) Major  \u2192 {tag_prefix}{suggestions['major']}", file=sys.stderr)
    print("  4) Custom", file=sys.stderr)
    print("", file=sys.stderr)

    while True:
        try:
            choice = input("Select version bump [1-4]: ")
        except (EOFError, KeyboardInterrupt):
            print("", file=sys.stderr)
            raise SystemExit(1)

        choice = choice.strip()
        if choice == "1":
            return suggestions["patch"]
        elif choice == "2":
            return suggestions["minor"]
        elif choice == "3":
            return suggestions["major"]
        elif choice == "4":
            try:
                version = input("Enter version (X.Y.Z): ")
            except (EOFError, KeyboardInterrupt):
                print("", file=sys.stderr)
                raise SystemExit(1)
            try:
                validate_semver(version.strip())
                return version.strip()
            except ValueError as e:
                log_error(str(e))
        else:
            log_error(f"Invalid choice: '{choice}'. Enter 1, 2, 3, or 4.")
