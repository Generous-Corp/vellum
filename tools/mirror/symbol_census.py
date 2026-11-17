#!/usr/bin/env python3
"""Symbol census over the dylibs an installed Vellum package ships.

Three questions, answered per image set:

* no image defines a `pulp::` symbol (the rename left nothing behind);
* the images do define `vellum::` symbols (the control that the census can
  see the names it counts, so a zero above is not a blind reader);
* no exported symbol and no Objective-C class is defined by two images, apart
  from names listed with --allow. Two images that both export a name give a
  process two definitions, and a class registered twice is a runtime warning
  with an arbitrary winner.

The `pulp::` and `vellum::` counts read the full symbol table (`nm -m`), local
and hidden definitions included. Only exported strong definitions count for the
duplicate check ("external", not weak and not "private external"): a hidden or
weak copy cannot be bound from another image. Objective-C classes come from class metadata (`otool -oV`), because a
class is one per process whatever its symbols' visibility. Images with
identical content (versioned dylib names) are read once.

--plant is the control for the duplicate check: it lists the largest image
twice and the run must then fail. Exit: 0 clean, 1 findings, 3 blind (no
images, no `vellum::` definition, or nm/otool failed on an image).
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Iterable

PULP = re.compile(r"(?<![\w])pulp::")
VELLUM = re.compile(r"(?<![\w])vellum::")
# `nm -m`: "<value> (<segment,section>) <attributes> <name>"
_NM_LINE = re.compile(r"^\s*(?:[0-9a-fA-F]+\s+)?\((?P<section>[^)]*)\)\s+(?P<attrs>.*?)\s*(?P<name>\S+)$")
_ANNOTATION = re.compile(r"\[[^\]]*\]")
_OBJC_CLASS = re.compile(r"^[0-9a-f]+\s+0x[0-9a-f]+\s+_OBJC_CLASS_\$_(\S+)$")
# The class_ro_t's own `name` field sits at the first indent level under `data`.
_CLASS_RO_NAME = re.compile(r"^ {8}name\s+0x[0-9a-f]+\s+(\S+)\s*$")

Nm = Callable[[Path], str]
Otool = Callable[[Path], str]
Demangle = Callable[[list[str]], dict[str, str]]


def exported_strong(listing: str) -> set[str]:
    """Defined, exported, non-weak names of an `nm -m` listing."""
    names: set[str] = set()
    for line in listing.splitlines():
        match = _NM_LINE.match(line)
        if not match or match["section"] == "undefined":
            continue
        # `nm -m` may annotate a definition ("[referenced dynamically]",
        # "[no dead-strip]"); the annotation does not change what it exports.
        if _ANNOTATION.sub("", match["attrs"]).split() == ["external"]:
            names.add(match["name"])
    return names


def all_defined(listing: str) -> set[str]:
    """Every defined name, local, hidden and weak included (for the pulp:: count)."""
    names: set[str] = set()
    for line in listing.splitlines():
        match = _NM_LINE.match(line)
        if match and match["section"] != "undefined":
            names.add(match["name"])
    return names


def objc_classes(listing: str) -> set[str]:
    """Class names from an `otool -oV` listing's `__objc_classlist`.

    Each class is one top-level entry: an address line, then indented class
    metadata. The name is the `name` field of the entry's class_ro_t, which a
    stripped binary keeps even when the entry's own line carries no
    `_OBJC_CLASS_$_` symbol. An entry whose metadata holds no name falls back
    to that symbol; an entry with neither is an error, never a skipped class.
    """
    classes: set[str] = set()
    in_classlist = False
    entry: str | None = None   # the entry's address line, while one is open
    symbol: str | None = None
    name: str | None = None

    def close() -> None:
        nonlocal entry, symbol, name
        if entry is not None:
            found = name or symbol
            if not found:
                raise RuntimeError(f"an Objective-C class entry has no readable name: {entry!r}")
            classes.add(found)
        entry = symbol = name = None

    for line in listing.splitlines():
        if line.startswith("Contents of"):
            close()
            in_classlist = "__objc_classlist" in line
        elif not in_classlist or not line.strip():
            continue
        elif not line[0].isspace():
            close()
            entry = line
            match = _OBJC_CLASS.match(line)
            symbol = match[1] if match else None
        elif name is None:
            match = _CLASS_RO_NAME.match(line)
            if match:
                name = match[1]
    close()
    return classes


def run_nm(image: Path) -> str:
    done = subprocess.run(["nm", "-m", str(image)], capture_output=True, text=True, errors="replace", check=False)
    if done.returncode:
        raise RuntimeError(f"nm failed for {image}: {done.stderr.strip()}")
    return done.stdout


def run_otool(image: Path) -> str:
    done = subprocess.run(["otool", "-oV", str(image)], capture_output=True, text=True, errors="replace",
                          check=False)
    if done.returncode:
        raise RuntimeError(f"otool failed for {image}: {done.stderr.strip()}")
    return done.stdout


def run_demangle(names: list[str]) -> dict[str, str]:
    ordered = sorted(set(names))
    if not ordered:
        return {}
    done = subprocess.run(["c++filt", "-_"], input="\n".join(ordered), capture_output=True, text=True,
                          errors="replace", check=False)
    out = done.stdout.splitlines()
    if done.returncode or len(out) != len(ordered):
        raise RuntimeError(f"c++filt failed or returned {len(out)} names for {len(ordered)}: {done.stderr.strip()}")
    return dict(zip(ordered, out))


def distinct_images(images: Iterable[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for image in images:
        digest = hashlib.sha256(image.read_bytes()).hexdigest()
        if digest not in seen:
            seen.add(digest)
            result.append(image)
    return result


def census(images: list[Path], allow: frozenset[str] = frozenset(), nm: Nm | None = None, otool: Otool | None = None,
           demangle: Demangle | None = None) -> tuple[dict, list[str]]:
    """(report, findings). `images` may repeat an image to plant a duplicate.

    The readers are looked up here, not bound at definition, so a caller or test
    that replaces run_nm, run_otool or run_demangle is honoured.
    """
    nm, otool, demangle = nm or run_nm, otool or run_otool, demangle or run_demangle
    pulp_defs: list[str] = []
    vellum_count = 0
    symbol_owners: dict[str, list[str]] = {}
    class_owners: dict[str, list[str]] = {}
    for position, image in enumerate(images):
        listing = nm(image)
        names = all_defined(listing)
        readable = demangle(list(names))
        pulp_defs += [readable.get(n, n) for n in names if PULP.search(readable.get(n, n))]
        vellum_count += sum(1 for n in names if VELLUM.search(readable.get(n, n)))
        tag = f"{image}#{position}"
        for name in exported_strong(listing):
            symbol_owners.setdefault(name, []).append(tag)
        for cls in objc_classes(otool(image)):
            class_owners.setdefault(cls, []).append(tag)
    duplicate_symbols = {n: o for n, o in symbol_owners.items() if len(o) > 1 and n not in allow}
    duplicate_classes = {c: o for c, o in class_owners.items() if len(o) > 1}
    readable = demangle(list(duplicate_symbols))
    report = {
        "images": len(images),
        "pulp_definitions": len(pulp_defs),
        "vellum_definitions": vellum_count,
        "duplicate_symbols": len(duplicate_symbols),
        "duplicate_classes": len(duplicate_classes),
    }
    findings = [f"pulp:: is defined {len(pulp_defs)} times, e.g. {pulp_defs[0]}"] if pulp_defs else []
    for name, owners in sorted(duplicate_symbols.items())[:20]:
        findings.append(f"{readable.get(name, name)} is exported by {len(owners)} images: {owners}")
    if len(duplicate_symbols) > 20:
        findings.append(f"... and {len(duplicate_symbols) - 20} more duplicated symbols")
    for cls, owners in sorted(duplicate_classes.items()):
        findings.append(f"Objective-C class {cls} is defined by {len(owners)} images: {owners}")
    return report, findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--allow", action="append", default=[], metavar="SYMBOL",
                        help="a mangled name several images may define on purpose (repeatable)")
    parser.add_argument("--plant", action="store_true",
                        help="control: list the largest image twice; the run must fail")
    args = parser.parse_args(argv)
    images = distinct_images(args.images)
    if args.plant and images:
        images = images + [max(images, key=lambda p: p.stat().st_size)]
    try:
        report, findings = census(images, frozenset(args.allow))
    except RuntimeError as error:
        print(f"symbol census: blind, {error}", file=sys.stderr)
        return 3
    print(f"symbol census: {report}")
    for finding in findings:
        print(f"symbol census: {finding}", file=sys.stderr)
    if not images or report["vellum_definitions"] == 0:
        print("symbol census: blind, no vellum:: definition was seen", file=sys.stderr)
        return 3
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
