"""Git-history discovery and derived observation fields for the observatory."""

from __future__ import annotations

import fnmatch
from functools import lru_cache
from pathlib import Path, PurePosixPath
import subprocess
from typing import Iterable

if __package__:
    from .observatory_common import ObservatoryError, SHA_RE, git
else:
    from observatory_common import ObservatoryError, SHA_RE, git


def require_commit(repo: Path, commit: str, field: str) -> None:
    if not isinstance(commit, str) or not SHA_RE.fullmatch(commit):
        raise ObservatoryError(f"{field} must be an exact 40-character commit SHA")
    resolved = git(repo, "rev-parse", f"{commit}^{{commit}}")
    if resolved != commit:
        raise ObservatoryError(f"{field} did not resolve exactly: {commit}")


def require_ancestor(repo: Path, ancestor: str, descendant: str, field: str) -> None:
    completed = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=repo,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise ObservatoryError(f"{field}: {ancestor} is not an ancestor of {descendant}")


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    completed = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=repo,
        capture_output=True,
    )
    if completed.returncode not in (0, 1):
        raise ObservatoryError(
            f"cannot compare Git ancestry: {ancestor} -> {descendant}"
        )
    return completed.returncode == 0


def tree_identical_scanned_parent(
    repo: Path, commit: str, cursor_from: str
) -> str | None:
    """Return a scanned parent when a merge adds no tree beyond that parent."""
    row = git(repo, "rev-list", "--parents", "-n", "1", commit).split()
    if len(row) < 3:
        return None
    commit_tree = git(repo, "rev-parse", f"{commit}^{{tree}}")
    for parent in row[1:]:
        if (
            is_ancestor(repo, cursor_from, parent)
            and git(repo, "rev-parse", f"{parent}^{{tree}}") == commit_tree
        ):
            return parent
    return None


def path_matches(path: str, patterns: Iterable[str]) -> bool:
    return any(path.startswith(pattern) for pattern in patterns)


def transitive_match(path: str, rules: Iterable[str]) -> bool:
    name = PurePosixPath(path).name
    return any(
        fnmatch.fnmatch(name, rule) or fnmatch.fnmatch(path, rule)
        for rule in rules
    )


@lru_cache(maxsize=16384)
def parse_diff_entries(repo: Path, commit: str) -> list[dict[str, object]]:
    parents = git(repo, "rev-list", "--parents", "-n", "1", commit).split()
    arguments = ["diff-tree", "--no-commit-id", "--name-status", "-r", "-M"]
    if len(parents) == 1:
        arguments.append("--root")
        arguments.append(commit)
    else:
        arguments.extend([parents[1], commit])
    output = git(repo, *arguments)
    entries: list[dict[str, object]] = []
    for line in output.splitlines():
        pieces = line.split("\t")
        status = pieces[0]
        if status.startswith(("R", "C")):
            if len(pieces) != 3:
                raise ObservatoryError(
                    f"malformed rename/copy entry for {commit}: {line}"
                )
            entries.append(
                {"status": status, "old_path": pieces[1], "path": pieces[2]}
            )
        else:
            if len(pieces) != 2:
                raise ObservatoryError(f"malformed diff entry for {commit}: {line}")
            entries.append({"status": status, "path": pieces[1]})
    return entries


@lru_cache(maxsize=16384)
def patch_id(repo: Path, commit: str) -> str | None:
    patch = subprocess.run(
        ["git", "show", "--pretty=format:", "--no-ext-diff", "--binary", commit],
        cwd=repo,
        capture_output=True,
        check=True,
    ).stdout
    if not patch.strip():
        return None
    completed = subprocess.run(
        ["git", "patch-id", "--stable"],
        cwd=repo,
        input=patch,
        capture_output=True,
        check=True,
    )
    text = completed.stdout.decode().strip()
    return text.split()[0] if text else None


def mapped_change(
    entries: list[dict[str, object]],
    mappings: list[dict[str, object]],
    source: str,
    transitive_rules: list[str],
) -> dict[str, object] | None:
    path_key = "pulp_paths" if source == "pulp" else "vellum_paths"
    all_paths = sorted(
        {
            str(entry[side])
            for entry in entries
            for side in ("old_path", "path")
            if side in entry
        }
    )
    matched: list[dict[str, object]] = []
    for mapping in mappings:
        patterns = mapping.get(path_key)
        if not isinstance(patterns, list):
            raise ObservatoryError(f"mapping {mapping.get('id')} lacks {path_key}")
        direct = [path for path in all_paths if path_matches(path, patterns)]
        if direct:
            matched.append(mapping)
    if not matched:
        return None
    direct_patterns = [
        pattern for mapping in matched for pattern in mapping[path_key]
    ]
    mapped_paths = sorted(
        path for path in all_paths if path_matches(path, direct_patterns)
    )
    roots = {
        PurePosixPath(pattern).parts[0]
        for pattern in direct_patterns
        if PurePosixPath(pattern).parts
    }
    transitive = sorted(
        path
        for path in all_paths
        if path not in mapped_paths
        and PurePosixPath(path).parts
        and (
            PurePosixPath(path).parts[0] in roots
            or (source == "pulp" and path == "CMakeLists.txt")
        )
        and transitive_match(path, transitive_rules)
    )
    renames = []
    for entry in entries:
        if str(entry["status"]).startswith(("R", "C")):
            old = str(entry["old_path"])
            new = str(entry["path"])
            if (
                old in mapped_paths
                or new in mapped_paths
                or old in transitive
                or new in transitive
            ):
                score = int(str(entry["status"])[1:] or "0")
                renames.append(
                    {
                        "old_path": old,
                        "new_path": new,
                        "similarity_percent": score,
                    }
                )
    tests = sorted(
        {
            str(test)
            for mapping in matched
            for test in mapping.get("contract_tests", [])
        }
    )
    contracts = sorted(str(mapping["id"]) for mapping in matched)
    return {
        "mapped_contracts": contracts,
        "mapped_paths": mapped_paths,
        "transitive_paths": transitive,
        "rename_candidates": renames,
        "contract_tests": tests,
        "contract_keys": contracts,
    }


def classify_paths(paths: list[str]) -> str:
    lowered = "\n".join(paths).lower()
    if any(
        token in lowered
        for token in ("security", "crypto", "license", "provenance")
    ):
        return "security"
    if any(
        token in lowered for token in ("schema", "design_ir", "design-ir", ".d.ts")
    ):
        return "schema"
    if any(token in lowered for token in ("import", "figma", "reimport")):
        return "importer"
    if any(
        token in lowered for token in ("render", "graphics", "canvas", "skia", "dawn")
    ):
        return "rendering"
    if any(
        token in lowered for token in ("platform", "window_host", "apps/", "macos")
    ):
        return "platform"
    if any(token in lowered for token in ("test", "fixture", "scenario")):
        return "test"
    if any(
        token in lowered
        for token in ("cmakelists", ".cmake", "package.json", "package-lock")
    ):
        return "build"
    if any(token in lowered for token in ("readme", "docs/", ".md")):
        return "documentation"
    return "correctness"


def delta_paths(paths: list[str]) -> tuple[list[str], list[str]]:
    includes = sorted(
        path
        for path in paths
        if any(
            token in path.lower()
            for token in (
                "cmakelists",
                ".cmake",
                "/include/",
                ".hpp",
                ".h",
                "package.json",
                "package-lock",
            )
        )
    )
    schemas = sorted(
        path
        for path in paths
        if any(
            token in path.lower()
            for token in ("schema", "design_ir", "design-ir", ".d.ts", "contract")
        )
    )
    return includes, schemas


def commits_between(repo: Path, start: str, target: str) -> list[str]:
    require_commit(repo, start, "scan start")
    require_commit(repo, target, "scan target")
    require_ancestor(repo, start, target, "scan cursor")
    output = git(repo, "rev-list", "--reverse", "--topo-order", f"{start}..{target}")
    return output.splitlines() if output else []


def observation_for_commit(
    *,
    source: str,
    repository: str,
    repo: Path,
    commit: str,
    cursor_from: str,
    cursor_to: str,
    discovered_at: str,
    mappings: list[dict[str, object]],
    transitive_rules: list[str],
) -> dict[str, object] | None:
    if (
        source == "vellum"
        and tree_identical_scanned_parent(repo, commit, cursor_from) is not None
    ):
        return None
    entries = parse_diff_entries(repo, commit)
    mapped = mapped_change(entries, mappings, source, transitive_rules)
    if mapped is None:
        return None
    paths = sorted(set(mapped["mapped_paths"]) | set(mapped["transitive_paths"]))
    include_deltas, schema_deltas = delta_paths(paths)
    direction = "Pulp-to-framework" if source == "pulp" else "framework-to-Pulp"
    prefix = "pulp" if source == "pulp" else "vellum"
    return {
        "schema_version": 1,
        "event_id": f"{prefix}-{commit}",
        "kind": "observation",
        "source_repository": repository,
        "source_commit": commit,
        "discovered_at": discovered_at,
        "scan_cursor": {"from_commit": cursor_from, "to_commit": cursor_to},
        "direction": direction,
        **mapped,
        "patch_id": patch_id(repo, commit),
        "include_dependency_deltas": include_deltas,
        "schema_api_deltas": schema_deltas,
        "class": classify_paths(paths),
        "severity": None,
        "disposition": "pending",
        "rationale": "Human classification required; discovery is not a port decision.",
        "owner": "@danielraffel",
        "linked_commits": [],
        "linked_pull_requests": [],
        "shared_contract_release_blocker": False,
        "effort_minutes": 0,
    }
