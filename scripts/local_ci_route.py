#!/usr/bin/env python3
"""Resolve a Vellum CI lane without mutating GitHub or runner state.

The checked-in targets are intentionally unproven. A local target becomes
selectable only in a later reviewed change that records its real disposable
dispatch and teardown proof. Until then this command always returns the hosted
fallback, even if an inventory claims that a similarly labelled runner exists.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set


REPOSITORY = "Generous-Corp/vellum"

TARGETS: Dict[str, Dict[str, Any]] = {
    "macpro.vellum-linux-x64-pr-safe": {
        "provider": "proxmox",
        "group": "vellum-pr-safe-build",
        "labels": [
            "self-hosted",
            "Linux",
            "X64",
            "vellum-build-linux-x64",
            "vellum-host-macpro",
            "vellum-pr-safe-linux-x64",
        ],
        "name_prefix": "vellum-pr-safe-ephemeral-",
        "workflow_access": [
            "Generous-Corp/vellum/.github/workflows/product-quality.yml@refs/heads/main",
            "Generous-Corp/vellum/.github/workflows/provenance.yml@refs/heads/main",
        ],
        "proven": False,
    },
    "m3.vellum-macos-arm64-vm": {
        "provider": "tartci",
        "group": "vellum-macos-build",
        "labels": [
            "self-hosted", "macOS", "ARM64", "vellum-build-macos",
            "vellum-host-m3",
        ],
        "name_prefix": "vellum-macos-ephemeral-",
        "workflow_access": [
            "Generous-Corp/vellum/.github/workflows/readme-quick-start.yml@refs/heads/main",
        ],
        "proven": False,
    },
    "m5.vellum-macos-arm64-vm": {
        "provider": "tartci",
        "group": "vellum-macos-build",
        "labels": [
            "self-hosted", "macOS", "ARM64", "vellum-build-macos",
            "vellum-host-m5",
        ],
        "name_prefix": "vellum-macos-ephemeral-",
        "workflow_access": [
            "Generous-Corp/vellum/.github/workflows/readme-quick-start.yml@refs/heads/main",
        ],
        "proven": False,
    },
    "m1.vellum-macos-arm64-vm": {
        "provider": "tartci",
        "group": "vellum-macos-build",
        "labels": [
            "self-hosted", "macOS", "ARM64", "vellum-build-macos",
            "vellum-host-m1",
        ],
        "name_prefix": "vellum-macos-ephemeral-",
        "workflow_access": [
            "Generous-Corp/vellum/.github/workflows/readme-quick-start.yml@refs/heads/main",
        ],
        "proven": False,
    },
    "macpro.vellum-windows-x64-qemu": {
        "provider": "proxmox",
        "group": "vellum-windows-build",
        "labels": [
            "self-hosted", "Windows", "X64", "vellum-build-windows-x64",
            "vellum-host-macpro",
        ],
        "name_prefix": "vellum-windows-ephemeral-",
        "workflow_access": [],
        "proven": False,
    },
    "macpro.vellum-linux-x64-release": {
        "provider": "proxmox",
        "group": "vellum-release-build",
        "labels": [
            "self-hosted", "Linux", "X64", "vellum-build-linux-x64",
            "vellum-host-macpro", "vellum-release-linux-x64",
        ],
        "name_prefix": "vellum-release-linux-ephemeral-",
        "workflow_access": [],
        "proven": False,
    },
    "m3.vellum-macos-arm64-release-vm": {
        "provider": "tartci",
        "group": "vellum-release-build",
        "labels": [
            "self-hosted", "macOS", "ARM64", "vellum-release-macos",
            "vellum-host-m3",
        ],
        "name_prefix": "vellum-release-macos-ephemeral-",
        "workflow_access": [],
        "proven": False,
    },
    "m5.vellum-macos-arm64-release-vm": {
        "provider": "tartci",
        "group": "vellum-release-build",
        "labels": [
            "self-hosted", "macOS", "ARM64", "vellum-release-macos",
            "vellum-host-m5",
        ],
        "name_prefix": "vellum-release-macos-ephemeral-",
        "workflow_access": [],
        "proven": False,
    },
    "m1.vellum-macos-arm64-release-vm": {
        "provider": "tartci",
        "group": "vellum-release-build",
        "labels": [
            "self-hosted", "macOS", "ARM64", "vellum-release-macos",
            "vellum-host-m1",
        ],
        "name_prefix": "vellum-release-macos-ephemeral-",
        "workflow_access": [],
        "proven": False,
    },
    "macmini.vellum-macos-intel-native": {
        "provider": "shipyard",
        "group": "vellum-macos-intel",
        "labels": [
            "self-hosted", "macOS", "X64", "vellum-build-macos-intel",
            "vellum-host-macmini",
        ],
        "name_prefix": "vellum-intel-ephemeral-",
        "workflow_access": [],
        "proven": False,
    },
    "github.linux-x64": {
        "provider": "github",
        "runs_on": "ubuntu-latest",
    },
    "github.macos-arm64": {
        "provider": "github",
        "runs_on": "macos-15",
    },
    "github.macos-intel": {
        "provider": "github",
        "runs_on": "macos-15-intel",
    },
    "github.windows-x64": {
        "provider": "github",
        "runs_on": "windows-latest",
    },
}

ROUTES: Dict[str, Dict[str, Any]] = {
    "pr.linux": {
        "targets": [
            "macpro.vellum-linux-x64-pr-safe", "github.linux-x64",
        ],
        "lease_variable": "VELLUM_PR_SAFE_LINUX_LEASE_UNTIL",
        "lease_ttl_seconds": 300,
        "events": ["push", "workflow_dispatch", "workflow_run"],
        "min_idle": 1,
    },
    "pr.macos": {
        "targets": [
            "m3.vellum-macos-arm64-vm",
            "m5.vellum-macos-arm64-vm",
            "m1.vellum-macos-arm64-vm",
            "github.macos-arm64",
        ],
        "lease_variable": "VELLUM_PR_MACOS_LEASE_UNTIL",
        "lease_ttl_seconds": 300,
        "events": ["workflow_dispatch", "workflow_run", "schedule"],
        "min_idle": 1,
    },
    "pr.windows": {
        "targets": [
            "macpro.vellum-windows-x64-qemu", "github.windows-x64",
        ],
        "lease_variable": "VELLUM_PR_WINDOWS_LEASE_UNTIL",
        "lease_ttl_seconds": 300,
        "events": ["workflow_dispatch", "workflow_run"],
        "min_idle": 1,
    },
    "release.macos_build": {
        "targets": [
            "m3.vellum-macos-arm64-release-vm",
            "m5.vellum-macos-arm64-release-vm",
            "m1.vellum-macos-arm64-release-vm",
            "github.macos-arm64",
        ],
        "lease_variable": "VELLUM_RELEASE_BUILD_MACOS_LEASE_UNTIL",
        "lease_ttl_seconds": 300,
        "events": ["workflow_dispatch"],
        "min_idle": 1,
    },
    "release.linux_build": {
        "targets": [
            "macpro.vellum-linux-x64-release", "github.linux-x64",
        ],
        "lease_variable": "VELLUM_RELEASE_BUILD_LINUX_LEASE_UNTIL",
        "lease_ttl_seconds": 300,
        "events": ["workflow_dispatch"],
        "min_idle": 1,
    },
    "scheduled.nightly_intel": {
        "targets": [
            "macmini.vellum-macos-intel-native", "github.macos-intel",
        ],
        "lease_variable": "VELLUM_NIGHTLY_INTEL_LEASE_UNTIL",
        "lease_ttl_seconds": 300,
        "events": ["schedule", "workflow_dispatch"],
        "min_idle": 1,
    },
    "release.signing": {"targets": ["github.linux-x64"]},
    "release.deploy": {"targets": ["github.linux-x64"]},
    "privileged.authority": {"targets": ["github.linux-x64"]},
    "privileged.merge_steward": {"targets": ["github.linux-x64"]},
}


def _normalized_labels(values: Any) -> Optional[Set[str]]:
    if not isinstance(values, list) or not values:
        return None
    if not all(isinstance(item, str) and item == item.strip() for item in values):
        return None
    normalized = {item.lower() for item in values}
    if len(normalized) != len(values):
        return None
    return normalized


def _parse_instant(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except (ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _lease_live(value: Any, now: datetime, ttl_seconds: int) -> bool:
    expiry = _parse_instant(value)
    if expiry is None:
        return False
    remaining = (expiry - now.astimezone(timezone.utc)).total_seconds()
    return 0 < remaining <= ttl_seconds


def _group_problem(
    inventory: Mapping[str, Any], target: Mapping[str, Any]
) -> Optional[str]:
    groups = inventory.get("groups")
    if not isinstance(groups, dict):
        return "runner-group-inventory-missing"
    row = groups.get(target["group"])
    if not isinstance(row, dict):
        return "runner-group-missing"
    repositories = row.get("repositories")
    if repositories != [REPOSITORY]:
        return "runner-group-repository-mismatch"
    if row.get("restricted_to_workflows") is not True:
        return "runner-group-workflow-unrestricted"
    if row.get("allows_public_repositories") is not False:
        return "runner-group-public-access"
    required_access = set(target.get("workflow_access", []))
    actual_access = row.get("workflow_access")
    if not required_access:
        return "target-workflow-access-unproven"
    if (
        not isinstance(actual_access, list)
        or not all(isinstance(item, str) for item in actual_access)
        or set(actual_access) != required_access
        or len(actual_access) != len(required_access)
    ):
        return "runner-group-workflow-access-mismatch"
    return None


def _workflow_problem(
    inventory: Mapping[str, Any], target: Mapping[str, Any]
) -> Optional[str]:
    allowed = target.get("workflow_access")
    if not isinstance(allowed, list) or not allowed:
        return "target-workflow-access-unproven"
    workflow_ref = inventory.get("workflow_ref")
    if not isinstance(workflow_ref, str) or workflow_ref not in allowed:
        return "workflow-ref-not-protected"
    return None


def _runner_eligible(runner: Any, target: Mapping[str, Any]) -> bool:
    if not isinstance(runner, dict):
        return False
    if runner.get("repository") != REPOSITORY:
        return False
    if runner.get("group") != target["group"]:
        return False
    name = runner.get("name")
    if not isinstance(name, str) or not name.startswith(target["name_prefix"]):
        return False
    actual = _normalized_labels(runner.get("labels"))
    required = _normalized_labels(target.get("labels"))
    if actual is None or required is None or required != actual:
        return False
    return all(
        [
            runner.get("status") == "online",
            runner.get("busy") is False,
            runner.get("healthy") is True,
            runner.get("ephemeral") is True,
            runner.get("teardown_proven") is True,
            runner.get("credentials_reusable") is False,
            runner.get("egress_policy_proven") is True,
            runner.get("writable_host_mounts") == [],
        ]
    )


def validate_contract() -> None:
    for lane, route in ROUTES.items():
        target_ids = route.get("targets")
        if not isinstance(target_ids, list) or not target_ids:
            raise ValueError("{}: route has no targets".format(lane))
        missing = [target_id for target_id in target_ids if target_id not in TARGETS]
        if missing:
            raise ValueError("{}: unknown targets: {}".format(lane, missing))
        providers = [TARGETS[target_id]["provider"] for target_id in target_ids]
        if providers[-1] != "github":
            raise ValueError("{}: final target is not hosted".format(lane))
        if "github" in providers[:-1]:
            raise ValueError("{}: hosted target precedes a local target".format(lane))
        if any(provider != "github" for provider in providers[:-1]):
            for key in ("lease_variable", "lease_ttl_seconds", "events", "min_idle"):
                if key not in route:
                    raise ValueError("{}: local route lacks {}".format(lane, key))


def resolve_route(
    lane: str,
    event: str,
    inventory: Mapping[str, Any],
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Return one concrete selector and a reason for every skipped target."""
    validate_contract()
    if lane not in ROUTES:
        raise ValueError("unknown lane: {}".format(lane))
    if inventory.get("repository", REPOSITORY) != REPOSITORY:
        raise ValueError("inventory repository is not {}".format(REPOSITORY))
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise ValueError("now must include a timezone")
    route = ROUTES[lane]
    skipped: List[Dict[str, str]] = []
    for target_id in route["targets"]:
        target = TARGETS[target_id]
        if target["provider"] == "github":
            return {
                "schema": 1,
                "repository": REPOSITORY,
                "lane": lane,
                "event": event,
                "selected": {
                    "target": target_id,
                    "provider": "github",
                    "runs_on": target["runs_on"],
                },
                "hosted_fallback": bool(skipped),
                "skipped": skipped,
            }
        if target.get("proven") is not True:
            skipped.append({"target": target_id, "reason": "target-unproven"})
            continue
        if event not in route["events"]:
            skipped.append({"target": target_id, "reason": "event-not-eligible"})
            continue
        workflow_problem = _workflow_problem(inventory, target)
        if workflow_problem is not None:
            skipped.append({"target": target_id, "reason": workflow_problem})
            continue
        leases = inventory.get("leases")
        lease_value = leases.get(route["lease_variable"]) if isinstance(leases, dict) else None
        if not _lease_live(lease_value, instant, route["lease_ttl_seconds"]):
            skipped.append({"target": target_id, "reason": "health-lease-not-live"})
            continue
        group_problem = _group_problem(inventory, target)
        if group_problem is not None:
            skipped.append({"target": target_id, "reason": group_problem})
            continue
        runners = inventory.get("runners")
        eligible = [
            row for row in runners if _runner_eligible(row, target)
        ] if isinstance(runners, list) else []
        if len(eligible) < route["min_idle"]:
            skipped.append({"target": target_id, "reason": "idle-capacity-unproven"})
            continue
        return {
            "schema": 1,
            "repository": REPOSITORY,
            "lane": lane,
            "event": event,
            "selected": {
                "target": target_id,
                "provider": target["provider"],
                "runs_on": copy.deepcopy(target["labels"]),
            },
            "hosted_fallback": False,
            "skipped": skipped,
        }
    raise ValueError("{}: no hosted fallback".format(lane))


def _load_inventory(path: Optional[str]) -> Mapping[str, Any]:
    if path is None:
        return {"repository": REPOSITORY, "groups": {}, "runners": [], "leases": {}}
    if path == "-":
        value = json.load(sys.stdin)
    else:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("inventory root must be an object")
    return value


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lane", required=True, choices=sorted(ROUTES))
    parser.add_argument("--event", required=True)
    parser.add_argument(
        "--inventory",
        help="read-only inventory JSON path, or '-' for stdin; omitted means empty",
    )
    parser.add_argument(
        "--now",
        help="UTC RFC3339 instant for deterministic audit output",
    )
    args = parser.parse_args(argv)
    try:
        now = _parse_instant(args.now) if args.now else None
        if args.now and now is None:
            raise ValueError("--now must be an RFC3339 UTC instant ending in Z")
        result = resolve_route(
            args.lane,
            args.event,
            _load_inventory(args.inventory),
            now=now,
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print("local-ci-route: {}".format(error), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
