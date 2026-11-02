from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]
MODULE = runpy.run_path(
    str(REPO / "scripts/verify_immutable_release_preflight.py")
)
verify = MODULE["verify"]
verify_live = MODULE["verify_live"]
ImmutableReleasePreflightError = MODULE["ImmutableReleasePreflightError"]


def payload() -> dict[str, object]:
    return {
        "schema": "vellum.immutable-release-preflight.v1",
        "repository": "Generous-Corp/vellum",
        "release_tag": "v0.1.0",
        "integrity_model": "covered-by-exact-signed-annotated-release-tag",
        "maximum_age_seconds": 21600,
        "documentation": "https://docs.github.com/example",
        "administrator_check": {
            "checked_at": "2026-07-23T12:24:30Z",
            "enabled": True,
            "enforced_by_owner": False,
            "endpoint": "GET /repos/Generous-Corp/vellum/immutable-releases",
            "required_permission": "Administration: read",
        },
    }


class ImmutableReleasePreflightTests(unittest.TestCase):
    NOW = datetime(2026, 7, 23, 13, tzinfo=timezone.utc)

    def test_the_record_shape_is_closed(self) -> None:
        def extra_top(value): value["note"] = "x"
        def extra_admin(value): value["administrator_check"]["note"] = "x"
        def missing(value): del value["documentation"]
        def float_age(value): value["maximum_age_seconds"] = 21600.0
        def bool_age(value): value["maximum_age_seconds"] = True
        for change in (extra_top, extra_admin, missing, float_age, bool_age):
            with self.subTest(change.__name__):
                value = payload()
                change(value)
                with self.assertRaises(ImmutableReleasePreflightError):
                    verify(value, repository="Generous-Corp/vellum", release_tag="v0.1.0", now=self.NOW)

    def test_duplicate_keys_are_refused(self) -> None:
        with self.assertRaisesRegex(ImmutableReleasePreflightError, "duplicate keys: release_tag"):
            json.loads('{"release_tag": "v0.1.0", "release_tag": "v9.9.9"}',
                       object_pairs_hook=MODULE["reject_duplicate_keys"])

    def test_recent_enabled_admin_check_passes(self) -> None:
        value = payload()
        verified = verify(
            value,
            repository="Generous-Corp/vellum",
            release_tag="v0.1.0",
            now=datetime(2026, 7, 23, 13, tzinfo=timezone.utc),
        )
        self.assertEqual(verified, value)

    def test_disabled_stale_and_future_checks_fail_closed(self) -> None:
        disabled = json.loads(json.dumps(payload()))
        disabled["administrator_check"]["enabled"] = False
        with self.assertRaisesRegex(
            ImmutableReleasePreflightError, "were not enabled"
        ):
            verify(
                disabled,
                repository="Generous-Corp/vellum",
                release_tag="v0.1.0",
                now=datetime(2026, 7, 23, 13, tzinfo=timezone.utc),
            )

        with self.assertRaisesRegex(
            ImmutableReleasePreflightError, "stale"
        ):
            verify(
                payload(),
                repository="Generous-Corp/vellum",
                release_tag="v0.1.0",
                now=datetime(2026, 7, 25, 13, tzinfo=timezone.utc),
            )

        with self.assertRaisesRegex(
            ImmutableReleasePreflightError, "in the future"
        ):
            verify(
                payload(),
                repository="Generous-Corp/vellum",
                release_tag="v0.1.0",
                now=datetime(2026, 7, 23, 12, tzinfo=timezone.utc),
            )

    def test_wrong_repository_endpoint_permission_and_age_policy_fail(self) -> None:
        cases = [
            ("repository", "Other/repo", "does not equal"),
            ("release_tag", "v0.1.1", "release tag"),
            ("maximum_age_seconds", 86400, "must equal"),
            ("integrity_model", "standalone-json-signature", "integrity model"),
        ]
        for key, replacement, expected in cases:
            with self.subTest(key=key):
                value = payload()
                value[key] = replacement
                with self.assertRaisesRegex(
                    ImmutableReleasePreflightError, expected
                ):
                    verify(
                        value,
                        repository="Generous-Corp/vellum",
                        release_tag="v0.1.0",
                        now=datetime(2026, 7, 23, 13, tzinfo=timezone.utc),
                    )

        for key, replacement, expected in [
            ("endpoint", "GET /wrong", "endpoint"),
            ("required_permission", "Contents: write", "Administration"),
            ("enforced_by_owner", "false", "must be a boolean"),
        ]:
            with self.subTest(key=key):
                value = payload()
                value["administrator_check"][key] = replacement
                with self.assertRaisesRegex(
                    ImmutableReleasePreflightError, expected
                ):
                    verify(
                        value,
                        repository="Generous-Corp/vellum",
                        release_tag="v0.1.0",
                        now=datetime(2026, 7, 23, 13, tzinfo=timezone.utc),
                    )

    def test_age_and_clock_skew_boundaries_are_exact(self) -> None:
        verify(
            payload(),
            repository="Generous-Corp/vellum",
            release_tag="v0.1.0",
            now=datetime(2026, 7, 23, 18, 24, 30, tzinfo=timezone.utc),
        )
        with self.assertRaisesRegex(
            ImmutableReleasePreflightError, "stale by 1s"
        ):
            verify(
                payload(),
                repository="Generous-Corp/vellum",
                release_tag="v0.1.0",
                now=datetime(2026, 7, 23, 18, 24, 31, tzinfo=timezone.utc),
            )
        verify(
            payload(),
            repository="Generous-Corp/vellum",
            release_tag="v0.1.0",
            now=datetime(2026, 7, 23, 12, 19, 30, tzinfo=timezone.utc),
        )
        with self.assertRaisesRegex(
            ImmutableReleasePreflightError, "301s in the future"
        ):
            verify(
                payload(),
                repository="Generous-Corp/vellum",
                release_tag="v0.1.0",
                now=datetime(2026, 7, 23, 12, 19, 29, tzinfo=timezone.utc),
            )



class LiveImmutableReleaseAnswerTests(unittest.TestCase):
    """The release jobs read GET /immutable-releases live and verify that answer."""

    NOW = datetime(2026, 11, 2, 1, 2, 3, tzinfo=timezone.utc)

    def live(self, answer: object, **overrides: object) -> dict[str, object]:
        args = dict(repository="Generous-Corp/vellum", release_tag="v0.1.10", checked_at=self.NOW)
        args.update(overrides)
        return verify_live(answer, **args)

    def test_an_enabled_answer_becomes_a_verified_record_stamped_at_the_read(self) -> None:
        record = self.live({"enabled": True, "enforced_by_owner": False})
        self.assertEqual(record["schema"], "vellum.immutable-release-preflight.v1")
        self.assertEqual(record["release_tag"], "v0.1.10")
        self.assertEqual(record["administrator_check"]["checked_at"], "2026-11-02T01:02:03Z")
        self.assertEqual(record["administrator_check"]["endpoint"],
                         "GET /repos/Generous-Corp/vellum/immutable-releases")
        self.assertIs(record["administrator_check"]["enforced_by_owner"], False)
        self.assertIs(self.live({"enabled": True, "enforced_by_owner": True})
                      ["administrator_check"]["enforced_by_owner"], True)
        # The record is exactly what the record verifier accepts.
        self.assertEqual(verify(record, repository="Generous-Corp/vellum", release_tag="v0.1.10",
                                now=self.NOW), record)
        # A key GitHub adds later decides nothing and does not block a release.
        self.live({"enabled": True, "enforced_by_owner": False, "url": "https://example"})

    def test_a_disabled_or_malformed_answer_refuses(self) -> None:
        cases = [
            ({"enabled": False, "enforced_by_owner": False}, "not enabled"),
            ({"enabled": "true", "enforced_by_owner": False}, "not enabled"),
            ({"enforced_by_owner": False}, "not enabled"),
            ({"enabled": True}, "has no boolean enforced_by_owner"),
            ({"enabled": True, "enforced_by_owner": "false"}, "has no boolean enforced_by_owner"),
            ([], "must be an object"),
            (None, "must be an object"),
            ({"message": "Not Found", "status": "404"}, "not enabled"),
        ]
        for answer, message in cases:
            with self.subTest(answer=answer), self.assertRaisesRegex(ImmutableReleasePreflightError, message):
                self.live(answer)

    def test_the_tag_and_repository_must_be_well_formed(self) -> None:
        answer = {"enabled": True, "enforced_by_owner": False}
        for tag in ("0.1.10", "v0.1", "v0.1.10-rc1", "latest"):
            with self.subTest(tag=tag), self.assertRaisesRegex(ImmutableReleasePreflightError, "release tag"):
                self.live(answer, release_tag=tag)
        with self.assertRaisesRegex(ImmutableReleasePreflightError, "repository"):
            self.live(answer, repository="not a repo")

    def run_cli(self, answer: str, *extra: str) -> tuple[subprocess.CompletedProcess[str], Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        source = directory / "immutable-releases.json"
        source.write_text(answer, encoding="utf-8")
        output = directory / "record.json"
        completed = subprocess.run(
            [sys.executable, "-I", str(REPO / "scripts/verify_immutable_release_preflight.py"),
             "--input", str(source), "--repository", "Generous-Corp/vellum", "--release-tag", "v0.1.10",
             "--output", str(output), *extra],
            capture_output=True, text=True, check=False)
        return completed, output

    def test_the_cli_writes_a_record_only_for_an_enabled_answer(self) -> None:
        completed, output = self.run_cli('{"enabled": true, "enforced_by_owner": false}',
                                         "--now", "2026-11-02T01:02:03Z")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(output.read_text())["administrator_check"]["checked_at"],
                         "2026-11-02T01:02:03Z")
        completed, output = self.run_cli('{"enabled": false, "enforced_by_owner": false}')
        self.assertEqual(completed.returncode, 1)
        self.assertIn("not enabled", completed.stderr)
        self.assertFalse(output.exists())
        completed, output = self.run_cli('{"enabled": true, "enabled": false, "enforced_by_owner": false}')
        self.assertEqual(completed.returncode, 1)
        self.assertIn("duplicate keys", completed.stderr)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
