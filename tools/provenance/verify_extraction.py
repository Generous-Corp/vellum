#!/usr/bin/env python3
"""Verify immutable extraction history and the active-source boundary.

The historical cut manifest and filter maps describe the original projection.
They are verified against the preserved seed commit, not against HEAD. HEAD is
checked independently in two scopes (Decision 0010):

- Vellum-native roots must contain no Pulp namespace, Pulp package or target
  name, audio/plug-in SDK reference, or byte copy of any Pulp-origin blob.
- The mirror root holds Pulp framework source exported by automation. Every
  mirrored file must match an append-only blob-origin record, and no mirrored
  file may carry an audio, plug-in, host, or product edge. The Pulp namespace
  is permitted there because mirrored bytes stay identical to their origin.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable


FILTERED_SEED = "e4f8c96fcfd19bac433252c36fdf5bfa681e6d25"
EXPECTED_SOURCE = "2ccff748f0d59da34b01ce1fbceabcf19f452731"
EXPECTED_HASHES = {
    "cut-manifest.json": "c2b392afa4c6a05d3079e31e24e21275b9a72e058adb4036539c7f5634e2d78d",
    "cut-paths.txt": "efe6dbb8d517bdee801dcc37ab4dd4a9a08868618e870acd9101a195d6caa753",
    "filter-repo-commit-map.txt": "c2bfb2665fbecfdab2407c02143bea0f8bf9d18cc779abb12293ff29b7f909c7",
    "filter-repo-ref-map.txt": "ef7546204c7bcbdffbd7bee4d234a2956a201bf51a287de1385afe9a15b01582",
}
EXPECTED_ACTIVE_ROOTS = (
    "CMakeLists.txt",
    "apps",
    "authoring",
    "cli",
    "cmake",
    "foundation",
    "graphics",
    "packages/vellum-design-ir",
    "packages/vellum-ui",
    "rmlui",
    "runtime",
    "scripts",
    "templates",
)
MIRROR_ROOT = "mirror/"
MIRROR_ORIGIN_RECORDS = "provenance/mirror/blob-origins.json"
MIRROR_ORIGIN_SCHEMA = "vellum.mirror-blob-origins.v1"
MIRROR_DECISION = "decisions/0010-mirror-extracted-pulp-framework-source.md"
MIRROR_RECORD_KEYS = {
    "mirror_path",
    "git_blob_sha",
    "pulp_commit",
    "pulp_path",
    "export_receipt",
}
EXPECTED_RULES = {
    "retired_paths_at_active_tip": 0,
    "native_files_matching_pulp_origin_blobs": 0,
    "mirror_files_without_matching_origin_record": 0,
    "mirror_files_without_pulp_identity_proof": 0,
    "mirror_origin_records_naming_unlanded_receipts": 0,
    "native_forbidden_namespace_or_product_references": 0,
    "mirror_audio_plugin_host_or_product_edges": 0,
    "open_extraction_debts": 0,
}
EXPECTED_RETIRED_PREFIXES = (
    "core/",
    "external/fonts/",
    "external/nanosvg/",
    "packages/pulp-import-ir/",
    "tools/figma-plugin/",
)
# Which files are text, and the Pulp names native source refuses, are shared
# with the SDK installer's payload scan (content_scan_policy.py).
CONTENT_SCAN_POLICY_PATH = Path(__file__).with_name("content_scan_policy.py")
_POLICY_SPEC = importlib.util.spec_from_file_location("content_scan_policy", CONTENT_SCAN_POLICY_PATH)
assert _POLICY_SPEC and _POLICY_SPEC.loader
content_scan_policy = importlib.util.module_from_spec(_POLICY_SPEC)
_POLICY_SPEC.loader.exec_module(content_scan_policy)
FORBIDDEN_ACTIVE_PATTERNS = content_scan_policy.compiled_text_patterns()


def scans_active_content(relative: str) -> bool:
    """Whether the native scan reads this checkout path as text."""
    suffix = Path(relative).suffix.lower()
    if suffix in content_scan_policy.NATIVE_SOURCE_EXCLUDED_SUFFIXES:
        return False
    return content_scan_policy.should_scan_content(relative, extensionless=True)


# Forbidden mirror edges (Decision 0010 / D3): audio, DSP, MIDI, music theory,
# plug-in formats, plug-in hosting, GPU audio and product content stay
# Pulp-owned, and so do the Pulp-kept view and state files. The table is
# vendored byte-for-byte from Pulp's tools/scripts/vellum_mirror_edge_patterns.json,
# which Pulp's closure gate applies before export. mirror_edge_patterns.pin.json
# records the Pulp commit and git blob it was vendored from: every run checks
# the vendored bytes against that blob, --mirror-edge-pulp-root proves the blob
# against a Pulp checkout of the pinned commit, and --pulp-root additionally
# compares it with the Pulp commit of the last export.
MIRROR_EDGE_TABLE = Path(__file__).with_name("mirror_edge_patterns.json")
MIRROR_EDGE_TABLE_PIN = Path(__file__).with_name("mirror_edge_patterns.pin.json")
PULP_MIRROR_EDGE_TABLE = "tools/scripts/vellum_mirror_edge_patterns.json"
_MIRROR_EDGE_TABLE = json.loads(MIRROR_EDGE_TABLE.read_text(encoding="utf-8"))
FORBIDDEN_MIRROR_EDGE_PATTERNS = {
    name: re.compile(pattern) for name, pattern in _MIRROR_EDGE_TABLE["content_patterns"].items()
}
# Path rules are relative to the mirror root.
MIRROR_EDGE_PATH_PATTERNS = {
    name: re.compile(pattern) for name, pattern in _MIRROR_EDGE_TABLE["path_patterns"].items()
}
PULP_KEPT_MIRROR_PATH = MIRROR_EDGE_PATH_PATTERNS["mirror-pulp-kept-file"]
MIRROR_EDGE_SUFFIXES = set(_MIRROR_EDGE_TABLE["suffixes"])
MIRROR_EDGE_BINARY_SUFFIXES = set(_MIRROR_EDGE_TABLE["binary_suffixes"])
# One exact line of one Pulp path may be exempt from one content rule, each
# tied to a recorded Pulp view-audio seam debt (Pulp generates these rows).
MIRROR_EDGE_EXCEPTIONS = {
    (row["rule"], row["path"], row["line"]) for row in _MIRROR_EDGE_TABLE.get("approved_exceptions", [])
}


def mirror_edge_exempt(
    rule: str, path_in_mirror: str, text: str, start: int, exceptions: set | None = None
) -> bool:
    """Whether the match at `start` sits on an approved exception line."""
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", start)
    line = text[line_start:] if line_end < 0 else text[line_start:line_end]
    return (rule, path_in_mirror, line) in (MIRROR_EDGE_EXCEPTIONS if exceptions is None else exceptions)
POLICY_IMPLEMENTATION_PATHS = {
    "scripts/verify_sdk_artifact.py",
}
SHA_RE = re.compile(r"[0-9a-f]{40}")
# An empty file is identical everywhere and proves no copying.
EMPTY_BLOB_SHA = "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
UTC_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def load_mirror_export_verifier():
    path = Path(__file__).with_name("verify_mirror_export.py")
    spec = importlib.util.spec_from_file_location("verify_mirror_export", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_blob_sha(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content).hexdigest()  # noqa: S324 - Git object identity


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=root, text=True, stderr=subprocess.STDOUT
    ).strip()


def seed_blobs(root: Path) -> dict[str, str]:
    output = git(root, "ls-tree", "-r", FILTERED_SEED)
    blobs: dict[str, str] = {}
    for line in output.splitlines():
        metadata, path = line.split("\t", 1)
        _mode, object_type, object_id = metadata.split(" ")
        if object_type == "blob":
            blobs[path] = object_id
    return blobs


def top_level_scalar(path: Path, key: str) -> str | None:
    prefix = f"{key}:"
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            return line.removeprefix(prefix).strip()
    return None


def configured_boundary(root: Path) -> dict[str, object]:
    return json.loads((root / "provenance/active-source-boundary.json").read_text())


def active_files(root: Path, roots: Iterable[str]) -> Iterable[Path]:
    seen: set[Path] = set()
    for relative in roots:
        candidate = root / relative
        if candidate.is_file():
            files = [candidate]
        elif candidate.is_dir():
            files = [path for path in candidate.rglob("*") if path.is_file()]
        else:
            continue
        for path in files:
            if path in seen:
                continue
            if any(part in {"node_modules", "__pycache__", ".git"} for part in path.parts):
                continue
            seen.add(path)
            yield path


def scan_active_surface(root: Path, roots: Iterable[str] = EXPECTED_ACTIVE_ROOTS) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for path in active_files(root, roots):
        relative = path.relative_to(root).as_posix()
        if relative in POLICY_IMPLEMENTATION_PATHS or not scans_active_content(relative):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for name, pattern in FORBIDDEN_ACTIVE_PATTERNS.items():
            for match in pattern.finditer(text):
                findings.append(
                    {
                        "rule": name,
                        "path": path.relative_to(root).as_posix(),
                        "line": text.count("\n", 0, match.start()) + 1,
                        "match": match.group(0)[:120],
                    }
                )
    return findings


def retired_path_findings(root: Path, prefixes: Iterable[str]) -> list[str]:
    tracked = set(git(root, "ls-files").splitlines())
    findings: set[str] = set()
    for prefix in prefixes:
        findings.update(path for path in tracked if path.startswith(prefix))
        candidate = root / prefix.rstrip("/")
        if candidate.is_file():
            findings.add(candidate.relative_to(root).as_posix())
        elif candidate.is_dir():
            findings.update(
                path.relative_to(root).as_posix()
                for path in candidate.rglob("*")
                if path.is_file()
            )
    return sorted(findings)


def copied_seed_findings(
    root: Path, roots: Iterable[str], historical_blob_ids: set[str]
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for path in active_files(root, roots):
        blob = git_blob_sha(path.read_bytes())
        if blob in historical_blob_ids:
            findings.append({"path": path.relative_to(root).as_posix(), "git_blob_sha": blob})
    return findings


def load_mirror_origin_records(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    """Load the append-only blob-origin records for the mirror root."""
    if not path.is_file():
        return [], [f"{MIRROR_ORIGIN_RECORDS} is missing"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [], [f"{MIRROR_ORIGIN_RECORDS} is unreadable: {exc}"]
    if not isinstance(data, dict) or set(data) != {"schema", "pulp_repository", "records"}:
        return [], [f"{MIRROR_ORIGIN_RECORDS} has unexpected top-level keys"]
    errors: list[str] = []
    if data["schema"] != MIRROR_ORIGIN_SCHEMA:
        errors.append(f"{MIRROR_ORIGIN_RECORDS} schema must be {MIRROR_ORIGIN_SCHEMA}")
    if data["pulp_repository"] != "Generous-Corp/pulp":
        errors.append(f"{MIRROR_ORIGIN_RECORDS} must name Generous-Corp/pulp")
    records = data["records"]
    if not isinstance(records, list):
        return [], errors + [f"{MIRROR_ORIGIN_RECORDS} records must be a list"]
    valid: list[dict[str, str]] = []
    seen: set[tuple[str, ...]] = set()
    for index, record in enumerate(records):
        label = f"{MIRROR_ORIGIN_RECORDS} record {index}"
        if not isinstance(record, dict) or set(record) != MIRROR_RECORD_KEYS:
            errors.append(f"{label} has unexpected keys")
            continue
        if not all(isinstance(record[key], str) for key in MIRROR_RECORD_KEYS):
            errors.append(f"{label} has a non-string field")
            continue
        if not SHA_RE.fullmatch(record["git_blob_sha"]):
            errors.append(f"{label} git_blob_sha is not a full object ID")
        if not SHA_RE.fullmatch(record["pulp_commit"]):
            errors.append(f"{label} pulp_commit is not a full commit SHA")
        mirror_path = record["mirror_path"]
        if not mirror_path.startswith(MIRROR_ROOT) or ".." in mirror_path.split("/"):
            errors.append(f"{label} mirror_path is outside {MIRROR_ROOT}")
        pulp_path = record["pulp_path"]
        if not pulp_path or pulp_path.startswith("/") or ".." in pulp_path.split("/"):
            errors.append(f"{label} pulp_path is not a repository-relative path")
        if not record["export_receipt"]:
            errors.append(f"{label} names no export receipt")
        identity = tuple(record[key] for key in sorted(MIRROR_RECORD_KEYS))
        if identity in seen:
            errors.append(f"{label} duplicates an earlier record")
        seen.add(identity)
        valid.append(record)
    return valid, errors


def mirror_files(root: Path) -> Iterable[Path]:
    return active_files(root, (MIRROR_ROOT.rstrip("/"),))


def mirror_origin_findings(
    root: Path, records: Iterable[dict[str, str]]
) -> list[dict[str, str]]:
    """Every mirrored file must equal a recorded Pulp-origin blob at its path.

    Records are append-only, so an earlier export's record may remain after a
    later export replaced or removed the file; only the reverse is a failure.
    """
    recorded: dict[str, set[str]] = {}
    for record in records:
        recorded.setdefault(record["mirror_path"], set()).add(record["git_blob_sha"])
    findings: list[dict[str, str]] = []
    for path in mirror_files(root):
        relative = path.relative_to(root).as_posix()
        blob = git_blob_sha(path.read_bytes())
        if relative not in recorded:
            rule = "mirror-file-without-origin-record"
        elif blob not in recorded[relative]:
            rule = "mirror-blob-differs-from-origin-record"
        else:
            continue
        findings.append({"rule": rule, "path": relative, "git_blob_sha": blob})
    return findings


def mirror_edge_table_drift(
    pulp_root: Path, table: Path = MIRROR_EDGE_TABLE, pin_path: Path = MIRROR_EDGE_TABLE_PIN
) -> list[str]:
    """The vendored edge table is Pulp's copy at this checkout, or a later one.

    The checkout is the Pulp commit of the last export. The table may advance
    on Pulp main between exports (a stricter rule lands before the next
    export), so a different copy passes only when the pinned commit is on
    Pulp main (refs/remotes/origin/main), descends from this checkout and
    holds the vendored blob. verify() then scans the mirror under both the
    vendored table and this checkout's table.
    """
    pulp_table = pulp_root / PULP_MIRROR_EDGE_TABLE
    if not pulp_table.is_file():
        return [f"Pulp checkout {pulp_root} has no {PULP_MIRROR_EDGE_TABLE}"]
    vendored = table.read_bytes()
    if pulp_table.read_bytes() == vendored:
        return []
    differs = f"{table.name} differs from Pulp's {PULP_MIRROR_EDGE_TABLE}"
    try:
        pin = json.loads(pin_path.read_text(encoding="utf-8"))
        pinned = str(pin["pulp_commit"])
        head = git(pulp_root, "rev-parse", "HEAD")
        descends = subprocess.run(
            ["git", "merge-base", "--is-ancestor", head, pinned],
            cwd=pulp_root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        ).returncode == 0
        blob = git(pulp_root, "rev-parse", f"{pinned}:{PULP_MIRROR_EDGE_TABLE}")
    except (OSError, KeyError, json.JSONDecodeError, subprocess.CalledProcessError):
        return [f"{differs}, and the pinned commit cannot be resolved in {pulp_root}"]
    # A descendant on a side branch is unreviewed: only Pulp main may advance the table.
    main = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", "refs/remotes/origin/main"],
        cwd=pulp_root, capture_output=True, text=True, check=False,
    )
    if main.returncode != 0 or not main.stdout.strip():
        return [f"{differs}, and {pulp_root} has no refs/remotes/origin/main to prove the pin is on Pulp main"]
    on_main = subprocess.run(
        ["git", "merge-base", "--is-ancestor", pinned, main.stdout.strip()],
        cwd=pulp_root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    ).returncode == 0
    if not on_main:
        return [f"{differs}, and the pinned commit {pinned} is not on Pulp main"]
    if not descends:
        return [f"{differs}, and the pinned commit {pinned} does not descend from {head}"]
    if blob != git_blob_sha(vendored) or blob != pin.get("git_blob"):
        return [f"{differs}, and the pinned commit {pinned} does not hold the vendored table"]
    return []


def mirror_edge_table_pin_errors(
    pulp_root: Path | None = None, table: Path = MIRROR_EDGE_TABLE, pin_path: Path = MIRROR_EDGE_TABLE_PIN
) -> list[str]:
    """The vendored table is the exact blob its pin names, and, given a Pulp
    checkout of the pinned commit, that blob is Pulp's table there."""
    try:
        pin = json.loads(pin_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{pin_path.name} is unreadable: {exc}"]
    if pin.get("schema") != "vellum.mirror-edge-table-pin.v1" \
            or pin.get("pulp_repository") != "Generous-Corp/pulp" \
            or pin.get("pulp_path") != PULP_MIRROR_EDGE_TABLE \
            or not re.fullmatch(r"[0-9a-f]{40}", str(pin.get("pulp_commit", ""))) \
            or not re.fullmatch(r"[0-9a-f]{40}", str(pin.get("git_blob", ""))):
        return [f"{pin_path.name} must name Generous-Corp/pulp, {PULP_MIRROR_EDGE_TABLE}, a commit and a git blob"]
    errors: list[str] = []
    if git_blob_sha(table.read_bytes()) != pin["git_blob"]:
        errors.append(f"{table.name} is not the git blob {pin['git_blob']} its pin names")
    if pulp_root is not None:
        try:
            head = git(pulp_root, "rev-parse", "HEAD")
            blob = git(pulp_root, "rev-parse", f"HEAD:{PULP_MIRROR_EDGE_TABLE}")
        except (OSError, subprocess.CalledProcessError) as exc:
            return errors + [f"Pulp checkout {pulp_root} cannot resolve the pinned table: {exc}"]
        if head != pin["pulp_commit"]:
            errors.append(f"Pulp checkout {pulp_root} is at {head}, not the pinned {pin['pulp_commit']}")
        if blob != pin["git_blob"]:
            errors.append(f"Pulp's {PULP_MIRROR_EDGE_TABLE} at {head} is blob {blob}, not the pinned {pin['git_blob']}")
    return errors


def compile_mirror_edge_table(table: dict) -> dict[str, object]:
    return {
        "content": {name: re.compile(pattern) for name, pattern in table["content_patterns"].items()},
        "path": {name: re.compile(pattern) for name, pattern in table["path_patterns"].items()},
        "suffixes": set(table["suffixes"]),
        "binary_suffixes": set(table.get("binary_suffixes", [])),
        "exceptions": {(row["rule"], row["path"], row["line"]) for row in table.get("approved_exceptions", [])},
    }


def mirror_edge_findings(root: Path, table: dict | None = None) -> list[dict[str, object]]:
    """Mirror edges under the vendored table, or under another table's rules."""
    if table is not None:
        rules = compile_mirror_edge_table(table)
        content_rules, path_rules = rules["content"], rules["path"]
        suffixes, binary, exceptions = rules["suffixes"], rules["binary_suffixes"], rules["exceptions"]
    else:
        content_rules, path_rules = FORBIDDEN_MIRROR_EDGE_PATTERNS, MIRROR_EDGE_PATH_PATTERNS
        suffixes, binary, exceptions = MIRROR_EDGE_SUFFIXES, MIRROR_EDGE_BINARY_SUFFIXES, MIRROR_EDGE_EXCEPTIONS
    findings: list[dict[str, object]] = []
    for path in mirror_files(root):
        relative = path.relative_to(root).as_posix()
        in_mirror = relative.removeprefix(MIRROR_ROOT)
        for name, pattern in path_rules.items():
            if pattern.search(in_mirror):
                findings.append({"rule": name, "path": relative, "line": 0, "match": relative})
        suffix = path.suffix.lower()
        if suffix in binary:
            continue
        if suffix not in suffixes:
            findings.append({"rule": "mirror-unclassified-suffix", "path": relative, "line": 0, "match": suffix})
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for name, pattern in content_rules.items():
            for match in pattern.finditer(text):
                if mirror_edge_exempt(name, in_mirror, text, match.start(), exceptions):
                    continue
                findings.append(
                    {
                        "rule": name,
                        "path": path.relative_to(root).as_posix(),
                        "line": text.count("\n", 0, match.start()) + 1,
                        "match": match.group(0)[:120],
                    }
                )
    return findings


PULP_DEPENDENCY_MANIFEST = "tools/deps/manifest.json"
RENDERER_ASSET_KEY = "mac-universal"


def current_mirror_receipts(
    root: Path, receipts: Iterable[str]
) -> tuple[list[str], list[str], list[str]]:
    """Split landed receipts into the current one and the older ones.

    The current receipt is the one whose mirror_tree is HEAD:mirror: the
    mirror Vellum builds today. Returns (current, older, errors); exactly one
    receipt may be current.
    """
    receipts = list(receipts)
    if not receipts:
        return [], [], []
    try:
        head_tree = git(root, "rev-parse", "HEAD:mirror")
    except subprocess.CalledProcessError:
        return [], [], ["HEAD has no mirror/ tree to name the current export receipt"]
    current: list[str] = []
    older: list[str] = []
    for receipt_path in receipts:
        try:
            receipt = json.loads((root / receipt_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            receipt = {}
        tree = receipt.get("mirror_tree") if isinstance(receipt, dict) else None
        (current if tree == head_tree else older).append(receipt_path)
    if len(current) != 1:
        return current, older, [
            f"{len(current)} landed receipts name HEAD:mirror ({head_tree}); exactly one must"]
    return current, older, []


def renderer_pin_errors(
    root: Path, pulp_root: Path, receipts: Iterable[str]
) -> tuple[list[str], list[dict[str, object]]]:
    """The current mirror export must come from a Pulp commit pinning the locked renderer.

    A mirror export copies Pulp sources that compile against Pulp's Skia/Dawn
    pin. If that pin is not the archive and source tuple Vellum's lock names,
    the export and the renderer it links disagree, and the dependency checksums
    alone would not notice. Callers pass the current receipt only (see
    current_mirror_receipts): the pin is a property of the mirror Vellum builds
    today, so an older receipt is never judged against a lock it was not
    exported under; it keeps its identity, origin and blob checks.
    """
    lock = json.loads((root / "provenance/third-party-lock.json").read_text(encoding="utf-8"))
    renderer = lock["runtime_toolchain_dependencies"]["renderer"]
    artifact = renderer["artifact"]
    identities = renderer["source_identities"]
    expected = {
        "skia_branch": artifact["release_tag"],
        "skia_commit": identities["skia"]["commit"],
        "built_dawn": identities["dawn"]["commit"],
        "skia_builder_ref": identities["builder"]["commit"],
        "archive_url": artifact["url"],
        "archive_sha256": artifact["sha256"],
    }
    errors: list[str] = []
    rows: list[dict[str, object]] = []
    for receipt_path in receipts:
        receipt = json.loads((root / receipt_path).read_text(encoding="utf-8"))
        pulp_sha = receipt.get("pulp_sha")
        label = f"{receipt_path} (Pulp {pulp_sha})"
        try:
            manifest = json.loads(git(pulp_root, "show", f"{pulp_sha}:{PULP_DEPENDENCY_MANIFEST}"))
        except (subprocess.CalledProcessError, ValueError) as exc:
            errors.append(f"{label}: cannot read Pulp's {PULP_DEPENDENCY_MANIFEST}: {exc}")
            continue
        pins = [d.get("determinism") for d in manifest.get("dependencies", [])
                if isinstance(d, dict) and isinstance(d.get("determinism"), dict)
                and d["determinism"].get("skia_branch")]
        if len(pins) != 1:
            errors.append(f"{label}: expected one Skia determinism pin, found {len(pins)}")
            continue
        pin = pins[0]
        asset = (pin.get("release_assets") or {}).get(RENDERER_ASSET_KEY) or {}
        actual = {key: pin.get(key) for key in ("skia_branch", "skia_commit", "built_dawn", "skia_builder_ref")}
        actual.update(archive_url=asset.get("url"), archive_sha256=asset.get("sha256"))
        mismatched = sorted(key for key in expected if actual[key] != expected[key])
        rows.append({"receipt": receipt_path, "pulp_sha": pulp_sha, "matches_lock": not mismatched})
        if mismatched:
            errors.append(
                f"{label}: Pulp's renderer pin differs from third-party-lock.json in "
                + ", ".join(f"{key} ({actual[key]} != {expected[key]})" for key in mismatched))
    return errors, rows


def mirror_renderer_pin_check(
    root: Path, pulp_root: Path, receipts: Iterable[str]
) -> tuple[list[str], list[dict[str, object]]]:
    """Check the current receipt's renderer pin; report older receipts as unchecked."""
    current, older, errors = current_mirror_receipts(root, receipts)
    pin_errors, rows = renderer_pin_errors(root, pulp_root, current) if not errors else ([], [])
    rows += [{"receipt": path, "matches_lock": None, "checked": "older export, not HEAD:mirror"}
             for path in older]
    return errors + pin_errors, rows


def verify_origins_against_pulp(
    pulp_root: Path, records: Iterable[dict[str, str]]
) -> list[str]:
    """Prove each record against the Pulp history it cites (identity oracle)."""
    errors: list[str] = []
    try:
        main = git(pulp_root, "rev-parse", "--verify", "--quiet", "refs/remotes/origin/main")
    except subprocess.CalledProcessError:
        return [f"Pulp checkout {pulp_root} has no refs/remotes/origin/main"]
    for record in records:
        on_main = subprocess.run(
            ["git", "merge-base", "--is-ancestor", record["pulp_commit"], main],
            cwd=pulp_root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ).returncode == 0
        if not on_main:
            errors.append(
                f"mirror origin {record['mirror_path']} cites Pulp commit "
                f"{record['pulp_commit']}, which is not on the checked-out Pulp main"
            )
            continue
        spec = f"{record['pulp_commit']}:{record['pulp_path']}"
        try:
            actual = git(pulp_root, "rev-parse", "--verify", "--quiet", spec)
        except subprocess.CalledProcessError:
            actual = ""
        if actual != record["git_blob_sha"]:
            errors.append(
                f"mirror origin {record['mirror_path']} is not blob "
                f"{record['git_blob_sha']} at Pulp {spec}"
            )
    return errors


def verify_authority_phase(
    extraction: dict[str, object], lock: dict[str, object]
) -> tuple[bool, list[str]]:
    errors: list[str] = []
    authority = extraction.get("authority")
    if not isinstance(authority, dict):
        return False, ["pulp-extraction.json authority record is malformed"]
    state = authority.get("state")
    if extraction.get("status") == "prepared" and state == "prepared":
        for field in (
            "ownership_start_commit",
            "authority_record_commit",
            "pulp_activation_commit",
            "accepted_by",
            "accepted_at",
        ):
            if authority.get(field) is not None:
                errors.append(f"prepared extraction authority carries {field}")
        if lock.get("state") != "prepared":
            errors.append("prepared extraction authority differs from observatory lock")
        return False, errors
    if extraction.get("status") != "active" or state != "active":
        return False, ["extraction status and authority state must be prepared or active"]
    coordinates = {
        "ownership_start_commit": lock.get("vellum_authority_start_commit"),
        "authority_record_commit": lock.get("vellum_authority_record_commit"),
        "pulp_activation_commit": lock.get("pulp_activation_commit"),
    }
    if lock.get("state") != "active":
        errors.append("active extraction authority differs from observatory lock")
    for field, expected in coordinates.items():
        actual = authority.get(field)
        if (
            not isinstance(actual, str)
            or not SHA_RE.fullmatch(actual)
            or actual != expected
        ):
            errors.append(f"active extraction authority has invalid {field}")
    for field in (
        "authority_record_path",
        "authority_ref",
        "pulp_authority_event_path",
        "pulp_authority_event_id",
        "accepted_by",
    ):
        if not isinstance(authority.get(field), str) or not authority[field]:
            errors.append(f"active extraction authority lacks {field}")
    accepted_at = authority.get("accepted_at")
    if not isinstance(accepted_at, str) or not UTC_RE.fullmatch(accepted_at):
        errors.append("active extraction authority has invalid accepted_at")
    return not errors, errors


def verify(
    root: Path,
    pulp_root: Path | None = None,
    defer_mirror_identity: bool = False,
    mirror_edge_pulp_root: Path | None = None,
) -> dict[str, object]:
    provenance = root / "provenance"
    errors: list[str] = []
    checks: dict[str, object] = {}

    pin_errors = mirror_edge_table_pin_errors(mirror_edge_pulp_root)
    checks["mirror_edge_table_matches_pin"] = not pin_errors
    checks["mirror_edge_table_proved_against_pulp"] = mirror_edge_pulp_root is not None and not pin_errors
    errors.extend(pin_errors)

    for name, expected in EXPECTED_HASHES.items():
        actual = sha256(provenance / name)
        checks[f"sha256:{name}"] = actual
        if actual != expected:
            errors.append(f"{name}: expected sha256 {expected}, got {actual}")

    extraction = json.loads((provenance / "pulp-extraction.json").read_text())
    lock = json.loads(
        (provenance / "pulp-observatory/provenance.lock").read_text()
    )
    authority_transferred, authority_errors = verify_authority_phase(
        extraction, lock
    )
    errors.extend(authority_errors)
    if extraction["source"]["commit"] != EXPECTED_SOURCE:
        errors.append("pulp-extraction.json source commit drifted")
    declared_hashes = {
        "cut-manifest.json": extraction["cut"]["manifest_sha256"],
        "cut-paths.txt": extraction["cut"]["path_specification_sha256"],
        "filter-repo-commit-map.txt": extraction["history_extraction"]["commit_map_sha256"],
        "filter-repo-ref-map.txt": extraction["history_extraction"]["ref_map_sha256"],
    }
    if declared_hashes != EXPECTED_HASHES:
        errors.append("pulp-extraction.json immutable digest declarations drifted")

    manifest = json.loads((provenance / "cut-manifest.json").read_text())
    entries = manifest.get("entries", [])
    unresolved = [entry for entry in entries if entry["classification"] == "unresolved"]
    checks["historical_manifest_entry_count"] = len(entries)
    checks["historical_unresolved_entry_count"] = len(unresolved)
    if len(entries) != extraction["cut"]["entry_count"] or len(entries) != 235:
        errors.append(f"cut manifest has {len(entries)} entries, expected 235")
    if len(unresolved) != extraction["cut"]["unresolved_entry_count"] or len(unresolved) != 39:
        errors.append(f"cut manifest has {len(unresolved)} unresolved entries, expected 39")
    if len({entry["source_path"] for entry in entries}) != len(entries):
        errors.append("cut manifest contains duplicate source paths")

    blobs = seed_blobs(root)
    blob_mismatches = []
    for entry in entries:
        historical_path = entry["source_path"]
        actual = blobs.get(historical_path)
        if actual != entry["git_blob_sha"]:
            blob_mismatches.append(
                {"path": historical_path, "expected": entry["git_blob_sha"], "actual": actual}
            )
    checks["historical_seed_blob_mismatches"] = blob_mismatches
    if blob_mismatches:
        errors.append(f"{len(blob_mismatches)} filtered-seed blobs differ from the cut manifest")

    source_row = None
    for line in (provenance / "filter-repo-commit-map.txt").read_text().splitlines():
        if line.startswith(EXPECTED_SOURCE + " "):
            source_row = line.split()[1]
            break
    checks["source_commit_map_row"] = source_row
    if source_row != "0" * 40:
        errors.append("expected source tip to map to zero because it touched no selected path")
    ref_map = (provenance / "filter-repo-ref-map.txt").read_text()
    if f"{EXPECTED_SOURCE} {FILTERED_SEED} refs/heads/main" not in ref_map:
        errors.append("filtered main ref mapping is missing or changed")

    boundary = configured_boundary(root)
    active_roots = tuple(boundary.get("active_source_roots", []))
    retired_prefixes = tuple(boundary.get("retired_prefixes", []))
    if active_roots != EXPECTED_ACTIVE_ROOTS:
        errors.append("active source roots differ from the required complete inventory")
    if retired_prefixes != EXPECTED_RETIRED_PREFIXES:
        errors.append("retired prefixes differ from the required extraction boundary")
    expected_mirror = {
        "root": MIRROR_ROOT,
        "origin_records": MIRROR_ORIGIN_RECORDS,
        "decision": MIRROR_DECISION,
    }
    if boundary.get("mirror") != expected_mirror:
        errors.append("mirror scope differs from the Decision 0010 boundary")
    if boundary.get("rules") != EXPECTED_RULES:
        errors.append("declared boundary rules differ from the enforced rules")
    if not (root / MIRROR_DECISION).is_file():
        errors.append(f"{MIRROR_DECISION} is missing")

    inventory = sorted(path.relative_to(root).as_posix() for path in active_files(root, active_roots))
    checks["active_source_file_count"] = len(inventory)
    checks["active_source_files"] = inventory

    retired_findings = retired_path_findings(root, retired_prefixes)
    checks["retired_path_findings"] = retired_findings
    if retired_findings:
        errors.append(f"active tip still contains {len(retired_findings)} retired projection paths")

    records, record_errors = load_mirror_origin_records(root / MIRROR_ORIGIN_RECORDS)
    errors.extend(record_errors)
    checks["mirror_origin_record_count"] = len(records)

    # A Pulp-origin blob is legal only under the mirror root with a matching
    # record. In a native root it would be a second editable copy.
    pulp_origin_blobs = {
        entry["git_blob_sha"]
        for entry in entries
        if entry["source_path"] not in {"DEPENDENCIES.md", "LICENSE.md", "NOTICE.md"}
    } | {record["git_blob_sha"] for record in records}
    pulp_origin_blobs.discard(EMPTY_BLOB_SHA)
    copied_findings = copied_seed_findings(root, active_roots, pulp_origin_blobs)
    checks["active_historical_blob_matches"] = copied_findings
    if copied_findings:
        errors.append(
            f"native source contains {len(copied_findings)} unrecorded Pulp-origin blob copies"
        )

    mirror_inventory = sorted(path.relative_to(root).as_posix() for path in mirror_files(root))
    checks["mirror_file_count"] = len(mirror_inventory)
    origin_findings = mirror_origin_findings(root, records)
    checks["mirror_origin_findings"] = origin_findings
    if origin_findings:
        errors.append(f"mirror root has {len(origin_findings)} files without a matching origin record")
    # Fail closed: a self-consistent record proves nothing about origin, so a
    # mirrored file passes only where a Pulp checkout proved it. Lanes that are
    # not the required provenance lane may defer that proof to it.
    if pulp_root is not None:
        drift = mirror_edge_table_drift(pulp_root)
        checks["mirror_edge_table_matches_pulp"] = not drift
        errors.extend(drift)
        pulp_errors = verify_origins_against_pulp(pulp_root, records)
        checks["mirror_origins_verified_against_pulp"] = not pulp_errors
        errors.extend(pulp_errors)
    elif mirror_inventory and defer_mirror_identity:
        checks["mirror_identity_deferred"] = True
    elif mirror_inventory:
        errors.append(
            f"mirror root has {len(mirror_inventory)} files but no Pulp checkout proved "
            "them; pass --pulp-root"
        )
    export_verifier = load_mirror_export_verifier()
    landed_receipts = set(export_verifier.landed_receipts(root))
    unlanded = sorted(
        {record["export_receipt"] for record in records} - landed_receipts
    )
    checks["mirror_origin_records_naming_unlanded_receipts"] = unlanded
    if unlanded:
        errors.append(
            f"blob-origin records name {len(unlanded)} receipts absent from the receipt index"
        )
    export_report = export_verifier.verify(root, pulp_root)
    checks["mirror_export_receipts"] = export_report["receipts"]
    if pulp_root is not None:
        pin_errors, pin_rows = mirror_renderer_pin_check(
            root, pulp_root, export_verifier.landed_receipts(root))
        checks["mirror_renderer_pin_matches_lock"] = pin_rows
        errors.extend(pin_errors)
    else:
        checks["mirror_renderer_pin_matches_lock"] = "deferred: no Pulp checkout"
    checks["mirror_export_pulp_identity_checked"] = export_report["pulp_identity_checked"]
    errors.extend(f"mirror export: {error}" for error in export_report["errors"])
    edge_findings = mirror_edge_findings(root)
    checks["mirror_edge_findings"] = edge_findings
    if edge_findings:
        errors.append(f"mirror root has {len(edge_findings)} audio, plug-in, host, or product edges")
    # The export commit's own table must pass too, so an advanced table
    # cannot admit an edge the exporting gate refused.
    export_table_path = pulp_root / PULP_MIRROR_EDGE_TABLE if pulp_root is not None else None
    if export_table_path is not None and export_table_path.is_file() \
            and export_table_path.read_bytes() != MIRROR_EDGE_TABLE.read_bytes():
        export_findings = mirror_edge_findings(root, json.loads(export_table_path.read_text(encoding="utf-8")))
        checks["mirror_edge_findings_under_export_table"] = export_findings
        if export_findings:
            errors.append(
                f"mirror root has {len(export_findings)} edges under the export commit's mirror-edge table")

    active_findings = scan_active_surface(root, active_roots)
    checks["active_forbidden_findings"] = active_findings
    if active_findings:
        errors.append(f"native build/authoring surface has {len(active_findings)} forbidden references")

    debt = provenance / "extraction-debt/initial.yaml"
    debt_status = top_level_scalar(debt, "status")
    open_debt_count = top_level_scalar(debt, "open_debt_count")
    debt_text = debt.read_text(encoding="utf-8")
    checks["extraction_debt"] = {"status": debt_status, "open_debt_count": open_debt_count}
    if debt_status != "closed" or open_debt_count != "0" or "\ndebts: []\n" not in debt_text:
        errors.append("extraction debt must be closed with zero open rows after seed retirement")

    dependency_lock = json.loads((provenance / "third-party-lock.json").read_text())
    dependencies = dependency_lock.get("dependencies")
    if not isinstance(dependencies, list):
        errors.append("third-party dependency inventory is malformed")
        dependencies = []
    for dependency in dependencies:
        if "path" in dependency and "sha256" in dependency:
            dependency_path = root / dependency["path"]
            if not dependency_path.is_file() or sha256(dependency_path) != dependency["sha256"]:
                errors.append(f"third-party checksum mismatch: {dependency['path']}")
    checks["active_third_party_dependency_count"] = len(dependencies)

    return {
        "schema_version": 3,
        "status": "pass" if not errors else "fail",
        "authority_transferred": authority_transferred,
        "raw_seed_present_at_active_tip": bool(retired_findings or copied_findings),
        "checks": checks,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--pulp-root",
        type=Path,
        help="Pulp checkout used to prove every mirror origin record",
    )
    parser.add_argument(
        "--mirror-edge-pulp-root",
        type=Path,
        help="Pulp checkout of the commit mirror_edge_patterns.pin.json names",
    )
    parser.add_argument(
        "--defer-mirror-identity",
        action="store_true",
        help="leave the Pulp identity proof of mirror/ to the required provenance lane",
    )
    args = parser.parse_args()
    report = verify(
        args.root.resolve(),
        args.pulp_root.resolve() if args.pulp_root else None,
        defer_mirror_identity=args.defer_mirror_identity,
        mirror_edge_pulp_root=args.mirror_edge_pulp_root.resolve() if args.mirror_edge_pulp_root else None,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
