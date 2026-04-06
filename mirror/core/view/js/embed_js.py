#!/usr/bin/env python3
"""Generate a C++ header that embeds JS preludes as concatenated raw strings."""

from __future__ import annotations

import pathlib
import sys

# Keep raw string chunks comfortably below MSVC's per-literal limit.
# The generator preserves exact JS bytes, so smaller chunks are safe.
CHUNK_SIZE = 4000


def var_name_for(path: pathlib.Path) -> str:
    return path.stem.replace("-", "_")


def chunk_text(text: str, chunk_size: int) -> list[str]:
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)] or [""]


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: embed_js.py <output.hpp> <input1.js> [input2.js ...]", file=sys.stderr)
        return 1

    output_path = pathlib.Path(argv[1])
    input_paths = [pathlib.Path(arg) for arg in argv[2:]]

    parts = [
        "#pragma once\n",
        "// Auto-generated from JS prelude files — do not edit\n",
        "namespace pulp::view::preludes {\n\n",
    ]

    for input_path in input_paths:
        text = input_path.read_text(encoding="utf-8")
        parts.append(f"static const char* {var_name_for(input_path)} =\n")
        for chunk in chunk_text(text, CHUNK_SIZE):
            parts.append('R"__JS__(')
            parts.append(chunk)
            parts.append(')__JS__"\n')
        parts.append(";\n\n")

    parts.append("} // namespace pulp::view::preludes\n")
    output_path.write_text("".join(parts), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
