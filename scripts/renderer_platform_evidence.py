#!/usr/bin/env python3
"""Evidence for the non-macOS rows of gpu-runtime/renderer-lock.json.

The macOS row is checked against provenance/third-party-lock.json and
provenance/renderer-attestation.json. Every other row is a build input too, so
it is checked against a pinned record of what skia-builder published for the
release the row names, field by field:

  offline   check_rows() compares each non-macOS row with
            provenance/renderer-platform-evidence.json. It needs no network, so
            the gate and ctest can run it. A row whose digest, archive digest,
            archive_root, asset, configuration, release or builder commit
            differs from the evidence is refused, and so is a row without
            evidence or evidence without a row.
  derive    `derive` rebuilds that evidence from the release itself: the
            release's build-provenance statement names every asset and legal
            manifest by digest and the builder commit; each legal manifest,
            checked against that digest, lists the static libraries and their
            digests.
  verify    `verify` derives the evidence again and refuses any difference from
            the committed file. It needs network access and is not part of the
            offline gates; run it when evidence or a row changes.

The statement's Sigstore signature is verified for the macOS asset by
tools/provenance/verify_renderer_dependency.py; this module reads the statement
as published and does not verify the signature.

Usage:
  renderer_platform_evidence.py derive --release chrome/m158-attested.2 --out <file>
  renderer_platform_evidence.py verify --release chrome/m158-attested.2 [--evidence <file>]
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Callable, Protocol

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = "provenance/renderer-platform-evidence.json"
SCHEMA = "gpu-runtime.renderer-platform-evidence.v1"
PLATFORM_FILE = "cmake/VellumSkiaDawnPlatform.cmake"
MACOS = "macos-arm64"
HEX_64 = re.compile(r"[0-9a-f]{64}")
HEX_40 = re.compile(r"[0-9a-f]{40}")
ATTESTED = re.compile(r"chrome/m[0-9]+-attested\.[0-9]+")
BUILD_WORKFLOW = ".github/workflows/build-skia.yml"
# The skia-builder asset each lock key is published as. A row naming another
# platform's asset (swapped rows) differs from this table.
PLATFORM_ASSETS = {
    "linux-x64": "skia-build-linux-x64-gpu-release.zip",
    "linux-arm64": "skia-build-linux-arm64-gpu-release.zip",
    "windows-x64": "skia-build-win-x64-gpu-release.zip",
    "windows-arm64": "skia-build-win-arm64-gpu-release.zip",
    "android-arm64-v8a": "skia-build-android-arm64-v8a-gpu-release.zip",
    "android-x86_64": "skia-build-android-x86_64-gpu-release.zip",
}


def platform_keys(root: Path) -> set[str]:
    """The keys cmake/VellumSkiaDawnPlatform.cmake can select."""
    text = (root / PLATFORM_FILE).read_text(encoding="utf-8")
    return set(re.findall(r'set\(_key "([a-z0-9_-]+)"\)', text))


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- offline check

def check_evidence_shape(evidence: object, problems: list[str]) -> dict | None:
    label = EVIDENCE
    if not isinstance(evidence, dict) or evidence.get("schema") != SCHEMA:
        problems.append(f"{label}: schema is not {SCHEMA}")
        return None
    release = evidence.get("release")
    if not isinstance(release, str) or not ATTESTED.fullmatch(release):
        problems.append(f"{label}: release is not chrome/mNNN-attested.N")
    builder = evidence.get("builder")
    if not isinstance(builder, dict) or not isinstance(builder.get("commit"), str) \
            or not HEX_40.fullmatch(builder["commit"]):
        problems.append(f"{label}: builder.commit is not a 40-character commit")
    attestation = evidence.get("attestation")
    if not isinstance(attestation, dict) or not HEX_64.fullmatch(str(attestation.get("sha256"))):
        problems.append(f"{label}: attestation digest is missing or malformed")
    if not isinstance(evidence.get("platforms"), dict):
        problems.append(f"{label}: platforms is not an object")
        return None
    return evidence


def check_evidence_record(key: str, record: object, problems: list[str]) -> None:
    label = f"{EVIDENCE}: {key}"
    if not isinstance(record, dict):
        problems.append(f"{label} is not an object")
        return
    if record.get("asset") != PLATFORM_ASSETS.get(key):
        problems.append(f"{label} asset is {record.get('asset')!r}, "
                        f"expected {PLATFORM_ASSETS.get(key)!r}")
    if not HEX_64.fullmatch(str(record.get("sha256"))):
        problems.append(f"{label} asset digest is missing or malformed")
    manifest = record.get("legal_manifest")
    stem = str(record.get("asset", "")).removesuffix(".zip")
    if not isinstance(manifest, dict) or not HEX_64.fullmatch(str(manifest.get("sha256"))) \
            or manifest.get("name") != f"{stem}.legal-manifest.sha256-{manifest.get('sha256')}.json":
        problems.append(f"{label} legal manifest name does not carry its own digest")
    if record.get("configuration") != "Release":
        problems.append(f"{label} configuration is {record.get('configuration')!r}, not Release")
    root = record.get("archive_root")
    if not isinstance(root, str) or not re.fullmatch(r"build/[A-Za-z0-9_./-]+", root) or ".." in root:
        problems.append(f"{label} archive_root is not a build/ relative path")
    archives = record.get("archives")
    if not isinstance(archives, dict) or not archives or not all(
            HEX_64.fullmatch(str(value)) for value in archives.values()):
        problems.append(f"{label} archives are missing or malformed")


def check_rows(lock: dict, evidence_text: str | None, keys: set[str], lock_path: str,
               problems: list[str]) -> None:
    """Compare every non-macOS row of `lock` with the pinned evidence."""
    platforms = lock.get("platforms") or {}
    rows = {key: row for key, row in platforms.items() if key != MACOS}
    for key in sorted(rows):
        if key not in keys:
            problems.append(f"{lock_path}: platform {key} is not one cmake/VellumSkiaDawnPlatform.cmake selects")
        elif key not in PLATFORM_ASSETS:
            problems.append(f"{lock_path}: platform {key} has no known skia-builder asset")
    roots: dict[str, str] = {}
    for key, row in sorted(rows.items()):
        if not isinstance(row, dict):
            continue
        if row.get("configuration") != "Release":
            problems.append(f"{lock_path}: {key} configuration is {row.get('configuration')!r}, "
                            "not Release")
        root = row.get("archive_root")
        if isinstance(root, str):
            if root in roots:
                problems.append(f"{lock_path}: {key} archive_root {root} repeats {roots[root]}")
            else:
                roots[root] = key
        expected_asset = PLATFORM_ASSETS.get(key)
        if expected_asset and row.get("asset") != expected_asset:
            problems.append(f"{lock_path}: {key} asset is {row.get('asset')!r}, "
                            f"skia-builder publishes {expected_asset!r} for it")
    if not rows and evidence_text is None:
        return
    if evidence_text is None:
        problems.append(f"{EVIDENCE}: missing, so the non-macOS rows ({', '.join(sorted(rows))}) "
                        "have nothing to be checked against")
        return
    try:
        evidence = check_evidence_shape(json.loads(evidence_text), problems)
    except ValueError as error:
        problems.append(f"{EVIDENCE}: cannot be read: {error}")
        return
    if evidence is None:
        return
    records = evidence["platforms"]
    for key, record in sorted(records.items()):
        if key not in keys or key == MACOS:
            problems.append(f"{EVIDENCE}: {key} is not a non-macOS platform cmake selects")
        else:
            check_evidence_record(key, record, problems)
    for key in sorted(set(records) - set(rows)):
        problems.append(f"{EVIDENCE}: {key} has evidence but the lock has no {key} row")
    for key in sorted(set(rows) - set(records)):
        problems.append(f"{lock_path}: {key} has no evidence in {EVIDENCE}")
    for key in sorted(set(rows) & set(records)):
        compare_row(key, rows[key], records[key], evidence, lock_path, problems)


def compare_row(key: str, row: object, record: object, evidence: dict, lock_path: str,
                problems: list[str]) -> None:
    if not isinstance(row, dict) or not isinstance(record, dict):
        return
    pairs = (("asset", row.get("asset"), record.get("asset")),
             ("asset digest", row.get("sha256"), record.get("sha256")),
             ("configuration", row.get("configuration"), record.get("configuration")),
             ("archive_root", row.get("archive_root"), record.get("archive_root")),
             ("release", row.get("release"), evidence.get("release")),
             ("builder_commit", row.get("builder_commit"),
              (evidence.get("builder") or {}).get("commit")))
    for field, actual, expected in pairs:
        if actual != expected:
            problems.append(f"{lock_path}: {key} {field} is {actual!r}, the release evidence "
                            f"says {expected!r}")
    archives = row.get("archives") if isinstance(row.get("archives"), dict) else {}
    wanted = record.get("archives") if isinstance(record.get("archives"), dict) else {}
    # A row locks the libraries the build links, a subset of what the release
    # holds; scripts/check_renderer_lock.py requires the linked set.
    for name in sorted(archives):
        if name not in wanted:
            problems.append(f"{lock_path}: {key} locks {name}, which the release evidence "
                            "does not list")
        elif archives[name] != wanted[name]:
            problems.append(f"{lock_path}: {key} {name} digest is {archives[name]}, the release "
                            f"evidence says {wanted[name]}")


# ------------------------------------------------------------------- derivation

class Fetcher(Protocol):
    def assets(self, repository: str, release: str) -> dict[str, str]:
        """Asset name -> sha256 as the release publishes it."""

    def get(self, repository: str, release: str, name: str) -> bytes: ...


class HttpFetcher:
    def assets(self, repository: str, release: str) -> dict[str, str]:
        slug = repository.removeprefix("https://github.com/")
        url = f"https://api.github.com/repos/{slug}/releases/tags/{release}"
        with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - fixed https host
            payload = json.load(response)
        return {asset["name"]: str(asset.get("digest", "")).removeprefix("sha256:")
                for asset in payload["assets"]}

    def get(self, repository: str, release: str, name: str) -> bytes:
        url = f"{repository}/releases/download/{release}/{name}"
        with urllib.request.urlopen(url, timeout=600) as response:  # noqa: S310
            return response.read()


class DeriveError(Exception):
    pass


def statement_of(bundle: bytes) -> dict:
    envelope = json.loads(bundle)["dsseEnvelope"]
    return json.loads(base64.b64decode(envelope["payload"]))


def derive(repository: str, release: str, fetcher: Fetcher,
           keys: dict[str, str] | None = None) -> dict:
    keys = keys or PLATFORM_ASSETS
    published = fetcher.assets(repository, release)
    platforms: dict[str, dict] = {}
    builder_commits: set[str] = set()
    attestations: set[str] = set()
    for key, asset in sorted(keys.items()):
        stem = asset.removesuffix(".zip")
        zip_digest = published.get(asset, "")
        if not HEX_64.fullmatch(zip_digest):
            raise DeriveError(f"{release} publishes no digest for {asset}")
        attestation_names = [n for n in published
                             if n.startswith(f"{stem}.provenance-attestation.sha256-")]
        manifest_names = [n for n in published if n.startswith(f"{stem}.legal-manifest.sha256-")]
        if len(attestation_names) != 1 or len(manifest_names) != 1:
            raise DeriveError(f"{asset}: expected one attestation and one legal manifest, found "
                              f"{len(attestation_names)} and {len(manifest_names)}")
        attestation_name, manifest_name = attestation_names[0], manifest_names[0]
        attestation = fetcher.get(repository, release, attestation_name)
        attestation_digest = sha256_hex(attestation)
        if f".sha256-{attestation_digest}." not in attestation_name or \
                published.get(attestation_name) != attestation_digest:
            raise DeriveError(f"{attestation_name}: bytes do not match the published digest")
        statement = statement_of(attestation)
        subjects = {s["name"]: s["digest"]["sha256"] for s in statement["subject"]}
        build = statement["predicate"]["buildDefinition"]
        workflow = build["externalParameters"]["workflow"]
        if workflow.get("repository") != repository or workflow.get("ref") != "refs/heads/main" \
                or workflow.get("path") != BUILD_WORKFLOW:
            raise DeriveError(f"{attestation_name}: not built by {BUILD_WORKFLOW} on main of "
                              f"{repository}: {workflow}")
        commits = {d["digest"]["gitCommit"] for d in build["resolvedDependencies"]
                   if d["uri"].startswith(f"git+{repository}@")}
        if len(commits) != 1 or not HEX_40.fullmatch(next(iter(commits))):
            raise DeriveError(f"{attestation_name}: no single builder commit: {commits}")
        builder_commits |= commits
        attestations.add(attestation_digest)
        manifest_digest = manifest_name.rsplit(".sha256-", 1)[1].removesuffix(".json")
        if subjects.get(asset) != zip_digest or subjects.get(manifest_name) != manifest_digest \
                or published.get(manifest_name) != manifest_digest:
            raise DeriveError(f"{asset}: the build statement, the release and the legal "
                              "manifest name disagree about the digests")
        manifest_bytes = fetcher.get(repository, release, manifest_name)
        if sha256_hex(manifest_bytes) != manifest_digest:
            raise DeriveError(f"{manifest_name}: bytes do not match its digest")
        manifest = json.loads(manifest_bytes)
        if manifest.get("archive", {}).get("name") != asset or \
                manifest["archive"].get("sha256") != zip_digest:
            raise DeriveError(f"{manifest_name}: does not describe {asset}")
        configuration = manifest["cell"]["configuration"]
        members = [m for m in manifest["zip_members"] if m.get("kind") == "static-archive"]
        roots = {m["name"].rsplit("/", 1)[0] for m in members}
        if not members or len(roots) != 1:
            raise DeriveError(f"{manifest_name}: static libraries are not in one directory: {roots}")
        platforms[key] = {
            "asset": asset, "sha256": zip_digest,
            "legal_manifest": {"name": manifest_name, "sha256": manifest_digest},
            "configuration": configuration, "archive_root": next(iter(roots)),
            "archives": {m["name"].rsplit("/", 1)[1]: m["sha256"] for m in
                         sorted(members, key=lambda m: m["name"])},
        }
    if len(builder_commits) != 1 or len(attestations) != 1:
        raise DeriveError(f"the release's statements differ: {builder_commits} {attestations}")
    (attestation_digest,) = attestations
    return {"schema": SCHEMA, "release": release,
            "builder": {"repository": repository, "commit": next(iter(builder_commits))},
            "attestation": {"sha256": attestation_digest},
            "platforms": platforms}


def render(evidence: dict) -> str:
    return json.dumps(evidence, indent=2, sort_keys=False) + "\n"


def main(argv: list[str] | None = None, fetcher: Fetcher | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("derive", "verify"):
        p = sub.add_parser(name)
        p.add_argument("--release", required=True)
        p.add_argument("--repository", default="https://github.com/danielraffel/skia-builder")
        p.add_argument("--root", type=Path, default=ROOT)
    sub.choices["derive"].add_argument("--out", type=Path, required=True)
    sub.choices["verify"].add_argument("--evidence", type=Path)
    args = parser.parse_args(argv)
    fetcher = fetcher or HttpFetcher()
    try:
        evidence = derive(args.repository, args.release, fetcher)
    except (DeriveError, OSError, ValueError, KeyError) as error:
        print(f"renderer platform evidence: {error}", file=sys.stderr)
        return 2
    if args.command == "derive":
        args.out.write_text(render(evidence), encoding="utf-8")
        print(f"wrote {args.out}")
        return 0
    path = args.evidence or args.root / EVIDENCE
    if path.read_text(encoding="utf-8") != render(evidence):
        print(f"renderer platform evidence: {path} differs from what {args.release} publishes",
              file=sys.stderr)
        return 1
    print(f"renderer platform evidence: {path} matches {args.release}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
