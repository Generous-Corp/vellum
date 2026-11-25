#!/usr/bin/env python3
"""A planner stand-in for the per-platform target probe.

The real planner fetches and digest-checks every pinned input, which a probe of
the mirror's platform rules must not need. This writes the smallest plan that
makes VellumMirror.cmake create the targets whose platform properties the probe
reads: view-core (the plug-in editor host sources), view-script (the V8
provider path) and canvas (the Skia unit that links the provider). Foundation and state-core
exist because the module's runtime image list names them.
"""
import argparse
import pathlib


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo")
    parser.add_argument("--deps-dir")
    parser.add_argument("--generated-dir", required=True)
    parser.add_argument("--skia-dir")
    parser.add_argument("--skia-asset-sha256")
    parser.add_argument("--cmake-output", required=True)
    parser.add_argument("--json-output")
    parser.add_argument("--platform")
    parser.add_argument("--system")
    parser.add_argument("--processor")
    parser.add_argument("--apple-sdk", nargs="?")
    parser.add_argument("--in-tree-definitions", action="append")
    args = parser.parse_args()
    generated = pathlib.Path(args.generated_dir)
    for name in ("build-info", "choc", "guard"):
        (generated / name).mkdir(parents=True, exist_ok=True)
    (generated / "guard/manifest.txt").write_text("", encoding="utf-8")
    sources = {}
    for target in ("pulp-view-core", "pulp-view-script", "pulp-canvas", "pulp-runtime", "pulp-state"):
        source = generated / f"{target}.cpp"
        source.write_text("namespace { const int probe = 0; }\n", encoding="utf-8")
        sources[target] = source
    repo = args.repo
    lines = [
        f'set(VELLUM_MIRROR_RECEIPT "provenance/mirror/receipts/index.json")',
        'set(VELLUM_MIRROR_CLOSURES "")',
        f'set(VELLUM_MIRROR_SOURCE_DIR "{repo}/framework")',
        'set(VELLUM_MIRROR_ROOT_NAMESPACE "vellum")',
        f'set(VELLUM_MIRROR_BUILD_INFO_DIR "{generated}/build-info")',
        f'set(VELLUM_MIRROR_CHOC_ROOT "{generated}/choc")',
        'set(VELLUM_MIRROR_CHOC_HEADERS "")',
        'set(VELLUM_MIRROR_LICENSED_DEPENDENCIES "")',
        'set(VELLUM_MIRROR_DEPENDENCIES "")',
        'set(VELLUM_MIRROR_BINARY_DATA_TARGETS "")',
        f'set(VELLUM_MIRROR_GPU_GUARD_CONFIG_DIR "{generated}/guard")',
        f'set(VELLUM_MIRROR_GPU_GUARD_MANIFEST "{generated}/guard/manifest.txt")',
        'set(VELLUM_MIRROR_GPU_GUARD_COUNT 0)',
        'set(VELLUM_MIRROR_UNITS "foundation;state-core;view-core;view-script;canvas")',
        'set(VELLUM_MIRROR_EXECUTABLES "")',
    ]
    units = {"foundation": ("pulp-runtime", False),
             "state-core": ("pulp-state", False),
             "view-core": ("pulp-view-core", False),
             "view-script": ("pulp-view-script", False),
             "canvas": ("pulp-canvas", True)}
    for unit, (target, skia) in units.items():
        ukey = unit.upper().replace("-", "_")
        tkey = target.upper().replace("-", "_")
        lines += [
            f'set(VELLUM_MIRROR_UNIT_{ukey}_TARGETS "{target}")',
            f'set(VELLUM_MIRROR_UNIT_{ukey}_DEFINITIONS "")',
            f'set(VELLUM_MIRROR_TARGET_{tkey}_UNIT "{unit}")',
            f'set(VELLUM_MIRROR_TARGET_{tkey}_TYPE "static")',
            f'set(VELLUM_MIRROR_TARGET_{tkey}_SOURCES "{sources[target]}")',
            f'set(VELLUM_MIRROR_TARGET_{tkey}_SKIA {"ON" if skia else "OFF"})',
            f'set(VELLUM_MIRROR_TARGET_{tkey}_OPTION_SOURCE_COUNT 0)',
            f'set(VELLUM_MIRROR_TARGET_{tkey}_INTERNAL_LINKS "")',
            f'set(VELLUM_MIRROR_TARGET_{tkey}_INCLUDE_DIRS "")',
            f'set(VELLUM_MIRROR_TARGET_{tkey}_IN_TREE_INTERFACES "")',
            f'set(VELLUM_MIRROR_TARGET_{tkey}_EXTERNAL_LINKS "")',
            f'set(VELLUM_MIRROR_TARGET_{tkey}_DEFINITIONS "")',
            f'set(VELLUM_MIRROR_TARGET_{tkey}_DECLARED OFF)',
        ]
    pathlib.Path(args.cmake_output).write_text("\n".join(lines) + "\n", encoding="utf-8")
    if args.json_output:
        pathlib.Path(args.json_output).write_text("{}\n", encoding="utf-8")
    print("stub mirror plan: foundation, state-core, view-core, view-script, canvas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
