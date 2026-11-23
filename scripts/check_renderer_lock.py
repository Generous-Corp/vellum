#!/usr/bin/env python3
"""Fail when any copy of the Skia/Dawn pin disagrees with gpu-runtime's lock.

gpu-runtime/renderer-lock.json is the one place the chrome/mNNN milestone and
the skia-builder digests are written. Everything else either reads it or is a
record that must agree with it:

  read      cmake/VellumSkiaDawn.cmake and .github/workflows/gpu-macos.yml load
            the lock; neither may carry a digest or milestone of its own.
  agree     the macOS row's own `release` and `builder_commit`, when it has them
            (otherwise the lock's milestone and builder.commit), are the release
            and builder every record below must name.
            provenance/third-party-lock.json (the release-eligibility lock),
            provenance/renderer-attestation.json (the builder's records) and
            the documents that quote the pin must name exactly the lock's
            milestone, asset, digests and source commits.
  evidence  every row other than macos-arm64 must agree, field by field, with
            provenance/renderer-platform-evidence.json, the pinned record of what
            skia-builder published for the row's release (see
            scripts/renderer_platform_evidence.py).
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
sys.path.append(str(Path(__file__).resolve().parent))
import renderer_platform_evidence as evidence  # noqa: E402

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
# A platform row's release: the lock's milestone, or an attested release of it.
RELEASE = re.compile(r"chrome/m([0-9]+)(-attested\.[0-9]+)?")
# The libraries cmake/VellumSkiaDawn.cmake links, by stem.
REQUIRED_LIBRARIES = ("skia", "dawn_combined", "svg", "skresources", "skshaper",
                      "skparagraph", "skunicode_core", "skunicode_icu")

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
EVIDENCE = ("provenance/mirror/closures/*", "tools/provenance/testdata/*",
            "scripts/testdata/renderer_platform/*")


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
    platforms = lock.get("platforms") or {}
    if not isinstance(platforms.get(PLATFORM), dict):
        problems.append(f"{LOCK}: platform {PLATFORM} is missing")
        return None
    seen: dict[str, str] = {}
    seen_assets: dict[str, str] = {}
    for key, platform in platforms.items():
        if not isinstance(platform, dict):
            problems.append(f"{LOCK}: platform {key} is not an object")
            continue
        check_platform_row(key, platform, lock.get("milestone"), seen, seen_assets, problems)
    return None if problems else lock


def effective_release(lock: dict, key: str = PLATFORM) -> str:
    """The release a row's asset is downloaded from: its own, else the milestone."""
    release = (lock["platforms"][key] or {}).get("release")
    return release if isinstance(release, str) else lock["milestone"]


def effective_builder_commit(lock: dict) -> str:
    """The builder commit the macOS asset was built at: the row's own, else the lock's."""
    commit = (lock["platforms"][PLATFORM] or {}).get("builder_commit")
    return commit if isinstance(commit, str) else lock["builder"]["commit"]


def check_release(key: str, platform: dict, milestone: object, problems: list[str]) -> None:
    """The release rules gpu-runtime/cmake/GpuRuntimeRendererLock.cmake enforces too.

    A row's release is of the lock's milestone. macos-arm64 may omit it (the
    milestone's own release) or name any release of the milestone; every other
    platform must name an attested release, never a mutable chrome/mNNN tag.
    """
    release = platform.get("release")
    # Absent is fine for macos-arm64; an explicit null (or any non-string) is
    # malformed, as in the CMake reader.
    if key == PLATFORM and "release" not in platform:
        return
    found = RELEASE.fullmatch(release) if isinstance(release, str) else None
    if not found or (key != PLATFORM and not found.group(2)):
        expected = "chrome/mNNN[-attested.N]" if key == PLATFORM else "chrome/mNNN-attested.N"
        problems.append(f"{LOCK}: {key} release is not {expected}")
    elif isinstance(milestone, str) and f"chrome/m{found.group(1)}" != milestone:
        problems.append(f"{LOCK}: {key} release {release} is not of milestone {milestone}")


def check_platform_row(key: str, platform: dict, milestone: object, seen: dict[str, str],
                       seen_assets: dict[str, str], problems: list[str]) -> None:
    archives = platform.get("archives")
    if not isinstance(archives, dict) or not archives:
        problems.append(f"{LOCK}: {key} lists no archives")
        archives = {}
    labelled = [(f"{key} asset", platform.get("sha256")),
                *((f"{key} {name}", value) for name, value in archives.items())]
    for label, value in labelled:
        if not isinstance(value, str) or not HEX_64.fullmatch(value):
            problems.append(f"{LOCK}: {label} digest is missing or malformed")
        elif not value.strip("0"):
            problems.append(f"{LOCK}: {label} digest is an all-zero placeholder")
        elif value in seen:
            problems.append(f"{LOCK}: {label} digest repeats {seen[value]}")
        else:
            seen[value] = label
    check_release(key, platform, milestone, problems)
    if platform.get("configuration") != "Release":
        problems.append(f"{LOCK}: {key} configuration is {platform.get('configuration')!r}, "
                        "expected 'Release'")
    if key != PLATFORM or "builder_commit" in platform:
        if not isinstance(platform.get("builder_commit"), str) or \
                not HEX_40.fullmatch(platform["builder_commit"]):
            problems.append(f"{LOCK}: {key} builder_commit is not a 40-character commit")
    asset = platform.get("asset")
    if not isinstance(asset, str) or not re.fullmatch(r"skia-build-[a-z0-9_.-]+-gpu-release\.zip", asset):
        problems.append(f"{LOCK}: {key} asset name is not a skia-builder GPU release zip")
    elif asset in seen_assets:
        problems.append(f"{LOCK}: {key} asset {asset} repeats {seen_assets[asset]}")
    else:
        seen_assets[asset] = key
    root = platform.get("archive_root")
    if not isinstance(root, str) or not re.fullmatch(r"build/[A-Za-z0-9_./-]+", root) or ".." in root:
        problems.append(f"{LOCK}: {key} archive_root is not a build/ relative path")
    suffix = ".lib" if key.startswith("windows-") else ".a"
    for library in REQUIRED_LIBRARIES:
        name = f"{library}.lib" if suffix == ".lib" else f"lib{library}.a"
        if archives and name not in archives:
            problems.append(f"{LOCK}: {key} does not lock {name}")


def asset_url(lock: dict) -> str:
    platform = lock["platforms"][PLATFORM]
    return "/".join((lock["builder"]["repository"], "releases/download",
                     effective_release(lock), platform["asset"]))


def locked_digests(lock: dict) -> set[str]:
    return {digest for platform in lock["platforms"].values()
            for digest in (platform["sha256"], *platform["archives"].values())}


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
    archives = {name for row in lock["platforms"].values() for name in row["archives"]}
    # Libraries are named directly (_vellum_renderer_SHA256_libfoo.a) or through a
    # per-library name variable (set(_vellum_foo_name "libfoo.a")); read both.
    direct = re.findall(r"_vellum_renderer_SHA256_(lib[A-Za-z0-9_]+\.a)", text)
    named = re.findall(r'set\(_vellum_[a-z0-9_]+_name\s+"(lib[A-Za-z0-9_]+\.a)"\)', text)
    # Windows names are the stem plus the archive suffix variable
    # (set(_vellum_foo_name "foo${_vellum_archive_suffix}")) or a literal foo.lib.
    stems = re.findall(r'set\(_vellum_[a-z0-9_]+_name\s+"([A-Za-z0-9_]+)\$\{_vellum_archive_suffix\}"\)', text)
    windows = [f"{stem}.lib" for stem in stems] + re.findall(r'"([A-Za-z0-9_]+\.lib)"', text)
    # Until the lock holds a Windows row there is nothing to compare them with.
    if not any(key.startswith("windows-") for key in lock["platforms"]):
        windows = []
    for name in sorted(set(direct) | set(named) | set(windows)):
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
        "release_tag": effective_release(lock),
        "name": platform["asset"],
        "url": asset_url(lock),
        "sha256": platform["sha256"],
        "builder_commit": effective_builder_commit(lock),
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
                         ("builder", effective_builder_commit(lock))):
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
                         ("release_tag", effective_release(lock)),
                         ("name", platform["asset"]),
                         ("sha256", platform["sha256"])):
        if artifact.get(field) != value:
            problems.append(f"{relative}: artifact {field} is {artifact.get(field)!r}, "
                            f"the lock says {value!r}")
    identities = record.get("source_identities") or {}
    for name, commit in (("skia", lock["sources"]["skia"]), ("dawn", lock["sources"]["dawn"]),
                         ("builder", effective_builder_commit(lock))):
        if (identities.get(name) or {}).get("commit") != commit:
            problems.append(f"{relative}: {name} source commit differs from the lock")
    if record.get("build_identity", {}).get("target") != PLATFORM:
        problems.append(f"{relative}: build target is not {PLATFORM}")
    release = "/".join((lock["builder"]["repository"], "releases/download",
                        effective_release(lock), ""))
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
    allowed = {LOCK, evidence.EVIDENCE, *READERS, *RECORDS, *DOCUMENTS}
    digests = locked_digests(lock)
    for relative in tracked_files(root):
        if relative in allowed or any(fnmatch.fnmatch(relative, pattern) for pattern in EVIDENCE):
            continue
        if relative.startswith(("mirror/", "framework/")):
            # The mirror, and framework/ that it became, are Pulp's source,
            # verified byte for byte by the export receipts and the transfer
            # record; they pin no renderer digest of their own.
            continue
        try:
            text = (root / relative).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for digest in sorted(digests_in(text) & digests):
            problems.append(f"{relative}: holds locked digest {digest}; read it from {LOCK}")


def check_platform_evidence(root: Path, lock: dict, problems: list[str]) -> None:
    """Every non-macOS row must agree with the pinned release evidence."""
    path = root / evidence.EVIDENCE
    text = path.read_text(encoding="utf-8") if path.is_file() else None
    evidence.check_rows(lock, text, evidence.platform_keys(root), LOCK, problems)


def check(root: Path) -> list[str]:
    problems: list[str] = []
    lock = load_lock(root, problems)
    if lock is None:
        return problems
    check_platform_evidence(root, lock, problems)
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
