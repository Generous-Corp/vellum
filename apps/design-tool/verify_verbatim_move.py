#!/usr/bin/env python3
"""Prove the design-tool files moved from Pulp are Pulp's bytes.

Each manifest in apps/design-tool/moves/ names a Pulp commit and, per moved
file, its Pulp path (`source_path`) and the git blob id (`source_blob`) of
Pulp's file at that commit. The respelling applied on the way in is listed in
provenance/design-tool/respell.json, one entry per moved path: an exact `old`
string, its `new` spelling and how many times it occurs. The table lives under
provenance/, like the framework's respell table, because it has to spell the
Pulp names that Vellum's native trees refuse. The check undoes the respelling
(exactly `count` occurrences of each `new`, and no more), hashes the result as
a git blob, and requires that hash to equal the recorded blob. So any changed
byte, an unlisted respelling or a wrong count fails.

With --pulp-repo it also reads each recorded blob id from that Pulp checkout
(`git rev-parse <commit>:<path>`), so the manifest is checked against Pulp
itself rather than against its own record, and requires that no `new`
spelling already occurs in Pulp's file (which would make the undo ambiguous).

Exit status: 0 every file verified, 1 a mismatch, 2 a malformed manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

REPO = Path(__file__).resolve().parents[2]
MOVES = Path(__file__).resolve().parent / "moves"
RESPELL_TABLE = "provenance/design-tool/respell.json"
RESPELL_SCHEMA = "vellum.design-tool-respell.v1"
SCHEMA = "vellum.design-tool-verbatim-move.v1"


class ManifestError(Exception):
    pass


def git_blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def undo_respelling(data: bytes, respell: list[dict]) -> bytes:
    for entry in reversed(respell):
        old, new, count = entry["old"].encode(), entry["new"].encode(), entry["count"]
        found = data.count(new)
        if found != count:
            raise ValueError(f"respelling {entry['old']!r} -> {entry['new']!r} occurs {found} times, "
                             f"the manifest says {count}")
        data = data.replace(new, old)
    return data


def load(manifest_path: Path) -> dict:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ManifestError(f"{manifest_path}: {error}") from error
    if manifest.get("schema") != SCHEMA:
        raise ManifestError(f"{manifest_path}: schema must be {SCHEMA}")
    if not manifest.get("source_commit") or not isinstance(manifest.get("files"), list) or not manifest["files"]:
        raise ManifestError(f"{manifest_path}: needs source_commit and a non-empty files list")
    for item in manifest["files"]:
        if not {"path", "source_path", "source_blob"} <= set(item):
            raise ManifestError(f"{manifest_path}: each file needs path, source_path and source_blob")
    return manifest


def load_respellings(root: Path) -> dict[str, list[dict]]:
    """The respelling table, by moved path. A missing table means no respelling."""
    path = root / RESPELL_TABLE
    if not path.is_file():
        return {}
    try:
        table = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ManifestError(f"{path}: {error}") from error
    if table.get("schema") != RESPELL_SCHEMA or not isinstance(table.get("entries"), list):
        raise ManifestError(f"{path}: schema must be {RESPELL_SCHEMA} with an entries list")
    by_path: dict[str, list[dict]] = {}
    for entry in table["entries"]:
        if not {"path", "old", "new", "count"} <= set(entry) or not entry["new"]:
            raise ManifestError(f"{path}: each respelling needs path, old, new and count")
        by_path.setdefault(entry["path"], []).append(entry)
    return by_path


def verify(root: Path, manifests: list[Path], pulp_repo: Path | None) -> list[str]:
    problems: list[str] = []
    respellings = load_respellings(root)
    moved = {item["path"] for manifest_path in manifests for item in load(manifest_path)["files"]}
    for stray in sorted(set(respellings) - moved):
        problems.append(f"{RESPELL_TABLE}: {stray} is respelled but no manifest moves it")
    for manifest_path in manifests:
        manifest = load(manifest_path)
        commit = manifest["source_commit"]
        for item in manifest["files"]:
            path = root / item["path"]
            respell = respellings.get(item["path"], [])
            try:
                original = undo_respelling(path.read_bytes(), respell)
            except (OSError, ValueError) as error:
                problems.append(f"{item['path']}: {error}")
                continue
            if git_blob_id(original) != item["source_blob"]:
                problems.append(f"{item['path']}: differs from Pulp {item['source_path']} at {commit} "
                                f"beyond the listed respelling")
            if pulp_repo is None:
                continue
            completed = subprocess.run(
                ["git", "-C", str(pulp_repo), "rev-parse", f"{commit}:{item['source_path']}"],
                text=True, capture_output=True, check=False)
            if completed.returncode != 0 or completed.stdout.strip() != item["source_blob"]:
                problems.append(f"{item['path']}: Pulp's {item['source_path']} at {commit} is not blob "
                                f"{item['source_blob']} ({completed.stdout.strip() or completed.stderr.strip()})")
                continue
            pulp_bytes = subprocess.run(
                ["git", "-C", str(pulp_repo), "cat-file", "blob", item["source_blob"]],
                capture_output=True, check=True).stdout
            for entry in respell:
                if entry["new"].encode() in pulp_bytes:
                    problems.append(f"{item['path']}: {entry['new']!r} already occurs in Pulp's file, "
                                    f"so the respelling cannot be undone unambiguously")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pulp-repo", type=Path, help="also check each recorded blob against this Pulp checkout")
    parser.add_argument("--root", type=Path, default=REPO, help=argparse.SUPPRESS)
    parser.add_argument("manifests", nargs="*", type=Path, help="default: every manifest in moves/")
    args = parser.parse_args(argv)
    manifests = args.manifests or sorted(MOVES.glob("*.json"))
    if not manifests:
        print("verify_verbatim_move: no manifests", file=sys.stderr)
        return 2
    try:
        problems = verify(args.root, manifests, args.pulp_repo)
    except ManifestError as error:
        print(f"verify_verbatim_move: {error}", file=sys.stderr)
        return 2
    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    if problems:
        return 1
    count = sum(len(load(m)["files"]) for m in manifests)
    print(f"verbatim move verified: {count} file(s) in {len(manifests)} manifest(s)"
          + (" against the Pulp checkout" if args.pulp_repo else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
