"""Shared Git settings for provenance tests that use temporary repositories."""

from __future__ import annotations

import os


def disable_auto_maintenance() -> None:
    """Keep fixture repositories quiescent before temporary-directory cleanup."""
    count = int(os.environ.get("GIT_CONFIG_COUNT", "0"))
    for index in range(count):
        if os.environ.get(f"GIT_CONFIG_KEY_{index}") == "maintenance.auto":
            os.environ[f"GIT_CONFIG_VALUE_{index}"] = "false"
            return
    os.environ[f"GIT_CONFIG_KEY_{count}"] = "maintenance.auto"
    os.environ[f"GIT_CONFIG_VALUE_{count}"] = "false"
    os.environ["GIT_CONFIG_COUNT"] = str(count + 1)


disable_auto_maintenance()
