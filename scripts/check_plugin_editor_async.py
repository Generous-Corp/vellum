#!/usr/bin/env python3
"""Fail when plugin-editor code calls a blocking native menu, file dialog or alert.

A modal loop run from inside a host's process stalls the host, so plugin
editors use only the window-parented asynchronous forms: show_menu_async,
FileDialog::open_async / save_async / save_copy_async, show_alert_async.
Blocking forms stay available to apps.

Plugin-editor code is every tracked source file whose name starts with
"plugin_" or contains "_plugin_" (or ends in "_plugin"), plus anything under a
directory named plugin-editor or plugin_editor. Add a path to EXTRA_PATHS when
an editor lives somewhere else. Add "plugin-editor-async: ok" and a reason to a
line to accept it.

Usage: check_plugin_editor_async.py [--root <repo>]
Exit 0 when clean, 1 on findings, 2 when the file list cannot be read.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys

SOURCE_SUFFIXES = {".cpp", ".cc", ".hpp", ".h", ".mm", ".m", ".swift"}
EXTRA_PATHS: set[str] = set()
OPT_OUT = "plugin-editor-async: ok"

# Each pattern names a blocking call. The FileDialog calls are matched by
# their qualified name so a method of another class is not flagged.
FORBIDDEN = [
    (re.compile(r"\bshow_at_view\s*\("), "PopupMenu::show_at_view blocks; use show_menu_async"),
    (re.compile(r"\bPopupMenu\b[^;\n]*\.\s*show\s*\("), "PopupMenu::show blocks; use show_menu_async"),
    (re.compile(r"\bmenu\s*\.\s*show\s*\("), "PopupMenu::show blocks; use show_menu_async"),
    (
        re.compile(r"\bFileDialog\s*::\s*(open_files?|save_file|save_copy|choose_folders?(_with)?)\s*\("),
        "synchronous FileDialog call blocks; use FileDialog::open_async / save_async / save_copy_async",
    ),
    (re.compile(r"\bshow_alert\s*\("), "blocking alert; use show_alert_async"),
    (re.compile(r"\brunModal\b|\bbeginSheetModal\b|\[NSMenu\s+popUp"), "modal native call; use the async service forms"),
]


def is_plugin_editor_path(path: str) -> bool:
    p = Path(path)
    if p.suffix not in SOURCE_SUFFIXES:
        return False
    if path in EXTRA_PATHS:
        return True
    parts = set(p.parts[:-1])
    if parts & {"plugin-editor", "plugin_editor"}:
        return True
    stem = p.stem
    return stem.startswith("plugin_") or "_plugin_" in stem or stem.endswith("_plugin")


def tracked_files(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode(errors="replace").strip() or "git ls-files failed")
    return [name for name in result.stdout.decode().split("\0") if name]


def scan_text(text: str) -> list[tuple[int, str]]:
    findings = []
    for number, line in enumerate(text.splitlines(), 1):
        code = line.split("//", 1)[0]
        if OPT_OUT in line:
            continue
        for pattern, message in FORBIDDEN:
            if pattern.search(code):
                findings.append((number, message))
                break
    return findings


def check(root: Path, files: list[str]) -> list[str]:
    out = []
    for name in files:
        if not is_plugin_editor_path(name):
            continue
        path = root / name
        if not path.is_file():
            continue
        for number, message in scan_text(path.read_text(errors="replace")):
            out.append(f"{name}:{number}: {message}")
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    args = parser.parse_args(argv)
    root = Path(args.root)
    try:
        files = tracked_files(root)
    except RuntimeError as err:
        print(f"check_plugin_editor_async: {err}", file=sys.stderr)
        return 2
    scanned = [f for f in files if is_plugin_editor_path(f)]
    findings = check(root, files)
    for line in findings:
        print(line)
    if findings:
        return 1
    print(f"check_plugin_editor_async: {len(scanned)} plugin-editor files clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
