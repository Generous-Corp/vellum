#!/usr/bin/env python3
"""Every release path reads the retired-tag record through one loader."""

from __future__ import annotations

import json
import re
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import release_request  # noqa: E402
import retired_release_tags as record  # noqa: E402
import version_at_land  # noqa: E402

SELECT = runpy.run_path(str(ROOT / "scripts/select_release.py"))
VERIFY = runpy.run_path(str(ROOT / "scripts/verify_release_tag.py"))


class RetiredReleaseTagTests(unittest.TestCase):
    def test_the_record_retires_every_unpublished_or_lost_tag(self) -> None:
        self.assertEqual(record.load(), {"v0.1.7", "v0.1.8", "v0.1.9"})

    def test_every_release_path_reads_the_same_set(self) -> None:
        payload = json.loads(record.PATH.read_text(encoding="utf-8"))
        expected = record.load()
        self.assertEqual(SELECT["retired_release_tags"](record.PATH), expected)
        self.assertEqual(VERIFY["retired_release_tags"](record.PATH), expected)
        self.assertEqual(release_request.retired(payload), expected)
        self.assertEqual(version_at_land.retired(payload), expected)
        self.assertEqual(release_request.RETIRED_TAGS, record.RELATIVE_PATH)
        self.assertEqual(version_at_land.RETIRED_TAGS, record.RELATIVE_PATH)

    def test_no_other_script_names_the_record_path(self) -> None:
        # Only the loader may spell the record's path as a string constant; a
        # second constant means a second parser, which is how v0.1.8 went
        # missing from one release path.
        listed = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-co", "--exclude-standard", "--", "*.py"],
            check=True, capture_output=True, text=True, encoding="utf-8",
        ).stdout.split()
        spelled = re.compile(r"[\"']provenance/retired-release-tags\.json[\"']")
        hits = [path for path in listed if not path.startswith("mirror/")
                and spelled.search((ROOT / path).read_text(encoding="utf-8"))]
        self.assertIn("scripts/retired_release_tags.py", hits)  # control: the scan sees it
        self.assertEqual([h for h in hits if h != "scripts/retired_release_tags.py"
                          and not Path(h).name.startswith("test_")], [])

    def test_malformed_records_fail_closed_on_every_path(self) -> None:
        bad = [
            {"tags": [{"tag": "v0.1.7"}]},
            {"schema": record.SCHEMA, "tags": "v0.1.7"},
            {"schema": record.SCHEMA, "tags": [{"tag": 7}]},
            {"schema": record.SCHEMA, "tags": [{}]},
        ]
        for payload in bad:
            with self.subTest(payload=payload):
                with self.assertRaises(release_request.ReleaseRequestError):
                    release_request.retired(payload)
                with self.assertRaises(version_at_land.VersionError):
                    version_at_land.retired(payload)
                with tempfile.TemporaryDirectory() as temporary:
                    path = Path(temporary) / "retired.json"
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaises(SELECT["ReleaseSelectionError"]):
                        SELECT["retired_release_tags"](path)
                    with self.assertRaises(VERIFY["ReleaseTagVerificationError"]):
                        VERIFY["retired_release_tags"](path)

    def test_a_missing_record_is_an_error_not_an_empty_set(self) -> None:
        with self.assertRaises(record.RetiredTagsError):
            record.load(Path("/nonexistent/retired-release-tags.json"))


if __name__ == "__main__":
    unittest.main()
