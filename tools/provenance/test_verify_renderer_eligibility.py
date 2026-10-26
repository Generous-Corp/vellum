#!/usr/bin/env python3
"""Contract tests for the renderer's blocked -> eligible transition.

Every test signs with a throwaway key generated in a temporary directory and
pins that key in place of the owner's, so the same verifier code that checks
the real record is exercised end to end, including ``ssh-keygen -Y verify``.
"""

from __future__ import annotations

import contextlib
import copy
import io
import hashlib
import json
import os
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


def bsd_ar(members: list[tuple[str, bytes]], date: int = 0) -> bytes:
    out = bytearray(b"!<arch>\n")
    for name, data in members:
        encoded = name.encode().ljust(8, b"\0")
        body = encoded + data
        out += f"#1/{len(encoded)}".ljust(16).encode() + str(date).ljust(12).encode() + b"501   20    100644  "
        out += str(len(body)).ljust(10).encode() + b"`\n" + body + (b"\n" if len(body) & 1 else b"")
    return bytes(out)


RUN_URI = "https://github.com/danielraffel/skia-builder/actions/runs/1/attempts/1"


def fake_gh(returncode: int = 0, calls: list | None = None, invocations=None):
    """A stand-in for gh attestation verify; ``invocations`` maps a subject's
    file name to the run invocation its certificate names (default RUN_URI)."""
    def runner(args):
        if calls is not None:
            calls.append(args)
        uri = (invocations or {}).get(Path(args[3]).name, RUN_URI)
        out = json.dumps([{"verificationResult": {"signature": {"certificate": {"runInvocationURI": uri}}}}])
        return subprocess.CompletedProcess(args, returncode, out, "" if returncode == 0 else "no match")
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
                             "licenses": ["source:LICENSE"]}],
                "symbol_tables": []}}}},
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
            "run": {"run_id": 1, "run_attempt": 1},
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
                                 "danielraffel/skia-builder/.github/workflows/build-skia.yml@refs/heads/main"),
                                ("--source-ref", "refs/heads/main")):
                self.assertEqual(args[args.index(flag) + 1], value)
            self.assertIn("--deny-self-hosted-runners", args)

    def test_reserved_underscore_keys_are_refused(self) -> None:
        # The verifier's own results live under "_" keys and are not signed, so
        # a record must not carry any, at the top level or nested.
        for edit in (lambda r: r.update(_verified_signature={"principal": PRINCIPAL}),
                     lambda r: r["build_identity"].update(_note="unsigned"),
                     lambda r: r["legal_inventory"].update(gaps=[{"_x": "inside a list"}])):
            lock_path, record_path, signature = self.eligible()
            self.edit_record(record_path, edit)
            with self.assertRaisesRegex(VerificationError, "uses reserved keys"):
                self.load(lock_path, record_path, signature)

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

    def test_derived_records_must_be_attested_by_the_rebind_workflow(self) -> None:
        def derive(lock, record, workflow):
            record["legal_inventory"]["derived_from"] = {"rebuild_archive": {"sha256": "8" * 64}}
            record["build_provenance"]["signer_workflow"] = workflow
        lock_path, record_path, signature = self.eligible(
            mutate=lambda lock, record: derive(lock, record, verifier.BUILDER_SIGNER_WORKFLOW))
        with self.assertRaisesRegex(VerificationError, "pinned builder repository, workflow and ref"):
            self.load(lock_path, record_path, signature)
        lock_path, record_path, signature = self.eligible(
            mutate=lambda lock, record: derive(lock, record, verifier.REBIND_SIGNER_WORKFLOW))
        _, renderer = self.load(lock_path, record_path, signature)
        self.assertEqual(renderer["_attestation"]["build_provenance"]["signer_workflow"],
                         verifier.REBIND_SIGNER_WORKFLOW)

    def test_build_provenance_is_pinned_to_the_lock_builder_commit(self) -> None:
        lock_path, record_path, signature = self.eligible()
        _, renderer = self.load(lock_path, record_path, signature)
        calls: list = []
        verifier.verify_build_provenance(renderer["_attestation"], {"legal manifest": self.manifest_path},
                                         self.bundle_path, runner=fake_gh(0, calls))
        builder = renderer["_attestation"]["source_identities"]["builder"]["commit"]
        self.assertEqual(calls[0][calls[0].index("--source-digest") + 1], builder)

    def test_rebound_build_provenance_needs_the_rebind_commit(self) -> None:
        def derive(lock, record, digest):
            record["legal_inventory"]["derived_from"] = {"rebuild_archive": {"sha256": "8" * 64}}
            record["build_provenance"]["signer_workflow"] = verifier.REBIND_SIGNER_WORKFLOW
            if digest:
                record["build_provenance"]["source_digest"] = digest
        lock_path, record_path, signature = self.eligible(mutate=lambda lock, record: derive(lock, record, None))
        _, renderer = self.load(lock_path, record_path, signature)
        with self.assertRaisesRegex(VerificationError, "must name the rebind commit"):
            verifier.verify_build_provenance(renderer["_attestation"], {}, self.bundle_path, runner=fake_gh(0))
        lock_path, record_path, signature = self.eligible(
            mutate=lambda lock, record: derive(lock, record, "a" * 40))
        _, renderer = self.load(lock_path, record_path, signature)
        calls: list = []
        verifier.verify_build_provenance(renderer["_attestation"], {"x": self.manifest_path}, self.bundle_path,
                                         runner=fake_gh(0, calls))
        self.assertEqual(calls[0][calls[0].index("--source-digest") + 1], "a" * 40)

    def test_rebound_records_bind_the_archive_by_digest_not_as_a_rebind_subject(self) -> None:
        # rebind-provenance.yml attests only the rebound manifest and receipt,
        # so a rebind bundle never covers the published archive. A gh that
        # refuses the archive (as the real one would) must not fail a derived
        # record; the archive is bound by the rebound manifest's digest.
        self.write_manifest(self.legal_manifest(derived_from={"method": verifier.REBIND_METHOD}))
        def derive(lock, record):
            record["legal_inventory"]["derived_from"] = {"rebuild_archive": {"sha256": "8" * 64}}
            record["build_provenance"]["signer_workflow"] = verifier.REBIND_SIGNER_WORKFLOW
            record["build_provenance"]["source_digest"] = "a" * 40
        lock_path, record_path, signature = self.eligible(mutate=derive)
        _, renderer = self.load(lock_path, record_path, signature)
        attestation = renderer["_attestation"]
        subjects = verifier.build_provenance_subjects(attestation, self.manifest_path, self.receipt_path,
                                                      self.archive)
        self.assertEqual(sorted(subjects), ["legal manifest", "toolchain receipt"])

        def gh(args):
            refused = Path(args[3]) == self.archive
            return fake_gh(1 if refused else 0)(args)
        self.assertEqual(verifier.verify_build_provenance(attestation, subjects, self.bundle_path, runner=gh)[
            "build_provenance_verified"], ["legal manifest", "toolchain receipt"])
        other = self.tmp / "other.zip"
        other.write_bytes(b"not the archive")
        with self.assertRaisesRegex(VerificationError, "not the one the rebind-attested legal manifest describes"):
            verifier.build_provenance_subjects(attestation, self.manifest_path, self.receipt_path, other)

    def test_record_and_manifest_must_agree_on_derivation(self) -> None:
        # A build record with a derived manifest, and a derived record with a
        # build manifest, are both refused before a subject set is chosen.
        lock_path, record_path, signature = self.eligible()
        _, renderer = self.load(lock_path, record_path, signature)
        derived_manifest = self.tmp / "derived-manifest.json"
        derived_manifest.write_text(json.dumps(dict(self.legal_manifest(), derived_from={"method": "x"})))
        with self.assertRaisesRegex(VerificationError, "disagree on whether the records were rebound"):
            verifier.build_provenance_subjects(renderer["_attestation"], derived_manifest,
                                               self.receipt_path, self.archive)

        def derive(lock, record):
            record["legal_inventory"]["derived_from"] = {"rebuild_archive": {"sha256": "8" * 64}}
            record["build_provenance"]["signer_workflow"] = verifier.REBIND_SIGNER_WORKFLOW
            record["build_provenance"]["source_digest"] = "a" * 40
        lock_path, record_path, signature = self.eligible(mutate=derive)
        _, renderer = self.load(lock_path, record_path, signature)
        with self.assertRaisesRegex(VerificationError, "disagree on whether the records were rebound"):
            verifier.build_provenance_subjects(renderer["_attestation"], self.manifest_path,
                                               self.receipt_path, self.archive)

    def test_run_invocation_parser_refuses_non_list_results(self) -> None:
        certificate = {"verificationResult": {"signature": {"certificate": {"runInvocationURI": RUN_URI}}}}
        self.assertEqual(verifier.result_invocations(json.dumps([certificate]), "x"), {RUN_URI})
        for stdout in (json.dumps(certificate), json.dumps("text"), "not json", "[]", json.dumps([{}])):
            with self.assertRaisesRegex(VerificationError, "names no run invocation"):
                verifier.result_invocations(stdout, "x")

    def test_build_records_keep_the_archive_as_a_subject(self) -> None:
        lock_path, record_path, signature = self.eligible()
        _, renderer = self.load(lock_path, record_path, signature)
        self.assertEqual(sorted(verifier.build_provenance_subjects(
            renderer["_attestation"], self.manifest_path, self.receipt_path, self.archive)),
            ["archive", "legal manifest", "toolchain receipt"])

    def test_build_provenance_must_come_from_the_signed_records_run(self) -> None:
        lock_path, record_path, signature = self.eligible()
        _, renderer = self.load(lock_path, record_path, signature)
        other = "https://github.com/danielraffel/skia-builder/actions/runs/1/attempts/2"
        with self.assertRaisesRegex(VerificationError, "not the signed record's"):
            verifier.verify_build_provenance(renderer["_attestation"], {"legal manifest": self.manifest_path},
                                             self.bundle_path,
                                             runner=fake_gh(0, invocations={self.manifest_path.name: other}))
        mixed = {self.manifest_path.name: other}
        with self.assertRaisesRegex(VerificationError, "attested by .*runs/1/attempts/1.*runs/1/attempts/2|attested by .*runs/1/attempts/2.*runs/1/attempts/1"):
            verifier.verify_build_provenance(renderer["_attestation"],
                                             {"legal manifest": self.manifest_path, "toolchain receipt": self.receipt_path},
                                             self.bundle_path, runner=fake_gh(0, invocations=mixed))
        lock_path, record_path, signature = self.eligible(
            mutate=lambda lock, record: record["build_provenance"].pop("run"))
        _, renderer = self.load(lock_path, record_path, signature)
        with self.assertRaisesRegex(VerificationError, "must name the run that attested it"):
            verifier.verify_build_provenance(renderer["_attestation"], {}, self.bundle_path, runner=fake_gh(0))

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

    def test_manifest_with_duplicate_zip_member_names_fails(self) -> None:
        manifest = self.legal_manifest()
        manifest["zip_members"].append(dict(manifest["zip_members"][0]))
        self.write_manifest(manifest)
        lock_path, record_path, signature = self.eligible()
        _, renderer = self.load(lock_path, record_path, signature)
        with self.assertRaisesRegex(VerificationError, "lists a zip member more than once"):
            verifier.verify_legal_manifest_file(renderer["_attestation"], self.manifest_path)

    def test_manifest_symbol_table_differing_from_the_archive_fails(self) -> None:
        manifest = self.legal_manifest()
        manifest["static_archives"]["build/mac-gpu/lib/Release/libskia.a"]["slices"]["arm64"][
            "symbol_tables"] = ["9" * 64]
        self.write_manifest(manifest)
        lock_path, record_path, signature = self.eligible()
        _, renderer = self.load(lock_path, record_path, signature)
        with self.assertRaisesRegex(VerificationError, "symbol tables .* differ from the archive"):
            verifier.verify_legal_manifest_file(renderer["_attestation"], self.manifest_path, self.archive)

    def test_static_archive_relisting_reads_fat_and_bsd_archives(self) -> None:
        arm = bsd_ar([("a.o", b"arm"), ("long_member_name.o", b"x" * 9)])
        x86 = bsd_ar([("a.o", b"x86")])
        header = (0xCAFEBABE).to_bytes(4, "big") + (2).to_bytes(4, "big")
        table = b""
        body = b""
        offset = 4096
        for cputype, subtype, data in ((0x0100000C, 0, arm), (0x01000007, 3, x86)):
            table += b"".join(v.to_bytes(4, "big") for v in (cputype, subtype, offset + len(body), len(data), 12))
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
        """The CLI in process, with the pinned owner swapped for the test key.

        The CLI has no flag that overrides the owner identity, so a release
        invocation cannot pass one; only the module constants change here.
        """
        argv = ["verify_renderer_dependency.py", "--lock", str(lock_path),
                "--attestation", str(record_path), "--signature", str(signature),
                "--allowed-signers", str(self.allowed), "--require-release-eligible", "--json", *extra]
        stderr = io.StringIO()
        with unittest.mock.patch.object(verifier, "OWNER_PRINCIPAL", PRINCIPAL), \
                unittest.mock.patch.object(verifier, "OWNER_FINGERPRINT", self.fingerprint), \
                unittest.mock.patch.object(sys, "argv", argv), \
                contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
            code = verifier.main()
        return subprocess.CompletedProcess(argv, code, "", stderr.getvalue())

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


class RebindVerificationTests(unittest.TestCase):
    """Records rebound from a member-identical rebuild are re-compared, not trusted."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="vellum-rebind-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.locked = self.archive("locked", date=1)

    def archive(self, where: str, date: int, objects=None, header: bytes = b"int core(void);\n") -> Path:
        path = self.tmp / where / "skia-build-mac-universal-gpu-release.zip"
        path.parent.mkdir(parents=True)
        with zipfile.ZipFile(path, "w") as handle:
            handle.writestr(zipfile.ZipInfo("build/include/core.h", (2026, 8, 18, 4, 0, date * 2)), header)
            handle.writestr("build/mac-gpu/lib/Release/libskia.a",
                            bsd_ar(objects or [("a.o", OBJECT), ("a.o", b"dup")], date=date))
        return path

    REBUILD_MANIFEST = {
        "schema": verifier.LEGAL_MANIFEST_SCHEMA, "exhaustive": True, "gaps": [],
        "licenses": {"source:LICENSE": {"sha256": "0" * 64, "text": "BSD"}},
        "files": {"source:a.c": {"licenses": ["source:LICENSE"]}},
        "zip_members": [{"name": "build/include/core.h", "kind": "packaged-file",
                         "origin": "source:include/core.h", "licenses": ["source:LICENSE"]}],
    }

    ARTIFACT = "skia-build-mac-universal-gpu-release.zip"
    HEAD = "c" * 40

    def rebuild_manifest_for(self, rebuild: Path) -> dict:
        """The rebuild's own manifest: bound to the rebuild archive, listing its members."""
        manifest = json.loads(json.dumps(self.REBUILD_MANIFEST))
        manifest["archive"] = {"name": self.ARTIFACT, "sha256": sha256(rebuild.read_bytes())}
        with zipfile.ZipFile(rebuild) as handle:
            manifest["zip_members"] = [
                {"name": info.filename, "sha256": sha256(handle.read(info)),
                 "kind": "static-archive" if info.filename.endswith(".a") else "packaged-file",
                 "origin": None if info.filename.endswith(".a") else f"source:{info.filename}",
                 "licenses": ["source:LICENSE"]}
                for info in handle.infolist()]
        return manifest

    def records(self, rebuild: Path, receipt_fields=None, receipt_derived=None, rebound_changes=None,
                rebuild_manifest_edit=None, rebuild_receipt_archive=None):
        """Rebuild records, rebound records, the rebuild bundle and the signed record."""
        rebuild_receipt = {"compiler": {"cxx": {"version": "Apple clang version 17.0.0"}},
                           "sdk": {"version": "15.5"}, "runner": {"image_version": "20260907.0337.1"},
                           "archive": rebuild_receipt_archive or {"name": self.ARTIFACT,
                                                                  "sha256": sha256(rebuild.read_bytes())}}
        rebuild_receipt_path = self.tmp / "rebuild-receipt.json"
        rebuild_receipt_path.write_text(json.dumps(rebuild_receipt))
        self.bundle = self.tmp / "rebuild.sigstore.json"
        self.bundle.write_text('{"mediaType": "application/vnd.dev.sigstore.bundle.v0.3+json"}')
        rebuild_manifest = self.rebuild_manifest_for(rebuild)
        if rebuild_manifest_edit:
            rebuild_manifest_edit(rebuild_manifest)
        rebuild_manifest_path = self.tmp / "rebuild-manifest.json"
        rebuild_manifest_path.write_text(json.dumps(rebuild_manifest))
        derived = {"method": verifier.REBIND_METHOD,
                   "comparison": {"name": "c.json", "sha256": "d" * 64, "verdict": "metadata-only"},
                   "rebuild_archive": {"name": rebuild.name, "sha256": sha256(rebuild.read_bytes())},
                   "rebuild_receipt": {"name": "r.json", "sha256": sha256(rebuild_receipt_path.read_bytes())},
                   "rebuild_manifest": {"name": "m.json",
                                        "sha256": sha256(rebuild_manifest_path.read_bytes())},
                   "rebuild_bundle": {"name": self.bundle.name, "sha256": sha256(self.bundle.read_bytes())},
                   "rebuild_run": {"repository": "danielraffel/skia-builder", "run_id": 1,
                                   "run_attempt": 1, "ref": "refs/heads/main", "head_sha": self.HEAD}}
        rebound = json.loads(json.dumps(rebuild_manifest))
        rebound.update(rebound_changes or {}, derived_from=derived)
        manifest_path, receipt_path = self.tmp / "manifest.json", self.tmp / "receipt.json"
        manifest_path.write_text(json.dumps(rebound))
        receipt = dict(rebuild_receipt, derived_from=receipt_derived or derived, **(receipt_fields or {}))
        receipt_path.write_text(json.dumps(receipt))
        self.attestation = {"artifact": {"name": self.ARTIFACT},
                            "legal_inventory": {"derived_from": json.loads(json.dumps(derived))}}
        return manifest_path, receipt_path, rebuild_receipt_path, rebuild_manifest_path

    def verify(self, rebuild: Path, gh=None, **kwargs):
        manifest_path, receipt_path, rebuild_receipt, rebuild_manifest = self.records(rebuild, **kwargs)
        return verifier.verify_rebind(self.attestation, manifest_path, receipt_path, self.locked, rebuild,
                                      rebuild_receipt, rebuild_manifest, self.bundle, gh or fake_gh(0))

    def test_member_identical_rebuild_verifies(self) -> None:
        calls: list = []
        result = self.verify(self.archive("rebuild", date=2), gh=fake_gh(0, calls))
        self.assertEqual(result["rebind_archives_with_container_differences"], 1)
        self.assertEqual(result["rebuild_provenance_verified"],
                         ["archive", "legal manifest", "toolchain receipt"])
        self.assertEqual(len(calls), 3)
        for args in calls:
            for flag, value in (("--bundle", str(self.bundle)), ("--repo", "danielraffel/skia-builder"),
                                ("--signer-workflow",
                                 "danielraffel/skia-builder/.github/workflows/build-skia.yml@refs/heads/main"),
                                ("--source-ref", "refs/heads/main")):
                self.assertEqual(args[args.index(flag) + 1], value)
            self.assertIn("--deny-self-hosted-runners", args)

    def test_rebuild_not_attested_on_main_is_refused(self) -> None:
        # The rebound records copy the rebuild's license mapping and toolchain
        # values, so a rebuild whose own attestation does not verify for
        # build-skia.yml on main (a branch run, edited tools) is refused.
        with self.assertRaisesRegex(VerificationError, "rebuild archive is not attested by build-skia.yml on main"):
            self.verify(self.archive("rebuild", date=2), gh=fake_gh(1))

    def test_rebuild_bundle_must_be_the_signed_records(self) -> None:
        rebuild = self.archive("rebuild", date=2)
        manifest_path, receipt_path, rebuild_receipt, rebuild_manifest = self.records(rebuild)
        self.bundle.write_text("{}")
        with self.assertRaisesRegex(VerificationError, "not the one the signed record names"):
            verifier.verify_rebind(self.attestation, manifest_path, receipt_path, self.locked, rebuild,
                                   rebuild_receipt, rebuild_manifest, self.bundle, fake_gh(0))
        del self.attestation["legal_inventory"]["derived_from"]["rebuild_bundle"]
        with self.assertRaisesRegex(VerificationError, "attested derivation's rebuild_bundle differs"):
            verifier.verify_rebind(self.attestation, manifest_path, receipt_path, self.locked, rebuild,
                                   rebuild_receipt, rebuild_manifest, self.bundle, fake_gh(0))

    def test_rebound_license_mapping_must_be_the_rebuilds(self) -> None:
        changed = {"licenses": {"source:LICENSE": {"sha256": "0" * 64, "text": "MIT, actually"}}}
        with self.assertRaisesRegex(VerificationError, "license mapping differs.*licenses"):
            self.verify(self.archive("rebuild", date=2), rebound_changes=changed)
        changed = {"zip_members": [{"name": "build/include/core.h", "kind": "packaged-file",
                                    "origin": "source:include/core.h", "licenses": []}]}
        with self.assertRaisesRegex(VerificationError, "license mapping differs.*zip_members"):
            self.verify(self.archive("rebuild2", date=2), rebound_changes=changed)

    def test_rebuild_manifest_must_describe_the_rebuild_archive(self) -> None:
        for edit in (lambda m: m.pop("archive"),
                     lambda m: m.update(archive={"name": self.ARTIFACT, "sha256": "e" * 64}),
                     lambda m: m.update(archive=dict(m["archive"], name="other.zip"))):
            with self.assertRaisesRegex(VerificationError, "rebuild manifest does not describe the rebuild archive"):
                self.verify(self.archive(f"rebuild-{id(edit)}", date=2), rebuild_manifest_edit=edit)

    def test_rebuild_receipt_must_describe_the_rebuild_archive(self) -> None:
        with self.assertRaisesRegex(VerificationError, "rebuild receipt does not describe the rebuild archive"):
            self.verify(self.archive("rebuild", date=2),
                        rebuild_receipt_archive={"name": self.ARTIFACT, "sha256": "e" * 64})

    def test_rebuild_manifest_zip_members_must_match_the_rebuild_archive(self) -> None:
        def flip(manifest):
            manifest["zip_members"][0]["sha256"] = "f" * 64
        with self.assertRaisesRegex(VerificationError, "zip members differ from the rebuild archive"):
            self.verify(self.archive("rebuild", date=2), rebuild_manifest_edit=flip)

    def test_duplicate_zip_member_rows_are_refused(self) -> None:
        def duplicate(manifest):
            manifest["zip_members"].append(dict(manifest["zip_members"][0]))
        with self.assertRaisesRegex(VerificationError, "lists a zip member more than once"):
            self.verify(self.archive("rebuild", date=2), rebuild_manifest_edit=duplicate)
        # The rebound mapping is compared as a multiset: a duplicated row can't
        # stand in for a different one.
        row_a = {"name": "a", "kind": "packaged-file", "origin": "x", "licenses": ["L"]}
        row_b = {"name": "b", "kind": "packaged-file", "origin": "y", "licenses": ["M"]}
        rebound, rebuild = [row_a, row_a, row_b], [row_a, row_b, row_b]
        self.assertIn("zip_members", verifier.manifest_inventory_problems(
            {"zip_members": rebound}, {"zip_members": rebuild}))

    def test_rebuild_records_attested_by_different_runs_are_refused(self) -> None:
        other = "https://github.com/danielraffel/skia-builder/actions/runs/2/attempts/1"
        with self.assertRaisesRegex(VerificationError, "attested by different runs"):
            self.verify(self.archive("rebuild", date=2),
                        gh=fake_gh(0, invocations={"rebuild-receipt.json": other}))

    def test_rebuild_commit_is_required_and_bound(self) -> None:
        calls: list = []
        self.verify(self.archive("rebuild", date=2), gh=fake_gh(0, calls))
        for args in calls:
            self.assertEqual(args[args.index("--source-digest") + 1], self.HEAD)
        rebuild = self.archive("rebuild-nohead", date=2)
        manifest_path, receipt_path, rebuild_receipt, rebuild_manifest = self.records(rebuild)
        del self.attestation["legal_inventory"]["derived_from"]["rebuild_run"]["head_sha"]
        with self.assertRaisesRegex(VerificationError, "rebuild commit differs"):
            verifier.verify_rebind(self.attestation, manifest_path, receipt_path, self.locked, rebuild,
                                   rebuild_receipt, rebuild_manifest, self.bundle, fake_gh(0))
        with self.assertRaisesRegex(VerificationError, "names no rebuild commit"):
            verifier.verify_rebuild_provenance({"rebuild_bundle": {"sha256": "0" * 64}}, {}, self.bundle,
                                               fake_gh(0))

    def test_rebuild_attested_by_a_run_other_than_the_signed_records_is_refused(self) -> None:
        # All three subjects agree with each other but name run 1 attempt 2,
        # while the signed record names attempt 1.
        other = "https://github.com/danielraffel/skia-builder/actions/runs/1/attempts/2"
        everything = {name: other for name in ("skia-build-mac-universal-gpu-release.zip",
                                               "rebuild-manifest.json", "rebuild-receipt.json")}
        with self.assertRaisesRegex(VerificationError, "not the signed record's"):
            self.verify(self.archive("rebuild", date=2), gh=fake_gh(0, invocations=everything))

    def test_rebuild_run_fields_are_cross_checked(self) -> None:
        for field, value in (("repository", "someone/fork"), ("run_id", 2), ("run_attempt", 2),
                             ("ref", "refs/heads/probe")):
            rebuild = self.archive(f"rebuild-{field}", date=2)
            manifest_path, receipt_path, rebuild_receipt, rebuild_manifest = self.records(rebuild)
            self.attestation["legal_inventory"]["derived_from"]["rebuild_run"][field] = value
            with self.assertRaisesRegex(VerificationError, f"rebuild_run.{field} differs"):
                verifier.verify_rebind(self.attestation, manifest_path, receipt_path, self.locked, rebuild,
                                       rebuild_receipt, rebuild_manifest, self.bundle, fake_gh(0))

    def test_rebuild_run_must_be_a_main_build_skia_run(self) -> None:
        derived = {"rebuild_bundle": {"sha256": "0" * 64},
                   "rebuild_run": {"repository": "danielraffel/skia-builder", "run_id": 1, "run_attempt": 1,
                                   "ref": "refs/heads/probe", "head_sha": "c" * 40}}
        with self.assertRaisesRegex(VerificationError, "not a build-skia.yml run on main"):
            verifier.verify_rebuild_provenance(derived, {}, self.tmp / "unused.sigstore.json", fake_gh(0))

    def test_attested_rebuild_bundle_must_match_the_manifests(self) -> None:
        rebuild = self.archive("rebuild", date=2)
        manifest_path, receipt_path, rebuild_receipt, rebuild_manifest = self.records(rebuild)
        self.attestation["legal_inventory"]["derived_from"]["rebuild_bundle"]["sha256"] = "9" * 64
        with self.assertRaisesRegex(VerificationError, "attested derivation's rebuild_bundle differs"):
            verifier.verify_rebind(self.attestation, manifest_path, receipt_path, self.locked, rebuild,
                                   rebuild_receipt, rebuild_manifest, self.bundle, fake_gh(0))

    def test_unsigned_derivation_is_refused(self) -> None:
        rebuild = self.archive("rebuild", date=2)
        manifest_path, receipt_path, rebuild_receipt, rebuild_manifest = self.records(rebuild)
        with self.assertRaisesRegex(VerificationError, "signed record does not name the derivation"):
            verifier.verify_rebind({}, manifest_path, receipt_path, self.locked, rebuild,
                                   rebuild_receipt, rebuild_manifest, self.bundle, fake_gh(0))

    def test_rebuild_with_different_object_content_is_refused(self) -> None:
        with self.assertRaisesRegex(VerificationError, "member, symbol table or slice differs"):
            self.verify(self.archive("rebuild", date=2, objects=[("a.o", b"other"), ("a.o", b"dup")]))

    def test_rebuild_with_a_different_header_is_refused(self) -> None:
        with self.assertRaisesRegex(VerificationError, "core.h differs in content"):
            self.verify(self.archive("rebuild", date=2, header=b"patched\n"))

    def fat_archive(self, where: str, date: int, x86_symdef: bytes = b"\0" * 8, extra=None) -> Path:
        x86 = bsd_ar([("__.SYMDEF SORTED", x86_symdef), ("x.o", b"x86 object")], date=date)
        pieces = [(0x0100000C, 0, bsd_ar([("a.o", OBJECT)], date=date)), (0x01000007, 3, x86)]
        if extra is not None:
            pieces.insert(0, extra)
        header = (0xCAFEBABE).to_bytes(4, "big") + len(pieces).to_bytes(4, "big")
        table, body, offset = b"", b"", 4096
        for cputype, subtype, data in pieces:
            table += b"".join(v.to_bytes(4, "big") for v in (cputype, subtype, offset + len(body), len(data), 12))
            body += data + b"\0" * ((-len(data)) % 16)
        path = self.tmp / where / "skia-build-mac-universal-gpu-release.zip"
        path.parent.mkdir(parents=True)
        with zipfile.ZipFile(path, "w") as handle:
            handle.writestr("build/mac-gpu/lib/Release/libskresources.a",
                            (header + table).ljust(offset, b"\0") + body)
        return path

    def test_flipped_symbol_table_byte_is_refused(self) -> None:
        # Reviewer mutation: one byte flipped in the x86_64 __.SYMDEF body.
        self.locked = self.fat_archive("locked-fat", date=1)
        self.assertEqual(self.verify(self.fat_archive("control", date=2))["rebind_verified"], True)
        with self.assertRaisesRegex(VerificationError, "member, symbol table or slice differs"):
            self.verify(self.fat_archive("rebuild", date=2, x86_symdef=b"\0" * 7 + b"\1"))

    def test_extra_slice_of_the_same_cpu_type_is_refused(self) -> None:
        # Reviewer mutation: an extra x86_64 slice (cpusubtype 4) with a flipped object.
        self.locked = self.fat_archive("locked-fat", date=1)
        extra = (0x01000007, 4, bsd_ar([("x.o", b"X86 object")], date=2))
        with self.assertRaisesRegex(VerificationError, "member, symbol table or slice differs"):
            self.verify(self.fat_archive("rebuild", date=2, extra=extra))

    def test_duplicate_slice_is_refused(self) -> None:
        self.locked = self.fat_archive("locked-fat", date=1)
        duplicate = (0x01000007, 3, bsd_ar([("x.o", b"x86 object")], date=2))
        with self.assertRaisesRegex(VerificationError, "more than one x86_64 slice"):
            self.verify(self.fat_archive("rebuild", date=2, extra=duplicate))

    def test_derived_manifest_requires_every_rebuild_proof(self) -> None:
        rebuild = self.archive("rebuild", date=2)
        manifest_path, _receipt, rebuild_receipt, rebuild_manifest = self.records(rebuild)
        proofs = [rebuild, rebuild_receipt, rebuild_manifest, self.bundle]
        for missing in range(len(proofs)):
            partial = [None if index == missing else proof for index, proof in enumerate(proofs)]
            with self.assertRaisesRegex(VerificationError, "--rebuild-provenance-bundle are required"):
                verifier.rebuild_proofs_required(manifest_path, *partial)
        self.assertTrue(verifier.rebuild_proofs_required(manifest_path, *proofs))
        plain = self.tmp / "plain.json"
        plain.write_text("{}")
        self.assertFalse(verifier.rebuild_proofs_required(plain, None, None, None, None))

    def test_another_rebuild_archive_is_refused(self) -> None:
        rebuild = self.archive("rebuild", date=2)
        manifest_path, receipt_path, rebuild_receipt, rebuild_manifest = self.records(rebuild)
        other = self.archive("other", date=3)
        with self.assertRaisesRegex(VerificationError, "not the one the records were derived from"):
            verifier.verify_rebind(self.attestation, manifest_path, receipt_path, self.locked, other,
                                   rebuild_receipt, rebuild_manifest, self.bundle, fake_gh(0))

    def test_receipt_naming_another_derivation_is_refused(self) -> None:
        with self.assertRaisesRegex(VerificationError, "different derivations"):
            self.verify(self.archive("rebuild", date=2),
                        receipt_derived={"method": verifier.REBIND_METHOD, "rebuild_archive": {}})

    def test_attested_derivation_must_match_the_manifest(self) -> None:
        rebuild = self.archive("rebuild", date=2)
        manifest_path, receipt_path, rebuild_receipt, rebuild_manifest = self.records(rebuild)
        attestation = {"legal_inventory": {"derived_from": {"rebuild_archive": {"sha256": "7" * 64}}}}
        with self.assertRaisesRegex(VerificationError, "attested derivation's rebuild_archive differs"):
            verifier.verify_rebind(attestation, manifest_path, receipt_path, self.locked, rebuild,
                                   rebuild_receipt, rebuild_manifest, self.bundle, fake_gh(0))

    def test_rebound_receipt_differing_from_the_rebuild_receipt_is_refused(self) -> None:
        with self.assertRaisesRegex(VerificationError, "sdk differs from the rebuild receipt"):
            self.verify(self.archive("rebuild", date=2), receipt_fields={"sdk": {"version": "26.0"}})


TESTDATA = Path(__file__).with_name("testdata")


def gh_can_verify() -> bool:
    gh = shutil.which("gh")
    if gh is None:
        return False
    probe = subprocess.run([gh, "attestation", "verify", "--help"], capture_output=True, text=True)
    return probe.returncode == 0 and "--bundle" in probe.stdout


# CI sets VELLUM_REQUIRE_REAL_SIGSTORE=1 so these tests can never be skipped
# there; elsewhere they skip when gh cannot verify bundles.
REQUIRE_REAL_SIGSTORE = os.environ.get("VELLUM_REQUIRE_REAL_SIGSTORE") == "1"


@unittest.skipUnless(REQUIRE_REAL_SIGSTORE or gh_can_verify(),
                     "a gh that verifies Sigstore bundles is required")
class RealSigstoreBundleTests(unittest.TestCase):
    """The production verify path against a real skia-builder attestation bundle.

    The bundle and its subject (a toolchain receipt) come from skia-builder run
    39920192131, a branch dispatch, so the probe's source ref is that branch.
    """

    BRANCH_REF = "refs/heads/provenance/apple-legal-manifest-20261022"
    COMMIT = "0091d97f17ae716f2fd5968e37a903224cd32b49"  # the run's head commit

    def record(self, source_ref: str) -> dict:
        bundle = TESTDATA / "skia-builder-probe-provenance.sigstore.json"
        digest = sha256(bundle.read_bytes())
        artifact = {"repository": "https://github.com/danielraffel/skia-builder",
                    "release_tag": "chrome/m157", "name": "skia-build-mac-universal-gpu-release.zip"}
        name = f"skia-build-mac-universal-gpu-release.provenance-attestation.sha256-{digest}.sigstore.json"
        return {"artifact": artifact,
                "source_identities": {"builder": {"commit": self.COMMIT}},
                "build_provenance": {
            "repository": verifier.BUILDER_REPOSITORY, "signer_workflow": verifier.BUILDER_SIGNER_WORKFLOW,
            "source_ref": source_ref, "run": {"run_id": 39920192131, "run_attempt": 1},
            "bundle": {"name": name, "sha256": digest, "url": verifier.release_asset_url(artifact, name)}}}

    def verify(self, record: dict, subject: Path):
        return verifier.verify_build_provenance(
            record, {"toolchain receipt": subject}, TESTDATA / "skia-builder-probe-provenance.sigstore.json",
            runner=lambda args: subprocess.run([shutil.which("gh"), *args[1:]], capture_output=True, text=True))

    def test_gh_can_verify_bundles(self) -> None:
        self.assertTrue(gh_can_verify(), "gh cannot verify Sigstore bundles on this runner")

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

    def rebuild_derivation(self, ref: str = "refs/heads/main") -> dict:
        bundle = TESTDATA / "skia-builder-probe-provenance.sigstore.json"
        return {"rebuild_bundle": {"name": bundle.name, "sha256": sha256(bundle.read_bytes())},
                "rebuild_run": {"repository": "danielraffel/skia-builder", "run_id": 39920192131,
                                "run_attempt": 1, "ref": ref, "head_sha": self.COMMIT}}

    def verify_rebuild(self, ref: str = "refs/heads/main"):
        return verifier.verify_rebuild_provenance(
            self.rebuild_derivation(ref),
            {"toolchain receipt": TESTDATA / "skia-builder-probe-apple-toolchain.json"},
            TESTDATA / "skia-builder-probe-provenance.sigstore.json",
            runner=lambda args: subprocess.run([shutil.which("gh"), *args[1:]], capture_output=True, text=True))

    def test_rebuild_from_a_branch_ref_is_refused(self) -> None:
        # The fixture rebuild ran on a branch; rebound records may only rest on
        # a rebuild attested by build-skia.yml on refs/heads/main.
        with self.assertRaisesRegex(VerificationError,
                                    "not attested by build-skia.yml on main: Error: verifying"):
            self.verify_rebuild()

    def test_rebuild_from_another_commit_is_refused(self) -> None:
        # --source-digest binds the attestation to the rebuild commit the
        # signed record names.
        derivation = self.rebuild_derivation(self.BRANCH_REF)
        derivation["rebuild_run"]["head_sha"] = "1" * 40
        with unittest.mock.patch.object(verifier, "BUILDER_SOURCE_REF", self.BRANCH_REF):
            with self.assertRaisesRegex(VerificationError, "not attested by build-skia.yml on main"):
                verifier.verify_rebuild_provenance(
                    derivation, {"toolchain receipt": TESTDATA / "skia-builder-probe-apple-toolchain.json"},
                    TESTDATA / "skia-builder-probe-provenance.sigstore.json",
                    runner=lambda args: subprocess.run([shutil.which("gh"), *args[1:]],
                                                       capture_output=True, text=True))

    def test_rebuild_check_accepts_the_bundle_on_its_own_ref(self) -> None:
        # Control: the same bundle and subject verify when the pinned ref is the
        # one the fixture ran on, so the refusal above is the ref, not the bundle.
        with unittest.mock.patch.object(verifier, "BUILDER_SOURCE_REF", self.BRANCH_REF):
            self.assertEqual(self.verify_rebuild(self.BRANCH_REF), ["toolchain receipt"])

    def test_rebuild_check_binds_the_real_run_invocation(self) -> None:
        # The real certificate names run 39920192131 attempt 1; a record that
        # names attempt 2 of the same run is refused.
        derivation = self.rebuild_derivation(self.BRANCH_REF)
        derivation["rebuild_run"]["run_attempt"] = 2
        with unittest.mock.patch.object(verifier, "BUILDER_SOURCE_REF", self.BRANCH_REF):
            with self.assertRaisesRegex(VerificationError, "not the signed record's"):
                verifier.verify_rebuild_provenance(
                    derivation, {"toolchain receipt": TESTDATA / "skia-builder-probe-apple-toolchain.json"},
                    TESTDATA / "skia-builder-probe-provenance.sigstore.json",
                    runner=lambda args: subprocess.run([shutil.which("gh"), *args[1:]],
                                                       capture_output=True, text=True))

    def test_real_bundle_from_a_branch_is_not_main(self) -> None:
        # Production pins refs/heads/main; the branch-built bundle must not pass it.
        record = self.record(self.BRANCH_REF)
        record["build_provenance"]["source_ref"] = verifier.BUILDER_SOURCE_REF
        # The signer workflow is pinned as build-skia.yml@refs/heads/main and the
        # source ref as refs/heads/main; either refuses a branch-built bundle.
        with self.assertRaisesRegex(VerificationError, "does not cover the toolchain receipt: Error: verifying"):
            self.verify(record, TESTDATA / "skia-builder-probe-apple-toolchain.json")


if __name__ == "__main__":
    unittest.main()
