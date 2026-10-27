#!/usr/bin/env python3
"""Name every difference between two SDK archives that should be identical.

The SDK release job builds the archive twice and requires byte equality. When
the two differ, this prints where: the gzip header, the member order, and each
member whose tar header or content differs. It always exits 1 when it finds a
difference, and says so explicitly when the archives differ only in bytes it
cannot attribute (for example the deflate stream itself).
"""

from __future__ import annotations

import gzip
import hashlib
import sys
import tarfile
import zlib
from pathlib import Path

# Every tar header field a reproducible build must pin, plus the content digest.
MEMBER_FIELDS = ("type", "mode", "uid", "gid", "uname", "gname", "mtime", "size", "linkname")


def member_rows(path: Path) -> list[tuple[str, dict[str, object]]]:
    rows = []
    with tarfile.open(path) as handle:
        for member in handle.getmembers():
            row: dict[str, object] = {field: getattr(member, field) for field in MEMBER_FIELDS}
            if member.isfile():
                stream = handle.extractfile(member)
                row["sha256"] = hashlib.sha256(stream.read() if stream else b"").hexdigest()
            rows.append((member.name, row))
    return rows


def gzip_header(path: Path) -> dict[str, object]:
    data = path.read_bytes()[:10]
    if len(data) < 10 or data[:2] != b"\x1f\x8b":
        return {"magic": data[:2].hex()}
    return {
        "method": data[2],
        "flags": data[3],
        "mtime": int.from_bytes(data[4:8], "little"),
        "xfl": data[8],
        "os": data[9],
    }


def differences(first: Path, second: Path) -> list[str]:
    found: list[str] = []
    header_a, header_b = gzip_header(first), gzip_header(second)
    if header_a != header_b:
        found.append(f"gzip header: {header_a} != {header_b}")
    try:
        rows_a, rows_b = member_rows(first), member_rows(second)
    except (tarfile.TarError, OSError, EOFError, zlib.error) as error:
        found.append(f"cannot read both archives as gzip tar: {error}")
        return found
    names_a, names_b = [name for name, _ in rows_a], [name for name, _ in rows_b]
    if names_a != names_b and sorted(names_a) == sorted(names_b):
        found.append("member order differs")
    a, b = dict(rows_a), dict(rows_b)
    for name in sorted(set(a) | set(b)):
        if name not in a or name not in b:
            found.append(f"{name}: only in {'second' if name not in a else 'first'}")
            continue
        changed = [field for field in a[name] if a[name].get(field) != b[name].get(field)]
        changed += [field for field in b[name] if field not in a[name]]
        if changed:
            detail = ", ".join(f"{field} {a[name].get(field)!r} != {b[name].get(field)!r}" for field in changed)
            found.append(f"{name}: {detail}")
    if not found:
        try:
            with gzip.open(first) as one, gzip.open(second) as two:
                tar_equal = one.read() == two.read()
        except (OSError, EOFError, zlib.error) as error:
            return [f"cannot decompress both archives: {error}"]
        found.append(
            "every tar header and member is equal; the uncompressed tar streams differ"
            if not tar_equal
            else "the tar streams are equal; only the compressed bytes differ"
        )
    return found


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: compare_sdk_archives.py FIRST SECOND", file=sys.stderr)
        return 2
    first, second = Path(argv[1]), Path(argv[2])
    if first.read_bytes() == second.read_bytes():
        print("SDK archives are byte-identical")
        return 0
    print("SDK archives differ:", file=sys.stderr)
    for line in differences(first, second):
        print(f"  {line}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
