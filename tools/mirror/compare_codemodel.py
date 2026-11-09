#!/usr/bin/env python3
"""Compare per-source compile groups between Pulp's build and Vellum's mirror build.

The package plan reconstructs Pulp's per-source compile options with a static
reading of Pulp's CMake (source_compile_options.py). This is the check behind
that reading: CMake itself, through the File API codemodel-v2 reply of each
build directory, says how every exported source is compiled.

    compare_codemodel.py --pulp-build <pulp>/build --vellum-build <vellum>/build-pkg \
        --vellum-root <vellum> [--json OUT]

Both build directories must have been configured with a codemodel-v2 query
(an empty file at .cmake/api/v1/query/codemodel-v2). The newest landed export
receipt names the exported sources and, through its closures, the targets.

For every exported source, in each build, the source's compile group is
reduced to what the source itself contributes, and the two must be equal:

* language;
* source-specific fragments: the group's compile-command fragments, minus
  fragment 0 (the language flags CMake composes from CMAKE_<LANG>_FLAGS, the
  build type, the language standard, the architecture, the deployment target
  and -fPIC), minus every fragment the target's base group for that language
  also carries;
* source-specific defines and include directories, by the same difference.

The base group of a target and language is the group of that language with
the most sources. What every source of a target and language shares is
target-level by construction: Vellum's targets legitimately differ from
Pulp's there (Vellum adds -ffile-prefix-map, its deployment-target and
_LIBCPP_DISABLE_AVAILABILITY handling and the host-owned Dawn definition;
Pulp adds its warning flags), so target-level flags are not compared. Path
prefixes are normalized: each build's source root becomes <src> (Vellum's
mirror/ prefix is dropped) and its build directory becomes <build>.

A source compiled in one build and not the other, or in a different language,
also fails.

What this cannot see: a property set on every source a target compiles in one
language. Such a group is the base group itself, so its options read as
target-level here; the static evaluator, which reads the calls, still sees
it. Exit status: 0 every exported source matches, 1 a mismatch, 2 the
evidence is unusable.
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from collections import Counter
from pathlib import Path
from typing import Any


class Unusable(RuntimeError):
    pass


def load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise Unusable(f"cannot read {path}: {error}") from error


def codemodel(build: Path) -> tuple[Path, dict[str, Any]]:
    reply = build / ".cmake/api/v1/reply"
    indexes = sorted(reply.glob("index-*.json"))
    if not indexes:
        raise Unusable(f"{build} has no File API reply; configure it with a codemodel-v2 query")
    index = load(indexes[-1])
    for response in index.get("reply", {}).values():
        if isinstance(response, dict) and response.get("kind") == "codemodel":
            return reply, load(reply / response["jsonFile"])
    raise Unusable(f"{build}'s File API reply has no codemodel")


class Normalizer:
    def __init__(self, source: str, build: str, strip_prefix: str = "") -> None:
        self.source = source.rstrip("/")
        self.build = build.rstrip("/")
        self.strip_prefix = strip_prefix

    def path(self, value: str) -> str:
        """A source path relative to the Pulp tree."""
        if value.startswith(self.source + "/"):
            value = value[len(self.source) + 1:]
        if self.strip_prefix and value.startswith(self.strip_prefix):
            value = value[len(self.strip_prefix):]
        return value

    def text(self, value: str) -> str:
        # The build directory may sit inside the source tree: replace it first.
        value = value.replace(self.build, "<build>")
        if self.strip_prefix:
            value = value.replace(f"{self.source}/{self.strip_prefix.rstrip('/')}", "<src>")
        return value.replace(self.source, "<src>")


def tokens(fragments: list[dict[str, Any]], normalize: Normalizer) -> Counter:
    out: Counter = Counter()
    for fragment in fragments[1:]:
        for token in shlex.split(fragment.get("fragment", "")):
            out[normalize.text(token)] += 1
    return out


def reduced(build: Path, normalize: Normalizer, targets: set[str]) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """Source path -> {target, language, fragments, defines, includes} deltas."""
    reply, model = codemodel(build)
    configurations = model.get("configurations") or []
    if len(configurations) != 1:
        raise Unusable(f"{build} is not a single-configuration build")
    found_targets: set[str] = set()
    sources: dict[str, dict[str, Any]] = {}
    for entry in configurations[0].get("targets", []):
        if entry.get("name") not in targets:
            continue
        found_targets.add(entry["name"])
        target = load(reply / entry["jsonFile"])
        groups = target.get("compileGroups", [])
        counts = Counter(s["compileGroupIndex"] for s in target.get("sources", []) if "compileGroupIndex" in s)
        base: dict[str, int] = {}
        for index, group in enumerate(groups):
            language = group.get("language")
            if language not in base or counts[index] > counts[base[language]]:
                base[language] = index

        def parts(group: dict[str, Any]) -> tuple[Counter, Counter, Counter]:
            return (tokens(group.get("compileCommandFragments", []), normalize),
                    Counter(d["define"] for d in group.get("defines", [])),
                    Counter(normalize.text(i["path"]) for i in group.get("includes", [])))

        for source in target.get("sources", []):
            path = normalize.path(source["path"])
            if "compileGroupIndex" not in source:
                continue
            group = groups[source["compileGroupIndex"]]
            mine = parts(group)
            theirs = parts(groups[base[group["language"]]])
            delta = {}
            for name, a, b in zip(("fragments", "defines", "includes"), mine, theirs):
                delta[name] = {"added": sorted((a - b).elements()), "removed": sorted((b - a).elements())}
            row = {"target": entry["name"], "language": group["language"], **delta}
            if path in sources and sources[path] != row:
                raise Unusable(f"{path} is compiled differently by two targets in {build}")
            sources[path] = row
    return sources, found_targets


def compare(pulp_build: Path, vellum_build: Path, vellum_root: Path) -> dict[str, Any]:
    index = load(vellum_root / "provenance/mirror/receipts/index.json")
    receipt_path = index["receipts"][-1]
    receipt = load(vellum_root / receipt_path)
    exported = {row["pulp_path"] for row in receipt["files"]}
    targets: set[str] = set()
    for unit in receipt["units"]:
        closure = load(vellum_root / unit["closure_manifest"])
        targets |= {target["name"] for target in closure.get("targets", [])}

    def paths(build: Path) -> dict[str, str]:
        _, model = codemodel(build)
        return model["paths"]

    pulp_paths, vellum_paths = paths(pulp_build), paths(vellum_build)
    pulp, pulp_targets = reduced(pulp_build, Normalizer(pulp_paths["source"], pulp_paths["build"]), targets)
    vellum, vellum_targets = reduced(
        vellum_build, Normalizer(vellum_paths["source"], vellum_paths["build"], "mirror/"), targets)
    if pulp_targets != targets or vellum_targets != targets:
        raise Unusable(f"targets missing from a codemodel: Pulp {sorted(targets - pulp_targets)}, "
                       f"Vellum {sorted(targets - vellum_targets)}")
    mismatches = []
    compiled = 0
    for path in sorted(exported):
        a, b = pulp.get(path), vellum.get(path)
        if a is None and b is None:
            continue
        compiled += 1
        if a is None or b is None or {k: v for k, v in a.items()} != {k: v for k, v in b.items()}:
            mismatches.append({"source": path, "pulp": a, "vellum": b})
    source_level = sorted(
        path for path in exported
        if path in pulp and any(pulp[path][k]["added"] or pulp[path][k]["removed"]
                                for k in ("fragments", "defines", "includes")))
    return {
        "schema": "vellum.mirror-codemodel-comparison.v1",
        "receipt": receipt_path,
        "pulp_sha": receipt["pulp_sha"],
        "exported_files": len(exported),
        "targets": sorted(targets),
        "compiled_exported_sources": compiled,
        "not_compiled_exported_files": len(exported) - compiled,
        "pulp_source_level_groups": {path: pulp[path] for path in source_level},
        "mismatches": mismatches,
        "status": "match" if not mismatches else "mismatch",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pulp-build", type=Path, required=True)
    parser.add_argument("--vellum-build", type=Path, required=True)
    parser.add_argument("--vellum-root", type=Path, required=True)
    parser.add_argument("--json", type=Path, help="write the full comparison here")
    args = parser.parse_args(argv)
    try:
        report = compare(args.pulp_build.resolve(), args.vellum_build.resolve(), args.vellum_root.resolve())
    except Unusable as error:
        print(f"compare-codemodel: {error}", file=sys.stderr)
        return 2
    if args.json:
        args.json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"compare-codemodel: {report['status']}: {report['compiled_exported_sources']} compiled exported "
          f"sources compared ({report['not_compiled_exported_files']} exported files are not compiled), "
          f"{len(report['pulp_source_level_groups'])} with source-level groups in Pulp, "
          f"{len(report['mismatches'])} mismatch(es)")
    for mismatch in report["mismatches"][:20]:
        print(f"  {mismatch['source']}: pulp={json.dumps(mismatch['pulp'])} vellum={json.dumps(mismatch['vellum'])}")
    return 0 if not report["mismatches"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
