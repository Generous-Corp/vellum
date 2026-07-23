from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]
CLI = REPO / "cli" / "vellum_cli.py"
BACKEND = REPO / "packages" / "vellum-design-ir" / "bin" / "vellum-backend.js"
FIXTURES = REPO / "fixtures" / "design-ir"


def invoke(*arguments: str, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), "--json", *arguments],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "VELLUM_BACKEND": str(BACKEND), **(env or {})},
    )


def json_files(root: Path) -> dict[str, bytes]:
    owned_roots = ("assets/generated", "design", "sources/imported", "tokens/imported", "ui/generated")
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for prefix in owned_roots
        for path in sorted((root / prefix).rglob("*"))
        if path.is_file()
    }


class ImportBackendTests(unittest.TestCase):
    def create(self, parent: Path, directory: str = "app") -> Path:
        app = parent / directory
        completed = invoke("create", "Import App", "--directory", str(app), cwd=parent)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return app

    def test_public_cli_import_and_reimport_preserve_authored_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = self.create(root)
            imported = invoke(
                "import",
                str(FIXTURES / "revision-a.source.json"),
                "--source-type",
                "figma",
                "--as",
                "main",
                cwd=app,
            )
            self.assertEqual(imported.returncode, 0, imported.stdout)
            payload = json.loads(imported.stdout)
            self.assertEqual(payload["status"], "imported")

            required = {
                "sources/imported/main/palette-board-a/source.json",
                "sources/imported/main/palette-board-a/provenance.json",
                "design/ir/sources/main.designir.json",
                "design/ir/app.designir.json",
                "design/generated/main.components.json",
                "design/generated/node-ids.d.ts",
                "design/overlays/main.authored.json",
                "design/reports/main.import-report.json",
                "design/import.lock.json",
                "tokens/imported/main.tokens.json",
                "assets/generated/main/manifest.json",
                "ui/generated/main.materialized.json",
                "ui/generated/main.bindings.json",
            }
            self.assertTrue(required.issubset(json_files(app)))

            overlay = FIXTURES / "authored.overlay.json"
            overlay_path = app / "design/overlays/main.authored.json"
            overlay_path.write_bytes(overlay.read_bytes())
            graph_path = app / "design/imports.json"
            graph = json.loads(graph_path.read_text())
            graph["sources"]["main"]["mount"] = "app-root"
            graph_path.write_text(json.dumps(graph, indent=2) + "\n", encoding="utf-8")
            authored_source = app / "src/App.tsx"
            authored_source.write_text(
                authored_source.read_text(encoding="utf-8") + "\n// developer-owned behavior\n",
                encoding="utf-8",
            )
            overlay_before = overlay_path.read_bytes()
            graph_before = graph_path.read_bytes()
            authored_before = authored_source.read_bytes()

            reimported = invoke(
                "reimport",
                "--source",
                str(FIXTURES / "revision-b.source.json"),
                "--as",
                "main",
                cwd=app,
            )
            self.assertEqual(reimported.returncode, 0, reimported.stdout)
            reimport_payload = json.loads(reimported.stdout)
            self.assertEqual(reimport_payload["status"], "reimported")
            self.assertEqual(overlay_path.read_bytes(), overlay_before)
            self.assertEqual(graph_path.read_bytes(), graph_before)
            self.assertEqual(authored_source.read_bytes(), authored_before)

            lock = json.loads((app / "design/import.lock.json").read_text())
            self.assertEqual(lock["sources"]["main"]["activeRevision"], "palette-board-b")
            report = json.loads(
                (app / "design/reports/main.palette-board-b.reimport-report.json").read_text()
            )
            self.assertTrue(report["accepted"])
            self.assertEqual(report["summary"]["conflicts"], 0)
            self.assertGreater(report["summary"]["heuristicCandidates"], 0)
            bindings = json.loads((app / "ui/generated/main.bindings.json").read_text())
            self.assertEqual(bindings["bindings"][0]["resolvedNodeId"], "main/create-button-v2")

            unchanged = invoke(
                "reimport",
                "--source",
                str(FIXTURES / "revision-b.source.json"),
                cwd=app,
            )
            self.assertEqual(unchanged.returncode, 0, unchanged.stdout)
            self.assertEqual(json.loads(unchanged.stdout)["status"], "reimport_unchanged")
            self.assertEqual(overlay_path.read_bytes(), overlay_before)
            self.assertEqual(graph_path.read_bytes(), graph_before)
            self.assertEqual(authored_source.read_bytes(), authored_before)

    def test_compatibility_figma_envelopes_reimport_with_stable_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = self.create(root)
            source_a = FIXTURES / "figma-plugin-a.export.json"
            source_b = FIXTURES / "figma-plugin-b.export.json"

            imported = invoke(
                "import", str(source_a), "--source-type", "figma", "--as", "main", cwd=app,
            )
            self.assertEqual(imported.returncode, 0, imported.stdout)
            self.assertEqual(json.loads(imported.stdout)["status"], "imported")
            document = json.loads((app / "design/ir/app.designir.json").read_text())
            self.assertEqual(document["source"]["adapter"], "figma-plugin")
            self.assertEqual(document["source"]["providerFileKey"], "paletteboard")
            self.assertEqual(document["root"]["id"], "main/1:2")

            overlay_path = app / "design/overlays/main.authored.json"
            overlay = json.loads(overlay_path.read_text())
            overlay["bindings"] = [{
                "action": "createBoard",
                "event": "press",
                "nodeId": "main/1:8",
            }]
            overlay_path.write_text(json.dumps(overlay, indent=2) + "\n", encoding="utf-8")
            overlay_before = overlay_path.read_bytes()

            reimported = invoke("reimport", "--source", str(source_b), "--as", "main", cwd=app)
            self.assertEqual(reimported.returncode, 0, reimported.stdout)
            self.assertEqual(json.loads(reimported.stdout)["status"], "reimported")
            self.assertEqual(overlay_path.read_bytes(), overlay_before)
            bindings = json.loads((app / "ui/generated/main.bindings.json").read_text())
            self.assertEqual(bindings["bindings"][0]["resolvedNodeId"], "main/1:8")

            expected_revision = "figma-" + hashlib.sha256(source_b.read_bytes()).hexdigest()[:16]
            lock = json.loads((app / "design/import.lock.json").read_text())
            self.assertEqual(lock["sources"]["main"]["activeRevision"], expected_revision)
            updated = json.loads((app / "design/ir/app.designir.json").read_text())
            self.assertEqual(updated["root"]["children"][0]["text"], "Palette Studio")
            card_ids = {
                child["id"]
                for child in updated["root"]["children"][2]["children"]
            }
            self.assertEqual(card_ids, {"main/1:6", "main/1:7", "main/1:9"})

    def test_pinned_pulp_emitter_output_reaches_materialized_ui_with_verified_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = self.create(root)
            source = FIXTURES / "pulp-emitter-generic.export.json"
            completed = invoke("import", str(source), "--source-type", "figma", cwd=app)
            self.assertEqual(completed.returncode, 0, completed.stdout)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["status"], "imported")

            document = json.loads((app / "design/ir/app.designir.json").read_text())
            self.assertEqual(document["source"]["provenance"]["parserVersion"], "0.1.0")
            self.assertEqual(document["root"]["id"], "main/1:2")
            self.assertEqual(
                [document["root"]["kind"]] + [
                    child["kind"] for child in document["root"]["children"]
                ],
                ["view", "text", "view"],
            )
            asset = document["assets"][0]
            self.assertRegex(asset["contentHash"], r"^sha256:[0-9a-f]{64}$")
            copied = app / "assets/generated/main/files" / asset["uri"]
            self.assertTrue(copied.is_file())
            self.assertEqual(
                hashlib.sha256(copied.read_bytes()).hexdigest(),
                asset["contentHash"].removeprefix("sha256:"),
            )
            materialized = json.loads((app / "ui/generated/main.materialized.json").read_text())
            self.assertEqual(materialized["root"]["children"][0]["text"], "Emitter Proof")

            unchanged = invoke("reimport", "--source", str(source), cwd=app)
            self.assertEqual(unchanged.returncode, 0, unchanged.stdout)
            self.assertEqual(json.loads(unchanged.stdout)["status"], "reimport_unchanged")

    def test_partial_backend_keeps_native_capabilities_honestly_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = self.create(root)
            completed = invoke("build", cwd=app)
            self.assertEqual(completed.returncode, 4, completed.stdout)
            payload = json.loads(completed.stdout)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["status"], "capability_unavailable")

    def test_conflicted_reimport_retains_review_artifacts_without_advancing_active_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = self.create(root)
            imported = invoke("import", str(FIXTURES / "revision-a.source.json"), cwd=app)
            self.assertEqual(imported.returncode, 0, imported.stdout)
            overlay_path = app / "design/overlays/main.authored.json"
            overlay_path.write_bytes((FIXTURES / "authored-with-orphan.overlay.json").read_bytes())
            active_ir = app / "design/ir/sources/main.designir.json"
            lock_path = app / "design/import.lock.json"
            active_before = active_ir.read_bytes()
            lock_before = lock_path.read_bytes()
            overlay_before = overlay_path.read_bytes()
            authored_before = (app / "src/App.tsx").read_bytes()

            completed = invoke(
                "reimport",
                "--source",
                str(FIXTURES / "revision-b.source.json"),
                cwd=app,
            )
            self.assertEqual(completed.returncode, 5, completed.stdout)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["status"], "reimport_conflict")
            self.assertEqual(active_ir.read_bytes(), active_before)
            self.assertEqual(lock_path.read_bytes(), lock_before)
            self.assertEqual(overlay_path.read_bytes(), overlay_before)
            self.assertEqual((app / "src/App.tsx").read_bytes(), authored_before)
            self.assertTrue(
                (app / "design/reports/main.palette-board-b.candidate.designir.json").is_file()
            )
            report = json.loads(
                (app / "design/reports/main.palette-board-b.reimport-report.json").read_text()
            )
            self.assertFalse(report["accepted"])
            self.assertGreater(len(report["conflicts"]), 0)

    def test_import_outputs_are_deterministic_across_project_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            apps = [self.create(root, name) for name in ("first", "second")]
            for app in apps:
                completed = invoke("import", str(FIXTURES / "revision-a.source.json"), cwd=app)
                self.assertEqual(completed.returncode, 0, completed.stdout)
            self.assertEqual(json_files(apps[0]), json_files(apps[1]))

    def test_assets_are_copied_and_hash_checked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = self.create(root)
            source_root = root / "source"
            (source_root / "assets").mkdir(parents=True)
            asset_bytes = b"<svg xmlns='http://www.w3.org/2000/svg'/>\n"
            (source_root / "assets/mark.svg").write_bytes(asset_bytes)
            source = json.loads((FIXTURES / "revision-a.source.json").read_text())
            source["source"]["revision"] = "assets-a"
            source["assets"] = [{
                "contentHash": "sha256:" + hashlib.sha256(asset_bytes).hexdigest(),
                "id": "mark",
                "mimeType": "image/svg+xml",
                "uri": "assets/mark.svg",
            }]
            source_path = source_root / "source.json"
            source_path.write_text(json.dumps(source), encoding="utf-8")

            completed = invoke("import", str(source_path), cwd=app)
            self.assertEqual(completed.returncode, 0, completed.stdout)
            self.assertEqual(
                (app / "sources/imported/main/assets-a/assets/mark.svg").read_bytes(),
                asset_bytes,
            )
            self.assertEqual(
                (app / "assets/generated/main/files/assets/mark.svg").read_bytes(),
                asset_bytes,
            )

            source["source"]["revision"] = "assets-b"
            source["assets"] = []
            updated_source = source_root / "updated.json"
            updated_source.write_text(json.dumps(source), encoding="utf-8")
            reimported = invoke("reimport", "--source", str(updated_source), cwd=app)
            self.assertEqual(reimported.returncode, 0, reimported.stdout)
            self.assertFalse((app / "assets/generated/main/files/assets/mark.svg").exists())
            self.assertTrue((app / "sources/imported/main/assets-a/assets/mark.svg").is_file())

            shutil.rmtree(app)
            app = self.create(root)
            source["source"]["revision"] = "assets-a"
            source["assets"] = [{
                "contentHash": "sha256:" + "0" * 64,
                "id": "mark",
                "mimeType": "image/svg+xml",
                "uri": "assets/mark.svg",
            }]
            source_path.write_text(json.dumps(source), encoding="utf-8")
            rejected = invoke("import", str(source_path), cwd=app)
            self.assertEqual(rejected.returncode, 5, rejected.stdout)
            self.assertEqual(json.loads(rejected.stdout)["status"], "asset_hash_mismatch")
            self.assertFalse((app / "design/import.lock.json").exists())

    def test_bare_asset_hashes_are_verified_on_import_and_unchanged_reimport(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = self.create(root)
            source_root = root / "source"
            source_root.mkdir()
            asset_path = source_root / "mark.svg"
            original = b"<svg xmlns='http://www.w3.org/2000/svg'><path d='M0 0'/></svg>\n"
            asset_path.write_bytes(original)
            source = json.loads((FIXTURES / "revision-a.source.json").read_text())
            source["source"]["revision"] = "bare-hash-a"
            source["assets"] = [{
                "contentHash": hashlib.sha256(original).hexdigest(),
                "id": "mark",
                "mimeType": "image/svg+xml",
                "uri": "mark.svg",
            }]
            source_path = source_root / "source.json"
            source_path.write_text(json.dumps(source), encoding="utf-8")

            imported = invoke("import", str(source_path), cwd=app)
            self.assertEqual(imported.returncode, 0, imported.stdout)
            canonical = json.loads((app / "design/ir/app.designir.json").read_text())
            self.assertEqual(
                canonical["assets"][0]["contentHash"],
                "sha256:" + hashlib.sha256(original).hexdigest(),
            )

            asset_path.write_bytes(b"changed after the source JSON was locked\n")
            rejected = invoke("reimport", "--source", str(source_path), cwd=app)
            self.assertEqual(rejected.returncode, 5, rejected.stdout)
            self.assertEqual(json.loads(rejected.stdout)["status"], "asset_hash_mismatch")

            shutil.rmtree(app)
            app = self.create(root)
            bad = json.loads(source_path.read_text())
            bad["source"]["revision"] = "bare-hash-bad"
            bad["assets"][0]["contentHash"] = "0" * 64
            bad_path = source_root / "bad.json"
            bad_path.write_text(json.dumps(bad), encoding="utf-8")
            rejected = invoke("import", str(bad_path), cwd=app)
            self.assertEqual(rejected.returncode, 5, rejected.stdout)
            self.assertEqual(json.loads(rejected.stdout)["status"], "asset_hash_mismatch")

    def test_pulp_zip_is_rejected_with_an_explicit_current_limitation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = self.create(root)
            archive = root / "design.pulp.zip"
            archive.write_bytes(b"PK\x03\x04not-a-real-archive")
            rejected = invoke("import", str(archive), cwd=app)
            self.assertEqual(rejected.returncode, 5, rejected.stdout)
            payload = json.loads(rejected.stdout)
            self.assertEqual(payload["status"], "source_archive_unsupported")
            self.assertIn("scene.pulp.json", payload["message"])

            empty_archive = root / "empty.pulp.zip"
            empty_archive.write_bytes(b"PK\x05\x06" + b"\0" * 18)
            rejected = invoke("import", str(empty_archive), cwd=app)
            self.assertEqual(rejected.returncode, 5, rejected.stdout)
            self.assertEqual(
                json.loads(rejected.stdout)["status"],
                "source_archive_unsupported",
            )

    @unittest.skipIf(os.name == "nt", "symlink semantics differ on Windows")
    def test_import_refuses_symlinked_generated_output_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = self.create(root)
            outside = root / "outside"
            outside.mkdir()
            shutil.rmtree(app / "design")
            (app / "design").symlink_to(outside, target_is_directory=True)
            rejected = invoke("import", str(FIXTURES / "revision-a.source.json"), cwd=app)
            self.assertEqual(rejected.returncode, 5, rejected.stdout)
            self.assertEqual(json.loads(rejected.stdout)["status"], "unsafe_output_path")
            self.assertEqual(list(outside.iterdir()), [])

    @unittest.skipIf(os.name == "nt", "symlink semantics differ on Windows")
    def test_import_refuses_asset_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = self.create(root)
            source_root = root / "source"
            source_root.mkdir()
            outside = root / "outside.svg"
            outside.write_text("secret", encoding="utf-8")
            (source_root / "mark.svg").symlink_to(outside)
            source = json.loads((FIXTURES / "revision-a.source.json").read_text())
            source["source"]["revision"] = "symlink-a"
            source["assets"] = [{"id": "mark", "uri": "mark.svg"}]
            source_path = source_root / "source.json"
            source_path.write_text(json.dumps(source), encoding="utf-8")
            rejected = invoke("import", str(source_path), cwd=app)
            self.assertEqual(rejected.returncode, 5, rejected.stdout)
            self.assertEqual(json.loads(rejected.stdout)["status"], "unsafe_asset_uri")
            self.assertFalse((app / "design/import.lock.json").exists())


if __name__ == "__main__":
    unittest.main()
