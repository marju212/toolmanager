"""Color-coded logging to stderr.

All output goes to stderr so that stdout remains clean for machine-readable
data (e.g. ``scan`` prints its version table to stdout).  Colour is
auto-detected: ANSI escape codes are only emitted when stderr is a TTY.

Four log levels are provided, each with a distinct colour and icon:

    log_info     blue  ``i``  — progress updates and informational messages
    log_warn     yellow ``!`` — non-fatal warnings the operator should notice
    log_error    red ``x``    — fatal errors (usually followed by SystemExit)
    log_success  green ``v``  — confirmation that an action completed
"""

import sys

# Detect once at import time whether the output supports colour.
_use_color = hasattr(sys.stderr, "isatty") and sys.stderr.isatty()


def _color(code: str) -> str:
    """Return an ANSI escape sequence, or empty string if colour is off."""
    if _use_color:
        return f"\033[{code}m"
    return ""


def _reset() -> str:
    """Return the ANSI reset sequence, or empty string if colour is off."""
    if _use_color:
        return "\033[0m"
    return ""


def log_info(msg: str) -> None:
    """Print an informational message (blue) to stderr."""
    print(f"{_color('94')}\u2139 {msg}{_reset()}", file=sys.stderr)


def log_warn(msg: str) -> None:
    """Print a warning message (yellow) to stderr."""
    print(f"{_color('33')}\u26a0 {msg}{_reset()}", file=sys.stderr)


def log_error(msg: str) -> None:
    """Print an error message (red) to stderr."""
    print(f"{_color('31')}\u2716 {msg}{_reset()}", file=sys.stderr)


def log_success(msg: str) -> None:
    """Print a success message (green) to stderr."""
    print(f"{_color('32')}\u2714 {msg}{_reset()}", file=sys.stderr)
