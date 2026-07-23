#!/usr/bin/env python3
"""Verify local release assets against GitHub's recorded SHA-256 digests."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Iterable


SHA256 = re.compile(r"sha256:([0-9a-f]{64})")
SCHEMA = "vellum.release-assets-verification.v1"


class ReleaseVerificationError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_asset(value: str) -> tuple[str, Path]:
    name, separator, path_text = value.partition("=")
    if (
        not separator
        or not name
        or Path(name).name != name
        or not path_text
    ):
        raise argparse.ArgumentTypeError(
            "--asset must be an exact release name followed by =PATH"
        )
    return name, Path(path_text)


def verify(
    release: dict[str, object],
    expected_tag: str,
    local_assets: list[tuple[str, Path]],
    *,
    require_published: bool,
    require_immutable: bool,
) -> dict[str, object]:
    if release.get("tag_name") != expected_tag:
        raise ReleaseVerificationError(
            "GitHub release tag does not match the expected tag"
        )
    if require_published and (
        release.get("draft") is not False
        or release.get("published_at") in {None, ""}
    ):
        raise ReleaseVerificationError("GitHub release is not published")
    if require_immutable and release.get("immutable") is not True:
        raise ReleaseVerificationError("GitHub release is not immutable")

    raw_assets = release.get("assets")
    if not isinstance(raw_assets, list):
        raise ReleaseVerificationError("GitHub release assets are malformed")
    remote: dict[str, dict[str, object]] = {}
    for row in raw_assets:
        if not isinstance(row, dict):
            raise ReleaseVerificationError("GitHub release asset is malformed")
        name = row.get("name")
        if not isinstance(name, str) or not name or name in remote:
            raise ReleaseVerificationError(
                "GitHub release asset names are malformed or duplicated"
            )
        remote[name] = row

    local: dict[str, Path] = {}
    for name, path in local_assets:
        if name in local:
            raise ReleaseVerificationError("local release asset is duplicated")
        if path.is_symlink() or not path.is_file():
            raise ReleaseVerificationError(
                f"local release asset is not a regular file: {name}"
            )
        local[name] = path
    if set(remote) != set(local):
        missing = sorted(set(local) - set(remote))
        unexpected = sorted(set(remote) - set(local))
        raise ReleaseVerificationError(
            f"release asset set differs: missing={missing} unexpected={unexpected}"
        )

    verified: dict[str, dict[str, object]] = {}
    for name in sorted(local):
        row = remote[name]
        match = SHA256.fullmatch(str(row.get("digest", "")))
        size = row.get("size")
        if row.get("state") != "uploaded" or match is None:
            raise ReleaseVerificationError(
                f"GitHub release asset has no uploaded SHA-256 digest: {name}"
            )
        path = local[name]
        if not isinstance(size, int) or size != path.stat().st_size:
            raise ReleaseVerificationError(
                f"GitHub release asset size differs: {name}"
            )
        actual = sha256(path)
        if actual != match.group(1):
            raise ReleaseVerificationError(
                f"GitHub release asset digest differs: {name}"
            )
        verified[name] = {
            "digest": f"sha256:{actual}",
            "size": size,
        }

    return {
        "schema": SCHEMA,
        "status": "pass",
        "tag": expected_tag,
        "draft": release.get("draft"),
        "immutable": release.get("immutable"),
        "published_at": release.get("published_at"),
        "assets": verified,
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-json", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument(
        "--asset", action="append", type=parse_asset, default=[], required=True
    )
    parser.add_argument("--require-published", action="store_true")
    parser.add_argument("--require-immutable", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        release = json.loads(args.release_json.read_text(encoding="utf-8"))
        if not isinstance(release, dict):
            raise ReleaseVerificationError("GitHub release JSON is malformed")
        result = verify(
            release,
            args.tag,
            args.asset,
            require_published=args.require_published,
            require_immutable=args.require_immutable,
        )
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ReleaseVerificationError,
    ) as error:
        print(f"vellum-release-assets: {error}", file=sys.stderr)
        return 1
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
