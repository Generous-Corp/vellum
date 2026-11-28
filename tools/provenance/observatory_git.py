"""Git-history discovery and derived observation fields for the observatory."""

from __future__ import annotations

import fnmatch
from functools import lru_cache
from pathlib import Path, PurePosixPath
import subprocess
import tempfile
from typing import Iterable

if __package__:
    from .observatory_common import ObservatoryError, SHA_RE, git, git_bytes
else:
    from observatory_common import ObservatoryError, SHA_RE, git, git_bytes


def require_commit(repo: Path, commit: str, field: str) -> None:
    require_commits(repo, (commit, field))


def require_commits(repo: Path, *commits: tuple[str, str]) -> None:
    for commit, field in commits:
        if not isinstance(commit, str) or not SHA_RE.fullmatch(commit):
            raise ObservatoryError(f"{field} must be an exact 40-character commit SHA")
    resolved = git(repo, "rev-parse", *(f"{commit}^{{commit}}" for commit, _ in commits)).splitlines()
    if len(resolved) != len(commits):
        raise ObservatoryError(f"git rev-parse returned an incomplete commit batch in {repo}")
    for (commit, field), oid in zip(commits, resolved):
        if oid != commit:
            raise ObservatoryError(f"{field} did not resolve exactly: {commit}")


def existing_objects(repo: Path, expressions: Iterable[str]) -> set[str]:
    """Return the object expressions Git can resolve and read."""
    queries = list(dict.fromkeys(expressions))
    if not queries:
        return set()
    answers = git(
        repo, "cat-file", "--batch-check=%(objectname)",
        input_bytes=("".join(f"{query}\n" for query in queries)).encode("utf-8"),
    ).splitlines()
    if len(answers) != len(queries):
        raise ObservatoryError(f"git cat-file returned an incomplete object batch in {repo}")
    return {query for query, answer in zip(queries, answers) if SHA_RE.fullmatch(answer)}


@lru_cache(maxsize=4)
def _parents_by_commit(repo: Path) -> dict[str, tuple[str, ...]]:
    return {}


def commit_parents(repo: Path, commit: str) -> tuple[str, ...]:
    parents = _parents_by_commit(repo)
    if commit not in parents:
        parents[commit] = tuple(git(repo, "rev-list", "--parents", "-n", "1", commit).split()[1:])
    return parents[commit]


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
    parents = commit_parents(repo, commit)
    if len(parents) < 2:
        return None
    commit_tree, *parent_trees = git(
        repo, "rev-parse", *(f"{rev}^{{tree}}" for rev in (commit, *parents))
    ).splitlines()
    if len(parent_trees) != len(parents):
        raise ObservatoryError(f"git rev-parse returned an incomplete tree batch in {repo}")
    for parent, tree in zip(parents, parent_trees):
        if is_ancestor(repo, cursor_from, parent) and tree == commit_tree:
            return parent
    return None


@lru_cache(maxsize=16)
def tree_entries(repo: Path, commit: str) -> dict[str, tuple[str, str]]:
    """Read modes and object IDs without opening blobs in a partial clone."""
    entries = {}
    output = git_bytes(repo, "ls-tree", "-r", "-t", "-z", commit)
    for row in output.split(b"\0")[:-1]:
        metadata, path = row.split(b"\t", 1)
        mode, _kind, oid = metadata.decode("ascii").split()
        entries[path.decode("utf-8", errors="surrogateescape")] = (mode, oid)
    return entries


def tree_entry_at(repo: Path, commit: str, path: str) -> tuple[str, str] | None:
    """The (mode, object id) of a path, including mode-only changes."""
    return tree_entries(repo, commit).get(path)


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
    is covered when both sides and the merge hold the same entry (the same fix
    landed twice), or when the merge holds exactly git's conflict-free
    three-way merge of the two sides (two pull requests editing different
    hunks of one file); a path only the merge changed, a hand-resolved path,
    or a path the merge did not carry over is a change of the merge's own and
    needs its own event.

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
    base, first, second, first_paths, second_paths, observable = sides
    # Symmetric rule, so it judges a queue merge (first = main, second = the
    # pull request) and an author's merge of main (first = the pull request,
    # second = main) alike: every observable path must carry exactly the entry
    # of the one side that changed it. A path both sides changed that the
    # merge does not hold as their clean three-way merge, a path the merge
    # changed that neither side did, and a path the merge did not carry over
    # from the side that changed it are all changes of the merge's own.
    for path in observable:
        on_first = path in first_paths
        on_second = path in second_paths
        merged = tree_entry_at(repo, commit, path)
        if on_first and on_second:
            first_entry = tree_entry_at(repo, first, path)
            second_entry = tree_entry_at(repo, second, path)
            if first_entry == second_entry and merged == first_entry:
                continue
            if not _is_clean_content_merge(repo, base, first, second, commit, path):
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


_REGULAR_MODES = frozenset({"100644", "100755"})


def _is_clean_content_merge(
    repo: Path, base: str | None, first: str, second: str, commit: str, path: str
) -> bool:
    """True when `commit` holds exactly git's conflict-free three-way merge of
    `path`, so the merge adds no content beyond the two sides' own changes.

    Two pull requests that edit different hunks of one file land through a
    queue merge whose blob matches neither side. Each side's observation
    already describes its own hunks, and the merged blob is a deterministic
    function of the base and those two sides. Anything else stays a change of
    the merge's own: a conflicted or hand-resolved file, a file added or
    deleted on either side, a symlink or submodule, or a mode both sides
    changed differently."""
    if base is None:
        return False
    entries = [tree_entry_at(repo, rev, path) for rev in (base, first, second, commit)]
    if any(entry is None or entry[0] not in _REGULAR_MODES for entry in entries):
        return False
    base_entry, first_entry, second_entry, merged_entry = entries
    if first_entry[0] == base_entry[0]:
        expected_mode = second_entry[0]
    elif second_entry[0] in (base_entry[0], first_entry[0]):
        expected_mode = first_entry[0]
    else:
        return False
    if merged_entry[0] != expected_mode:
        return False
    merged = _blob_bytes(repo, merged_entry[1])
    with tempfile.TemporaryDirectory() as scratch:
        names = []
        for label, entry in (("first", first_entry), ("base", base_entry), ("second", second_entry)):
            target = Path(scratch) / label
            target.write_bytes(_blob_bytes(repo, entry[1]))
            names.append(str(target))
        # merge-ort merges content with the histogram algorithm; plain
        # merge-file uses myers. Either result is a conflict-free merge of the
        # two sides, so a merge holding exactly one of them is covered.
        for algorithm in ("histogram", None):
            command = ["git", "merge-file", "-p"]
            if algorithm:
                command.append(f"--diff-algorithm={algorithm}")
            completed = subprocess.run(
                [*command, *names], cwd=repo, capture_output=True, check=False
            )
            if completed.returncode == 0 and completed.stdout == merged:
                return True
    return False


def _blob_bytes(repo: Path, object_id: str) -> bytes:
    completed = subprocess.run(
        ["git", "cat-file", "blob", object_id], cwd=repo, capture_output=True, check=False
    )
    if completed.returncode != 0:
        raise ObservatoryError(f"cannot read blob {object_id} in {repo}")
    return completed.stdout


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
    return set() if sides is None else sides[5]


def _merge_sides(
    repo: Path,
    commit: str,
    mappings: list[dict[str, object]] | None,
    transitive_rules: list[str] | None,
) -> tuple[str | None, str, str, set[str], set[str], set[str]] | None:
    parents = commit_parents(repo, commit)
    if len(parents) != 2:
        return None
    first, second = parents
    bases = git(repo, "merge-base", "--all", first, second).split()
    if not bases:
        return None
    base = bases[0]

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
    # A criss-cross history has no single base, so no three-way content merge
    # can be judged against it: the first element is None and only the exact
    # entry rules apply.
    unique_base = base if len(bases) == 1 else None
    return unique_base, first, second, first_paths, second_paths, observable


def path_matches(path: str, patterns: Iterable[str]) -> bool:
    return any(path.startswith(pattern) for pattern in patterns)


def transitive_match(path: str, rules: Iterable[str]) -> bool:
    name = PurePosixPath(path).name
    return any(
        fnmatch.fnmatch(name, rule) or fnmatch.fnmatch(path, rule)
        for rule in rules
    )


@lru_cache(maxsize=4)
def _diffs_by_commit(repo: Path) -> dict[str, list[dict[str, object]]]:
    return {}


_DIFF_ARGS = ("--name-status", "-r", "-M")


def load_range_diffs(repo: Path, commits: list[str]) -> None:
    """Read first-parent diffs in batches and reuse them across the scan."""
    cached = _diffs_by_commit(repo)
    missing = [commit for commit in commits if commit not in cached]
    for offset in range(0, len(missing), 512):
        batch = missing[offset:offset + 512]
        requests = []
        for commit in batch:
            parents = commit_parents(repo, commit)
            requests.append(" ".join((commit, *parents[:1])))
        output = git(
            repo, "diff-tree", "--stdin", "--always", "--root", *_DIFF_ARGS,
            input_bytes=("\n".join(requests) + "\n").encode("ascii"),
        )
        rows: dict[str, list[str]] = {}
        lines: list[str] | None = None
        for line in output.splitlines():
            if SHA_RE.fullmatch(line):
                lines = rows[line] = []
            elif line:
                if lines is None:
                    raise ObservatoryError(f"git diff-tree returned data before its first commit in {repo}")
                lines.append(line)
        if list(rows) != batch:
            raise ObservatoryError(f"git diff-tree returned an incomplete commit batch in {repo}")
        for commit, changes in rows.items():
            cached[commit] = _parse_diff_entries(commit, "\n".join(changes).strip().splitlines())


def parse_diff_entries(repo: Path, commit: str) -> list[dict[str, object]]:
    cached = _diffs_by_commit(repo)
    if commit not in cached:
        parents = commit_parents(repo, commit)
        revisions = (parents[0], commit) if parents else ("--root", commit)
        output = git(repo, "diff-tree", "--no-commit-id", *_DIFF_ARGS, *revisions)
        cached[commit] = _parse_diff_entries(commit, output.splitlines())
    return cached[commit]


def _parse_diff_entries(commit: str, lines: Iterable[str]) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for line in lines:
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
    require_commits(repo, (start, "scan start"), (target, "scan target"))
    require_ancestor(repo, start, target, "scan cursor")
    output = git(repo, "rev-list", "--parents", "--reverse", "--topo-order", f"{start}..{target}")
    parents_by_commit = _parents_by_commit(repo)
    commits = []
    for row in output.splitlines():
        commit, *parents = row.split()
        commits.append(commit)
        parents_by_commit[commit] = tuple(parents)
    return commits


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
