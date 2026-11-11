"""Vellum-native paths: automatic framework-only resolutions for the observatory.

`provenance/pulp-observatory/vellum-native-paths.json` lists Vellum paths that
have no Pulp counterpart, each with symbols that must not exist in Pulp. A
Vellum observation whose every mapped and transitive path is listed, that
carries no schema or API delta, is not security or P0, and touches nothing
under framework/, is resolved framework-only by reconcile. The resolution's
owner is `auto:vellum-native-paths@<entry ids>`: a policy that two reviews
approved made the decision, not a person, and the record says so. Any later
resolution overrides it.

Nothing here is trusted from the event itself. `verify` re-derives, for every
effective automatic resolution, that the observation still qualifies under the
current list, and that every entry's symbols are still absent from Pulp at the
observatory cursor. An entry that goes stale, or an automatic resolution that
no longer qualifies, fails verify until a person writes a later resolution or
the list is corrected.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

if __package__:
    from .observatory_common import OBSERVATORY, ObservatoryError, git
else:
    from observatory_common import OBSERVATORY, ObservatoryError, git

NATIVE_PATHS_PATH = OBSERVATORY / "vellum-native-paths.json"
AUTO_PREFIX = "auto:vellum-native-paths@"
ENTRY_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,62}")
AUTO_OWNER_RE = re.compile(r"auto:vellum-native-paths@[a-z0-9][a-z0-9-]{1,62}(?:,[a-z0-9][a-z0-9-]{1,62})*")
HUMAN_OWNER_RE = re.compile(r"@[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
SYMBOL_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,80}")
# Never automatic: these trees carry code exported from Pulp, where a Vellum
# change can be a real port obligation.
NEVER_NATIVE_PREFIXES = ("framework/", "mirror/")
AUTO_RATIONALE = (
    "auto-classified framework-only: every mapped and transitive path matches "
    "provenance/pulp-observatory/vellum-native-paths.json entries {ids}, whose "
    "symbols are absent from Pulp; no schema or API delta, not security or P0. "
    "Policy approved by its two-review landing and DECISIONS df7573679. Any "
    "later resolution overrides this one."
)


def load_native_paths(root: Path) -> list[dict[str, object]]:
    """The validated entries, or [] when the file is absent."""
    path = root / NATIVE_PATHS_PATH
    if not path.is_file():
        return []
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ObservatoryError(f"{NATIVE_PATHS_PATH} is unreadable: {error}") from error
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ObservatoryError(f"{NATIVE_PATHS_PATH} must be schema_version 1")
    entries = document.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ObservatoryError(f"{NATIVE_PATHS_PATH} needs a non-empty entries list")
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"id", "paths", "absent_in_pulp", "reason"}:
            raise ObservatoryError(f"{NATIVE_PATHS_PATH} entry fields must be id, paths, absent_in_pulp, reason")
        entry_id = entry["id"]
        if not isinstance(entry_id, str) or not ENTRY_ID_RE.fullmatch(entry_id) or entry_id in seen:
            raise ObservatoryError(f"{NATIVE_PATHS_PATH} entry id is invalid or repeated: {entry_id!r}")
        seen.add(entry_id)
        paths = entry["paths"]
        if not isinstance(paths, list) or not paths or paths != sorted(set(paths)):
            raise ObservatoryError(f"{NATIVE_PATHS_PATH} {entry_id}: paths must be a sorted, unique, non-empty list")
        for item in paths:
            if (not isinstance(item, str) or not item or item.startswith(("/", "./")) or ".." in item.split("/")
                    or any(ch in item for ch in "*?[]") or item.startswith(NEVER_NATIVE_PREFIXES)):
                raise ObservatoryError(f"{NATIVE_PATHS_PATH} {entry_id}: path {item!r} must be an exact file "
                                       "or a directory ending in /, outside framework/ and mirror/")
        symbols = entry["absent_in_pulp"]
        if not isinstance(symbols, list) or not symbols \
                or not all(isinstance(s, str) and SYMBOL_RE.fullmatch(s) for s in symbols):
            raise ObservatoryError(f"{NATIVE_PATHS_PATH} {entry_id}: absent_in_pulp must list at least one "
                                   "identifier; an empty list would check nothing")
        if not isinstance(entry["reason"], str) or not entry["reason"].strip():
            raise ObservatoryError(f"{NATIVE_PATHS_PATH} {entry_id}: reason is required")
    return entries


def entry_matches(entry: dict[str, object], path: str) -> bool:
    return any(path == item or (item.endswith("/") and path.startswith(item)) for item in entry["paths"])


def qualifying_entries(observation: dict[str, object], entries: list[dict[str, object]]) -> list[str] | None:
    """The sorted entry ids covering the observation, or None when it does not qualify."""
    if not entries or not str(observation.get("source_repository", "")).endswith("/vellum"):
        return None
    # include_dependency_deltas carries ordinary C++ headers and include paths, which
    # delta_paths does not count as schema/API deltas but which can change a public API.
    if observation.get("schema_api_deltas") or observation.get("include_dependency_deltas") \
            or observation.get("class") == "security" \
            or observation.get("severity") == "P0":
        return None
    paths = sorted(set(observation.get("mapped_paths") or []) | set(observation.get("transitive_paths") or []))
    if not paths:
        return None
    used: set[str] = set()
    for path in paths:
        if path.startswith(NEVER_NATIVE_PREFIXES):
            return None
        matching = [str(entry["id"]) for entry in entries if entry_matches(entry, path)]
        if not matching:
            return None
        used.add(matching[0])
    return sorted(used)


def auto_resolution(observation: dict[str, object], entries: list[dict[str, object]],
                    created_at: str) -> dict[str, object] | None:
    ids = qualifying_entries(observation, entries)
    if ids is None:
        return None
    return {
        "schema_version": 1,
        "event_id": f"resolution-{observation['event_id']}",
        "kind": "resolution",
        "created_at": created_at,
        "resolves": observation["event_id"],
        "disposition": "framework-only",
        "rationale": AUTO_RATIONALE.format(ids=", ".join(ids)),
        "owner": AUTO_PREFIX + ",".join(ids),
        "linked_commits": [str(observation["source_commit"])],
        "linked_pull_requests": [],
        "contract_tests": list(observation["contract_tests"]),
        "shared_contract_release_blocker": False,
        "effort_minutes": 0,
    }


def validate_owner(event: dict[str, object], event_id: str) -> None:
    """A person's @handle, or the automatic owner on a framework-only resolution only."""
    owner = event.get("owner")
    if not isinstance(owner, str) or not owner:
        raise ObservatoryError(f"event owner is required: {event_id}")
    if owner.startswith("auto:"):
        if event.get("kind") != "resolution" or event.get("disposition") != "framework-only":
            raise ObservatoryError(f"an automatic owner may only resolve framework-only: {event_id}")
        if not AUTO_OWNER_RE.fullmatch(owner):
            raise ObservatoryError(f"automatic owner is malformed: {event_id}")
        return
    if not HUMAN_OWNER_RE.fullmatch(owner):
        raise ObservatoryError(f"event owner must be an @handle or {AUTO_PREFIX}<entry ids>: {event_id}")


def stale_entries(entries: list[dict[str, object]], pulp_repo: Path | None, pulp_target: str | None) -> list[str]:
    """Entries whose absent_in_pulp symbols appear in Pulp at the target."""
    if not entries:
        return []
    if pulp_repo is None or not pulp_target:
        return [f"{NATIVE_PATHS_PATH} cannot be checked without the Pulp repository and target"]
    git(pulp_repo, "rev-parse", "--verify", "--quiet", f"{pulp_target}^{{commit}}")
    found = []
    for entry in entries:
        for symbol in entry["absent_in_pulp"]:
            done = subprocess.run(["git", "grep", "-l", "-w", "-F", "-e", str(symbol), pulp_target, "--"],
                                  cwd=pulp_repo, capture_output=True, text=True)
            # git grep exits 1 for no match; anything else is a failure, never a pass.
            if done.returncode not in (0, 1):
                raise ObservatoryError(f"cannot search Pulp for {symbol}: {done.stderr.strip()}")
            if done.returncode == 0 and done.stdout.strip():
                first = done.stdout.splitlines()[0].split(":", 1)[-1]
                found.append(f"{NATIVE_PATHS_PATH} entry {entry['id']} is stale: Pulp {pulp_target[:12]} "
                             f"has {symbol} in {first}")
    return found


def invalid_auto_resolutions(effective: list[dict[str, object]], entries: list[dict[str, object]]) -> list[str]:
    """Effective automatic resolutions the current list no longer supports."""
    problems = []
    for observation in effective:
        owner = str(observation.get("owner", ""))
        if not owner.startswith("auto:"):
            continue
        claimed = owner[len(AUTO_PREFIX):].split(",")
        ids = qualifying_entries(observation, entries)
        if observation.get("disposition") != "framework-only" or ids is None or not set(ids) <= set(claimed):
            problems.append(f"automatic resolution of {observation['event_id']} is no longer supported by "
                            f"{NATIVE_PATHS_PATH}; add a later resolution or correct the list")
    return problems
