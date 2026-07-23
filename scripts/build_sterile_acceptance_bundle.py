#!/usr/bin/env python3
"""Build the source-free input bundle for Vellum's sterile consumer job."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import tarfile
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_FILES = (
    "install.sh",
    "install_core.py",
    "select_release.py",
    "validate_installed_sdk.py",
    "verify_release_assets.py",
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
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
        with output.open("wb") as output_stream:
            with gzip.GzipFile(
                filename="", mode="wb", fileobj=output_stream, mtime=0
            ) as compressed:
                with tarfile.open(
                    fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT
                ) as archive:
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
                            with path.open("rb") as stream:
                                archive.addfile(info, stream)
                        else:
                            archive.addfile(info)
    return {
        "schema": "vellum.sterile-acceptance-bundle-build.v1",
        "archive": output.name,
        "sha256": digest(output),
        "fileCount": len(manifest["files"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = build(args.output)
    if args.json:
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    else:
        print(f"Built {result['archive']} ({result['fileCount']} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
