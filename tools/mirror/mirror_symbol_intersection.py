#!/usr/bin/env python3
"""Measure the defined-symbol intersection of renamed mirror archives and native ones.

Reads every symbol each archive DEFINES (external, private external and local:
`nm -U -P`, with `nm -m` counting the private externs separately),
demangles it (`c++filt`), respells the mirror's root namespace through the
rename (`OLD::` -> `NEW::`), and reports the names both sides would define
under the shared namespaces.

A zero intersection proves nothing unless both sides are seen, so the run fails
(exit 3) when either side has no symbol under `--namespace`, and `--plant`
copies one mirror name into the native set to show the intersection becomes
non-zero.

  mirror_symbol_intersection.py --old pulp --new vellum --namespace vellum::runtime \\
      --mirror libpulp-runtime.a ... --native libvellum-runtime.a ...

Objective-C classes come from class metadata (`otool -oV`, the
`__objc_classlist` names), not from symbol names, because a class is one per
process whatever its symbols' visibility. `--expect-objc-classes N` is the
control that the reader sees them: on a pre-rename mirror build it must report
all N declared classes.

Exit: 0 no intersection, 1 intersection found or the class control failed,
3 a side is blind.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

DEFINED = set("TtDdBbSsCcGgRrVvWwIiUu") - set("Uu")


def defined_symbols(archive: str) -> set[str]:
    out = subprocess.run(["nm", "-U", "-P", archive], capture_output=True, text=True, errors="replace").stdout
    names = set()
    for line in out.splitlines():
        fields = line.split()
        if len(fields) >= 2 and len(fields[1]) == 1 and fields[1] in DEFINED:
            names.add(fields[0])
    return names


def private_externs(archive: str) -> int:
    out = subprocess.run(["nm", "-m", archive], capture_output=True, text=True, errors="replace").stdout
    return sum(1 for line in out.splitlines() if "private external" in line and "(undefined)" not in line)


# An `__objc_classlist` entry: "<address> <target> _OBJC_CLASS_$_<Name>". The
# class's own `name` line is not used: its label carries a per-object suffix
# (`l_OBJC_CLASS_NAME_.60`) that a fixed pattern silently misses.
OBJC_CLASS_NAME = re.compile(r"^[0-9a-f]+\s+0x[0-9a-f]+\s+_OBJC_CLASS_\$_(\S+)$")


def objc_classes(archive: str) -> set[str]:
    """Class names from `otool -oV`'s `__objc_classlist` metadata (metaclasses share the name)."""
    out = subprocess.run(["otool", "-oV", archive], capture_output=True, text=True, errors="replace").stdout
    names = set()
    in_classlist = False
    for line in out.splitlines():
        if line.startswith("Contents of"):
            in_classlist = "__objc_classlist" in line
        elif in_classlist:
            m = OBJC_CLASS_NAME.match(line)
            if m:
                names.add(m.group(1))
    return names


def demangle(names: set[str]) -> set[str]:
    if not names:
        return set()
    ordered = sorted(names)
    out = subprocess.run(["c++filt", "-_"], input="\n".join(ordered), capture_output=True, text=True, errors="replace").stdout
    return set(out.splitlines())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--old", required=True)
    parser.add_argument("--new", required=True)
    parser.add_argument("--namespace", action="append", required=True,
                        help="a shared namespace to compare, spelled with --new (repeatable)")
    parser.add_argument("--mirror", nargs="+", required=True)
    parser.add_argument("--native", nargs="+", required=True)
    parser.add_argument("--plant", action="store_true", help="control: plant one mirror name into the native set")
    parser.add_argument("--old-class-prefix", help="Objective-C class prefix before the rename (e.g. Pulp)")
    parser.add_argument("--new-class-prefix", help="and after it (e.g. VellumMirror)")
    parser.add_argument("--list-unrenamed-c", action="store_true",
                        help="also list the external C symbols the mirror archives define: the rename does not touch "
                             "them, so they collide with Pulp's copies in one image (MED-7)")
    parser.add_argument("--expect-objc-classes", type=int,
                        help="control: the mirror archives must define exactly this many Objective-C classes")
    args = parser.parse_args()

    old = re.compile(rf"(?<![\w]){re.escape(args.old)}::")
    under = re.compile("|".join(rf"(?<![\w]){re.escape(ns)}::" for ns in args.namespace))
    mirror = {old.sub(f"{args.new}::", s) for a in args.mirror for s in demangle(defined_symbols(a))}
    native = {s for a in args.native for s in demangle(defined_symbols(a))}
    mirror = {s for s in mirror if under.search(s)}
    native = {s for s in native if under.search(s)}
    if args.plant and mirror:
        native.add(sorted(mirror)[0])
    shared = sorted(mirror & native)
    mirror_classes = {c for a in args.mirror for c in objc_classes(a)}
    native_classes = {c for a in args.native for c in objc_classes(a)}
    if args.old_class_prefix and args.new_class_prefix:
        respelled = {args.new_class_prefix + c[len(args.old_class_prefix):] if c.startswith(args.old_class_prefix)
                     else c for c in mirror_classes}
    else:
        respelled = mirror_classes
    shared_classes = sorted(respelled & native_classes)
    report = {"namespaces": args.namespace, "mirror_defined": len(mirror), "native_defined": len(native),
              "intersection": len(shared), "examples": shared[:20], "planted": args.plant,
              "private_externs": {"mirror": sum(private_externs(a) for a in args.mirror),
                                  "native": sum(private_externs(a) for a in args.native)},
              "objc_classes": {"mirror": sorted(mirror_classes), "native": sorted(native_classes),
                               "shared_after_rename": shared_classes}}
    if args.list_unrenamed_c:
        c_symbols = set()
        for archive in args.mirror:
            out = subprocess.run(["nm", "-gU", "-P", archive], capture_output=True, text=True, errors="replace").stdout
            for line in out.splitlines():
                fields = line.split()
                if len(fields) >= 2 and len(fields[1]) == 1 and fields[1] in DEFINED:
                    name = fields[0]
                    if not name.startswith(("__Z", "_OBJC_", "__OBJC_", "_objc_", "l_", "L")) and ":" not in name:
                        c_symbols.add(name)
        report["unrenamed_c_symbols"] = {"count": len(c_symbols), "names": sorted(c_symbols)}
    print(json.dumps(report, indent=2))
    if not mirror or not native:
        print("error: a side defines nothing under the namespaces; the measurement is blind", file=sys.stderr)
        return 3
    if args.expect_objc_classes is not None and len(mirror_classes) != args.expect_objc_classes:
        print(f"error: the mirror archives define {len(mirror_classes)} Objective-C classes, "
              f"expected {args.expect_objc_classes}", file=sys.stderr)
        return 1
    return 1 if shared or shared_classes else 0


if __name__ == "__main__":
    sys.exit(main())
