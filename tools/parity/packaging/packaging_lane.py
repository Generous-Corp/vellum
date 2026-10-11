#!/usr/bin/env python3
"""D6 rung 2: Pulp's own tests, composed against the installed Vellum package.

The identity lane (rung 1) proves the exported tree equals the origin
closure. This lane proves the *package* built from that tree behaves like the
origin. It takes the origin repository's own test sources at the exported
commit, compiles them against the installed package through `find_package`,
runs them, and compares the Catch2 results test by test with the origin's
in-tree run of the same sources.

- The reference is the origin's own test binary, built in the configuration
  the closure records. The binary's file tags (`-#`) choose which sources run.
- The candidate is a consumer project generated here. It links only the
  package's exported target and adds no compile definitions of its own.
- Identity first: the lane is `blocked` unless rung 1 passes for the same
  export (closure manifests + export receipt + exported commit).
- Configuration: the reference build must match the closure's recorded
  configuration (build type, Skia on/off, compile definitions), or the two
  sides are not compiling the same thing.
- Single definition: every global symbol in the package's archives has one
  owning archive.

`calibrate` runs the comparison and then seeded controls that must each make
it non-accepting. The lane is only as trustworthy as its controls.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import parity_harness as ph  # noqa: E402

SCHEMA = "vellum.parity.packaging-receipt.v1"
CALIBRATION_SCHEMA = "vellum.parity.packaging-calibration.v1"


class LaneError(ph.HarnessError):
    pass


# ── reference side ────────────────────────────────────────────────────────


def file_tags(binary: Path) -> list[str]:
    """Source stems compiled into a Catch2 binary, from its file tags."""
    output = subprocess.run([str(binary), "--list-tests", "-#"], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=300).stdout
    return sorted(set(re.findall(r"\[#([A-Za-z0-9_]+)\]", output)))


def run_junit(binary: Path, tags: list[str], out_xml: Path, cwd: Path | None = None) -> dict[str, Any]:
    """Run the binary over the given file tags; return per-test outcomes keyed
    by "<source stem>::<test name>"."""
    if not tags:
        raise LaneError("no sources selected: an empty population is not evidence")
    out_xml.parent.mkdir(parents=True, exist_ok=True)
    spec = ",".join(f"[#{tag}]" for tag in tags)
    result = subprocess.run([str(binary), spec, "-#", "--reporter", "junit", "--out", str(out_xml)],
                            capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=1800, cwd=cwd)
    if not out_xml.is_file():
        raise LaneError(f"{binary.name} wrote no JUnit (exit {result.returncode}): {result.stderr[-400:]}")
    outcomes: dict[str, str] = {}
    for case in ET.parse(out_xml).getroot().iter("testcase"):
        stem = case.get("classname", "").rsplit(".", 1)[-1]
        if case.find("skipped") is not None:
            status = "skipped"
        elif case.find("failure") is not None or case.find("error") is not None:
            status = "failed"
        else:
            status = "passed"
        outcomes[f"{stem}::{case.get('name')}"] = status
    return {"exit": result.returncode, "outcomes": outcomes, "junit_sha256": ph.sha256_file(out_xml)}


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
    for closure in closures:
        want = closure.get("configuration")
        if want and cache.get("CMAKE_BUILD_TYPE") != want:
            errors.append(f"reference build type {cache.get('CMAKE_BUILD_TYPE')} != closure {want}")
        defs = set(closure.get("compile_definitions") or [])
        closure_skia = any(d.startswith("PULP_HAS_SKIA") for d in defs)
        build_skia = cache.get("PULP_HAS_SKIA", "").upper() in ("TRUE", "ON", "1")
        if closure_skia != build_skia:
            errors.append(f"reference PULP_HAS_SKIA={cache.get('PULP_HAS_SKIA')} but the closure was cut "
                          f"{'with' if closure_skia else 'without'} Skia")
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
target_link_libraries(parity-consumer PRIVATE {target} Catch2::Catch2WithMain)
"""


def build_consumer(work: Path, *, package: str, target: str, prefix: Path, sources: list[Path],
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
        package=package, target=target, catch2=catch2, test_dir=test_dir,
        sources=" ".join(copied)), encoding="utf-8")
    log = []
    for step, argv in (
        ("configure", ["cmake", "-S", str(src), "-B", str(work / "build"), "-G", "Ninja",
                       "-DCMAKE_BUILD_TYPE=Release", f"-DCMAKE_PREFIX_PATH={prefix}"]),
        ("build", ["cmake", "--build", str(work / "build"), "-j", str(jobs)]),
    ):
        result = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=3600)
        log.append({"step": step, "exit": result.returncode, "tail": (result.stdout + result.stderr)[-1500:]})
        if result.returncode != 0:
            return {"binary": None, "log": log}
    return {"binary": work / "build" / "parity-consumer", "log": log}


def archive_census(archives: list[Path]) -> dict[str, Any]:
    """Every strong global definition must come from exactly one archive.
    Weak definitions (inline functions, template instantiations) are coalesced
    by the linker and legitimately appear in several archives."""
    owners: dict[str, set[str]] = {}
    for archive in archives:
        out = subprocess.run(["nm", "-gUm", str(archive)], capture_output=True, text=True,
                             encoding="utf-8", errors="replace").stdout
        for line in out.splitlines():
            if "external" not in line or "weak" in line or "(undefined)" in line:
                continue
            symbol = line.rsplit(" ", 1)[-1]
            owners.setdefault(symbol, set()).add(archive.name)
    duplicates = sorted(s for s, o in owners.items() if len(o) > 1)
    return {"archives": [a.name for a in archives], "symbols": len(owners),
            "duplicates": duplicates[:25], "duplicate_count": len(duplicates)}


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


def evaluate(spec: dict[str, Any], out: Path, *, candidate_sources: list[Path] | None = None,
             candidate_prefix: Path | None = None, reference_binary: Path | None = None,
             tags_override: list[str] | None = None, extra_archives: list[Path] | None = None,
             identity_override: dict[str, Any] | None = None,
             cache_override: dict[str, str] | None = None) -> dict[str, Any]:
    reasons: list[str] = []
    closures = [ph.load_json(Path(p)) for p in spec["closures"]]
    manifest = ph.merge_manifests(closures)
    reclassified = spec.get("closure_external_paths") or {}
    if reclassified:
        # A closure path that is really an external input (a build-tree copy of
        # a fetched dependency) may be reclassified, one path at a time, with a
        # justification; it then leaves the identity comparison and is
        # reported in the receipt. Anything not listed still fails identity.
        if not all(str(why).strip() for why in reclassified.values()):
            raise LaneError("closure_external_paths entries need a justification")
        manifest = dict(manifest, files=[p for p in ph.manifest_paths(manifest) if p not in reclassified])

    identity = identity_override
    if identity is None:
        origin = ph.git_tree_blobs(Path(spec["origin_repo"]), spec["origin_commit"], ph.manifest_paths(manifest))
        exported = ph.git_exported_blobs(Path(spec["exported_repo"]), spec["exported_commit"],
                                         spec.get("exported_prefix", "mirror/"))
        identity = ph.compare_identity(manifest, origin, exported)
    if identity["status"] != "pass":
        return {"status": "blocked", "reasons": ["rung 1 (identity) did not pass"] + identity["reasons"][:10],
                "identity": identity}

    checkout_head = ph.git_head(Path(spec["origin_repo"]))
    if checkout_head != spec["origin_commit"]:
        reasons.append(f"origin checkout HEAD {checkout_head} != exported origin commit {spec['origin_commit']}")

    cache = cache_override or cmake_cache(Path(spec["reference_build"]))
    reasons += configuration_errors(closures, cache)

    binary = Path(reference_binary or spec["reference_binary"])
    if not binary.is_file():
        return {"status": "blocked", "reasons": [f"reference binary missing: {binary}"], "identity": identity}
    tags = tags_override if tags_override is not None else file_tags(binary)
    excluded = spec.get("exclude_sources") or {}
    if excluded:
        # A source left out of the comparison must say why, and the receipt
        # names it: a partial population never reads as the whole one.
        if not all(str(why).strip() for why in excluded.values()):
            raise LaneError("exclude_sources entries need a justification")
        tags = [t for t in tags if t not in excluded]
    test_dir = Path(spec["origin_repo"]) / "test"
    sources = candidate_sources or [test_dir / f"{tag}.cpp" for tag in tags]
    try:
        reference = run_junit(binary, tags, out / "reference.xml")
    except LaneError as error:
        return {"status": "fail", "reasons": [str(error)], "identity": identity}

    prefix = Path(candidate_prefix or spec["candidate_prefix"])
    built = build_consumer(out / "consumer", package=spec["candidate_package"],
                           target=spec["candidate_target"], prefix=prefix, sources=sources,
                           test_dir=test_dir, catch2=Path(spec["catch2_source"]), jobs=int(spec.get("jobs", 4)))
    if built["binary"] is None:
        return {"status": "fail", "reasons": reasons + ["candidate consumer did not build against the package"],
                "consumer_log": built["log"], "identity": identity}
    candidate = run_junit(built["binary"], tags, out / "candidate.xml", cwd=Path(spec["origin_repo"]))

    archives = sorted((prefix / "lib").glob(spec.get("archive_glob", "libvellum-mirror-*.a")))
    archives += list(extra_archives or [])
    census = archive_census(archives)
    if len(archives) == 0:
        reasons.append("no package archives found for the single-definition census")
    if census["duplicate_count"]:
        reasons.append(f"{census['duplicate_count']} symbols defined by more than one archive: "
                       f"{census['duplicates'][:5]}")

    reasons += compare(reference["outcomes"], candidate["outcomes"])
    return {
        "status": "fail" if reasons else "pass",
        "reasons": reasons,
        "identity": {k: identity[k] for k in ("status", "closure_paths", "origin_files", "exported_files")},
        "closure_external_paths": reclassified,
        "excluded_sources": excluded,
        "sources": [s.name for s in sources],
        "tests": len(reference["outcomes"]),
        "passed_on_both": sum(1 for t, s in reference["outcomes"].items()
                              if s == "passed" and candidate["outcomes"].get(t) == "passed"),
        "skipped_on_both": skipped_on_both(reference["outcomes"], candidate["outcomes"]),
        "reference": {"binary": str(binary), "binary_sha256": ph.sha256_file(binary),
                      "junit_sha256": reference["junit_sha256"], "exit": reference["exit"]},
        "candidate": {"prefix": str(prefix), "binary_sha256": ph.sha256_file(built["binary"]),
                      "junit_sha256": candidate["junit_sha256"], "exit": candidate["exit"]},
        "census": census,
    }


# ── calibration ───────────────────────────────────────────────────────────


def seeded_failure(source: Path, work: Path) -> Path:
    work.mkdir(parents=True, exist_ok=True)
    seeded = work / source.name
    seeded.write_text(source.read_text(encoding="utf-8") +
                      '\nTEST_CASE("parity packaging seeded failure") { REQUIRE(1 == 2); }\n',
                      encoding="utf-8")
    return seeded


def prefix_without(prefix: Path, relative: str, work: Path) -> Path:
    copy = work / "prefix"
    if copy.exists():
        shutil.rmtree(copy)
    shutil.copytree(prefix, copy, symlinks=True)
    (copy / relative).unlink()
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

    tags = [t for t in file_tags(Path(spec["reference_binary"])) if t not in (spec.get("exclude_sources") or {})]
    sources = [Path(spec["origin_repo"]) / "test" / f"{tag}.cpp" for tag in tags]
    seeded = [seeded_failure(sources[0], out / "seeded")] + sources[1:]
    control("seeded-failing-test-in-candidate",
            evaluate(spec, out / "c-seeded", candidate_sources=seeded), must_mention="seeded failure")
    control("dropped-source-in-candidate",
            evaluate(spec, out / "c-dropped", candidate_sources=sources[1:]), must_mention="missing from candidate")
    header = spec.get("control_header", "include/pulp/canvas/path.hpp")
    control("header-removed-from-package",
            evaluate(spec, out / "c-header", candidate_prefix=prefix_without(Path(spec["candidate_prefix"]),
                                                                            header, out / "c-header-prefix")),
            must_mention="did not build")
    control("empty-population", evaluate(spec, out / "c-empty", tags_override=[]), must_mention="no sources selected")
    control("reference-binary-missing",
            evaluate(spec, out / "c-nobin", reference_binary=out / "absent-binary"), expect="blocked")
    archives = sorted((Path(spec["candidate_prefix"]) / "lib").glob(spec.get("archive_glob", "libvellum-mirror-*.a")))
    dup = out / "c-dup" / "libduplicate-copy.a"
    dup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(archives[-1], dup)
    control("duplicate-symbol-archive",
            evaluate(spec, out / "c-dup", extra_archives=[dup]), must_mention="more than one archive")
    control("reference-config-mismatch",
            evaluate(spec, out / "c-config", cache_override={"CMAKE_BUILD_TYPE": "Debug", "PULP_HAS_SKIA": "TRUE"}),
            must_mention="reference build type")
    control("identity-not-passing",
            evaluate(spec, out / "c-ident", identity_override={"status": "fail", "reasons": ["planted"]}),
            expect="blocked")
    failures = [c["control"] for c in controls if c["outcome"] != "REJECTED as required"]
    return {"schema": CALIBRATION_SCHEMA, "calibrated": positive["status"] == "pass" and not failures,
            "positive": positive, "controls": controls, "control_failures": failures,
            "harness": ph.sha256_file(Path(__file__).resolve())}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("run", "calibrate"))
    parser.add_argument("--spec", required=True, type=Path, help="JSON lane spec (see README)")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    spec = ph.load_json(args.spec)
    try:
        if args.command == "run":
            result = evaluate(spec, args.out)
            result["schema"] = SCHEMA
            ph.write_json(args.out / "packaging-receipt.json", result)
            print(json.dumps({k: result.get(k) for k in ("status", "tests", "sources")}, indent=2))
            for reason in result["reasons"][:10]:
                print(f"  {reason}")
            return 0 if result["status"] == "pass" else 1
        report = calibrate(spec, args.out)
        ph.write_json(args.out / "packaging-calibration.json", report)
        print(f"positive: {report['positive']['status']} ({report['positive'].get('tests')} tests)")
        for reason in report["positive"]["reasons"][:10]:
            print(f"  {reason}")
        for c in report["controls"]:
            print(f"{c['control']:36} {c['outcome']}")
        print("CALIBRATED" if report["calibrated"] else "NOT CALIBRATED")
        return 0 if report["calibrated"] else 1
    except ph.HarnessError as error:
        print(f"packaging-lane: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
