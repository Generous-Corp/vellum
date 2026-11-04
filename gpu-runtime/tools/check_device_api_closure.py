#!/usr/bin/env python3
"""Check that the gpu-runtime device API stays at the bottom of the GPU stack.

The DeviceApi headers are consumed by GPU audio and by renderers alike, so they
may name nothing above themselves. For every header under <include>/gpu_runtime:

  * each #include is a C++ standard header or another gpu_runtime header that
    exists, written with angle brackets;
  * the code (comments and string literals removed) names no Pulp, Vellum,
    WebGPU, Dawn or Skia identifier;
  * every namespace it opens is spelled from gpu_runtime (gpu_runtime or
    gpu_runtime::inner), so no header adds names outside the API's namespace.

Usage: check_device_api_closure.py <include-dir>
Exit 0 when the headers are closed, 1 with one line per violation otherwise.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

STANDARD_HEADERS = frozenset({
    "algorithm", "array", "atomic", "bit", "cassert", "cstddef", "cstdint",
    "cstdio", "cstdlib", "cstring", "chrono", "cmath", "functional", "limits",
    "memory", "mutex", "optional", "span", "string", "string_view",
    "type_traits", "utility", "variant", "vector",
})

INCLUDE = re.compile(r'^\s*#\s*include\s*([<"])([^>"]+)[>"]', re.MULTILINE)
NAMESPACE = re.compile(r"\bnamespace\s+([A-Za-z_][A-Za-z0-9_:]*)\s*\{")
# Identifiers that would tie the API to a framework or to the GPU libraries.
FORBIDDEN = re.compile(
    r"\b(?:pulp|vellum|wgpu|WGPU\w*|dawn|Dawn\w*::|Sk[A-Z]\w*|skgpu)\b"
    r"|\b(?:pulp|vellum|wgpu|dawn)::")


def strip_comments_and_strings(text: str) -> str:
    """Blank out comments, string and character literals, keeping line breaks."""
    pattern = re.compile(
        r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'',
        re.DOTALL)

    def blank(match: re.Match[str]) -> str:
        return re.sub(r"[^\n]", " ", match.group(0))

    return pattern.sub(blank, text)


def check(include_dir: Path) -> list[str]:
    root = include_dir / "gpu_runtime"
    if not root.is_dir():
        return [f"{root}: the gpu_runtime header directory is missing"]
    headers = sorted(root.rglob("*.hpp"))
    if not headers:
        return [f"{root}: no device API headers found"]
    problems: list[str] = []
    for header in headers:
        label = header.relative_to(include_dir).as_posix()
        text = header.read_text(encoding="utf-8")
        for match in INCLUDE.finditer(text):
            delimiter, target = match.groups()
            line = text.count("\n", 0, match.start()) + 1
            if delimiter == '"':
                problems.append(f"{label}:{line}: quoted include {target!r}; use <gpu_runtime/...>")
            elif target.startswith("gpu_runtime/"):
                if not (include_dir / target).is_file():
                    problems.append(f"{label}:{line}: includes missing header <{target}>")
            elif target not in STANDARD_HEADERS:
                problems.append(f"{label}:{line}: includes <{target}>, which is outside the device API")
        code = strip_comments_and_strings(text)
        for match in FORBIDDEN.finditer(code):
            line = code.count("\n", 0, match.start()) + 1
            problems.append(f"{label}:{line}: names {match.group(0)!r}, which is above the device API")
        for match in NAMESPACE.finditer(code):
            name = match.group(1)
            line = code.count("\n", 0, match.start()) + 1
            if name != "gpu_runtime" and not name.startswith("gpu_runtime::"):
                problems.append(f"{label}:{line}: opens namespace {name}; use gpu_runtime")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: check_device_api_closure.py <include-dir>", file=sys.stderr)
        return 2
    problems = check(Path(argv[1]))
    for problem in problems:
        print(f"gpu-runtime device API closure: {problem}", file=sys.stderr)
    if not problems:
        print("gpu-runtime device API closure: ok")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
