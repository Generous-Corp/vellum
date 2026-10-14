#!/usr/bin/env python3
"""D6 rung 2: Pulp's own tests, composed against the installed Vellum package.

The identity lane (rung 1) proves the exported tree equals the origin
closure. This lane proves the *package* built from that tree behaves like the
origin. For each origin test group it takes the origin's own test sources at
the exported commit, compiles them against the installed package through
`find_package`, runs them, and compares the Catch2 results test by test with
the origin's in-tree run of the same sources.

- The reference is the origin's own test binary, built at the origin commit in
  the configuration the closures record. Its file tags (`-#`) choose which
  sources run.
- The candidate is a consumer project generated here. It links only the
  package targets the group names, plus Catch2, and adds no compile
  definitions of its own.
- Identity first: the lane is `blocked` unless rung 1 passes for the same
  export (closure manifests + exported commit).
- Package binding: the installed package must carry the export receipt of the
  exported commit, byte for byte, and that receipt must name the origin
  commit. A package built from another export is `blocked`.
- Configuration: the reference build must match the closures' recorded
  configuration (build type, Skia on/off).
- Single definition: every strong global symbol in the package's libraries
  has one owning library, and no candidate image defines a symbol the package
  exports (that would be a second copy in one image).
- Single provider: no candidate image defines Skia/Dawn itself; the package's
  library is its only provider. The census is proven able to see a provider by
  finding the statically linked one in the reference binary.

`calibrate` runs the comparison and then seeded controls that must each make
it non-accepting. The lane is only as trustworthy as its controls.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import parity_harness as ph  # noqa: E402

SCHEMA = "vellum.parity.packaging-receipt.v2"
CALIBRATION_SCHEMA = "vellum.parity.packaging-calibration.v2"
RECEIPT_SCHEMA = "pulp.vellum.export-receipt.v2"
# Definitions that mark an image as carrying its own Skia/Dawn provider.
PROVIDER_SYMBOL = re.compile(r"^(_wgpuCreateInstance|__ZN8SkCanvas[0-9A-Za-z_]+|__ZN9SkSurface[0-9A-Za-z_]+)$")


class LaneError(ph.HarnessError):
    pass


def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", **kwargs)


# ── symbols ───────────────────────────────────────────────────────────────


def strong_definitions(binary: Path) -> set[str]:
    """Strong external definitions of an archive, dylib or executable.
    Weak definitions (inline functions, template instantiations) are
    coalesced and legitimately appear in several images."""
    result = run(["nm", "-gUm", str(binary)])
    if result.returncode != 0:
        raise LaneError(f"nm failed on {binary.name}: {result.stderr[-300:]}")
    symbols = set()
    for line in result.stdout.splitlines():
        if "external" not in line or "weak" in line or "(undefined)" in line:
            continue
        symbols.add(line.rsplit(" ", 1)[-1])
    return symbols


def library_census(libraries: list[Path]) -> dict[str, Any]:
    """Every strong global definition must come from exactly one library."""
    owners: dict[str, set[str]] = {}
    for library in libraries:
        for symbol in strong_definitions(library):
            owners.setdefault(symbol, set()).add(library.name)
    duplicates = sorted(s for s, o in owners.items() if len(o) > 1)
    return {"libraries": [lib.name for lib in libraries], "symbols": len(owners),
            "duplicates": duplicates[:25], "duplicate_count": len(duplicates),
            "_exported": set(owners)}


def image_census(binary: Path, package_symbols: set[str]) -> dict[str, Any]:
    """A candidate image may not define what the package exports, nor carry
    its own Skia/Dawn provider."""
    defined = strong_definitions(binary)
    shadowed = sorted(defined & package_symbols)
    providers = sorted(s for s in defined if PROVIDER_SYMBOL.match(s))
    return {"defined": len(defined), "shadowed_count": len(shadowed), "shadowed": shadowed[:10],
            "provider_count": len(providers), "providers": providers[:5]}


def linked_libraries(binary: Path) -> list[str]:
    result = run(["otool", "-L", str(binary)])
    return [line.strip().split(" ", 1)[0] for line in result.stdout.splitlines()[1:] if line.strip()]


# ── identity and package binding ──────────────────────────────────────────


def identity_result(spec: dict[str, Any], exported_commit: str | None = None) -> dict[str, Any]:
    closures = [ph.load_json(Path(p)) for p in spec["closures"]]
    manifest = ph.merge_manifests(closures)
    origin = ph.git_tree_blobs(Path(spec["origin_repo"]), spec["origin_commit"], ph.manifest_paths(manifest))
    exported = ph.git_exported_blobs(Path(spec["exported_repo"]), exported_commit or spec["exported_commit"],
                                     spec.get("exported_prefix", "mirror/"))
    return ph.compare_identity(manifest, origin, exported)


def receipt_binding(spec: dict[str, Any], prefix: Path) -> list[str]:
    """The installed package must have been built from the export the
    identity lane just checked."""
    receipts = sorted((prefix / "mirror" / "share" / "provenance").glob("*.json"))
    if len(receipts) != 1:
        return [f"package carries {len(receipts)} export receipts; expected exactly one"]
    installed = receipts[0].read_bytes()
    committed = subprocess.run(["git", "-C", spec["exported_repo"], "show",
                                f"{spec['exported_commit']}:{spec['export_receipt']}"], capture_output=True)
    if committed.returncode != 0:
        return [f"exported commit has no {spec['export_receipt']}"]
    reasons = []
    if installed != committed.stdout:
        reasons.append(f"package receipt {receipts[0].name} differs from {spec['export_receipt']} at the exported commit")
    receipt = json.loads(installed)
    if receipt.get("schema") != RECEIPT_SCHEMA:
        reasons.append(f"package receipt schema {receipt.get('schema')} != {RECEIPT_SCHEMA}")
    if receipt.get("pulp_sha") != spec["origin_commit"]:
        reasons.append(f"package receipt names origin {receipt.get('pulp_sha')} != {spec['origin_commit']}")
    return reasons


def mutated_export_commit(repo: Path, commit: str, prefix: str) -> str:
    """A commit object identical to `commit` except one mirrored blob gains a
    byte. It is written to the object store only; no ref names it."""
    blobs = ph.git_exported_blobs(repo, commit, prefix)
    victim = sorted(p for p in blobs if p.endswith(".cpp"))[0]
    data = run(["git", "-C", str(repo), "cat-file", "blob", blobs[victim]]).stdout + "\n"
    new_blob = subprocess.run(["git", "-C", str(repo), "hash-object", "-w", "--stdin"], input=data,
                              capture_output=True, text=True, check=True).stdout.strip()
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(tmp) / "index"))
        subprocess.run(["git", "-C", str(repo), "read-tree", commit], env=env, check=True)
        subprocess.run(["git", "-C", str(repo), "update-index", "--cacheinfo", f"100644,{new_blob},{prefix}{victim}"],
                       env=env, check=True)
        tree = subprocess.run(["git", "-C", str(repo), "write-tree"], env=env, capture_output=True, text=True,
                              check=True).stdout.strip()
    return subprocess.run(["git", "-C", str(repo), "commit-tree", tree, "-p", commit, "-m",
                           "parity packaging control: one mutated mirror blob"],
                          capture_output=True, text=True, check=True,
                          env=dict(os.environ, GIT_AUTHOR_NAME="parity", GIT_AUTHOR_EMAIL="parity@invalid",
                                   GIT_COMMITTER_NAME="parity", GIT_COMMITTER_EMAIL="parity@invalid")).stdout.strip()


# ── reference side ────────────────────────────────────────────────────────


def file_tags(binary: Path) -> list[str]:
    """Source stems compiled into a Catch2 binary, from its file tags."""
    output = run([str(binary), "--list-tests", "-#"], timeout=300).stdout
    return sorted(set(re.findall(r"\[#([A-Za-z0-9_]+)\]", output)))


def resolve_sources(origin_repo: Path, commit: str, stems: list[str]) -> dict[str, Path]:
    """Each file tag names exactly one test source at the origin commit."""
    listed = run(["git", "-C", str(origin_repo), "ls-tree", "-r", "--name-only", commit, "--", "test"]).stdout.split()
    out = {}
    for stem in stems:
        matches = [p for p in listed if Path(p).name == f"{stem}.cpp"]
        if len(matches) != 1:
            raise LaneError(f"file tag {stem} names {len(matches)} sources at the origin commit")
        out[stem] = origin_repo / matches[0]
    return out


def run_junit(binary: Path, tags: list[str], out_xml: Path, cwd: Path | None = None) -> dict[str, Any]:
    """Run the binary over the given file tags; return per-test outcomes keyed
    by "<source stem>::<test name>".

    The JUnit file is removed first, so a binary that dies before writing it
    can never be read through a previous run's file. A signal exit is an error,
    and the exit status must agree with the run: nonzero only when a test
    failed, zero only when none did. Anything else is a process that did not
    finish the run it reported.
    """
    if not tags:
        raise LaneError("no sources selected: an empty population is not evidence")
    out_xml.parent.mkdir(parents=True, exist_ok=True)
    out_xml.unlink(missing_ok=True)
    spec = ",".join(f"[#{tag}]" for tag in tags)
    result = run([str(binary), spec, "-#", "--reporter", "junit", "--out", str(out_xml)], timeout=1800, cwd=cwd)
    if result.returncode < 0:
        raise LaneError(f"{binary.name} was killed by signal {-result.returncode}: {result.stderr[-400:]}")
    if not out_xml.is_file():
        raise LaneError(f"{binary.name} wrote no JUnit (exit {result.returncode}): {result.stderr[-400:]}")
    outcomes = junit_outcomes(out_xml)
    failed = any(status == "failed" for status in outcomes.values())
    if result.returncode != 0 and not failed:
        raise LaneError(f"{binary.name} exited {result.returncode} with no failed test to explain it")
    if result.returncode == 0 and failed:
        raise LaneError(f"{binary.name} exited 0 although its JUnit records a failed test")
    return {"exit": result.returncode, "outcomes": outcomes, "junit_sha256": ph.sha256_file(out_xml)}


def junit_outcomes(path: Path) -> dict[str, str]:
    outcomes: dict[str, str] = {}
    for case in ET.parse(path).getroot().iter("testcase"):
        stem = case.get("classname", "").rsplit(".", 1)[-1]
        if case.find("skipped") is not None:
            status = "skipped"
        elif case.find("failure") is not None or case.find("error") is not None:
            status = "failed"
        else:
            status = "passed"
        outcomes[f"{stem}::{case.get('name')}"] = status
    return outcomes


def cmake_cache(build: Path) -> dict[str, str]:
    values = {}
    for line in (build / "CMakeCache.txt").read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.match(r"^([A-Za-z0-9_]+):[A-Z]+=(.*)$", line)
        if match:
            values[match.group(1)] = match.group(2)
    return values


def configuration_errors(closures: list[dict[str, Any]], cache: dict[str, str]) -> list[str]:
    """The reference build must be the configuration the export was cut from."""
    errors = []
    build_skia = cache.get("PULP_HAS_SKIA", "").upper() in ("TRUE", "ON", "1")
    for closure in closures:
        want = closure.get("configuration")
        if want and cache.get("CMAKE_BUILD_TYPE") != want:
            errors.append(f"reference build type {cache.get('CMAKE_BUILD_TYPE')} != closure {want}")
        defs = set(closure.get("compile_definitions") or [])
        closure_skia = any(d.startswith("PULP_HAS_SKIA") for d in defs)
        if closure_skia and not build_skia:
            errors.append(f"reference PULP_HAS_SKIA={cache.get('PULP_HAS_SKIA')} but a closure was cut with Skia")
    if not any(any(d.startswith("PULP_HAS_SKIA") for d in c.get("compile_definitions") or []) for c in closures) \
            and build_skia:
        errors.append(f"reference PULP_HAS_SKIA={cache.get('PULP_HAS_SKIA')} but the closures were cut without Skia")
    return sorted(set(errors))


# ── candidate side ────────────────────────────────────────────────────────


CONSUMER_CMAKE = """cmake_minimum_required(VERSION 3.24)
project(ParityPackagingConsumer LANGUAGES C CXX OBJCXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
find_package({package} CONFIG REQUIRED)
include(FetchContent)
FetchContent_Declare(Catch2 SOURCE_DIR "{catch2}")
FetchContent_MakeAvailable(Catch2)
add_executable(parity-consumer {sources})
target_include_directories(parity-consumer PRIVATE "{test_dir}")
target_link_libraries(parity-consumer PRIVATE {targets} Catch2::Catch2WithMain)
"""


def build_consumer(work: Path, *, package: str, targets: list[str], prefix: Path, sources: list[Path],
                   test_dir: Path, catch2: Path, jobs: int) -> dict[str, Any]:
    """Generate, configure and build the consumer; return the binary or the
    failing step. Sources are copied so a control can alter them."""
    if work.exists():
        shutil.rmtree(work)
    src = work / "src"
    src.mkdir(parents=True)
    copied = []
    for source in sources:
        shutil.copy2(source, src / source.name)
        copied.append(source.name)
    (src / "CMakeLists.txt").write_text(CONSUMER_CMAKE.format(
        package=package, targets=" ".join(targets), catch2=catch2, test_dir=test_dir,
        sources=" ".join(copied)), encoding="utf-8")
    log = []
    for step, argv in (
        ("configure", ["cmake", "-S", str(src), "-B", str(work / "build"), "-G", "Ninja",
                       "-DCMAKE_BUILD_TYPE=Release", f"-DCMAKE_PREFIX_PATH={prefix}",
                       "-DCMAKE_FIND_PACKAGE_NO_PACKAGE_REGISTRY=ON"]),
        ("build", ["cmake", "--build", str(work / "build"), "-j", str(jobs)]),
    ):
        result = run(argv, timeout=3600)
        log.append({"step": step, "exit": result.returncode, "tail": (result.stdout + result.stderr)[-1500:]})
        if result.returncode != 0:
            return {"binary": None, "log": log}
    return {"binary": work / "build" / "parity-consumer", "log": log}


PROBE_CMAKE = """cmake_minimum_required(VERSION 3.24)
project(ParityPackagingExclusionProbe LANGUAGES C CXX OBJCXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
find_package({package} CONFIG REQUIRED)
include(FetchContent)
FetchContent_Declare(Catch2 SOURCE_DIR "{catch2}")
FetchContent_MakeAvailable(Catch2)
foreach(_source IN ITEMS {sources})
    get_filename_component(_stem "${{_source}}" NAME_WE)
    add_executable("probe-${{_stem}}" "${{_source}}")
    target_include_directories("probe-${{_stem}}" PRIVATE "{test_dir}")
    target_link_libraries("probe-${{_stem}}" PRIVATE {targets} Catch2::Catch2WithMain)
endforeach()
"""


def excluded_sources_that_build(work: Path, *, package: str, targets: list[str], prefix: Path,
                                sources: list[Path], test_dir: Path, catch2: Path, jobs: int) -> list[str]:
    """An exclusion holds only while its source still cannot be built against
    the package. Each excluded source is compiled and linked on its own; the
    stems that now build are returned, so a stale exclusion cannot hide a
    source that would compile and diverge."""
    if not sources:
        return []
    if work.exists():
        shutil.rmtree(work)
    src = work / "src"
    src.mkdir(parents=True)
    for source in sources:
        shutil.copy2(source, src / source.name)
    (src / "CMakeLists.txt").write_text(PROBE_CMAKE.format(
        package=package, targets=" ".join(targets), catch2=catch2, test_dir=test_dir,
        sources=" ".join(source.name for source in sources)), encoding="utf-8")
    configured = run(["cmake", "-S", str(src), "-B", str(work / "build"), "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release",
                      f"-DCMAKE_PREFIX_PATH={prefix}", "-DCMAKE_FIND_PACKAGE_NO_PACKAGE_REGISTRY=ON"], timeout=3600)
    if configured.returncode != 0:
        raise LaneError(f"exclusion probe did not configure: {(configured.stdout + configured.stderr)[-400:]}")
    run(["cmake", "--build", str(work / "build"), "-j", str(jobs), "--", "-k", "0"], timeout=3600)
    return sorted(source.stem for source in sources if (work / "build" / f"probe-{source.stem}").is_file())


def git_blob(repo: Path, commit: str, rel: str) -> str | None:
    result = run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"{commit}:{rel}"])
    return result.stdout.strip() if result.returncode == 0 else None


def dependency_violations(build: Path, origin_repo: Path, origin_commit: str, exported_repo: Path,
                          exported_commit: str, exported_prefix: str) -> list[str]:
    """The candidate may read origin files only from test/. Any other origin
    file a translation unit includes (a private `src/` header reached from a
    test) is accepted only when the export carries the identical blob at the
    same path, so the candidate still compiles nothing but exported code."""
    result = run(["ninja", "-C", str(build), "-t", "deps"])
    if result.returncode != 0:
        return [f"cannot read the consumer's dependency log: {result.stderr[-200:]}"]
    origin = origin_repo.resolve()
    seen: set[str] = set()
    violations = []
    for line in result.stdout.splitlines():
        if not line.startswith(" ") or not line.strip():
            continue
        path = Path(line.strip())
        if not path.is_absolute():
            path = build / path
        try:
            rel = path.resolve().relative_to(origin).as_posix()
        except ValueError:
            continue
        if rel in seen or rel.startswith("test/"):
            continue
        seen.add(rel)
        origin_blob = git_blob(origin_repo, origin_commit, rel)
        worktree_blob = run(["git", "-C", str(origin_repo), "hash-object", rel]).stdout.strip()
        exported_blob = git_blob(exported_repo, exported_commit, exported_prefix + rel)
        if not origin_blob or origin_blob != worktree_blob or origin_blob != exported_blob:
            violations.append(f"candidate compiled origin file {rel} outside test/ that the export does not carry "
                              "identically")
    if not result.stdout.strip():
        violations.append("the consumer's dependency log is empty: nothing to audit")
    return violations


# ── comparison ────────────────────────────────────────────────────────────


def compare(reference: dict[str, str], candidate: dict[str, str]) -> list[str]:
    reasons = []
    if not reference:
        reasons.append("reference population is empty")
    missing = sorted(set(reference) - set(candidate))
    extra = sorted(set(candidate) - set(reference))
    reasons += [f"missing from candidate: {t}" for t in missing[:15]]
    reasons += [f"not in reference: {t}" for t in extra[:15]]
    for test in sorted(set(reference) & set(candidate)):
        if reference[test] != candidate[test]:
            reasons.append(f"{test}: reference {reference[test]} != candidate {candidate[test]}")
        elif reference[test] == "failed":
            reasons.append(f"{test}: failed on both sides")
    if not any(status == "passed" for status in candidate.values()):
        reasons.append("no test passed on the candidate: nothing was compared")
    return reasons


def skipped_on_both(reference: dict[str, str], candidate: dict[str, str]) -> list[str]:
    """Tests that skip identically: not a difference, but not evidence either;
    the receipt lists them so the passing denominator is explicit."""
    return sorted(t for t in set(reference) & set(candidate)
                  if reference[t] == candidate[t] == "skipped")


def justified(entries: dict[str, Any], what: str) -> dict[str, Any]:
    if not all(str(why).strip() for why in entries.values()):
        raise LaneError(f"{what} entries need a justification")
    return entries


def evaluate(spec: dict[str, Any], out: Path, *, prefix_override: Path | None = None,
             source_overrides: dict[str, list[Path]] | None = None, reference_override: Path | None = None,
             tags_override: list[str] | None = None, extra_libraries: list[Path] | None = None,
             exported_commit_override: str | None = None,
             cache_override: dict[str, str] | None = None) -> dict[str, Any]:
    reasons: list[str] = []
    closures = [ph.load_json(Path(p)) for p in spec["closures"]]
    identity = identity_result(spec, exported_commit_override)
    if identity["status"] != "pass":
        return {"status": "blocked", "reasons": ["rung 1 (identity) did not pass"] + identity["reasons"][:10],
                "identity": identity}

    prefix = Path(prefix_override or spec["candidate_prefix"])
    binding = receipt_binding(spec, prefix)
    if binding:
        return {"status": "blocked", "reasons": ["package is not bound to the checked export"] + binding,
                "identity": identity}

    origin_repo = Path(spec["origin_repo"])
    checkout_head = ph.git_head(origin_repo)
    if checkout_head != spec["origin_commit"]:
        reasons.append(f"origin checkout HEAD {checkout_head} != exported origin commit {spec['origin_commit']}")
    cache = cache_override or cmake_cache(Path(spec["reference_build"]))
    reasons += configuration_errors(closures, cache)
    excluded = justified(spec.get("exclude_sources") or {}, "exclude_sources")
    # A test the origin itself cannot repeat (it flips on the reference binary
    # alone) is left out by name, never by source, and the receipt lists it.
    unstable = justified(spec.get("unstable_tests") or {}, "unstable_tests")

    libraries = sorted(prefix.glob(spec.get("library_glob", "mirror/lib/libvellum-mirror-*.dylib")))
    libraries += list(extra_libraries or [])
    if not libraries:
        reasons.append("no package libraries found for the single-definition census")
    census = library_census(libraries) if libraries else {"_exported": set(), "duplicate_count": 0}
    if census["duplicate_count"]:
        reasons.append(f"{census['duplicate_count']} symbols defined by more than one package library: "
                       f"{census['duplicates'][:5]}")

    groups = []
    unstable_seen: set[str] = set()
    for group in spec["groups"]:
        name = group["name"]
        binary = Path(reference_override or group["reference_binary"])
        if not binary.is_file():
            return {"status": "blocked", "reasons": [f"{name}: reference binary missing: {binary}"],
                    "identity": identity}
        all_tags = tags_override if tags_override is not None else file_tags(binary)
        tags = [t for t in all_tags if t not in excluded]
        excluded_here = [t for t in all_tags if t in excluded]
        try:
            sources = (source_overrides or {}).get(name) or list(
                resolve_sources(origin_repo, spec["origin_commit"], tags).values())
            reference = run_junit(binary, tags, out / name / "reference.xml")
            now_build = excluded_sources_that_build(
                out / name / "exclusion-probe", package=spec["candidate_package"],
                targets=group["candidate_targets"], prefix=prefix,
                sources=list(resolve_sources(origin_repo, spec["origin_commit"], excluded_here).values()),
                test_dir=origin_repo / "test", catch2=Path(spec["catch2_source"]), jobs=int(spec.get("jobs", 4)))
        except LaneError as error:
            return {"status": "fail", "reasons": reasons + [f"{name}: {error}"], "identity": identity}
        reasons += [f"{name}: excluded source {stem} now builds against the package; remove its exclusion"
                    for stem in now_build]
        reference_census = image_census(binary, set())
        if group.get("expect_reference_provider") and not reference_census["provider_count"]:
            reasons.append(f"{name}: provider census found no Skia/Dawn definition in the reference binary, "
                           "which links them statically: the census is blind")
        built = build_consumer(out / name / "consumer", package=spec["candidate_package"],
                               targets=group["candidate_targets"], prefix=prefix, sources=sources,
                               test_dir=origin_repo / "test", catch2=Path(spec["catch2_source"]),
                               jobs=int(spec.get("jobs", 4)))
        if built["binary"] is None:
            reasons.append(f"{name}: candidate consumer did not build against the package")
            groups.append({"group": name, "consumer_log": built["log"]})
            continue
        try:
            candidate = run_junit(built["binary"], tags, out / name / "candidate.xml", cwd=origin_repo)
        except LaneError as error:
            reasons.append(f"{name}: candidate: {error}")
            groups.append({"group": name, "candidate_error": str(error)})
            continue
        reasons += [f"{name}: {v}" for v in dependency_violations(
            out / name / "consumer" / "build", origin_repo, spec["origin_commit"], Path(spec["exported_repo"]),
            exported_commit_override or spec["exported_commit"], spec.get("exported_prefix", "mirror/"))]
        image = image_census(built["binary"], census["_exported"])
        links = linked_libraries(built["binary"])
        if image["shadowed_count"]:
            reasons.append(f"{name}: candidate image defines {image['shadowed_count']} symbols the package exports: "
                           f"{image['shadowed'][:3]}")
        if image["provider_count"]:
            reasons.append(f"{name}: candidate image carries its own Skia/Dawn provider: {image['providers'][:3]}")
        if not any("libvellum-mirror-" in lib for lib in links):
            reasons.append(f"{name}: candidate image links no package library")
        for test in unstable:
            if test in reference["outcomes"]:
                unstable_seen.add(test)
                if candidate["outcomes"].get(test) == "failed":
                    reasons.append(f"{name}: unstable test {test} failed on the candidate")
            reference["outcomes"].pop(test, None)
            candidate["outcomes"].pop(test, None)
        compared = compare(reference["outcomes"], candidate["outcomes"])
        reasons += [f"{name}: {r}" for r in compared]
        groups.append({
            "group": name,
            "sources": sorted(s.name for s in sources),
            "sources_total": len(all_tags),
            "sources_excluded": excluded_here,
            "candidate_targets": group["candidate_targets"],
            "tests": len(reference["outcomes"]),
            "passed_on_both": sum(1 for t, s in reference["outcomes"].items()
                                  if s == "passed" and candidate["outcomes"].get(t) == "passed"),
            "skipped_on_both": skipped_on_both(reference["outcomes"], candidate["outcomes"]),
            "reference": {"binary_sha256": ph.sha256_file(binary), "junit_sha256": reference["junit_sha256"],
                          "exit": reference["exit"], "provider_definitions": reference_census["provider_count"]},
            "candidate": {"binary_sha256": ph.sha256_file(built["binary"]),
                          "junit_sha256": candidate["junit_sha256"], "exit": candidate["exit"],
                          "linked_package_libraries": sorted(l.rsplit("/", 1)[-1] for l in links
                                                             if "libvellum-mirror-" in l),
                          "image_census": {k: v for k, v in image.items() if k != "defined"}},
        })
    if len(groups) == len(spec["groups"]) and all("tests" in g for g in groups):
        reasons += [f"unstable test {t} is in no group's reference population" for t in sorted(set(unstable) - unstable_seen)]
    total = sum(g.get("tests", 0) for g in groups)
    if not total:
        reasons.append("no group produced a population")
    compared_sources = sum(len(g.get("sources", [])) for g in groups)
    total_sources = sum(g.get("sources_total", 0) for g in groups)
    scope = (f"named subset: {compared_sources} of {total_sources} origin test sources ({total} tests) across "
             f"{len(groups)} groups; {total_sources - compared_sources} excluded, each listed with its reason"
             if compared_sources != total_sources else
             f"full population: {total_sources} origin test sources ({total} tests) across {len(groups)} groups")
    return {
        "scope": scope,
        "status": "fail" if reasons else "pass",
        "reasons": reasons,
        "identity": {k: identity[k] for k in ("status", "closure_paths", "origin_files", "exported_files")},
        "origin_commit": spec["origin_commit"],
        "exported_commit": exported_commit_override or spec["exported_commit"],
        "export_receipt": spec["export_receipt"],
        "excluded_sources": excluded,
        "unstable_tests": unstable,
        "groups": groups,
        "tests": total,
        "passed_on_both": sum(g.get("passed_on_both", 0) for g in groups),
        "census": {k: v for k, v in census.items() if k != "_exported"},
        "package_libraries": {lib.name: ph.sha256_file(lib) for lib in libraries},
    }


# ── calibration ───────────────────────────────────────────────────────────


def seeded_failure(source: Path, work: Path) -> Path:
    work.mkdir(parents=True, exist_ok=True)
    seeded = work / source.name
    seeded.write_text(source.read_text(encoding="utf-8") +
                      '\nTEST_CASE("parity packaging seeded failure") { REQUIRE(1 == 2); }\n',
                      encoding="utf-8")
    return seeded


def prefix_copy(prefix: Path, work: Path) -> Path:
    copy = work / "prefix"
    if copy.exists():
        shutil.rmtree(copy)
    shutil.copytree(prefix, copy, symlinks=True)
    return copy


def calibrate(spec: dict[str, Any], out: Path) -> dict[str, Any]:
    if out.exists():
        shutil.rmtree(out)
    positive = evaluate(spec, out / "positive")
    controls = []

    def control(name: str, result: dict[str, Any], expect: str = "fail", must_mention: str = "") -> None:
        bites = result["status"] == expect and (not must_mention or any(must_mention in r for r in result["reasons"]))
        controls.append({"control": name, "status": result["status"], "reasons": result["reasons"][:4],
                         "outcome": "REJECTED as required" if bites else "CONTROL DID NOT FAIL"})

    first = spec["groups"][0]
    excluded = spec.get("exclude_sources") or {}
    tags = [t for t in file_tags(Path(first["reference_binary"])) if t not in excluded]
    sources = list(resolve_sources(Path(spec["origin_repo"]), spec["origin_commit"], tags).values())
    def only(group: dict[str, Any]) -> dict[str, Any]:
        # A single-group control drops unstable entries that name other
        # groups' tests, so each control fails for its own reason only.
        return dict(spec, groups=[group], unstable_tests={})

    one_group = only(first)
    control("seeded-failing-test-in-candidate",
            evaluate(one_group, out / "c-seeded",
                     source_overrides={first["name"]: [seeded_failure(sources[0], out / "seeded")] + sources[1:]}),
            must_mention="seeded failure")
    aborting = out / "aborting" / sources[0].name
    aborting.parent.mkdir(parents=True, exist_ok=True)
    aborting.write_text(sources[0].read_text(encoding="utf-8") +
                        "\n#include <cstdlib>\nstatic const int parity_abort_before_main = (std::abort(), 0);\n",
                        encoding="utf-8")
    control("candidate-aborts-before-main",
            evaluate(one_group, out / "c-abort", source_overrides={first["name"]: [aborting] + sources[1:]}),
            must_mention="killed by signal")
    control("stale-exclusion-of-a-building-source",
            evaluate(dict(one_group, exclude_sources=dict(excluded, **{sources[0].stem: "planted stale exclusion"})),
                     out / "c-stale-exclusion"), must_mention="now builds against the package")
    control("unstable-test-not-in-population",
            evaluate(dict(one_group, unstable_tests={"test_absent::planted": "planted typo"}),
                     out / "c-unstable"), must_mention="is in no group's reference population")
    control("dropped-source-in-candidate",
            evaluate(one_group, out / "c-dropped", source_overrides={first["name"]: sources[1:]}),
            must_mention="missing from candidate")
    header_prefix = prefix_copy(Path(spec["candidate_prefix"]), out / "c-header")
    (header_prefix / spec["control_header"]).unlink()
    control("header-removed-from-package",
            evaluate(only(next(g for g in spec["groups"] if g["name"] == spec["control_header_group"])),
                     out / "c-header", prefix_override=header_prefix), must_mention="did not build")
    control("empty-population", evaluate(one_group, out / "c-empty", tags_override=[]),
            must_mention="no sources selected")
    control("reference-binary-missing",
            evaluate(one_group, out / "c-nobin", reference_override=out / "absent-binary"), expect="blocked")
    libraries = sorted(Path(spec["candidate_prefix"]).glob(spec.get("library_glob",
                                                                    "mirror/lib/libvellum-mirror-*.dylib")))
    dup = out / "c-dup" / "libduplicate-copy.dylib"
    dup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(libraries[0], dup)
    control("duplicate-definition-library",
            evaluate(one_group, out / "c-dup", extra_libraries=[dup]), must_mention="more than one package library")
    control("reference-config-mismatch",
            evaluate(one_group, out / "c-config", cache_override={"CMAKE_BUILD_TYPE": "Debug",
                                                                  "PULP_HAS_SKIA": "FALSE"}),
            must_mention="reference build type")
    mutated = mutated_export_commit(Path(spec["exported_repo"]), spec["exported_commit"],
                                    spec.get("exported_prefix", "mirror/"))
    control("identity-one-mutated-mirror-blob",
            evaluate(one_group, out / "c-ident", exported_commit_override=mutated), expect="blocked",
            must_mention="blob differs")
    receipt_prefix = prefix_copy(Path(spec["candidate_prefix"]), out / "c-receipt")
    installed = next((receipt_prefix / "mirror" / "share" / "provenance").glob("*.json"))
    receipt = json.loads(installed.read_text(encoding="utf-8"))
    receipt["pulp_sha"] = "0" * 40
    installed.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    control("package-built-from-another-export",
            evaluate(one_group, out / "c-receipt", prefix_override=receipt_prefix), expect="blocked",
            must_mention="package receipt")
    # The Vellum code is what is measured: a package built from a mirror with
    # one planted behavior change must fail the differential, even though
    # identity and the receipt binding (both about the commit) still pass.
    divergent = spec.get("divergent_prefix")
    if divergent:
        group = next(g for g in spec["groups"] if g["name"] == spec["divergent_group"])
        control("planted-divergence-in-package",
                evaluate(only(group), out / "c-divergent", prefix_override=Path(divergent)),
                must_mention="reference passed != candidate failed")
    else:
        controls.append({"control": "planted-divergence-in-package", "status": "missing",
                         "reasons": ["spec names no divergent_prefix"], "outcome": "CONTROL DID NOT FAIL"})
    failures = [c["control"] for c in controls if c["outcome"] != "REJECTED as required"]
    return {"schema": CALIBRATION_SCHEMA, "calibrated": positive["status"] == "pass" and not failures,
            "positive": positive, "controls": controls, "control_failures": failures,
            "harness": ph.sha256_file(Path(__file__).resolve())}


def redact(value: Any, roots: dict[str, str]) -> Any:
    """Receipts are committed as evidence: replace host paths with root names."""
    if isinstance(value, dict):
        return {k: redact(v, roots) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, roots) for v in value]
    if isinstance(value, str):
        for name, root in sorted(roots.items(), key=lambda item: -len(item[1])):
            value = value.replace(root, f"<{name}>")
    return value


def spec_roots(spec: dict[str, Any], out: Path) -> dict[str, str]:
    roots = {"out": str(out.resolve()), "origin": spec["origin_repo"], "exported": spec["exported_repo"],
             "prefix": spec["candidate_prefix"], "reference-build": spec["reference_build"],
             "catch2": spec["catch2_source"], "home": str(Path.home())}
    if spec.get("divergent_prefix"):
        roots["divergent-prefix"] = spec["divergent_prefix"]
    return {k: str(Path(v).resolve()) if k != "home" else v for k, v in roots.items()}


def bind_roots(value: Any, roots: dict[str, str]) -> Any:
    """A committed spec names host locations as `<root>`; `--root` binds them.
    An unbound `<root>` left in a path is refused rather than guessed."""
    if isinstance(value, dict):
        return {k: bind_roots(v, roots) for k, v in value.items()}
    if isinstance(value, list):
        return [bind_roots(v, roots) for v in value]
    if isinstance(value, str) and value.startswith("<"):
        name, _, rest = value[1:].partition(">")
        if name not in roots:
            raise LaneError(f"spec path {value!r} names root <{name}>; pass --root {name}=PATH")
        return roots[name] + rest
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("run", "calibrate"))
    parser.add_argument("--spec", required=True, type=Path, help="JSON lane spec (see README)")
    parser.add_argument("--root", action="append", default=[], metavar="NAME=PATH",
                        help="bind a <NAME> root the spec uses")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    roots = {}
    for item in args.root:
        name, _, path = item.partition("=")
        roots[name] = str(Path(path).resolve())
    try:
        spec = bind_roots(ph.load_json(args.spec), roots)
    except LaneError as error:
        print(f"packaging-lane: {error}", file=sys.stderr)
        return 2
    try:
        if args.command == "run":
            if args.out.exists():
                shutil.rmtree(args.out)
            result = evaluate(spec, args.out)
            result["schema"] = SCHEMA
            ph.write_json(args.out / "packaging-receipt.json", redact(result, spec_roots(spec, args.out)))
            print(json.dumps({k: result.get(k) for k in ("status", "tests", "passed_on_both")}, indent=2))
            for reason in result["reasons"][:20]:
                print(f"  {reason}")
            return 0 if result["status"] == "pass" else 1
        report = calibrate(spec, args.out)
        ph.write_json(args.out / "packaging-calibration.json", redact(report, spec_roots(spec, args.out)))
        print(f"positive: {report['positive']['status']} ({report['positive'].get('tests')} tests)")
        for reason in report["positive"]["reasons"][:20]:
            print(f"  {reason}")
        for c in report["controls"]:
            print(f"{c['control']:36} {c['outcome']}  {c['reasons'][:1]}")
        print("CALIBRATED" if report["calibrated"] else "NOT CALIBRATED")
        return 0 if report["calibrated"] else 1
    except ph.HarnessError as error:
        print(f"packaging-lane: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
