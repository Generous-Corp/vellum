from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid


REPO = Path(__file__).resolve().parents[2]
CLI = REPO / "cli" / "vellum_cli.py"
BACKEND = REPO / "packages" / "vellum-design-ir" / "bin" / "vellum-backend.js"
FIXTURES = REPO / "fixtures" / "design-ir"


def current_version() -> str:
    """The framework version CMakeLists.txt declares, which a release bump moves."""
    text = (REPO / "CMakeLists.txt").read_text(encoding="utf-8")
    found = re.findall(r"^project\(vellum\s+VERSION\s+([0-9]+\.[0-9]+\.[0-9]+)\b", text, re.MULTILINE | re.IGNORECASE)
    if len(found) != 1:
        raise RuntimeError("CMakeLists.txt must declare exactly one project(vellum VERSION ...)")
    return found[0]


VERSION = current_version()


def load_backend_module():
    # Loaded by path, like test_pulp_zip.py, so this file runs both as
    # `python3 cli/tests/test_import_backend.py` and under unittest discovery.
    spec = importlib.util.spec_from_file_location(
        "vellum_backend_import_tests", REPO / "cli/vellum_backend.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load Vellum backend dispatcher")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


source_argument = load_backend_module().source_argument


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
    owned_roots = (
        "assets/generated", "design", "sources/imported",
        "tokens/generated", "tokens/imported", "ui/generated",
    )
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for prefix in owned_roots
        for path in sorted((root / prefix).rglob("*"))
        if path.is_file()
    }


@unittest.skipUnless(shutil.which("node"), "requires Node")
class InstalledFigImportTests(unittest.TestCase):
    def test_fig_outline_select_by_name_and_reimport(self) -> None:
        fixture = REPO / "packages/vellum-design-ir/test/fixtures/synthetic.fig"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prefix = root / "sdk"
            installed = subprocess.run(
                ["sh", str(REPO / "scripts/install.sh"), "--local", str(REPO),
                 "--install-dir", str(prefix)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
            vellum = prefix / "bin/vellum"
            environment = os.environ.copy()
            environment.pop("VELLUM_BACKEND", None)

            def command(*args: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [str(vellum), *args], cwd=root, env=environment,
                    capture_output=True, text=True, check=False,
                )

            listing = command("import", "--outline", str(fixture))
            self.assertEqual(listing.returncode, 0, listing.stdout + listing.stderr)
            self.assertIn("0:10  Paint Lab", listing.stdout)
            outlined = command("import", str(fixture), "--outline", "--json")
            self.assertEqual(outlined.returncode, 0, outlined.stdout + outlined.stderr)
            payload = json.loads(outlined.stdout)
            self.assertEqual(payload["status"], "fig_outline")
            self.assertEqual(payload["data"]["title"], "Synthetic Fixture")
            self.assertEqual(payload["data"]["pageCount"], 2)
            page = next(page for page in payload["data"]["pages"] if page["name"] == "Page One")
            frame = next(frame for frame in page["frames"] if frame["guid"] == "0:10")
            self.assertEqual(frame["name"], "Paint Lab")
            self.assertGreater(frame["descendants"], 0)
            self.assertEqual(set(root.iterdir()), {prefix})

            app = root / "app"
            created = command("create", "Import App", "--directory", str(app))
            self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
            before = json_files(app)
            wrong_page = command(
                "import", "--project", str(app), "--frame", frame["name"],
                "--page", "Empty Page", str(fixture), "--json",
            )
            self.assertNotEqual(wrong_page.returncode, 0)
            self.assertIn("frame not found", json.loads(wrong_page.stdout)["message"])
            self.assertEqual(json_files(app), before)

            imported = command(
                "import", "--project", str(app), "--frame", frame["name"].lower(),
                "--page", page["name"].lower(), str(fixture),
            )
            self.assertEqual(imported.returncode, 0, imported.stdout + imported.stderr)
            materialized = json.loads((app / "ui/generated/main.materialized.json").read_text())
            self.assertEqual(materialized["root"]["name"], frame["name"])
            lock = json.loads((app / "design/import.lock.json").read_text())
            self.assertEqual(lock["sources"]["main"]["figFrame"], frame["guid"])
            checked = command("design", "check", "--project", str(app))
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            reimported = command("reimport", "--project", str(app), "--source", str(fixture), "--json")
            self.assertEqual(reimported.returncode, 0, reimported.stdout + reimported.stderr)
            self.assertEqual(json.loads(reimported.stdout)["status"], "reimport_unchanged")


class ImportBackendTests(unittest.TestCase):
    def test_standalone_jsx_modes_and_svg_control_map_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            jsx = root / "panel.jsx"
            jsx.write_text("export default function Panel() { return <div>ok</div>; }\n", encoding="utf-8")
            svg = root / "panel.svg"
            svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="80"><rect id="mix" x="10" y="10" width="20" height="20"/></svg>', encoding="utf-8")
            digest = hashlib.sha256(svg.read_bytes()).hexdigest()
            manifest = root / "controls.json"
            manifest.write_text(json.dumps({
                "provenance": {"source": "synthetic"},
                "svg_sha256": digest,
                "canvas": {"width": 100, "height": 80},
                "elements": [{"selector": "#mix", "kind": "knob", "geometry": {"cx": 20, "cy": 20, "hit_radius": 10}}],
            }), encoding="utf-8")
            live = root / "live.js"
            for mode, emit, output in (("live", "js", live), ("baked", "ir-json", root / "baked.json")):
                completed = invoke("import", "jsx", str(jsx), "--mode", mode, "--emit", emit, "--out", str(output), cwd=root)
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                self.assertTrue(output.is_file())
            materialized = root / "runtime"
            completed = invoke("import", "jsx", str(jsx), "--mode", "live", "--emit", "materialized-runtime", "--source-out", str(materialized), cwd=root)
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("not implemented", completed.stdout)
            self.assertFalse(materialized.exists())
            bad_utf8 = root / "bad.jsx"
            bad_utf8.write_bytes(b"\xff\xfe export")
            completed = invoke("import", "jsx", str(bad_utf8), "--mode", "baked", "--emit", "ir-json", "--out", str(root / "o.json"), cwd=root)
            self.assertNotEqual(completed.returncode, 0)
            self.assertNotIn("Traceback", completed.stderr)
            self.assertIn("valid UTF-8", completed.stdout)
            completed = invoke("import", "jsx", str(bad_utf8), "--mode", "live", "--emit", "js", "--out", str(root / "o.js"), cwd=root)
            self.assertNotEqual(completed.returncode, 0)
            self.assertFalse((root / "o.js").exists())
            directory_out = root / "adir"
            directory_out.mkdir()
            completed = invoke("import", "jsx", str(jsx), "--mode", "live", "--emit", "js", "--out", str(directory_out), cwd=root)
            self.assertNotEqual(completed.returncode, 0)
            self.assertNotIn("Traceback", completed.stderr)
            dangling = root / "dangling.js"
            dangling.symlink_to(root / "nowhere")
            completed = invoke("import", "jsx", str(jsx), "--mode", "live", "--emit", "js", "--out", str(dangling), cwd=root)
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("symlink", completed.stdout)
            (root / "real").mkdir()
            (root / "link").symlink_to(root / "real")
            completed = invoke("import", "jsx", str(jsx), "--mode", "live", "--emit", "js", "--out", "link/result.js", cwd=root)
            self.assertNotEqual(completed.returncode, 0)
            self.assertFalse((root / "real/result.js").exists())
            nan_manifest = root / "nan.json"
            nan = json.loads(manifest.read_text())
            nan["elements"][0] = {"selector": "#mix", "kind": "rect", "geometry": {"x": True, "y": 0, "w": 1, "h": 1}}
            nan_manifest.write_text(json.dumps(nan), encoding="utf-8")
            completed = invoke("import", "preconverted-svg", str(svg), "--manifest", str(nan_manifest), "--validate-only", cwd=root)
            self.assertNotEqual(completed.returncode, 0)
            nan["elements"][0]["geometry"] = {"x": float("nan"), "y": 0, "w": 1, "h": 1}
            nan_manifest.write_text(json.dumps(nan), encoding="utf-8")
            completed = invoke("import", "preconverted-svg", str(svg), "--manifest", str(nan_manifest), "--validate-only", cwd=root)
            self.assertNotEqual(completed.returncode, 0)
            nan["elements"][0]["geometry"] = {"x": 10**1000, "y": 0, "w": 1, "h": 1}
            nan_manifest.write_text(json.dumps(nan), encoding="utf-8")
            completed = invoke("import", "preconverted-svg", str(svg), "--manifest", str(nan_manifest), "--validate-only", cwd=root)
            self.assertNotEqual(completed.returncode, 0)
            self.assertNotIn("Traceback", completed.stderr)
            nan["canvas"]["width"] = 10**1000
            nan_manifest.write_text(json.dumps(nan), encoding="utf-8")
            completed = invoke("import", "preconverted-svg", str(svg), "--manifest", str(nan_manifest), "--validate-only", cwd=root)
            self.assertNotEqual(completed.returncode, 0)
            self.assertNotIn("Traceback", completed.stderr)
            bad_side = root / "badside.json"
            bad_side.write_bytes(b"\xff\xfe")
            completed = invoke("import", "preconverted-svg", str(svg), "--manifest", str(bad_side), "--validate-only", cwd=root)
            self.assertNotEqual(completed.returncode, 0)
            self.assertNotIn("Traceback", completed.stderr)
            completed = invoke("import", "preconverted-svg", str(svg), "--manifest", str(manifest), "--validate-only", cwd=root)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            bad = json.loads(manifest.read_text())
            bad["elements"][0]["selector"] = "#missing"
            manifest.write_text(json.dumps(bad), encoding="utf-8")
            completed = invoke("import", "preconverted-svg", str(svg), "--manifest", str(manifest), "--validate-only", cwd=root)
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("absent from SVG", completed.stdout)

    def test_archive_staging_parser_ignores_backend_handshake_and_capture_options(self) -> None:
        arguments = [
            "--project", "/tmp/project", "--json",
            "--framework-version", VERSION, "--cli-api", "1",
            "--capture-envelope", "/tmp/capture.json",
            "/tmp/index.html", "--source-type", "html", "--as", "main",
        ]

        result = source_argument(arguments, "import")

        self.assertEqual(result, (9, Path("/tmp/index.html")))

    def test_dispatcher_recognizes_fig_frame_option(self) -> None:
        arguments = ["--frame", "0:10", "/tmp/source.fig", "--source-type", "fig"]
        self.assertEqual(source_argument(arguments, "import"), (2, Path("/tmp/source.fig")))

    def create(self, parent: Path, directory: str = "app") -> Path:
        app = parent / directory
        completed = invoke("create", "Import App", "--directory", str(app), cwd=parent)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return app

    @unittest.skipUnless(shutil.which("node"), "requires Node")
    def test_fig_frame_selection_survives_reimport_and_design_check(self) -> None:
        fixture = REPO / "packages/vellum-design-ir/test/fixtures/synthetic.fig"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = self.create(root)
            imported = invoke("import", "--frame", "0:10", str(fixture), cwd=app)
            self.assertEqual(imported.returncode, 0, imported.stdout)
            design_path = app / "ui/generated/main.materialized.json"
            design = json.loads(design_path.read_text(encoding="utf-8"))
            self.assertEqual(design["root"]["name"], "Paint Lab")

            assets = list((app / "assets/generated/main").rglob("*.png"))
            self.assertEqual(len(assets), 1)
            self.assertTrue(assets[0].read_bytes().startswith(b"\x89PNG"))

            checked = invoke("design", "check", cwd=app)
            self.assertEqual(checked.returncode, 0, checked.stdout)
            reimported = invoke("reimport", "--source", str(fixture), cwd=app)
            self.assertEqual(json.loads(reimported.stdout)["status"], "reimport_unchanged")

    @unittest.skipUnless(shutil.which("node"), "requires Node")
    def test_fig_uses_archive_size_limit(self) -> None:
        generator = REPO / "packages/vellum-design-ir/test/support/make_synthetic_fig.mjs"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = self.create(root)
            source = root / "large.fig"
            subprocess.run(
                ["node", "--input-type=module", "-e",
                 "import {writeFileSync} from 'node:fs';"
                 "const {makeSyntheticFig} = await import(process.argv[1]);"
                 "writeFileSync(process.argv[2], makeSyntheticFig({paddingBytes: 16 * 1024 * 1024}));",
                 generator.as_uri(), str(source)],
                check=True,
            )
            imported = invoke("import", "--source-type", "fig", str(source), cwd=app)
            self.assertEqual(imported.returncode, 0, imported.stdout)

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
                "tokens/generated/main.layers.json",
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
            self.assertEqual(bindings["schema"], "vellum.generated-bindings.v1")
            self.assertEqual(bindings["revision"], "palette-board-b")
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

            checked = invoke("design", "check", "--as", "main", cwd=app)
            self.assertEqual(checked.returncode, 0, checked.stdout)
            checked_payload = json.loads(checked.stdout)
            self.assertEqual(checked_payload["schema"], "vellum.cli.result.v1")
            self.assertEqual(checked_payload["status"], "design_clean")
            self.assertEqual(
                checked_payload["data"]["backend"]["data"]["changes"],
                [],
            )

            materialized_path = app / "ui/generated/main.materialized.json"
            materialized_path.write_text('{"tampered":true}\n', encoding="utf-8")
            diffed = invoke("design", "diff", "--as", "main", cwd=app)
            self.assertEqual(diffed.returncode, 0, diffed.stdout)
            diff_payload = json.loads(diffed.stdout)
            self.assertEqual(diff_payload["status"], "design_diff")
            self.assertEqual(
                [
                    (item["kind"], item["path"])
                    for item in diff_payload["data"]["backend"]["data"]["changes"]
                ],
                [("modified", "ui/generated/main.materialized.json")],
            )
            rejected = invoke("design", "check", "--as", "main", cwd=app)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertEqual(json.loads(rejected.stdout)["status"], "design_drift")

            repaired = invoke(
                "reimport",
                "--source",
                str(FIXTURES / "revision-b.source.json"),
                "--as",
                "main",
                cwd=app,
            )
            self.assertEqual(repaired.returncode, 0, repaired.stdout)
            self.assertEqual(json.loads(repaired.stdout)["status"], "reimport_rematerialized")
            clean_again = invoke("design", "check", "--as", "main", cwd=app)
            self.assertEqual(clean_again.returncode, 0, clean_again.stdout)
            self.assertEqual(json.loads(clean_again.stdout)["status"], "design_clean")

    def test_public_source_key_and_unchanged_source_rematerialize_authored_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = self.create(root)
            source = FIXTURES / "figma-plugin-a.export.json"
            imported = invoke(
                "import", str(source), "--source-type", "figma", "--as", "shell", cwd=app,
            )
            self.assertEqual(imported.returncode, 0, imported.stdout)
            payload = json.loads(imported.stdout)
            self.assertEqual(payload["data"]["backend"]["data"]["sourceKey"], "shell")
            self.assertTrue((app / "ui/generated/shell.bindings.json").is_file())
            self.assertFalse((app / "ui/generated/main.bindings.json").exists())

            overlay_path = app / "design/overlays/shell.authored.json"
            overlay = json.loads(overlay_path.read_text())
            overlay["bindings"] = [{
                "action": "openSettings",
                "event": "press",
                "nodeId": "shell/1:4",
            }]
            overlay_path.write_text(json.dumps(overlay, indent=2) + "\n", encoding="utf-8")
            overlay_before = overlay_path.read_bytes()

            reimported = invoke(
                "reimport", "--source", str(source), "--as", "shell", cwd=app,
            )
            self.assertEqual(reimported.returncode, 0, reimported.stdout)
            reimport_payload = json.loads(reimported.stdout)
            self.assertEqual(reimport_payload["status"], "reimport_rematerialized")
            self.assertEqual(reimport_payload["data"]["backend"]["data"]["files"], [
                "ui/generated/shell.bindings.json",
            ])
            bindings = json.loads((app / "ui/generated/shell.bindings.json").read_text())
            self.assertEqual(bindings["sourceKey"], "shell")
            self.assertEqual(bindings["bindings"][0]["action"], "openSettings")
            self.assertEqual(bindings["bindings"][0]["resolvedNodeId"], "shell/1:4")
            self.assertEqual(overlay_path.read_bytes(), overlay_before)

            unchanged = invoke(
                "reimport", "--source", str(source), "--as", "shell", cwd=app,
            )
            self.assertEqual(unchanged.returncode, 0, unchanged.stdout)
            self.assertEqual(json.loads(unchanged.stdout)["status"], "reimport_unchanged")

            overlay = json.loads(overlay_path.read_text())
            overlay["overrides"] = [{
                "nodeId": "shell/1:6",
                "path": "properties.paint.backgroundColor",
                "value": "#0ea5e9",
            }]
            overlay_path.write_text(json.dumps(overlay, indent=2) + "\n", encoding="utf-8")
            rematerialized = invoke(
                "reimport", "--source", str(source), "--as", "shell", cwd=app,
            )
            self.assertEqual(rematerialized.returncode, 0, rematerialized.stdout)
            payload = json.loads(rematerialized.stdout)
            self.assertEqual(payload["status"], "reimport_rematerialized")
            self.assertEqual(payload["data"]["backend"]["data"]["files"], [
                "ui/generated/shell.materialized.json",
            ])
            materialized = json.loads(
                (app / "ui/generated/shell.materialized.json").read_text()
            )
            cyan = materialized["root"]["children"][2]["children"][0]
            self.assertEqual(cyan["properties"]["paint"]["backgroundColor"], "#0ea5e9")

            overlay = json.loads(overlay_path.read_text())
            overlay["semanticTokens"] = {"color.action": "{shell.color.accent}"}
            overlay_path.write_text(json.dumps(overlay, indent=2) + "\n", encoding="utf-8")
            retokenized = invoke(
                "reimport", "--source", str(source), "--as", "shell", cwd=app,
            )
            self.assertEqual(retokenized.returncode, 0, retokenized.stdout)
            payload = json.loads(retokenized.stdout)
            self.assertEqual(payload["status"], "reimport_rematerialized")
            self.assertEqual(payload["data"]["backend"]["data"]["files"], [
                "tokens/generated/shell.layers.json",
            ])
            layers = json.loads(
                (app / "tokens/generated/shell.layers.json").read_text()
            )
            self.assertEqual(layers["layers"]["semantic"]["color.action"], "#22c55e")

            bindings_path = app / "ui/generated/shell.bindings.json"
            bindings_path.write_text("{}\n", encoding="utf-8")
            repaired = invoke(
                "reimport", "--source", str(source), "--as", "shell", cwd=app,
            )
            self.assertEqual(repaired.returncode, 0, repaired.stdout)
            payload = json.loads(repaired.stdout)
            self.assertEqual(payload["status"], "reimport_rematerialized")
            self.assertEqual(payload["data"]["backend"]["data"]["files"], [
                "ui/generated/shell.bindings.json",
            ])
            self.assertEqual(
                json.loads(bindings_path.read_text())["bindings"][0]["action"],
                "openSettings",
            )

    def test_two_queued_reimports_serialize_and_leave_one_coherent_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = self.create(root)
            source_a = FIXTURES / "revision-a.source.json"
            source_b = FIXTURES / "revision-b.source.json"
            source_c = root / "revision-c.source.json"
            revision_c = json.loads(source_b.read_text(encoding="utf-8"))
            revision_c["source"]["revision"] = "palette-board-c"
            revision_c["root"]["name"] = "Palette board C"
            source_c.write_text(
                json.dumps(revision_c, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            imported = invoke(
                "import", str(source_a), "--source-type", "figma", "--as", "main", cwd=app,
            )
            self.assertEqual(imported.returncode, 0, imported.stdout)

            lock = app / ".vellum/locks/design-import.lock"
            lock.mkdir(parents=True)
            nonce = str(uuid.uuid4())
            (lock / "owner.json").write_text(json.dumps({
                "acquiredAt": "2026-07-22T00:00:00.000Z",
                "hostname": socket.gethostname(),
                "nonce": nonce,
                "pid": os.getpid(),
                "schema": "vellum.import-operation-lock.v1",
            }), encoding="utf-8")

            environment = {**os.environ, "VELLUM_BACKEND": str(BACKEND)}
            command = [
                sys.executable, str(CLI), "--json", "reimport",
                "--source", "", "--as", "main",
            ]
            first_command = list(command)
            first_command[first_command.index("")] = str(source_b)
            second_command = list(command)
            second_command[second_command.index("")] = str(source_c)
            first = subprocess.Popen(
                first_command, cwd=app, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment,
            )
            second = subprocess.Popen(
                second_command, cwd=app, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment,
            )
            try:
                time.sleep(0.35)
                self.assertIsNone(first.poll(), "first reimport did not wait for the live lock")
                self.assertIsNone(second.poll(), "second reimport did not wait for the live lock")
                self.assertEqual(
                    json.loads((lock / "owner.json").read_text())["nonce"],
                    nonce,
                    "a live owner lock was replaced",
                )
                shutil.rmtree(lock)
                first_stdout, first_stderr = first.communicate(timeout=15)
                second_stdout, second_stderr = second.communicate(timeout=15)
            finally:
                for process in (first, second):
                    if process.poll() is None:
                        process.kill()
                        process.communicate()

            self.assertEqual(first.returncode, 0, first_stderr or first_stdout)
            self.assertEqual(second.returncode, 0, second_stderr or second_stdout)
            self.assertEqual(json.loads(first_stdout)["status"], "reimported")
            self.assertEqual(json.loads(second_stdout)["status"], "reimported")
            active = json.loads(
                (app / "design/import.lock.json").read_text(encoding="utf-8")
            )["sources"]["main"]["activeRevision"]
            self.assertIn(active, {"palette-board-b", "palette-board-c"})
            revision_receipts = [
                json.loads(path.read_text(encoding="utf-8"))["source"]["revision"]
                for path in (
                    app / "design/ir/sources/main.designir.json",
                    app / "design/ir/app.designir.json",
                    app / "ui/generated/main.materialized.json",
                )
            ]
            revision_receipts.extend([
                json.loads(path.read_text(encoding="utf-8"))["revision"]
                for path in (
                    app / "design/generated/main.components.json",
                    app / "ui/generated/main.bindings.json",
                    app / "tokens/generated/main.layers.json",
                    app / "assets/generated/main/manifest.json",
                )
            ])
            self.assertEqual(revision_receipts, [active] * len(revision_receipts))
            self.assertFalse(lock.exists())

    def test_dead_and_stale_import_locks_recover_without_recursive_project_deletion(self) -> None:
        for mode in ("dead-owner", "missing-owner"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                app = self.create(root)
                lock = app / ".vellum/locks/design-import.lock"
                lock.mkdir(parents=True)
                if mode == "dead-owner":
                    dead_pid = 2_147_483_647
                    with self.assertRaises(ProcessLookupError):
                        os.kill(dead_pid, 0)
                    (lock / "owner.json").write_text(json.dumps({
                        "acquiredAt": "2026-07-22T00:00:00.000Z",
                        "hostname": socket.gethostname(),
                        "nonce": str(uuid.uuid4()),
                        "pid": dead_pid,
                        "schema": "vellum.import-operation-lock.v1",
                    }), encoding="utf-8")
                else:
                    stale = time.time() - 120
                    os.utime(lock, (stale, stale))
                completed = invoke(
                    "import", str(FIXTURES / "revision-a.source.json"),
                    "--source-type", "figma", "--as", "main", cwd=app,
                )
                self.assertEqual(completed.returncode, 0, completed.stdout)
                self.assertEqual(json.loads(completed.stdout)["status"], "imported")
                self.assertFalse(lock.exists())
                self.assertTrue((app / "src/App.tsx").is_file())

    def test_import_lock_root_symlink_fails_with_stable_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = self.create(root)
            outside = root / "outside"
            outside.mkdir()
            sentinel = outside / "keep.txt"
            sentinel.write_text("keep\n", encoding="utf-8")
            os.symlink(outside, app / ".vellum/locks")

            completed = invoke(
                "import", str(FIXTURES / "revision-a.source.json"),
                "--source-type", "figma", "--as", "main", cwd=app,
            )
            self.assertNotEqual(completed.returncode, 0)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["status"], "unsafe_output_path")
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep\n")
            self.assertFalse((app / "design/import.lock.json").exists())

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
            expected_revision = "figma-" + hashlib.sha256(source_b.read_bytes()).hexdigest()[:16]
            bindings = json.loads((app / "ui/generated/main.bindings.json").read_text())
            self.assertEqual(bindings["schema"], "vellum.generated-bindings.v1")
            self.assertEqual(bindings["revision"], expected_revision)
            self.assertEqual(bindings["bindings"][0]["resolvedNodeId"], "main/1:8")

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

    def test_internal_node_backend_requires_secure_dispatcher_for_pulp_zip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = self.create(root)
            archive = root / "design.pulp.zip"
            archive.write_bytes(b"PK\x03\x04not-a-real-archive")
            rejected = invoke("import", str(archive), cwd=app)
            self.assertEqual(rejected.returncode, 5, rejected.stdout)
            payload = json.loads(rejected.stdout)
            self.assertEqual(payload["status"], "source_archive_requires_dispatcher")
            self.assertIn("installed vellum CLI dispatcher", payload["message"])

            empty_archive = root / "empty.pulp.zip"
            empty_archive.write_bytes(b"PK\x05\x06" + b"\0" * 18)
            rejected = invoke("import", str(empty_archive), cwd=app)
            self.assertEqual(rejected.returncode, 5, rejected.stdout)
            self.assertEqual(
                json.loads(rejected.stdout)["status"],
                "source_archive_requires_dispatcher",
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
