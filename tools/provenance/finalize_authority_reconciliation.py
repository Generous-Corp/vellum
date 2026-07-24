#!/usr/bin/env python3
"""Materialize the durable active Pulp/Vellum authority state."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
AUTHORITY_MODULE = Path(__file__).with_name("verify_authority_activation.py")
OBSERVATORY_MODULE = Path(__file__).with_name("observatory.py")
OWNERSHIP_PATH = Path("provenance/ownership-map.yaml")
EXTRACTION_PATH = Path("provenance/pulp-extraction.json")
LOCK_PATH = Path("provenance/pulp-observatory/provenance.lock")
LEGACY_MAP_PATH = Path("provenance/pulp-observatory/legacy-path-map.yaml")
CURSOR_PATH = Path("provenance/pulp-observatory/cursor.json")
REPORT_JSON_PATH = Path("provenance/pulp-observatory/reports/current.json")
REPORT_MD_PATH = Path("provenance/pulp-observatory/reports/current.md")
TRANSFER_PLAN = "../authority/transfer-plan.v2.json"
TRANSFERRED_AUTHORITY = "vellum-authoritative-transferred"
ACTIVE_VELLUM_SLICE_IDS = {
    "vellum-foundation",
    "vellum-design-ir",
    "vellum-platform-graphics",
    "vellum-authoring-js",
    "vellum-native-javascriptcore-host",
    "vellum-skia-dawn-renderer",
}


class ReconciliationError(RuntimeError):
    pass


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ReconciliationError(f"cannot load verifier module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


authority = _load_module("vellum_authority_activation", AUTHORITY_MODULE)
observatory = _load_module("vellum_authority_observatory", OBSERVATORY_MODULE)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReconciliationError(f"cannot read {path}: {error}") from error


def serialize_json(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def replace_once(source: str, before: str, after: str) -> str:
    if source.count(before) != 1:
        raise ReconciliationError(f"prepared ownership map differs at {before!r}")
    return source.replace(before, after)


def build_ownership_map(
    *,
    source: str,
    authority_start_commit: str,
    record_path: str,
    record_ref: str,
    record_commit: str,
    activation_commit: str,
    event_path: str,
    event_id: str,
    accepted_by: str,
    accepted_at: str,
) -> str:
    result = replace_once(source, "schema_version: 1\n", "schema_version: 2\n")
    prepared_activation = """activation:
  state: independent-validation
  pulp_extraction_base: 2ccff748f0d59da34b01ce1fbceabcf19f452731
  vellum_authority_commit: null
  pulp_activation_commit: null
  accepted_by: null
  accepted_at: null
"""
    active_activation = f"""activation:
  state: active
  pulp_extraction_base: 2ccff748f0d59da34b01ce1fbceabcf19f452731
  vellum_authority_start_commit: {authority_start_commit}
  vellum_authority_record_commit: {record_commit}
  authority_record_path: {record_path}
  authority_ref: {record_ref}
  pulp_activation_commit: {activation_commit}
  pulp_authority_event_path: {event_path}
  pulp_authority_event_id: {event_id}
  accepted_by: "{accepted_by}"
  accepted_at: "{accepted_at}"
"""
    result = replace_once(
        result, prepared_activation, active_activation
    )
    prepared_lineage_note = """# The historical projection remains byte-locked in cut-manifest.json and Git
# history. It is not present as editable source at the active tip and no source
# authority has transferred. The rows below describe historical lineage only.
"""
    active_lineage_note = """# The historical projection remains byte-locked in cut-manifest.json and Git
# history. It is not present as editable source at the active tip. Authority for
# the selected legacy slices is active in Vellum's independent implementation.
"""
    result = replace_once(result, prepared_lineage_note, active_lineage_note)
    lines = result.splitlines(keepends=True)
    current_slice: str | None = None
    activated: set[str] = set()
    for index, line in enumerate(lines):
        if line.startswith("  - id: "):
            current_slice = line.removeprefix("  - id: ").strip()
        elif (
            current_slice in ACTIVE_VELLUM_SLICE_IDS
            and line == "    state: framework-reimplemented-no-transfer\n"
        ):
            lines[index] = "    state: framework-authoritative-active\n"
            activated.add(current_slice)
    if activated != ACTIVE_VELLUM_SLICE_IDS:
        raise ReconciliationError(
            f"Vellum ownership slice set differs: "
            f"{sorted(ACTIVE_VELLUM_SLICE_IDS - activated)}"
        )
    result = "".join(lines)
    return result


def exact_check_evidence(
    *, evidence: dict[str, object], activation_commit: str,
    expected_apps: dict[str, int],
) -> None:
    rows = evidence.get("checks")
    if not isinstance(rows, list):
        raise ReconciliationError("activation evidence checks must be an array")
    normalized: dict[str, dict[str, object]] = {}
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("name"), str):
            normalized[str(row["name"])] = row
    if set(normalized) != set(expected_apps):
        raise ReconciliationError("activation evidence check names differ")
    for name, app_id in expected_apps.items():
        row = normalized[name]
        if (
            row.get("head_sha") != activation_commit
            or row.get("conclusion") != "success"
            or row.get("app_id") != app_id
            or not isinstance(row.get("check_run_id"), str)
            or not isinstance(row.get("details_url"), str)
        ):
            raise ReconciliationError(
                f"activation evidence check is not exact and App-bound: {name}"
            )


def verify_offline_activation(
    *, root: Path, pulp_repo: Path, record_path: str,
    record: dict[str, object], record_commit: str,
    evidence: dict[str, object], active_proof: dict[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    evidence = authority.validate_evidence_shape(evidence)
    activation_commit = str(evidence["pulp_activation_commit"])
    expected_proof = {
        "status": "pass",
        "authority_start_commit": record["authority_start_commit"],
        "authority_record_commit": record_commit,
        "pulp_activation_commit": activation_commit,
    }
    if active_proof != expected_proof:
        raise ReconciliationError("live authority proof coordinates differ")
    policy = authority.validate_trust_policy(
        authority.load_json(root / authority.TRUST_PATH)
    )
    exact_check_evidence(
        evidence=evidence,
        activation_commit=activation_commit,
        expected_apps=policy["repositories"]["pulp"]["required_check_app_ids"],
    )
    commit = authority.require_commit(
        pulp_repo, activation_commit, "Pulp activation commit"
    )
    authority.verify_exact_pulp_activation_transition(
        pulp_repo=pulp_repo,
        activation_commit=commit,
        authority_event_path=str(evidence["authority_event_path"]),
    )
    for field in ("ownership_projection_path", "authority_event_path"):
        expected_blob = evidence[
            "ownership_projection_blob"
            if field == "ownership_projection_path"
            else "authority_event_blob"
        ]
        if authority.git_blob(pulp_repo, commit, str(evidence[field])) != expected_blob:
            raise ReconciliationError(f"Pulp activation evidence blob differs: {field}")
    ownership = authority.json_at(
        pulp_repo, commit, str(evidence["ownership_projection_path"])
    )
    event = authority.json_at(
        pulp_repo, commit, str(evidence["authority_event_path"])
    )
    expected_slices = sorted(
        {
            str(slice_id)
            for group in record["authority_groups"]
            for slice_id in group["pulp_legacy_slices"]
        }
    )
    if (
        event.get("kind") != "authority-transition"
        or event.get("transition") != "activate"
        or event.get("vellum_authority_commit") != record_commit
        or event.get("counterpart") != record_path
        or event.get("slices") != expected_slices
        or event.get("approved_by") != record["approved_by"]
    ):
        raise ReconciliationError("landed Pulp authority event differs from record")
    activation = ownership.get("activation")
    if (
        not isinstance(activation, dict)
        or activation.get("state") != "active"
        or activation.get("vellum_authority_commit") != record_commit
        or activation.get("authority_record_path") != record_path
        or activation.get("initial_transition_event") != event.get("event_id")
        or activation.get("accepted_by") != event.get("approved_by")
        or activation.get("accepted_at") != event.get("created_at")
    ):
        raise ReconciliationError("landed Pulp ownership activation differs")
    slices = authority.ownership_slices(ownership)
    candidate_paths = sorted(
        {
            path
            for group in record["authority_groups"]
            for path in group["pulp_activation_candidate_projection"]
        }
    )
    authority.verify_activated_path_set(
        slices=slices,
        expected_slice_ids=set(expected_slices),
        candidate_paths=candidate_paths,
    )
    authority.verify_candidate_unchanged(
        pulp_repo=pulp_repo,
        candidate_commit=str(record["pulp_candidate_commit"]),
        activation_commit=commit,
        paths=candidate_paths,
    )
    for slice_id in expected_slices:
        item = slices[slice_id]
        metadata = item.get("authority")
        if (
            not isinstance(metadata, dict)
            or metadata.get("vellum_commit") != record_commit
            or metadata.get("counterpart") != record_path
            or metadata.get("event_id") != event.get("event_id")
        ):
            raise ReconciliationError(f"Pulp slice authority differs: {slice_id}")
    return ownership, event


def build_state(
    *, root: Path, pulp_repo: Path, vellum_repo: Path, record_path: str,
    record_commit: str, evidence: dict[str, object],
    active_proof: dict[str, object],
) -> dict[Path, bytes]:
    record = authority.validate_record_shape(load_json(root / record_path))
    authority.verify_pending_record(
        root=root,
        pulp_repo=pulp_repo,
        pulp_ownership_commit=str(record["pulp_candidate_commit"]),
        record_path=record_path,
        authority_record_commit=record_commit,
        expected_authority_ref=str(record["authority_record_ref"]),
        require_head=True,
    )
    _, event = verify_offline_activation(
        root=root,
        pulp_repo=pulp_repo,
        record_path=record_path,
        record=record,
        record_commit=record_commit,
        evidence=evidence,
        active_proof=active_proof,
    )
    activation_commit = str(evidence["pulp_activation_commit"])
    accepted_by = str(event["approved_by"])
    accepted_at = str(event["created_at"])

    lock = load_json(root / LOCK_PATH)
    if (
        not isinstance(lock, dict)
        or lock.get("state") != "prepared"
        or any(
            lock.get(field) is not None
            for field in (
                "vellum_authority_start_commit",
                "vellum_authority_record_commit",
                "pulp_activation_commit",
            )
        )
    ):
        raise ReconciliationError("observatory lock is not exactly prepared")
    lock.update(
        {
            "state": "active",
            "vellum_authority_start_commit": record["authority_start_commit"],
            "vellum_authority_record_commit": record_commit,
            "pulp_activation_commit": activation_commit,
            "ownership_schema_version": 2,
            "transfer_plan": TRANSFER_PLAN,
        }
    )

    legacy = load_json(root / LEGACY_MAP_PATH)
    if not isinstance(legacy, dict) or legacy.get("state") != "prepared-no-transfer":
        raise ReconciliationError("legacy map is not exactly prepared")
    transferred = {
        str(slice_id)
        for group in record["authority_groups"]
        for slice_id in group["pulp_legacy_slices"]
    }
    seen: set[str] = set()
    for item in legacy.get("mappings", []):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ReconciliationError("legacy map contains an invalid mapping")
        if item["id"] in transferred:
            if item.get("authority") != "pulp-authoritative-untransferred":
                raise ReconciliationError(
                    f"legacy mapping is not prepared: {item['id']}"
                )
            item["authority"] = TRANSFERRED_AUTHORITY
            seen.add(str(item["id"]))
    if seen != transferred:
        raise ReconciliationError(
            f"legacy map transfer set differs: {sorted(transferred - seen)}"
        )
    legacy["state"] = "active"

    cursor = load_json(root / CURSOR_PATH)
    if not isinstance(cursor, dict) or cursor.get("state") != "prepared":
        raise ReconciliationError("observatory cursor is not exactly prepared")
    authority.require_commit(vellum_repo, record_commit, "authority record commit")
    authority.require_commit(pulp_repo, activation_commit, "Pulp activation commit")
    cursor["state"] = "active"
    cursor["pulp"]["last_scanned_commit"] = activation_commit
    cursor["pulp"]["last_dispatch_event"] = event["event_id"]
    cursor["vellum"]["last_scanned_commit"] = record_commit
    cursor["reconciled_at"] = evidence["retrieved_at"]

    extraction = load_json(root / EXTRACTION_PATH)
    extraction_authority = extraction.get("authority")
    if (
        not isinstance(extraction_authority, dict)
        or extraction_authority.get("state") != "prepared"
        or extraction_authority.get("ownership_start_commit") is not None
        or extraction_authority.get("pulp_activation_commit") is not None
    ):
        raise ReconciliationError("extraction authority record is not prepared")
    extraction_authority.update(
        {
            "state": "active",
            "ownership_start_commit": record["authority_start_commit"],
            "authority_record_commit": record_commit,
            "authority_record_path": record_path,
            "authority_ref": record["authority_record_ref"],
            "pulp_activation_commit": activation_commit,
            "pulp_authority_event_path": evidence["authority_event_path"],
            "pulp_authority_event_id": event["event_id"],
            "accepted_by": accepted_by,
            "accepted_at": accepted_at,
        }
    )
    extraction["status"] = "active"
    extraction["notes"] = [
        note
        for note in extraction.get("notes", [])
        if "does not claim that source authority has transferred" not in str(note)
    ] + [
        "The historical seed remains immutable lineage; active source authority "
        f"is recorded by {record_commit} and the landed Pulp activation "
        f"{activation_commit}."
    ]

    ownership_text = build_ownership_map(
        source=(root / OWNERSHIP_PATH).read_text(encoding="utf-8"),
        authority_start_commit=str(record["authority_start_commit"]),
        record_path=record_path,
        record_ref=str(record["authority_record_ref"]),
        record_commit=record_commit,
        activation_commit=activation_commit,
        event_path=str(evidence["authority_event_path"]),
        event_id=str(event["event_id"]),
        accepted_by=accepted_by,
        accepted_at=accepted_at,
    )
    events = observatory.load_events(root)
    gaps = observatory.coverage_gaps(
        root=root,
        mapping=legacy,
        cursor=cursor,
        events=events,
        pulp_repo=pulp_repo,
        vellum_repo=vellum_repo,
    )
    now = observatory.parse_utc(str(evidence["retrieved_at"]), "evidence.retrieved_at")
    report = observatory.build_report(
        root=root,
        lock=lock,
        cursor=cursor,
        events=events,
        budgets=observatory.load_budgets(root / observatory.BUDGETS_PATH),
        now=now,
        coverage_gaps=gaps,
    )
    if report["health"] != "pass" or report["activation_blockers"]:
        raise ReconciliationError(
            "active observatory report is not healthy and blocker-free: "
            f"violations={report['budget_violations']!r}, "
            f"blockers={report['activation_blockers']!r}, "
            f"coverage_gaps={report['coverage_gaps']!r}"
        )
    return {
        OWNERSHIP_PATH: ownership_text.encode("utf-8"),
        EXTRACTION_PATH: serialize_json(extraction),
        LOCK_PATH: serialize_json(lock),
        LEGACY_MAP_PATH: serialize_json(legacy),
        CURSOR_PATH: serialize_json(cursor),
        REPORT_JSON_PATH: serialize_json(report),
        REPORT_MD_PATH: observatory.render_markdown(report).encode("utf-8"),
    }


def write_transaction(root: Path, outputs: dict[Path, bytes]) -> None:
    with tempfile.TemporaryDirectory(dir=root) as temporary:
        transaction = Path(temporary)
        staged = transaction / "staged"
        backup = transaction / "backup"
        for relative, content in outputs.items():
            target = staged / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            original = root / relative
            saved = backup / relative
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, saved)
        replaced: list[Path] = []
        try:
            for relative in sorted(outputs, key=lambda item: item.as_posix()):
                destination = root / relative
                os.replace(staged / relative, destination)
                replaced.append(relative)
        except OSError:
            for relative in reversed(replaced):
                os.replace(backup / relative, root / relative)
            raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--pulp-repo", type=Path, required=True)
    parser.add_argument("--vellum-repo", type=Path)
    parser.add_argument("--record-path", required=True)
    parser.add_argument("--authority-record-commit", required=True)
    parser.add_argument("--pulp-evidence", type=Path, required=True)
    parser.add_argument("--active-proof", type=Path, required=True)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        root = args.root.resolve()
        outputs = build_state(
            root=root,
            pulp_repo=args.pulp_repo.resolve(),
            vellum_repo=(args.vellum_repo or root).resolve(),
            record_path=args.record_path,
            record_commit=args.authority_record_commit,
            evidence=load_json(args.pulp_evidence),
            active_proof=load_json(args.active_proof),
        )
        result = {
            "status": "pass",
            "mode": "write" if args.write else "check",
            "authority_record_commit": args.authority_record_commit,
            "pulp_activation_commit": load_json(args.pulp_evidence)[
                "pulp_activation_commit"
            ],
            "outputs": [path.as_posix() for path in sorted(outputs)],
        }
        if args.write:
            write_transaction(root, outputs)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (
        ReconciliationError,
        authority.ActivationError,
        observatory.ObservatoryError,
        OSError,
        ValueError,
    ) as error:
        print(f"authority-reconciliation: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
