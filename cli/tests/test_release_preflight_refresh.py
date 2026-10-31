from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
import runpy
import unittest

REPO = Path(__file__).resolve().parents[2]
REFRESH = runpy.run_path(str(REPO / "scripts/refresh_immutable_release_preflight.py"))
VERIFY = runpy.run_path(str(REPO / "scripts/verify_immutable_release_preflight.py"))
NOW = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)


def enabled(_repo: str) -> dict:
    return {"enabled": True, "enforced_by_owner": False}


class RefreshTests(unittest.TestCase):
    def test_refreshed_file_passes_the_gate_verifier(self) -> None:
        payload = REFRESH["build"](
            repository="Generous-Corp/vellum",
            release_tag="v0.1.9",
            now=NOW,
            fetch=enabled,
        )
        VERIFY["verify"](
            payload,
            repository="Generous-Corp/vellum",
            release_tag="v0.1.9",
            now=NOW,
        )

    def test_stale_committed_file_is_still_refused(self) -> None:
        import json

        committed = json.loads(
            (REPO / "provenance/immutable-release-preflight.json").read_text()
        )
        with self.assertRaisesRegex(
            VERIFY["ImmutableReleasePreflightError"], "stale"
        ):
            VERIFY["verify"](
                committed,
                repository=committed["repository"],
                release_tag=committed["release_tag"],
                now=datetime(2026, 12, 1, tzinfo=timezone.utc),
            )

    def test_refuses_disabled_and_malformed(self) -> None:
        error = REFRESH["RefreshError"]
        build = REFRESH["build"]
        with self.assertRaisesRegex(error, "not enabled"):
            build(repository="Generous-Corp/vellum", release_tag="v0.1.9",
                  now=NOW, fetch=lambda _r: {"enabled": False})
        with self.assertRaisesRegex(error, "invalid release tag"):
            build(repository="Generous-Corp/vellum", release_tag="0.1.9",
                  now=NOW, fetch=enabled)
        with self.assertRaisesRegex(error, "enforced_by_owner"):
            build(repository="Generous-Corp/vellum", release_tag="v0.1.9",
                  now=NOW, fetch=lambda _r: {"enabled": True})


class SdkReleaseDerivationTests(unittest.TestCase):
    def test_sdk_release_derives_archive_and_name_from_validated_tag(self) -> None:
        text = (REPO / ".github/workflows/sdk-release.yml").read_text()
        self.assertIsNone(re.search(r"\b0\.1\.8\b", text))
        self.assertNotIn("substr(", text)
        self.assertEqual(text.count('version="${RELEASE_TAG#v}"'), 2)
        self.assertEqual(text.count('echo "RELEASE_ARCHIVE=vellum-sdk-$version'), 2)
        self.assertEqual(text.count('echo "RELEASE_NAME=Vellum $version'), 1)
        self.assertEqual(
            text.count('[[ "$RELEASE_TAG" =~ ^v[0-9]+\\.[0-9]+\\.[0-9]+$ ]]'), 5
        )

    def test_workflows_use_only_real_expression_functions(self) -> None:
        for path in (REPO / ".github/workflows").glob("*.yml"):
            self.assertNotRegex(path.read_text(), r"\$\{\{[^}]*\b(substr|substring)\(")

    def test_no_workflow_carries_a_literal_release_version(self) -> None:
        for path in sorted((REPO / ".github/workflows").glob("*.yml")):
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if re.search(
                    r"VELLUM_RELEASE_\w+:|RELEASE_(ARCHIVE|NAME):|vellum-sdk-\d",
                    line,
                ) and re.search(r"\b\d+\.\d+\.\d+\b", line):
                    self.fail(f"{path.name}:{number} hard-codes a version")

    def test_gpu_workflow_derives_identity_and_checks_tag(self) -> None:
        text = (REPO / ".github/workflows/gpu-macos.yml").read_text()
        self.assertIn("VELLUM_RELEASE_VERSION=$version", text)
        self.assertIn('"$GITHUB_REF_NAME" != "v$version"', text)


if __name__ == "__main__":
    unittest.main()
