from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import runpy
import unittest


REPO = Path(__file__).resolve().parents[2]
MODULE = runpy.run_path(
    str(REPO / "scripts/verify_immutable_release_preflight.py")
)
verify = MODULE["verify"]
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


if __name__ == "__main__":
    unittest.main()
