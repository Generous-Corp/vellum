#!/usr/bin/env python3
"""Verify Pulp-to-Vellum mirror export receipts (Decision 0010).

An export projects the closure manifests of one exact Pulp commit onto
`refs/heads/mirror/pulp-framework` with every path renamed under `mirror/`.
The landing merges that projected commit into Vellum and records its receipt,
closures, and commit map under `provenance/mirror/`. This verifier proves:

- the newest receipt's mirror tree is exactly `HEAD:mirror` (tree equality);
- every receipt agrees with its projected commit, closures, commit map, and
  blob-origin records;
- every closure passes the Vellum link-closure policy; and, with a Pulp
  checkout, every mirrored blob is the Pulp blob at the exported commit.

The executable form of the shared receipt specification; field rules here
win over prose.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any


RECEIPT_SCHEMA = "pulp.vellum.export-receipt.v2"
CLOSURE_SCHEMA = "pulp.vellum.closure.v1"
INDEX_SCHEMA = "vellum.mirror-receipt-index.v1"
POLICY_SCHEMA = "vellum.mirror-link-policy.v1"
MIRROR_DIR = "mirror"
MIRROR_REF = "refs/heads/mirror/pulp-framework"
PULP_REPOSITORY = "Generous-Corp/pulp"
INDEX_PATH = "provenance/mirror/receipts/index.json"
POLICY_PATH = "provenance/mirror/link-policy.json"
ORIGINS_PATH = "provenance/mirror/blob-origins.json"
RECEIPT_DIR = "provenance/mirror/receipts/"
CLOSURE_DIR = "provenance/mirror/closures/"
COMMIT_MAP_DIR = "provenance/mirror/commit-maps/"
RECEIPT_KEYS = {
    "schema", "export_id", "pulp_repository", "pulp_sha", "mirror_ref",
    "mirror_sha", "mirror_tree", "rename", "commit_map", "units", "files",
    "generated_inputs", "tool", "created_at",
}
UNIT_KEYS = {"name", "closure_manifest", "closure_sha256", "oracle_class"}
FILE_KEYS = {"mirror_path", "pulp_path", "mode", "blob"}
GENERATED_KEYS = {"path", "sha256", "producer_paths"}
POLICY_KEYS = {
    "schema", "allowed_external_targets", "forbidden_target_pattern",
    "forbidden_path_prefixes", "forbidden_definition_pattern",
}
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
FILE_MODES = {"100644", "100755", "120000"}


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=root, text=True, stderr=subprocess.DEVNULL
    ).strip()


def git_ok(root: Path, *args: str) -> bool:
    return subprocess.run(
        ["git", *args], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    ).returncode == 0


def git_try(root: Path, *args: str) -> str | None:
    try:
        return git(root, *args)
    except subprocess.CalledProcessError:
        return None


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def strict_json(path: Path) -> Any:
    def no_duplicates(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate JSON key {key!r}")
            result[key] = value
        return result

    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=no_duplicates)


def safe_relative(value: Any) -> bool:
    if not isinstance(value, str) or not value or value.startswith("/"):
        return False
    parts = PurePosixPath(value).parts
    return ".." not in parts and "." not in parts and "\\" not in value


def tree_entries(root: Path, commit: str) -> list[dict[str, str]] | None:
    output = git_try(root, "ls-tree", "-r", "--full-tree", commit)
    if output is None:
        return None
    entries = []
    for line in output.splitlines():
        metadata, path = line.split("\t", 1)
        mode, kind, blob = metadata.split(" ")
        entries.append({"mirror_path": path, "mode": mode, "blob": blob, "kind": kind})
    return entries


def validate_receipt_shape(receipt: Any, label: str) -> list[str]:
    if not isinstance(receipt, dict) or set(receipt) != RECEIPT_KEYS:
        return [f"{label}: receipt keys must be exactly {sorted(RECEIPT_KEYS)}"]
    errors: list[str] = []
    expected = {
        "schema": RECEIPT_SCHEMA,
        "pulp_repository": PULP_REPOSITORY,
        "mirror_ref": MIRROR_REF,
        "rename": {"from_prefix": "", "to_prefix": "mirror/"},
    }
    for key, value in expected.items():
        if receipt[key] != value:
            errors.append(f"{label}: {key} must be {value!r}")
    for key in ("pulp_sha", "mirror_sha", "mirror_tree"):
        if not isinstance(receipt[key], str) or not SHA40.fullmatch(receipt[key]):
            errors.append(f"{label}: {key} is not a full object ID")
    pulp_sha = receipt["pulp_sha"] if isinstance(receipt["pulp_sha"], str) else ""
    export_id = receipt["export_id"]
    if not isinstance(export_id, str) or not re.fullmatch(
        r"\d{8}-" + re.escape(pulp_sha[:12]) + r"\Z", export_id
    ) or len(pulp_sha) != 40:
        errors.append(f"{label}: export_id must be YYYYMMDD-<pulp_sha[:12]>")
        export_id = ""
    if not isinstance(receipt["created_at"], str) or not UTC.fullmatch(receipt["created_at"]):
        errors.append(f"{label}: created_at must be a UTC timestamp")
    commit_map = receipt["commit_map"]
    if (
        not isinstance(commit_map, dict)
        or set(commit_map) != {"path", "sha256"}
        or commit_map.get("path") != f"{COMMIT_MAP_DIR}{export_id}.txt"
        or not SHA256.fullmatch(str(commit_map.get("sha256")))
    ):
        errors.append(f"{label}: commit_map must name {COMMIT_MAP_DIR}<export_id>.txt and its sha256")
    tool = receipt["tool"]
    if (
        not isinstance(tool, dict)
        or set(tool) != {"path", "pulp_blob"}
        or tool.get("path") != "tools/scripts/vellum_closure.py"
        or not SHA40.fullmatch(str(tool.get("pulp_blob")))
    ):
        errors.append(f"{label}: tool must name tools/scripts/vellum_closure.py and its blob")
    units = receipt["units"]
    if not isinstance(units, list) or not units:
        errors.append(f"{label}: units must be a non-empty list")
    else:
        names: set[str] = set()
        for index, unit in enumerate(units):
            where = f"{label}: units[{index}]"
            if not isinstance(unit, dict) or set(unit) != UNIT_KEYS:
                errors.append(f"{where} has unexpected keys")
                continue
            if not isinstance(unit["name"], str) or not unit["name"] or unit["name"] in names:
                errors.append(f"{where} name is empty or duplicated")
            names.add(str(unit["name"]))
            if unit["oracle_class"] not in {"identity", "divergent"}:
                errors.append(f"{where} oracle_class must be identity or divergent")
            manifest = unit["closure_manifest"]
            if (
                not safe_relative(manifest)
                or not manifest.startswith(f"{CLOSURE_DIR}{export_id}/")
                or not manifest.endswith(".json")
            ):
                errors.append(f"{where} closure_manifest must live under {CLOSURE_DIR}<export_id>/")
            if not SHA256.fullmatch(str(unit["closure_sha256"])):
                errors.append(f"{where} closure_sha256 is not a SHA-256")
    files = receipt["files"]
    if not isinstance(files, list) or not files:
        errors.append(f"{label}: files must be a non-empty list")
    else:
        previous = ""
        for index, row in enumerate(files):
            where = f"{label}: files[{index}]"
            if not isinstance(row, dict) or set(row) != FILE_KEYS:
                errors.append(f"{where} has unexpected keys")
                continue
            if not safe_relative(row["pulp_path"]):
                errors.append(f"{where} pulp_path is not repository-relative")
            if row["mirror_path"] != f"mirror/{row['pulp_path']}":
                errors.append(f"{where} mirror_path must be mirror/<pulp_path>")
            if row["mode"] not in FILE_MODES:
                errors.append(f"{where} mode is not a file mode")
            if not SHA40.fullmatch(str(row["blob"])):
                errors.append(f"{where} blob is not a full object ID")
            if str(row["mirror_path"]) <= previous:
                errors.append(f"{where} files must be sorted and unique by mirror_path")
            previous = str(row["mirror_path"])
    generated = receipt["generated_inputs"]
    if not isinstance(generated, list):
        errors.append(f"{label}: generated_inputs must be a list")
    else:
        for index, row in enumerate(generated):
            where = f"{label}: generated_inputs[{index}]"
            if (
                not isinstance(row, dict)
                or set(row) != GENERATED_KEYS
                or not str(row.get("path", "")).startswith("@build/")
                or not SHA256.fullmatch(str(row.get("sha256")))
                or not isinstance(row.get("producer_paths"), list)
                or not row["producer_paths"]
            ):
                errors.append(f"{where} must be an @build/ path, sha256, and producer_paths")
    return errors


def load_policy(root: Path) -> tuple[dict[str, Any] | None, list[str]]:
    path = root / POLICY_PATH
    try:
        policy = strict_json(path)
    except (OSError, ValueError) as exc:
        return None, [f"{POLICY_PATH}: unreadable: {exc}"]
    if not isinstance(policy, dict) or set(policy) != POLICY_KEYS:
        return None, [f"{POLICY_PATH}: keys must be exactly {sorted(POLICY_KEYS)}"]
    errors = []
    if policy["schema"] != POLICY_SCHEMA:
        errors.append(f"{POLICY_PATH}: schema must be {POLICY_SCHEMA}")
    for key in ("forbidden_target_pattern", "forbidden_definition_pattern"):
        try:
            re.compile(policy[key])
        except (re.error, TypeError):
            errors.append(f"{POLICY_PATH}: {key} is not a regular expression")
    for key in ("allowed_external_targets", "forbidden_path_prefixes"):
        if not isinstance(policy[key], list) or not all(isinstance(x, str) for x in policy[key]):
            errors.append(f"{POLICY_PATH}: {key} must be a list of strings")
    return (policy if not errors else None), errors


def link_closure_findings(
    closures: dict[str, dict[str, Any]], policy: dict[str, Any]
) -> list[str]:
    """Re-check machine-generated closures against the Vellum-owned policy."""
    findings: list[str] = []
    forbidden_target = re.compile(policy["forbidden_target_pattern"], re.IGNORECASE)
    forbidden_define = re.compile(policy["forbidden_definition_pattern"])
    allowed_external = set(policy["allowed_external_targets"])
    exported_targets = {
        target.get("name")
        for closure in closures.values()
        for target in closure.get("targets", [])
        if isinstance(target, dict)
    }
    for unit, closure in sorted(closures.items()):
        if closure.get("missing_targets"):
            findings.append(f"{unit}: missing targets {closure['missing_targets']}")
        links = set(closure.get("transitive_link_targets", []))
        for target in closure.get("targets", []):
            links.update(target.get("link_targets", []))
        for link in sorted(links):
            if forbidden_target.search(link):
                findings.append(f"{unit}: forbidden link target {link}")
            elif link not in exported_targets and link not in allowed_external:
                findings.append(f"{unit}: link target {link} is neither exported nor allowed")
        for path in closure.get("files", []):
            if any(path.startswith(prefix) for prefix in policy["forbidden_path_prefixes"]):
                findings.append(f"{unit}: closure file under a forbidden Pulp root: {path}")
        for definition in closure.get("compile_definitions", []):
            if forbidden_define.search(definition):
                findings.append(f"{unit}: forbidden compile definition {definition}")
    return findings


def verify_receipt(
    root: Path, receipt_path: str, receipt: dict[str, Any], policy: dict[str, Any] | None,
    origins: list[dict[str, Any]], *, newest: bool, head: str,
) -> list[str]:
    label = receipt_path
    errors = validate_receipt_shape(receipt, label)
    if errors:
        return errors
    export_id = receipt["export_id"]
    if receipt_path != f"{RECEIPT_DIR}{export_id}.json":
        errors.append(f"{label}: file name must be {RECEIPT_DIR}<export_id>.json")
    mirror_sha = receipt["mirror_sha"]
    if not git_ok(root, "cat-file", "-e", f"{mirror_sha}^{{commit}}"):
        return errors + [f"{label}: mirror_sha {mirror_sha} is not a commit in this repository"]
    if not git_ok(root, "merge-base", "--is-ancestor", mirror_sha, head):
        errors.append(f"{label}: mirror_sha is not an ancestor of HEAD")
    top = git_try(root, "ls-tree", "--name-only", mirror_sha) or ""
    if top.splitlines() != [MIRROR_DIR]:
        errors.append(f"{label}: mirror_sha root tree must contain only mirror/")
    if git_try(root, "rev-parse", f"{mirror_sha}:{MIRROR_DIR}") != receipt["mirror_tree"]:
        errors.append(f"{label}: mirror_tree differs from {mirror_sha}:mirror")
    if newest and git_try(root, "rev-parse", f"{head}:{MIRROR_DIR}") != receipt["mirror_tree"]:
        errors.append(f"{label}: HEAD:mirror is not the newest receipt's mirror tree")
    entries = tree_entries(root, mirror_sha) or []
    projected = [
        {"mirror_path": e["mirror_path"], "mode": e["mode"], "blob": e["blob"]}
        for e in entries
        if e["kind"] == "blob"
    ]
    if any(e["kind"] != "blob" for e in entries):
        errors.append(f"{label}: mirror_sha contains non-blob entries (submodules)")
    recorded = [
        {"mirror_path": f["mirror_path"], "mode": f["mode"], "blob": f["blob"]}
        for f in receipt["files"]
    ]
    if projected != recorded:
        errors.append(f"{label}: files differ from the projected tree of mirror_sha")

    commit_map = root / receipt["commit_map"]["path"]
    if not commit_map.is_file() or sha256_file(commit_map) != receipt["commit_map"]["sha256"]:
        errors.append(f"{label}: commit map is missing or its sha256 differs")
    elif f"{receipt['pulp_sha']} {mirror_sha}" not in commit_map.read_text(encoding="utf-8").splitlines():
        errors.append(f"{label}: commit map lacks the row '<pulp_sha> <mirror_sha>'")

    closures: dict[str, dict[str, Any]] = {}
    for unit in receipt["units"]:
        path = root / unit["closure_manifest"]
        if not path.is_file() or sha256_file(path) != unit["closure_sha256"]:
            errors.append(f"{label}: closure {unit['closure_manifest']} is missing or differs")
            continue
        try:
            closure = strict_json(path)
        except ValueError as exc:
            errors.append(f"{label}: closure {unit['closure_manifest']} unreadable: {exc}")
            continue
        if (
            not isinstance(closure, dict)
            or closure.get("schema") != CLOSURE_SCHEMA
            or closure.get("source_root") is not None
            or closure.get("build_dir") is not None
            or not isinstance(closure.get("files"), list)
        ):
            errors.append(
                f"{label}: closure {unit['name']} must be {CLOSURE_SCHEMA} with null source_root/build_dir"
            )
            continue
        closures[unit["name"]] = closure
    if len(closures) == len(receipt["units"]):
        closure_paths = {
            p for c in closures.values() for p in c["files"] if not p.startswith("@build/")
        }
        if closure_paths != {f["pulp_path"] for f in receipt["files"]}:
            errors.append(f"{label}: closure files differ from the exported file set")
        generated = {
            p for c in closures.values() for p in c["files"] if p.startswith("@build/")
        } | {p for c in closures.values() for p in c.get("generated_inputs", [])}
        if generated != {g["path"] for g in receipt["generated_inputs"]}:
            errors.append(f"{label}: generated_inputs differ from the closures' @build/ inputs")
        exported = {f["pulp_path"] for f in receipt["files"]}
        for row in receipt["generated_inputs"]:
            missing = [p for p in row["producer_paths"] if p not in exported]
            if missing:
                errors.append(f"{label}: generated input {row['path']} producers not exported: {missing}")
        if policy is not None:
            errors.extend(f"{label}: {f}" for f in link_closure_findings(closures, policy))

    recorded_origins = {
        (o.get("mirror_path"), o.get("git_blob_sha"))
        for o in origins
        if o.get("pulp_commit") == receipt["pulp_sha"] and o.get("export_receipt") == receipt_path
    }
    missing_origins = [
        f["mirror_path"] for f in receipt["files"]
        if (f["mirror_path"], f["blob"]) not in recorded_origins
    ]
    if missing_origins:
        errors.append(
            f"{label}: {len(missing_origins)} files lack a blob-origin record for this receipt"
        )
    return errors


def verify_receipt_against_pulp(pulp_root: Path, receipt: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    pulp_sha = receipt["pulp_sha"]
    if not git_ok(pulp_root, "cat-file", "-e", f"{pulp_sha}^{{commit}}"):
        return [f"Pulp commit {pulp_sha} is not available in {pulp_root}"]
    main = git_try(pulp_root, "rev-parse", "--verify", "--quiet", "refs/remotes/origin/main") or git_try(
        pulp_root, "rev-parse", "HEAD"
    )
    if main is None or not git_ok(pulp_root, "merge-base", "--is-ancestor", pulp_sha, main):
        errors.append(f"Pulp commit {pulp_sha} is not reachable from Pulp main")
    listing = git_try(pulp_root, "ls-tree", "-r", "--full-tree", pulp_sha) or ""
    origin: dict[str, tuple[str, str]] = {}
    for line in listing.splitlines():
        metadata, path = line.split("\t", 1)
        mode, _kind, blob = metadata.split(" ")
        origin[path] = (mode, blob)
    for row in receipt["files"]:
        if origin.get(row["pulp_path"]) != (row["mode"], row["blob"]):
            errors.append(f"{row['mirror_path']} is not Pulp {pulp_sha}:{row['pulp_path']}")
    tool = receipt["tool"]
    if origin.get(tool["path"], ("", ""))[1] != tool["pulp_blob"]:
        errors.append(f"tool blob differs from Pulp {pulp_sha}:{tool['path']}")
    return errors


def verify(root: Path, pulp_root: Path | None = None) -> dict[str, Any]:
    errors: list[str] = []
    head = git(root, "rev-parse", "HEAD")
    try:
        index = strict_json(root / INDEX_PATH)
    except (OSError, ValueError) as exc:
        return {"status": "fail", "receipts": [], "errors": [f"{INDEX_PATH}: unreadable: {exc}"]}
    if (
        not isinstance(index, dict)
        or set(index) != {"schema", "receipts"}
        or index["schema"] != INDEX_SCHEMA
        or not isinstance(index["receipts"], list)
        or not all(isinstance(x, str) for x in index["receipts"])
        or len(set(index["receipts"])) != len(index["receipts"])
    ):
        return {"status": "fail", "receipts": [], "errors": [f"{INDEX_PATH}: malformed index"]}
    receipts: list[str] = index["receipts"]
    policy, policy_errors = load_policy(root)
    errors.extend(policy_errors)
    try:
        origins_doc = strict_json(root / ORIGINS_PATH)
        origins = origins_doc.get("records", []) if isinstance(origins_doc, dict) else []
    except (OSError, ValueError) as exc:
        errors.append(f"{ORIGINS_PATH}: unreadable: {exc}")
        origins = []
    stored = sorted(
        path.relative_to(root).as_posix()
        for path in (root / RECEIPT_DIR).glob("*.json")
        if path.name != "index.json"
    )
    if stored != sorted(receipts):
        errors.append("receipt files on disk differ from the receipt index")
    has_mirror = git_ok(root, "cat-file", "-e", f"{head}:{MIRROR_DIR}")
    if not receipts and has_mirror:
        errors.append("mirror/ exists at HEAD without an export receipt")
    pulp_checked = False
    for position, receipt_path in enumerate(receipts):
        try:
            receipt = strict_json(root / receipt_path)
        except (OSError, ValueError) as exc:
            errors.append(f"{receipt_path}: unreadable: {exc}")
            continue
        newest = position == len(receipts) - 1
        receipt_errors = verify_receipt(
            root, receipt_path, receipt, policy, origins, newest=newest, head=head
        )
        errors.extend(receipt_errors)
        if pulp_root is not None and not receipt_errors:
            errors.extend(f"{receipt_path}: {e}" for e in verify_receipt_against_pulp(pulp_root, receipt))
            pulp_checked = True
    return {
        "status": "pass" if not errors else "fail",
        "receipts": receipts,
        "pulp_identity_checked": pulp_checked,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--pulp-root", type=Path)
    parser.add_argument("--require-pulp-identity", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = verify(args.root.resolve(), args.pulp_root.resolve() if args.pulp_root else None)
    if args.require_pulp_identity and report["receipts"] and not report["pulp_identity_checked"]:
        report["status"] = "fail"
        report["errors"].append("receipts exist but Pulp identity was not proved")
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
