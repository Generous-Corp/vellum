#!/usr/bin/env python3
"""Decide whether a merged release request may be tagged, and describe its freeze.

A release starts as a reviewed pull request that changes
provenance/release-request.json. When it lands, release-tag.yml tags the
commit that landed it. This script makes the decision from files the
workflow has already fetched, so it needs no network:

  check   the request names a new, unretired version that matches the
          project version, the tag does not exist yet, and every check the
          main branch rules require concluded success on the commit.
  freeze  the per-tag ruleset that verify_release_tag_ruleset.py requires
          before an immutable release is finalized.
  attestation
          the promotion attestation the tag carries as its signed message,
          attested by the release pipeline itself (owner decision 2026-10-31),
          checked with the release-readiness verifier's own validators before
          anything is signed. It binds the parity completion record when one
          exists at the commit, and otherwise is the mirror-promotion
          attestation that only the Phase 4 promotion tag may carry.

The immutable-release preflight is committed with the request and is only
accepted for a few hours; `check` refuses to tag unless enough of that window
remains for the tagged run to reach its trust step, so a stale record never
spends a version number.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REQUEST_SCHEMA = "vellum.release-request.v1"
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
SHA = re.compile(r"^[0-9a-f]{40}$")
PROJECT_VERSION = re.compile(r"project\(\s*vellum\b[^)]*\bVERSION\s+([0-9.]+)", re.IGNORECASE | re.DOTALL)


PREFLIGHT = Path("provenance/immutable-release-preflight.json")
# The tagged gpu-macos run reaches its trust step about 20 minutes after the
# tag, and sdk-release re-checks the preflight when it finalizes, after the
# whole gpu-macos run; tag only with at least this much of the window left.
PREFLIGHT_MARGIN = timedelta(hours=2)
EXPANSION = Path("provenance/authority/expansions/full-design-import-render-v1")


class ReleaseRequestError(RuntimeError):
    pass


def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseRequestError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=strict_object)
    except (OSError, ValueError) as error:
        raise ReleaseRequestError(f"cannot read {path}: {error}") from error


def request_version(request: Any) -> str:
    if not isinstance(request, dict) or set(request) != {"schema", "version"}:
        raise ReleaseRequestError("the request must hold exactly `schema` and `version`")
    if request["schema"] != REQUEST_SCHEMA:
        raise ReleaseRequestError(f"the request schema must be {REQUEST_SCHEMA}")
    version = request["version"]
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise ReleaseRequestError("the requested version must be MAJOR.MINOR.PATCH")
    return version


def project_version(cmakelists: str) -> str:
    found = PROJECT_VERSION.search(cmakelists)
    if not found:
        raise ReleaseRequestError("CMakeLists.txt declares no project VERSION")
    return found[1]


def retired(retired_tags: Any) -> set[str]:
    rows = retired_tags.get("tags") if isinstance(retired_tags, dict) else None
    if not isinstance(rows, list):
        raise ReleaseRequestError("the retired-tags file must hold a `tags` list")
    return {row["tag"] for row in rows if isinstance(row, dict) and isinstance(row.get("tag"), str)}


def required_contexts(branch_rules: Any) -> list[str]:
    """Contexts the branch's rulesets require, from GET /rules/branches/main."""
    if not isinstance(branch_rules, list):
        raise ReleaseRequestError("branch rules must be a list")
    contexts = []
    for rule in branch_rules:
        if isinstance(rule, dict) and rule.get("type") == "required_status_checks":
            for row in rule.get("parameters", {}).get("required_status_checks", []):
                if isinstance(row, dict) and isinstance(row.get("context"), str):
                    contexts.append(row["context"])
    if not contexts:
        raise ReleaseRequestError("the main branch rules require no status check; refusing to tag unchecked")
    return sorted(set(contexts))


def check_runs(pages: Any) -> list[dict[str, Any]]:
    """Check runs from `gh api --paginate --slurp`, an array of pages."""
    if not isinstance(pages, list):
        raise ReleaseRequestError("check-run pages must be an array")
    runs = []
    for page in pages:
        if not isinstance(page, dict) or not isinstance(page.get("check_runs"), list):
            raise ReleaseRequestError("a check-run page has no `check_runs` list")
        runs += [run for run in page["check_runs"] if isinstance(run, dict)]
    return runs


def unmet(contexts: list[str], runs: list[dict[str, Any]], commit: str) -> list[str]:
    """Required contexts whose latest run on `commit` did not conclude success."""
    latest: dict[str, dict[str, Any]] = {}
    for run in runs:
        if run.get("head_sha") != commit or not isinstance(run.get("name"), str):
            continue
        key = (run.get("completed_at") or "", run.get("id") or 0)
        current = latest.get(run["name"])
        if current is None or key > ((current.get("completed_at") or ""), current.get("id") or 0):
            latest[run["name"]] = run
    missing = []
    for context in contexts:
        run = latest.get(context)
        if run is None:
            missing.append(f"{context}: no run on {commit}")
        elif run.get("status") != "completed" or run.get("conclusion") != "success":
            missing.append(f"{context}: {run.get('status')}/{run.get('conclusion')}")
    return missing


def preflight_remaining(preflight: Any, tag: str, now: datetime) -> timedelta:
    """How much of the preflight's acceptance window is left; raises when it cannot be used."""
    if not isinstance(preflight, dict) or preflight.get("release_tag") != tag:
        raise ReleaseRequestError(f"the immutable-release preflight is not for {tag}; refresh it with the request")
    admin = preflight.get("administrator_check")
    if not isinstance(admin, dict) or admin.get("enabled") is not True:
        raise ReleaseRequestError("the immutable-release preflight does not show immutable releases enabled")
    try:
        checked = datetime.fromisoformat(str(admin["checked_at"]).replace("Z", "+00:00"))
        window = timedelta(seconds=int(preflight["maximum_age_seconds"]))
    except (KeyError, TypeError, ValueError) as error:
        raise ReleaseRequestError(f"the immutable-release preflight is malformed: {error}") from error
    return checked + window - now


def check(*, request: Any, cmakelists: str, retired_tags: Any, commit: str, tag_exists: bool,
          branch_rules: Any, pages: Any, preflight: Any = None, now: datetime | None = None) -> str:
    """The tag to create; raises ReleaseRequestError when the request may not be tagged."""
    if not SHA.fullmatch(commit):
        raise ReleaseRequestError("the commit must be a full SHA")
    version = request_version(request)
    tag = f"v{version}"
    if project_version(cmakelists) != version:
        raise ReleaseRequestError(f"the request names {version}, but CMakeLists.txt declares {project_version(cmakelists)}")
    if tag in retired(retired_tags):
        raise ReleaseRequestError(f"{tag} is retired and is never reused")
    if tag_exists:
        raise ReleaseRequestError(f"{tag} already exists; a release tag is never moved")
    missing = unmet(required_contexts(branch_rules), check_runs(pages), commit)
    if missing:
        raise ReleaseRequestError("required checks have not passed: " + "; ".join(missing))
    remaining = preflight_remaining(preflight, tag, now or datetime.now(timezone.utc))
    if remaining < PREFLIGHT_MARGIN:
        raise ReleaseRequestError(
            f"the immutable-release preflight has {remaining} of its window left, under {PREFLIGHT_MARGIN}; "
            "refresh it (scripts/refresh_immutable_release_preflight.py) and land it again, and the tag "
            "is made then")
    return tag


def freeze_ruleset(tag: str) -> dict[str, Any]:
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
        raise ReleaseRequestError("the tag must be vMAJOR.MINOR.PATCH")
    return {
        "name": f"freeze-{tag}",
        "target": "tag",
        "enforcement": "active",
        "bypass_actors": [],
        "conditions": {"ref_name": {"include": [f"refs/tags/{tag}"], "exclude": []}},
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            {"type": "update", "parameters": {"update_allows_fetch_and_merge": False}},
        ],
    }


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def attestation(root: Path, commit: str, attested_at: str, tag: str) -> dict[str, Any]:
    """The promotion attestation for `commit`, validated as the release-readiness gate will.

    With a parity completion record at `commit` it is the authority promotion
    attestation that binds it. Without one it is the mirror-promotion
    attestation (owner decision 2026-10-31, third sitting), which binds the
    newest mirror export receipt instead and states that parity is not yet
    proven; the verifier accepts that shape only for the Phase 4 promotion tag.
    """
    sys.path.insert(0, str(ROOT / "tools/provenance"))
    import verify_authority_expansion as vae  # noqa: PLC0415

    if not SHA.fullmatch(commit):
        raise ReleaseRequestError("the commit must be a full SHA")
    tree = git(root, "rev-parse", f"{commit}^{{tree}}")
    try:
        acknowledgement = (root / EXPANSION / "exact-boundary-acknowledgement-1.json").read_bytes()
    except OSError as error:
        raise ReleaseRequestError(f"a promotion record is missing at {commit}: {error}") from error
    amendment = json.loads((root / EXPANSION / "exact-boundary-amendment-1.json").read_text(encoding="utf-8"))
    data: dict[str, Any] = {
        "schema_version": 1,
        "state": "attested",
        "attested_at": attested_at,
        "attested_by": vae.RELEASE_PIPELINE_ATTESTER,
        "authority_repository": "Generous-Corp/vellum",
        "delivery_repository": "danielraffel/vellum",
        "promotion_mode": "repository-transfer",
        "authority_commit": commit,
        "delivery_commit": commit,
        "authority_tree": tree,
        "delivery_tree": tree,
        "exact_boundary_amendment_id": "full-design-import-render-v1-exact-boundary-amendment-1",
        "exact_boundary_amendment_sha256": vae.EXPECTED_BOUNDARY_AMENDMENT_SHA256,
        "exact_boundary_acknowledgement_id": "full-design-import-render-v1-exact-boundary-acknowledgement-1",
        "exact_boundary_acknowledgement_sha256": hashlib.sha256(acknowledgement).hexdigest(),
    }
    if git_exists(root, commit, vae.PARITY_COMPLETION_PATH.as_posix()):
        completion = git_show(root, commit, vae.PARITY_COMPLETION_PATH.as_posix())
        data.update({
            "kind": vae.AUTHORITY_PROMOTION_KIND,
            "attestation_id": "full-design-import-render-v1-authority-promotion-1",
            "parity_completion_id": "full-design-import-render-v1-parity-completion-1",
            "parity_completion_sha256": hashlib.sha256(completion).hexdigest(),
            "parity_release_source_commit": commit,
        })
        errors = (vae.validate_authority_promotion_attestation(data, amendment, delivery_retired=True)
                  + vae.validate_promotion_completion_digest(root, data, commit))
    else:
        if tag not in vae.MIRROR_PROMOTION_RELEASE_TAGS:
            raise ReleaseRequestError(
                f"{tag} needs a parity completion record at {commit}; only "
                f"{', '.join(sorted(vae.MIRROR_PROMOTION_RELEASE_TAGS))} may ship as a mirror promotion")
        try:
            index = json.loads(git_show(root, commit, vae.MIRROR_RECEIPT_INDEX_PATH))
            receipt = index["receipts"][-1]
            receipt_bytes = git_show(root, commit, receipt)
        except (subprocess.CalledProcessError, ValueError, KeyError, IndexError, TypeError) as error:
            raise ReleaseRequestError(f"no mirror export receipt is recorded at {commit}: {error}") from error
        data.update({
            "kind": vae.MIRROR_PROMOTION_KIND,
            "attestation_id": vae.MIRROR_PROMOTION_ATTESTATION_ID,
            "mirror_receipt": receipt,
            "mirror_receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest(),
            "release_source_commit": commit,
            "parity": vae.MIRROR_PROMOTION_PARITY,
        })
        errors = (vae.validate_mirror_promotion_attestation(data, amendment, delivery_retired=True)
                  + vae.validate_mirror_promotion_release(root, data, commit, tag))
    errors += vae.validate_promotion_acknowledgement_digest(root, data)
    if errors:
        raise ReleaseRequestError("the promotion attestation would not verify: " + "; ".join(errors))
    return data


def git_exists(root: Path, commit: str, path: str) -> bool:
    return subprocess.run(["git", "-C", str(root), "cat-file", "-e", f"{commit}:{path}"],
                          capture_output=True).returncode == 0


def git_show(root: Path, commit: str, path: str) -> bytes:
    return subprocess.run(["git", "-C", str(root), "show", f"{commit}:{path}"], capture_output=True,
                          check=True).stdout


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("check")
    one.add_argument("--request", type=Path, default=ROOT / "provenance/release-request.json")
    one.add_argument("--cmakelists", type=Path, default=ROOT / "CMakeLists.txt")
    one.add_argument("--retired-tags", type=Path, default=ROOT / "provenance/retired-release-tags.json")
    one.add_argument("--commit", required=True)
    one.add_argument("--tag-exists", choices=("true", "false"), required=True)
    one.add_argument("--branch-rules", type=Path, required=True)
    one.add_argument("--check-runs", type=Path, required=True)
    one.add_argument("--preflight", type=Path, default=ROOT / PREFLIGHT)
    attest = sub.add_parser("attestation")
    attest.add_argument("--commit", required=True)
    attest.add_argument("--attested-at", required=True)
    attest.add_argument("--tag", required=True)
    freeze = sub.add_parser("freeze")
    freeze.add_argument("--tag", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "freeze":
            print(json.dumps(freeze_ruleset(args.tag), indent=2, sort_keys=True))
            return 0
        if args.command == "attestation":
            print(json.dumps(attestation(ROOT, args.commit, args.attested_at, args.tag), indent=2, sort_keys=True))
            return 0
        tag = check(request=load(args.request), cmakelists=args.cmakelists.read_text(encoding="utf-8"),
                    retired_tags=load(args.retired_tags), commit=args.commit,
                    tag_exists=args.tag_exists == "true", branch_rules=load(args.branch_rules),
                    pages=load(args.check_runs), preflight=load(args.preflight))
    except (OSError, ReleaseRequestError) as error:
        print(f"release request refused: {error}", file=sys.stderr)
        return 1
    print(tag)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
