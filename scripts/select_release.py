#!/usr/bin/env python3
"""Select exactly one GitHub release by tag from paginated REST output."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Iterable


MISSING_EXIT = 4


class ReleaseSelectionError(RuntimeError):
    pass


class ReleaseMissingError(ReleaseSelectionError):
    pass


def release_rows(payload: object) -> list[dict[str, object]]:
    if not isinstance(payload, list):
        raise ReleaseSelectionError("GitHub releases JSON is not a list")
    if not payload:
        return []
    if all(isinstance(row, dict) for row in payload):
        return payload
    if not all(isinstance(page, list) for page in payload):
        raise ReleaseSelectionError(
            "GitHub paginated releases JSON has mixed row types"
        )
    rows: list[dict[str, object]] = []
    for page in payload:
        for row in page:
            if not isinstance(row, dict):
                raise ReleaseSelectionError(
                    "GitHub paginated releases JSON contains a malformed row"
                )
            rows.append(row)
    return rows


def select_release(
    payload: object,
    expected_tag: str,
    *,
    expected_name: str | None = None,
    expected_body: str | None = None,
    expected_author: str | None = None,
    expected_target: str | None = None,
    allowed_assets: set[str] | None = None,
) -> dict[str, object]:
    matches = [
        row
        for row in release_rows(payload)
        if row.get("tag_name") == expected_tag
    ]
    if not matches:
        raise ReleaseMissingError(
            f"GitHub has no release with tag {expected_tag!r}"
        )
    if len(matches) != 1:
        raise ReleaseSelectionError(
            f"GitHub has {len(matches)} releases with tag {expected_tag!r}"
        )
    release = matches[0]
    release_id = release.get("id")
    if not isinstance(release_id, int) or release_id <= 0:
        raise ReleaseSelectionError("GitHub release has a malformed numeric id")
    if not isinstance(release.get("draft"), bool):
        raise ReleaseSelectionError("GitHub release has a malformed draft state")
    if not isinstance(release.get("immutable"), bool):
        raise ReleaseSelectionError(
            "GitHub release has a malformed immutable state"
        )
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise ReleaseSelectionError("GitHub release has malformed assets")
    if release.get("prerelease") is not False:
        raise ReleaseSelectionError("GitHub release has a malformed prerelease state")
    expected_fields = {
        "name": expected_name,
        "body": expected_body,
        "target_commitish": expected_target,
    }
    for field, expected in expected_fields.items():
        if expected is not None and release.get(field) != expected:
            raise ReleaseSelectionError(
                f"GitHub release {field} does not match the expected value"
            )
    if expected_author is not None:
        author = release.get("author")
        if (
            not isinstance(author, dict)
            or author.get("login") != expected_author
        ):
            raise ReleaseSelectionError(
                "GitHub release author does not match the expected value"
            )
    if release["draft"] is True and (
        release["immutable"] is not False
        or release.get("published_at") is not None
    ):
        raise ReleaseSelectionError(
            "GitHub draft release has an invalid publication state"
        )
    if release["draft"] is False and (
        release["immutable"] is not True
        or release.get("published_at") in {None, ""}
    ):
        raise ReleaseSelectionError(
            "GitHub published release has an invalid immutable state"
        )
    asset_names: list[str] = []
    for asset in assets:
        if not isinstance(asset, dict):
            raise ReleaseSelectionError("GitHub release has a malformed asset")
        name = asset.get("name")
        if not isinstance(name, str) or not name:
            raise ReleaseSelectionError("GitHub release has a malformed asset name")
        asset_names.append(name)
    if len(asset_names) != len(set(asset_names)):
        raise ReleaseSelectionError("GitHub release has duplicate asset names")
    if allowed_assets is not None:
        unexpected = sorted(set(asset_names) - allowed_assets)
        if unexpected:
            raise ReleaseSelectionError(
                f"GitHub release has unexpected resumable assets: {unexpected}"
            )
    return release


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--releases-json", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--expected-name")
    parser.add_argument("--expected-body-file", type=Path)
    parser.add_argument("--expected-author")
    parser.add_argument("--expected-target")
    parser.add_argument("--allowed-asset", action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.releases_json.read_text(encoding="utf-8"))
        expected_body = (
            args.expected_body_file.read_text(encoding="utf-8")
            if args.expected_body_file
            else None
        )
        release = select_release(
            payload,
            args.tag,
            expected_name=args.expected_name,
            expected_body=expected_body,
            expected_author=args.expected_author,
            expected_target=args.expected_target,
            allowed_assets=(
                set(args.allowed_asset) if args.allowed_asset else None
            ),
        )
        args.output.write_text(
            json.dumps(release, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except ReleaseMissingError as error:
        print(f"vellum-release-selection: {error}", file=sys.stderr)
        return MISSING_EXIT
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ReleaseSelectionError,
    ) as error:
        print(f"vellum-release-selection: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
