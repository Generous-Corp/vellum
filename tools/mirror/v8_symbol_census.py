#!/usr/bin/env python3
"""Prove the sealed libv8 and the Skia/Dawn provider cannot bind each other's ICU or Abseil.

Both images carry their own copies of ICU (the same versioned icu_78
namespace), Abseil and zlib. In one process that is safe only while those
copies stay private to their image: the macOS dynamic linker coalesces
exported weak definitions across every loaded image, so one exported copy
would let the other image's references bind to it. The census reads both
images with `nm -m` and fails unless:

  * every symbol libv8 exports is under the v8:: or cppgc:: embedder API
    (the v8-builder seal's export allow-list), so none of its ICU, Abseil or
    zlib can coalesce with the provider's;
  * every symbol both images define and export is weak in both, so the only
    shared names are inline V8 header definitions compiled with the same
    consumer_defines, never a second strong definition;
  * the provider imports V8 symbols, every one of them from libv8;
  * libv8 imports nothing but system libraries.

It also reports how many ICU, Abseil and zlib symbols the provider itself
exports, which libv8 cannot reach under the rules above.

  v8_symbol_census.py --provider "$provider_path" --v8 lib/libv8.dylib

`--plant` adds an exported Abseil name to libv8's set, the control that the
allow-list check fails. Exit: 0 clean, 1 a rule fails, 3 an image shows no
symbols to judge.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field

# The v8-builder seal's exported_symbols_list, as mangled prefixes: functions,
# const member functions, vtables, typeinfo and typeinfo names.
V8_ALLOW = re.compile(r"^__Z(?:N|NK|TVN|TIN|TSN)(?:2v8|6cppgc)")
# Roots of the libraries that both images carry privately.
SHARED_LIBRARY_ROOT = re.compile(r"^(?:__Z[A-Z]*N\d*(?:icu_\d+|absl)|_u_|_ucol_|_ubrk_|_uloc_|_deflate|_inflate|_crc32|_adler32)")
V8_ROOT = re.compile(r"^__Z[A-Z]*N(?:K)?(?:2v8|6cppgc)")
SYSTEM_LIBRARIES = {"libc++", "libc++abi", "libSystem", "Foundation", "CoreFoundation", "libobjc"}
PLANTED = "__ZN4absl12lts_2025012714CordzInfoE"
NM_LINE = re.compile(
    r"^(?:[0-9a-f]+\s+)?\((?P<section>[^)]*)\)\s+(?P<weak>weak\s+)?(?P<scope>external|non-external|private external)"
    r"(?:\s+\[[^\]]*\])?\s+(?P<name>\S+)(?:\s+\(from (?P<library>[^)]+)\))?")


@dataclass
class Image:
    exported: dict[str, bool] = field(default_factory=dict)  # name -> weak
    imports: dict[str, str] = field(default_factory=dict)  # name -> library


def parse_nm(listing: str) -> Image:
    """Exported definitions and imports from `nm -m` output."""
    image = Image()
    for line in listing.splitlines():
        match = NM_LINE.match(line.strip())
        if not match:
            continue
        name = match["name"]
        if match["section"] == "undefined":
            if match["library"]:
                image.imports[name] = match["library"]
        elif match["scope"] == "external":
            image.exported[name] = bool(match["weak"])
    return image


def judge(provider: Image, v8: Image) -> tuple[list[str], dict[str, object]]:
    errors: list[str] = []
    leaks = sorted(name for name in v8.exported if not V8_ALLOW.match(name))
    if leaks:
        errors.append(f"libv8 exports {len(leaks)} symbols outside v8:: and cppgc::, e.g. {leaks[:3]}")
    shared = sorted(set(provider.exported) & set(v8.exported))
    strong = [name for name in shared if not (provider.exported[name] and v8.exported[name])]
    if strong:
        errors.append(f"{len(strong)} symbols are defined by both images and strong in one, e.g. {strong[:3]}")
    v8_imports = {name: library for name, library in provider.imports.items() if V8_ROOT.match(name)}
    if not v8_imports:
        errors.append("the provider imports no V8 symbol; it does not link libv8")
    elsewhere = sorted(name for name, library in v8_imports.items() if library != "libv8")
    if elsewhere:
        errors.append(f"the provider binds {len(elsewhere)} V8 symbols to an image other than libv8, "
                      f"e.g. {elsewhere[:3]}")
    foreign = sorted({library for library in v8.imports.values() if library not in SYSTEM_LIBRARIES})
    if foreign:
        errors.append(f"libv8 imports from non-system images: {foreign}")
    report = {
        "v8_exports": len(v8.exported),
        "v8_exports_outside_allow_list": len(leaks),
        "provider_exports": len(provider.exported),
        "provider_exports_icu_abseil_zlib": sum(1 for n in provider.exported if SHARED_LIBRARY_ROOT.match(n)),
        "provider_v8_imports_from_libv8": sum(1 for lib in v8_imports.values() if lib == "libv8"),
        "shared_weak_definitions": len(shared) - len(strong),
        "shared_strong_definitions": len(strong),
        "v8_import_libraries": sorted(set(v8.imports.values())),
    }
    return errors, report


def read(path: str) -> Image:
    result = subprocess.run(["nm", "-m", path], capture_output=True, text=True, errors="replace")
    if result.returncode != 0:
        raise SystemExit(f"v8-symbol-census: nm -m {path} failed: {result.stderr.strip()}")
    return parse_nm(result.stdout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--v8", required=True)
    parser.add_argument("--plant", action="store_true", help="add an exported Abseil name to libv8 (control)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    provider, v8 = read(args.provider), read(args.v8)
    if not provider.exported or not v8.exported:
        print("v8-symbol-census: an image shows no exported symbols; nothing was judged", file=sys.stderr)
        return 3
    if args.plant:
        v8.exported[PLANTED] = False
    errors, report = judge(provider, v8)
    if args.json:
        print(json.dumps({"errors": errors, **report}, indent=2, sort_keys=True))
    else:
        for key, value in report.items():
            print(f"{key}: {value}")
    for error in errors:
        print(f"v8-symbol-census: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
