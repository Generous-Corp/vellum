#!/usr/bin/env python3
"""Fetch and verify the Pulp pulp-view-audio archive the landed mirror needs.

A package with a view unit proves its view-audio debts inert against the
symbols Pulp's pulp-view-audio archive defines (verify_mirror_inertness.py
--view-audio-archive). That archive must come from the same Pulp commit the
mirror was exported from, built by Pulp's own hosted workflow, never from a
local build. provenance/mirror/pulp-view-audio-archive.json pins it:

  * source_sha must equal the newest export receipt's pulp_sha, and the
    descriptor must name that receipt;
  * the six release assets of Pulp's immutable artifact/pulp-view-audio/<sha>
    release (archive, manifest, symbol floor, floor-gate output, SHA256SUMS,
    Sigstore bundle) are each pinned by sha256, and the output directory holds
    exactly those six; SHA256SUMS lists the first four and holds;
  * the floor and the gate output match the manifest, the floor is not empty,
    and the manifest records an identical separate-build-dir rebuild;
  * the manifest must name the same commit, archive digest, size and run,
    and record a passing symbol-floor gate;
  * `gh attestation verify`, run without a token from the bundle, must accept
    all five attested files (everything but the bundle) as
    built by Pulp's view-audio-archive workflow on main, run at source_sha
    itself (the workflow builds only its own commit), on a GitHub-hosted
    runner.

    fetch_view_audio_archive.py --root . --repository Generous-Corp/pulp \
        --output-dir DIR [--json]
    fetch_view_audio_archive.py --root . --repository Generous-Corp/pulp --check

`--check` validates the descriptor against the receipt without the network.
Any unpinned, missing or mismatched input fails closed.

Exit codes: 0 = verified, 1 = mismatch, 2 = descriptor unusable or unpinned.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Callable

DESCRIPTOR_PATH = "provenance/mirror/pulp-view-audio-archive.json"
INDEX_PATH = "provenance/mirror/receipts/index.json"
DESCRIPTOR_SCHEMA = "vellum.pulp-view-audio-archive.v1"
MANIFEST_SCHEMA = "pulp.view-audio-archive.v1"
INDEX_SCHEMA = "vellum.mirror-receipt-index.v1"
RECEIPT_SCHEMA = "pulp.vellum.export-receipt.v2"
SIGNER_WORKFLOW_PATH = ".github/workflows/view-audio-archive.yml"
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
# Descriptor key -> the six release assets, in Pulp's verify-release contract.
ASSETS = ("archive", "manifest", "symbol_floor", "symbol_floor_gate", "checksums",
          "attestation_bundle")
# The five files the Sigstore bundle attests; SHA256SUMS lists the first four.
ATTESTED = ("archive", "manifest", "symbol_floor", "symbol_floor_gate", "checksums")
CHECKSUMMED = ("archive", "manifest", "symbol_floor", "symbol_floor_gate")


class Unusable(RuntimeError):
    pass


class Mismatch(RuntimeError):
    pass


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise Unusable(f"cannot read {path}: {error}") from error


def newest_receipt(root: Path) -> tuple[str, dict[str, Any]]:
    index = load_json(root / INDEX_PATH)
    receipts = index.get("receipts") if isinstance(index, dict) else None
    if not isinstance(index, dict) or index.get("schema") != INDEX_SCHEMA or not receipts:
        raise Unusable(f"{INDEX_PATH} names no export receipt")
    relative = receipts[-1]
    receipt = load_json(root / relative)
    if not isinstance(receipt, dict) or receipt.get("schema") != RECEIPT_SCHEMA:
        raise Unusable(f"{relative} is not a {RECEIPT_SCHEMA} receipt")
    return relative, receipt


def check_descriptor(root: Path, repository: str) -> dict[str, Any]:
    """The descriptor, once it is fully pinned and agrees with the receipt."""
    descriptor = load_json(root / DESCRIPTOR_PATH)
    if not isinstance(descriptor, dict) or descriptor.get("schema") != DESCRIPTOR_SCHEMA:
        raise Unusable(f"{DESCRIPTOR_PATH} is not a {DESCRIPTOR_SCHEMA} descriptor")
    relative, receipt = newest_receipt(root)
    problems = []
    if descriptor.get("pulp_repository") != repository:
        problems.append(f"descriptor repository {descriptor.get('pulp_repository')!r} != {repository!r}")
    if receipt.get("pulp_repository") != repository:
        problems.append(f"receipt repository {receipt.get('pulp_repository')!r} != {repository!r}")
    if descriptor.get("export_receipt") != relative:
        problems.append(f"descriptor names receipt {descriptor.get('export_receipt')!r}, newest is {relative!r}")
    source_sha = descriptor.get("source_sha")
    if not isinstance(source_sha, str) or not SHA40.match(source_sha):
        raise Unusable(f"descriptor source_sha {source_sha!r} is not a full commit")
    if source_sha != receipt.get("pulp_sha"):
        problems.append(f"descriptor source_sha {source_sha} != receipt pulp_sha {receipt.get('pulp_sha')}")
    if descriptor.get("release_tag") != f"artifact/pulp-view-audio/{source_sha}":
        problems.append(f"release_tag {descriptor.get('release_tag')!r} is not this commit's archive release")
    if problems:
        raise Mismatch("; ".join(problems))
    unpinned = []
    for key in ("workflow_run_id", "workflow_sha"):
        if not descriptor.get(key):
            unpinned.append(key)
    if descriptor.get("workflow_sha") and not SHA40.match(str(descriptor["workflow_sha"])):
        raise Unusable(f"workflow_sha {descriptor['workflow_sha']!r} is not a full commit")
    # Pulp's workflow builds the commit it runs on, so the run that signed the
    # archive ran at the archive's source commit.
    if descriptor.get("workflow_sha") and descriptor["workflow_sha"] != source_sha:
        raise Mismatch(f"workflow_sha {descriptor['workflow_sha']} != source_sha {source_sha}")
    for asset in ASSETS:
        entry = descriptor.get(asset)
        if not isinstance(entry, dict) or not entry.get("name") or "/" in str(entry.get("name")):
            raise Unusable(f"descriptor {asset} has no plain asset name")
        if not SHA256.match(str(entry.get("sha256") or "")):
            unpinned.append(f"{asset}.sha256")
    if not isinstance(descriptor["archive"].get("size"), int):
        unpinned.append("archive.size")
    if unpinned:
        raise Unusable(
            f"{DESCRIPTOR_PATH} is not pinned yet ({', '.join(unpinned)}); a package with a "
            "view unit needs Pulp's published archive for this commit")
    return descriptor


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "vellum-mirror"})
    with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as out:
        shutil.copyfileobj(response, out)


def check_checksums(paths: dict[str, Path]) -> None:
    """SHA256SUMS must list exactly the four checksummed assets, and hold."""
    sums: dict[str, str] = {}
    for line in paths["checksums"].read_text(encoding="utf-8").splitlines():
        digest, sep, name = line.partition("  ")
        if not sep or name in sums:
            raise Mismatch(f"SHA256SUMS line {line!r} is not '<sha256>  <name>' once per file")
        sums[name] = digest
    expected = {paths[key].name for key in CHECKSUMMED}
    if set(sums) != expected:
        raise Mismatch(f"SHA256SUMS lists {sorted(sums)}, not {sorted(expected)}")
    for key in CHECKSUMMED:
        if sums[paths[key].name] != sha256_file(paths[key]):
            raise Mismatch(f"SHA256SUMS does not hold for {paths[key].name}")


def check_floor(manifest: dict[str, Any], paths: dict[str, Path]) -> None:
    """The floor and the gate output are the ones the manifest records."""
    floor = manifest.get("symbol_floor") or {}
    if floor.get("sha256") != sha256_file(paths["symbol_floor"]):
        raise Mismatch("symbol floor sha256 differs from the manifest")
    lines = len(paths["symbol_floor"].read_text(encoding="utf-8").splitlines())
    if lines == 0 or floor.get("symbols") != lines:
        raise Mismatch(f"symbol floor has {lines} symbols, manifest records {floor.get('symbols')!r}")
    gate = manifest.get("symbol_floor_gate") or {}
    if gate.get("sha256") != sha256_file(paths["symbol_floor_gate"]):
        raise Mismatch("symbol floor gate output sha256 differs from the manifest")
    rebuild = manifest.get("rebuild") or {}
    if rebuild.get("kind") != "separate-build-dir" or rebuild.get("identical") is not True:
        raise Mismatch(f"manifest records no identical separate-build-dir rebuild: {rebuild or None}")


def check_manifest(manifest: Any, descriptor: dict[str, Any], repository: str) -> None:
    if not isinstance(manifest, dict) or manifest.get("schema") != MANIFEST_SCHEMA:
        raise Mismatch(f"manifest is not a {MANIFEST_SCHEMA} manifest")
    expected = {
        "source.repository": repository,
        "source.sha": descriptor["source_sha"],
        "target": "pulp-view-audio",
        "archive.name": descriptor["archive"]["name"],
        "archive.sha256": descriptor["archive"]["sha256"],
        "archive.size": descriptor["archive"]["size"],
        "workflow.run_id": str(descriptor["workflow_run_id"]),
        "workflow.workflow_sha": descriptor["workflow_sha"],
        "symbol_floor_gate.exit_code": 0,
    }
    problems = []
    for dotted, want in expected.items():
        value: Any = manifest
        for key in dotted.split("."):
            value = value.get(key) if isinstance(value, dict) else None
        if value != want:
            problems.append(f"manifest {dotted} {value!r} != {want!r}")
    if problems:
        raise Mismatch("; ".join(problems))


def attest(gh: str, path: Path, bundle: Path, descriptor: dict[str, Any], repository: str) -> None:
    # Token-free on purpose, as a consumer anywhere would run it: the bundle
    # alone must verify.
    env = {key: value for key, value in os.environ.items() if key not in ("GH_TOKEN", "GITHUB_TOKEN")}
    with tempfile.TemporaryDirectory() as config:
        env["GH_CONFIG_DIR"] = config
        proc = run_attest(gh, path, bundle, descriptor, repository, env)
    if proc.returncode != 0:
        raise Mismatch(f"attestation rejected {path.name}: {(proc.stderr or proc.stdout).strip()}")


def run_attest(gh: str, path: Path, bundle: Path, descriptor: dict[str, Any], repository: str,
               env: dict[str, str]) -> subprocess.CompletedProcess:
    command = [
        gh, "attestation", "verify", str(path),
        "--repo", repository,
        "--bundle", str(bundle),
        "--signer-workflow", f"{repository}/{SIGNER_WORKFLOW_PATH}",
        "--source-ref", "refs/heads/main",
        "--source-digest", descriptor["workflow_sha"],
        "--deny-self-hosted-runners",
    ]
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=False, env=env)


def fetch(root: Path, repository: str, output_dir: Path, gh: str,
          fetcher: Callable[[str, Path], None] = download) -> dict[str, Any]:
    descriptor = check_descriptor(root, repository)
    output_dir.mkdir(parents=True, exist_ok=True)
    if any(output_dir.iterdir()):
        raise Unusable(f"{output_dir} is not empty; the fetch must hold exactly the six assets")
    base = f"https://github.com/{repository}/releases/download/{descriptor['release_tag']}"
    paths = {}
    for asset in ASSETS:
        entry = descriptor[asset]
        destination = output_dir / entry["name"]
        try:
            fetcher(f"{base}/{entry['name']}", destination)
        except OSError as error:
            raise Mismatch(f"cannot download {entry['name']}: {error}") from error
        actual = sha256_file(destination)
        if actual != entry["sha256"]:
            destination.unlink()
            raise Mismatch(f"{entry['name']} sha256 {actual} != pinned {entry['sha256']}")
        paths[asset] = destination
    present = sorted(path.name for path in output_dir.iterdir())
    if present != sorted(descriptor[asset]["name"] for asset in ASSETS):
        raise Mismatch(f"{output_dir} holds {present}, not exactly the six assets")
    if paths["archive"].stat().st_size != descriptor["archive"]["size"]:
        raise Mismatch("archive size differs from the pinned size")
    check_checksums(paths)
    manifest = load_json(paths["manifest"])
    check_manifest(manifest, descriptor, repository)
    check_floor(manifest, paths)
    for asset in ATTESTED:
        attest(gh, paths[asset], paths["attestation_bundle"], descriptor, repository)
    return {
        "schema": "vellum.pulp-view-audio-archive-fetch.v1",
        "source_sha": descriptor["source_sha"],
        "archive": str(paths["archive"]),
        "archive_sha256": descriptor["archive"]["sha256"],
        "manifest": str(paths["manifest"]),
        "workflow_run_id": descriptor["workflow_run_id"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--repository", required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--check", action="store_true", help="validate the descriptor only, offline")
    parser.add_argument("--gh", default=shutil.which("gh") or "gh")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.check:
            descriptor = check_descriptor(args.root.resolve(), args.repository)
            result: dict[str, Any] = {"status": "pinned", "source_sha": descriptor["source_sha"]}
        else:
            if args.output_dir is None:
                parser.error("--output-dir is required unless --check is given")
            result = fetch(args.root.resolve(), args.repository, args.output_dir.resolve(), args.gh)
    except Unusable as error:
        print(f"pulp-view-audio-archive: {error}", file=sys.stderr)
        return 2
    except Mismatch as error:
        print(f"pulp-view-audio-archive: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True) if args.json else
          f"pulp-view-audio-archive: verified {result.get('archive', result['source_sha'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
