from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest import mock
import warnings
import zipfile


REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "fixtures/design-ir"
INSTALLER = REPO / "scripts/install.sh"
ARCHIVE_FIXTURE = FIXTURES / "pulp-emitter-generic.pulp.zip"
SCENE_FIXTURE = FIXTURES / "pulp-emitter-generic.export.json"
RECEIPT = json.loads((FIXTURES / "pulp-emitter-generic.receipt.json").read_text())
ASSET_FIXTURE = FIXTURES / RECEIPT["asset"]["path"]


def load_dispatcher_module():
    spec = importlib.util.spec_from_file_location(
        "vellum_backend_zip_tests", REPO / "cli/vellum_backend.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load Vellum backend dispatcher")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DISPATCHER = load_dispatcher_module()


def write_archive(
    path: Path,
    *,
    scene: bytes | None = None,
    asset: bytes | None = None,
    entries: list[tuple[str | zipfile.ZipInfo, bytes]] | None = None,
    compression: int = zipfile.ZIP_DEFLATED,
) -> None:
    if entries is None:
        entries = [
            ("scene.pulp.json", scene if scene is not None else SCENE_FIXTURE.read_bytes()),
            (RECEIPT["asset"]["path"], asset if asset is not None else ASSET_FIXTURE.read_bytes()),
        ]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(path, "w", compression=compression) as archive:
            for name, content in entries:
                archive.writestr(name, content)


class PulpZipSecurityTests(unittest.TestCase):
    def assert_archive_failure(self, archive: Path, status: str = "invalid_source_archive") -> None:
        with self.assertRaises(DISPATCHER.ArchiveFailure) as raised:
            with DISPATCHER.stage_pulp_archive(archive):
                self.fail("unsafe archive was staged")
        self.assertEqual(raised.exception.status, status)

    def test_rejects_traversal_absolute_backslash_drive_and_noncanonical_paths(self) -> None:
        bad_names = [
            "../scene.pulp.json",
            "/scene.pulp.json",
            "assets\\scene.pulp.json",
            "C:/scene.pulp.json",
            "nested/../scene.pulp.json",
            "nested//scene.pulp.json",
            "CON/scene.pulp.json",
            "scene.pulp.json.",
            "bad\n/scene.pulp.json",
            "a" * 1020 + ".pulp.json",
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index, name in enumerate(bad_names):
                with self.subTest(name=name):
                    archive = root / f"bad-{index}.pulp.zip"
                    write_archive(archive, entries=[(name, b"{}")])
                    self.assert_archive_failure(archive)

    def test_rejects_links_special_files_duplicates_case_collisions_and_shadowing(self) -> None:
        symlink = zipfile.ZipInfo("scene.pulp.json")
        symlink.create_system = 3
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        fifo = zipfile.ZipInfo("scene.pulp.json")
        fifo.create_system = 3
        fifo.external_attr = (stat.S_IFIFO | 0o600) << 16
        cases = [
            [(symlink, b"target")],
            [(fifo, b"pipe")],
            [("scene.pulp.json", b"{}"), ("scene.pulp.json", b"{}")],
            [("scene.pulp.json", b"{}"), ("SCENE.PULP.JSON", b"{}")],
            [("scene.pulp.json", b"{}"), ("assets/a", b"a"), ("assets", b"file")],
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index, entries in enumerate(cases):
                with self.subTest(case=index):
                    archive = root / f"collision-{index}.pulp.zip"
                    write_archive(archive, entries=entries)
                    self.assert_archive_failure(archive)

    def test_requires_exactly_one_scene_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            missing = root / "missing.pulp.zip"
            write_archive(missing, entries=[("assets/a.svg", b"svg")])
            self.assert_archive_failure(missing)
            multiple = root / "multiple.pulp.zip"
            write_archive(multiple, entries=[
                ("scene.pulp.json", b"{}"),
                ("second.pulp.json", b"{}"),
            ])
            self.assert_archive_failure(multiple)

    def test_enforces_archive_member_total_and_ratio_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "bounded.pulp.zip"
            write_archive(archive, entries=[
                ("scene.pulp.json", b"{" + b" " * 1000 + b"}"),
                ("assets/a.svg", b"asset"),
            ])
            checks = [
                ("MAX_ARCHIVE_BYTES", 10),
                ("MAX_ARCHIVE_MEMBERS", 1),
                ("MAX_MEMBER_BYTES", 100),
                ("MAX_SCENE_BYTES", 100),
                ("MAX_TOTAL_UNCOMPRESSED_BYTES", 500),
                ("MAX_COMPRESSION_RATIO", 1),
            ]
            for constant, value in checks:
                with self.subTest(bound=constant), mock.patch.object(DISPATCHER, constant, value):
                    self.assert_archive_failure(archive, "source_archive_too_large")

    def test_rejects_unsupported_compression_and_malformed_zip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            unsupported = root / "unsupported.pulp.zip"
            write_archive(
                unsupported,
                entries=[("scene.pulp.json", b"{}")],
                compression=zipfile.ZIP_BZIP2,
            )
            self.assert_archive_failure(unsupported)
            malformed = root / "malformed.pulp.zip"
            malformed.write_bytes(b"PK\x03\x04not-a-zip")
            self.assert_archive_failure(malformed)

    def test_rejects_archive_mutation_during_the_stable_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "mutating.pulp.zip"
            archive.write_bytes(ARCHIVE_FIXTURE.read_bytes())
            real_read = DISPATCHER.os.read
            mutated = False

            def mutate_after_first_read(descriptor: int, count: int) -> bytes:
                nonlocal mutated
                chunk = real_read(descriptor, count)
                if chunk and not mutated:
                    mutated = True
                    with archive.open("ab") as destination:
                        destination.write(b"mutated-during-read")
                return chunk

            with mock.patch.object(DISPATCHER.os, "read", side_effect=mutate_after_first_read):
                self.assert_archive_failure(archive, "source_archive_mutated")

    def test_native_commands_bypass_import_source_parsing(self) -> None:
        completed = subprocess.CompletedProcess(["backend"], 0)
        with mock.patch.object(DISPATCHER.subprocess, "run", return_value=completed) as invoked:
            actual = DISPATCHER.invoke_backend_with_archive_staging(
                Path("/installed/vellum-native-backend"),
                "capture",
                ["--scenario", "smoke", "--output", "artifacts/proof.png"],
            )
        self.assertIs(actual, completed)
        invoked.assert_called_once_with([
            "/installed/vellum-native-backend", "capture",
            "--scenario", "smoke", "--output", "artifacts/proof.png",
        ], check=False)


@unittest.skipUnless(shutil.which("node"), "Node.js unavailable")
class PulpZipJourneyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.install_root = tempfile.TemporaryDirectory(prefix="vellum-pulp-zip-sdk-")
        cls.prefix = Path(cls.install_root.name) / "prefix"
        completed = subprocess.run(
            ["sh", str(INSTALLER), "--local", str(REPO), "--install-dir", str(cls.prefix)],
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode:
            raise RuntimeError(completed.stdout + completed.stderr)
        cls.vellum = cls.prefix / "bin/vellum"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.install_root.cleanup()

    def invoke(self, *arguments: str, cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self.vellum), *arguments, "--json"],
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
        )

    def create(self, root: Path, name: str = "ZIP Import") -> Path:
        project = root / "app"
        created = self.invoke("create", name, "--directory", str(project), cwd=root)
        self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
        return project

    def test_public_import_unchanged_and_changed_reimport_bind_archive_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = self.create(root)
            imported = self.invoke(
                "import", str(ARCHIVE_FIXTURE), "--source-type", "figma", cwd=project,
            )
            self.assertEqual(imported.returncode, 0, imported.stdout)
            archive_hash = hashlib.sha256(ARCHIVE_FIXTURE.read_bytes()).hexdigest()
            lock_path = project / "design/import.lock.json"
            lock = json.loads(lock_path.read_text())
            source = lock["sources"]["main"]
            self.assertEqual(source["snapshotHash"], f"sha256:{archive_hash}")
            self.assertEqual(source["sourceArtifactKind"], "pulp-zip")
            revision = source["activeRevision"]
            snapshot = project / f"sources/imported/main/{revision}"
            self.assertEqual((snapshot / "source.pulp.zip").read_bytes(), ARCHIVE_FIXTURE.read_bytes())
            self.assertEqual((snapshot / "source.json").read_bytes(), self.archive_scene(ARCHIVE_FIXTURE))
            provenance = json.loads((snapshot / "provenance.json").read_text())
            self.assertEqual(provenance["sourceArtifact"]["sha256"], f"sha256:{archive_hash}")
            self.assertEqual(provenance["sourceArtifact"]["member"], "scene.pulp.json")
            checked = self.invoke("design", "check", cwd=project)
            self.assertEqual(checked.returncode, 0, checked.stdout)
            self.assertEqual(json.loads(checked.stdout)["status"], "design_clean")

            unchanged = self.invoke(
                "reimport", "--source", str(ARCHIVE_FIXTURE), cwd=project,
            )
            self.assertEqual(unchanged.returncode, 0, unchanged.stdout)
            self.assertEqual(json.loads(unchanged.stdout)["status"], "reimport_unchanged")

            unwrapped = self.invoke(
                "reimport", "--source", str(SCENE_FIXTURE), cwd=project,
            )
            self.assertEqual(unwrapped.returncode, 5, unwrapped.stdout)
            self.assertEqual(
                json.loads(unwrapped.stdout)["status"],
                "source_artifact_kind_mismatch",
            )

            # Repacking identical members changes the source artifact bytes.
            # Archive identity must advance instead of taking the scene-only
            # unchanged fast path.
            repacked = root / "repacked.pulp.zip"
            write_archive(
                repacked,
                scene=self.archive_scene(ARCHIVE_FIXTURE),
                asset=ASSET_FIXTURE.read_bytes(),
                compression=zipfile.ZIP_STORED,
            )
            self.assertNotEqual(repacked.read_bytes(), ARCHIVE_FIXTURE.read_bytes())
            reimported = self.invoke("reimport", "--source", str(repacked), cwd=project)
            self.assertEqual(reimported.returncode, 0, reimported.stdout)
            self.assertEqual(json.loads(reimported.stdout)["status"], "reimported")
            repacked_hash = hashlib.sha256(repacked.read_bytes()).hexdigest()
            repacked_source = json.loads(lock_path.read_text())["sources"]["main"]
            self.assertEqual(repacked_source["snapshotHash"], f"sha256:{repacked_hash}")

            scene = json.loads(self.archive_scene(ARCHIVE_FIXTURE))
            scene["root"]["children"][0]["content"] = "Updated ZIP Design"
            changed = root / "changed.pulp.zip"
            write_archive(changed, scene=json.dumps(scene, indent=2).encode())
            reimported = self.invoke("reimport", "--source", str(changed), cwd=project)
            self.assertEqual(reimported.returncode, 0, reimported.stdout)
            self.assertEqual(json.loads(reimported.stdout)["status"], "reimported")
            changed_hash = hashlib.sha256(changed.read_bytes()).hexdigest()
            active = json.loads(lock_path.read_text())["sources"]["main"]
            self.assertEqual(active["snapshotHash"], f"sha256:{changed_hash}")
            document = json.loads((project / "design/ir/app.designir.json").read_text())
            self.assertEqual(document["root"]["children"][0]["text"], "Updated ZIP Design")
            checked = self.invoke("design", "check", cwd=project)
            self.assertEqual(checked.returncode, 0, checked.stdout)
            self.assertEqual(json.loads(checked.stdout)["status"], "design_clean")

    def test_public_create_from_figma_zip_completes_initial_import(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "created-from-zip"
            completed = self.invoke(
                "create", "Created From ZIP", "--directory", str(project),
                "--from", "figma", str(ARCHIVE_FIXTURE), "--no-verify", cwd=root,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["status"], "created")
            self.assertIn(
                {"command": "import", "status": "imported"},
                payload["data"]["validation"]["commands"],
            )
            document = json.loads((project / "design/ir/app.designir.json").read_text())
            self.assertEqual(document["root"]["children"][0]["text"], "Emitter Proof")

    def test_asset_or_immutable_snapshot_mutation_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = self.create(root)
            imported = self.invoke("import", str(ARCHIVE_FIXTURE), cwd=project)
            self.assertEqual(imported.returncode, 0, imported.stdout)

            changed_asset = root / "changed-asset.pulp.zip"
            write_archive(changed_asset, asset=b"mutated asset bytes")
            rejected = self.invoke("reimport", "--source", str(changed_asset), cwd=project)
            self.assertEqual(rejected.returncode, 5, rejected.stdout)
            self.assertEqual(json.loads(rejected.stdout)["status"], "asset_hash_mismatch")

            lock = json.loads((project / "design/import.lock.json").read_text())
            revision = lock["sources"]["main"]["activeRevision"]
            stored = project / f"sources/imported/main/{revision}/source.pulp.zip"
            stored.write_bytes(stored.read_bytes() + b"tampered")
            rejected = self.invoke("reimport", "--source", str(ARCHIVE_FIXTURE), cwd=project)
            self.assertEqual(rejected.returncode, 5, rejected.stdout)
            self.assertEqual(json.loads(rejected.stdout)["status"], "immutable_snapshot_conflict")

    def test_public_cli_reports_invalid_archive_without_writing_import_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = self.create(root)
            invalid = root / "invalid.pulp.zip"
            write_archive(invalid, entries=[("../scene.pulp.json", b"{}")])
            rejected = self.invoke("import", str(invalid), cwd=project)
            self.assertEqual(rejected.returncode, 5, rejected.stdout)
            self.assertEqual(json.loads(rejected.stdout)["status"], "invalid_source_archive")
            self.assertFalse((project / "design/import.lock.json").exists())

            wrong_contract = root / "wrong-contract.pulp.zip"
            write_archive(wrong_contract, entries=[("scene.pulp.json", b"{}")])
            rejected = self.invoke("import", str(wrong_contract), cwd=project)
            self.assertEqual(rejected.returncode, 5, rejected.stdout)
            self.assertEqual(json.loads(rejected.stdout)["status"], "invalid_source_archive")
            self.assertFalse((project / "design/import.lock.json").exists())

    @staticmethod
    def archive_scene(path: Path) -> bytes:
        with zipfile.ZipFile(path) as archive:
            return archive.read("scene.pulp.json")


if __name__ == "__main__":
    unittest.main()
