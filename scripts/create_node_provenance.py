#!/usr/bin/env python3
"""Create an exact provenance record for a redistributable Node runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlparse


SCHEMA = "vellum.node-runtime-provenance.v1"
SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node-binary", type=Path, required=True)
    parser.add_argument("--node-license", type=Path, required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--distribution-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    binary = args.node_binary.resolve()
    license_path = args.node_license.resolve()
    parsed_url = urlparse(args.source_url)
    if (
        not binary.is_file() or not license_path.is_file()
        or parsed_url.scheme != "https" or not parsed_url.netloc
        or SHA_RE.fullmatch(args.distribution_sha256) is None
    ):
        parser.error("binary/license, HTTPS source URL, or distribution SHA-256 is invalid")
    probe = subprocess.run(
        [str(binary), "--version"], text=True, capture_output=True, check=False
    )
    version = probe.stdout.strip().removeprefix("v")
    if probe.returncode or re.fullmatch(r"\d+(?:\.\d+){1,2}", version) is None:
        parser.error("--node-binary did not report a valid Node.js version")
    record = {
        "schema": SCHEMA,
        "name": "Node.js",
        "version": version,
        "target": args.target,
        "source_url": args.source_url,
        "distribution_sha256": args.distribution_sha256,
        "binary_sha256": sha256(binary),
        "license_file": "LICENSE",
        "license_sha256": sha256(license_path),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(record, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
