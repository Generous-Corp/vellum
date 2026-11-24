#!/usr/bin/env python3
"""Generate the C++ definitions of Vellum's installed web-compat preludes.

The consumer chooses the namespace, the declaration header name, and the
inflate function that the generated `source()` calls. The encoding (zlib
level 9, array layout, variable names, recorded SHA-256) matches the
framework's embed_js.py byte for byte; a packaging test compares the two.
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import sys
import zlib

COMPRESSION_LEVEL = 9
BYTES_PER_LINE = 20

SOURCE_DEFINITION = """std::string source(const EmbeddedPrelude& prelude) {{
    auto text = {inflate}(prelude.deflated, prelude.deflated_size, prelude.source_size);
    if (!text) {{
        throw std::runtime_error(std::string("embedded JS prelude is corrupt: ") +
                                 prelude.file_name);
    }}
    return std::move(*text);
}}

"""


def var_name_for(path: pathlib.Path) -> str:
    return path.stem.replace("-", "_")


def byte_lines(data: bytes) -> list[str]:
    if not data:
        return ["    0"]
    return [
        "    " + ",".join(str(b) for b in data[i : i + BYTES_PER_LINE]) + ","
        for i in range(0, len(data), BYTES_PER_LINE)
    ]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="vellum_embed_preludes.py")
    parser.add_argument("output", type=pathlib.Path)
    parser.add_argument("inputs", nargs="+", type=pathlib.Path)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--header", required=True,
                        help="declaration header the generated source includes")
    parser.add_argument("--inflate-header", required=True,
                        help="include spelling, e.g. <vellum/runtime/zip.hpp>")
    parser.add_argument("--inflate-function", required=True)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    parts = [
        "// Auto-generated from the Vellum web-compat preludes, do not edit\n",
        f'#include "{args.header}"\n',
        f"#include {args.inflate_header}\n#include <stdexcept>\n\n",
        f"namespace {args.namespace} {{\n\n",
    ]
    names = []
    for input_path in args.inputs:
        source = input_path.read_bytes()
        source.decode("utf-8")
        deflated = zlib.compress(source, COMPRESSION_LEVEL)
        var = var_name_for(input_path)
        names.append(var)
        parts.append(f"static const unsigned char {var}_deflated[] = {{\n")
        parts.append("\n".join(byte_lines(deflated)))
        parts.append("\n};\n")
        parts.append(
            f"const EmbeddedPrelude {var}{{\"{input_path.name}\", {var}_deflated, "
            f"{len(deflated)}, {len(source)}, \"{hashlib.sha256(source).hexdigest()}\"}};\n\n")
    parts.append("const EmbeddedPrelude* const kAll[] = {\n")
    parts.extend(f"    &{var},\n" for var in names)
    parts.append("};\n")
    parts.append(f"const std::size_t kAllCount = {len(names)};\n\n")
    parts.append(SOURCE_DEFINITION.format(inflate=args.inflate_function))
    parts.append(f"}}  // namespace {args.namespace}\n")
    args.output.write_text("".join(parts), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
