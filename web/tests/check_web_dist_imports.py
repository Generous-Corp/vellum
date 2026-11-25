#!/usr/bin/env python3
"""Fail when a module the web-dist page loads is missing from web-dist.

The browser resolves every relative ES module specifier against the file that
imports it, and a missing file surfaces only as a 404 and a page that never
boots. This walks the real import graph, starting at each module script of each
HTML page in the payload and at each shipped module, and reports every specifier that does not resolve to
a file inside the payload. Nothing is listed by hand, so a module added to the
sources but not to the copy list is caught the first time something imports it.
"""

from __future__ import annotations

import argparse
from html.parser import HTMLParser
from pathlib import Path
import re
import sys


STATIC_IMPORT = re.compile(
    r"""(?:^|[;\s}])(?:import|export)\s+(?:[^'"`;]*?\s+from\s+)?['"]([^'"]+)['"]""",
    re.DOTALL,
)
DYNAMIC_IMPORT = re.compile(r"""\bimport\(\s*['"]([^'"]+)['"]\s*\)""")
BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
LINE_COMMENT = re.compile(r"(^|[^:\\'\"])//[^\n]*")


class ModuleScripts(HTMLParser):
    """Collects module script sources and inline module script bodies."""

    def __init__(self) -> None:
        super().__init__()
        self.sources: list[str] = []
        self.inline: list[str] = []
        self._inline_module = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "script":
            return
        values = dict(attrs)
        if values.get("type") != "module":
            return
        if values.get("src"):
            self.sources.append(values["src"] or "")
        else:
            self._inline_module = True
            self.inline.append("")

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._inline_module = False

    def handle_data(self, data: str) -> None:
        if self._inline_module:
            self.inline[-1] += data


def specifiers(source: str) -> tuple[list[str], list[str]]:
    """Return (static, dynamic) import specifiers named by a module's source."""
    stripped = LINE_COMMENT.sub(r"\1", BLOCK_COMMENT.sub("", source))
    return STATIC_IMPORT.findall(stripped), DYNAMIC_IMPORT.findall(stripped)


def resolve(root: Path, importer_dir: Path, specifier: str) -> Path | None:
    """Resolve a browser specifier to a payload path, or None for a bare one."""
    clean = specifier.split("?", 1)[0].split("#", 1)[0]
    if clean.startswith("/"):
        return (root / clean.lstrip("/")).resolve()
    if clean.startswith("./") or clean.startswith("../"):
        return (importer_dir / clean).resolve()
    return None


def missing_modules(root: Path) -> list[str]:
    """Every import in the page graph that does not resolve inside `root`."""
    root = root.resolve()
    problems: list[str] = []
    pending: list[tuple[Path, str]] = []
    pages = sorted(root.rglob("*.html"))
    if not pages:
        return [f"{root}: no HTML page to start from"]
    for page in pages:
        parser = ModuleScripts()
        parser.feed(page.read_text(encoding="utf-8"))
        label = page.relative_to(root).as_posix()
        for src in parser.sources:
            pending.append((page.parent, f"{label} -> {src}"))
        for body in parser.inline:
            static, dynamic = specifiers(body)
            for spec in static + dynamic:
                pending.append((page.parent, f"{label} (inline) -> {spec}"))
    # Every shipped module is importable by a consumer of the payload, so the
    # graph starts at each of them too, not only at what the demo page reaches.
    for module in sorted(root.rglob("*.js")) + sorted(root.rglob("*.mjs")):
        pending.append((module.parent, f"(payload) -> ./{module.name}"))
    seen: set[Path] = set()
    while pending:
        importer_dir, edge = pending.pop()
        specifier = edge.rsplit(" -> ", 1)[1]
        target = resolve(root, importer_dir, specifier)
        if target is None:
            # A bare specifier only loads through an import map, which this
            # payload does not ship. Dynamic bare imports are runtime-guarded
            # host modules (Emscripten's `node:module`) and never run in a page.
            if "(dynamic)" not in edge:
                problems.append(f"{edge}: bare specifier has no import map")
            continue
        if root not in target.parents:
            problems.append(f"{edge}: resolves outside the payload")
            continue
        if not target.is_file():
            problems.append(f"{edge}: missing {target.relative_to(root).as_posix()}")
            continue
        if target in seen or target.suffix not in (".js", ".mjs"):
            continue
        seen.add(target)
        label = target.relative_to(root).as_posix()
        static, dynamic = specifiers(target.read_text(encoding="utf-8", errors="replace"))
        for spec in static:
            pending.append((target.parent, f"{label} -> {spec}"))
        for spec in dynamic:
            pending.append((target.parent, f"{label} (dynamic) -> {spec}"))
    return sorted(problems)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="the built web-dist directory")
    args = parser.parse_args()
    if not args.root.is_dir():
        raise SystemExit(f"web-dist directory not found: {args.root}")
    problems = missing_modules(args.root)
    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1
    print(f"web-dist import graph resolves: {args.root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
