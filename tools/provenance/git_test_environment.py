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


HERMETIC_GIT_CONFIG = (
    ("user.name", "t"),
    ("user.email", "t@t"),
    ("init.defaultBranch", "main"),
    ("core.hooksPath", "/dev/null"),
    ("core.fsmonitor", "false"),
    ("core.untrackedCache", "false"),
    ("commit.gpgsign", "false"),
    ("maintenance.auto", "false"),
    ("gc.auto", "0"),
)


def hermetic_git_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """An environment in which Git reads no global or system configuration.

    A runner's own ~/.gitconfig, a system gitconfig, or an inherited GIT_* variable
    must not change how a fixture repository behaves.
    """
    env = {key: value for key, value in (os.environ if base is None else base).items()
           if not key.startswith("GIT_CONFIG_") and key not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
    env.update(GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0",
               GIT_CONFIG_COUNT=str(len(HERMETIC_GIT_CONFIG)))
    for index, (key, value) in enumerate(HERMETIC_GIT_CONFIG):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = value
    return env


def run_git(root, *args: str, input=None, text: bool = True):
    """Run git in a fixture repository; on failure raise with git's own stderr in the message."""
    import subprocess
    done = subprocess.run(["git", "-C", str(root), *args], input=input, capture_output=True, text=text,
                          env=hermetic_git_env())
    if done.returncode != 0:
        stderr = done.stderr if text else done.stderr.decode(errors="replace")
        raise RuntimeError(f"git {' '.join(args)} failed in {root} (exit {done.returncode}): {stderr.strip()}")
    return done.stdout
