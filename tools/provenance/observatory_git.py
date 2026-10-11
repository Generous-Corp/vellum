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


def blob_at(repo: Path, commit: str, path: str) -> str | None:
    """The blob id of `path` at `commit`, or None when the path is absent."""
    completed = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{commit}:{path}"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def tree_entry_at(repo: Path, commit: str, path: str) -> tuple[str, str] | None:
    """The (mode, object id) of `path` at `commit`, or None when absent.

    The mode is part of the entry on purpose: a merge that keeps a file's
    content but drops its executable bit changes what lands on main just as a
    content edit does, and a blob-only comparison cannot see it.
    """
    line = git(repo, "ls-tree", commit, "--", path)
    if not line:
        return None
    mode, _kind, rest = line.split(None, 2)
    return mode, rest.split("\t", 1)[0]


def integration_merge_covered(
    repo: Path,
    commit: str,
    mappings: list[dict[str, object]] | None = None,
    transitive_rules: list[str] | None = None,
) -> bool:
    """True when a two-parent merge adds nothing beyond its two sides.

    A merge queue (or a plain fast-forward-free merge) lands a merge commit
    whose first parent is the branch and whose second parent is the pull
    request head; an author merging main into a pull request produces the
    mirror image. The merge is covered by the two sides' own observation
    events when every observable path changed since the merge base carries
    exactly the (mode, blob) entry of the side that changed it (a path absent
    on both counts as equal, so a deletion lands). A path both sides changed
    is covered only when both sides and the merge hold the same entry (the
    same fix landed twice); a path only the merge changed, or a path the merge
    did not carry over, is a change of the merge's own and needs its own event.

    Only paths that would create an observation matter when `mappings` is
    given: unmapped content can never need an event, so two entries that
    share an unmapped file do not make a batch merge uncovered. Transitive
    paths under a mapped root (build files) count, because the observation
    for a real change lists them. An octopus merge is never covered; it must
    carry its own event.
    """
    sides = _merge_sides(repo, commit, mappings, transitive_rules)
    if sides is None:
        return False
    first, second, first_paths, second_paths, observable = sides
    # Symmetric rule, so it judges a queue merge (first = main, second = the
    # pull request) and an author's merge of main (first = the pull request,
    # second = main) alike: every observable path must carry exactly the entry
    # of the one side that changed it. A path both sides changed differently,
    # a path the merge changed that neither side did, and a path the merge did
    # not carry over from the side that changed it are all changes of the
    # merge's own.
    for path in observable:
        on_first = path in first_paths
        on_second = path in second_paths
        merged = tree_entry_at(repo, commit, path)
        if on_first and on_second:
            first_entry = tree_entry_at(repo, first, path)
            if first_entry != tree_entry_at(repo, second, path) or merged != first_entry:
                return False
        elif on_second:
            if merged != tree_entry_at(repo, second, path):
                return False
        elif on_first:
            if merged != tree_entry_at(repo, first, path):
                return False
        else:
            return False
    return True


def merge_observable_paths(
    repo: Path,
    commit: str,
    mappings: list[dict[str, object]] | None = None,
    transitive_rules: list[str] | None = None,
) -> set[str]:
    """Observable paths a two-parent merge touches on either side or itself.

    Non-empty for a merge that is not an integration merge means the merge
    changed something observable, even when its diff against the first parent
    shows nothing (a merge that drops the other side's change)."""
    sides = _merge_sides(repo, commit, mappings, transitive_rules)
    return set() if sides is None else sides[4]


def _merge_sides(
    repo: Path,
    commit: str,
    mappings: list[dict[str, object]] | None,
    transitive_rules: list[str] | None,
) -> tuple[str, str, set[str], set[str], set[str]] | None:
    row = git(repo, "rev-list", "--parents", "-n", "1", commit).split()
    if len(row) != 3:
        return None
    first, second = row[1], row[2]
    base = git(repo, "merge-base", first, second)
    if not base:
        return None

    def changed(a: str, b: str) -> set[str]:
        return {
            line
            for line in git(
                repo, "diff-tree", "-r", "--name-only", "--no-renames", a, b
            ).splitlines()
            if line
        }

    merged_paths = changed(first, commit)
    first_paths = changed(base, first)
    second_paths = changed(base, second)
    observable = merged_paths | first_paths | second_paths
    if mappings is not None:
        # Judge the whole set at once, as the observation for a real change
        # would: a build file under a mapped root is transitive-only, so on its
        # own it never maps, but next to a mapped change it is listed under
        # `transitive_paths` and a merge that reverts it must not be covered.
        observation = mapped_change(
            [{"status": "M", "path": path} for path in sorted(observable)],
            mappings,
            "vellum",
            transitive_rules or [],
        )
        if observation is None:
            observable = set()
        else:
            observable = set(observation["mapped_paths"]) | set(
                observation["transitive_paths"]
            )
    return first, second, first_paths, second_paths, observable


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
