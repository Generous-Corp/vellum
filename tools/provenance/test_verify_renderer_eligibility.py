#!/usr/bin/env python3
"""Contract tests for the renderer's blocked -> eligible transition.

Every test signs with a throwaway key generated in a temporary directory and
pins that key in place of the owner's, so the same verifier code that checks
the real record is exercised end to end, including ``ssh-keygen -Y verify``.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_renderer_dependency as verifier  # noqa: E402
from verify_renderer_dependency import VerificationError  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]
PRINCIPAL = "owner@example.test"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def keygen(directory: Path, name: str) -> tuple[Path, str]:
    key = directory / name
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(key)],
                   check=True)
    fingerprint = subprocess.run(["ssh-keygen", "-lf", str(key) + ".pub"], check=True,
                                 capture_output=True, text=True).stdout.split()[1]
    return key, fingerprint


@unittest.skipUnless(shutil.which("ssh-keygen"), "ssh-keygen is required")
class RendererEligibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="vellum-renderer-eligible-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.key, self.fingerprint = keygen(self.tmp, "owner")
        self.other_key, _ = keygen(self.tmp, "intruder")
        self.allowed = self.tmp / "allowed_signers"
        public = (Path(str(self.key) + ".pub")).read_text().split()
        self.allowed.write_text(f"{PRINCIPAL} {public[0]} {public[1]}\n")

        self.archive = self.tmp / "skia-build-mac-universal-gpu-release.zip"
        with zipfile.ZipFile(self.archive, "w") as handle:
            handle.writestr("build/include/core.h", b"int core(void);\n")
            handle.writestr("build/mac-gpu/lib/Release/libskia.a", b"!<arch>\n")
        self.archive_sha = sha256(self.archive.read_bytes())

        lock = json.loads((ROOT / "provenance/third-party-lock.json").read_text())
        self.base_attestation = json.loads((ROOT / "provenance/renderer-attestation.json").read_text())
        self.lock = lock
        self.write_manifest(self.legal_manifest())
        self.release = None

    # -- fixtures ------------------------------------------------------------
    def legal_manifest(self, **overrides) -> dict:
        with zipfile.ZipFile(self.archive) as handle:
            members = [{"name": info.filename, "sha256": sha256(handle.read(info)),
                        "kind": "static-archive" if info.filename.endswith(".a") else "packaged-file",
                        "licenses": ["source:LICENSE"]}
                       for info in handle.infolist()]
        manifest = {
            "schema": verifier.LEGAL_MANIFEST_SCHEMA,
            "archive": {"name": self.archive.name, "sha256": self.archive_sha},
            "exhaustive": True,
            "gaps": [],
            "zip_members": members,
            "static_archives": {"build/mac-gpu/lib/Release/libskia.a": {"slices": {"arm64": {
                "members": [{"name": "a.o", "objects": ["generated:arm64:obj/a.o"],
                             "licenses": ["source:LICENSE"]}]}}}},
            "licenses": {"source:LICENSE": {"sha256": "0" * 64, "text": "BSD"}},
        }
        manifest.update(overrides)
        return manifest

    def write_manifest(self, manifest: dict) -> None:
        data = (json.dumps(manifest, sort_keys=True, indent=1) + "\n").encode()
        self.manifest_sha = sha256(data)
        self.manifest_name = (f"skia-build-mac-universal-gpu-release.legal-manifest."
                              f"sha256-{self.manifest_sha}.json")
        self.manifest_path = self.tmp / self.manifest_name
        self.manifest_path.write_bytes(data)

    def eligible(self, sign_with: Path | None = None, namespace: str = verifier.SIGNATURE_NAMESPACE,
                 mutate=None) -> tuple[Path, Path, Path]:
        lock = copy.deepcopy(self.lock)
        renderer = lock["runtime_toolchain_dependencies"]["renderer"]
        lock["status"] = verifier.LOCK_STATUS_ELIGIBLE
        renderer["release_eligibility"] = "eligible"
        renderer["release_blockers"] = []
        renderer["artifact"]["sha256"] = self.archive_sha
        renderer["bundled_components_completeness"] = {
            "claim": "exhaustive", "legal_manifest_sha256": self.manifest_sha}

        record = copy.deepcopy(self.base_attestation)
        artifact = record["artifact"]
        artifact["sha256"] = self.archive_sha
        record["state"] = "owner-attested"
        record["build_identity"]["compiler"] = {
            "vendor": "Apple", "version": "Apple clang version 17.0.0 (clang-1700.0.13.5)",
            "sha256": "a" * 64}
        record["build_identity"]["linker"] = {
            "vendor": "Apple", "version": "ld-1167.5", "sha256": "b" * 64}
        record["build_identity"]["cpp_abi"] = {"stdlib": "libc++ 190102", "abi": "1 (__1)"}
        record["legal_inventory"] = {
            "state": "exhaustive", "manifest_sha256": self.manifest_sha, "entries": [],
            "manifest": {"name": self.manifest_name, "sha256": self.manifest_sha,
                         "url": verifier.release_asset_url(artifact, self.manifest_name)}}
        record["signature"] = {"state": "signed", "issuer": PRINCIPAL,
                               "subject": self.archive_sha, "namespace": verifier.SIGNATURE_NAMESPACE}
        if mutate:
            mutate(lock, record)

        payload = self.tmp / "payload"
        payload.write_bytes(verifier.signing_payload(record))
        signature = self.tmp / "renderer-attestation.json.sig"
        signature.unlink(missing_ok=True)
        subprocess.run(["ssh-keygen", "-q", "-Y", "sign", "-f", str(sign_with or self.key),
                        "-n", namespace, str(payload)], check=True, capture_output=True)
        Path(str(payload) + ".sig").rename(signature)
        record["signature"]["signature_sha256"] = sha256(signature.read_bytes())
        record["signature"]["payload_sha256"] = sha256(payload.read_bytes())

        lock_path, record_path = self.tmp / "lock.json", self.tmp / "attestation.json"
        lock_path.write_text(json.dumps(lock))
        record_path.write_text(json.dumps(record))
        self.release = self.tmp / "release.json"
        self.release.write_text(json.dumps({"tag_name": artifact["release_tag"], "assets": [
            {"name": artifact["name"], "digest": f"sha256:{self.archive_sha}"},
            {"name": self.manifest_name, "digest": f"sha256:{self.manifest_sha}"}]}))
        return lock_path, record_path, signature

    def load(self, lock_path: Path, record_path: Path, signature: Path, fingerprint: str | None = None):
        return verifier.load_lock(lock_path, record_path, signature_path=signature,
                                  allowed_signers=self.allowed, owner_principal=PRINCIPAL,
                                  owner_fingerprint=fingerprint or self.fingerprint)

    def edit_record(self, record_path: Path, edit) -> None:
        record = json.loads(record_path.read_text())
        edit(record)
        record_path.write_text(json.dumps(record))

    # -- the positive control ------------------------------------------------
    def test_signed_exhaustive_record_makes_the_renderer_eligible(self) -> None:
        lock_path, record_path, signature = self.eligible()
        _, renderer = self.load(lock_path, record_path, signature)
        attestation = renderer["_attestation"]
        self.assertEqual(renderer["release_eligibility"], "eligible")
        self.assertEqual(attestation["_verified_signature"]["principal"], PRINCIPAL)
        result = verifier.verify_legal_manifest_file(attestation, self.manifest_path, self.archive)
        self.assertEqual(result["legal_manifest_sha256"], self.manifest_sha)
        self.assertEqual(verifier.verify_release_assets(attestation, self.release),
                         {"release_assets_verified": 2})

    # -- the signature is checked, never trusted -----------------------------
    def test_signed_state_without_a_signature_file_fails(self) -> None:
        lock_path, record_path, signature = self.eligible()
        signature.unlink()
        with self.assertRaisesRegex(VerificationError, "marked signed but"):
            self.load(lock_path, record_path, signature)

    def test_record_changed_after_signing_fails(self) -> None:
        lock_path, record_path, signature = self.eligible()
        self.edit_record(record_path, lambda r: r["build_identity"]["compiler"].update(version="clang 99"))
        with self.assertRaisesRegex(VerificationError, "payload_sha256 does not match"):
            self.load(lock_path, record_path, signature)

    def test_record_changed_after_signing_with_recomputed_digest_fails_cryptographically(self) -> None:
        lock_path, record_path, signature = self.eligible()

        def forge(record):
            record["build_identity"]["compiler"]["version"] = "clang 99"
            record["signature"]["payload_sha256"] = sha256(verifier.signing_payload(record))
        self.edit_record(record_path, forge)
        with self.assertRaisesRegex(VerificationError, "signature does not verify"):
            self.load(lock_path, record_path, signature)

    def test_signature_from_a_key_that_is_not_the_owners_fails(self) -> None:
        lock_path, record_path, signature = self.eligible(sign_with=self.other_key)
        with self.assertRaisesRegex(VerificationError, "signature does not verify"):
            self.load(lock_path, record_path, signature)

    def test_allowed_signers_key_that_is_not_pinned_fails(self) -> None:
        lock_path, record_path, signature = self.eligible()
        with self.assertRaisesRegex(VerificationError, "not the pinned owner key"):
            self.load(lock_path, record_path, signature, fingerprint="SHA256:" + "A" * 43)

    def test_signature_in_another_namespace_fails(self) -> None:
        lock_path, record_path, signature = self.eligible(namespace="git")
        with self.assertRaisesRegex(VerificationError, "signature does not verify"):
            self.load(lock_path, record_path, signature)

    def test_subject_must_be_the_artifact_digest(self) -> None:
        lock_path, record_path, signature = self.eligible(
            mutate=lambda lock, record: record["signature"].update(subject="1" * 64))
        with self.assertRaisesRegex(VerificationError, "subject must be the artifact digest"):
            self.load(lock_path, record_path, signature)

    def test_issuer_must_be_the_owner(self) -> None:
        lock_path, record_path, signature = self.eligible(
            mutate=lambda lock, record: record["signature"].update(issuer="someone@example.test"))
        with self.assertRaisesRegex(VerificationError, "is not the owner"):
            self.load(lock_path, record_path, signature)

    def test_the_real_owner_pin_rejects_a_test_key(self) -> None:
        # The shipped constants are the owner's; a test key signs nothing they accept.
        lock_path, record_path, signature = self.eligible()
        with self.assertRaises(VerificationError):
            verifier.load_lock(lock_path, record_path, signature_path=signature,
                               allowed_signers=self.allowed)

    # -- eligibility needs every condition -----------------------------------
    def test_eligible_lock_with_a_pending_record_fails(self) -> None:
        lock_path, _, signature = self.eligible()
        pending = copy.deepcopy(self.base_attestation)
        pending["artifact"]["sha256"] = self.archive_sha
        pending_path = self.tmp / "pending.json"
        pending_path.write_text(json.dumps(pending))
        with self.assertRaisesRegex(VerificationError, "owner-attested"):
            verifier.load_lock(lock_path, pending_path,
                               signature_path=signature, allowed_signers=self.allowed,
                               owner_principal=PRINCIPAL, owner_fingerprint=self.fingerprint)

    def test_eligible_lock_with_open_blockers_fails(self) -> None:
        lock_path, record_path, signature = self.eligible(mutate=lambda lock, record: lock[
            "runtime_toolchain_dependencies"]["renderer"].update(release_blockers=[{"id": "x"}]))
        with self.assertRaisesRegex(VerificationError, "no open release blockers"):
            self.load(lock_path, record_path, signature)

    def test_eligible_lock_must_keep_its_status_in_step(self) -> None:
        lock_path, record_path, signature = self.eligible(
            mutate=lambda lock, record: lock.update(status=verifier.LOCK_STATUS_BLOCKED))
        with self.assertRaisesRegex(VerificationError, "status does not match"):
            self.load(lock_path, record_path, signature)

    def test_owner_attested_record_with_pending_build_identity_fails(self) -> None:
        lock_path, record_path, signature = self.eligible(mutate=lambda lock, record: record[
            "build_identity"]["linker"].update(version="pending-builder-attestation"))
        with self.assertRaisesRegex(VerificationError, "linker.version is not attested"):
            self.load(lock_path, record_path, signature)

    def test_lock_naming_another_manifest_fails(self) -> None:
        lock_path, record_path, signature = self.eligible(mutate=lambda lock, record: lock[
            "runtime_toolchain_dependencies"]["renderer"]["bundled_components_completeness"].update(
                legal_manifest_sha256="2" * 64))
        with self.assertRaisesRegex(VerificationError, "different legal manifest"):
            self.load(lock_path, record_path, signature)

    # -- the manifest is the attested release asset --------------------------
    def test_manifest_name_must_carry_its_digest(self) -> None:
        def rename(lock, record):
            record["legal_inventory"]["manifest"]["name"] = "legal-manifest.json"
        lock_path, record_path, signature = self.eligible(mutate=rename)
        with self.assertRaisesRegex(VerificationError, "must carry its own SHA-256"):
            self.load(lock_path, record_path, signature)

    def test_downloaded_manifest_with_other_bytes_fails(self) -> None:
        lock_path, record_path, signature = self.eligible()
        _, renderer = self.load(lock_path, record_path, signature)
        self.manifest_path.write_bytes(self.manifest_path.read_bytes() + b" ")
        with self.assertRaisesRegex(VerificationError, "differs from the attestation"):
            verifier.verify_legal_manifest_file(renderer["_attestation"], self.manifest_path)

    def test_non_exhaustive_manifest_fails_even_when_attested(self) -> None:
        self.write_manifest(self.legal_manifest(exhaustive=False, gaps=[{"kind": "x"}]))
        lock_path, record_path, signature = self.eligible()
        _, renderer = self.load(lock_path, record_path, signature)
        with self.assertRaisesRegex(VerificationError, "exhaustive inventory"):
            verifier.verify_legal_manifest_file(renderer["_attestation"], self.manifest_path)

    def test_manifest_for_another_archive_fails(self) -> None:
        self.write_manifest(self.legal_manifest(archive={"name": self.archive.name, "sha256": "3" * 64}))
        lock_path, record_path, signature = self.eligible()
        _, renderer = self.load(lock_path, record_path, signature)
        with self.assertRaisesRegex(VerificationError, "different archive"):
            verifier.verify_legal_manifest_file(renderer["_attestation"], self.manifest_path)

    def test_manifest_that_omits_an_archive_member_fails(self) -> None:
        manifest = self.legal_manifest()
        manifest["zip_members"] = manifest["zip_members"][:1]
        self.write_manifest(manifest)
        lock_path, record_path, signature = self.eligible()
        _, renderer = self.load(lock_path, record_path, signature)
        with self.assertRaisesRegex(VerificationError, "inventory differs from the archive"):
            verifier.verify_legal_manifest_file(renderer["_attestation"], self.manifest_path, self.archive)

    def test_release_asset_digest_mismatch_fails(self) -> None:
        lock_path, record_path, signature = self.eligible()
        _, renderer = self.load(lock_path, record_path, signature)
        release = json.loads(self.release.read_text())
        release["assets"][1]["digest"] = "sha256:" + "4" * 64
        self.release.write_text(json.dumps(release))
        with self.assertRaisesRegex(VerificationError, "differs from sha256:"):
            verifier.verify_release_assets(renderer["_attestation"], self.release)

    def test_release_without_the_manifest_asset_fails(self) -> None:
        lock_path, record_path, signature = self.eligible()
        _, renderer = self.load(lock_path, record_path, signature)
        release = json.loads(self.release.read_text())
        release["assets"] = release["assets"][:1]
        self.release.write_text(json.dumps(release))
        with self.assertRaisesRegex(VerificationError, "release asset is missing"):
            verifier.verify_release_assets(renderer["_attestation"], self.release)

    # -- the shipped state ---------------------------------------------------
    def test_shipped_lock_is_blocked_and_its_record_unsigned(self) -> None:
        _, renderer = verifier.load_lock(ROOT / "provenance/third-party-lock.json")
        self.assertEqual(renderer["release_eligibility"], "blocked")
        self.assertNotIn("_verified_signature", renderer["_attestation"])

    def test_cli_eligibility_requires_the_manifest_and_release_assets(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("verify_renderer_dependency.py")),
             "--require-release-eligible"], check=False, capture_output=True, text=True)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("GPU release is blocked", completed.stderr)

    def test_print_signing_payload_is_what_verification_checks(self) -> None:
        _, record_path, _ = self.eligible()
        completed = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("verify_renderer_dependency.py")),
             "--attestation", str(record_path), "--print-signing-payload"],
            check=True, capture_output=True)
        record = json.loads(record_path.read_text())
        self.assertEqual(sha256(completed.stdout), record["signature"]["payload_sha256"])


if __name__ == "__main__":
    unittest.main()
