#!/usr/bin/env python3
"""Refuse a Three.js runtime payload that does not match its pin.

The payload under assets/threejs is pinned file by file in
payload-manifest.json, and the manifest itself is pinned by digest in
provenance/third-party-lock.json. A missing, extra or changed file, or a
manifest edited without the lock, is an error.

  python3 -I scripts/check_threejs_payload.py [--root REPO] [--payload DIR]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

SCHEMA = "vellum.threejs-payload.v1"
LOCK_NAME = "Three.js"
SIDE_FILES = {"payload-manifest.json", "README.md"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_payload(payload: Path, manifest: dict) -> list[str]:
    errors: list[str] = []
    if manifest.get("schema") != SCHEMA:
        errors.append(f"manifest schema is not {SCHEMA}")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        return errors + ["manifest lists no files"]
    for rel, expected in sorted(files.items()):
        path = payload / rel
        if not path.is_file():
            errors.append(f"missing {rel}")
        elif sha256(path) != expected:
            errors.append(f"digest mismatch for {rel}")
    present = {p.relative_to(payload).as_posix() for p in payload.rglob("*") if p.is_file()}
    for extra in sorted(present - set(files) - SIDE_FILES):
        errors.append(f"unlisted file {extra}")
    return errors


def check_lock(manifest_path: Path, manifest: dict, lock: dict) -> list[str]:
    rows = [d for d in lock.get("dependencies", []) if d.get("name") == LOCK_NAME]
    if len(rows) != 1:
        return [f"third-party-lock.json has {len(rows)} {LOCK_NAME} entries, expected 1"]
    row = rows[0]
    errors = []
    if row.get("version") != manifest.get("package_version"):
        errors.append("lock version differs from the manifest")
    if row.get("revision") != manifest.get("revision"):
        errors.append("lock revision differs from the manifest")
    if row.get("payload_manifest_sha256") != sha256(manifest_path):
        errors.append("lock payload_manifest_sha256 differs from payload-manifest.json")
    return errors


def check(root: Path, payload: Path | None = None) -> list[str]:
    payload = payload or root / "assets/threejs"
    manifest_path = payload / "payload-manifest.json"
    if not manifest_path.is_file():
        return [f"{manifest_path} is missing"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    errors = check_payload(payload, manifest)
    lock_path = root / "provenance/third-party-lock.json"
    if lock_path.is_file():
        errors += check_lock(manifest_path, manifest, json.loads(lock_path.read_text(encoding="utf-8")))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--payload", type=Path)
    args = parser.parse_args()
    errors = check(args.root, args.payload)
    for error in errors:
        print(f"threejs payload: {error}", file=sys.stderr)
    if not errors:
        print("threejs payload: all files match the pin")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
