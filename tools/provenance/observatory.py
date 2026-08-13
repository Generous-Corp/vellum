#!/usr/bin/env python3
"""Reconcile and verify the append-only Pulp/Vellum change observatory.

The CLI owns orchestration and cross-record invariants. Event schemas, Git
discovery, reporting, and shared persistence primitives live in focused
modules and remain re-exported here for compatibility with existing callers.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

if __package__:
    from .observatory_common import (
        BUDGETS_PATH,
        CLASSES,
        CURSOR_PATH,
        DISPOSITIONS,
        EVENTS_PATH,
        EVENT_ID_RE,
        LOCK_PATH,
        MAP_PATH,
        OBSERVATORY,
        OBSERVATION_FIELDS,
        REPORT_JSON_PATH,
        REPORT_MD_PATH,
        REQUIRED_BUDGETS,
        RESOLUTION_FIELDS,
        ROOT,
        SHA_RE,
        ObservatoryError,
        canonical_bytes,
        canonical_sha256,
        git,
        load_budgets,
        load_json,
        parse_utc,
        utc_text,
        write_json_atomic,
        write_text_atomic,
    )
    from .observatory_events import (
        add_business_days,
        deadline,
        effective_observations,
        load_events,
        validate_event,
        validate_string_list,
    )
    from .observatory_git import (
        classify_paths,
        commits_between,
        delta_paths,
        is_ancestor,
        mapped_change,
        observation_for_commit,
        parse_diff_entries,
        patch_id,
        path_matches,
        require_ancestor,
        require_commit,
        transitive_match,
        tree_identical_scanned_parent,
    )
    from .observatory_report import (
        activation_blockers,
        build_report,
        render_markdown,
    )
else:
    from observatory_common import (
        BUDGETS_PATH,
        CLASSES,
        CURSOR_PATH,
        DISPOSITIONS,
        EVENTS_PATH,
        EVENT_ID_RE,
        LOCK_PATH,
        MAP_PATH,
        OBSERVATORY,
        OBSERVATION_FIELDS,
        REPORT_JSON_PATH,
        REPORT_MD_PATH,
        REQUIRED_BUDGETS,
        RESOLUTION_FIELDS,
        ROOT,
        SHA_RE,
        ObservatoryError,
        canonical_bytes,
        canonical_sha256,
        git,
        load_budgets,
        load_json,
        parse_utc,
        utc_text,
        write_json_atomic,
        write_text_atomic,
    )
    from observatory_events import (
        add_business_days,
        deadline,
        effective_observations,
        load_events,
        validate_event,
        validate_string_list,
    )
    from observatory_git import (
        classify_paths,
        commits_between,
        delta_paths,
        is_ancestor,
        mapped_change,
        observation_for_commit,
        parse_diff_entries,
        patch_id,
        path_matches,
        require_ancestor,
        require_commit,
        transitive_match,
        tree_identical_scanned_parent,
    )
    from observatory_report import (
        activation_blockers,
        build_report,
        render_markdown,
    )


def validate_lock_map_cursor(root: Path) -> tuple[dict[str, object], dict[str, object], dict[str, object], dict[str, int]]:
    lock = load_json(root / LOCK_PATH)
    mapping = load_json(root / MAP_PATH)
    cursor = load_json(root / CURSOR_PATH)
    budgets = load_budgets(root / BUDGETS_PATH)
    if not isinstance(lock, dict) or lock.get("schema_version") != 2:
        raise ObservatoryError("observatory provenance.lock schema_version must be 2")
    if lock.get("state") not in {"prepared", "active"}:
        raise ObservatoryError("observatory lock state must be prepared or active")
    state = str(lock["state"])
    coordinates = (
        "vellum_authority_start_commit",
        "vellum_authority_record_commit",
        "pulp_activation_commit",
    )
    if state == "prepared":
        if (
            any(lock.get(field) is not None for field in coordinates)
            or lock.get("ownership_schema_version") != 1
            or lock.get("transfer_plan") != "../authority/transfer-plan.v1.json"
        ):
            raise ObservatoryError("prepared observatory lock carries active coordinates")
    else:
        if (
            any(
                not isinstance(lock.get(field), str)
                or not SHA_RE.fullmatch(str(lock[field]))
                for field in coordinates
            )
            or lock.get("ownership_schema_version") != 2
            or lock.get("transfer_plan") != "../authority/transfer-plan.v2.json"
        ):
            raise ObservatoryError("active observatory lock lacks exact coordinates")
    policy = lock.get("policy")
    if policy != {
        "synchronized_editable_copies_allowed": False,
        "automatic_patch_application_allowed": False,
        "one_active_authority_required": True,
    }:
        raise ObservatoryError("observatory anti-synchronization policy drifted")
    if not isinstance(mapping, dict) or mapping.get("schema_version") != 2:
        raise ObservatoryError("legacy path map schema_version must be 2")
    expected_mapping_state = "prepared-no-transfer" if state == "prepared" else "active"
    if mapping.get("state") != expected_mapping_state:
        raise ObservatoryError("legacy path map phase differs from observatory lock")
    mappings = mapping.get("mappings")
    if not isinstance(mappings, list) or not mappings:
        raise ObservatoryError("legacy path map needs mappings")
    ids: set[str] = set()
    for item in mappings:
        if not isinstance(item, dict):
            raise ObservatoryError("each legacy mapping must be an object")
        required = {"id", "pulp_paths", "vellum_paths", "symbols", "targets", "schemas", "platform_hosts", "contract_tests", "authority"}
        if set(item) != required:
            raise ObservatoryError(f"legacy mapping fields differ: {item.get('id')}")
        identifier = item.get("id")
        if not isinstance(identifier, str) or identifier in ids:
            raise ObservatoryError("legacy mapping IDs must be unique strings")
        ids.add(identifier)
        for field in ("pulp_paths", "vellum_paths", "symbols", "targets", "schemas", "platform_hosts", "contract_tests"):
            validate_string_list(item.get(field), f"mapping.{identifier}.{field}", sorted_unique=False)
        authority = item.get("authority")
        allowed_authorities = (
            {"pulp-authoritative-untransferred", "pulp-only"}
            if state == "prepared"
            else {"vellum-authoritative-transferred", "pulp-only"}
        )
        if authority not in allowed_authorities:
            raise ObservatoryError(
                f"legacy mapping authority differs for phase: {identifier}"
            )
    if not isinstance(cursor, dict) or cursor.get("schema_version") != 2 or cursor.get("state") not in {"prepared", "active"}:
        raise ObservatoryError("cursor schema/state is invalid")
    if cursor.get("state") != lock.get("state"):
        raise ObservatoryError("cursor and provenance lock states differ")
    for source, repository in (("pulp", "Generous-Corp/pulp"), ("vellum", "Generous-Corp/vellum")):
        value = cursor.get(source)
        if not isinstance(value, dict) or set(value) != {"repository", "scan_base_commit", "last_scanned_commit", "last_dispatch_event"}:
            raise ObservatoryError(f"cursor.{source} fields differ")
        if value.get("repository") != repository:
            raise ObservatoryError(f"cursor.{source}.repository drifted")
        for field in ("scan_base_commit", "last_scanned_commit"):
            if not isinstance(value.get(field), str) or not SHA_RE.fullmatch(str(value[field])):
                raise ObservatoryError(f"cursor.{source}.{field} must be exact")
    reconciled = cursor.get("reconciled_at")
    if reconciled is not None:
        parse_utc(reconciled, "cursor.reconciled_at")
    if state == "active":
        if (
            not isinstance(cursor["pulp"].get("last_dispatch_event"), str)
            or not cursor["pulp"]["last_dispatch_event"]
        ):
            raise ObservatoryError("active cursor lacks its authority dispatch event")
    return lock, mapping, cursor, budgets


def mapping_fingerprint(mapping: dict[str, object]) -> str:
    """Return the stable identity of the mapping used for reconciliation."""

    return canonical_sha256(mapping)


def verify_active_cursor_ancestry(
    lock: dict[str, object],
    cursor: dict[str, object],
    pulp_repo: Path | None,
    vellum_repo: Path | None,
) -> None:
    if lock.get("state") != "active":
        return
    for source, repo, coordinate in (
        ("pulp", pulp_repo, lock["pulp_activation_commit"]),
        ("vellum", vellum_repo, lock["vellum_authority_record_commit"]),
    ):
        if repo is None:
            continue
        require_ancestor(
            repo,
            str(coordinate),
            str(cursor[source]["last_scanned_commit"]),
            f"cursor.{source} must include its authority coordinate",
        )


def expected_observations(
    *, source: str, repo: Path, repository: str, start: str, target: str,
    discovered_at: str, mappings: list[dict[str, object]], transitive_rules: list[str]
) -> list[dict[str, object]]:
    result = []
    for commit in commits_between(repo, start, target):
        event = observation_for_commit(
            source=source, repository=repository, repo=repo, commit=commit,
            cursor_from=start, cursor_to=target, discovered_at=discovered_at,
            mappings=mappings, transitive_rules=transitive_rules,
        )
        if event is not None:
            result.append(event)
    return result


def verify_event_against_git(event: dict[str, object], source: str, repo: Path, mappings: list[dict[str, object]], rules: list[str]) -> None:
    expected = observation_for_commit(
        source=source,
        repository=str(event["source_repository"]),
        repo=repo,
        commit=str(event["source_commit"]),
        cursor_from=str(event["scan_cursor"]["from_commit"]),
        cursor_to=str(event["scan_cursor"]["to_commit"]),
        discovered_at=str(event["discovered_at"]),
        mappings=mappings,
        transitive_rules=rules,
    )
    if expected is None:
        raise ObservatoryError(f"event does not correspond to a mapped change: {event['event_id']}")
    derived_fields = {
        "source_repository", "source_commit", "scan_cursor", "direction", "mapped_contracts",
        "mapped_paths", "transitive_paths", "rename_candidates", "patch_id",
        "include_dependency_deltas", "schema_api_deltas", "class", "contract_tests", "contract_keys"
    }
    mismatches = [field for field in sorted(derived_fields) if event[field] != expected[field]]
    if mismatches:
        raise ObservatoryError(f"event derived fields differ from Git for {event['event_id']}: {mismatches}")


def coverage_gaps(
    *, root: Path, mapping: dict[str, object], cursor: dict[str, object], events: list[tuple[Path, dict[str, object]]],
    pulp_repo: Path | None, vellum_repo: Path | None,
    pulp_target: str | None = None, vellum_target: str | None = None,
    incremental_from: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    observations = {
        (str(event["source_repository"]), str(event["source_commit"])): event
        for _, event in events if event["kind"] == "observation"
    }
    gaps: list[dict[str, str]] = []
    mappings = mapping["mappings"]
    rules = mapping.get("transitive_path_rules", [])
    assert isinstance(mappings, list) and isinstance(rules, list)
    for source, repo, requested_target in (
        ("pulp", pulp_repo, pulp_target), ("vellum", vellum_repo, vellum_target)
    ):
        if repo is None:
            gaps.append({"source": source, "commit": "unverified", "reason": "source-repository-not-supplied"})
            continue
        source_cursor = cursor[source]
        repository = str(source_cursor["repository"])
        # ``verify`` intentionally replays the complete extraction history.
        # Reconciliation has already verified the committed ledger and only
        # needs to prove the newly scanned range.  Keeping that distinction
        # avoids turning every append-only catch-up into an O(history) replay
        # while retaining the full audit command for release/CI gates.
        start = str(
            incremental_from[source]
            if incremental_from is not None and source in incremental_from
            else source_cursor["scan_base_commit"]
        )
        target = str(source_cursor["last_scanned_commit"])
        require_commit(repo, start, f"cursor.{source}.scan_base_commit")
        require_commit(repo, target, f"cursor.{source}.last_scanned_commit")
        require_ancestor(repo, start, target, f"cursor.{source}")
        scanned_commit_list = commits_between(repo, start, target)
        scanned_commits = set(scanned_commit_list)
        for commit in scanned_commit_list:
            probe = observation_for_commit(
                source=source, repository=repository, repo=repo, commit=commit,
                cursor_from=start, cursor_to=target,
                discovered_at="2000-01-01T00:00:00Z", mappings=mappings, transitive_rules=rules,
            )
            if probe is None:
                continue
            event = observations.get((repository, commit))
            if event is None:
                gaps.append({"source": source, "commit": commit, "reason": "mapped-commit-has-no-event"})
            else:
                verify_event_against_git(event, source, repo, mappings, rules)
        for (event_repository, event_commit), event in observations.items():
            if event_repository != repository:
                continue
            if event_commit not in scanned_commits:
                if incremental_from is not None and source in incremental_from:
                    continue
                gaps.append({
                    "source": source,
                    "commit": event_commit,
                    "reason": "observation-outside-reconciled-cursor",
                })
                continue
            verify_event_against_git(event, source, repo, mappings, rules)
        if requested_target is not None:
            require_commit(repo, requested_target, f"requested {source} target")
            require_ancestor(repo, target, requested_target, f"requested {source} target")
            if source == "vellum" and target != requested_target:
                verify_vellum_observatory_tail(repo, target, requested_target)
            for commit in commits_between(repo, target, requested_target):
                probe = observation_for_commit(
                    source=source, repository=repository, repo=repo, commit=commit,
                    cursor_from=target, cursor_to=requested_target,
                    discovered_at="2000-01-01T00:00:00Z", mappings=mappings,
                    transitive_rules=rules,
                )
                if probe is not None:
                    gaps.append({
                        "source": source,
                        "commit": commit,
                        "reason": "mapped-commit-after-reconciled-cursor",
                    })
    return gaps


def _json_at_commit(repo: Path, commit: str, path: Path) -> dict[str, object]:
    try:
        value = json.loads(git(repo, "show", f"{commit}:{path.as_posix()}"))
    except json.JSONDecodeError as error:
        raise ObservatoryError(f"{commit}:{path} is not JSON") from error
    if not isinstance(value, dict):
        raise ObservatoryError(f"{commit}:{path} must be an object")
    return value


def _is_exact_authority_reconciliation(
    repo: Path, before: str, commit: str, entries: list[dict[str, object]]
) -> bool:
    expected_paths = {
        "provenance/ownership-map.yaml",
        "provenance/pulp-extraction.json",
        LOCK_PATH.as_posix(),
        MAP_PATH.as_posix(),
        CURSOR_PATH.as_posix(),
        REPORT_JSON_PATH.as_posix(),
        REPORT_MD_PATH.as_posix(),
    }
    if {
        str(entry.get("path"))
        for entry in entries
        if entry.get("status") == "M" and "old_path" not in entry
    } != expected_paths or len(entries) != len(expected_paths):
        return False
    old_lock = _json_at_commit(repo, before, LOCK_PATH)
    lock = _json_at_commit(repo, commit, LOCK_PATH)
    legacy = _json_at_commit(repo, commit, MAP_PATH)
    cursor = _json_at_commit(repo, commit, CURSOR_PATH)
    extraction = _json_at_commit(repo, commit, Path("provenance/pulp-extraction.json"))
    report = _json_at_commit(repo, commit, REPORT_JSON_PATH)
    coordinates = (
        "vellum_authority_start_commit",
        "vellum_authority_record_commit",
        "pulp_activation_commit",
    )
    extraction_authority = extraction.get("authority")
    if not isinstance(extraction_authority, dict):
        return False
    if (
        old_lock.get("state") != "prepared"
        or any(old_lock.get(field) is not None for field in coordinates)
        or old_lock.get("ownership_schema_version") != 1
        or old_lock.get("transfer_plan") != "../authority/transfer-plan.v1.json"
        or lock.get("state") != "active"
        or lock.get("vellum_authority_record_commit") != before
        or any(
            not isinstance(lock.get(field), str)
            or not SHA_RE.fullmatch(str(lock[field]))
            for field in coordinates
        )
        or lock.get("ownership_schema_version") != 2
        or lock.get("transfer_plan") != "../authority/transfer-plan.v2.json"
        or cursor.get("state") != "active"
        or cursor.get("vellum", {}).get("last_scanned_commit") != before
        or cursor.get("pulp", {}).get("last_scanned_commit")
        != lock.get("pulp_activation_commit")
        or not isinstance(cursor.get("pulp", {}).get("last_dispatch_event"), str)
        or cursor.get("pulp", {}).get("last_dispatch_event")
        != extraction_authority.get("pulp_authority_event_id")
        or legacy.get("state") != "active"
        or extraction.get("status") != "active"
        or extraction_authority.get("state") != "active"
        or extraction_authority.get("ownership_start_commit")
        != lock.get("vellum_authority_start_commit")
        or extraction_authority.get("authority_record_commit") != before
        or extraction_authority.get("pulp_activation_commit")
        != lock.get("pulp_activation_commit")
        or report.get("state") != "active"
        or report.get("health") != "pass"
        or report.get("activation_blockers") != []
        or report.get("coverage_gaps") != []
    ):
        return False
    ownership = git(
        repo, "show", f"{commit}:provenance/ownership-map.yaml"
    )
    return (
        ownership.startswith("schema_version: 2\n")
        and "\n  state: active\n" in ownership
        and f"\n  vellum_authority_record_commit: {before}\n" in ownership
        and f"\n  vellum_authority_start_commit: {lock.get('vellum_authority_start_commit')}\n"
        in ownership
        and f"\n  pulp_activation_commit: {lock.get('pulp_activation_commit')}\n"
        in ownership
    )


def verify_vellum_observatory_tail(repo: Path, cursor: str, target: str) -> None:
    """Prove that commits after the reconciled source cursor contain evidence only."""
    require_commit(repo, cursor, "cursor.vellum.last_scanned_commit")
    require_commit(repo, target, "requested vellum target")
    require_ancestor(repo, cursor, target, "requested vellum target")
    allowed_modified = {
        CURSOR_PATH.as_posix(),
        REPORT_JSON_PATH.as_posix(),
        REPORT_MD_PATH.as_posix(),
    }
    events_prefix = EVENTS_PATH.as_posix() + "/"
    previous = cursor
    for commit in commits_between(repo, cursor, target):
        parents = git(repo, "rev-list", "--parents", "-n", "1", commit).split()
        if (
            len(parents) == 3
            and previous in parents[1:]
            and git(repo, "rev-parse", f"{commit}^{{tree}}")
            == git(repo, "rev-parse", f"{previous}^{{tree}}")
        ):
            previous = commit
            continue
        if len(parents) != 2:
            raise ObservatoryError(
                f"Vellum observatory evidence tail must be linear: {commit}"
            )
        if parents[1] != previous:
            raise ObservatoryError(
                f"Vellum observatory evidence tail is discontinuous: {commit}"
            )
        entries = parse_diff_entries(repo, commit)
        if _is_exact_authority_reconciliation(repo, previous, commit, entries):
            previous = commit
            continue
        for entry in entries:
            status = str(entry["status"])
            path = str(entry["path"])
            if "old_path" in entry:
                raise ObservatoryError(
                    f"Vellum observatory evidence tail forbids rename/copy at {commit}: "
                    f"{entry['old_path']} -> {path}"
                )
            if path in allowed_modified and status == "M":
                continue
            relative_event = path[len(events_prefix):] if path.startswith(events_prefix) else ""
            if (
                status == "A"
                and relative_event
                and "/" not in relative_event
                and relative_event.endswith(".yaml")
            ):
                continue
            raise ObservatoryError(
                f"Vellum observatory evidence tail contains non-evidence change at "
                f"{commit}: {status} {path}"
            )
        previous = commit


def verify_append_only(
    root: Path,
    git_base: str | None,
    *,
    pulp_repo: Path | None = None,
    vellum_repo: Path | None = None,
) -> None:
    if git_base is None:
        return
    require_commit(root, git_base, "git base")
    head = git(root, "rev-parse", "HEAD")
    require_ancestor(root, git_base, head, "append-only comparison")
    commits = git(
        root, "rev-list", "--reverse", "--topo-order", f"{git_base}..{head}"
    ).splitlines()
    try:
        json.loads(git(root, "show", f"{git_base}:{CURSOR_PATH.as_posix()}"))
        base_has_cursor = True
    except ObservatoryError:
        base_has_cursor = False
    for commit in commits:
        parents = git(root, "rev-list", "--parents", "-n", "1", commit).split()[1:]
        for parent in parents:
            # Inspect every tree edge, including each merge parent. A newly
            # appended event may resemble an old one, so disable similarity
            # detection; a real rename remains visible as a forbidden deletion.
            output = git(
                root,
                "diff",
                "--name-status",
                "--no-renames",
                parent,
                commit,
                "--",
                EVENTS_PATH.as_posix(),
            )
            bad = [
                line
                for line in output.splitlines()
                if line and not line.startswith("A\t")
            ]
            if bad:
                raise ObservatoryError(
                    "observatory events are append-only; "
                    f"commit {commit} modified/deleted/renamed events: "
                    + "; ".join(bad)
                )
            try:
                previous = json.loads(
                    git(root, "show", f"{parent}:{CURSOR_PATH.as_posix()}")
                )
                current = json.loads(
                    git(root, "show", f"{commit}:{CURSOR_PATH.as_posix()}")
                )
            except ObservatoryError:
                if base_has_cursor:
                    raise ObservatoryError(
                        f"observatory cursor must exist on every history edge: "
                        f"{parent} -> {commit}"
                    )
                continue
            for source in ("pulp", "vellum"):
                before = previous[source]["last_scanned_commit"]
                after = current[source]["last_scanned_commit"]
                if before == after:
                    continue
                repo = pulp_repo if source == "pulp" else (vellum_repo or root)
                if repo is None:
                    raise ObservatoryError(
                        f"cannot verify cursor.{source} monotonicity without its source repository"
                    )
                require_ancestor(
                    repo,
                    before,
                    after,
                    f"cursor.{source} cannot move backward at {commit}",
                )


def verify(
    *, root: Path, pulp_repo: Path | None, vellum_repo: Path | None, git_base: str | None,
    allow_missing_source_repositories: bool = False,
    pulp_target: str | None = None, vellum_target: str | None = None
) -> dict[str, object]:
    lock, mapping, cursor, budgets = validate_lock_map_cursor(root)
    verify_active_cursor_ancestry(lock, cursor, pulp_repo, vellum_repo)
    events = load_events(root)
    verify_append_only(
        root,
        git_base,
        pulp_repo=pulp_repo,
        vellum_repo=vellum_repo,
    )
    gaps = coverage_gaps(
        root=root, mapping=mapping, cursor=cursor, events=events,
        pulp_repo=pulp_repo, vellum_repo=vellum_repo,
        pulp_target=pulp_target, vellum_target=vellum_target,
    )
    if allow_missing_source_repositories:
        gaps = [gap for gap in gaps if gap["reason"] != "source-repository-not-supplied"]
    now = parse_utc(cursor["reconciled_at"], "cursor.reconciled_at") if cursor.get("reconciled_at") else None
    report = build_report(root=root, lock=lock, cursor=cursor, events=events, budgets=budgets, now=now, coverage_gaps=gaps)
    committed_json = load_json(root / REPORT_JSON_PATH)
    if committed_json != report:
        raise ObservatoryError("reports/current.json is stale; run observatory reconcile")
    committed_md = (root / REPORT_MD_PATH).read_text(encoding="utf-8")
    if committed_md != render_markdown(report):
        raise ObservatoryError("reports/current.md is stale; run observatory reconcile")
    if report["health"] != "pass":
        raise ObservatoryError("observatory health budgets failed: " + ", ".join(report["budget_violations"]))
    return report


def reconcile(
    *, root: Path, pulp_repo: Path, vellum_repo: Path, pulp_target: str, vellum_target: str,
    now_text: str, write: bool
) -> dict[str, object]:
    now = parse_utc(now_text, "--now")
    lock, mapping, cursor, budgets = validate_lock_map_cursor(root)
    verify_active_cursor_ancestry(lock, cursor, pulp_repo, vellum_repo)
    mappings = mapping["mappings"]
    rules = mapping.get("transitive_path_rules", [])
    assert isinstance(mappings, list) and isinstance(rules, list)
    current_mapping_fingerprint = mapping_fingerprint(mapping)
    mapping_changed = cursor.get("mapping_sha256") != current_mapping_fingerprint
    existing = load_events(root)
    previous_cursor = {
        source: str(cursor[source]["last_scanned_commit"])
        for source in ("pulp", "vellum")
    }
    observations = {
        (str(event["source_repository"]), str(event["source_commit"])): event
        for _, event in existing if event["kind"] == "observation"
    }
    new_events: list[dict[str, object]] = []
    for source, repo, target in (("pulp", pulp_repo, pulp_target), ("vellum", vellum_repo, vellum_target)):
        source_cursor = cursor[source]
        start = str(
            source_cursor["scan_base_commit"]
            if mapping_changed
            else source_cursor["last_scanned_commit"]
        )
        repository = str(source_cursor["repository"])
        for event in expected_observations(
            source=source, repo=repo, repository=repository, start=start, target=target,
            discovered_at=now_text, mappings=mappings, transitive_rules=rules,
        ):
            key = (repository, str(event["source_commit"]))
            if key in observations:
                verify_event_against_git(observations[key], source, repo, mappings, rules)
            else:
                new_events.append(event)
        source_cursor["last_scanned_commit"] = target
    cursor["reconciled_at"] = now_text
    cursor["mapping_sha256"] = current_mapping_fingerprint
    combined = existing + [(root / EVENTS_PATH / f"{event['event_id']}.yaml", event) for event in new_events]
    gaps = coverage_gaps(
        root=root,
        mapping=mapping,
        cursor=cursor,
        events=combined,
        pulp_repo=pulp_repo,
        vellum_repo=vellum_repo,
        incremental_from=None if mapping_changed else previous_cursor,
    )
    report = build_report(root=root, lock=lock, cursor=cursor, events=combined, budgets=budgets, now=now, coverage_gaps=gaps)
    if write:
        for event in new_events:
            path = root / EVENTS_PATH / f"{event['event_id']}.yaml"
            if path.exists():
                raise ObservatoryError(f"refusing to overwrite append-only event: {path}")
            write_json_atomic(path, event)
        write_json_atomic(root / CURSOR_PATH, cursor)
        write_json_atomic(root / REPORT_JSON_PATH, report)
        write_text_atomic(root / REPORT_MD_PATH, render_markdown(report))
    return {"new_events": [event["event_id"] for event in new_events], "cursor": cursor, "report": report}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--pulp-repo", type=Path)
    verify_parser.add_argument("--vellum-repo", type=Path)
    verify_parser.add_argument("--git-base")
    verify_parser.add_argument("--pulp-target")
    verify_parser.add_argument("--vellum-target")
    verify_parser.add_argument("--allow-missing-source-repositories", action="store_true")
    verify_parser.add_argument("--output", type=Path)
    reconcile_parser = subparsers.add_parser("reconcile")
    reconcile_parser.add_argument("--pulp-repo", type=Path, required=True)
    reconcile_parser.add_argument("--vellum-repo", type=Path, required=True)
    reconcile_parser.add_argument("--pulp-target", required=True)
    reconcile_parser.add_argument("--vellum-target", required=True)
    reconcile_parser.add_argument("--now", required=True)
    reconcile_parser.add_argument("--write", action="store_true")
    reconcile_parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve()
        if args.command == "verify":
            result = verify(
                root=root,
                pulp_repo=args.pulp_repo.resolve() if args.pulp_repo else None,
                vellum_repo=args.vellum_repo.resolve() if args.vellum_repo else None,
                git_base=args.git_base,
                allow_missing_source_repositories=args.allow_missing_source_repositories,
                pulp_target=args.pulp_target,
                vellum_target=args.vellum_target,
            )
        else:
            result = reconcile(
                root=root,
                pulp_repo=args.pulp_repo.resolve(),
                vellum_repo=args.vellum_repo.resolve(),
                pulp_target=args.pulp_target,
                vellum_target=args.vellum_target,
                now_text=args.now,
                write=args.write,
            )
        output = json.dumps(result, indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output, encoding="utf-8")
        print(output, end="")
        return 0
    except (ObservatoryError, OSError, ValueError) as error:
        print(f"observatory: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
