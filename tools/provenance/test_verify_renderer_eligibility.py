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
import unittest.mock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_renderer_dependency as verifier  # noqa: E402
from verify_renderer_dependency import VerificationError  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]
PRINCIPAL = "owner@example.test"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


OBJECT = b"\xcf\xfa\xed\xfe object bytes"
BIN = "/Applications/Xcode_16.4.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin"


def bsd_ar(members: list[tuple[str, bytes]]) -> bytes:
    out = bytearray(b"!<arch>\n")
    for name, data in members:
        encoded = name.encode().ljust(8, b"\0")
        body = encoded + data
        out += f"#1/{len(encoded)}".ljust(16).encode() + b"0".ljust(12) + b"501   20    100644  "
        out += str(len(body)).ljust(10).encode() + b"`\n" + body + (b"\n" if len(body) & 1 else b"")
    return bytes(out)


def fake_gh(returncode: int = 0, calls: list | None = None):
    def runner(args):
        if calls is not None:
            calls.append(args)
        return subprocess.CompletedProcess(args, returncode, "[]", "" if returncode == 0 else "no match")
    return runner


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
            handle.writestr("build/mac-gpu/lib/Release/libskia.a", bsd_ar([("a.o", OBJECT)]))
        self.archive_sha = sha256(self.archive.read_bytes())

        lock = json.loads((ROOT / "provenance/third-party-lock.json").read_text())
        self.base_attestation = json.loads((ROOT / "provenance/renderer-attestation.json").read_text())
        self.lock = lock
        self.write_manifest(self.legal_manifest())
        self.write_receipt(self.toolchain_receipt())
        self.bundle_path = self.tmp / "bundle"
        self.bundle_path.write_bytes(b'{"mediaType": "application/vnd.dev.sigstore.bundle.v0.3+json"}\n')
        self.bundle_sha = sha256(self.bundle_path.read_bytes())
        self.bundle_name = (f"skia-build-mac-universal-gpu-release.provenance-attestation."
                            f"sha256-{self.bundle_sha}.sigstore.json")
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
                "members": [{"name": "a.o", "sha256": sha256(OBJECT),
                             "objects": ["generated:arm64:obj/a.o"],
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

    def toolchain_receipt(self, **overrides) -> dict:
        receipt = {
            "schema": verifier.TOOLCHAIN_RECEIPT_SCHEMA,
            "archive": {"name": self.archive.name, "sha256": self.archive_sha},
            "compiler": {"cxx": {"path": f"{BIN}/clang++", "sha256": "a" * 64,
                                 "version": "Apple clang version 17.0.0 (clang-1700.0.13.5)"}},
            "linker": {"path": f"{BIN}/ld", "sha256": "b" * 64,
                       "version": "@(#)PROGRAM:ld PROJECT:ld-1167.5"},
            "sdk": {"version": "15.5"},
            "cpp_abi": {arch: {"libcpp_version": "190102", "libcpp_abi_version": "1"}
                        for arch in ("arm64", "x86_64")},
            "observed_macho": {"sdk_versions": ["15.5"]},
        }
        receipt.update(overrides)
        return receipt

    def write_receipt(self, receipt: dict) -> None:
        data = (json.dumps(receipt, sort_keys=True, indent=1) + "\n").encode()
        self.receipt_sha = sha256(data)
        self.receipt_name = (f"skia-build-mac-universal-gpu-release.apple-toolchain."
                             f"sha256-{self.receipt_sha}.json")
        self.receipt_path = self.tmp / self.receipt_name
        self.receipt_path.write_bytes(data)

    def eligible(self, sign_with: Path | None = None, namespace: str = verifier.SIGNATURE_NAMESPACE,
                 mutate=None) -> tuple[Path, Path, Path]:
        lock = copy.deepcopy(self.lock)
        renderer = lock["runtime_toolchain_dependencies"]["renderer"]
        lock["status"] = verifier.LOCK_STATUS_ELIGIBLE
        renderer["release_eligibility"] = "eligible"
        renderer["release_blockers"] = []
        renderer["artifact"]["sha256"] = self.archive_sha
        with zipfile.ZipFile(self.archive) as handle:
            renderer["archives"] = [
                {"name": Path(info.filename).name, "sha256": sha256(handle.read(info))}
                for info in handle.infolist() if info.filename.endswith(".a")]
        renderer["bundled_components_completeness"] = {
            "claim": "exhaustive", "legal_manifest_sha256": self.manifest_sha}

        record = copy.deepcopy(self.base_attestation)
        artifact = record["artifact"]
        artifact["sha256"] = self.archive_sha
        record["state"] = "owner-attested"
        record["build_identity"]["compiler"] = {
            "vendor": "Apple", "version": "Apple clang version 17.0.0 (clang-1700.0.13.5)",
            "path": f"{BIN}/clang++", "sha256": "a" * 64}
        record["build_identity"]["linker"] = {
            "vendor": "Apple", "version": "@(#)PROGRAM:ld PROJECT:ld-1167.5",
            "path": f"{BIN}/ld", "sha256": "b" * 64}
        record["build_identity"]["sdk"] = {"version": "15.5"}
        record["build_identity"]["cpp_abi"] = {"stdlib": "libc++", "abi": "1",
                                               "libcpp_version": "190102"}
        record["build_identity"]["toolchain_receipt"] = {
            "name": self.receipt_name, "sha256": self.receipt_sha,
            "url": verifier.release_asset_url(artifact, self.receipt_name)}
        record["build_provenance"] = {
            "repository": verifier.BUILDER_REPOSITORY,
            "signer_workflow": verifier.BUILDER_SIGNER_WORKFLOW,
            "source_ref": verifier.BUILDER_SOURCE_REF,
            "bundle": {"name": self.bundle_name, "sha256": self.bundle_sha,
                       "url": verifier.release_asset_url(artifact, self.bundle_name)}}
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
            {"name": self.manifest_name, "digest": f"sha256:{self.manifest_sha}"},
            {"name": self.receipt_name, "digest": f"sha256:{self.receipt_sha}"},
            {"name": self.bundle_name, "digest": f"sha256:{self.bundle_sha}"}]}))
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
                         {"release_assets_verified": 4})
        self.assertEqual(verifier.verify_toolchain_receipt_file(attestation, self.receipt_path)[
            "toolchain_receipt_architectures"], ["arm64", "x86_64"])
        calls: list = []
        result = verifier.verify_build_provenance(
            attestation, {"legal manifest": self.manifest_path, "toolchain receipt": self.receipt_path,
                          "archive": self.archive}, self.bundle_path, runner=fake_gh(0, calls))
        self.assertEqual(result["build_provenance_verified"],
                         ["archive", "legal manifest", "toolchain receipt"])
        for args in calls:
            self.assertEqual(args[:3], ["gh", "attestation", "verify"])
            for flag, value in (("--bundle", str(self.bundle_path)),
                                ("--repo", "danielraffel/skia-builder"),
                                ("--signer-workflow",
                                 "danielraffel/skia-builder/.github/workflows/build-skia.yml"),
                                ("--source-ref", "refs/heads/main")):
                self.assertEqual(args[args.index(flag) + 1], value)

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
        # Drop the header row; the static-archive row stays consistent.
        manifest["zip_members"] = [row for row in manifest["zip_members"]
                                   if row["kind"] == "static-archive"]
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

    # -- build identity is the measured identity -----------------------------
    def test_free_text_compiler_version_is_refused(self) -> None:
        lock_path, record_path, signature = self.eligible(mutate=lambda lock, record: record[
            "build_identity"]["compiler"].update(version="a good compiler"))
        with self.assertRaisesRegex(VerificationError, "is not a measured value"):
            self.load(lock_path, record_path, signature)

    def test_linker_without_an_ld_project_version_is_refused(self) -> None:
        lock_path, record_path, signature = self.eligible(mutate=lambda lock, record: record[
            "build_identity"]["linker"].update(version="ld"))
        with self.assertRaisesRegex(VerificationError, "linker.version"):
            self.load(lock_path, record_path, signature)

    def test_identity_differing_from_the_receipt_fails(self) -> None:
        lock_path, record_path, signature = self.eligible(mutate=lambda lock, record: record[
            "build_identity"]["compiler"].update(sha256="c" * 64))
        _, renderer = self.load(lock_path, record_path, signature)
        with self.assertRaisesRegex(VerificationError, "compiler.sha256 differs from the toolchain receipt"):
            verifier.verify_toolchain_receipt_file(renderer["_attestation"], self.receipt_path)

    def test_receipt_for_another_archive_fails(self) -> None:
        self.write_receipt(self.toolchain_receipt(archive={"name": self.archive.name, "sha256": "5" * 64}))
        lock_path, record_path, signature = self.eligible()
        _, renderer = self.load(lock_path, record_path, signature)
        with self.assertRaisesRegex(VerificationError, "describes a different archive"):
            verifier.verify_toolchain_receipt_file(renderer["_attestation"], self.receipt_path)

    def test_objects_built_against_another_sdk_fail(self) -> None:
        self.write_receipt(self.toolchain_receipt(observed_macho={"sdk_versions": ["15.5", "26.0"]}))
        lock_path, record_path, signature = self.eligible()
        _, renderer = self.load(lock_path, record_path, signature)
        with self.assertRaisesRegex(VerificationError, "not all built against the attested SDK"):
            verifier.verify_toolchain_receipt_file(renderer["_attestation"], self.receipt_path)

    def test_artifact_other_than_the_lock_fails(self) -> None:
        lock_path, record_path, signature = self.eligible(mutate=lambda lock, record: lock[
            "runtime_toolchain_dependencies"]["renderer"]["artifact"].update(sha256="6" * 64))
        with self.assertRaisesRegex(VerificationError, "no matching provenance record"):
            self.load(lock_path, record_path, signature)

    # -- build provenance ----------------------------------------------------
    def test_provenance_from_another_workflow_is_refused(self) -> None:
        lock_path, record_path, signature = self.eligible(mutate=lambda lock, record: record[
            "build_provenance"].update(signer_workflow="someone/fork/.github/workflows/x.yml"))
        with self.assertRaisesRegex(VerificationError, "pinned builder repository, workflow and ref"):
            self.load(lock_path, record_path, signature)

    def test_attestation_that_does_not_cover_a_subject_fails(self) -> None:
        lock_path, record_path, signature = self.eligible()
        _, renderer = self.load(lock_path, record_path, signature)
        with self.assertRaisesRegex(VerificationError, "does not cover the legal manifest"):
            verifier.verify_build_provenance(renderer["_attestation"],
                                             {"legal manifest": self.manifest_path},
                                             self.bundle_path, runner=fake_gh(1))

    def test_bundle_with_other_bytes_fails(self) -> None:
        lock_path, record_path, signature = self.eligible()
        _, renderer = self.load(lock_path, record_path, signature)
        self.bundle_path.write_bytes(b"{}")
        with self.assertRaisesRegex(VerificationError, "bundle SHA-256 differs"):
            verifier.verify_build_provenance(renderer["_attestation"], {}, self.bundle_path,
                                             runner=fake_gh(0))

    def test_release_without_the_receipt_asset_fails(self) -> None:
        lock_path, record_path, signature = self.eligible()
        _, renderer = self.load(lock_path, record_path, signature)
        release = json.loads(self.release.read_text())
        release["assets"] = [a for a in release["assets"] if a["name"] != self.receipt_name]
        self.release.write_text(json.dumps(release))
        with self.assertRaisesRegex(VerificationError, "release asset is missing"):
            verifier.verify_release_assets(renderer["_attestation"], self.release)

    # -- the manifest covers every static-archive member ---------------------
    def test_manifest_with_no_static_archive_inventory_fails(self) -> None:
        self.write_manifest(self.legal_manifest(static_archives={}))
        lock_path, record_path, signature = self.eligible()
        _, renderer = self.load(lock_path, record_path, signature)
        with self.assertRaisesRegex(VerificationError, "inventories no static archive"):
            verifier.verify_legal_manifest_file(renderer["_attestation"], self.manifest_path)

    def test_manifest_with_an_empty_member_list_fails(self) -> None:
        manifest = self.legal_manifest()
        manifest["static_archives"]["build/mac-gpu/lib/Release/libskia.a"]["slices"]["arm64"]["members"] = []
        self.write_manifest(manifest)
        lock_path, record_path, signature = self.eligible()
        _, renderer = self.load(lock_path, record_path, signature)
        with self.assertRaisesRegex(VerificationError, "lists no members"):
            verifier.verify_legal_manifest_file(renderer["_attestation"], self.manifest_path)

    def test_archive_member_the_manifest_omits_fails(self) -> None:
        # The manifest claims exhaustive, but the archive holds a member it
        # never listed; re-listing the archive catches it.
        with zipfile.ZipFile(self.archive, "w") as handle:
            handle.writestr("build/include/core.h", b"int core(void);\n")
            handle.writestr("build/mac-gpu/lib/Release/libskia.a",
                            bsd_ar([("a.o", OBJECT), ("hidden.o", b"unlisted object")]))
        self.archive_sha = sha256(self.archive.read_bytes())
        self.write_manifest(self.legal_manifest())
        manifest = json.loads(self.manifest_path.read_text())
        manifest["static_archives"]["build/mac-gpu/lib/Release/libskia.a"]["slices"]["arm64"]["members"] = [
            {"name": "a.o", "sha256": sha256(OBJECT), "objects": ["o"], "licenses": ["source:LICENSE"]}]
        self.write_manifest(manifest)
        self.write_receipt(self.toolchain_receipt())
        lock_path, record_path, signature = self.eligible()
        _, renderer = self.load(lock_path, record_path, signature)
        with self.assertRaisesRegex(VerificationError, "does not cover every member"):
            verifier.verify_legal_manifest_file(renderer["_attestation"], self.manifest_path, self.archive)

    def test_static_archive_relisting_reads_fat_and_bsd_archives(self) -> None:
        arm = bsd_ar([("a.o", b"arm"), ("long_member_name.o", b"x" * 9)])
        x86 = bsd_ar([("a.o", b"x86")])
        header = (0xCAFEBABE).to_bytes(4, "big") + (2).to_bytes(4, "big")
        table = b""
        body = b""
        offset = 4096
        for cputype, data in ((0x0100000C, arm), (0x01000007, x86)):
            table += b"".join(v.to_bytes(4, "big") for v in (cputype, 0, offset + len(body), len(data), 12))
            body += data
        fat = (header + table).ljust(offset, b"\0") + body
        self.assertEqual(verifier.static_archive_members(fat), {
            "arm64": [("a.o", sha256(b"arm")), ("long_member_name.o", sha256(b"x" * 9))],
            "x86_64": [("a.o", sha256(b"x86"))]})

    # -- the shipped state ---------------------------------------------------
    def test_shipped_lock_is_blocked_and_its_record_unsigned(self) -> None:
        _, renderer = verifier.load_lock(ROOT / "provenance/third-party-lock.json")
        self.assertEqual(renderer["release_eligibility"], "blocked")
        self.assertNotIn("_verified_signature", renderer["_attestation"])

    def test_cli_eligibility_requires_the_manifest_and_release_assets(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("verify_renderer_dependency.py")),
             "--require-release-eligible"], check=False, capture_output=True, text=True)
        # The shipped lock is blocked, so eligibility fails before asking for the proofs.
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("GPU release is blocked", completed.stderr)

    def run_cli(self, lock_path: Path, record_path: Path, signature: Path, *extra: str):
        return subprocess.run(
            [sys.executable, str(Path(__file__).with_name("verify_renderer_dependency.py")),
             "--lock", str(lock_path), "--attestation", str(record_path),
             "--signature", str(signature), "--allowed-signers", str(self.allowed),
             "--owner-principal", PRINCIPAL, "--owner-fingerprint", self.fingerprint,
             "--require-release-eligible", "--json", *extra],
            check=False, capture_output=True, text=True)

    def test_cli_eligible_lock_still_requires_every_proof(self) -> None:
        lock_path, record_path, signature = self.eligible()
        completed = self.run_cli(lock_path, record_path, signature,
                                 "--legal-manifest", str(self.manifest_path),
                                 "--release-json", str(self.release))
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("release eligibility requires --archive, --toolchain-receipt, "
                      "--provenance-bundle", completed.stderr)
        self.assertNotIn("GPU release is blocked", completed.stderr)

    def test_cli_hollow_manifest_is_refused(self) -> None:
        # A manifest that claims exhaustive but inventories nothing.
        self.write_manifest(self.legal_manifest(zip_members=[], static_archives={}))
        lock_path, record_path, signature = self.eligible()
        completed = self.run_cli(lock_path, record_path, signature,
                                 "--legal-manifest", str(self.manifest_path))
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("inventories no zip member", completed.stderr)

    def test_manifest_must_inventory_exactly_the_locked_archives(self) -> None:
        lock_path, record_path, signature = self.eligible()
        _, renderer = self.load(lock_path, record_path, signature)
        with self.assertRaisesRegex(VerificationError, "differ from the lock's archives"):
            verifier.verify_legal_manifest_file(
                renderer["_attestation"], self.manifest_path,
                locked_archives={"build/mac-gpu/lib/Release/libskia.a",
                                 "build/mac-gpu/lib/Release/libdawn_combined.a"})

    def test_print_signing_payload_is_what_verification_checks(self) -> None:
        _, record_path, _ = self.eligible()
        completed = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("verify_renderer_dependency.py")),
             "--attestation", str(record_path), "--print-signing-payload"],
            check=True, capture_output=True)
        record = json.loads(record_path.read_text())
        self.assertEqual(sha256(completed.stdout), record["signature"]["payload_sha256"])


TESTDATA = Path(__file__).with_name("testdata")


def gh_can_verify() -> bool:
    gh = shutil.which("gh")
    if gh is None:
        return False
    probe = subprocess.run([gh, "attestation", "verify", "--help"], capture_output=True, text=True)
    return probe.returncode == 0 and "--bundle" in probe.stdout


@unittest.skipUnless(gh_can_verify(), "a gh that verifies Sigstore bundles is required")
class RealSigstoreBundleTests(unittest.TestCase):
    """The production verify path against a real skia-builder attestation bundle.

    The bundle and its subject (a toolchain receipt) come from skia-builder run
    39920192131, a branch dispatch, so the probe's source ref is that branch.
    """

    BRANCH_REF = "refs/heads/provenance/apple-legal-manifest-20261022"

    def record(self, source_ref: str) -> dict:
        bundle = TESTDATA / "skia-builder-probe-provenance.sigstore.json"
        digest = sha256(bundle.read_bytes())
        artifact = {"repository": "https://github.com/danielraffel/skia-builder",
                    "release_tag": "chrome/m157", "name": "skia-build-mac-universal-gpu-release.zip"}
        name = f"skia-build-mac-universal-gpu-release.provenance-attestation.sha256-{digest}.sigstore.json"
        return {"artifact": artifact, "build_provenance": {
            "repository": verifier.BUILDER_REPOSITORY, "signer_workflow": verifier.BUILDER_SIGNER_WORKFLOW,
            "source_ref": source_ref,
            "bundle": {"name": name, "sha256": digest, "url": verifier.release_asset_url(artifact, name)}}}

    def verify(self, record: dict, subject: Path):
        return verifier.verify_build_provenance(
            record, {"toolchain receipt": subject}, TESTDATA / "skia-builder-probe-provenance.sigstore.json",
            runner=lambda args: subprocess.run([shutil.which("gh"), *args[1:]], capture_output=True, text=True))

    def test_real_bundle_covers_its_subject(self) -> None:
        # Bypass the pinned-ref check to exercise Sigstore itself on the probe's ref.
        record = self.record(self.BRANCH_REF)
        with unittest.mock.patch.object(verifier, "BUILDER_SOURCE_REF", self.BRANCH_REF):
            result = self.verify(record, TESTDATA / "skia-builder-probe-apple-toolchain.json")
        self.assertEqual(result["build_provenance_verified"], ["toolchain receipt"])

    def test_real_bundle_rejects_other_bytes(self) -> None:
        other = Path(tempfile.mkdtemp()) / "other.json"
        self.addCleanup(shutil.rmtree, other.parent, ignore_errors=True)
        other.write_bytes((TESTDATA / "skia-builder-probe-apple-toolchain.json").read_bytes() + b" ")
        with unittest.mock.patch.object(verifier, "BUILDER_SOURCE_REF", self.BRANCH_REF):
            # The refusal must come from Sigstore, not from a gh that never ran.
            with self.assertRaisesRegex(VerificationError, "does not cover.*verifying with issuer"):
                self.verify(self.record(self.BRANCH_REF), other)

    def test_real_bundle_from_a_branch_is_not_main(self) -> None:
        # Production pins refs/heads/main; the branch-built bundle must not pass it.
        record = self.record(self.BRANCH_REF)
        record["build_provenance"]["source_ref"] = verifier.BUILDER_SOURCE_REF
        with self.assertRaisesRegex(VerificationError, "expected SourceRepositoryRef to be refs/heads/main"):
            self.verify(record, TESTDATA / "skia-builder-probe-apple-toolchain.json")


if __name__ == "__main__":
    unittest.main()
