#!/usr/bin/env python3
"""Build the parity harness's observer tools against Vellum's mirrored units.

The observers (pulp-import-design, pulp-design-ir-observe, pulp-ui-frames-observe)
are measurement instruments, not part of any unit, so both implementations run
the same instrument source. What differs is the code under measurement: on
the Vellum side every Pulp-origin unit the observer links (foundation, canvas,
render, gpu-device, state-core, view-core, view-script, view-platform,
design-import) comes from the archives Vellum built from its mirror tree, and
every header of those units resolves into mirror/.

How: the Pulp reference build's own compile and link commands for each
observer are read from its build.ninja and rewritten -
  * an include root under the Pulp checkout that the mirror also carries is redirected to the same
    path under Vellum's mirror/ (identity guarantees the bytes match; the point
    is that the Vellum tree is what is compiled against);
  * every archive of a target named by a closure manifest is replaced by the
    Vellum rehearsal archive built from that manifest; an archive that cannot be
    replaced is an error, never a silent fallback to Pulp's copy;
  * archives not in any closure (third-party, Pulp-owned units such as
    view-audio/format/host, and the pulp::view umbrella) are kept and listed.
Each link writes a linker map; the census counts linked objects per archive
and fails if any object still comes from a Pulp archive of a mirrored target.

Writes <out>/bin/<tool>, <out>/<tool>.map and <out>/provenance.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys

TOOL_DIR = "tools/import-design"


def ninja_blocks(build_ninja: Path) -> list[tuple[str, dict[str, str]]]:
    blocks: list[tuple[str, dict[str, str]]] = []
    current: tuple[str, dict[str, str]] | None = None
    for line in build_ninja.read_text(encoding="utf-8").splitlines():
        if line.startswith("build "):
            current = (line, {})
            blocks.append(current)
        elif current is not None and line.startswith("  ") and " = " in line:
            key, _, value = line.strip().partition(" = ")
            current[1][key] = value
        elif not line.strip():
            current = None
    return blocks


def closure_targets(closures: Path) -> dict[str, list[dict]]:
    """Target name -> every unit that mirrors part of it (render-2d and
    render-3d each carry a slice of pulp-render)."""
    out: dict[str, list[dict]] = {}
    for manifest in sorted(closures.glob("*.json")):
        data = json.loads(manifest.read_text(encoding="utf-8"))
        for target in data.get("targets", []):
            out.setdefault(target["name"], []).append(
                {"unit": manifest.stem, "has_sources": bool(target.get("sources"))})
    return out


def vellum_archive(vellum_build: Path, unit: str, target: str) -> Path:
    ident = re.sub(r"[^A-Za-z0-9_]", "_", f"vellum-mirror-rehearsal-{unit}-{target}")
    return vellum_build / f"lib{ident}.a"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def object_sources(blocks, pulp_build: Path, pulp_src: Path) -> dict[str, str]:
    """Object file (absolute) -> source path relative to the Pulp checkout."""
    out = {}
    for header, _ in blocks:
        if "_COMPILER__" not in header or not header.startswith("build "):
            continue
        obj = header[len("build "):].split(":", 1)[0].strip()
        src = header.split(":", 1)[1].split()[1]
        src_path = (Path(src) if Path(src).is_absolute() else pulp_build / src).resolve()
        if src_path.is_relative_to(pulp_src):
            out[str((pulp_build / obj).resolve())] = src_path.relative_to(pulp_src).as_posix()
    return out


def header_census(depfile: Path, mirror: Path, pulp_src: Path, mirrored_files: set[str]) -> dict:
    text = depfile.read_text(encoding="utf-8").replace("\\\n", " ")
    deps = [Path(d) for d in text.split(":", 1)[1].split()]
    out = {"mirror": [], "mirrored_from_pulp": [], "pulp_unmirrored": [], "other": []}
    for dep in deps:
        dep = dep.resolve()
        if dep.is_relative_to(mirror):
            out["mirror"].append(str(dep))
        elif dep.is_relative_to(pulp_src):
            rel = dep.relative_to(pulp_src).as_posix()
            if rel.startswith("tools/import-design/"):
                out["other"].append(str(dep))  # the instrument's own sources
            elif rel in mirrored_files:
                out["mirrored_from_pulp"].append(rel)
            else:
                out["pulp_unmirrored"].append(rel)
        else:
            out["other"].append(str(dep))
    out["pulp_unmirrored"] = sorted(set(out["pulp_unmirrored"]))
    return out


def mirrored_file_set(closures: Path) -> set[str]:
    files: set[str] = set()
    for manifest in closures.glob("*.json"):
        files.update(json.loads(manifest.read_text(encoding="utf-8")).get("files", []))
    return files


def build_tool(tool: str, args, blocks, closures, out: Path) -> dict:
    pulp_build, pulp_src = args.pulp_build.resolve(), args.pulp_source.resolve()
    mirror = (args.vellum_source / "mirror").resolve()
    mirrored_files = mirrored_file_set(args.closures)
    obj_sources = object_sources(blocks, pulp_build, pulp_src)
    objects_dir = out / "obj" / tool
    objects_dir.mkdir(parents=True, exist_ok=True)
    compiled = []
    link_block = None
    for header, var in blocks:
        if header.startswith(f"build {TOOL_DIR}/CMakeFiles/{tool}.dir/") and "_COMPILER__" in header:
            src = header.split(":", 1)[1].split()[1]
            src_path = Path(src) if Path(src).is_absolute() else pulp_build / src
            if src_path.resolve().is_relative_to(pulp_src):
                rel_src = src_path.resolve().relative_to(pulp_src).as_posix()
                if rel_src in mirrored_files:
                    src_path = mirror / rel_src  # a mirrored file the instrument compiles directly
            src = str(src_path)
            obj = objects_dir / (Path(src).name + ".o")
            includes = []
            redirected = 0
            retained = []
            tokens = shlex.split(var.get("INCLUDES", ""))
            joined = []
            for i, token in enumerate(tokens):  # "-isystem DIR" -> "-isystemDIR"
                if token in ("-isystem", "-I") and i + 1 < len(tokens):
                    tokens[i + 1] = token + tokens[i + 1]
                    continue
                joined.append(token)
            for flag in joined:
                for prefix in ("-I", "-isystem"):
                    if flag.startswith(prefix) and flag != prefix:
                        path = flag[len(prefix):]
                        break
                else:
                    includes.append(flag)
                    continue
                if path.startswith(str(pulp_src) + "/"):
                    rel = Path(path).relative_to(pulp_src)
                    candidate = mirror / rel
                    if candidate.is_dir():
                        # Mirror first; the Pulp root stays behind it for headers of
                        # units the mirror does not carry (view-audio shares
                        # core/view/include). The dependency census below fails any
                        # mirrored header that still resolved from Pulp.
                        includes.append(prefix + str(candidate))
                        redirected += 1
                        retained.append(path)
                        includes.append(flag)
                        continue
                if path.startswith((str(pulp_src) + "/", str(pulp_build) + "/")):
                    retained.append(path)  # Pulp-owned or generated: a declared build-machine input
                includes.append(flag)
            depfile = obj.with_suffix(".d")
            rule = header.split(":", 1)[1].split()[0]
            compiler = "clang" if rule.startswith("C_COMPILER__") else "clang++"
            argv = [compiler, *shlex.split(var.get("DEFINES", "")), *includes, *shlex.split(var.get("FLAGS", "")),
                    "-MD", "-MF", str(depfile),
                    "-c", src, "-o", str(obj)]
            result = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8")
            if result.returncode != 0:
                raise SystemExit(f"{tool}: compile failed for {src}:\n{result.stderr[-4000:]}")
            headers = header_census(depfile, mirror, pulp_src, mirrored_files)
            if headers["mirrored_from_pulp"]:
                raise SystemExit(f"{tool}: mirrored headers resolved from Pulp, not the mirror: "
                                 f"{headers['mirrored_from_pulp'][:10]}")
            compiled.append({"source": src, "headers": {k: (len(v) if k != "pulp_unmirrored" else v)
                                                         for k, v in headers.items()}, "object": str(obj), "include_roots_redirected_to_mirror": redirected,
                             "pulp_include_roots_retained": retained})
        if header.startswith(f"build {TOOL_DIR}/{tool}:") and "_LINKER__" in header:
            link_block = var
    if link_block is None or not compiled:
        raise SystemExit(f"{tool}: no compile/link rule in {args.pulp_build}/build.ninja")
    swapped, kept, libs = {}, [], []
    for token in shlex.split(link_block["LINK_LIBRARIES"]):
        if token.endswith(".a") and not token.startswith("-"):
            path = Path(token) if Path(token).is_absolute() else pulp_build / token
            target = re.sub(r"^lib", "", path.stem)
            units = closures.get(target)
            if units is not None:
                replacements = []
                for info in units:
                    if not info["has_sources"]:
                        continue  # header-only closure target: nothing to link
                    repl = vellum_archive(args.vellum_build.resolve(), info["unit"], target)
                    if not repl.is_file():
                        raise SystemExit(f"{tool}: Vellum archive missing for mirrored target {target}: {repl}")
                    replacements.append(str(repl))
                swapped[str(path)] = replacements
                libs.extend(replacements)
                continue
            kept.append(str(path))
            libs.append(str(path))
        else:
            libs.append(token)
    exe = out / "bin" / tool
    exe.parent.mkdir(parents=True, exist_ok=True)
    link_map = out / f"{tool}.map"
    argv = ["clang++", *shlex.split(link_block.get("FLAGS", "")), *shlex.split(link_block.get("LINK_FLAGS", "")),
            *(c["object"] for c in compiled), "-o", str(exe), *libs, f"-Wl,-map,{link_map}"]
    result = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", cwd=pulp_build)
    if result.returncode != 0:
        raise SystemExit(f"{tool}: link failed:\n{result.stderr[-6000:]}")
    census = link_census(link_map, swapped, kept_member_sources(kept, obj_sources), mirrored_files)
    if census["kept_objects_from_mirrored_sources"]:
        raise SystemExit(f"{tool}: a kept Pulp archive supplied objects compiled from mirrored sources: "
                         f"{census['kept_objects_from_mirrored_sources']}")
    if census["pulp_mirrored_objects"]:
        raise SystemExit(f"{tool}: {census['pulp_mirrored_objects']} objects still came from Pulp archives of mirrored targets")
    if not census["vellum_objects"]:
        raise SystemExit(f"{tool}: no object was linked from a Vellum archive (vacuous adapter)")
    return {"tool": tool, "binary": str(exe), "sha256": sha256(exe), "compiled": compiled,
            "swapped_archives": swapped, "kept_archives": kept, "census": census}


def kept_member_sources(kept: list[str], obj_sources: dict[str, str]) -> dict[str, dict[str, str]]:
    """archive -> {member name -> source}: ar members are basenames of the
    objects CMake compiled for that archive's target."""
    out: dict[str, dict[str, str]] = {}
    for archive in kept:
        target_dir = str(Path(archive).parent.resolve())
        target = re.sub(r"^lib", "", Path(archive).stem)
        prefix = f"{target_dir}/CMakeFiles/{target}.dir/"
        out[archive] = {Path(o).name: src for o, src in obj_sources.items() if o.startswith(prefix)}
    return out


def link_census(link_map: Path, swapped: dict[str, list[str]],
                kept_sources: dict[str, dict[str, str]], mirrored_files: set[str]) -> dict:
    """Count linked object files per archive from the ld64 map's object table."""
    vellum = {a for archives in swapped.values() for a in archives}
    pulp_mirrored = set(swapped.keys())
    counts: dict[str, int] = {}
    from_mirrored: list[str] = []
    in_objects = False
    for line in link_map.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("# Object files:"):
            in_objects = True
            continue
        if in_objects and line.startswith("# Sections:"):
            break
        if in_objects:
            m = re.match(r"\[\s*\d+\]\s+(.+?)(\((.+)\))?$", line.strip())
            if m:
                archive = m.group(1)
                counts[archive] = counts.get(archive, 0) + 1
                member = m.group(3)
                source = (kept_sources.get(archive) or {}).get(member or "")
                if source in mirrored_files:
                    from_mirrored.append(f"{Path(archive).name}({member}) <- {source}")
    return {"vellum_objects": sum(n for a, n in counts.items() if a in vellum),
            "pulp_mirrored_objects": sum(n for a, n in counts.items() if a in pulp_mirrored),
            "per_vellum_archive": {Path(a).name: n for a, n in counts.items() if a in vellum},
            "kept_objects_from_mirrored_sources": from_mirrored,
            "kept_archive_objects": sum(n for a, n in counts.items() if a not in vellum and a.endswith(".a"))}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pulp-build", required=True, type=Path, help="Pulp reference build dir (same origin commit)")
    parser.add_argument("--pulp-source", required=True, type=Path)
    parser.add_argument("--vellum-source", required=True, type=Path)
    parser.add_argument("--vellum-build", required=True, type=Path, help="Vellum build dir holding rehearsal archives")
    parser.add_argument("--closures", required=True, type=Path)
    parser.add_argument("--tool", action="append", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    blocks = ninja_blocks(args.pulp_build / "build.ninja")
    closures = closure_targets(args.closures)
    results = [build_tool(tool, args, blocks, closures, args.out.resolve()) for tool in args.tool]
    provenance = {"schema": "vellum.parity.mirror-observers.v1", "dry_run": True,
                  "vellum_source": str(args.vellum_source.resolve()),
                  "vellum_head": subprocess.check_output(["git", "-C", str(args.vellum_source), "rev-parse", "HEAD"],
                                                         text=True).strip(),
                  "pulp_reference_build": str(args.pulp_build.resolve()),
                  "closures": str(args.closures.resolve()), "tools": results}
    (args.out / "provenance.json").write_text(json.dumps(provenance, indent=1) + "\n", encoding="utf-8")
    for r in results:
        print(f"{r['tool']}: {r['census']['vellum_objects']} objects from {len(r['swapped_archives'])} Vellum archives, "
              f"0 from Pulp mirrored archives; {len(r['kept_archives'])} non-mirrored archives kept; sha256 {r['sha256'][:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
