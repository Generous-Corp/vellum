#!/usr/bin/env python3
"""Contract tests for the renderer's blocked -> eligible transition.

The renderer becomes release eligible only on skia-builder's own records for
the exact locked archive: a Sigstore build-provenance attestation from
build-skia.yml on main at the lock's builder commit, the content-addressed
legal manifest, and the content-addressed Apple toolchain receipt.

The fixture tests build a complete, internally consistent set of those records
around a small archive and run the same verifier code the release runs. A
stand-in for `gh attestation verify` reports what real Sigstore would report
for the fixture bundle, so each of the verifier's own checks can be shown to
refuse its binding. RealSigstoreTests then runs real `gh attestation verify`
against the real chrome/m158 bundle; CI requires those tests
(VELLUM_REQUIRE_REAL_SIGSTORE=1) so they can never be skipped there.
"""

from __future__ import annotations

import base64
import contextlib
import copy
import hashlib
import io
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
TESTDATA = Path(__file__).with_name("testdata")
STEM = "skia-build-mac-universal-gpu-release"
OBJECT = b"\xcf\xfa\xed\xfe object bytes"
BIN = "/Applications/Xcode_16.4.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin"
RUN_ID, RUN_ATTEMPT = 40626447857, 1
INVOCATION = f"https://github.com/danielraffel/skia-builder/actions/runs/{RUN_ID}/attempts/{RUN_ATTEMPT}"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def bsd_ar(members: list[tuple[str, bytes]], date: int = 0) -> bytes:
    out = bytearray(b"!<arch>\n")
    for name, data in members:
        encoded = name.encode().ljust(8, b"\0")
        body = encoded + data
        out += f"#1/{len(encoded)}".ljust(16).encode() + str(date).ljust(12).encode() + b"501   20    100644  "
        out += str(len(body)).ljust(10).encode() + b"`\n" + body + (b"\n" if len(body) & 1 else b"")
    return bytes(out)


def certificate(builder_commit: str, **overrides) -> dict:
    """The certificate fields real Sigstore reports for a build-skia.yml main build."""
    row = {
        "issuer": "https://token.actions.githubusercontent.com",
        "subjectAlternativeName": verifier.BUILDER_SIGNER_URI,
        "buildSignerURI": verifier.BUILDER_SIGNER_URI,
        "buildSignerDigest": builder_commit,
        "sourceRepositoryURI": "https://github.com/danielraffel/skia-builder",
        "sourceRepositoryDigest": builder_commit,
        "sourceRepositoryRef": "refs/heads/main",
        "githubWorkflowRepository": "danielraffel/skia-builder",
        "githubWorkflowRef": "refs/heads/main",
        "runnerEnvironment": "github-hosted",
        "runInvocationURI": INVOCATION,
        "sourceRepositoryIdentifier": "1247180457",
        "sourceRepositoryOwnerIdentifier": "25807",
    }
    row.update(overrides)
    return row


class FakeGh:
    """Stands in for `gh attestation verify --format json` over a fixture bundle.

    It reports the bundle's own statement and a certificate, the way real
    Sigstore does after it has verified the signature; the verifier's checks
    of those values are what the fixture tests exercise.
    """

    def __init__(self, builder_commit: str) -> None:
        self.calls: list[list[str]] = []
        self.returncode = 0
        self.certificate = certificate(builder_commit)
        self.statement_edit = None
        self.results = 1

    def __call__(self, args: list[str]) -> subprocess.CompletedProcess:
        self.calls.append(args)
        bundle = Path(args[args.index("--bundle") + 1]).read_bytes()
        statement = verifier.bundle_statement(bundle)
        if self.statement_edit:
            statement = copy.deepcopy(statement)
            self.statement_edit(statement)
        row = {"verificationResult": {"signature": {"certificate": self.certificate},
                                      "statement": statement}}
        out = json.dumps([row] * self.results)
        return subprocess.CompletedProcess(args, self.returncode, out,
                                           "" if self.returncode == 0 else "Error: verifying")


class Fixture:
    """A complete, consistent set of builder records around a small archive."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        lock = json.loads((ROOT / "provenance/third-party-lock.json").read_text())
        self.lock = lock
        renderer = lock["runtime_toolchain_dependencies"]["renderer"]
        self.builder = renderer["source_identities"]["builder"]["commit"]
        self.skia = renderer["source_identities"]["skia"]["commit"]
        self.dawn = renderer["source_identities"]["dawn"]["commit"]
        self.archive_members = {"build/mac-gpu/lib/Release/libskia.a": bsd_ar([("a.o", OBJECT)]),
                                "build/mac-gpu/lib/Release/libdawn_combined.a": bsd_ar([("d.o", b"dawn")])}
        self.write_archive()
        self.manifest = self.legal_manifest()
        self.receipt = self.toolchain_receipt()
        self.statement_edit = None
        self.gh = FakeGh(self.builder)

    # -- the builder's records -------------------------------------------
    def write_archive(self, extra: dict[str, bytes] | None = None) -> None:
        renderer = self.lock["runtime_toolchain_dependencies"]["renderer"]
        dawn_bytes = ", ".join(f"0x{self.dawn[i:i + 2]}" for i in range(0, 40, 2))
        gn_args = "".join(
            f"{verifier_feature(key)} = {str(value).lower() if isinstance(value, bool) else value}\n"
            for key, value in renderer["features"].items())
        files = {"build/include/core.h": b"int core(void);\n",
                 "build/include/dawn/dawn_version.h":
                     ("namespace dawn { static constexpr std::array<uint8_t, 20> kDawnVersion = { "
                      + dawn_bytes + " }; }\n").encode(),
                 "build/mac-gpu/lib/gn_args.txt": gn_args.encode()}
        files.update(self.archive_members)
        files.update(extra or {})
        self.archive = self.tmp / f"{STEM}.zip"
        with zipfile.ZipFile(self.archive, "w") as handle:
            for name, data in files.items():
                handle.writestr(name, data)
        self.archive_sha = sha256(self.archive.read_bytes())
        self.files = files

    def legal_manifest(self) -> dict:
        members = [{"name": name, "sha256": sha256(data),
                    "kind": "static-archive" if name.endswith(".a") else "packaged-file",
                    **({} if name.endswith(".a") else {"licenses": ["source:LICENSE"]})}
                   for name, data in self.files.items()]
        static = {}
        for name, data in self.files.items():
            if name.endswith(".a"):
                listed = verifier.static_archive_contents(data)["thin"]
                static[name] = {"slices": {"arm64": {
                    "members": [{"name": member, "sha256": digest, "objects": [f"generated:arm64:obj/{member}"],
                                 "licenses": ["source:LICENSE"]} for member, digest in listed["members"]],
                    "symbol_tables": listed["symbol_tables"]}}}
        text = "BSD-3-Clause license text"
        return {
            "schema": verifier.LEGAL_MANIFEST_SCHEMA,
            "archive": {"name": self.archive.name, "sha256": self.archive_sha},
            "exhaustive": True,
            "gaps": [],
            "source_revisions": {"skia": {"checkout": ".", "commit": self.skia, "expected": self.skia},
                                 "dawn": {"checkout": "third_party/externals/dawn", "commit": self.dawn,
                                          "expected": self.dawn}},
            "zip_members": members,
            "static_archives": static,
            "licenses": {"source:LICENSE": {"sha256": sha256(text.encode()), "text": text}},
        }

    def toolchain_receipt(self) -> dict:
        return {
            "schema": verifier.TOOLCHAIN_RECEIPT_SCHEMA,
            "archive": {"name": self.archive.name, "sha256": self.archive_sha},
            "compiler": {"cxx": {"path": f"{BIN}/clang++", "sha256": "a" * 64,
                                 "version": "Apple clang version 17.0.0 (clang-1700.0.13.5)"}},
            "linker": {"path": f"{BIN}/ld", "sha256": "b" * 64,
                       "version": "@(#)PROGRAM:ld PROJECT:ld-1167.5"},
            "sdk": {"version": "15.5"},
            "target": {"architectures": ["arm64", "x86_64"], "configuration": "Release",
                       "minimum_os": "13.0", "platform": "mac", "sdk_name": "macosx", "variant": "gpu"},
            "cpp_abi": {arch: {"libcpp_version": "190102", "libcpp_abi_version": "1", "stdlib": "libc++"}
                        for arch in ("arm64", "x86_64")},
            "observed_macho": {"sdk_versions": ["15.5"]},
        }

    def content_addressed(self, kind: str, data: bytes, suffix: str = ".json") -> Path:
        path = self.tmp / f"{STEM}.{kind}.sha256-{sha256(data)}{suffix}"
        path.write_bytes(data)
        return path

    def statement(self) -> dict:
        subjects = [{"name": path.name, "digest": {"sha256": sha256(path.read_bytes())}}
                    for path in (self.archive, self.receipt_path, self.manifest_path)]
        statement = {
            "_type": "https://in-toto.io/Statement/v1",
            "subject": subjects,
            "predicateType": "https://slsa.dev/provenance/v1",
            "predicate": {
                "buildDefinition": {
                    "buildType": "https://actions.github.io/buildtypes/workflow/v1",
                    "externalParameters": {"workflow": {
                        "ref": "refs/heads/main",
                        "repository": "https://github.com/danielraffel/skia-builder",
                        "path": ".github/workflows/build-skia.yml"}},
                    "internalParameters": {"github": {"event_name": "workflow_dispatch",
                                                      "repository_id": "1247180457",
                                                      "repository_owner_id": "25807",
                                                      "runner_environment": "github-hosted"}},
                    "resolvedDependencies": [{
                        "uri": "git+https://github.com/danielraffel/skia-builder@refs/heads/main",
                        "digest": {"gitCommit": self.builder}}]},
                "runDetails": {"builder": {"id": verifier.BUILDER_SIGNER_URI},
                               "metadata": {"invocationId": INVOCATION}}},
        }
        if self.statement_edit:
            self.statement_edit(statement)
        return statement

    def write_records(self) -> None:
        self.manifest_path = self.content_addressed(
            "legal-manifest", (json.dumps(self.manifest, sort_keys=True, indent=1) + "\n").encode())
        self.receipt_path = self.content_addressed(
            "apple-toolchain", (json.dumps(self.receipt, sort_keys=True, indent=1) + "\n").encode())
        payload = base64.b64encode(json.dumps(self.statement()).encode()).decode()
        bundle = json.dumps({"mediaType": "application/vnd.dev.sigstore.bundle.v0.3+json",
                             "dsseEnvelope": {"payloadType": "application/vnd.in-toto+json",
                                              "payload": payload, "signatures": []}}).encode()
        self.bundle_path = self.content_addressed("provenance-attestation", bundle, ".sigstore.json")

    # -- the lock, the record and the release -----------------------------
    def eligible_lock(self) -> dict:
        lock = copy.deepcopy(self.lock)
        renderer = lock["runtime_toolchain_dependencies"]["renderer"]
        lock["status"] = verifier.LOCK_STATUS_ELIGIBLE
        renderer["release_eligibility"] = "eligible"
        renderer["release_blockers"] = []
        renderer["artifact"]["sha256"] = self.archive_sha
        renderer["archives"] = [{"name": Path(name).name, "sha256": sha256(data)}
                                for name, data in self.files.items() if name.endswith(".a")]
        headers = sorted(name for name in self.files if name.startswith("build/include/"))
        digest = hashlib.sha256()
        for name in headers:
            digest.update(f"{sha256(self.files[name])}  ./{name[len('build/include/'):]}\n".encode())
        renderer["headers"].update(file_count=len(headers), construction_specific_sha256=digest.hexdigest())
        renderer["bundled_components_completeness"] = {
            "claim": "exhaustive", "legal_manifest_sha256": sha256(self.manifest_path.read_bytes())}
        return lock

    def build(self, mutate=None, record_edit=None) -> tuple[Path, Path]:
        """Write the records, an eligible lock and the derived record."""
        self.write_records()
        lock = self.eligible_lock()
        if mutate:
            mutate(lock)
        renderer = lock["runtime_toolchain_dependencies"]["renderer"]
        record = verifier.build_record(renderer, legal_manifest=self.manifest_path,
                                       toolchain_receipt=self.receipt_path,
                                       provenance_bundle=self.bundle_path)
        if record_edit:
            record_edit(record)
        lock_path, record_path = self.tmp / "lock.json", self.tmp / "attestation.json"
        lock_path.write_text(json.dumps(lock))
        record_path.write_text(json.dumps(record))
        artifact = record["artifact"]
        self.release = self.tmp / "release.json"
        self.release.write_text(json.dumps({"tag_name": artifact["release_tag"], "assets": [
            {"name": path.name, "digest": f"sha256:{sha256(path.read_bytes())}"}
            for path in (self.archive, self.manifest_path, self.receipt_path, self.bundle_path)]}))
        return lock_path, record_path

    def prove(self, lock_path: Path, record_path: Path) -> dict:
        _, renderer = verifier.load_lock(lock_path, record_path)
        with unittest.mock.patch.object(verifier.sys, "platform", "linux"):
            return verifier.prove_builder_records(
                renderer, renderer["_attestation"], archive=self.archive,
                legal_manifest=self.manifest_path, toolchain_receipt=self.receipt_path,
                provenance_bundle=self.bundle_path, release_json=self.release, runner=self.gh)


def verifier_feature(key: str) -> str:
    return {"graphite": "skia_enable_graphite", "dawn": "skia_use_dawn",
            "metal": "skia_use_metal"}.get(key, key)


class BuilderAttestedRendererTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="vellum-renderer-eligible-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.fx = Fixture(self.tmp)

    def refused(self, pattern: str, mutate=None, record_edit=None) -> None:
        lock_path, record_path = self.fx.build(mutate, record_edit)
        with self.assertRaisesRegex(VerificationError, pattern):
            self.fx.prove(lock_path, record_path)

    # -- the positive control ------------------------------------------------
    def test_builder_records_make_the_renderer_eligible(self) -> None:
        lock_path, record_path = self.fx.build()
        result = self.fx.prove(lock_path, record_path)
        self.assertEqual(result["build_provenance_verified"],
                         ["archive", "legal manifest", "toolchain receipt"])
        self.assertEqual(result["build_provenance_run"], INVOCATION)
        self.assertEqual(result["release_assets_verified"], 4)
        self.assertEqual(result["archives_verified"], 2)
        record = json.loads(record_path.read_text())
        self.assertEqual(record["state"], "builder-attested")
        self.assertNotIn("signature", record)
        self.assertEqual(record["build_provenance"]["run"], {"run_id": RUN_ID, "run_attempt": RUN_ATTEMPT})
        self.assertEqual(record["build_identity"]["cpp_abi"],
                         {"stdlib": "libc++", "abi": "1", "libcpp_version": "190102"})
        # Real Sigstore is asked to pin every binding, once per subject.
        self.assertEqual(len(self.fx.gh.calls), 3)
        for args, path in zip(self.fx.gh.calls, (self.fx.archive, self.fx.manifest_path,
                                                 self.fx.receipt_path)):
            self.assertEqual(args, [
                "gh", "attestation", "verify", str(path), "--bundle", str(self.fx.bundle_path),
                "--repo", "danielraffel/skia-builder",
                "--signer-workflow", "danielraffel/skia-builder/.github/workflows/build-skia.yml@refs/heads/main",
                "--source-ref", "refs/heads/main", "--source-digest", self.fx.builder,
                "--predicate-type", "https://slsa.dev/provenance/v1",
                "--cert-oidc-issuer", "https://token.actions.githubusercontent.com",
                "--deny-self-hosted-runners", "--format", "json"])

    # -- (a) build provenance: digest -----------------------------------------
    def test_archive_other_than_the_attested_one_is_refused(self) -> None:
        lock_path, record_path = self.fx.build()
        self.fx.archive.write_bytes(self.fx.archive.read_bytes() + b"\0")
        with self.assertRaisesRegex(VerificationError, "artifact SHA-256 mismatch"):
            self.fx.prove(lock_path, record_path)

    def test_statement_naming_another_archive_digest_is_refused(self) -> None:
        def edit(statement):
            statement["subject"][0]["digest"]["sha256"] = "0" * 64
        self.fx.statement_edit = edit
        self.refused("does not name the archive")

    def test_statement_missing_a_subject_is_refused(self) -> None:
        self.fx.statement_edit = lambda s: s.update(subject=s["subject"][:2])
        self.refused("does not name the legal manifest")

    def test_statement_naming_a_subject_twice_with_two_digests_is_refused(self) -> None:
        def edit(statement):
            statement["subject"].append({"name": statement["subject"][0]["name"],
                                         "digest": {"sha256": "1" * 64}})
        self.fx.statement_edit = edit
        self.refused("does not name the archive")

    def test_bundle_other_than_the_recorded_one_is_refused(self) -> None:
        lock_path, record_path = self.fx.build()
        self.fx.bundle_path.write_bytes(self.fx.bundle_path.read_bytes() + b" ")
        with self.assertRaisesRegex(VerificationError, "bundle SHA-256 differs"):
            self.fx.prove(lock_path, record_path)

    def test_sigstore_refusing_a_subject_is_refused(self) -> None:
        self.fx.gh.returncode = 1
        self.refused("does not cover the archive: .*Error: verifying")

    def test_sigstore_verifying_another_statement_is_refused(self) -> None:
        self.fx.gh.statement_edit = lambda s: s.update(predicateType="https://slsa.dev/provenance/v0.2")
        self.refused("is not the bundle's statement")

    def test_sigstore_reporting_more_than_one_attestation_is_refused(self) -> None:
        self.fx.gh.results = 2
        self.refused("exactly one attestation")

    # -- (a) build provenance: repository -------------------------------------
    def test_statement_from_another_repository_is_refused(self) -> None:
        def edit(statement):
            statement["predicate"]["buildDefinition"]["externalParameters"]["workflow"][
                "repository"] = "https://github.com/someone/skia-builder"
        self.fx.statement_edit = edit
        self.refused("build provenance names workflow")

    def test_certificate_from_another_repository_is_refused(self) -> None:
        self.fx.gh.certificate["sourceRepositoryURI"] = "https://github.com/someone/skia-builder"
        self.refused("certificate sourceRepositoryURI")

    def test_record_naming_another_repository_is_refused(self) -> None:
        self.refused("pinned builder repository",
                     record_edit=lambda r: r["build_provenance"].update(repository="someone/skia-builder"))

    def test_artifact_from_another_repository_is_refused(self) -> None:
        def mutate(lock):
            artifact = lock["runtime_toolchain_dependencies"]["renderer"]["artifact"]
            artifact["repository"] = "https://github.com/someone/skia-builder"
            artifact["url"] = verifier.release_asset_url(artifact, artifact["name"])
        lock_path, record_path = self.fx.build(mutate)
        with self.assertRaisesRegex(VerificationError, "not published by the pinned builder"):
            self.fx.prove(lock_path, record_path)

    # -- (a) build provenance: workflow ----------------------------------------
    def test_statement_from_another_workflow_is_refused(self) -> None:
        def edit(statement):
            statement["predicate"]["buildDefinition"]["externalParameters"]["workflow"][
                "path"] = ".github/workflows/rebind-provenance.yml"
        self.fx.statement_edit = edit
        self.refused("build provenance names workflow")

    def test_certificate_from_another_workflow_is_refused(self) -> None:
        self.fx.gh.certificate["buildSignerURI"] = (
            "https://github.com/danielraffel/skia-builder/.github/workflows/rebind-provenance.yml"
            "@refs/heads/main")
        self.refused("certificate buildSignerURI")

    def test_statement_from_another_builder_id_is_refused(self) -> None:
        def edit(statement):
            statement["predicate"]["runDetails"]["builder"]["id"] = "https://example.test/builder"
        self.fx.statement_edit = edit
        self.refused("names another builder")

    def test_record_naming_another_workflow_is_refused(self) -> None:
        self.refused("pinned builder repository, workflow", record_edit=lambda r: r["build_provenance"].update(
            signer_workflow="danielraffel/skia-builder/.github/workflows/rebind-provenance.yml"))

    def test_build_type_other_than_a_workflow_build_is_refused(self) -> None:
        def edit(statement):
            statement["predicate"]["buildDefinition"]["buildType"] = "https://example.test/local"
        self.fx.statement_edit = edit
        self.refused("not produced by a GitHub Actions workflow build")

    # -- (a) build provenance: ref ---------------------------------------------
    def test_statement_from_a_branch_is_refused(self) -> None:
        def edit(statement):
            statement["predicate"]["buildDefinition"]["externalParameters"]["workflow"][
                "ref"] = "refs/heads/feature"
        self.fx.statement_edit = edit
        self.refused("build provenance names workflow")

    def test_certificate_from_a_branch_is_refused(self) -> None:
        self.fx.gh.certificate["sourceRepositoryRef"] = "refs/heads/feature"
        self.refused("certificate sourceRepositoryRef")

    def test_certificate_workflow_ref_from_a_branch_is_refused(self) -> None:
        self.fx.gh.certificate["githubWorkflowRef"] = "refs/heads/feature"
        self.refused("certificate githubWorkflowRef")

    def test_record_naming_another_ref_is_refused(self) -> None:
        self.refused("pinned builder repository, workflow, ref",
                     record_edit=lambda r: r["build_provenance"].update(source_ref="refs/heads/feature"))

    # -- (a) build provenance: commit, predicate, issuer, runner, run ----------
    def test_statement_built_from_another_commit_is_refused(self) -> None:
        def edit(statement):
            statement["predicate"]["buildDefinition"]["resolvedDependencies"][0]["digest"][
                "gitCommit"] = "1" * 40
        self.fx.statement_edit = edit
        self.refused("not builder commit")

    def test_certificate_from_another_commit_is_refused(self) -> None:
        self.fx.gh.certificate["sourceRepositoryDigest"] = "1" * 40
        self.refused("certificate sourceRepositoryDigest")

    def test_lock_builder_commit_must_be_its_artifact_builder_commit(self) -> None:
        def mutate(lock):
            lock["runtime_toolchain_dependencies"]["renderer"]["artifact"]["builder_commit"] = "1" * 40
        lock_path, record_path = self.fx.build(mutate)
        with self.assertRaisesRegex(VerificationError, "builder_commit differs"):
            self.fx.prove(lock_path, record_path)

    def test_statement_with_another_predicate_is_refused(self) -> None:
        self.fx.statement_edit = lambda s: s.update(predicateType="https://slsa.dev/provenance/v0.2")
        self.refused("not SLSA v1 provenance")

    def test_record_naming_another_predicate_is_refused(self) -> None:
        self.refused("predicate", record_edit=lambda r: r["build_provenance"].update(
            predicate_type="https://example.test/predicate"))

    def test_certificate_from_a_recreated_repository_is_refused(self) -> None:
        self.fx.gh.certificate["sourceRepositoryIdentifier"] = "1"
        self.refused("certificate sourceRepositoryIdentifier")

    def test_statement_from_a_recreated_repository_is_refused(self) -> None:
        def edit(statement):
            statement["predicate"]["buildDefinition"]["internalParameters"]["github"]["repository_id"] = "1"
        self.fx.statement_edit = edit
        self.refused("another repository or owner id")

    def test_receipt_abi_row_without_its_standard_library_is_refused(self) -> None:
        del self.fx.receipt["cpp_abi"]["x86_64"]["stdlib"]
        self.refused("cpp_abi differs from the toolchain receipt for x86_64")

    def test_certificate_from_another_issuer_is_refused(self) -> None:
        self.fx.gh.certificate["issuer"] = "https://example.test/oidc"
        self.refused("certificate issuer")

    def test_self_hosted_runner_build_is_refused(self) -> None:
        def edit(statement):
            statement["predicate"]["buildDefinition"]["internalParameters"]["github"][
                "runner_environment"] = "self-hosted"
        self.fx.statement_edit = edit
        self.refused("not produced on a GitHub-hosted runner")

    def test_self_hosted_runner_certificate_is_refused(self) -> None:
        self.fx.gh.certificate["runnerEnvironment"] = "self-hosted"
        self.refused("certificate runnerEnvironment")

    def test_record_naming_another_run_is_refused(self) -> None:
        self.refused("not the record's", record_edit=lambda r: r["build_provenance"].update(
            run={"run_id": RUN_ID, "run_attempt": 2}))

    def test_certificate_from_another_run_is_refused(self) -> None:
        self.fx.gh.certificate["runInvocationURI"] = INVOCATION.replace("attempts/1", "attempts/2")
        self.refused("certificate runInvocationURI")

    def test_statement_from_another_run_is_refused(self) -> None:
        def edit(statement):
            statement["predicate"]["runDetails"]["metadata"]["invocationId"] = INVOCATION.replace(
                "attempts/1", "attempts/2")
        self.fx.statement_edit = edit
        with self.assertRaisesRegex(VerificationError, "names run .* not the record's"):
            lock_path, record_path = self.fx.build(record_edit=lambda r: r["build_provenance"].update(
                run={"run_id": RUN_ID, "run_attempt": RUN_ATTEMPT}))
            self.fx.prove(lock_path, record_path)

    def test_bundle_without_an_in_toto_statement_is_refused(self) -> None:
        self.fx.write_records()
        data = json.loads(self.fx.bundle_path.read_bytes())
        data["dsseEnvelope"]["payloadType"] = "text/plain"
        with self.assertRaisesRegex(VerificationError, "does not carry an in-toto statement"):
            verifier.bundle_statement(json.dumps(data).encode())

    def test_gh_is_required(self) -> None:
        lock_path, record_path = self.fx.build()
        _, renderer = verifier.load_lock(lock_path, record_path)
        with unittest.mock.patch.object(verifier.shutil, "which", return_value=None):
            with self.assertRaisesRegex(VerificationError, "gh is required"):
                verifier.verify_build_provenance(
                    renderer["_attestation"], {"archive": self.fx.archive,
                                               "legal manifest": self.fx.manifest_path,
                                               "toolchain receipt": self.fx.receipt_path},
                    self.fx.bundle_path)

    def test_every_subject_is_required(self) -> None:
        lock_path, record_path = self.fx.build()
        _, renderer = verifier.load_lock(lock_path, record_path)
        with self.assertRaisesRegex(VerificationError, "must cover the archive, legal manifest"):
            verifier.verify_build_provenance(
                renderer["_attestation"], {"legal manifest": self.fx.manifest_path,
                                           "toolchain receipt": self.fx.receipt_path},
                self.fx.bundle_path, runner=self.fx.gh)

    # -- (b) the legal manifest -------------------------------------------------
    def test_manifest_missing_a_zip_member_is_refused(self) -> None:
        self.fx.manifest["zip_members"] = [row for row in self.fx.manifest["zip_members"]
                                           if row["name"] != "build/include/core.h"]
        self.refused("zip-member inventory differs from the archive")

    def test_manifest_missing_a_static_archive_member_is_refused(self) -> None:
        self.fx.archive_members["build/mac-gpu/lib/Release/libskia.a"] = bsd_ar(
            [("a.o", OBJECT), ("hidden.o", b"unlisted object")])
        self.fx.write_archive()
        manifest = self.fx.legal_manifest()
        slices = manifest["static_archives"]["build/mac-gpu/lib/Release/libskia.a"]["slices"]["arm64"]
        slices["members"] = slices["members"][:1]
        self.fx.manifest = manifest
        self.fx.receipt = self.fx.toolchain_receipt()
        self.refused("does not cover every member")

    def test_manifest_missing_a_locked_archive_is_refused(self) -> None:
        del self.fx.manifest["static_archives"]["build/mac-gpu/lib/Release/libdawn_combined.a"]
        self.refused("static archives differ from the lock's archives")

    def test_manifest_member_without_a_license_is_refused(self) -> None:
        self.fx.manifest["zip_members"][0]["licenses"] = []
        self.refused("without a license")

    def test_manifest_object_without_a_license_is_refused(self) -> None:
        members = self.fx.manifest["static_archives"]["build/mac-gpu/lib/Release/libskia.a"][
            "slices"]["arm64"]["members"]
        members[0]["licenses"] = ["source:NOT-LISTED"]
        self.refused("unresolved")

    def test_manifest_license_text_not_matching_its_digest_is_refused(self) -> None:
        self.fx.manifest["licenses"]["source:LICENSE"]["text"] = "edited"
        self.refused("no text matching its digest")

    def test_non_exhaustive_manifest_is_refused(self) -> None:
        self.fx.manifest["exhaustive"] = False
        self.refused("does not claim an exhaustive inventory")

    def test_manifest_with_gaps_is_refused(self) -> None:
        self.fx.manifest["gaps"] = [{"path": "obj/x.o"}]
        self.refused("does not claim an exhaustive inventory")

    def test_manifest_for_another_archive_is_refused(self) -> None:
        self.fx.manifest["archive"]["sha256"] = "0" * 64
        self.refused("describes a different archive")

    def test_manifest_built_from_another_skia_is_refused(self) -> None:
        self.fx.manifest["source_revisions"]["skia"]["commit"] = "1" * 40
        self.refused("skia revision differs from the lock")

    def test_manifest_built_from_another_dawn_is_refused(self) -> None:
        self.fx.manifest["source_revisions"]["dawn"]["expected"] = "1" * 40
        self.refused("dawn revision differs from the lock")

    def test_manifest_symbol_table_differing_from_the_archive_is_refused(self) -> None:
        self.fx.manifest["static_archives"]["build/mac-gpu/lib/Release/libskia.a"]["slices"]["arm64"][
            "symbol_tables"] = ["9" * 64]
        self.refused("symbol tables .* differ from the archive")

    def test_manifest_listing_a_zip_member_twice_is_refused(self) -> None:
        self.fx.manifest["zip_members"].append(dict(self.fx.manifest["zip_members"][0]))
        self.refused("lists a zip member more than once")

    def test_lock_naming_another_manifest_is_refused(self) -> None:
        def mutate(lock):
            lock["runtime_toolchain_dependencies"]["renderer"]["bundled_components_completeness"][
                "legal_manifest_sha256"] = "0" * 64
        with self.assertRaisesRegex(VerificationError, "names a different legal manifest"):
            self.fx.build(mutate)
            verifier.load_lock(self.tmp / "lock.json", self.tmp / "attestation.json")

    def test_manifest_asset_name_must_carry_its_digest(self) -> None:
        def edit(record):
            record["legal_inventory"]["manifest"]["name"] = f"{STEM}.legal-manifest.json"
        lock_path, record_path = self.fx.build(record_edit=edit)
        with self.assertRaisesRegex(VerificationError, "must carry its own SHA-256"):
            verifier.load_lock(lock_path, record_path)

    # -- (c) the toolchain receipt ----------------------------------------------
    def test_identity_differing_from_the_receipt_is_refused(self) -> None:
        self.refused("compiler.version differs from the toolchain receipt",
                     record_edit=lambda r: r["build_identity"]["compiler"].update(
                         version="Apple clang version 16.0.0 (clang-1600.0.26.6)"))

    def test_receipt_with_another_sdk_is_refused(self) -> None:
        self.fx.receipt["observed_macho"]["sdk_versions"] = ["15.5", "26.0"]
        self.refused("not all built against the attested SDK")

    def test_receipt_with_another_abi_is_refused(self) -> None:
        self.fx.receipt["cpp_abi"]["x86_64"]["libcpp_abi_version"] = "2"
        self.refused("cpp_abi differs from the toolchain receipt for x86_64")

    def test_receipt_without_the_locked_architecture_is_refused(self) -> None:
        self.fx.receipt["target"]["architectures"] = ["x86_64"]
        del self.fx.receipt["cpp_abi"]["arm64"]
        self.refused("did not build macos-arm64", record_edit=lambda r: r["build_identity"].update(
            cpp_abi={"stdlib": "libc++", "abi": "1", "libcpp_version": "190102"}))

    def test_receipt_abi_rows_must_be_its_architectures(self) -> None:
        self.fx.receipt["target"]["architectures"] = ["arm64"]
        self.refused("C\\+\\+ ABI rows differ from its target architectures")

    def test_receipt_for_another_configuration_is_refused(self) -> None:
        self.fx.receipt["target"]["configuration"] = "Debug"
        self.refused("another platform or configuration")

    def test_receipt_with_another_minimum_os_than_the_lock_is_refused(self) -> None:
        self.fx.receipt["target"]["minimum_os"] = "14.0"
        self.refused("Mach-O SDK or minimum OS differs from the receipt")

    def test_receipt_for_another_archive_is_refused(self) -> None:
        self.fx.receipt["archive"]["sha256"] = "0" * 64
        self.refused("receipt describes a different archive")

    def test_free_text_compiler_version_is_refused(self) -> None:
        self.refused("is not a measured value", record_edit=lambda r: r["build_identity"]["compiler"].update(
            version="some clang"))

    def test_pending_build_identity_is_refused(self) -> None:
        self.refused("is not attested", record_edit=lambda r: r["build_identity"]["linker"].update(
            vendor="pending-builder-attestation"))

    # -- GitHub's own release digests -------------------------------------------
    def test_release_asset_digest_mismatch_is_refused(self) -> None:
        lock_path, record_path = self.fx.build()
        release = json.loads(self.fx.release.read_text())
        release["assets"][1]["digest"] = "sha256:" + "0" * 64
        self.fx.release.write_text(json.dumps(release))
        with self.assertRaisesRegex(VerificationError, "release asset .* differs"):
            self.fx.prove(lock_path, record_path)

    def test_release_missing_or_duplicating_an_asset_is_refused(self) -> None:
        for edit in (lambda assets: assets.pop(3), lambda assets: assets.append(dict(assets[2]))):
            with self.subTest(edit=edit):
                lock_path, record_path = self.fx.build()
                release = json.loads(self.fx.release.read_text())
                edit(release["assets"])
                self.fx.release.write_text(json.dumps(release))
                with self.assertRaisesRegex(VerificationError, "missing or duplicated"):
                    self.fx.prove(lock_path, record_path)

    def test_release_json_for_another_tag_is_refused(self) -> None:
        lock_path, record_path = self.fx.build()
        self.fx.release.write_text(json.dumps({"tag_name": "chrome/m1", "assets": []}))
        with self.assertRaisesRegex(VerificationError, "not the locked release tag"):
            self.fx.prove(lock_path, record_path)

    # -- the record and the lock ---------------------------------------------------
    def test_retired_owner_signature_block_is_refused(self) -> None:
        lock_path, record_path = self.fx.build(record_edit=lambda r: r.update(
            signature={"state": "signed", "issuer": "owner@example.test"}))
        with self.assertRaisesRegex(VerificationError, "unexpected fields: signature"):
            verifier.load_lock(lock_path, record_path)

    def test_previous_record_schema_is_refused(self) -> None:
        lock_path, record_path = self.fx.build(record_edit=lambda r: r.update(
            schema="vellum.renderer-provenance.v1", state="owner-attested"))
        with self.assertRaisesRegex(VerificationError, "schema is missing or unsupported"):
            verifier.load_lock(lock_path, record_path)

    def test_reserved_underscore_keys_are_refused(self) -> None:
        lock_path, record_path = self.fx.build(record_edit=lambda r: r["build_identity"].update(_note="x"))
        with self.assertRaisesRegex(VerificationError, "uses reserved keys"):
            verifier.load_lock(lock_path, record_path)

    def test_eligible_lock_with_a_pending_record_is_refused(self) -> None:
        lock_path, record_path = self.fx.build(record_edit=lambda r: r.update(state="pending-build-provenance"))
        with self.assertRaisesRegex(VerificationError, "requires a builder-attested provenance record"):
            verifier.load_lock(lock_path, record_path)

    def test_eligible_lock_with_open_blockers_is_refused(self) -> None:
        def mutate(lock):
            lock["runtime_toolchain_dependencies"]["renderer"]["release_blockers"] = [{"id": "x"}]
        with self.assertRaisesRegex(VerificationError, "no open release blockers"):
            self.fx.build(mutate)
            verifier.load_lock(self.tmp / "lock.json", self.tmp / "attestation.json")

    def test_eligible_lock_must_keep_its_status_in_step(self) -> None:
        def mutate(lock):
            lock["status"] = verifier.LOCK_STATUS_BLOCKED
        with self.assertRaisesRegex(VerificationError, "status does not match"):
            self.fx.build(mutate)
            verifier.load_lock(self.tmp / "lock.json", self.tmp / "attestation.json")

    def test_attested_record_must_carry_an_exhaustive_inventory(self) -> None:
        lock_path, record_path = self.fx.build(record_edit=lambda r: r["legal_inventory"].update(
            state="observed-not-exhaustive"))
        with self.assertRaisesRegex(VerificationError, "must carry an exhaustive legal inventory"):
            verifier.load_lock(lock_path, record_path)

    def test_record_for_another_artifact_is_refused(self) -> None:
        lock_path, record_path = self.fx.build(record_edit=lambda r: r["artifact"].update(sha256="0" * 64))
        with self.assertRaisesRegex(VerificationError, "no matching provenance record"):
            verifier.load_lock(lock_path, record_path)

    def test_record_naming_another_source_commit_is_refused(self) -> None:
        lock_path, record_path = self.fx.build(
            record_edit=lambda r: r["source_identities"]["skia"].update(commit="1" * 40))
        with self.assertRaisesRegex(VerificationError, "commit differs from the lock: skia"):
            verifier.load_lock(lock_path, record_path)

    # -- the CLI ------------------------------------------------------------------
    def run_cli(self, *argv: str) -> subprocess.CompletedProcess:
        stdout, stderr = io.StringIO(), io.StringIO()
        with unittest.mock.patch.object(sys, "argv", ["verify_renderer_dependency.py", *argv]), \
                unittest.mock.patch.object(verifier, "run_gh", self.fx.gh), \
                unittest.mock.patch.object(verifier.sys, "platform", "linux"), \
                contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout):
            code = verifier.main()
        return subprocess.CompletedProcess(argv, code, stdout.getvalue(), stderr.getvalue())

    def proof_flags(self) -> list[str]:
        return ["--archive", str(self.fx.archive), "--legal-manifest", str(self.fx.manifest_path),
                "--toolchain-receipt", str(self.fx.receipt_path),
                "--provenance-bundle", str(self.fx.bundle_path), "--release-json", str(self.fx.release)]

    def test_cli_proves_an_eligible_lock(self) -> None:
        lock_path, record_path = self.fx.build()
        completed = self.run_cli("--lock", str(lock_path), "--attestation", str(record_path),
                                 "--require-release-eligible", "--json", *self.proof_flags())
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(json.loads(completed.stdout)["builder_records_proven"])

    def test_cli_eligible_lock_still_requires_every_proof(self) -> None:
        lock_path, record_path = self.fx.build()
        flags = self.proof_flags()
        for index in range(0, len(flags), 2):
            with self.subTest(dropped=flags[index]):
                completed = self.run_cli("--lock", str(lock_path), "--attestation", str(record_path),
                                         "--require-release-eligible",
                                         *(flags[:index] + flags[index + 2:]))
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn(f"release eligibility requires {flags[index]}", completed.stderr)

    def test_cli_without_the_flag_does_not_claim_eligibility(self) -> None:
        lock_path, record_path = self.fx.build()
        completed = self.run_cli("--lock", str(lock_path), "--attestation", str(record_path), "--json")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertFalse(json.loads(completed.stdout)["builder_records_proven"])

    def test_cli_bundle_alone_cannot_be_checked(self) -> None:
        lock_path, record_path = self.fx.build()
        completed = self.run_cli("--lock", str(lock_path), "--attestation", str(record_path),
                                 "--provenance-bundle", str(self.fx.bundle_path))
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("needs the archive, legal manifest, toolchain receipt", completed.stderr)

    def test_cli_emits_the_record_it_proves(self) -> None:
        lock_path, record_path = self.fx.build()
        out = self.tmp / "emitted.json"
        completed = self.run_cli("--lock", str(lock_path), "--emit-attestation", str(out),
                                 *self.proof_flags())
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(out.read_text()), json.loads(record_path.read_text()))

    def test_cli_does_not_emit_a_record_that_fails_its_proof(self) -> None:
        self.fx.gh.certificate["sourceRepositoryRef"] = "refs/heads/feature"
        lock_path, _ = self.fx.build()
        out = self.tmp / "emitted.json"
        completed = self.run_cli("--lock", str(lock_path), "--emit-attestation", str(out),
                                 *self.proof_flags())
        self.assertNotEqual(completed.returncode, 0)
        self.assertFalse(out.exists())

    def test_cli_cannot_emit_and_claim_eligibility_at_once(self) -> None:
        lock_path, _ = self.fx.build()
        with self.assertRaises(SystemExit) as raised:
            self.run_cli("--lock", str(lock_path), "--emit-attestation", str(self.tmp / "out.json"),
                         "--require-release-eligible", *self.proof_flags())
        self.assertEqual(raised.exception.code, 2)
        self.assertFalse((self.tmp / "out.json").exists())

    def test_script_refuses_to_run_where_a_sibling_could_shadow_the_standard_library(self) -> None:
        # A package beside the script named like a standard-library module.
        copy = self.tmp / "tools" / "provenance"
        copy.mkdir(parents=True)
        shutil.copy(Path(verifier.__file__), copy / "verify_renderer_dependency.py")
        marker = self.tmp / "shadow-ran"
        (copy / "subprocess").mkdir()
        (copy / "subprocess" / "__init__.py").write_text(
            f"open({str(marker)!r}, 'w').write('ran')\nraise SystemExit(0)\n")
        unisolated = subprocess.run([sys.executable, str(copy / "verify_renderer_dependency.py")],
                                    capture_output=True, text=True)
        self.assertNotEqual(unisolated.returncode, 0)
        self.assertIn("run with python3 -I", unisolated.stderr)
        self.assertFalse(marker.exists())
        isolated = subprocess.run([sys.executable, "-I", str(copy / "verify_renderer_dependency.py"),
                                   "--help"], capture_output=True, text=True)
        self.assertEqual(isolated.returncode, 0, isolated.stderr)
        self.assertFalse(marker.exists())

    def test_shipped_lock_matches_its_declared_eligibility(self) -> None:
        _, renderer = verifier.load_lock(ROOT / "provenance/third-party-lock.json")
        attestation = renderer["_attestation"]
        if renderer["release_eligibility"] == "blocked":
            self.assertIn(attestation["state"], {"pending-build-provenance", "builder-attested"})
        else:
            self.assertEqual(attestation["state"], "builder-attested")


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
class RealSigstoreTests(unittest.TestCase):
    """Real `gh attestation verify` against skia-builder's published records.

    M158_BUNDLE and M158_RECEIPT are the chrome/m158 mac-universal build
    provenance bundle and toolchain receipt (build-skia.yml on main, run
    40626447857 attempt 1, builder commit 312f05e2). The receipt is one of the
    bundle's subjects, so the production verify path can be exercised against
    real Sigstore without the archive. PROBE_* is an older branch-dispatched
    build, kept as a real bundle that main's pin must refuse.
    """

    M158_BUNDLE = TESTDATA / ("skia-build-mac-universal-gpu-release.provenance-attestation.sha256-"
                              "b781f48d57c6157850db0efe072a872eb555fb352c669950ed2e186625967884.sigstore.json")
    M158_RECEIPT = TESTDATA / ("skia-build-mac-universal-gpu-release.apple-toolchain.sha256-"
                               "101a51466c7f09f2fd8ea6c6edca6c912d13712eba85e1c6e2606607312cbb6e.json")
    M158_COMMIT = "312f05e24a5d5a45d4a3e70ca70ff3ccff1b4d97"
    M158_INVOCATION = "https://github.com/danielraffel/skia-builder/actions/runs/40626447857/attempts/1"
    PROBE_BUNDLE = TESTDATA / "skia-builder-probe-provenance.sigstore.json"
    PROBE_RECEIPT = TESTDATA / "skia-builder-probe-apple-toolchain.json"
    PROBE_COMMIT = "0091d97f17ae716f2fd5968e37a903224cd32b49"

    def gh(self, args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run([shutil.which("gh"), *args[1:]], capture_output=True, text=True)

    def verify(self, subject: Path, bundle: Path, commit: str, invocation: str) -> None:
        verifier.verify_subject_with_sigstore("toolchain receipt", subject, bundle,
                                              verifier.bundle_statement(bundle.read_bytes()),
                                              commit, invocation, self.gh)

    def test_gh_can_verify_bundles(self) -> None:
        self.assertTrue(gh_can_verify(), "gh cannot verify Sigstore bundles on this runner")

    def test_m158_fixtures_are_the_published_assets(self) -> None:
        for path in (self.M158_BUNDLE, self.M158_RECEIPT):
            self.assertIn(sha256(path.read_bytes()), path.name)

    def test_m158_bundle_verifies_under_the_production_pins(self) -> None:
        # The positive control every refusal below is measured against.
        self.verify(self.M158_RECEIPT, self.M158_BUNDLE, self.M158_COMMIT, self.M158_INVOCATION)

    def test_m158_statement_binds_the_production_pins(self) -> None:
        statement = verifier.bundle_statement(self.M158_BUNDLE.read_bytes())
        receipt = json.loads(self.M158_RECEIPT.read_bytes())
        archive = receipt["archive"]
        manifest = next(row for row in statement["subject"] if ".legal-manifest." in row["name"]
                        and row["name"].startswith(STEM + "."))
        verifier.verify_statement(statement, {
            "archive": (archive["name"], archive["sha256"]),
            "legal manifest": (manifest["name"], manifest["digest"]["sha256"]),
            "toolchain receipt": (self.M158_RECEIPT.name, sha256(self.M158_RECEIPT.read_bytes()))},
            self.M158_COMMIT, self.M158_INVOCATION)

    def test_other_bytes_are_refused_by_sigstore(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        edited = tmp / self.M158_RECEIPT.name
        edited.write_bytes(self.M158_RECEIPT.read_bytes() + b" ")
        with self.assertRaisesRegex(VerificationError, "does not cover the toolchain receipt: .*rror"):
            self.verify(edited, self.M158_BUNDLE, self.M158_COMMIT, self.M158_INVOCATION)

    def test_another_builder_commit_is_refused_by_sigstore(self) -> None:
        with self.assertRaisesRegex(VerificationError, "does not cover the toolchain receipt: .*rror"):
            self.verify(self.M158_RECEIPT, self.M158_BUNDLE, "1" * 40, self.M158_INVOCATION)

    def test_another_repository_is_refused_by_sigstore(self) -> None:
        with unittest.mock.patch.object(verifier, "BUILDER_REPOSITORY", "danielraffel/v8-builder"):
            with self.assertRaisesRegex(VerificationError, "does not cover the toolchain receipt: .*rror"):
                self.verify(self.M158_RECEIPT, self.M158_BUNDLE, self.M158_COMMIT, self.M158_INVOCATION)

    def test_another_workflow_is_refused_by_sigstore(self) -> None:
        with unittest.mock.patch.object(
                verifier, "BUILDER_SIGNER_WORKFLOW",
                "danielraffel/skia-builder/.github/workflows/rebind-provenance.yml"):
            with self.assertRaisesRegex(VerificationError, "does not cover the toolchain receipt: .*rror"):
                self.verify(self.M158_RECEIPT, self.M158_BUNDLE, self.M158_COMMIT, self.M158_INVOCATION)

    def test_another_ref_is_refused_by_sigstore(self) -> None:
        with unittest.mock.patch.object(verifier, "BUILDER_SOURCE_REF", "refs/heads/release"):
            with self.assertRaisesRegex(VerificationError, "does not cover the toolchain receipt: .*rror"):
                self.verify(self.M158_RECEIPT, self.M158_BUNDLE, self.M158_COMMIT, self.M158_INVOCATION)

    def test_another_predicate_is_refused_by_sigstore(self) -> None:
        with unittest.mock.patch.object(verifier, "PREDICATE_TYPE", "https://slsa.dev/provenance/v0.2"):
            with self.assertRaisesRegex(VerificationError, "does not cover the toolchain receipt: .*rror"):
                self.verify(self.M158_RECEIPT, self.M158_BUNDLE, self.M158_COMMIT, self.M158_INVOCATION)

    def test_another_run_is_refused_after_sigstore(self) -> None:
        with self.assertRaisesRegex(VerificationError, "certificate runInvocationURI"):
            self.verify(self.M158_RECEIPT, self.M158_BUNDLE, self.M158_COMMIT,
                        self.M158_INVOCATION.replace("attempts/1", "attempts/2"))

    def test_branch_built_bundle_is_refused_under_the_main_pin(self) -> None:
        with self.assertRaisesRegex(VerificationError, "does not cover the toolchain receipt: .*rror"):
            self.verify(self.PROBE_RECEIPT, self.PROBE_BUNDLE, self.PROBE_COMMIT,
                        "https://github.com/danielraffel/skia-builder/actions/runs/39920192131/attempts/1")


if __name__ == "__main__":
    unittest.main()
