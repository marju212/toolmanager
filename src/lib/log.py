"""Color-coded logging to stderr."""

import sys

_use_color = hasattr(sys.stderr, "isatty") and sys.stderr.isatty()


def _color(code: str) -> str:
    if _use_color:
        return f"\033[{code}m"
    return ""


def _reset() -> str:
    if _use_color:
        return "\033[0m"
    return ""


def log_info(msg: str) -> None:
    print(f"{_color('94')}\u2139 {msg}{_reset()}", file=sys.stderr)


def log_warn(msg: str) -> None:
    print(f"{_color('33')}\u26a0 {msg}{_reset()}", file=sys.stderr)


def log_error(msg: str) -> None:
    print(f"{_color('31')}\u2716 {msg}{_reset()}", file=sys.stderr)


def log_success(msg: str) -> None:
    print(f"{_color('32')}\u2714 {msg}{_reset()}", file=sys.stderr)
