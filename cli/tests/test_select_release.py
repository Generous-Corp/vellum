from __future__ import annotations

import runpy
from pathlib import Path
import unittest


REPO = Path(__file__).resolve().parents[2]
MODULE = runpy.run_path(str(REPO / "scripts/select_release.py"))
select_release = MODULE["select_release"]
ReleaseMissingError = MODULE["ReleaseMissingError"]
ReleaseSelectionError = MODULE["ReleaseSelectionError"]


def release(
    tag: str = "v0.1.0",
    *,
    release_id: int = 358806792,
    draft: bool = True,
    immutable: bool = False,
) -> dict[str, object]:
    return {
        "id": release_id,
        "tag_name": tag,
        "draft": draft,
        "immutable": immutable,
        "assets": [],
        "prerelease": False,
        "name": "Vellum 0.1.0 experimental SDK",
        "body": "release notes\n",
        "target_commitish": "main",
        "author": {"login": "github-actions[bot]"},
        "published_at": None if draft else "2026-07-23T00:00:00Z",
    }


class SelectReleaseTests(unittest.TestCase):
    def test_selects_draft_from_paginated_release_list(self) -> None:
        selected = select_release(
            [[release("v0.0.9", release_id=1)], [release()]],
            "v0.1.0",
            expected_name="Vellum 0.1.0 experimental SDK",
            expected_body="release notes\n",
            expected_author="github-actions[bot]",
            expected_target="main",
        )
        self.assertEqual(selected["id"], 358806792)
        self.assertIs(selected["draft"], True)

    def test_accepts_single_page_unwrapped_rest_output(self) -> None:
        selected = select_release([release()], "v0.1.0")
        self.assertEqual(selected["tag_name"], "v0.1.0")

    def test_missing_tag_is_distinct_from_malformed_or_duplicate_state(self) -> None:
        with self.assertRaises(ReleaseMissingError):
            select_release([[release("v0.0.9")]], "v0.1.0")
        with self.assertRaisesRegex(ReleaseSelectionError, "2 releases"):
            select_release([[release()], [release(release_id=2)]], "v0.1.0")
        with self.assertRaisesRegex(ReleaseSelectionError, "mixed row types"):
            select_release([release(), []], "v0.1.0")

    def test_malformed_identity_and_state_fail_closed(self) -> None:
        cases = [
            ({**release(), "id": "358806792"}, "numeric id"),
            ({**release(), "draft": None}, "draft state"),
            ({**release(), "immutable": None}, "immutable state"),
            ({**release(), "assets": None}, "assets"),
            ({**release(), "prerelease": True}, "prerelease state"),
        ]
        for row, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ReleaseSelectionError, message):
                    select_release([row], "v0.1.0")

    def test_mismatched_resumable_draft_metadata_fails_closed(self) -> None:
        expected = {
            "expected_name": "Vellum 0.1.0 experimental SDK",
            "expected_body": "release notes\n",
            "expected_author": "github-actions[bot]",
            "expected_target": "main",
        }
        cases = [
            ({**release(), "name": "other"}, "name"),
            ({**release(), "body": "other"}, "body"),
            (
                {**release(), "author": {"login": "someone-else"}},
                "author",
            ),
            ({**release(), "target_commitish": "other"}, "target_commitish"),
        ]
        for row, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ReleaseSelectionError, message):
                    select_release([row], "v0.1.0", **expected)

    def test_draft_publication_state_and_asset_subset_fail_closed(self) -> None:
        invalid_states = [
            ({**release(), "immutable": True}, "publication state"),
            (
                {
                    **release(),
                    "published_at": "2026-07-23T00:00:00Z",
                },
                "publication state",
            ),
            (
                {
                    **release(draft=False, immutable=False),
                },
                "immutable state",
            ),
            (
                {
                    **release(draft=False, immutable=True),
                    "published_at": None,
                },
                "immutable state",
            ),
        ]
        for row, message in invalid_states:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ReleaseSelectionError, message):
                    select_release([row], "v0.1.0")

        expected_asset = {
            "name": "install.sh",
            "state": "uploaded",
        }
        selected = select_release(
            [{**release(), "assets": [expected_asset]}],
            "v0.1.0",
            allowed_assets={"install.sh", "SHA256SUMS"},
        )
        self.assertEqual(selected["assets"], [expected_asset])
        with self.assertRaisesRegex(
            ReleaseSelectionError, "unexpected resumable assets"
        ):
            select_release(
                [{**release(), "assets": [{"name": "other"}]}],
                "v0.1.0",
                allowed_assets={"install.sh"},
            )
        with self.assertRaisesRegex(ReleaseSelectionError, "duplicate"):
            select_release(
                [
                    {
                        **release(),
                        "assets": [expected_asset, expected_asset.copy()],
                    }
                ],
                "v0.1.0",
                allowed_assets={"install.sh"},
            )


if __name__ == "__main__":
    unittest.main()
