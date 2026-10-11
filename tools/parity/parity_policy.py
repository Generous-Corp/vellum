#!/usr/bin/env python3
"""Fail-closed policy for the parity harness: path containment, licensed
sources, regions, and the provenance of every observation.

Kept apart from parity_harness.py so each invariant can be read and tested on
its own. The harness re-exports these names; its fingerprint covers this file.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
from typing import Any

SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
# Which artifact an implementation's observations come from. Parity is only a
# claim between two implementations of the same authority; a join across
# authorities (a browser capture against a native render) is diagnostic.
AUTHORITIES = ("native-render", "materialized-runtime", "browser-source")
# Licences under which a third-party corpus source may be checked in or staged.
PERMISSIVE_LICENSES = ("MIT", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "ISC", "Zlib", "BSL-1.0",
                       "CC0-1.0", "0BSD")
# Shells that run a command string given with -c (interpreters with an inline
# program flag are treated the same way).
SHELLS = ("sh", "bash", "zsh", "dash", "ksh", "env")


class HarnessError(ValueError):
    """A malformed corpus, implementation, or manifest. Never a lane result."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def resolve_under(root: Path, relative: str, label: str) -> Path:
    """A path the corpus names, proven to stay inside root: no absolute path,
    no `..`, and no symlink anywhere along it or inside it."""
    if not isinstance(relative, str) or not relative:
        raise HarnessError(f"{label}: empty path")
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise HarnessError(f"{label}: {relative!r} must be relative and must not contain '..'")
    root_real = Path(os.path.realpath(root))
    current = Path(root)
    for part in candidate.parts:
        current = current / part
        if current.is_symlink():
            raise HarnessError(f"{label}: {relative!r} passes through a symlink ({current})")
    real = Path(os.path.realpath(current))
    if real != root_real and root_real not in real.parents:
        raise HarnessError(f"{label}: {relative!r} resolves outside {root}")
    if current.is_dir():
        for inner in current.rglob("*"):
            if inner.is_symlink():
                raise HarnessError(f"{label}: {relative!r} contains a symlink ({inner})")
    return current


def region_bounds_errors(region: dict[str, Any], rect: tuple[int, int, int, int],
                         sizes: tuple[tuple[int, int], ...]) -> list[str]:
    """A declared region must lie wholly inside every rendered image; it is
    never shortened to fit."""
    x, y, w, h = rect
    errors = []
    for side, (width, height) in zip(("reference", "candidate"), sizes):
        if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > width or y + h > height:
            errors.append(f"region {region['name']} ({x},{y} {w}x{h} px) not fully present in the {side} "
                          f"render ({width}x{height})")
    return errors


def validate_provenance(case: dict[str, Any]) -> None:
    """A third-party source is licensed, pinned, and checked byte for byte."""
    prov = case.get("provenance")
    case_id = case["case_id"]
    if not isinstance(prov, dict):
        raise HarnessError(f"{case_id}: an external source needs provenance (license, repository, commit, sha256)")
    if prov.get("license") not in PERMISSIVE_LICENSES:
        raise HarnessError(f"{case_id}: provenance license {prov.get('license')!r} is not a recorded permissive licence")
    if not prov.get("repository") or not SHA1_RE.match(str(prov.get("commit", ""))):
        raise HarnessError(f"{case_id}: provenance needs repository and a 40-hex commit")
    if not SHA256_RE.match(str(prov.get("sha256", ""))):
        raise HarnessError(f"{case_id}: provenance needs the source's sha256")


def validate_regions(case: dict[str, Any]) -> None:
    """Named regions in design px; each is scored on its own in the raster lane."""
    names = set()
    for region in case.get("regions", []):
        if not isinstance(region, dict) or not region.get("name") or region["name"] in names:
            raise HarnessError(f"{case['case_id']}: regions need unique names")
        names.add(region["name"])
        x, y, w, h = (region.get(k) for k in ("x", "y", "w", "h"))
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in (x, y, w, h)) or w <= 0 or h <= 0:
            raise HarnessError(f"{case['case_id']}: region {region['name']} needs integer x, y and positive w, h")
        if x < 0 or y < 0 or x + w > case["width"] or y + h > case["height"]:
            raise HarnessError(f"{case['case_id']}: region {region['name']} lies outside the {case['width']}x"
                               f"{case['height']} viewport")


def snapshot(directory: Path) -> dict[str, str]:
    """Every regular file under the directory, by relative posix path."""
    if not directory.is_dir():
        return {}
    return {p.relative_to(directory).as_posix(): sha256_file(p)
            for p in directory.rglob("*") if p.is_file() and not p.is_symlink()}


def producing_binary(argv: list[str]) -> dict[str, Any]:
    """The binary a step runs, digested at the moment it runs, and the script
    it interprets if any: an observation is bound to what actually drew it."""
    info: dict[str, Any] = {}
    if not argv:
        return info
    executable = Path(argv[0]) if os.sep in argv[0] else Path(shutil.which(argv[0]) or argv[0])
    if executable.is_file():
        info["binary"] = str(executable)
        info["binary_sha256"] = sha256_file(executable)
    if len(argv) > 1 and Path(argv[1]).suffix in (".py", ".mjs", ".js", ".sh") and Path(argv[1]).is_file():
        info["script_sha256"] = sha256_file(Path(argv[1]))
    name = executable.name
    inline_flags = ("-c",) if name in SHELLS or name.startswith("python") else (
        ("-e", "--eval", "-p", "--print") if name in ("node", "perl", "ruby") else ())
    if any(flag in argv[1:] for flag in inline_flags):
        info["delegated"] = True
    return info


def provenance_errors(run: dict[str, Any], identity: dict[str, Any], side: str,
                      lanes: list[str], lane_artifacts: dict[str, tuple[str, ...]]) -> list[str]:
    """Every observation a selected lane reads must have been produced by a
    binary (or script) the side's identity declares, digested at run time."""
    declared = {t.get("sha256") for t in (identity.get("tools") or {}).values()}
    passthrough = set(identity.get("passthrough_binaries") or [])
    errors = []
    for lane in lanes:
        for name in lane_artifacts.get(lane, ()):
            if name not in run.get("artifacts", {}):
                continue
            origin = (run.get("produced_by") or {}).get(name)
            if origin is None:
                errors.append(f"{side}/{name}: no producing step recorded")
            elif origin.get("delegated"):
                # A shell command string runs whatever it names; the shell's own
                # digest says nothing about the program that wrote the file.
                errors.append(f"{side}/{name}: produced through a shell command string "
                              f"({origin.get('binary')} -c); run the producer directly")
            elif origin.get("binary") in passthrough:
                continue
            elif origin.get("binary_sha256") not in declared and origin.get("script_sha256") not in declared:
                errors.append(f"{side}/{name}: produced by an undeclared binary {origin.get('binary')} "
                              f"({str(origin.get('binary_sha256'))[:12]})")
    return errors
