#!/usr/bin/env python3
"""Provide the one CMake that generates the gpu-runtime package files.

Four files in vellum-gpu-runtime-<version>-<target>.tar.gz are written by CMake
itself: the package config, its version file and the two target exports. Their
text is a property of the CMake release, not only of the source: one release
rewords the version file's comments, another raises the policy range an export
names. The archive is built on one host and held to its source on another, so
both must generate those files with the same CMake or an exact comparison
tells two hosts apart instead of two sources.

gpu-runtime/cmake-lock.json names that CMake: a Kitware release, its macOS
archive and the archive's SHA-256. The builder configures, builds and installs
with it, and the verifier regenerates with it. Nothing on PATH takes part.

The archive is fetched from Kitware's GitHub release, never from a URL the lock
supplies, and is used only when its bytes match the locked digest. A cached
copy is hashed again on every use, so a stale or planted file is replaced, not
trusted. The unpacked CMake must then report the locked version.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import time
from typing import Any, Callable
import urllib.error
import urllib.request

SCHEMA = "vellum.gpu-runtime-cmake-lock.v1"
LOCK = Path("gpu-runtime/cmake-lock.json")
PUBLISHER = "https://github.com/Kitware/CMake"
PLATFORM = "macos-arm64"
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
SHA = re.compile(r"^[0-9a-f]{64}$")
CACHE_ENV = "VELLUM_PINNED_CMAKE_CACHE"
ATTEMPTS = 4


class PinnedCMakeError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PinnedCMakeError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_lock(repo: Path) -> dict[str, str]:
    """`version`, `asset`, `sha256` and `url` of the CMake `repo` pins. The URL
    is built here from Kitware's release layout; the lock cannot name another."""
    path = repo / LOCK
    require(path.is_file(), f"{LOCK} is missing: the source pins no CMake for its package files")
    lock = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(lock, dict) and lock.get("schema") == SCHEMA, f"{LOCK} schema is unsupported")
    require(set(lock) == {"schema", "publisher", "version", "platforms"}, f"{LOCK} fields differ")
    require(lock["publisher"] == PUBLISHER, f"{LOCK} names another publisher than {PUBLISHER}")
    version = lock["version"]
    require(isinstance(version, str) and VERSION.fullmatch(version) is not None,
            f"{LOCK} version is not <major>.<minor>.<patch>")
    platforms = lock["platforms"]
    require(isinstance(platforms, dict) and set(platforms) == {PLATFORM}
            and isinstance(platforms[PLATFORM], dict)
            and set(platforms[PLATFORM]) == {"asset", "sha256"},
            f"{LOCK} must pin exactly the {PLATFORM} archive")
    asset, sha256 = platforms[PLATFORM]["asset"], platforms[PLATFORM]["sha256"]
    require(asset == f"cmake-{version}-macos-universal.tar.gz",
            f"{LOCK} asset is not Kitware's macOS archive of {version}")
    require(isinstance(sha256, str) and SHA.fullmatch(sha256) is not None,
            f"{LOCK} does not hold a sha256")
    return {"version": version, "asset": asset, "sha256": sha256,
            "url": f"{PUBLISHER}/releases/download/v{version}/{asset}"}


def cache_root() -> Path:
    configured = os.environ.get(CACHE_ENV)
    return Path(configured) if configured else Path(tempfile.gettempdir()) / "vellum-pinned-cmake"


def download(url: str, target: Path, opener: Callable[[str], Any]) -> None:
    """Ride out a release CDN's brief 5xx or dropped connection; a 4xx is final."""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            with opener(url) as response, target.open("wb") as handle:
                for block in iter(lambda: response.read(1 << 20), b""):
                    handle.write(block)
            return
        except urllib.error.HTTPError as error:
            if error.code < 500 or attempt == ATTEMPTS:
                raise PinnedCMakeError(f"could not fetch {url}: HTTP {error.code}") from error
        except OSError as error:
            if attempt == ATTEMPTS:
                raise PinnedCMakeError(f"could not fetch {url}: {error}") from error
        time.sleep(2 ** attempt)


def fetch(lock: dict[str, str], cache: Path,
          opener: Callable[[str], Any] = urllib.request.urlopen) -> Path:
    """The locked archive, from the cache when its bytes still match."""
    directory = cache / lock["sha256"]
    target = directory / lock["asset"]
    if target.is_file() and not target.is_symlink() and sha256_file(target) == lock["sha256"]:
        return target
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="fetch-", dir=directory) as temporary:
        partial = Path(temporary) / lock["asset"]
        download(lock["url"], partial, opener)
        require(sha256_file(partial) == lock["sha256"],
                f"the CMake archive {lock['asset']} does not match the locked digest")
        os.replace(partial, target)
    return target


def reported_version(cmake: Path) -> str:
    completed = subprocess.run([str(cmake), "--version"], text=True, capture_output=True, check=False)
    match = re.match(r"cmake version (\S+)", completed.stdout)
    require(completed.returncode == 0 and match is not None,
            "the pinned CMake does not run on this host")
    return match.group(1)


def unpack(archive: Path, lock: dict[str, str], directory: Path) -> Path:
    """Extract the archive under `directory` and return its cmake executable."""
    directory.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, mode="r:gz") as handle:
        handle.extractall(directory, filter="data")
    cmake = (directory / lock["asset"].removesuffix(".tar.gz")
             / "CMake.app" / "Contents" / "bin" / "cmake")
    require(cmake.is_file(), f"{lock['asset']} holds no CMake.app/Contents/bin/cmake")
    found = reported_version(cmake)
    require(found == lock["version"],
            f"the pinned CMake reports version {found}, not the locked {lock['version']}")
    return cmake


def use(command: list[str], cmake: Path) -> list[str]:
    """`command`, a `cmake ...` invocation, run with the pinned executable."""
    require(bool(command) and command[0] == "cmake", "the command does not invoke cmake")
    return [str(cmake), *command[1:]]


def provide(repo: Path, directory: Path,
            opener: Callable[[str], Any] = urllib.request.urlopen) -> tuple[Path, str]:
    """`(cmake executable, version)` for the CMake `repo` pins, unpacked under
    `directory`, which the caller owns and removes."""
    lock = read_lock(repo)
    return unpack(fetch(lock, cache_root(), opener), lock, directory), lock["version"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--directory", type=Path, required=True,
                        help="unpack the pinned CMake here and print its path")
    args = parser.parse_args(argv)
    try:
        cmake, _ = provide(args.repo.resolve(), args.directory.resolve())
    except (PinnedCMakeError, OSError, ValueError, tarfile.TarError, EOFError) as error:
        print(f"pinned-cmake: {error}", file=sys.stderr)
        return 1
    print(cmake)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
