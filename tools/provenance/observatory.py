#!/usr/bin/env python3
"""Reconcile and verify the append-only Pulp/Vellum change observatory.

The CLI owns orchestration and cross-record invariants. Event schemas, Git
discovery, reporting, and shared persistence primitives live in focused
modules and remain re-exported here for compatibility with existing callers.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import subprocess
import sys

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parent))

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
        integration_merge_covered,
        merge_observable_paths,
    )
    from .observatory_report import (
        activation_blockers,
        build_report,
        render_markdown,
    )
    from .observatory_native import (
        auto_resolution,
        invalid_auto_resolutions,
        load_native_paths,
        stale_entries,
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
        integration_merge_covered,
        merge_observable_paths,
    )
    from observatory_report import (
        activation_blockers,
        build_report,
        render_markdown,
    )
    from observatory_native import (
        auto_resolution,
        invalid_auto_resolutions,
        load_native_paths,
        stale_entries,
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


def coverage_tail_start(repo: Path, cursor: str, target: str) -> str:
    """Where the coverage tail begins: the cursor, or the merge base when the
    target does not yet contain the cursor (a pull request behind main)."""
    if is_ancestor(repo, cursor, target):
        return cursor
    base = git(repo, "merge-base", cursor, target)
    if not base:
        raise ObservatoryError(f"requested vellum target shares no history with the cursor: {target}")
    return base


VELLUM_TAIL_POLICIES = frozenset({"reconciled", "coverage"})
# Budgets that only the passage of time can break: an event is overdue, or the
# oldest pending event is old, whatever the change under test does. A pull
# request or queue entry cannot fix them by re-running or by any change of its
# own, so `--time-budgets report` reports them instead of failing; pushes to
# main and release tags keep enforcing them.
TIME_BUDGET_VIOLATIONS = frozenset({
    "overdue-event-count-exceeded",
    "pending-event-age-exceeded",
})
TIME_BUDGET_MODES = frozenset({"enforce", "report"})
MIRROR_RECEIPT_INDEX = Path("provenance/mirror/receipts/index.json")


def coverage_gaps(
    *, root: Path, mapping: dict[str, object], cursor: dict[str, object], events: list[tuple[Path, dict[str, object]]],
    pulp_repo: Path | None, vellum_repo: Path | None,
    pulp_target: str | None = None, vellum_target: str | None = None,
    incremental_from: dict[str, str] | None = None,
    vellum_tail_policy: str = "reconciled",
    vellum_pr_main: str | None = None,
) -> list[dict[str, str]]:
    if vellum_tail_policy not in VELLUM_TAIL_POLICIES:
        raise ObservatoryError(
            f"vellum_tail_policy must be one of {sorted(VELLUM_TAIL_POLICIES)}"
        )
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
                if source == "vellum" and integration_merge_covered(repo, commit, mappings, rules):
                    # An integration merge that adds nothing beyond its two
                    # sides is covered by their own events; reconcile records
                    # none for it (see reconcile).
                    continue
                gaps.append({"source": source, "commit": commit, "reason": "mapped-commit-has-no-event"})
            else:
                verify_event_against_git(event, source, repo, mappings, rules)
        # Under the coverage policy a pull request may carry events for its own
        # commits ahead of the cursor; those are verified below rather than
        # reported as outside the reconciled range.
        tail_covered_commits: set[str] = set()
        tail_start = target
        if (
            source == "vellum"
            and vellum_tail_policy == "coverage"
            and requested_target is not None
            and target != requested_target
        ):
            require_commit(repo, requested_target, f"requested {source} target")
            if vellum_pr_main is not None:
                # The merge-base guard proves the checked-out PR/queue target
                # contains current main. Option A deliberately treats mapped
                # landings beyond the cursor as normal lag; demanding an
                # evidence-only tail here would serialize every PR.
                require_commit(repo, vellum_pr_main, "current main head")
                require_ancestor(repo, vellum_pr_main, requested_target,
                                 "pull-request target contains current main")
            # A pull request behind main is judged on its own commits: the
            # part of main it lacks is verified by main's own runs.
            tail_start = coverage_tail_start(repo, target, requested_target)
            tail_covered_commits = set(commits_between(repo, tail_start, requested_target))
        for (event_repository, event_commit), event in observations.items():
            if event_repository != repository:
                continue
            if event_commit in tail_covered_commits:
                verify_event_against_git(event, source, repo, mappings, rules)
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
            if tail_start == target:
                require_ancestor(repo, target, requested_target, f"requested {source} target")
            require_event_coverage = tail_start != target
            if source == "vellum" and target != requested_target and not require_event_coverage:
                if vellum_tail_policy == "reconciled":
                    verify_vellum_observatory_tail(repo, target, requested_target)
                else:
                    # Coverage policy: an exactly reconciled evidence tail still
                    # passes; otherwise every mapped commit ahead of the cursor
                    # must carry its own event in this tree, and a merge that
                    # adds nothing beyond the pull request it lands is covered
                    # by that pull request's events. The cursor itself advances
                    # after the merge, by the receiver.
                    try:
                        verify_vellum_observatory_tail(repo, target, requested_target)
                    except ObservatoryError:
                        require_event_coverage = True
            # Only Vellum imports Pulp's mirror history; a Pulp range has none.
            imported = (
                imported_mirror_history(repo, tail_start, requested_target, pulp_repo)
                if source == "vellum" else set()
            )
            for commit in commits_between(repo, tail_start, requested_target):
                if commit in imported:
                    # Imported Pulp mirror history is not Vellum-authored; its
                    # content reaches the mainline only through the join below.
                    continue
                parents = git(repo, "rev-list", "--parents", "-n", "1", commit).split()[1:]
                imported_parents = [parent for parent in parents if parent in imported]
                if imported_parents:
                    # The join of an imported history has no merge base with
                    # the mainline. It must leave the observatory untouched,
                    # and its first-parent diff, observed below, carries every
                    # path the import adds.
                    verify_mirror_join(repo, commit, parents, imported_parents)
                elif require_event_coverage and integration_merge_covered(
                    repo, commit, mappings, rules
                ):
                    continue
                probe = observation_for_commit(
                    source=source, repository=repository, repo=repo, commit=commit,
                    cursor_from=tail_start, cursor_to=requested_target,
                    discovered_at="2000-01-01T00:00:00Z", mappings=mappings,
                    transitive_rules=rules,
                )
                if probe is None:
                    # A merge whose diff against its first parent shows no
                    # mapped change can still drop or override the other side's
                    # change. No event can describe it (observations derive from
                    # the first-parent diff), so it must become an explicit
                    # commit on the branch, which does get an event.
                    if require_event_coverage and not imported_parents and merge_observable_paths(
                        repo, commit, mappings, rules
                    ):
                        gaps.append({
                            "source": source,
                            "commit": commit,
                            "reason": "merge-changes-other-side-without-first-parent-diff",
                        })
                    continue
                if require_event_coverage and (repository, commit) in observations:
                    continue
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


# The observatory evidence an evidence-only change may touch: these three
# files, modified, plus event files added directly under EVENTS_PATH. It is
# the one definition for the evidence-tail check and the own-commit event
# check, and the observatory receiver's "Prepare the evidence-only
# coalescing PR" step holds the same list (test_observatory pins the two).
EVIDENCE_MODIFIED_PATHS = frozenset({
    CURSOR_PATH.as_posix(),
    REPORT_JSON_PATH.as_posix(),
    REPORT_MD_PATH.as_posix(),
})


def is_event_file(path: str) -> bool:
    prefix = EVENTS_PATH.as_posix() + "/"
    relative = path[len(prefix):] if path.startswith(prefix) else ""
    return bool(relative) and "/" not in relative and relative.endswith(".yaml")


def verify_vellum_observatory_tail(repo: Path, cursor: str, target: str) -> None:
    """Prove that commits after the reconciled source cursor contain evidence only."""
    require_commit(repo, cursor, "cursor.vellum.last_scanned_commit")
    require_commit(repo, target, "requested vellum target")
    require_ancestor(repo, cursor, target, "requested vellum target")
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
            if path in EVIDENCE_MODIFIED_PATHS and status == "M":
                continue
            if status == "A" and is_event_file(path):
                continue
            raise ObservatoryError(
                f"Vellum observatory evidence tail contains non-evidence change at "
                f"{commit}: {status} {path}"
            )
        previous = commit


def verify_vellum_observatory_pr_tail(
    repo: Path, cursor: str, main_head: str, target: str
) -> None:
    """Check the observed main tail and require the PR target to contain main."""
    require_commit(repo, main_head, "current main head")
    require_commit(repo, target, "requested vellum target")
    require_ancestor(repo, main_head, target, "pull-request target contains current main")
    merge_base = git(repo, "merge-base", main_head, target)
    verify_vellum_observatory_tail(repo, cursor, merge_base)


MIRROR_IMPORT_ENTRY = re.compile(r"040000 tree [0-9a-f]{40}(?:[0-9a-f]{24})?\tmirror")


def mirror_only_tree(root: Path, commit: str) -> tuple[bool, list[str]]:
    """Whether `commit`'s root tree is exactly one `mirror` directory.

    Compares the full `ls-tree` entry, so a `mirror` that is a symlink, a
    file or a gitlink is refused, not only a second name. Returns the verdict
    and the entries, for the error message.
    """
    entries = git(root, "ls-tree", commit).splitlines()
    return len(entries) == 1 and MIRROR_IMPORT_ENTRY.fullmatch(entries[0]) is not None, entries


def approved_mirror_shas(root: Path, pulp_repo: Path | None) -> tuple[set[str], list[str]]:
    """Mirror tips named by receipts the mirror-export verifier accepts.

    The receipts are verified against Pulp itself, so the approval never
    depends on another step having checked their Pulp identity first.
    Returns the approved tips and, when verification fails or Pulp is not
    supplied, the errors; such a verification approves nothing.
    """
    if pulp_repo is None:
        return set(), [
            "the receipts' Pulp identity cannot be proved without the Pulp repository"
        ]
    import importlib.util

    module_path = Path(__file__).with_name("verify_mirror_export.py")
    spec = importlib.util.spec_from_file_location("observatory_mirror_export", module_path)
    if spec is None or spec.loader is None:
        raise ObservatoryError(f"cannot load {module_path.name}")
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    # The mirror-export verifier reads the working tree, so the approved set
    # must come from receipts that match the commit being verified.
    if git(root, "status", "--porcelain", "--", "provenance/mirror"):
        raise ObservatoryError(
            "provenance/mirror has uncommitted changes; imported mirror history "
            "is approved only from committed receipts"
        )
    report = verifier.verify(root, pulp_repo)
    if report["status"] != "pass":
        return set(), [str(error) for error in report["errors"]]
    shas: set[str] = set()
    for receipt_path in report["receipts"]:
        receipt = json.loads((root / receipt_path).read_text(encoding="utf-8"))
        shas.add(str(receipt["mirror_sha"]))
    return shas, []


def imported_mirror_commits(
    root: Path, git_base: str, head: str, pulp_repo: Path | None
) -> tuple[set[str], list[str]]:
    """Commits of an imported Pulp mirror history that the append-only check skips.

    A side history qualifies only when its tip is the mirror_sha of an accepted
    export receipt and every one of its commits has a tree holding nothing but
    the mirror directory. Its commits may descend only from each other or from
    the history of an earlier import already landed at the base: a re-export's
    git-filter-repo history continues the previous export's, so its first new
    commit's parent is that export's mirror commit. Anything else stays on the
    ordinary checks, which reject a history edge that lacks the observatory
    cursor.
    """
    range_commits = set(git(root, "rev-list", f"{git_base}..{head}").splitlines())
    approved, verification_errors = approved_mirror_shas(root, pulp_repo)
    # The verifier approves tips reachable from the checkout's HEAD, which
    # need not be `head`; only a tip already in the base is a landed import.
    landed_tips = [
        tip for tip in sorted(approved - range_commits)
        if is_ancestor(root, tip, git_base)
    ]

    def in_landed_import(commit: str) -> bool:
        if not mirror_only_tree(root, commit)[0]:
            return False
        return any(is_ancestor(root, commit, tip) for tip in landed_tips)

    imported: set[str] = set()
    for tip in sorted(approved & range_commits):
        side = git(root, "rev-list", tip, f"^{git_base}").splitlines()
        side_set = set(side)
        for commit in side:
            mirror_only, entries = mirror_only_tree(root, commit)
            if not mirror_only:
                raise ObservatoryError(
                    f"imported mirror history commit {commit} holds paths other than "
                    f"mirror/: {'; '.join(entries) or '(empty tree)'}"
                )
            parents = git(root, "rev-list", "--parents", "-n", "1", commit).split()[1:]
            outside = [
                parent for parent in parents
                if parent not in side_set and not in_landed_import(parent)
            ]
            if outside:
                raise ObservatoryError(
                    f"imported mirror history commit {commit} descends from "
                    f"{', '.join(outside)}, outside the imported history"
                )
        imported.update(side)
    return imported, verification_errors


def imported_mirror_history(
    root: Path, start: str, target: str, pulp_repo: Path | None
) -> set[str]:
    """Imported mirror commits in start..target, or none when it has no root.

    Only a range containing a root commit can import an unrelated history, so
    the receipt verification runs only then. Every such root must belong to an
    approved import; any other unrelated history is refused.
    """
    roots = git(root, "rev-list", "--max-parents=0", f"{start}..{target}").split()
    if not roots:
        # A re-export continues an import already landed, so its history has
        # no root of its own. Its commits carry no observatory cursor; only
        # then is the receipt verification worth running.
        commits = git(root, "rev-list", f"{start}..{target}").split()
        cursorless = [commit for commit in commits if not has_cursor(root, commit)]
        if not cursorless:
            return set()
        imported, errors = imported_mirror_commits(root, start, target, pulp_repo)
        if errors and (
            lands_mirror_receipts(root, target)
            or any(mirror_only_tree(root, commit)[0] for commit in cursorless)
        ):
            # A commit without the cursor can enter only as part of an approved
            # import, and nothing was approved: refuse rather than walk it as
            # ordinary history, whether or not its tree looks like a mirror.
            # A repository with no landed receipts has no import to approve,
            # so the same errors are not a contradiction there.
            raise ObservatoryError(
                f"{start}..{target} has commits without the observatory cursor "
                f"and no approved imported mirror history; mirror-export "
                f"verification failed: {'; '.join(errors)}"
            )
        return imported
    imported, errors = imported_mirror_commits(root, start, target, pulp_repo)
    unexplained = [commit for commit in roots if commit not in imported]
    if unexplained:
        detail = f"; mirror-export verification failed: {'; '.join(errors)}" if errors else ""
        raise ObservatoryError(
            f"{start}..{target} joins unrelated history rooted at {', '.join(unexplained)} "
            f"that is not an approved imported mirror history{detail}"
        )
    return imported


def lands_mirror_receipts(root: Path, commit: str) -> bool:
    """Whether `commit`'s tree lists at least one landed mirror-export receipt."""
    try:
        index = json.loads(git(root, "show", f"{commit}:{MIRROR_RECEIPT_INDEX.as_posix()}"))
    except (ObservatoryError, ValueError):
        return False
    return isinstance(index, dict) and bool(index.get("receipts"))


def has_cursor(root: Path, commit: str) -> bool:
    try:
        git(root, "cat-file", "-e", f"{commit}:{CURSOR_PATH.as_posix()}")
    except ObservatoryError:
        return False
    return True


def verify_mirror_join(
    root: Path, commit: str, parents: list[str], imported_parents: list[str]
) -> None:
    """A merge joining an imported mirror history must not touch the observatory."""
    mainline = [parent for parent in parents if parent not in imported_parents]
    if len(parents) != 2 or mainline != parents[:1]:
        raise ObservatoryError(
            f"commit {commit} joins an imported mirror history but is not a "
            "two-parent merge whose first parent is the mainline"
        )
    observatory = OBSERVATORY.as_posix()
    if not has_cursor(root, commit) or git(
        root, "rev-parse", f"{mainline[0]}:{observatory}"
    ) != git(root, "rev-parse", f"{commit}:{observatory}"):
        raise ObservatoryError(
            f"merge {commit} joining an imported mirror history changed {observatory}/"
        )


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
    # Only a range with an edge lacking the cursor can contain an imported
    # mirror history, so the receipt verification runs only then.
    imported_cache: list[set[str]] = []
    verification_errors: list[str] = []

    def imported_for(commit: str, parents: list[str]) -> set[str]:
        if not base_has_cursor:
            return set()
        if imported_cache:
            return imported_cache[0]
        if all(has_cursor(root, sha) for sha in (commit, *parents)):
            return set()
        imported, errors = imported_mirror_commits(root, git_base, head, pulp_repo)
        imported_cache.append(imported)
        verification_errors.extend(errors)
        return imported

    for commit in commits:
        parents = git(root, "rev-list", "--parents", "-n", "1", commit).split()[1:]
        imported = imported_for(commit, parents)
        if commit in imported:
            continue
        imported_parents = [parent for parent in parents if parent in imported]
        if imported_parents:
            verify_mirror_join(root, commit, parents, imported_parents)
        for parent in parents:
            if parent in imported:
                # verify_mirror_join proved this edge leaves the observatory
                # exactly as the mainline parent had it.
                continue
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
                    detail = (
                        "; no imported mirror history was approved because "
                        "mirror-export verification failed: "
                        + "; ".join(verification_errors)
                        if verification_errors
                        else ""
                    )
                    raise ObservatoryError(
                        f"observatory cursor must exist on every history edge: "
                        f"{parent} -> {commit}{detail}"
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
                if len(parents) == 2:
                    # A merge may retain the cursor from either parent. Source
                    # histories can diverge while the repository tree merges;
                    # require the selected cursor to descend from at least one
                    # parent rather than incorrectly requiring both branches.
                    parent_cursors: list[str] = []
                    for merge_parent in parents:
                        try:
                            value = json.loads(
                                git(root, "show", f"{merge_parent}:{CURSOR_PATH.as_posix()}")
                            )
                            parent_cursors.append(str(value[source]["last_scanned_commit"]))
                        except (ObservatoryError, KeyError, TypeError, json.JSONDecodeError):
                            parent_cursors = []
                            break
                    if parent_cursors:
                        require_commit(repo, after, f"cursor.{source}.last_scanned_commit")
                        if any(is_ancestor(repo, candidate, after) for candidate in parent_cursors):
                            continue
                require_ancestor(
                    repo,
                    before,
                    after,
                    f"cursor.{source} cannot move backward at {commit}",
                )


def verify(
    *, root: Path, pulp_repo: Path | None, vellum_repo: Path | None, git_base: str | None,
    allow_missing_source_repositories: bool = False,
    pulp_target: str | None = None, vellum_target: str | None = None,
    vellum_tail_policy: str = "reconciled",
    vellum_pr_main: str | None = None,
    check_now: datetime | None = None,
    time_budgets: str = "enforce",
    allow_no_git_base: bool = False,
) -> dict[str, object]:
    """Verify the ledger; with `check_now`, judge the health budgets at that time.

    The committed report is always rebuilt at the cursor's `reconciled_at`, the
    time it describes, to prove it is not stale. Overdue events are a property
    of the present, so the CLI passes the current time as `check_now`: a ledger
    whose events fell overdue after the last reconcile fails here rather than
    passing on the clock of its last reconcile.
    """
    if git_base is None and not allow_no_git_base:
        # Without a base, append-only, the new-event time checks and the
        # own-commit rule cannot tell this change's events from its base's.
        raise ObservatoryError(
            "verify needs --git-base to tell the observations a change adds from those "
            "already on its base; pass --no-git-base only for a local check"
        )
    git_base = current_main_git_base(root, git_base)
    lock, mapping, cursor, budgets = validate_lock_map_cursor(root)
    verify_active_cursor_ancestry(lock, cursor, pulp_repo, vellum_repo)
    events = load_events(root)
    verify_append_only(
        root,
        git_base,
        pulp_repo=pulp_repo,
        vellum_repo=vellum_repo,
    )
    verify_new_event_times(
        root, git_base, events, pulp_repo=pulp_repo, vellum_repo=vellum_repo, now=check_now,
    )
    verify_new_event_sources(root, git_base, events, cursor, vellum_repo=vellum_repo,
                             allow_no_git_base=allow_no_git_base)
    gaps = coverage_gaps(
        root=root, mapping=mapping, cursor=cursor, events=events,
        pulp_repo=pulp_repo, vellum_repo=vellum_repo,
        pulp_target=pulp_target, vellum_target=vellum_target,
        vellum_tail_policy=vellum_tail_policy,
        vellum_pr_main=vellum_pr_main,
    )
    if allow_missing_source_repositories:
        gaps = [gap for gap in gaps if gap["reason"] != "source-repository-not-supplied"]
    now = parse_utc(cursor["reconciled_at"], "cursor.reconciled_at") if cursor.get("reconciled_at") else None
    native = load_native_paths(root)
    native_problems = stale_entries(native, pulp_repo, str(cursor["pulp"]["last_scanned_commit"]))
    native_problems += invalid_auto_resolutions(effective_observations(events), native)
    if native_problems:
        raise ObservatoryError("; ".join(native_problems))
    report = build_report(root=root, lock=lock, cursor=cursor, events=events, budgets=budgets, now=now, coverage_gaps=gaps)
    # The committed report describes the reconciled range. Under the coverage
    # policy a pull request may add events for commits ahead of the cursor
    # without regenerating the report (the receiver does that after the merge),
    # so the staleness comparison excludes exactly those events.
    committed_events = events
    committed_gaps = gaps
    if (
        vellum_tail_policy == "coverage"
        and vellum_repo is not None
        and vellum_target is not None
        and str(cursor["vellum"]["last_scanned_commit"]) != vellum_target
    ):
        vellum_cursor = cursor["vellum"]
        tail = set(
            commits_between(
                vellum_repo,
                coverage_tail_start(
                    vellum_repo, str(vellum_cursor["last_scanned_commit"]), vellum_target
                ),
                vellum_target,
            )
        )
        vellum_repository = str(vellum_cursor["repository"])
        tail_observation_ids = {
            str(event["event_id"])
            for _, event in events
            if event["kind"] == "observation"
            and str(event["source_repository"]) == vellum_repository
            and str(event["source_commit"]) in tail
        }
        # A resolution of an unreconciled observation belongs to the same
        # not-yet-reconciled set: the committed report cannot project it
        # without the observation it resolves.
        committed_events = [
            (path, event)
            for path, event in events
            if str(event["event_id"]) not in tail_observation_ids
            and not (
                event["kind"] == "resolution"
                and str(event["resolves"]) in tail_observation_ids
            )
        ]
        committed_gaps = [
            gap for gap in gaps
            if gap["reason"] not in (
                "mapped-commit-after-reconciled-cursor",
                "merge-changes-other-side-without-first-parent-diff",
            )
        ]
    if vellum_tail_policy == "coverage":
        # Only the receiver commits the report. A change judged under the
        # coverage policy leaves it as its base had it, and the comparison
        # leaves out every event added after the commit that last wrote it.
        receiver = refuse_report_edit(root, git_base)
        since = events_since_report(root, events, "HEAD" if receiver or git_base is None else git_base)
        if since:
            committed_events = [
                (path, event) for path, event in committed_events
                if str(event["event_id"]) not in since
            ]
    committed_expected = (
        report
        if committed_events is events and committed_gaps is gaps
        else build_report(
            root=root, lock=lock, cursor=cursor, events=committed_events,
            budgets=budgets, now=now, coverage_gaps=committed_gaps,
        )
    )
    committed_json = load_json(root / REPORT_JSON_PATH)
    if committed_json != committed_expected:
        raise ObservatoryError("reports/current.json is stale; run observatory reconcile")
    committed_md = (root / REPORT_MD_PATH).read_text(encoding="utf-8")
    if committed_md != render_markdown(committed_expected):
        raise ObservatoryError("reports/current.md is stale; run observatory reconcile")
    if check_now is not None:
        report = build_report(root=root, lock=lock, cursor=cursor, events=events, budgets=budgets,
                              now=check_now, coverage_gaps=gaps)
    if time_budgets not in TIME_BUDGET_MODES:
        raise ObservatoryError(f"unknown time-budget mode: {time_budgets}")
    violations = list(report["budget_violations"])
    if time_budgets == "report":
        fatal = [item for item in violations if item not in TIME_BUDGET_VIOLATIONS]
        reported = [item for item in violations if item in TIME_BUDGET_VIOLATIONS]
        late = late_new_events(root, git_base, events, budgets, check_now) if reported else []
        if reported and (git_base is None or late):
            # Only debt the change did not bring is reported: without a base
            # to tell old events from new ones, or when an event the change
            # adds is itself overdue or over age, the budgets are enforced.
            fatal, reported = violations, []
            if late:
                fatal.append("new-event-already-late: " + ", ".join(late))
    else:
        fatal, reported = violations, []
    if fatal:
        raise ObservatoryError(
            "observatory health budgets failed: " + ", ".join(fatal)
            + describe_coverage_gaps(gaps, cursor)
        )
    if reported:
        report = {**report, "reported_time_budget_violations": reported}
    return report


# Clock skew allowed between a commit's committer time, a discovery stamp and
# the verifying runner.
DISCOVERY_SKEW = timedelta(hours=1)


def new_event_ids(root: Path, git_base: str | None,
                  events: list[tuple[Path, dict[str, object]]]) -> set[str]:
    """IDs of the events whose files are absent from git_base."""
    if git_base is None:
        return set()
    prefix = EVENTS_PATH.as_posix()
    existing = set(git(root, "ls-tree", "-r", "--name-only", git_base, "--", prefix).splitlines())
    return {
        str(event["event_id"]) for path, event in events
        if path.resolve().relative_to(root.resolve()).as_posix() not in existing
    }


def verify_new_event_times(
    root: Path, git_base: str | None, events: list[tuple[Path, dict[str, object]]], *,
    pulp_repo: Path | None, vellum_repo: Path | None, now: datetime | None,
) -> None:
    """A new observation cannot be discovered before its commit, or in the future.

    Deadlines count from discovered_at, so a backdated stamp would make an
    event overdue (or a future one keep it from ever being) by choice.
    """
    new_ids = new_event_ids(root, git_base, events)
    repos = {"Generous-Corp/pulp": pulp_repo, "Generous-Corp/vellum": vellum_repo}
    for _, event in events:
        if event["kind"] != "observation" or str(event["event_id"]) not in new_ids:
            continue
        discovered = parse_utc(event["discovered_at"], "event.discovered_at")
        if now is not None and discovered > now + DISCOVERY_SKEW:
            raise ObservatoryError(f"{event['event_id']}: discovered_at is in the future")
        repo = repos.get(str(event["source_repository"]))
        if repo is None:
            continue
        committed = datetime.fromtimestamp(
            int(git(repo, "show", "-s", "--format=%ct", str(event["source_commit"]))),
            timezone.utc,
        )
        if discovered < committed - DISCOVERY_SKEW:
            raise ObservatoryError(
                f"{event['event_id']}: discovered_at {event['discovered_at']} precedes its "
                f"commit ({committed.isoformat()})"
            )


def is_evidence_path(path: str) -> bool:
    return path in EVIDENCE_MODIFIED_PATHS or is_event_file(path)


REPORT_PATHS = (REPORT_JSON_PATH.as_posix(), REPORT_MD_PATH.as_posix())


def events_since_report(root: Path, events: list[tuple[Path, dict[str, object]]], anchor: str = "HEAD") -> set[str]:
    """IDs of the events added after the commit that last wrote the committed report.

    The receiver writes the report; every change after it (a pull request's
    observations and resolutions, or main's landings before the next
    coalesce) adds events without rewriting it, so the report describes the
    ledger as that commit held it. The writer is looked up from `anchor`: the
    git base for any change that is not the receiver's, since such a change
    cannot edit the report and so carries its base's copy. Base history is
    main's own and the change cannot place a commit there. The receiver's
    reconciliation is anchored on itself. Outside a Git checkout nothing is
    left out.
    """
    try:
        commit = git(root, "log", "-1", "--format=%H", anchor, "--", REPORT_JSON_PATH.as_posix())
    except ObservatoryError:
        return set()
    return new_event_ids(root, commit, events) if commit else set()


def refuse_report_edit(root: Path, git_base: str | None) -> bool:
    """A change may not commit the report unless it is the receiver's reconciliation.

    Returns True for the receiver's reconciliation, False when the change
    leaves the report alone, and raises otherwise.

    The receiver is recognised by its content, as the own-commit rule does:
    every path it changes is observatory evidence, and it moves the cursor.
    Two pull requests that each rewrote the report would conflict on every
    merge of main, although their events never overlap.
    """
    if git_base is None:
        return False
    changed = [path for path in git(root, "diff", "--name-only", "--no-renames", git_base, "HEAD").splitlines() if path]
    if not any(path in REPORT_PATHS for path in changed):
        return False
    if CURSOR_PATH.as_posix() in changed and all(is_evidence_path(path) for path in changed):
        return True
    raise ObservatoryError(
        "a change may not commit provenance/pulp-observatory/reports/current.{json,md}: "
        "the receiver regenerates it after landing, and verify compares it with the events "
        "it was built from. Restore the base's copy (git checkout <base> -- "
        "provenance/pulp-observatory/reports)"
    )


def commit_exists(root: Path, commit: str) -> bool:
    return subprocess.run(
        ["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=root, capture_output=True
    ).returncode == 0


def verify_new_event_sources(
    root: Path, git_base: str | None, events: list[tuple[Path, dict[str, object]]],
    cursor: dict[str, object], *, vellum_repo: Path | None = None,
    allow_no_git_base: bool = False,
) -> None:
    """A change records Vellum observations only for its own commits.

    An observation the change adds must name a commit in git_base..HEAD.
    Commits already on the base (main) are observed by main's own
    reconciliation; a branch that records them as well collides with it on
    the next merge of main. The one exception is that reconciliation
    itself, recognised by its content rather than its branch or actor: a
    change whose diff touches only observatory evidence and whose committed
    Vellum cursor advances over the observed commit. An events-only branch
    reconcile does not move the cursor, and any code change breaks the
    evidence-only shape. The event's content is checked against Git by the
    coverage verification as before.

    The rule judges a change to the Vellum repository itself, so it applies
    only when the observed Vellum history is this checkout's.
    """
    if git_base is None:
        if allow_no_git_base:
            return
        raise ObservatoryError(
            "verify needs --git-base to tell the observations a change adds from those "
            "already on its base; pass --no-git-base only for a local check"
        )
    if vellum_repo is not None and vellum_repo.resolve() != root.resolve():
        return
    new_ids = new_event_ids(root, git_base, events)
    repository = str(cursor["vellum"]["repository"])
    added = [
        event for _, event in events
        if event["kind"] == "observation"
        and str(event["source_repository"]) == repository
        and str(event["event_id"]) in new_ids
    ]
    if not added:
        return
    head = git(root, "rev-parse", "HEAD")
    own = set(commits_between(root, git_base, head))
    foreign = [event for event in added if str(event["source_commit"]) not in own]
    if not foreign:
        return
    changed = [path for path in git(root, "diff", "--name-only", "--no-renames", git_base, head).splitlines() if path]
    advanced: set[str] = set()
    if all(is_evidence_path(path) for path in changed):
        try:
            before = json.loads(git(root, "show", f"{git_base}:{CURSOR_PATH.as_posix()}"))
            start = str(before["vellum"]["last_scanned_commit"])
        except (ObservatoryError, KeyError, TypeError, ValueError):
            start = None
        end = str(cursor["vellum"]["last_scanned_commit"])
        # The advance must move forward (start an ancestor of end, and not
        # equal) and stay on the base: an evidence-only change cannot move
        # the cursor onto a branch or foreign commit and record that.
        if (
            start is not None
            and start != end
            and commit_exists(root, start)
            and commit_exists(root, end)
            and is_ancestor(root, start, end)
            and is_ancestor(root, end, git_base)
        ):
            # Both ends must be positions main itself held: the receiver only
            # ever moves the cursor to a main tip, which lies on main's
            # first-parent line. A merged branch commit is an ancestor of main
            # but was never main's tip.
            mainline = set(git(root, "rev-list", "--first-parent", git_base).splitlines())
            if start in mainline and end in mainline:
                advanced = set(commits_between(root, start, end))
    refused = sorted(
        f"{event['event_id']} ({event['source_commit']})"
        for event in foreign if str(event["source_commit"]) not in advanced
    )
    if refused:
        raise ObservatoryError(
            "a change may add Vellum observations only for its own commits; "
            "these name commits outside git_base..HEAD that this change's "
            "evidence-only cursor advance does not cover: " + ", ".join(refused)
        )


def current_main_git_base(root: Path, git_base: str | None) -> str | None:
    """`git_base`, moved forward to the merge base of HEAD with origin/main.

    On a pull request CI passes the event's base.sha, but it checks out the
    pull request merged into the current base, and base.sha can predate it.
    Everything main gained in between would then count as this change's:
    new events, their times, their sources. The merge base of HEAD with the
    current origin/main is where this change really leaves main. The base
    only moves forward along HEAD's history. It is left alone without
    origin/main, outside a Git checkout, or when the change is already on
    main (a push to main).
    """
    if git_base is None:
        return None
    try:
        main = git(root, "rev-parse", "--verify", "--quiet", f"{VELLUM_MAIN_REF}^{{commit}}")
        head = git(root, "rev-parse", "HEAD")
        if main == head or is_ancestor(root, head, main):
            return git_base
        base = git(root, "merge-base", head, main)
        if base and base != git_base and is_ancestor(root, git_base, base):
            return base
    except ObservatoryError:
        pass
    return git_base


def late_new_events(
    root: Path, git_base: str | None, events: list[tuple[Path, dict[str, object]]],
    budgets: dict[str, int], now: datetime | None,
) -> list[str]:
    """Event IDs this change adds that are pending and already overdue or old.

    A backdated or long-unclassified event a change brings must fail that
    change, not wait for main.
    """
    if git_base is None or now is None:
        return []
    new_ids = new_event_ids(root, git_base, events)
    late = []
    for event in effective_observations(events):
        if event["disposition"] != "pending" or str(event["event_id"]) not in new_ids:
            continue
        discovered = parse_utc(event["discovered_at"], "event.discovered_at")
        age_days = (now - discovered).total_seconds() / 86400
        if now > deadline(event, budgets) or age_days > budgets["maximum_pending_event_age_days"]:
            late.append(str(event["event_id"]))
    return sorted(late)


def describe_coverage_gaps(gaps: list[dict[str, str]], cursor: dict[str, object]) -> str:
    """Name each coverage gap and the step that closes it.

    A missing event on a pull request's own commit is closed by the pull
    request recording it, not by re-running the check, which fails the same
    way every time.
    """
    if not gaps:
        return ""
    shown = 20
    lines = [f"\n  {gap['source']} {gap['commit']}: {gap['reason']}" for gap in gaps[:shown]]
    if len(gaps) > shown:
        lines.append(f"\n  ... and {len(gaps) - shown} more")
    reasons = {(gap["source"], gap["reason"]) for gap in gaps}
    if ("vellum", "mapped-commit-after-reconciled-cursor") in reasons:
        lines.append(
            "\nRe-running will not help: each commit above changes a mapped path and"
            " has no observation event. Record them on the branch and push"
            " (provenance/pulp-observatory/README.md):"
            "\n  python3 tools/provenance/observatory.py reconcile"
            " --pulp-repo <pulp-checkout> --vellum-repo ."
            f" --pulp-target {(cursor.get('pulp') or {}).get('last_scanned_commit', '<committed-pulp-cursor-sha>')}"
            " --vellum-target <exact-pr-head-sha> --now <utc-timestamp>"
            " --write --events-only"
            "\n(after `git fetch origin main`: commits already on origin/main are left to"
            " main's receiver)"
        )
    if ("pulp", "mapped-commit-after-reconciled-cursor") in reasons:
        lines.append(
            "\nA Pulp commit above has no observation event; the receiver's"
            " coalesced evidence pull request (branch"
            " automation/pulp-observatory-coalesced) records Pulp events, and"
            " this check passes once it lands and this branch merges main."
        )
    if any(reason == "merge-changes-other-side-without-first-parent-diff"
           for _, reason in reasons):
        lines.append(
            "\nA merge above changes a mapped path that its first-parent diff does"
            " not show; make that change an explicit commit on the branch."
        )
    return "".join(lines)


VELLUM_MAIN_REF = "refs/remotes/origin/main"


def resolve_vellum_main(repo: Path) -> str:
    """The commit `origin/main` names in `repo`, for an events-only reconcile.

    A branch records observations for its own commits only, so the commits
    already on main must be known. Without origin/main they are not, and
    recording every commit in range is exactly the mistake this prevents."""
    try:
        return git(repo, "rev-parse", "--verify", "--quiet", f"{VELLUM_MAIN_REF}^{{commit}}")
    except ObservatoryError:
        raise ObservatoryError(
            f"cannot resolve origin/main in {repo}: an events-only reconcile records only the"
            " branch's own commits and needs main to tell them apart. Run `git fetch origin main`,"
            " or pass --vellum-main <sha>; the receiver, which records main's commits, passes"
            " --include-main-commits"
        ) from None


def reconcile(
    *, root: Path, pulp_repo: Path, vellum_repo: Path, pulp_target: str, vellum_target: str,
    now_text: str, write: bool, events_only: bool = False, vellum_main: str | None = None,
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
    native = load_native_paths(root)
    for source, repo, target in (("pulp", pulp_repo, pulp_target), ("vellum", vellum_repo, vellum_target)):
        source_cursor = cursor[source]
        start = str(
            source_cursor["scan_base_commit"]
            if mapping_changed
            else source_cursor["last_scanned_commit"]
        )
        if events_only and source == "vellum" and not mapping_changed:
            # A pull request records observations for its own commits only;
            # when it is behind main the scan starts at the merge base.
            start = coverage_tail_start(repo, start, target)
        repository = str(source_cursor["repository"])
        if events_only and source == "vellum" and vellum_main is not None:
            require_commit(repo, vellum_main, "--vellum-main")
        for event in expected_observations(
            source=source, repo=repo, repository=repository, start=start, target=target,
            discovered_at=now_text, mappings=mappings, transitive_rules=rules,
        ):
            key = (repository, str(event["source_commit"]))
            if (
                source == "vellum" and key not in observations
                and integration_merge_covered(repo, str(event["source_commit"]), mappings, rules)
            ):
                # A Vellum integration merge that adds nothing beyond its two
                # sides needs no event of its own: coverage_gaps accepts it on
                # the sides' events, in the reconciled range and in the tail.
                # Recording one only adds a pending observation someone must
                # classify as superseded.
                continue
            if (
                events_only and source == "vellum" and vellum_main is not None
                and key not in observations
                and is_ancestor(repo, str(event["source_commit"]), vellum_main)
            ):
                # Main's commits are observed by main's own receiver; a pull
                # request that merged them records only its own commits.
                continue
            if key in observations:
                verify_event_against_git(observations[key], source, repo, mappings, rules)
            else:
                new_events.append(event)
                resolution = auto_resolution(event, native, now_text) if source == "vellum" else None
                if resolution is not None:
                    new_events.append(resolution)
        if not events_only:
            source_cursor["last_scanned_commit"] = target
    if not events_only:
        cursor["reconciled_at"] = now_text
        cursor["mapping_sha256"] = current_mapping_fingerprint
    combined = existing + [(root / EVENTS_PATH / f"{event['event_id']}.yaml", event) for event in new_events]
    if events_only:
        gaps = coverage_gaps(
            root=root,
            mapping=mapping,
            cursor=cursor,
            events=combined,
            pulp_repo=pulp_repo,
            vellum_repo=vellum_repo,
            pulp_target=pulp_target,
            vellum_target=vellum_target,
            vellum_tail_policy="coverage",
        )
    else:
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
        # A pull request records its own observations and leaves the cursor and
        # report to the post-merge receiver, so two pull requests never contend
        # for the same two files.
        if not events_only:
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
    verify_parser.add_argument("--vellum-pr-main")
    verify_parser.add_argument("--allow-missing-source-repositories", action="store_true")
    verify_parser.add_argument(
        "--vellum-tail-policy",
        choices=sorted(VELLUM_TAIL_POLICIES),
        default="reconciled",
        help=(
            "reconciled: commits after the Vellum cursor must be evidence only; "
            "coverage: they may be ordinary commits when each mapped one carries "
            "its own event (the receiver advances the cursor after the merge)"
        ),
    )
    verify_parser.add_argument(
        "--now",
        help="UTC time at which to judge overdue events (default: the current time)",
    )
    verify_parser.add_argument(
        "--time-budgets",
        choices=sorted(TIME_BUDGET_MODES),
        default="enforce",
        help=(
            "enforce: overdue and pending-age budgets fail verification; "
            "report: they are returned in the report instead (pull requests and "
            "queue entries, which cannot change them)"
        ),
    )
    verify_parser.add_argument("--output", type=Path)
    verify_parser.add_argument(
        "--no-git-base", action="store_true",
        help="local check only: verify without a git base, so the own-commit event rule cannot run",
    )
    reconcile_parser = subparsers.add_parser("reconcile")
    reconcile_parser.add_argument("--pulp-repo", type=Path, required=True)
    reconcile_parser.add_argument("--vellum-repo", type=Path, required=True)
    reconcile_parser.add_argument("--pulp-target", required=True)
    reconcile_parser.add_argument("--vellum-target", required=True)
    reconcile_parser.add_argument("--now", required=True)
    reconcile_parser.add_argument("--write", action="store_true")
    reconcile_parser.add_argument(
        "--events-only",
        action="store_true",
        help="with --write, record new observation events but leave cursor and report untouched",
    )
    reconcile_parser.add_argument(
        "--vellum-main",
        help="with --events-only, the current Vellum main commit (default: origin/main in "
        "--vellum-repo): observations of commits already on it are left to main's own receiver",
    )
    reconcile_parser.add_argument(
        "--include-main-commits",
        action="store_true",
        help="record observations of commits already on Vellum main too. The receiver passes "
        "it; a full reconcile (without --events-only) requires it",
    )
    reconcile_parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve()
        if args.command == "verify":
            if args.no_git_base and args.git_base:
                raise ObservatoryError("--no-git-base and --git-base are exclusive")
            result = verify(
                root=root,
                pulp_repo=args.pulp_repo.resolve() if args.pulp_repo else None,
                vellum_repo=args.vellum_repo.resolve() if args.vellum_repo else None,
                git_base=args.git_base,
                allow_missing_source_repositories=args.allow_missing_source_repositories,
                pulp_target=args.pulp_target,
                vellum_target=args.vellum_target,
                vellum_tail_policy=args.vellum_tail_policy,
                vellum_pr_main=args.vellum_pr_main,
                check_now=(
                    parse_utc(args.now, "--now") if args.now
                    else datetime.now(timezone.utc).replace(microsecond=0)
                ),
                time_budgets=args.time_budgets,
                allow_no_git_base=args.no_git_base,
            )
        else:
            if args.events_only and not args.write:
                raise ObservatoryError("--events-only requires --write")
            if args.vellum_main and not args.events_only:
                raise ObservatoryError("--vellum-main applies only with --events-only")
            if args.vellum_main and args.include_main_commits:
                raise ObservatoryError("--vellum-main and --include-main-commits are exclusive")
            if not args.events_only and not args.include_main_commits:
                raise ObservatoryError(
                    "a full reconcile records Vellum main's own commits and advances the cursor;"
                    " only the receiver runs it, with --include-main-commits. A branch records"
                    " its own commits with --write --events-only"
                )
            vellum_main = args.vellum_main
            if args.events_only and not args.include_main_commits and vellum_main is None:
                vellum_main = resolve_vellum_main(args.vellum_repo.resolve())
            result = reconcile(
                root=root,
                pulp_repo=args.pulp_repo.resolve(),
                vellum_repo=args.vellum_repo.resolve(),
                pulp_target=args.pulp_target,
                vellum_target=args.vellum_target,
                now_text=args.now,
                write=args.write,
                events_only=args.events_only,
                vellum_main=vellum_main,
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
