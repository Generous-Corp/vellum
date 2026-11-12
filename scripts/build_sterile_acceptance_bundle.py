#!/usr/bin/env python3
"""Build the source-free input bundle for Vellum's sterile consumer job.

The GPU proof builds the bundle and the SDK finalizer rebuilds it from its
trusted checkout, on a different runner. The tar stream is deterministic
(sorted entries, mtime 0, uid and gid 0, no owner names), so the two hold the
same bytes once decompressed. The gzip wrapper is written here byte for byte
(level 9, no name, mtime 0, fixed XFL and OS bytes) rather than by the
gzip module, whose header bytes vary across Python versions. The deflate
payload can still differ between zlib builds, so the bundles are compared by
their decompressed tar streams (`--compare`), not by their gzip bytes.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import struct
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import zlib


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_FILES = (
    "collect_release_tag_rulesets.py",
    "install.sh",
    "install_core.py",
    "select_release.py",
    "validate_installed_sdk.py",
    "verify_release_assets.py",
    "verify_release_tag_ruleset.py",
    "verify_sdk_artifact.py",
)
SUPPORT_FILES = (
    "apps/minimal-scene/CMakeLists.txt",
    "apps/minimal-scene/main.cpp",
    "fixtures/design-ir/revision-a.source.json",
    "fixtures/design-ir/revision-b.source.json",
    "fixtures/design-ir/pulp-emitter-generic.pulp.zip",
    "fixtures/authoring-phase3/scenarios/phase3.json",
    "fixtures/authoring-phase3/src/App.tsx",
    "fixtures/authoring-phase3/src/imported-design.json",
    "fixtures/authoring-phase3/vendor/pure-esm-leaf/index.js",
    "fixtures/authoring-phase3/vendor/pure-esm-leaf/package.json",
    "fixtures/authoring-phase3/vendor/pure-esm-root/index.js",
    "fixtures/authoring-phase3/vendor/pure-esm-root/package.json",
    "web/consumer/index.html",
    "web/consumer/style.css",
    "web/consumer/text_semantics.js",
    "web/consumer/vellum_host.js",
    "web/tests/run_text_semantics_browser.py",
    "tools/rmlui/sterile-consumer/CMakeLists.txt",
    "tools/rmlui/sterile-consumer/main.cpp",
    "tools/mirror/consumer/CMakeLists.txt",
    "tools/mirror/consumer/main.cpp",
    "tools/mirror/consumer/cross_image_main.cpp",
    "tools/mirror/provider-conflict/CMakeLists.txt",
    "graphics/tests/test_dawn_provider_topology.py",
    "tools/mirror/verify_mirror_inertness.py",
    "tools/mirror/nm_symbols.py",
    "tools/mirror/rtti_ownership.py",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# RFC 1952 header: magic, CM=deflate, no flags, MTIME=0, XFL=2 (level 9),
# OS=255 (unknown). Fixed, so it never depends on the Python that writes it.
GZIP_HEADER = b"\x1f\x8b\x08\x00" + struct.pack("<I", 0) + b"\x02\xff"


def write_gzip(data: bytes, output: Path) -> None:
    """Write data as one gzip member with the fixed header at level 9."""
    compressor = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
    body = compressor.compress(data) + compressor.flush()
    trailer = struct.pack("<II", zlib.crc32(data) & 0xFFFFFFFF, len(data) & 0xFFFFFFFF)
    output.write_bytes(GZIP_HEADER + body + trailer)


def tar_stream(path: Path) -> bytes:
    """The decompressed tar stream of a bundle."""
    return gzip.decompress(path.read_bytes())


def compare(first: Path, second: Path) -> list[str]:
    """Why two bundles do not hold the same tar stream; empty when they do."""
    a, b = tar_stream(first), tar_stream(second)
    if a == b:
        return []
    at = next((index for index, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    return [f"{first.name} and {second.name} hold different tar streams (first difference at byte {at}, "
            f"sizes {len(a)} and {len(b)})"]


def build(output: Path) -> dict[str, object]:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="vellum-sterile-bundle-") as temporary:
        staging = Path(temporary) / "vellum-sterile-acceptance"
        staging.mkdir()
        for relative in SCRIPT_FILES:
            source = ROOT / "scripts" / relative
            destination = staging / relative
            shutil.copy2(source, destination)
        for relative in SUPPORT_FILES:
            source = ROOT / relative
            destination = staging / "sterile-support" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        files = sorted(
            path for path in staging.rglob("*")
            if path.is_file()
        )
        manifest = {
            "schema": "vellum.sterile-acceptance-bundle.v1",
            "files": [
                {
                    "path": path.relative_to(staging).as_posix(),
                    "sha256": digest(path),
                }
                for path in files
            ],
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for path in sorted([staging, *staging.rglob("*")]):
                info = archive.gettarinfo(
                    str(path),
                    arcname=path.relative_to(staging.parent).as_posix(),
                )
                info.uid = 0
                info.gid = 0
                info.uname = ""
                info.gname = ""
                info.mtime = 0
                if path.is_file():
                    with path.open("rb") as file_stream:
                        archive.addfile(info, file_stream)
                else:
                    archive.addfile(info)
        write_gzip(stream.getvalue(), output)
    return {
        "schema": "vellum.sterile-acceptance-bundle-build.v1",
        "archive": output.name,
        "sha256": digest(output),
        "fileCount": len(manifest["files"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--compare", nargs=2, type=Path, metavar=("FIRST", "SECOND"),
        help="exit 0 only when two bundles hold the same tar stream",
    )
    args = parser.parse_args()
    if args.compare:
        problems = compare(*args.compare)
        for problem in problems:
            print(f"sterile-acceptance-bundle: {problem}", file=sys.stderr)
        return 1 if problems else 0
    if args.output is None:
        parser.error("--output is required unless --compare is given")
    result = build(args.output)
    if args.json:
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    else:
        print(f"Built {result['archive']} ({result['fileCount']} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
