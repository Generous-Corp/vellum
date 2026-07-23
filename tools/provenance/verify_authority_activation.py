#!/usr/bin/env python3
"""Build and verify the fail-closed Vellum/Pulp authority handshake.

There are deliberately three independently bound identities:

* the preserved filtered seed proves exact Pulp source lineage; and
* a later prepared Pulp commit proves the exact activation-candidate source;
* the immutable authority-start commit proves the evolved Vellum product tree.

The candidate snapshot does not freeze Pulp. The seed must be an ancestor, not
an editable copy at the authority start. Active verification additionally
requires that the candidate source stayed unchanged through exact landed Pulp
freeze evidence and live GitHub proof of repository, branch-protection, and
check-run producers.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Protocol


ROOT = Path(__file__).resolve().parents[2]
PLAN_PATH = Path("provenance/authority/transfer-plan.v2.json")
TRUST_PATH = Path("provenance/authority/trust-policy.v1.json")
MANIFEST_PATH = Path("provenance/cut-manifest.json")
OWNERSHIP_PATH = ".github/vellum-ownership.json"
SHA_RE = re.compile(r"[0-9a-f]{40}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
REF_RE = re.compile(r"refs/heads/authority/[a-z0-9][a-z0-9._/-]{2,120}")
ALLOWED_HISTORICAL_CLASSIFICATIONS = {
    "framework-core", "authoring-only", "platform-adapter", "test-only", "unresolved"
}
RECORD_FIELDS = {
    "schema_version", "state", "source_repository", "framework_repository",
    "pulp_extraction_base", "historical_seed_commit", "pulp_candidate_commit",
    "pulp_ownership_projection_blob", "authority_start_commit",
    "authority_record_ref", "cut_manifest_sha256", "authority_groups",
    "pulp_activation", "approved_by", "approved_at",
}
GROUP_FIELDS = {
    "id", "lineage_mode", "pulp_legacy_slices",
    "pulp_historical_seed_projection", "pulp_activation_candidate_projection",
    "vellum_implementation_projection",
}
EVIDENCE_FIELDS = {
    "schema_version", "state", "pulp_activation_commit", "ownership_projection_path",
    "ownership_projection_blob", "authority_event_path", "authority_event_blob", "checks",
    "branch_protection", "retrieved_at",
}


class ActivationError(RuntimeError):
    pass


class GitHub(Protocol):
    def get(self, path: str, token: str) -> Any: ...


class LiveGitHub:
    def get(self, path: str, token: str) -> Any:
        request = urllib.request.Request(
            "https://api.github.com" + path,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": "Bearer " + token,
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                value = json.load(response)
        except (urllib.error.URLError, json.JSONDecodeError) as error:
            raise ActivationError(f"GitHub request failed for {path}: {error}") from error
        return value


def canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ActivationError(f"cannot read {path}: {error}") from error


def git(repo: Path, *args: str) -> str:
    try:
        completed = subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)
    except subprocess.CalledProcessError as error:
        detail = error.stderr.decode("utf-8", errors="replace").strip()
        raise ActivationError(f"git {' '.join(args)} failed in {repo}: {detail}") from error
    return completed.stdout.decode("utf-8", errors="strict").strip()


def require_sha(value: object, field: str) -> str:
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise ActivationError(f"{field} must be an exact 40-character SHA")
    return value


def require_commit(repo: Path, value: object, field: str) -> str:
    commit = require_sha(value, field)
    if git(repo, "rev-parse", f"{commit}^{{commit}}") != commit:
        raise ActivationError(f"{field} did not resolve exactly")
    return commit


def require_ancestor(repo: Path, ancestor: str, descendant: str, field: str) -> None:
    completed = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant], cwd=repo, capture_output=True
    )
    if completed.returncode != 0:
        raise ActivationError(f"{field}: {ancestor} is not an ancestor of {descendant}")


def json_at(repo: Path, commit: str, path: str) -> dict[str, object]:
    try:
        value = json.loads(git(repo, "show", f"{commit}:{path}"))
    except json.JSONDecodeError as error:
        raise ActivationError(f"{commit}:{path} is not JSON") from error
    if not isinstance(value, dict):
        raise ActivationError(f"{commit}:{path} must be an object")
    return value


def tree_blobs(repo: Path, commit: str) -> dict[str, dict[str, str]]:
    output = git(repo, "ls-tree", "-r", commit)
    result: dict[str, dict[str, str]] = {}
    for line in output.splitlines():
        metadata, path = line.split("\t", 1)
        mode, object_type, blob = metadata.split()
        if object_type == "blob":
            result[path] = {"blob": blob, "mode": mode}
    return result


def git_blob(repo: Path, commit: str, path: str) -> str:
    output = git(repo, "ls-tree", commit, "--", path)
    rows = output.splitlines()
    if len(rows) != 1:
        raise ActivationError(f"expected one Git object for {commit}:{path}")
    metadata, actual_path = rows[0].split("\t", 1)
    mode, object_type, blob = metadata.split()
    if actual_path != path or object_type != "blob" or mode not in {"100644", "100755"}:
        raise ActivationError(f"expected a regular blob for {commit}:{path}")
    return blob


def validate_plan(plan: object) -> dict[str, object]:
    if not isinstance(plan, dict) or plan.get("schema_version") != 2 or plan.get("state") != "prepared":
        raise ActivationError("transfer plan must be prepared schema v2")
    required = {
        "schema_version", "state", "source_repository", "framework_repository",
        "pulp_extraction_base", "historical_seed_commit", "historical_seed_manifest",
        "authority_groups", "excluded_from_transfer", "invariants",
    }
    if set(plan) != required:
        raise ActivationError("transfer plan fields differ")
    if plan.get("source_repository") != "Generous-Corp/pulp" or plan.get("framework_repository") != "Generous-Corp/vellum":
        raise ActivationError("transfer plan repositories differ")
    require_sha(plan.get("pulp_extraction_base"), "plan.pulp_extraction_base")
    require_sha(plan.get("historical_seed_commit"), "plan.historical_seed_commit")
    if plan.get("invariants") != {
        "historical_projection_may_exist_only_in_ancestry": True,
        "historical_projection_editable_at_authority_start": False,
        "pulp_legacy_copy_frozen_only_after_atomic_activation": True,
        "synchronized_editable_copies_allowed": False,
    }:
        raise ActivationError("transfer plan invariants drifted")
    groups = plan.get("authority_groups")
    if not isinstance(groups, list) or not groups:
        raise ActivationError("transfer plan needs at least one authority group")
    seen: set[str] = set()
    for group in groups:
        if not isinstance(group, dict) or set(group) != {
            "id", "lineage_mode", "pulp_legacy_slices", "vellum_implementation_paths",
            "required_vellum_checks", "required_pulp_checks",
        }:
            raise ActivationError("transfer plan authority group fields differ")
        identifier = group.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            raise ActivationError("authority group IDs must be unique")
        seen.add(identifier)
        if group.get("lineage_mode") != "history-seed-ancestor-active-reimplementation":
            raise ActivationError("only the honest seed-ancestor/reimplementation lineage is enabled")
        for field in ("pulp_legacy_slices", "vellum_implementation_paths", "required_vellum_checks", "required_pulp_checks"):
            value = group.get(field)
            if not isinstance(value, list) or not value or value != sorted(set(value)):
                raise ActivationError(f"authority group {identifier}.{field} must be sorted and unique")
    return plan


def ownership_slices(projection: dict[str, object]) -> dict[str, dict[str, object]]:
    slices = projection.get("slices")
    if not isinstance(slices, list):
        raise ActivationError("Pulp ownership projection lacks slices")
    result: dict[str, dict[str, object]] = {}
    for item in slices:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ActivationError("Pulp ownership slice is invalid")
        result[str(item["id"])] = item
    return result


def selected_slice_paths(
    *, group: dict[str, object], ownership: dict[str, object]
) -> list[str]:
    by_slice = ownership_slices(ownership)
    selected: list[str] = []
    for slice_id in group["pulp_legacy_slices"]:
        item = by_slice.get(str(slice_id))
        if item is None or item.get("state") != "pulp-authoritative-untransferred":
            raise ActivationError(f"Pulp candidate slice is absent or not authoritative: {slice_id}")
        paths = item.get("paths")
        if not isinstance(paths, list) or not paths:
            raise ActivationError(f"Pulp slice lacks exact paths: {slice_id}")
        for path in paths:
            if not isinstance(path, str) or path.endswith("/") or any(char in path for char in "*?["):
                raise ActivationError(f"Pulp slice path is not exact: {slice_id}:{path}")
            selected.append(path)
    if len(selected) != len(set(selected)):
        raise ActivationError("Pulp candidate slices contain duplicate paths")
    return sorted(selected)


def build_historical_seed_projection(
    *, paths: list[str], manifest: dict[str, object], seed_tree: dict[str, dict[str, str]]
) -> dict[str, dict[str, str]]:
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        raise ActivationError("cut manifest lacks entries")
    by_path = {entry.get("source_path"): entry for entry in entries if isinstance(entry, dict)}
    projection: dict[str, dict[str, str]] = {}
    for path in paths:
        entry = by_path.get(path)
        if not isinstance(entry, dict):
            raise ActivationError(f"Pulp candidate path is absent from historical manifest: {path}")
        classification = entry.get("classification")
        if classification not in ALLOWED_HISTORICAL_CLASSIFICATIONS:
            raise ActivationError(
                f"Pulp historical classification cannot enter the candidate projection: "
                f"{path}:{classification}"
            )
        expected = {"blob": entry.get("git_blob_sha"), "mode": entry.get("git_mode")}
        if seed_tree.get(path) != expected:
            raise ActivationError(f"historical seed does not preserve exact source blob/mode: {path}")
        projection[path] = {
            "blob": str(expected["blob"]),
            "mode": str(expected["mode"]),
            "classification": str(classification),
        }
    return dict(sorted(projection.items()))


def build_activation_candidate_projection(
    *, paths: list[str], candidate_tree: dict[str, dict[str, str]]
) -> dict[str, dict[str, str]]:
    projection: dict[str, dict[str, str]] = {}
    for path in paths:
        metadata = candidate_tree.get(path)
        if metadata is None:
            raise ActivationError(f"Pulp candidate path is absent at the candidate commit: {path}")
        if metadata.get("mode") not in {"100644", "100755"}:
            raise ActivationError(f"Pulp candidate path is not a regular file: {path}")
        projection[path] = dict(metadata)
    return dict(sorted(projection.items()))


def expand_implementation_projection(
    *, group: dict[str, object], active_tree: dict[str, dict[str, str]], historical_blob_ids: set[str]
) -> dict[str, dict[str, str]]:
    projection: dict[str, dict[str, str]] = {}
    for pattern in group["vellum_implementation_paths"]:
        matches = [path for path in active_tree if path == pattern or path.startswith(str(pattern))]
        if not matches:
            raise ActivationError(f"Vellum implementation pattern matches no tracked file: {pattern}")
        for path in matches:
            if path.startswith(("core/", "packages/pulp-import-ir/", "tools/figma-plugin/")):
                raise ActivationError(f"retired Pulp projection restored at authority start: {path}")
            metadata = active_tree[path]
            if metadata["blob"] in historical_blob_ids:
                raise ActivationError(f"historical Pulp source blob restored into active authority: {path}")
            projection[path] = dict(metadata)
    return dict(sorted(projection.items()))


def build_record(
    *, root: Path, pulp_repo: Path, pulp_ownership_commit: str, authority_start_commit: str,
    authority_record_ref: str, approved_at: str
) -> dict[str, object]:
    plan = validate_plan(load_json(root / PLAN_PATH))
    manifest = load_json(root / MANIFEST_PATH)
    if not isinstance(manifest, dict) or manifest.get("source_commit") != plan["pulp_extraction_base"]:
        raise ActivationError("cut manifest and transfer plan Pulp bases differ")
    base = require_commit(pulp_repo, plan["pulp_extraction_base"], "Pulp extraction base")
    ownership_commit = require_commit(pulp_repo, pulp_ownership_commit, "Pulp ownership commit")
    require_ancestor(pulp_repo, base, ownership_commit, "Pulp ownership lineage")
    ownership = json_at(pulp_repo, ownership_commit, OWNERSHIP_PATH)
    ownership_blob = git_blob(pulp_repo, ownership_commit, OWNERSHIP_PATH)
    seed = require_commit(root, plan["historical_seed_commit"], "Vellum history seed")
    authority = require_commit(root, authority_start_commit, "Vellum authority-start commit")
    require_ancestor(root, seed, authority, "Vellum authority lineage")
    if not REF_RE.fullmatch(authority_record_ref) or ".." in PurePosixPath(authority_record_ref).parts:
        raise ActivationError("authority record ref must be a normalized refs/heads/authority/* ref")
    approved = dt.datetime.fromisoformat(approved_at.removesuffix("Z") + "+00:00") if approved_at.endswith("Z") else None
    if approved is None or approved.utcoffset() != dt.timedelta(0):
        raise ActivationError("approved_at must be an ISO-8601 UTC timestamp")
    seed_tree = tree_blobs(root, seed)
    active_tree = tree_blobs(root, authority)
    candidate_tree = tree_blobs(pulp_repo, ownership_commit)
    historical_ids = {
        str(entry["git_blob_sha"])
        for entry in manifest["entries"]
        if isinstance(entry, dict)
        and entry.get("source_path") not in {"DEPENDENCIES.md", "LICENSE.md", "NOTICE.md"}
    }
    groups = []
    for group in plan["authority_groups"]:
        assert isinstance(group, dict)
        paths = selected_slice_paths(group=group, ownership=ownership)
        groups.append({
            "id": group["id"],
            "lineage_mode": group["lineage_mode"],
            "pulp_legacy_slices": group["pulp_legacy_slices"],
            "pulp_historical_seed_projection": build_historical_seed_projection(
                paths=paths, manifest=manifest, seed_tree=seed_tree
            ),
            "pulp_activation_candidate_projection": build_activation_candidate_projection(
                paths=paths, candidate_tree=candidate_tree
            ),
            "vellum_implementation_projection": expand_implementation_projection(
                group=group, active_tree=active_tree, historical_blob_ids=historical_ids
            ),
        })
    return {
        "schema_version": 2,
        "state": "pending-pulp-activation",
        "source_repository": plan["source_repository"],
        "framework_repository": plan["framework_repository"],
        "pulp_extraction_base": plan["pulp_extraction_base"],
        "historical_seed_commit": seed,
        "pulp_candidate_commit": ownership_commit,
        "pulp_ownership_projection_blob": ownership_blob,
        "authority_start_commit": authority,
        "authority_record_ref": authority_record_ref,
        "cut_manifest_sha256": canonical_sha256(manifest),
        "authority_groups": groups,
        "pulp_activation": None,
        "approved_by": "@danielraffel",
        "approved_at": approved_at,
    }


def validate_record_shape(record: object) -> dict[str, object]:
    if not isinstance(record, dict) or set(record) != RECORD_FIELDS:
        raise ActivationError("authority record fields differ")
    if record.get("schema_version") != 2 or record.get("state") != "pending-pulp-activation":
        raise ActivationError("authority record must be pending schema v2")
    if record.get("source_repository") != "Generous-Corp/pulp" or record.get("framework_repository") != "Generous-Corp/vellum":
        raise ActivationError("authority record repository identity differs")
    for field in (
        "pulp_extraction_base", "historical_seed_commit", "pulp_candidate_commit",
        "authority_start_commit",
    ):
        require_sha(record.get(field), f"record.{field}")
    require_sha(record.get("pulp_ownership_projection_blob"), "record.pulp_ownership_projection_blob")
    if not isinstance(record.get("cut_manifest_sha256"), str) or not SHA256_RE.fullmatch(str(record["cut_manifest_sha256"])):
        raise ActivationError("authority record cut manifest digest is invalid")
    reference = record.get("authority_record_ref")
    if not isinstance(reference, str) or not REF_RE.fullmatch(reference) or ".." in PurePosixPath(reference).parts:
        raise ActivationError("authority record ref is invalid")
    if record.get("pulp_activation") is not None:
        raise ActivationError("pending record cannot self-assert landed Pulp activation")
    if record.get("approved_by") != "@danielraffel":
        raise ActivationError("authority record requires framework-owner approval")
    approved = record.get("approved_at")
    if not isinstance(approved, str) or not approved.endswith("Z"):
        raise ActivationError("authority record approved_at must be UTC")
    groups = record.get("authority_groups")
    if not isinstance(groups, list) or not groups:
        raise ActivationError("authority record needs groups")
    for group in groups:
        if not isinstance(group, dict) or set(group) != GROUP_FIELDS:
            raise ActivationError("authority record group fields differ")
        if group.get("lineage_mode") != "history-seed-ancestor-active-reimplementation":
            raise ActivationError("authority record lineage mode is not enabled")
        for field in (
            "pulp_historical_seed_projection", "pulp_activation_candidate_projection",
            "vellum_implementation_projection",
        ):
            projection = group.get(field)
            if not isinstance(projection, dict) or not projection:
                raise ActivationError(f"authority group {field} must be non-empty")
        if set(group["pulp_historical_seed_projection"]) != set(
            group["pulp_activation_candidate_projection"]
        ):
            raise ActivationError("historical and activation-candidate path sets differ")
    return record


def verify_structural_record(
    *, root: Path, pulp_repo: Path, pulp_ownership_commit: str, record: dict[str, object]
) -> dict[str, object]:
    expected = build_record(
        root=root,
        pulp_repo=pulp_repo,
        pulp_ownership_commit=pulp_ownership_commit,
        authority_start_commit=str(record["authority_start_commit"]),
        authority_record_ref=str(record["authority_record_ref"]),
        approved_at=str(record["approved_at"]),
    )
    if expected != record:
        raise ActivationError("authority record does not match exact seed/source/implementation projections")
    return record


def verify_record_commit_layout(
    *,
    root: Path,
    record: dict[str, object],
    record_path: str,
    authority_record_commit: str,
    expected_authority_ref: str | None = None,
    require_head: bool = False,
) -> dict[str, object]:
    if (
        not record_path.startswith("provenance/authority/records/")
        or not record_path.endswith(".json")
        or ".." in PurePosixPath(record_path).parts
        or PurePosixPath(record_path).parent
        != PurePosixPath("provenance/authority/records")
    ):
        raise ActivationError(
            "authority record must be a direct JSON artifact under "
            "provenance/authority/records"
        )
    if (
        expected_authority_ref is not None
        and record["authority_record_ref"] != expected_authority_ref
    ):
        raise ActivationError(
            "authority record ref differs from the expected checkout ref"
        )
    record_commit = require_commit(
        root, authority_record_commit, "authority record commit"
    )
    if require_head and git(root, "rev-parse", "HEAD") != record_commit:
        raise ActivationError(
            "authority record verification must run at the exact record commit"
        )
    authority_start = require_commit(
        root, record["authority_start_commit"], "authority-start commit"
    )
    parents = git(root, "show", "-s", "--format=%P", record_commit).split()
    if parents != [authority_start]:
        raise ActivationError(
            "authority record must be one non-merge commit directly after "
            "the authority-start commit"
        )
    committed = json_at(root, record_commit, record_path)
    if committed != record:
        raise ActivationError(
            "authority record bytes/content differ at the exact record commit"
        )
    changed = git(
        root,
        "diff",
        "--name-status",
        authority_start,
        record_commit,
    ).splitlines()
    if changed != [f"A\t{record_path}"]:
        raise ActivationError(
            "authority record commit must add only the pending authority record"
        )
    return {
        "status": "pending-pulp-activation",
        "authority_start_commit": authority_start,
        "authority_record_commit": record_commit,
        "authority_record_ref": record["authority_record_ref"],
        "pulp_candidate_commit": record["pulp_candidate_commit"],
        "record_path": record_path,
    }


def verify_pending_record(
    *,
    root: Path,
    pulp_repo: Path,
    pulp_ownership_commit: str,
    record_path: str,
    authority_record_commit: str,
    expected_authority_ref: str | None = None,
    require_head: bool = False,
) -> dict[str, object]:
    record = validate_record_shape(load_json(root / record_path))
    verify_structural_record(
        root=root,
        pulp_repo=pulp_repo,
        pulp_ownership_commit=pulp_ownership_commit,
        record=record,
    )
    return verify_record_commit_layout(
        root=root,
        record=record,
        record_path=record_path,
        authority_record_commit=authority_record_commit,
        expected_authority_ref=expected_authority_ref,
        require_head=require_head,
    )


def validate_trust_policy(policy: object) -> dict[str, object]:
    if not isinstance(policy, dict) or policy.get("schema_version") != 1:
        raise ActivationError("trust policy must be schema v1")
    if policy.get("state") != "enabled":
        raise ActivationError("authority trust policy is not enabled")
    repositories = policy.get("repositories")
    if not isinstance(repositories, dict) or set(repositories) != {"pulp", "vellum"}:
        raise ActivationError("trust policy repositories differ")
    for key, full_name in (("pulp", "Generous-Corp/pulp"), ("vellum", "Generous-Corp/vellum")):
        repository = repositories[key]
        expected_fields = {
            "full_name", "private", "repository_id", "reader_app_id", "required_check_app_ids"
        }
        if key == "vellum":
            expected_fields.add("dispatcher_app_id")
        if not isinstance(repository, dict) or set(repository) != expected_fields:
            raise ActivationError(f"trust policy {key} fields differ")
        if repository.get("full_name") != full_name:
            raise ActivationError(f"trust policy {key} repository differs")
        if repository.get("private") is not (key == "vellum"):
            raise ActivationError(f"trust policy {key} visibility differs")
        if not isinstance(repository.get("repository_id"), int) or repository["repository_id"] <= 0:
            raise ActivationError(f"trust policy {key} repository ID is not pinned")
        if not isinstance(repository.get("reader_app_id"), int) or repository["reader_app_id"] <= 0:
            raise ActivationError(f"trust policy {key} reader App ID is not pinned")
        if key == "vellum" and (
            not isinstance(repository.get("dispatcher_app_id"), int)
            or repository["dispatcher_app_id"] <= 0
        ):
            raise ActivationError("trust policy Vellum dispatcher App ID is not pinned")
        checks = repository.get("required_check_app_ids")
        if not isinstance(checks, dict) or not checks or any(not isinstance(value, int) or value <= 0 for value in checks.values()):
            raise ActivationError(f"trust policy {key} check producers are not pinned")
    return policy


def verify_installation_scope(github: GitHub, token: str, expected_repository_id: int) -> None:
    installation = github.get("/installation/repositories?per_page=100", token)
    if not isinstance(installation, dict) or installation.get("total_count") != 1:
        raise ActivationError("reader token must be a one-repository installation token")
    repositories = installation.get("repositories")
    ids = {item.get("id") for item in repositories if isinstance(item, dict)} if isinstance(repositories, list) else set()
    if ids != {expected_repository_id}:
        raise ActivationError("reader token repository scope differs from the pinned repository")


def verify_app_identity(github: GitHub, app_jwt: str, expected_app_id: int) -> None:
    app = github.get("/app", app_jwt)
    if not isinstance(app, dict) or app.get("id") != expected_app_id:
        raise ActivationError("authority reader App identity differs from the pinned App ID")


def verify_repository(
    github: GitHub, token: str, expected: dict[str, object], *, app_jwt: str
) -> None:
    verify_app_identity(github, app_jwt, int(expected["reader_app_id"]))
    repository = github.get(f"/repos/{expected['full_name']}", token)
    if not isinstance(repository, dict) or repository.get("id") != expected["repository_id"]:
        raise ActivationError(f"repository ID mismatch: {expected['full_name']}")
    if repository.get("private") != expected["private"] or repository.get("archived") is True:
        raise ActivationError(f"repository visibility/lifecycle differs: {expected['full_name']}")
    verify_installation_scope(github, token, int(expected["repository_id"]))


def live_check_runs(github: GitHub, token: str, full_name: str, commit: str) -> list[dict[str, object]]:
    response = github.get(f"/repos/{full_name}/commits/{commit}/check-runs?per_page=100", token)
    runs = response.get("check_runs") if isinstance(response, dict) else None
    if not isinstance(runs, list):
        raise ActivationError(f"check-run response is incomplete for {full_name}@{commit}")
    return [run for run in runs if isinstance(run, dict)]


def verify_checks(
    *, github: GitHub, token: str, full_name: str, commit: str,
    expected_apps: dict[str, int], supplied: list[dict[str, object]] | None = None
) -> None:
    runs = live_check_runs(github, token, full_name, commit)
    normalized: dict[str, dict[str, object]] = {}
    for run in runs:
        app = run.get("app")
        if run.get("name") in expected_apps and isinstance(app, dict):
            normalized[str(run["name"])] = {
                "name": run.get("name"),
                "head_sha": run.get("head_sha"),
                "conclusion": run.get("conclusion"),
                "app_id": app.get("id"),
                "check_run_id": str(run.get("id")),
                "details_url": run.get("details_url"),
            }
    for name, app_id in expected_apps.items():
        run = normalized.get(name)
        if run is None or run["head_sha"] != commit or run["conclusion"] != "success" or run["app_id"] != app_id:
            raise ActivationError(f"required check is absent, unsuccessful, or from the wrong producer: {name}")
    if supplied is not None:
        if not isinstance(supplied, list) or any(not isinstance(item, dict) for item in supplied):
            raise ActivationError("supplied check evidence must be an array")
        supplied_by_name = {str(item.get("name")): item for item in supplied}
        expected_supplied = {name: normalized[name] for name in expected_apps}
        if supplied_by_name != expected_supplied:
            raise ActivationError("supplied check evidence does not match live exact-SHA check runs")


def verify_protected_ref(
    *, github: GitHub, token: str, full_name: str, ref: str, commit: str,
    expected_apps: dict[str, int]
) -> None:
    branch = ref.removeprefix("refs/heads/")
    reference = github.get(
        f"/repos/{full_name}/git/ref/heads/{urllib.parse.quote(branch, safe='/')}", token
    )
    obj = reference.get("object") if isinstance(reference, dict) else None
    if not isinstance(obj, dict) or obj.get("sha") != commit:
        raise ActivationError("authority ref does not resolve to the exact record commit")
    protection = github.get(
        f"/repos/{full_name}/branches/{urllib.parse.quote(branch, safe='')}/protection", token
    )
    required = protection.get("required_status_checks") if isinstance(protection, dict) else None
    if not isinstance(required, dict) or required.get("strict") is not True:
        raise ActivationError("authority branch does not require strict status checks")
    checks = required.get("checks")
    observed: dict[str, int | None] = {}
    if isinstance(checks, list):
        for item in checks:
            if isinstance(item, dict) and isinstance(item.get("context"), str):
                observed[str(item["context"])] = item.get("app_id") if isinstance(item.get("app_id"), int) else None
    contexts = required.get("contexts")
    if isinstance(contexts, list):
        for context in contexts:
            if isinstance(context, str):
                observed.setdefault(context, None)
    for name, app_id in expected_apps.items():
        if observed.get(name) != app_id:
            raise ActivationError(f"branch protection does not bind {name} to App {app_id}")


def verify_pending_live(
    *, root: Path, record_path: str, record: dict[str, object], authority_record_commit: str,
    github: GitHub, vellum_token: str, vellum_app_jwt: str
) -> dict[str, object]:
    policy = validate_trust_policy(load_json(root / TRUST_PATH))
    plan = validate_plan(load_json(root / PLAN_PATH))
    expected_vellum_checks = sorted({
        str(name) for group in plan["authority_groups"] for name in group["required_vellum_checks"]
    })
    expected_pulp_checks = sorted({
        str(name) for group in plan["authority_groups"] for name in group["required_pulp_checks"]
    })
    if sorted(policy["repositories"]["vellum"]["required_check_app_ids"]) != expected_vellum_checks:
        raise ActivationError("Vellum trust-policy checks differ from the transfer plan")
    if sorted(policy["repositories"]["pulp"]["required_check_app_ids"]) != expected_pulp_checks:
        raise ActivationError("Pulp trust-policy checks differ from the transfer plan")
    vellum = policy["repositories"]["vellum"]
    verify_repository(github, vellum_token, vellum, app_jwt=vellum_app_jwt)
    verify_record_commit_layout(
        root=root,
        record=record,
        record_path=record_path,
        authority_record_commit=authority_record_commit,
    )
    verify_protected_ref(
        github=github, token=vellum_token, full_name="Generous-Corp/vellum",
        ref=str(record["authority_record_ref"]), commit=authority_record_commit,
        expected_apps=vellum["required_check_app_ids"],
    )
    verify_checks(
        github=github, token=vellum_token, full_name="Generous-Corp/vellum",
        commit=authority_record_commit, expected_apps=vellum["required_check_app_ids"],
    )
    return policy


def validate_evidence_shape(evidence: object) -> dict[str, object]:
    if not isinstance(evidence, dict) or set(evidence) != EVIDENCE_FIELDS:
        raise ActivationError("Pulp activation evidence fields differ")
    if evidence.get("schema_version") != 1 or evidence.get("state") != "landed-pulp-activation-evidence":
        raise ActivationError("Pulp activation evidence must be landed schema v1")
    require_sha(evidence.get("pulp_activation_commit"), "evidence.pulp_activation_commit")
    for field in ("ownership_projection_blob", "authority_event_blob"):
        require_sha(evidence.get(field), f"evidence.{field}")
    for field in ("ownership_projection_path", "authority_event_path"):
        value = evidence.get(field)
        if not isinstance(value, str) or value.startswith("/") or ".." in PurePosixPath(value).parts:
            raise ActivationError(f"evidence.{field} is unsafe")
    if evidence.get("ownership_projection_path") != OWNERSHIP_PATH:
        raise ActivationError("Pulp ownership projection path differs")
    if not isinstance(evidence.get("checks"), list):
        raise ActivationError("Pulp evidence checks must be an array")
    protection = evidence.get("branch_protection")
    if not isinstance(protection, dict) or set(protection) != {"strict", "required_contexts"}:
        raise ActivationError("Pulp evidence branch protection fields differ")
    if protection.get("strict") is not True:
        raise ActivationError("Pulp branch protection evidence must be strict")
    parse = evidence.get("retrieved_at")
    if not isinstance(parse, str) or not parse.endswith("Z"):
        raise ActivationError("Pulp evidence retrieved_at must be UTC")
    return evidence


def verify_candidate_unchanged(
    *, pulp_repo: Path, candidate_commit: str, activation_commit: str, paths: list[str]
) -> None:
    changed = git(
        pulp_repo, "diff", "--name-only", candidate_commit, activation_commit, "--", *paths
    )
    if changed:
        raise ActivationError(
            "Pulp candidate source changed after the recorded candidate commit: "
            + ", ".join(changed.splitlines())
        )


def verify_activated_path_set(
    *, slices: dict[str, dict[str, object]], expected_slice_ids: set[str],
    candidate_paths: list[str]
) -> None:
    active_paths: list[str] = []
    for slice_id in expected_slice_ids:
        item = slices.get(slice_id)
        paths = item.get("paths") if isinstance(item, dict) else None
        if not isinstance(paths, list) or any(not isinstance(path, str) for path in paths):
            raise ActivationError(f"Pulp activated slice lacks exact paths: {slice_id}")
        active_paths.extend(paths)
    if len(active_paths) != len(set(active_paths)) or sorted(active_paths) != candidate_paths:
        raise ActivationError("Pulp activated ownership path set differs from the recorded candidate")


def verify_pulp_activation(
    *, root: Path, pulp_repo: Path, record_path: str, record: dict[str, object],
    authority_record_commit: str, evidence: dict[str, object], github: GitHub, pulp_token: str,
    pulp_app_jwt: str, policy: dict[str, object]
) -> None:
    pulp = policy["repositories"]["pulp"]
    verify_repository(github, pulp_token, pulp, app_jwt=pulp_app_jwt)
    activation_commit = require_commit(pulp_repo, evidence["pulp_activation_commit"], "Pulp activation commit")
    require_ancestor(pulp_repo, str(record["pulp_extraction_base"]), activation_commit, "Pulp activation lineage")
    candidate_commit = require_commit(
        pulp_repo, record["pulp_candidate_commit"], "Pulp candidate commit"
    )
    require_ancestor(
        pulp_repo, str(record["pulp_extraction_base"]), candidate_commit,
        "Pulp candidate lineage",
    )
    require_ancestor(pulp_repo, candidate_commit, activation_commit, "Pulp activation candidate lineage")
    if git_blob(pulp_repo, candidate_commit, OWNERSHIP_PATH) != record["pulp_ownership_projection_blob"]:
        raise ActivationError("recorded Pulp candidate ownership projection blob differs")
    if git_blob(pulp_repo, activation_commit, str(evidence["ownership_projection_path"])) != evidence["ownership_projection_blob"]:
        raise ActivationError("Pulp ownership projection blob evidence differs")
    if git_blob(pulp_repo, activation_commit, str(evidence["authority_event_path"])) != evidence["authority_event_blob"]:
        raise ActivationError("Pulp authority event blob evidence differs")
    ownership = json_at(pulp_repo, activation_commit, str(evidence["ownership_projection_path"]))
    slices = ownership_slices(ownership)
    expected_slice_ids = {str(slice_id) for group in record["authority_groups"] for slice_id in group["pulp_legacy_slices"]}
    event = json_at(pulp_repo, activation_commit, str(evidence["authority_event_path"]))
    if event.get("kind") != "authority-transition" or event.get("transition") != "activate":
        raise ActivationError("Pulp authority event is not the activation transition")
    if event.get("approved_by") != "@danielraffel":
        raise ActivationError("Pulp authority event lacks framework-owner approval")
    event_id = event.get("event_id")
    created_at = event.get("created_at")
    if not isinstance(event_id, str) or not isinstance(created_at, str) or not created_at.endswith("Z"):
        raise ActivationError("Pulp authority event identity/time is invalid")
    activation = ownership.get("activation")
    if not isinstance(activation, dict) or activation.get("state") != "active":
        raise ActivationError("Pulp ownership projection is not active")
    if (
        activation.get("initial_transition_event") != event_id
        or activation.get("accepted_by") != event.get("approved_by")
        or activation.get("accepted_at") != created_at
    ):
        raise ActivationError("Pulp activation metadata does not derive from the authority event")
    for slice_id in expected_slice_ids:
        item = slices.get(slice_id)
        if item is None or item.get("state") != "framework-authoritative-transferred":
            raise ActivationError(f"Pulp slice is not frozen under framework authority: {slice_id}")
        authority = item.get("authority")
        if (
            not isinstance(authority, dict)
            or authority.get("event_id") != event_id
            or authority.get("vellum_commit") != authority_record_commit
            or authority.get("counterpart") != record_path
            or authority.get("accepted_by") != event.get("approved_by")
            or authority.get("accepted_at") != created_at
        ):
            raise ActivationError(f"Pulp slice authority does not reference the exact Vellum record: {slice_id}")
    if event.get("vellum_authority_commit") != authority_record_commit or event.get("counterpart") != record_path:
        raise ActivationError("Pulp authority event does not reference the exact Vellum record")
    if event.get("slices") != sorted(expected_slice_ids):
        raise ActivationError("Pulp authority event slice set differs")
    candidate_paths = sorted({
        path
        for group in record["authority_groups"]
        for path in group["pulp_activation_candidate_projection"]
    })
    verify_activated_path_set(
        slices=slices,
        expected_slice_ids=expected_slice_ids,
        candidate_paths=candidate_paths,
    )
    verify_candidate_unchanged(
        pulp_repo=pulp_repo,
        candidate_commit=candidate_commit,
        activation_commit=activation_commit,
        paths=candidate_paths,
    )
    verify_checks(
        github=github, token=pulp_token, full_name="Generous-Corp/pulp", commit=activation_commit,
        expected_apps=pulp["required_check_app_ids"], supplied=evidence["checks"],
    )
    protection = github.get("/repos/Generous-Corp/pulp/branches/main/protection", pulp_token)
    required = protection.get("required_status_checks") if isinstance(protection, dict) else None
    if not isinstance(required, dict) or required.get("strict") is not True:
        raise ActivationError("live Pulp main protection is not strict")
    checks = required.get("checks")
    observed = {
        str(item["context"]): item.get("app_id")
        for item in checks if isinstance(item, dict) and isinstance(item.get("context"), str)
    } if isinstance(checks, list) else {}
    if evidence["branch_protection"].get("required_contexts") != sorted(pulp["required_check_app_ids"]):
        raise ActivationError("supplied Pulp branch-protection contexts differ from the trust policy")
    for name, app_id in pulp["required_check_app_ids"].items():
        if observed.get(name) != app_id:
            raise ActivationError(f"live Pulp main protection does not bind {name} to App {app_id}")


def verify_prepared(root: Path) -> dict[str, object]:
    plan = validate_plan(load_json(root / PLAN_PATH))
    policy = load_json(root / TRUST_PATH)
    if not isinstance(policy, dict) or policy.get("state") == "enabled":
        raise ActivationError("prepared verification requires authority trust to remain disabled")
    if (root / "provenance/authority/records").exists() and any((root / "provenance/authority/records").glob("*.json")):
        raise ActivationError("prepared repository must not contain an active/pending authority record")
    return {
        "status": "prepared-not-active",
        "historical_seed_commit": plan["historical_seed_commit"],
        "authority_groups": [group["id"] for group in plan["authority_groups"]],
        "activation_blocker": "dedicated App/check trust policy is disabled",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("verify-prepared")
    build = subparsers.add_parser("build-record")
    build.add_argument("--pulp-repo", type=Path, required=True)
    build.add_argument("--pulp-ownership-commit", required=True)
    build.add_argument("--authority-start-commit", required=True)
    build.add_argument("--authority-record-ref", required=True)
    build.add_argument("--approved-at", required=True)
    build.add_argument("--output", type=Path, required=True)
    verify_pending = subparsers.add_parser("verify-pending")
    verify_pending.add_argument("--pulp-repo", type=Path, required=True)
    verify_pending.add_argument("--pulp-ownership-commit", required=True)
    verify_pending.add_argument("--record-path", required=True)
    verify_pending.add_argument("--authority-record-commit", required=True)
    verify_pending.add_argument("--expected-authority-ref", required=True)
    verify_pending.add_argument("--output", type=Path)
    verify_active = subparsers.add_parser("verify-active")
    verify_active.add_argument("--pulp-repo", type=Path, required=True)
    verify_active.add_argument("--pulp-ownership-commit", required=True)
    verify_active.add_argument("--record", type=Path, required=True)
    verify_active.add_argument("--record-path", required=True)
    verify_active.add_argument("--authority-record-commit", required=True)
    verify_active.add_argument("--pulp-evidence", type=Path, required=True)
    verify_active.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve()
        if args.command == "verify-prepared":
            result = verify_prepared(root)
        elif args.command == "build-record":
            result = build_record(
                root=root, pulp_repo=args.pulp_repo.resolve(),
                pulp_ownership_commit=args.pulp_ownership_commit,
                authority_start_commit=args.authority_start_commit,
                authority_record_ref=args.authority_record_ref,
                approved_at=args.approved_at,
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        elif args.command == "verify-pending":
            result = verify_pending_record(
                root=root,
                pulp_repo=args.pulp_repo.resolve(),
                pulp_ownership_commit=args.pulp_ownership_commit,
                record_path=args.record_path,
                authority_record_commit=args.authority_record_commit,
                expected_authority_ref=args.expected_authority_ref,
                require_head=True,
            )
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(
                    json.dumps(result, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
        else:
            record = validate_record_shape(load_json(args.record))
            verify_pending_record(
                root=root,
                pulp_repo=args.pulp_repo.resolve(),
                pulp_ownership_commit=args.pulp_ownership_commit,
                record_path=args.record_path,
                authority_record_commit=args.authority_record_commit,
            )
            if record != validate_record_shape(load_json(root / args.record_path)):
                raise ActivationError(
                    "supplied authority record differs from the committed record path"
                )
            vellum_token = os.environ.get("VELLUM_AUTHORITY_READER_TOKEN")
            pulp_token = os.environ.get("PULP_AUTHORITY_READER_TOKEN")
            vellum_app_jwt = os.environ.get("VELLUM_AUTHORITY_APP_JWT")
            pulp_app_jwt = os.environ.get("PULP_AUTHORITY_APP_JWT")
            if not vellum_token or not pulp_token or not vellum_app_jwt or not pulp_app_jwt:
                raise ActivationError(
                    "dedicated Vellum/Pulp authority reader tokens and matching App JWTs are required"
                )
            github = LiveGitHub()
            policy = verify_pending_live(
                root=root, record_path=args.record_path, record=record,
                authority_record_commit=args.authority_record_commit,
                github=github, vellum_token=vellum_token, vellum_app_jwt=vellum_app_jwt,
            )
            evidence = validate_evidence_shape(load_json(args.pulp_evidence))
            verify_pulp_activation(
                root=root, pulp_repo=args.pulp_repo.resolve(), record_path=args.record_path,
                record=record, authority_record_commit=args.authority_record_commit,
                evidence=evidence, github=github, pulp_token=pulp_token,
                pulp_app_jwt=pulp_app_jwt, policy=policy,
            )
            result = {
                "status": "pass",
                "authority_start_commit": record["authority_start_commit"],
                "authority_record_commit": args.authority_record_commit,
                "pulp_activation_commit": evidence["pulp_activation_commit"],
            }
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (ActivationError, OSError, ValueError) as error:
        print(f"authority-activation: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
