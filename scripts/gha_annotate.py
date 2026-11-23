"""Small helpers for GitHub Actions workflow-command annotations."""

from __future__ import annotations

import os


def _escape(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def error(title: str, message: str) -> None:
    """Annotate a failed check when running under GitHub Actions."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    print(f"::error title={_escape(title)}::{_escape(message)}", flush=True)
