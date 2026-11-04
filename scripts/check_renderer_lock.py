#!/usr/bin/env python3
"""Fail when any copy of the Skia/Dawn pin disagrees with gpu-runtime's lock.

gpu-runtime/renderer-lock.json is the one place the chrome/mNNN milestone and
the skia-builder digests are written. Everything else either reads it or is a
record that must agree with it:

  read      cmake/VellumSkiaDawn.cmake and .github/workflows/gpu-macos.yml load
            the lock; neither may carry a digest or milestone of its own.
  agree     provenance/third-party-lock.json (the release-eligibility lock),
            provenance/renderer-attestation.json (the builder's records) and
            the documents that quote the pin must name exactly the lock's
            milestone, asset, digests and source commits.
  nowhere   no other tracked file may contain a locked digest, so a new hand
            copy cannot appear unnoticed. Mirror export closures and verifier
            test data are recorded evidence, checked by their own verifiers.

Usage: check_renderer_lock.py [--root <vellum-checkout>]
Exit 0 when every copy agrees, 1 with one line per disagreement otherwise.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = "gpu-runtime/renderer-lock.json"
SCHEMA = "gpu-runtime.renderer-lock.v1"
PLATFORM = "macos-arm64"
HEX_64 = re.compile(r"\b[0-9a-f]{64}\b")
# Any-case digests in other files: an UPPERCASE hand copy is still a copy.
ANY_HEX_64 = re.compile(r"\b[0-9a-fA-F]{64}\b")


def digests_in(text: str) -> set[str]:
    return {match.lower() for match in ANY_HEX_64.findall(text)}
HEX_40 = re.compile(r"^[0-9a-f]{40}$")
MILESTONE = re.compile(r"\bchrome/m[0-9]+\b")

# Build inputs that must read the lock rather than restate it.
READERS = {
    "cmake/VellumSkiaDawn.cmake": "gpu_runtime_read_renderer_lock(",
    ".github/workflows/gpu-macos.yml": LOCK,
}
# Records checked field by field below.
RECORDS = ("provenance/third-party-lock.json", "provenance/renderer-attestation.json")
# Documents that quote the pin for readers; each must quote the lock exactly.
DOCUMENTS = ("README.md", "DEPENDENCIES.md", "NOTICE.md",
             "docs/provenance/renderer-release-attestation.md")
# Documents that give the asset's digest next to its name for a reader to check.
DOCUMENTS_WITH_DIGEST = ("README.md", "DEPENDENCIES.md")
# Recorded evidence that legitimately holds a digest and has its own verifier.
EVIDENCE = ("provenance/mirror/closures/*", "tools/provenance/testdata/*")


def load_lock(root: Path, problems: list[str]) -> dict | None:
    try:
        lock = json.loads((root / LOCK).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        problems.append(f"{LOCK}: cannot be read: {error}")
        return None
    if lock.get("schema") != SCHEMA:
        problems.append(f"{LOCK}: schema is {lock.get('schema')!r}, expected {SCHEMA}")
    if not isinstance(lock.get("milestone"), str) or not MILESTONE.fullmatch(lock["milestone"]):
        problems.append(f"{LOCK}: milestone is not chrome/mNNN")
    builder = lock.get("builder") or {}
    sources = lock.get("sources") or {}
    for label, value in (("builder.commit", builder.get("commit")),
                         ("sources.skia", sources.get("skia")),
                         ("sources.dawn", sources.get("dawn"))):
        if not isinstance(value, str) or not HEX_40.fullmatch(value):
            problems.append(f"{LOCK}: {label} is not a 40-character commit")
    platform = (lock.get("platforms") or {}).get(PLATFORM)
    if not isinstance(platform, dict):
        problems.append(f"{LOCK}: platform {PLATFORM} is missing")
        return None
    digests = [platform.get("sha256"), *(platform.get("archives") or {}).values()]
    if not platform.get("archives") or any(
            not isinstance(value, str) or not HEX_64.fullmatch(value) for value in digests):
        problems.append(f"{LOCK}: {PLATFORM} digests are missing or malformed")
    return None if problems else lock


def asset_url(lock: dict) -> str:
    platform = lock["platforms"][PLATFORM]
    return "/".join((lock["builder"]["repository"], "releases/download",
                     lock["milestone"], platform["asset"]))


def locked_digests(lock: dict) -> set[str]:
    platform = lock["platforms"][PLATFORM]
    return {platform["sha256"], *platform["archives"].values()}


def check_readers(root: Path, lock: dict, problems: list[str]) -> None:
    for relative, marker in READERS.items():
        text = (root / relative).read_text(encoding="utf-8")
        if marker not in text:
            problems.append(f"{relative}: does not read {LOCK}")
        for digest in sorted(digests_in(text) & locked_digests(lock)):
            problems.append(f"{relative}: restates locked digest {digest}; read it from {LOCK}")
        for milestone in sorted(set(MILESTONE.findall(text))):
            problems.append(f"{relative}: restates milestone {milestone}; read it from {LOCK}")


def check_cmake_names(root: Path, lock: dict, problems: list[str]) -> None:
    text = (root / "cmake/VellumSkiaDawn.cmake").read_text(encoding="utf-8")
    archives = lock["platforms"][PLATFORM]["archives"]
    for name in sorted(set(re.findall(r"_vellum_renderer_SHA256_(lib[A-Za-z0-9_]+\.a)", text))):
        if name not in archives:
            problems.append(f"cmake/VellumSkiaDawn.cmake: reads {name}, which the lock does not lock")


def check_third_party_lock(root: Path, lock: dict, problems: list[str]) -> None:
    relative = RECORDS[0]
    data = json.loads((root / relative).read_text(encoding="utf-8"))
    renderer = data["runtime_toolchain_dependencies"]["renderer"]
    artifact = renderer["artifact"]
    platform = lock["platforms"][PLATFORM]
    expected = {
        "repository": lock["builder"]["repository"],
        "release_tag": lock["milestone"],
        "name": platform["asset"],
        "url": asset_url(lock),
        "sha256": platform["sha256"],
        "builder_commit": lock["builder"]["commit"],
        "target": PLATFORM,
        "configuration": platform["configuration"],
        "archive_root": platform["archive_root"],
    }
    for field, value in expected.items():
        if artifact.get(field) != value:
            problems.append(f"{relative}: renderer artifact {field} is {artifact.get(field)!r}, "
                            f"the lock says {value!r}")
    archives = {row["name"]: row["sha256"] for row in renderer["archives"]}
    if archives != platform["archives"]:
        names = sorted(set(archives) ^ set(platform["archives"]) |
                       {name for name in archives if archives[name] != platform["archives"].get(name)})
        problems.append(f"{relative}: renderer archives differ from the lock: {', '.join(names)}")
    identities = renderer["source_identities"]
    for name, commit in (("skia", lock["sources"]["skia"]), ("dawn", lock["sources"]["dawn"]),
                         ("builder", lock["builder"]["commit"])):
        if identities.get(name, {}).get("commit") != commit:
            problems.append(f"{relative}: {name} source commit differs from the lock")
    if identities.get("skia", {}).get("ref") != f"refs/heads/{lock['milestone']}":
        problems.append(f"{relative}: skia source ref is not refs/heads/{lock['milestone']}")
    rows = [row for row in data["dependencies"]
            if row.get("name") == "Skia Graphite and Dawn renderer toolchain"]
    if len(rows) != 1 or rows[0].get("version") != lock["milestone"] \
            or rows[0].get("sha256") != platform["sha256"]:
        problems.append(f"{relative}: the renderer dependency row differs from the lock")


def check_attestation(root: Path, lock: dict, problems: list[str]) -> None:
    relative = RECORDS[1]
    record = json.loads((root / relative).read_text(encoding="utf-8"))
    artifact = record.get("artifact") or {}
    platform = lock["platforms"][PLATFORM]
    for field, value in (("repository", lock["builder"]["repository"]),
                         ("release_tag", lock["milestone"]),
                         ("name", platform["asset"]),
                         ("sha256", platform["sha256"])):
        if artifact.get(field) != value:
            problems.append(f"{relative}: artifact {field} is {artifact.get(field)!r}, "
                            f"the lock says {value!r}")
    identities = record.get("source_identities") or {}
    for name, commit in (("skia", lock["sources"]["skia"]), ("dawn", lock["sources"]["dawn"]),
                         ("builder", lock["builder"]["commit"])):
        if (identities.get(name) or {}).get("commit") != commit:
            problems.append(f"{relative}: {name} source commit differs from the lock")
    if record.get("build_identity", {}).get("target") != PLATFORM:
        problems.append(f"{relative}: build target is not {PLATFORM}")
    release = "/".join((lock["builder"]["repository"], "releases/download", lock["milestone"], ""))
    for url in re.findall(r'"url":\s*"([^"]+)"', json.dumps(record)):
        if not url.startswith(release):
            problems.append(f"{relative}: record {url} is not an asset of the locked release")


def check_documents(root: Path, lock: dict, problems: list[str]) -> None:
    platform = lock["platforms"][PLATFORM]
    for relative in DOCUMENTS:
        path = root / relative
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for milestone in sorted(set(MILESTONE.findall(text)) - {lock["milestone"]}):
            problems.append(f"{relative}: names {milestone}; the lock is {lock['milestone']}")
        if relative in DOCUMENTS_WITH_DIGEST and platform["sha256"] not in text:
            problems.append(f"{relative}: quotes {platform['asset']} without the lock's digest")
        for digest in sorted(digests_in(text)):
            if digest in locked_digests(lock):
                continue
            # A stale renderer digest next to the asset name is the drift this
            # catches; other digests in these documents pin other inputs.
            for line in text.splitlines():
                if digest in line and platform["asset"] in line:
                    problems.append(f"{relative}: quotes {platform['asset']} with stale digest {digest}")


def tracked_files(root: Path) -> list[str]:
    result = subprocess.run(["git", "-C", str(root), "ls-files", "-z"],
                            capture_output=True, check=False)
    if result.returncode != 0:
        return sorted(path.relative_to(root).as_posix()
                      for path in root.rglob("*") if path.is_file() and ".git" not in path.parts)
    return sorted(name for name in result.stdout.decode("utf-8").split("\0") if name)


def check_no_other_copies(root: Path, lock: dict, problems: list[str]) -> None:
    allowed = {LOCK, *READERS, *RECORDS, *DOCUMENTS}
    digests = locked_digests(lock)
    for relative in tracked_files(root):
        if relative in allowed or any(fnmatch.fnmatch(relative, pattern) for pattern in EVIDENCE):
            continue
        if relative.startswith("mirror/"):
            # The mirror is Pulp's source, verified byte for byte by its own
            # export receipts; it pins no renderer digest of its own.
            continue
        try:
            text = (root / relative).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for digest in sorted(digests_in(text) & digests):
            problems.append(f"{relative}: holds locked digest {digest}; read it from {LOCK}")


def check(root: Path) -> list[str]:
    problems: list[str] = []
    lock = load_lock(root, problems)
    if lock is None:
        return problems
    check_readers(root, lock, problems)
    check_cmake_names(root, lock, problems)
    check_third_party_lock(root, lock, problems)
    check_attestation(root, lock, problems)
    check_documents(root, lock, problems)
    check_no_other_copies(root, lock, problems)
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    problems = check(args.root.resolve())
    for problem in problems:
        print(f"renderer lock drift: {problem}", file=sys.stderr)
    if not problems:
        print(f"renderer lock: every copy agrees with {LOCK}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
