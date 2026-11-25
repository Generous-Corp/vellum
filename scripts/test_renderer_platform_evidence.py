#!/usr/bin/env python3
"""Tests for the non-macOS rows of the renderer lock and their release evidence.

The fixtures are real. `evidence-chrome-m158-attested.2.json` is what
`renderer_platform_evidence.py derive` writes for skia-builder's
chrome/m158-attested.2 release, and `rows-bbb46e7d.json` is the six non-macOS
rows as first drafted, which carry two wrong values: a one-nibble-wrong
windows-arm64 skunicode_icu.lib digest and an android-x86_64 archive_root that
is android-arm64-v8a's. The old checker accepted those rows (it checked syntax
and uniqueness only); every case here must be refused by the evidence check.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_renderer_lock as drift  # noqa: E402
import renderer_platform_evidence as evidence  # noqa: E402

DATA = ROOT / "scripts" / "testdata" / "renderer_platform"
COPIED = (drift.LOCK, *drift.READERS, *drift.RECORDS, *drift.DOCUMENTS, evidence.PLATFORM_FILE,
          evidence.EVIDENCE)
RELEASE = "chrome/m158-attested.2"
KNOWN_BAD = ("android-x86_64 archive_root build/android-gpu/lib/Release repeats android-arm64-v8a",
             "windows-arm64 skunicode_icu.lib digest is 59413d2fa4eddb314ed20529992c8d22c2a503b1d58f4199298ca5d4842d538c",
             "android-x86_64 archive_root is 'build/android-gpu/lib/Release'")


def load(name: str) -> dict:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


class RowsAgainstEvidenceTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for relative in COPIED:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
        self.evidence = load("evidence-chrome-m158-attested.2.json")
        self.rows = load("rows-bbb46e7d.json")["platforms"]
        self.commit = self.evidence["builder"]["commit"]
        self.install()

    # -- helpers ---------------------------------------------------------
    def corrected(self) -> dict:
        rows = copy.deepcopy(self.rows)
        for key, row in rows.items():
            row["release"] = RELEASE
            row["builder_commit"] = self.commit
            record = self.evidence["platforms"][key]
            row["archive_root"] = record["archive_root"]
            for name in row["archives"]:
                row["archives"][name] = record["archives"][name]
        return rows

    def drafted(self) -> dict:
        """The bbb46e7d rows with the two fields the lock now requires added."""
        rows = copy.deepcopy(self.rows)
        for row in rows.values():
            row["release"] = RELEASE
            row["builder_commit"] = self.commit
        return rows

    def install(self, rows: dict | None = None, pinned: dict | None = None) -> None:
        lock_path = self.root / drift.LOCK
        lock = json.loads((ROOT / drift.LOCK).read_text(encoding="utf-8"))
        for key in [k for k in lock["platforms"] if k != drift.PLATFORM]:
            del lock["platforms"][key]
        lock["platforms"].update(self.corrected() if rows is None else rows)
        lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
        target = self.root / evidence.EVIDENCE
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.evidence if pinned is None else pinned, indent=2) + "\n",
                          encoding="utf-8")

    def edit_rows(self, change) -> None:
        rows = self.corrected()
        change(rows)
        self.install(rows)

    def problems(self) -> list[str]:
        return drift.check(self.root)

    def assert_refused(self, fragment: str) -> None:
        problems = self.problems()
        self.assertTrue(any(fragment in problem for problem in problems),
                        f"expected {fragment!r} in {problems}")

    @staticmethod
    def flip(digest: str) -> str:
        return digest[:9] + ("e" if digest[9] != "e" else "f") + digest[10:]

    # -- the known-bad drafted rows --------------------------------------
    def test_the_corrected_rows_agree_with_the_release(self) -> None:
        self.assertEqual(self.problems(), [])

    def test_the_drafted_rows_are_refused_for_exactly_their_defects(self) -> None:
        self.install(self.drafted())
        problems = self.problems()
        for fragment in KNOWN_BAD:
            self.assertTrue(any(fragment in problem for problem in problems),
                            f"{fragment!r} not refused: {problems}")
        self.assertEqual(len(problems), 3, problems)

    # -- digests ---------------------------------------------------------
    def test_a_one_nibble_flip_in_an_archive_digest_is_refused(self) -> None:
        for key, name in (("linux-x64", "libskia.a"), ("windows-x64", "skia.lib"),
                          ("android-arm64-v8a", "libdawn_combined.a")):
            with self.subTest(key=key):
                self.setUp()
                self.edit_rows(lambda rows: rows[key]["archives"].update(
                    {name: self.flip(rows[key]["archives"][name])}))
                self.assert_refused(f"{key} {name} digest is")

    def test_a_one_nibble_flip_in_an_asset_digest_is_refused(self) -> None:
        self.edit_rows(lambda rows: rows["linux-arm64"].update(
            sha256=self.flip(rows["linux-arm64"]["sha256"])))
        self.assert_refused("linux-arm64 asset digest is")

    def test_swapped_asset_digests_are_refused(self) -> None:
        def swap(rows):
            a, b = rows["linux-x64"], rows["linux-arm64"]
            a["sha256"], b["sha256"] = b["sha256"], a["sha256"]
        self.edit_rows(swap)
        self.assert_refused("linux-x64 asset digest is")
        self.assert_refused("linux-arm64 asset digest is")

    def test_swapped_archive_digests_between_windows_rows_are_refused(self) -> None:
        def swap(rows):
            a, b = rows["windows-x64"]["archives"], rows["windows-arm64"]["archives"]
            a["skia.lib"], b["skia.lib"] = b["skia.lib"], a["skia.lib"]
        self.edit_rows(swap)
        self.assert_refused("windows-x64 skia.lib digest is")
        self.assert_refused("windows-arm64 skia.lib digest is")

    def test_swapped_whole_rows_are_refused(self) -> None:
        def swap(rows):
            rows["linux-x64"], rows["linux-arm64"] = rows["linux-arm64"], rows["linux-x64"]
        self.edit_rows(swap)
        self.assert_refused("linux-x64 asset is")
        self.assert_refused("linux-arm64 asset is")

    def test_an_archive_the_release_does_not_hold_is_refused(self) -> None:
        self.edit_rows(lambda rows: rows["linux-x64"]["archives"].update({"libextra.a": "3" * 64}))
        self.assert_refused("linux-x64 locks libextra.a, which the release evidence does not list")

    # -- roots, rows, keys -----------------------------------------------
    def test_a_wrong_archive_root_is_refused(self) -> None:
        self.edit_rows(lambda rows: rows["android-x86_64"].update(
            archive_root=rows["android-arm64-v8a"]["archive_root"]))
        self.assert_refused("android-x86_64 archive_root is")
        self.assert_refused("android-x86_64 archive_root build/android-gpu/lib/Release repeats")

    def test_a_repeated_archive_root_is_refused_even_with_matching_evidence(self) -> None:
        pinned = copy.deepcopy(self.evidence)
        pinned["platforms"]["android-x86_64"]["archive_root"] = \
            pinned["platforms"]["android-arm64-v8a"]["archive_root"]
        rows = self.corrected()
        rows["android-x86_64"]["archive_root"] = rows["android-arm64-v8a"]["archive_root"]
        self.install(rows, pinned)
        self.assert_refused("repeats android-arm64-v8a")

    def test_a_deleted_non_macos_row_is_refused(self) -> None:
        for key in ("linux-x64", "windows-arm64"):
            with self.subTest(key=key):
                self.setUp()
                self.edit_rows(lambda rows: rows.pop(key))
                self.assert_refused(f"{key} has evidence but the lock has no {key} row")

    def test_a_row_without_evidence_is_refused(self) -> None:
        pinned = copy.deepcopy(self.evidence)
        pinned["platforms"].pop("linux-arm64")
        self.install(None, pinned)
        self.assert_refused("linux-arm64 has no evidence")

    def test_rows_without_an_evidence_file_are_refused(self) -> None:
        (self.root / evidence.EVIDENCE).unlink()
        self.assert_refused("so the non-macOS rows")

    def test_an_unknown_platform_key_is_refused(self) -> None:
        def add(rows):
            row = copy.deepcopy(rows["linux-x64"])
            row["asset"] = "skia-build-plan9-mips-gpu-release.zip"
            row["archive_root"] = "build/plan9-gpu/lib/Release"
            row["sha256"] = "7" * 64
            row["archives"] = {name: f"{i:x}" * 64 for i, name in enumerate(row["archives"], 8)}
            rows["plan9-mips"] = row
        self.edit_rows(add)
        self.assert_refused("platform plan9-mips is not one cmake/VellumSkiaDawnPlatform.cmake selects")

    # -- release, builder, configuration ---------------------------------
    def test_a_wrong_release_is_refused(self) -> None:
        for release in ("chrome/m1-attested.9", "chrome/m158", "chrome/m158-attested.3"):
            with self.subTest(release=release):
                self.setUp()
                self.edit_rows(lambda rows: rows["linux-x64"].update(release=release))
                self.assertTrue(self.problems(), release)

    def test_an_all_zero_builder_commit_is_refused(self) -> None:
        self.edit_rows(lambda rows: rows["windows-x64"].update(builder_commit="0" * 40))
        self.assert_refused("windows-x64 builder_commit is")

    def test_a_builder_commit_other_than_the_releases_is_refused(self) -> None:
        self.edit_rows(lambda rows: rows["linux-x64"].update(builder_commit="b" * 40))
        self.assert_refused("linux-x64 builder_commit is")

    def test_a_debug_configuration_is_refused(self) -> None:
        self.edit_rows(lambda rows: rows["linux-arm64"].update(configuration="Debug"))
        self.assert_refused("linux-arm64 configuration is 'Debug'")

    def test_a_row_naming_another_platforms_asset_is_refused(self) -> None:
        def rename(rows):
            rows["linux-x64"]["asset"] = "skia-build-win-x64-gpu-release.zip"
            rows["windows-x64"]["asset"] = "skia-build-linux-x64-gpu-release.zip"
        self.edit_rows(rename)
        self.assert_refused("linux-x64 asset is 'skia-build-win-x64-gpu-release.zip'")

    # -- the evidence file itself ----------------------------------------
    def test_evidence_with_a_wrong_legal_manifest_name_is_refused(self) -> None:
        pinned = copy.deepcopy(self.evidence)
        pinned["platforms"]["linux-x64"]["legal_manifest"]["sha256"] = "4" * 64
        self.install(None, pinned)
        self.assert_refused("linux-x64 legal manifest name does not carry its own digest")

    def test_evidence_for_the_macos_row_is_refused(self) -> None:
        pinned = copy.deepcopy(self.evidence)
        pinned["platforms"][drift.PLATFORM] = copy.deepcopy(pinned["platforms"]["linux-x64"])
        self.install(None, pinned)
        self.assert_refused(f"{drift.PLATFORM} is not a non-macOS platform")

    def test_evidence_for_another_schema_is_refused(self) -> None:
        pinned = copy.deepcopy(self.evidence)
        pinned["schema"] = "other"
        self.install(None, pinned)
        self.assert_refused("schema is not")

    def test_a_lock_with_no_non_macos_rows_needs_no_evidence(self) -> None:
        lock_path = self.root / drift.LOCK
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        lock["platforms"] = {drift.PLATFORM: lock["platforms"][drift.PLATFORM]}
        lock_path.write_text(json.dumps(lock), encoding="utf-8")
        (self.root / evidence.EVIDENCE).unlink()
        self.assertEqual(self.problems(), [])

    def test_the_checkout_agrees(self) -> None:
        self.assertEqual(drift.check(ROOT), [])


class FakeRelease:
    """A tiny release in skia-builder's published shape, built in memory."""
    repository = "https://github.com/example/skia-builder"
    release = "chrome/m158-attested.2"
    commit = "c" * 40

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.digests: dict[str, str] = {}
        self.keys = {"linux-x64": "skia-build-linux-x64-gpu-release.zip",
                     "windows-x64": "skia-build-win-x64-gpu-release.zip"}
        subjects = []
        for key, asset in self.keys.items():
            stem = asset.removesuffix(".zip")
            zip_digest = hashlib.sha256(asset.encode()).hexdigest()
            suffix = ".lib" if key.startswith("windows") else ".a"
            prefix = "" if suffix == ".lib" else "lib"
            root = f"build/{key}/lib/Release"
            manifest = json.dumps({
                "archive": {"name": asset, "sha256": zip_digest, "size": 1},
                "cell": {"configuration": "Release"},
                "zip_members": [
                    {"kind": "static-archive", "name": f"{root}/{prefix}{lib}{suffix}",
                     "sha256": hashlib.sha256(f"{key}{lib}".encode()).hexdigest(), "size": 1}
                    for lib in ("skia", "dawn_combined")] +
                    [{"kind": "packaged-file", "name": "build/include/x.h", "sha256": "0" * 64}],
            }).encode()
            manifest_digest = hashlib.sha256(manifest).hexdigest()
            manifest_name = f"{stem}.legal-manifest.sha256-{manifest_digest}.json"
            self.files[manifest_name] = manifest
            self.digests[asset] = zip_digest
            self.digests[manifest_name] = manifest_digest
            subjects += [{"name": asset, "digest": {"sha256": zip_digest}},
                         {"name": manifest_name, "digest": {"sha256": manifest_digest}}]
        statement = {"subject": subjects, "predicate": {"buildDefinition": {
            "externalParameters": {"workflow": {
                "ref": "refs/heads/main", "repository": self.repository,
                "path": evidence.BUILD_WORKFLOW}},
            "resolvedDependencies": [{"uri": f"git+{self.repository}@refs/heads/main",
                                      "digest": {"gitCommit": self.commit}}]}}}
        self.set_statement(statement)

    def set_statement(self, statement: dict) -> None:
        bundle = json.dumps({"dsseEnvelope": {"payload": base64.b64encode(
            json.dumps(statement).encode()).decode()}}).encode()
        digest = hashlib.sha256(bundle).hexdigest()
        for key, asset in self.keys.items():
            name = f"{asset.removesuffix('.zip')}.provenance-attestation.sha256-{digest}.sigstore.json"
            for old in [n for n in self.files if n.startswith(asset.removesuffix('.zip') + ".provenance")]:
                del self.files[old]
                self.digests.pop(old, None)
            self.files[name] = bundle
            self.digests[name] = digest

    # Fetcher protocol
    def assets(self, repository: str, release: str) -> dict[str, str]:
        return dict(self.digests)

    def get(self, repository: str, release: str, name: str) -> bytes:
        return self.files[name]

    def statement(self) -> dict:
        name = next(n for n in self.files if ".provenance-attestation." in n)
        return evidence.statement_of(self.files[name])


class DeriveTest(unittest.TestCase):
    def derive(self, fake: FakeRelease) -> dict:
        return evidence.derive(fake.repository, fake.release, fake, fake.keys)

    def test_a_release_derives_its_rows(self) -> None:
        fake = FakeRelease()
        result = self.derive(fake)
        self.assertEqual(result["builder"]["commit"], fake.commit)
        record = result["platforms"]["windows-x64"]
        self.assertEqual(record["archive_root"], "build/windows-x64/lib/Release")
        self.assertEqual(sorted(record["archives"]), ["dawn_combined.lib", "skia.lib"])
        self.assertEqual(record["configuration"], "Release")
        self.assertEqual(evidence.render(result), evidence.render(self.derive(fake)))

    def test_a_manifest_whose_bytes_do_not_match_its_digest_is_refused(self) -> None:
        fake = FakeRelease()
        name = next(n for n in fake.files if ".legal-manifest." in n)
        fake.files[name] = fake.files[name].replace(b"Release", b"Debug_")
        with self.assertRaisesRegex(evidence.DeriveError, "do not match its digest"):
            self.derive(fake)

    def test_a_statement_from_another_workflow_is_refused(self) -> None:
        fake = FakeRelease()
        statement = fake.statement()
        statement["predicate"]["buildDefinition"]["externalParameters"]["workflow"]["ref"] = \
            "refs/heads/feature"
        fake.set_statement(statement)
        with self.assertRaisesRegex(evidence.DeriveError, "not built by"):
            self.derive(fake)

    def test_a_statement_that_names_another_asset_digest_is_refused(self) -> None:
        fake = FakeRelease()
        statement = fake.statement()
        statement["subject"][0]["digest"]["sha256"] = "5" * 64
        fake.set_statement(statement)
        with self.assertRaisesRegex(evidence.DeriveError, "disagree about the digests"):
            self.derive(fake)

    def test_a_release_without_a_manifest_is_refused(self) -> None:
        fake = FakeRelease()
        name = next(n for n in fake.digests if ".legal-manifest." in n)
        del fake.digests[name]
        with self.assertRaisesRegex(evidence.DeriveError, "one attestation and one legal manifest"):
            self.derive(fake)

    def test_a_manifest_that_describes_another_archive_is_refused(self) -> None:
        fake = FakeRelease()
        name = next(n for n in fake.files if ".legal-manifest." in n)
        manifest = json.loads(fake.files[name])
        manifest["archive"]["name"] = "skia-build-other-gpu-release.zip"
        data = json.dumps(manifest).encode()
        new_digest = hashlib.sha256(data).hexdigest()
        asset = name.split(".legal-manifest.")[0] + ".zip"
        new_name = f"{asset.removesuffix('.zip')}.legal-manifest.sha256-{new_digest}.json"
        del fake.files[name], fake.digests[name]
        fake.files[new_name] = data
        fake.digests[new_name] = new_digest
        statement = fake.statement()
        for subject in statement["subject"]:
            if subject["name"] == name:
                subject["name"], subject["digest"]["sha256"] = new_name, new_digest
        fake.set_statement(statement)
        with self.assertRaisesRegex(evidence.DeriveError, "does not describe"):
            self.derive(fake)

    def test_verify_refuses_evidence_that_differs_from_the_release(self) -> None:
        fake = FakeRelease()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "evidence.json"
            args = ["verify", "--release", fake.release, "--repository", fake.repository,
                    "--evidence", str(path)]
            keys = evidence.PLATFORM_ASSETS
            try:
                evidence.PLATFORM_ASSETS = fake.keys
                path.write_text(evidence.render(self.derive(fake)), encoding="utf-8")
                self.assertEqual(evidence.main(args, fetcher=fake), 0)
                data = json.loads(path.read_text(encoding="utf-8"))
                data["platforms"]["linux-x64"]["archives"]["libskia.a"] = "6" * 64
                path.write_text(evidence.render(data), encoding="utf-8")
                self.assertEqual(evidence.main(args, fetcher=fake), 1)
            finally:
                evidence.PLATFORM_ASSETS = keys

    def test_the_committed_fixture_is_a_release_derivation_shape(self) -> None:
        pinned = load("evidence-chrome-m158-attested.2.json")
        self.assertEqual(sorted(pinned["platforms"]), sorted(evidence.PLATFORM_ASSETS))
        for key, record in pinned["platforms"].items():
            self.assertEqual(record["asset"], evidence.PLATFORM_ASSETS[key])


if __name__ == "__main__":
    unittest.main()
