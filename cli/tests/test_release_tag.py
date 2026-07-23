from __future__ import annotations

from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]
MODULE = runpy.run_path(str(REPO / "scripts/verify_release_tag.py"))
verify = MODULE["verify"]
ReleaseTagVerificationError = MODULE["ReleaseTagVerificationError"]


def command(*arguments: str, cwd: Path) -> str:
    return subprocess.run(
        list(arguments),
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.strip()


class ReleaseTagTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        command("git", "init", "-q", cwd=self.repo)
        command("git", "config", "user.name", "Release Test", cwd=self.repo)
        command(
            "git", "config", "user.email", "release@example.com", cwd=self.repo
        )
        self.key = self.root / "release-key"
        command(
            "ssh-keygen",
            "-q",
            "-t",
            "ed25519",
            "-N",
            "",
            "-f",
            str(self.key),
            cwd=self.root,
        )
        self.allowed_signers = self.root / "allowed_signers"
        public_key = (self.key.with_suffix(".pub")).read_text(
            encoding="utf-8"
        ).strip()
        self.allowed_signers.write_text(
            f"release@example.com {public_key}\n", encoding="utf-8"
        )
        self.fingerprint = command(
            "ssh-keygen", "-lf", str(self.allowed_signers), cwd=self.root
        ).split()[1]
        (self.repo / "payload.txt").write_text("release\n", encoding="utf-8")
        command("git", "add", "payload.txt", cwd=self.repo)
        command("git", "commit", "-qm", "release source", cwd=self.repo)
        self.commit = command("git", "rev-parse", "HEAD", cwd=self.repo)
        command(
            "git",
            "-c",
            "gpg.format=ssh",
            "-c",
            f"user.signingkey={self.key}",
            "tag",
            "-s",
            "v0.1.0",
            "-m",
            "Release",
            cwd=self.repo,
        )
        self.tag_object = command(
            "git", "rev-parse", "v0.1.0", cwd=self.repo
        )
        # Reproduce actions/checkout's tag-push behavior: retain the fetched
        # annotated object, but rewrite the local tag ref to its peeled commit.
        command(
            "git",
            "update-ref",
            "refs/tags/v0.1.0",
            self.commit,
            cwd=self.repo,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def verify(self, **overrides: str) -> dict[str, object]:
        values = {
            "repo": self.repo,
            "repository": "Generous-Corp/vellum",
            "tag_name": "v0.1.0",
            "tag_object_sha": self.tag_object,
            "source_commit": self.commit,
            "allowed_signers": self.allowed_signers,
            "expected_principal": "release@example.com",
            "expected_fingerprint": self.fingerprint,
        }
        values.update(overrides)
        return verify(**values)

    def test_raw_remote_tag_object_survives_peeled_local_ref(self) -> None:
        self.assertEqual(
            command("git", "cat-file", "-t", "v0.1.0", cwd=self.repo),
            "commit",
        )
        evidence = self.verify()
        self.assertEqual(evidence["tag_object_sha"], self.tag_object)
        self.assertEqual(evidence["source_commit"], self.commit)

    def test_peeled_commit_cannot_substitute_for_annotated_tag(self) -> None:
        with self.assertRaisesRegex(
            ReleaseTagVerificationError, "not an annotated tag"
        ):
            self.verify(tag_object_sha=self.commit)

    def test_wrong_tag_name_and_source_commit_fail_closed(self) -> None:
        with self.assertRaisesRegex(
            ReleaseTagVerificationError, "does not equal 'v0.1.1'"
        ):
            self.verify(tag_name="v0.1.1")
        other_commit = "0" * 40
        with self.assertRaisesRegex(
            ReleaseTagVerificationError, "object header"
        ):
            self.verify(source_commit=other_commit)

    def test_wrong_principal_and_fingerprint_fail_closed(self) -> None:
        with self.assertRaisesRegex(
            ReleaseTagVerificationError, "principals .* differ"
        ):
            self.verify(expected_principal="other@example.com")
        with self.assertRaisesRegex(
            ReleaseTagVerificationError, "fingerprints .* differ"
        ):
            self.verify(expected_fingerprint="SHA256:not-the-release-key")

    def test_wrong_signer_reaches_signature_verification_and_fails(self) -> None:
        other_key = self.root / "other-release-key"
        command(
            "ssh-keygen",
            "-q",
            "-t",
            "ed25519",
            "-N",
            "",
            "-f",
            str(other_key),
            cwd=self.root,
        )
        other_allowed_signers = self.root / "other_allowed_signers"
        other_public_key = other_key.with_suffix(".pub").read_text(
            encoding="utf-8"
        ).strip()
        other_allowed_signers.write_text(
            f"release@example.com {other_public_key}\n", encoding="utf-8"
        )
        other_fingerprint = command(
            "ssh-keygen", "-lf", str(other_allowed_signers), cwd=self.root
        ).split()[1]
        with self.assertRaisesRegex(
            ReleaseTagVerificationError, "verify-tag .* failed"
        ):
            self.verify(
                allowed_signers=other_allowed_signers,
                expected_fingerprint=other_fingerprint,
            )


if __name__ == "__main__":
    unittest.main()
