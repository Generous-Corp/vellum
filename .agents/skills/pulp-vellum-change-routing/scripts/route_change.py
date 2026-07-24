#!/usr/bin/env python3
"""Read-only Pulp/Vellum authority and change router."""

from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from routing_evidence import (  # noqa: E402
    commit_exists as _commit_exists,
    commit_is_ancestor as _commit_is_ancestor,
    validate_adoption_contract as _validate_adoption_contract,
    validate_emergency as _validate_emergency,
)

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
OWNER_RE = re.compile(
    r"^@[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})"
    r"(?:/[A-Za-z0-9](?:[A-Za-z0-9_-]{0,99}))?$"
)
TRANSFERRED = "framework-authoritative-transferred"
PULP_STATES = {"pulp-only", "excluded", "pulp-authoritative-untransferred"}
COPY_OPERATIONS = {"auto-copy", "cherry-pick"}


class AuthorityError(RuntimeError):
    """Authority evidence is missing, malformed, or contradictory."""


@dataclass(frozen=True)
class Authority:
    vellum: Path
    pulp: Path
    vellum_head: str
    pulp_head: str
    pulp_projection: dict[str, Any]
    counterpart_map: dict[str, Any]
    coordinates: dict[str, str]


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuthorityError(f"cannot load {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AuthorityError(f"{path} must contain an object")
    return value


def _simple_activation_yaml(path: Path) -> dict[str, str]:
    """Read scalar fields from the top-level activation mapping without PyYAML."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise AuthorityError(f"cannot load {path}: {exc}") from exc
    active = False
    result: dict[str, str] = {}
    for line in lines:
        if line == "activation:":
            active = True
            continue
        if active and line and not line.startswith(" "):
            break
        if not active:
            continue
        match = re.fullmatch(r"  ([a-z0-9_]+):[ \t]*(.*)", line)
        if match:
            result[match.group(1)] = match.group(2).strip().strip("\"'")
    if not result:
        raise AuthorityError(f"{path} has no readable activation mapping")
    return result


def _require_string(mapping: dict[str, Any], key: str, source: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise AuthorityError(f"{source} requires {key}")
    return value


def _git_text(repo: Path, *arguments: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *arguments],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
    except subprocess.CalledProcessError as exc:
        raise AuthorityError(
            f"cannot read Git evidence in {repo}: {' '.join(arguments)}"
        ) from exc


def _clean_checkout_head(repo: Path, label: str) -> str:
    try:
        top = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--show-toplevel"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout.strip()
        head = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout.strip()
        status = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
    except subprocess.CalledProcessError as exc:
        raise AuthorityError(f"{label} must be a readable Git checkout") from exc
    if Path(top).resolve() != repo.resolve():
        raise AuthorityError(f"{label} must name the checkout root")
    if not SHA_RE.fullmatch(head):
        raise AuthorityError(f"{label} HEAD is not an immutable commit")
    if status:
        raise AuthorityError(f"{label} checkout must be clean")
    return head


def load_authority(vellum: Path, pulp: Path) -> Authority:
    vellum = vellum.resolve()
    pulp = pulp.resolve()
    vellum_head = _clean_checkout_head(vellum, "Vellum")
    pulp_head = _clean_checkout_head(pulp, "Pulp")
    ownership = _simple_activation_yaml(vellum / "provenance/ownership-map.yaml")
    extraction = _load_json(vellum / "provenance/pulp-extraction.json")
    projection = _load_json(pulp / ".github/vellum-ownership.json")
    counterpart = _load_json(
        vellum / "provenance/pulp-observatory/legacy-path-map.yaml"
    )

    extraction_authority = extraction.get("authority")
    projection_activation = projection.get("activation")
    if not isinstance(extraction_authority, dict):
        raise AuthorityError("Vellum extraction authority must be an object")
    if not isinstance(projection_activation, dict):
        raise AuthorityError("Pulp activation must be an object")

    states = {
        _require_string(ownership, "state", "Vellum ownership"),
        _require_string(extraction_authority, "state", "Vellum extraction"),
        _require_string(projection_activation, "state", "Pulp projection"),
    }
    if states != {"active"}:
        raise AuthorityError(f"authority activation states disagree or are not active: {sorted(states)}")

    candidates = {
        "vellum_authority_record_commit": [
            _require_string(ownership, "vellum_authority_record_commit", "Vellum ownership"),
            _require_string(extraction_authority, "authority_record_commit", "Vellum extraction"),
            _require_string(projection_activation, "vellum_authority_commit", "Pulp projection"),
        ],
        "authority_record_path": [
            _require_string(ownership, "authority_record_path", "Vellum ownership"),
            _require_string(extraction_authority, "authority_record_path", "Vellum extraction"),
            _require_string(projection_activation, "authority_record_path", "Pulp projection"),
        ],
        "pulp_activation_commit": [
            _require_string(ownership, "pulp_activation_commit", "Vellum ownership"),
            _require_string(extraction_authority, "pulp_activation_commit", "Vellum extraction"),
        ],
        "pulp_authority_event_id": [
            _require_string(ownership, "pulp_authority_event_id", "Vellum ownership"),
            _require_string(extraction_authority, "pulp_authority_event_id", "Vellum extraction"),
            _require_string(projection_activation, "initial_transition_event", "Pulp projection"),
        ],
    }
    coordinates: dict[str, str] = {}
    for key, values in candidates.items():
        if len(set(values)) != 1:
            raise AuthorityError(f"{key} disagrees across authority artifacts: {values}")
        coordinates[key] = values[0]
    for key in ("vellum_authority_record_commit", "pulp_activation_commit"):
        if not SHA_RE.fullmatch(coordinates[key]):
            raise AuthorityError(f"{key} is not an immutable 40-character commit")
    record_commit = coordinates["vellum_authority_record_commit"]
    pulp_activation_commit = coordinates["pulp_activation_commit"]
    for repo, commit, head, label in (
        (vellum, record_commit, vellum_head, "Vellum authority record"),
        (pulp, pulp_activation_commit, pulp_head, "Pulp activation"),
    ):
        if not _commit_exists(repo, commit):
            raise AuthorityError(f"{label} commit does not resolve")
        completed = subprocess.run(
            ["git", "-C", str(repo), "merge-base", "--is-ancestor", commit, head],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if completed.returncode != 0:
            raise AuthorityError(f"{label} commit is not an ancestor of checkout HEAD")
    record_path = coordinates["authority_record_path"]
    parsed_record_path = PurePosixPath(record_path)
    if (
        parsed_record_path.is_absolute()
        or "\\" in record_path
        or any(part in {".", ".."} for part in parsed_record_path.parts)
        or parsed_record_path.as_posix() != record_path
    ):
        raise AuthorityError("authority record path is not a safe repository-relative path")
    _git_text(vellum, "show", f"{record_commit}:{record_path}")
    try:
        activation_projection = json.loads(
            _git_text(
                pulp,
                "show",
                f"{pulp_activation_commit}:.github/vellum-ownership.json",
            )
        )
    except json.JSONDecodeError as exc:
        raise AuthorityError("Pulp activation projection is not valid JSON") from exc
    historical_activation = (
        activation_projection.get("activation")
        if isinstance(activation_projection, dict)
        else None
    )
    if not isinstance(historical_activation, dict):
        raise AuthorityError("Pulp activation projection has no activation object")
    for key, expected in (
        ("state", "active"),
        ("vellum_authority_commit", record_commit),
        ("authority_record_path", record_path),
        ("initial_transition_event", coordinates["pulp_authority_event_id"]),
    ):
        if historical_activation.get(key) != expected:
            raise AuthorityError(f"Pulp activation commit disagrees on {key}")
    historical_slices = activation_projection.get("slices")
    if not isinstance(historical_slices, list):
        raise AuthorityError("Pulp activation projection slices must be an array")

    if projection.get("framework_repository") != "Generous-Corp/vellum":
        raise AuthorityError("Pulp projection names an unexpected framework repository")
    slices = projection.get("slices")
    if not isinstance(slices, list):
        raise AuthorityError("Pulp projection slices must be an array")
    for label, rows in (
        ("Pulp activation projection", historical_slices),
        ("Pulp projection", slices),
    ):
        ids = [
            item.get("id")
            for item in rows
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        ]
        if len(ids) != len(rows) or len(ids) != len(set(ids)):
            raise AuthorityError(f"{label} slice IDs must be present and unique")
    current_by_id = {
        item.get("id"): item
        for item in slices
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    historical_transferred_ids = {
        item.get("id")
        for item in historical_slices
        if isinstance(item, dict) and item.get("state") == TRANSFERRED
    }
    current_transferred_ids = {
        item.get("id")
        for item in slices
        if isinstance(item, dict) and item.get("state") == TRANSFERRED
    }
    if current_transferred_ids != historical_transferred_ids:
        raise AuthorityError(
            "transferred slice set changed without a verified authority protocol"
        )
    for historical in historical_slices:
        if (
            not isinstance(historical, dict)
            or historical.get("state") != TRANSFERRED
        ):
            continue
        slice_id = historical.get("id")
        current = current_by_id.get(slice_id)
        if (
            not isinstance(current, dict)
            or current.get("state") != TRANSFERRED
            or current.get("paths") != historical.get("paths")
            or current.get("authority") != historical.get("authority")
        ):
            raise AuthorityError(
                f"activated transferred slice {slice_id} changed without a verified reversal protocol"
            )
    expected_slice_authority = {
        "event_id": coordinates["pulp_authority_event_id"],
        "vellum_commit": coordinates["vellum_authority_record_commit"],
        "counterpart": coordinates["authority_record_path"],
    }
    for item in slices:
        if not isinstance(item, dict):
            raise AuthorityError("Pulp projection contains a malformed slice")
        if item.get("state") != TRANSFERRED:
            continue
        slice_id = item.get("id")
        slice_authority = item.get("authority")
        if (
            not isinstance(slice_authority, dict)
            or set(slice_authority)
            != {"event_id", "vellum_commit", "counterpart", "accepted_by", "accepted_at"}
        ):
            raise AuthorityError(
                f"transferred slice {slice_id} requires exact authority metadata"
            )
        for key, expected in expected_slice_authority.items():
            if slice_authority.get(key) != expected:
                raise AuthorityError(
                    f"transferred slice {slice_id} authority disagrees on {key}"
                )
        if not OWNER_RE.fullmatch(str(slice_authority.get("accepted_by", ""))):
            raise AuthorityError(
                f"transferred slice {slice_id} has an invalid accepted_by owner"
            )
        try:
            accepted_at = dt.datetime.fromisoformat(
                str(slice_authority.get("accepted_at", "")).replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise AuthorityError(
                f"transferred slice {slice_id} has an invalid accepted_at timestamp"
            ) from exc
        if accepted_at.tzinfo is None:
            raise AuthorityError(
                f"transferred slice {slice_id} accepted_at must include a timezone"
            )
    mappings = counterpart.get("mappings")
    if not isinstance(mappings, list):
        raise AuthorityError("counterpart mappings must be an array")
    mapping_ids: set[str] = set()
    for mapping in mappings:
        if not isinstance(mapping, dict):
            raise AuthorityError("counterpart map contains a malformed row")
        mapping_id = mapping.get("id")
        if (
            not isinstance(mapping_id, str)
            or not mapping_id
            or mapping_id in mapping_ids
        ):
            raise AuthorityError("counterpart mapping IDs must be present and unique")
        mapping_ids.add(mapping_id)
        for field in ("pulp_paths", "vellum_paths", "contract_tests"):
            values = mapping.get(field)
            if (
                not isinstance(values, list)
                or any(not isinstance(value, str) or not value for value in values)
            ):
                raise AuthorityError(
                    f"counterpart mapping {mapping_id} {field} must be a string array"
                )
    transitive_rules = counterpart.get("transitive_path_rules", [])
    if (
        not isinstance(transitive_rules, list)
        or any(not isinstance(rule, str) or not rule for rule in transitive_rules)
    ):
        raise AuthorityError("counterpart transitive_path_rules must be a string array")
    return Authority(
        vellum,
        pulp,
        vellum_head,
        pulp_head,
        projection,
        counterpart,
        coordinates,
    )


def _path_matches(path: str, rule: str) -> bool:
    normalized = path.lstrip("./")
    rule = rule.lstrip("./")
    return normalized.startswith(rule) if rule.endswith("/") else normalized == rule


def _prefix_matches(path: str, prefix: str) -> bool:
    normalized = path.lstrip("./")
    prefix = prefix.lstrip("./")
    return normalized == prefix.rstrip("/") or normalized.startswith(prefix)


def _transitive_match(path: str, rules: list[str]) -> bool:
    name = PurePosixPath(path).name
    return any(
        fnmatch.fnmatch(name, rule) or fnmatch.fnmatch(path, rule)
        for rule in rules
    )


def exact_slices(authority: Authority, path: str) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for item in authority.pulp_projection["slices"]:
        if not isinstance(item, dict) or not isinstance(item.get("paths"), list):
            raise AuthorityError("Pulp projection contains a malformed slice")
        if any(isinstance(rule, str) and _path_matches(path, rule) for rule in item["paths"]):
            matches.append(item)
    if len(matches) > 1:
        ids = [str(item.get("id")) for item in matches]
        raise AuthorityError(f"Pulp path has multiple exact owners: {path}: {ids}")
    return matches


def counterpart_contracts(authority: Authority, source: str, paths: list[str]) -> tuple[list[str], list[str]]:
    contracts: set[str] = set()
    tests: set[str] = set()
    field = "pulp_paths" if source == "pulp" else "vellum_paths"
    for mapping in authority.counterpart_map["mappings"]:
        if not isinstance(mapping, dict):
            raise AuthorityError("counterpart map contains a malformed row")
        rules = mapping.get(field, [])
        if not isinstance(rules, list):
            raise AuthorityError("counterpart path rules must be an array")
        if any(
            isinstance(rule, str) and _prefix_matches(path, rule)
            for path in paths for rule in rules
        ):
            contracts.add(str(mapping.get("id")))
            tests.update(str(test) for test in mapping.get("contract_tests", []))
    return sorted(contracts), sorted(tests)


def _result(status: str, **values: Any) -> dict[str, Any]:
    return {
        "status": status,
        "primary_repository": values.pop("primary_repository", None),
        "secondary_repository": values.pop("secondary_repository", None),
        "matched_slices": values.pop("matched_slices", []),
        "counterpart_contracts": values.pop("counterpart_contracts", []),
        "pulp_event_disposition": values.pop("pulp_event_disposition", None),
        "observatory_disposition": values.pop("observatory_disposition", None),
        "required_tests": values.pop("required_tests", []),
        "transitive_paths": values.pop("transitive_paths", []),
        "unmapped_paths": values.pop("unmapped_paths", []),
        "conflicts": values.pop("conflicts", []),
        "reasons": values.pop("reasons", []),
        **values,
    }



def route(
    authority: Authority,
    *,
    source_repo: str,
    paths: list[str],
    intent: str,
    counterpart_result: str = "not-checked",
    operation: str = "route",
    framework_commit: str | None = None,
    adoption_contract: str | None = None,
    emergency_event: str | None = None,
    emergency_owner: str | None = None,
    emergency_created: str | None = None,
    emergency_expiry: str | None = None,
    emergency_follow_up: str | None = None,
    now: dt.date | None = None,
) -> dict[str, Any]:
    if (
        _clean_checkout_head(authority.vellum, "Vellum") != authority.vellum_head
        or _clean_checkout_head(authority.pulp, "Pulp") != authority.pulp_head
    ):
        raise AuthorityError("authority checkout HEAD changed after loading")
    normalized_paths: set[str] = set()
    invalid_paths: list[str] = []
    for supplied in paths:
        candidate = supplied.strip()
        parsed = PurePosixPath(candidate)
        if (
            not candidate
            or "\\" in candidate
            or parsed.is_absolute()
            or any(part in {".", ".."} for part in parsed.parts)
            or parsed.as_posix() != candidate
        ):
            invalid_paths.append(supplied)
        else:
            normalized_paths.add(candidate)
    paths = sorted(normalized_paths)
    if invalid_paths:
        return _result(
            "decision_required",
            reasons=[
                "paths must be normalized repository-relative POSIX paths: "
                + ", ".join(repr(path) for path in invalid_paths)
            ],
        )
    if not paths:
        return _result("decision_required", reasons=["at least one path is required"])
    if intent == "unknown":
        return _result("decision_required", reasons=["intent must be declared"])
    contracts, contract_tests = counterpart_contracts(authority, source_repo, paths)

    if operation in COPY_OPERATIONS:
        return _result(
            "decision_required",
            counterpart_contracts=contracts,
            required_tests=contract_tests,
            reasons=["automatic copying and blind cherry-picking are forbidden"],
        )
    if operation == "sdk-adoption":
        if source_repo != "vellum" or intent != "generic":
            return _result(
                "decision_required",
                counterpart_contracts=contracts,
                required_tests=contract_tests,
                reasons=["SDK adoption must consume a generic Vellum release through a verified Pulp adoption contract"],
            )
        if not contracts or counterpart_result != "affected":
            return _result(
                "decision_required",
                counterpart_contracts=contracts,
                required_tests=contract_tests,
                reasons=[
                    "SDK adoption requires an applicable Pulp counterpart that was independently reproduced as affected"
                ],
            )
        if not adoption_contract:
            return _result(
                "decision_required",
                counterpart_contracts=contracts,
                required_tests=contract_tests,
                reasons=["SDK adoption requires an explicit current Pulp adoption contract"],
            )
        parsed_contract = PurePosixPath(adoption_contract)
        if (
            parsed_contract.is_absolute()
            or "\\" in adoption_contract
            or any(part in {".", ".."} for part in parsed_contract.parts)
            or parsed_contract.as_posix() != adoption_contract
        ):
            return _result(
                "decision_required",
                reasons=["SDK adoption contract must be a normalized Pulp-relative path"],
            )
        try:
            contract_text = _git_text(
                authority.pulp,
                "show",
                f"{authority.pulp_head}:{adoption_contract}",
            )
        except AuthorityError:
            return _result(
                "decision_required",
                reasons=["SDK adoption contract is not committed at the supplied Pulp HEAD"],
            )
        adoption_problems = _validate_adoption_contract(authority, contract_text)
        if adoption_problems:
            return _result(
                "decision_required",
                counterpart_contracts=contracts,
                required_tests=contract_tests,
                reasons=adoption_problems,
            )
        return _result(
            "routed",
            primary_repository="vellum",
            secondary_repository="pulp",
            counterpart_contracts=contracts,
            observatory_disposition="port-required",
            required_tests=contract_tests,
            reasons=["release Vellum, then update Pulp's immutable SDK pin and integration tests without a source backport"],
        )
    if operation == "framework-backport":
        if source_repo != "pulp" or intent != "generic":
            return _result(
                "decision_required",
                counterpart_contracts=contracts,
                required_tests=contract_tests,
                reasons=[
                    "framework backport requires generic intent into an exactly transferred Pulp path"
                ],
            )
        if not framework_commit or not SHA_RE.fullmatch(framework_commit):
            return _result(
                "decision_required",
                reasons=["framework backport requires an immutable 40-character Vellum commit"],
            )
        if not _commit_exists(authority.vellum, framework_commit):
            return _result(
                "decision_required",
                reasons=["framework backport commit does not resolve in the supplied Vellum checkout"],
            )
        if not _commit_is_ancestor(
            authority.vellum, framework_commit, authority.vellum_head
        ):
            return _result(
                "decision_required",
                reasons=[
                    "framework backport commit is outside the selected Vellum HEAD history"
                ],
            )
        if counterpart_result == "not-affected":
            return _result(
                "decision_required",
                counterpart_contracts=contracts,
                required_tests=contract_tests,
                reasons=[
                    "framework backport contradicts a counterpart result of not-affected"
                ],
            )

    if intent == "emergency" and source_repo != "pulp":
        return _result(
            "decision_required",
            reasons=["emergency exceptions apply only to transferred Pulp paths"],
        )

    if source_repo == "vellum":
        if intent == "pulp-specific":
            return _result(
                "decision_required",
                counterpart_contracts=contracts,
                required_tests=contract_tests,
                reasons=["Pulp-specific intent cannot be implemented first in a Vellum path"],
            )
        disposition = "framework-only"
        secondary = None
        if contracts and counterpart_result == "not-checked":
            disposition = "pending"
        elif contracts and counterpart_result == "affected":
            disposition = "port-required"
            secondary = "pulp"
        return _result(
            "routed",
            primary_repository="vellum",
            secondary_repository=secondary,
            counterpart_contracts=contracts,
            observatory_disposition=disposition,
            required_tests=contract_tests,
            reasons=["Vellum source changes originate in Vellum"],
        )

    exact_by_path = {path: exact_slices(authority, path) for path in paths}
    direct_contracts_by_path = {
        path: counterpart_contracts(authority, "pulp", [path])[0]
        for path in paths
    }
    transitive_rules = authority.counterpart_map.get("transitive_path_rules", [])
    if not isinstance(transitive_rules, list) or not all(
        isinstance(rule, str) for rule in transitive_rules
    ):
        raise AuthorityError("counterpart transitive_path_rules must be a string array")
    seed_states_by_root: dict[str, set[str]] = {}
    for path, matches in exact_by_path.items():
        if not matches:
            continue
        state = str(matches[0].get("state"))
        routed_state = "transferred" if state == TRANSFERRED else "pulp"
        parts = PurePosixPath(path).parts
        if parts:
            seed_states_by_root.setdefault(parts[0], set()).add(routed_state)
    states: set[str] = set()
    slices: set[str] = set()
    unmapped_shared: list[str] = []
    unmapped_native: list[str] = []
    transitive_paths: list[str] = []
    for path, matches in exact_by_path.items():
        if matches:
            state = str(matches[0].get("state"))
            if state != TRANSFERRED and state not in PULP_STATES:
                raise AuthorityError(f"unknown ownership state for {path}: {state}")
            states.add("transferred" if state == TRANSFERRED else "pulp")
            slices.add(str(matches[0].get("id")))
        elif direct_contracts_by_path[path]:
            states.add("unmapped-shared")
            unmapped_shared.append(path)
        elif _transitive_match(path, transitive_rules):
            parts = PurePosixPath(path).parts
            seed_states = (
                set().union(*seed_states_by_root.values())
                if path == "CMakeLists.txt" and seed_states_by_root
                else seed_states_by_root.get(parts[0], set()) if parts else set()
            )
            if len(seed_states) == 1:
                states.update(seed_states)
                transitive_paths.append(path)
            else:
                states.add("pulp")
                unmapped_native.append(path)
        else:
            states.add("pulp")
            unmapped_native.append(path)

    if "unmapped-shared" in states:
        return _result(
            "decision_required",
            matched_slices=sorted(slices),
            counterpart_contracts=contracts,
            required_tests=contract_tests,
            transitive_paths=transitive_paths,
            unmapped_paths=unmapped_shared,
            reasons=["new Pulp path is absent from the exact projection but matches a broad shared counterpart"],
        )
    if len(states) > 1:
        return _result(
            "decision_required",
            matched_slices=sorted(slices),
            counterpart_contracts=contracts,
            required_tests=contract_tests,
            transitive_paths=transitive_paths,
            conflicts=sorted(states),
            reasons=["proposed change mixes transferred and Pulp-owned paths; split or make an explicit reviewed decision"],
        )

    transferred = states == {"transferred"}
    if intent == "emergency":
        if not transferred:
            return _result(
                "decision_required",
                matched_slices=sorted(slices),
                reasons=[
                    "emergency exceptions apply only to exactly transferred Pulp paths"
                ],
            )
        problems = _validate_emergency(
            authority,
            emergency_event,
            emergency_owner,
            emergency_created,
            emergency_expiry,
            emergency_follow_up,
            slices,
            now or dt.datetime.now(dt.timezone.utc).date(),
        )
        if problems:
            return _result(
                "decision_required",
                matched_slices=sorted(slices),
                reasons=problems,
            )
    if operation == "framework-backport" and not transferred:
        return _result(
            "decision_required",
            matched_slices=sorted(slices),
            reasons=["framework-backport applies only to exactly transferred Pulp paths"],
        )

    if transferred and intent == "generic":
        return _result(
            "routed",
            primary_repository="vellum",
            secondary_repository="pulp",
            matched_slices=sorted(slices),
            counterpart_contracts=contracts,
            pulp_event_disposition="framework-backport",
            observatory_disposition="port-required",
            required_tests=contract_tests,
            transitive_paths=transitive_paths,
            reasons=["generic fixes to transferred Pulp slices originate in Vellum"],
        )
    if transferred and intent == "pulp-specific":
        return _result(
            "routed",
            primary_repository="pulp",
            matched_slices=sorted(slices),
            counterpart_contracts=contracts,
            pulp_event_disposition="pulp-only",
            observatory_disposition="Pulp-only",
            required_tests=contract_tests,
            transitive_paths=transitive_paths,
            reasons=["declared Pulp-specific integration in a transferred slice requires a pulp-only event"],
        )
    if transferred and intent == "emergency":
        return _result(
            "routed",
            primary_repository="pulp",
            secondary_repository="vellum",
            matched_slices=sorted(slices),
            counterpart_contracts=contracts,
            pulp_event_disposition="emergency-exception",
            observatory_disposition="port-required",
            required_tests=contract_tests,
            transitive_paths=transitive_paths,
            reasons=["bounded Pulp emergency requires a parallel durable Vellum fix"],
        )

    if contracts and counterpart_result == "not-checked" and intent == "generic":
        return _result(
            "decision_required",
            primary_repository="pulp",
            matched_slices=sorted(slices),
            counterpart_contracts=contracts,
            observatory_disposition="pending",
            required_tests=contract_tests,
            transitive_paths=transitive_paths,
            unmapped_paths=unmapped_native,
            reasons=[
                "generic Pulp change has an applicable Vellum counterpart that must be reproduced independently"
            ],
        )
    if counterpart_result != "not-checked" and not contracts:
        return _result(
            "decision_required",
            primary_repository="pulp",
            matched_slices=sorted(slices),
            reasons=[
                "counterpart result was supplied but no Vellum counterpart contract applies"
            ],
        )
    if counterpart_result == "affected" and intent == "generic":
        return _result(
            "routed",
            primary_repository="split",
            secondary_repository="vellum",
            matched_slices=sorted(slices),
            counterpart_contracts=contracts,
            observatory_disposition="port-required",
            required_tests=contract_tests,
            transitive_paths=transitive_paths,
            unmapped_paths=unmapped_native,
            reasons=["Pulp owns this implementation, but the reproduced shared contract also needs an independent Vellum fix"],
        )
    return _result(
        "routed",
        primary_repository="pulp",
        matched_slices=sorted(slices),
        counterpart_contracts=contracts,
        observatory_disposition="Pulp-only" if contracts else "not-applicable",
        required_tests=contract_tests,
        transitive_paths=transitive_paths,
        unmapped_paths=unmapped_native,
        reasons=["exact Pulp ownership wins over broad counterpart discovery"],
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vellum-repo", required=True, type=Path)
    parser.add_argument("--pulp-repo", required=True, type=Path)
    parser.add_argument("--source-repo", required=True, choices=("vellum", "pulp"))
    parser.add_argument("--intent", required=True, choices=("generic", "pulp-specific", "emergency", "unknown"))
    parser.add_argument("--path", action="append", required=True)
    parser.add_argument("--counterpart-result", choices=("not-checked", "affected", "not-affected"), default="not-checked")
    parser.add_argument(
        "--operation",
        choices=("route", "framework-backport", "auto-copy", "cherry-pick", "sdk-adoption"),
        default="route",
    )
    parser.add_argument("--framework-commit")
    parser.add_argument("--adoption-contract")
    parser.add_argument("--emergency-event")
    parser.add_argument("--emergency-owner")
    parser.add_argument("--emergency-created")
    parser.add_argument("--emergency-expiry")
    parser.add_argument("--emergency-follow-up")
    parser.add_argument("--now", help="UTC date for deterministic emergency validation")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        today = dt.date.fromisoformat(args.now) if args.now else None
        authority = load_authority(args.vellum_repo, args.pulp_repo)
        result = route(
            authority,
            source_repo=args.source_repo,
            paths=args.path,
            intent=args.intent,
            counterpart_result=args.counterpart_result,
            operation=args.operation,
            framework_commit=args.framework_commit,
            adoption_contract=args.adoption_contract,
            emergency_event=args.emergency_event,
            emergency_owner=args.emergency_owner,
            emergency_created=args.emergency_created,
            emergency_expiry=args.emergency_expiry,
            emergency_follow_up=args.emergency_follow_up,
            now=today,
        )
    except (AuthorityError, ValueError) as exc:
        result = _result("invalid_authority", reasons=[str(exc)])
    print(json.dumps(result, indent=2, sort_keys=True))
    return {"routed": 0, "decision_required": 2, "invalid_authority": 3}[result["status"]]


if __name__ == "__main__":
    sys.exit(main())
