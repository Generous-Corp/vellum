"""`vellum import --mode baked --emit cpp|swiftui` through the built emitter.

Runs the checkout CLI against the native vellum-design-emit named by
$VELLUM_DESIGN_EMIT. Every input is a synthetic checked-in fixture.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
CLI = REPO / "cli" / "vellum_cli.py"
IR_FIXTURE = REPO / "fixtures/emitter-conformance/keyed-list-18.ir.json"
LOSSY_FIXTURE = REPO / "fixtures/emitter-conformance/form-controls-08.ir.json"
PLUGIN_FIXTURE = REPO / "fixtures/emitter-conformance/fidelity-scene-no-display.pulp.json"
TOOL = os.environ.get("VELLUM_DESIGN_EMIT", "")


def vellum(*arguments: str, env: dict[str, str] | None = None) -> tuple[int, dict]:
    completed = subprocess.run(
        [sys.executable, str(CLI), "--json", "import", *arguments],
        text=True, capture_output=True, check=False,
        env={**os.environ, **(env or {})},
    )
    return completed.returncode, json.loads(completed.stdout)


@unittest.skipUnless(TOOL, "VELLUM_DESIGN_EMIT is unset; the vellum.cli-design-emit ctest sets it")
class BakedEmitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # The ctest always sets the variable, so a set but missing emitter fails
        # rather than letting the ctest pass without running the native tool.
        if not Path(TOOL).is_file():
            raise AssertionError(f"VELLUM_DESIGN_EMIT names no file: {TOOL}")

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        # The CLI resolves its source path, and macOS temp dirs sit behind the
        # /var -> /private/var symlink, so build every path from the resolved
        # directory or absolute assets look like they leave the document.
        self.out = Path(self.temp.name).resolve()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_cpp_writes_source_header_and_bindings(self) -> None:
        code, payload = vellum("--source-type", "design-ir", str(IR_FIXTURE), "--mode", "baked",
                               "--emit", "cpp", "--out", str(self.out / "panel_ui.cpp"))
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["status"], "emitted")
        roles = {item["role"]: Path(item["path"]).name for item in payload["data"]["files"]}
        self.assertEqual(roles, {"cpp.header": "panel_ui.hpp", "cpp.source": "panel_ui.cpp",
                                 "cpp.binding_manifest": "panel_ui.bindings.json"})
        source = (self.out / "panel_ui.cpp").read_text(encoding="utf-8")
        self.assertIn('#include "panel_ui.hpp"', source)
        self.assertIn("build_imported_ui", (self.out / "panel_ui.hpp").read_text(encoding="utf-8"))
        json.loads((self.out / "panel_ui.bindings.json").read_text(encoding="utf-8"))

    def test_cpp_directory_output_uses_default_names(self) -> None:
        code, payload = vellum("--source-type", "figma-plugin", str(PLUGIN_FIXTURE), "--mode", "baked",
                               "--emit", "cpp", "--out", str(self.out))
        self.assertEqual(code, 0, payload)
        self.assertTrue((self.out / "imported_ui.cpp").is_file())
        self.assertTrue((self.out / "imported_ui.hpp").is_file())

    def test_swiftui_derives_theme_from_view_name(self) -> None:
        code, payload = vellum("--source-type", "figma-plugin", str(PLUGIN_FIXTURE), "--mode", "baked",
                               "--emit", "swiftui", "--out", str(self.out / "MixerPanel.swift"))
        self.assertEqual(code, 0, payload)
        view = (self.out / "MixerPanel.swift").read_text(encoding="utf-8")
        theme = (self.out / "MixerPanelTheme.swift").read_text(encoding="utf-8")
        self.assertIn("struct MixerPanel", view)
        self.assertIn("MixerPanelTheme", theme)
        self.assertTrue((self.out / "MixerPanel.bindings.json").is_file())

    def ir_with_absolute_asset(self, content_hash: str | None = None) -> Path:
        image = self.out / "source-art" / "swatch.png"
        image.parent.mkdir()
        image.write_bytes(b"synthetic swatch bytes")
        digest = hashlib.sha256(image.read_bytes()).hexdigest()
        document = json.loads(IR_FIXTURE.read_text(encoding="utf-8"))
        document["assetManifest"]["assets"] = [{
            "asset_id": "image:swatch", "original_uri": "swatch.png",
            "local_path": str(image), "content_hash": content_hash or digest,
            "mime": "image/png", "diagnostics": [],
        }]
        path = self.out / "with-asset.ir.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def test_cpp_copies_absolute_assets_beside_the_source(self) -> None:
        source = self.ir_with_absolute_asset()
        digest = hashlib.sha256(b"synthetic swatch bytes").hexdigest()
        code, payload = vellum("--source-type", "design-ir", str(source), "--mode", "baked",
                               "--emit", "cpp", "--out", str(self.out / "gen" / "ui.cpp"))
        self.assertEqual(code, 0, payload)
        copied = self.out / "gen" / "assets" / f"{digest}.png"
        self.assertEqual(copied.read_bytes(), b"synthetic swatch bytes")
        generated = (self.out / "gen" / "ui.cpp").read_text(encoding="utf-8")
        self.assertIn(f"assets/{digest}.png", generated)
        self.assertNotIn(str(self.out / "source-art"), generated)

    def test_cpp_refuses_an_asset_that_fails_its_hash(self) -> None:
        source = self.ir_with_absolute_asset(content_hash="0" * 64)
        code, payload = vellum("--source-type", "design-ir", str(source), "--mode", "baked",
                               "--emit", "cpp", "--out", str(self.out / "gen" / "ui.cpp"))
        self.assertNotEqual(code, 0, payload)
        self.assertEqual(payload["status"], "asset_localization_failed")
        self.assertFalse((self.out / "gen" / "ui.cpp").exists())

    def assert_preview_matches_write(self, source: Path) -> dict:
        output = self.out / "gen" / "ui.cpp"
        args = ("--source-type", "design-ir", str(source), "--mode", "baked",
                "--emit", "cpp", "--out", str(output))
        code, preview = vellum(*args, "--dry-run")
        self.assertEqual(code, 0, preview)
        self.assertFalse(output.parent.exists())
        code, written = vellum(*args)
        self.assertEqual(code, 0, written)
        for item in preview["data"]["files"]:
            self.assertEqual(item["content"], Path(item["path"]).read_text())
        return preview

    def test_cpp_dry_run_localizes_absolute_assets_without_writing(self) -> None:
        self.assert_preview_matches_write(self.ir_with_absolute_asset())

    def test_cpp_relative_assets_resolve_from_input_directory(self) -> None:
        source = self.ir_with_absolute_asset()
        document = json.loads(source.read_text())
        document["assetManifest"]["assets"][0]["local_path"] = "source-art/swatch.png"
        source.write_text(json.dumps(document))
        preview = self.assert_preview_matches_write(source)
        generated = next(f["content"] for f in preview["data"]["files"] if f["role"] == "cpp.source")
        digest = hashlib.sha256(b"synthetic swatch bytes").hexdigest()
        self.assertIn(f"assets/{digest}.png", generated)
        self.assertEqual((self.out / "gen" / "assets" / f"{digest}.png").read_bytes(),
                         b"synthetic swatch bytes")

    def test_cpp_dry_run_preserves_faithful_svg_bytes(self) -> None:
        source = self.ir_with_absolute_asset()
        document = json.loads(source.read_text())
        svg = '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32"><rect width="32" height="32" fill="red"/></svg>'
        image = self.out / "source-art" / "frame.svg"
        image.write_text(svg)
        asset = document["assetManifest"]["assets"][0]
        asset.update(local_path=str(image), original_uri="frame.svg", mime="image/svg+xml",
                     content_hash=hashlib.sha256(svg.encode()).hexdigest())
        document["root"].update(render_mode="faithful_svg", svg_asset_id=asset["asset_id"])
        source.write_text(json.dumps(document))
        preview = self.assert_preview_matches_write(source)
        generated = next(f["content"] for f in preview["data"]["files"] if f["role"] == "cpp.source")
        self.assertIn("::DesignFrameView>", generated)
        self.assertIn(base64.b64encode(svg.encode()).decode(), generated)

    def test_svg_embedding_reads_the_validated_normalized_path(self) -> None:
        doc = self.out / "document"
        doc.mkdir()
        outside = self.out / "outside"
        (outside / "child").mkdir(parents=True)
        (doc / "link").symlink_to(outside / "child", target_is_directory=True)
        local_svg = '<svg xmlns="http://www.w3.org/2000/svg"><rect fill="red"/></svg>'
        outside_svg = '<svg xmlns="http://www.w3.org/2000/svg"><rect fill="blue"/></svg>'
        (doc / "frame.svg").write_text(local_svg)
        (outside / "frame.svg").write_text(outside_svg)
        document = json.loads(IR_FIXTURE.read_text())
        document["assetManifest"]["assets"] = [{
            "asset_id": "frame", "original_uri": "frame.svg", "mime": "image/svg+xml",
            "local_path": "link/../frame.svg", "content_hash": hashlib.sha256(local_svg.encode()).hexdigest(),
        }]
        document["root"].update(render_mode="faithful_svg", svg_asset_id="frame")
        source = doc / "input.ir.json"
        source.write_text(json.dumps(document))
        preview = self.assert_preview_matches_write(source)
        generated = next(f["content"] for f in preview["data"]["files"] if f["role"] == "cpp.source")
        self.assertIn(base64.b64encode(local_svg.encode()).decode(), generated)
        self.assertNotIn(base64.b64encode(outside_svg.encode()).decode(), generated)

    def test_svg_file_uri_without_local_path_obeys_containment(self) -> None:
        doc = self.out / "document"
        doc.mkdir()
        svg = '<svg xmlns="http://www.w3.org/2000/svg"><rect fill="red"/></svg>'
        private = self.out / "private.svg"
        private.write_text(svg)
        document = json.loads(IR_FIXTURE.read_text())
        document["root"].update(render_mode="faithful_svg", svg_asset_id="frame")
        document["assetManifest"]["assets"] = [{
            "asset_id": "frame", "original_uri": private.as_uri(), "mime": "image/svg+xml",
            "content_hash": hashlib.sha256(svg.encode()).hexdigest(),
        }]
        source = doc / "input.ir.json"
        source.write_text(json.dumps(document))
        for extra in [("--dry-run",), ()]:
            with self.subTest(extra=extra):
                code, payload = vellum("--source-type", "design-ir", str(source), "--mode", "baked",
                                       "--emit", "cpp", "--out", str(self.out / "gen" / "ui.cpp"), *extra)
                self.assertEqual(code, 5, payload)
                self.assertEqual(payload["status"], "asset_localization_failed")
                self.assertFalse((self.out / "gen").exists())
        local = doc / "frame.svg"
        local.write_text(svg)
        document["assetManifest"]["assets"][0]["original_uri"] = local.as_uri()
        source.write_text(json.dumps(document))
        preview = self.assert_preview_matches_write(source)
        generated = next(f["content"] for f in preview["data"]["files"] if f["role"] == "cpp.source")
        self.assertIn(base64.b64encode(svg.encode()).decode(), generated)

    def test_cpp_refuses_conflicting_duplicate_hashes_in_preview_and_write(self) -> None:
        source = self.ir_with_absolute_asset()
        document = json.loads(source.read_text())
        asset = document["assetManifest"]["assets"][0]
        document["assetManifest"]["assets"].append({**asset, "asset_id": "image:other", "content_hash": "0" * 64})
        source.write_text(json.dumps(document))
        for extra in [("--dry-run",), ()]:
            with self.subTest(extra=extra):
                code, payload = vellum("--source-type", "design-ir", str(source), "--mode", "baked",
                                       "--emit", "cpp", "--out", str(self.out / "gen" / "ui.cpp"), *extra)
                self.assertEqual(code, 5, payload)
                self.assertEqual(payload["status"], "asset_localization_failed")
                self.assertFalse((self.out / "gen" / "ui.cpp").exists())

    def test_cpp_refuses_document_and_symlink_asset_escapes(self) -> None:
        outside_file = self.out / "outside.bin"
        outside_file.write_bytes(b"outside document")
        doc_dir = self.out / "document"
        doc_dir.mkdir()
        (doc_dir / "link.bin").symlink_to(outside_file)
        source = doc_dir / "input.ir.json"
        for raw in [str(outside_file), "../outside.bin", "link.bin"]:
            for field in ["asset_path", "fader_body_asset_path", "fader_indicator_asset_path", "manifest", "font"]:
                document = json.loads(IR_FIXTURE.read_text())
                if field == "manifest":
                    document["assetManifest"]["assets"] = [{
                        "asset_id": "outside", "local_path": raw,
                        "content_hash": hashlib.sha256(outside_file.read_bytes()).hexdigest(),
                    }]
                elif field == "font":
                    document["fontFamilyAssets"] = [{"family": "outside", "resolved_path": raw}]
                else:
                    document["root"].setdefault("attributes", {})[field] = raw
                source.write_text(json.dumps(document))
                for extra in [("--dry-run",), ()]:
                    with self.subTest(raw=raw, field=field, extra=extra):
                        code, payload = vellum("--source-type", "design-ir", str(source), "--mode", "baked",
                                               "--emit", "cpp", "--out", str(self.out / "gen" / "ui.cpp"), *extra)
                        self.assertEqual(code, 5, payload)
                        self.assertEqual(payload["status"], "asset_localization_failed")
                        self.assertFalse((self.out / "gen").exists())

    def test_relative_emitter_override_executes_the_verified_file(self) -> None:
        (self.out / "emitter").symlink_to(Path(TOOL).resolve())
        trap_dir = self.out / "path-bin"
        trap_dir.mkdir()
        trap = trap_dir / "emitter"
        trap.write_text("#!/bin/sh\nexit 87\n")
        trap.chmod(0o700)
        for override in ["./emitter", "emitter"]:
            completed = subprocess.run(
                [sys.executable, str(CLI), "--json", "import", "--source-type", "design-ir",
                 str(IR_FIXTURE), "--mode", "baked", "--emit", "cpp", "--out", "ui.cpp"],
                cwd=self.out, env={**os.environ, "VELLUM_DESIGN_EMIT": override, "PATH": str(trap_dir)},
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout)["status"], "emitted")
            self.assertTrue((self.out / "ui.cpp").is_file())

    def test_non_executable_emitter_is_structured_failure(self) -> None:
        tool = self.out / "not-executable"
        tool.write_text("#!/bin/sh\nexit 0\n")
        tool.chmod(0o600)
        code, payload = vellum("--source-type", "design-ir", str(IR_FIXTURE), "--mode", "baked",
                               "--emit", "cpp", env={"VELLUM_DESIGN_EMIT": str(tool)})
        self.assertEqual(code, 5, payload)
        self.assertEqual(payload["status"], "emit_failed")

    @unittest.skipUnless(os.name == "posix", "requires POSIX file size limits")
    def test_buffered_write_failure_is_reported(self) -> None:
        import resource
        def refuse_file_writes() -> None:
            signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
            resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
        source = self.out / "minimal.ir.json"
        document = json.loads(IR_FIXTURE.read_text())
        document["root"]["children"] = []
        source.write_text(json.dumps(document))
        completed = subprocess.run(
            [TOOL, "--source-type", "design-ir", "--input", str(source), "--emit", "swiftui", "--no-tokens",
             "--output", str(self.out / "Minimal.swift"), "--json"],
            text=True, capture_output=True, check=False, preexec_fn=refuse_file_writes,
        )
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["status"], "write_failed")

    def test_emit_requires_baked_mode(self) -> None:
        code, payload = vellum("--source-type", "design-ir", str(IR_FIXTURE), "--emit", "cpp",
                               "--out", str(self.out / "ui.cpp"))
        self.assertEqual(code, 2, payload)
        self.assertIn("requires --mode baked", payload["message"])
        self.assertFalse((self.out / "ui.cpp").exists())

    def test_designmd_is_refused(self) -> None:
        spec = self.out / "DESIGN.md"
        spec.write_text("---\nname: Synthetic\ncolors:\n  primary: '#336699'\n---\n", encoding="utf-8")
        code, payload = vellum("designmd", str(spec), "--mode", "baked", "--emit", "swiftui",
                               "--out", str(self.out / "View.swift"))
        self.assertEqual(code, 2, payload)
        self.assertEqual(payload["status"], "source_refused")
        self.assertIn("token spec", payload["message"])
        self.assertFalse((self.out / "View.swift").exists())

    def test_jsx_is_refused_without_a_misleading_route(self) -> None:
        code, payload = vellum("jsx", str(IR_FIXTURE), "--mode", "baked", "--emit", "cpp")
        self.assertEqual(code, 2, payload)
        self.assertIn("not lowered by the baked emitter", payload["message"])
        self.assertNotIn("then emit that file", payload["message"])

    def test_jsx_wrapper_is_not_emitted_as_design_ir(self) -> None:
        jsx = self.out / "Panel.jsx"
        jsx.write_text("export default function Panel() { return <div>Gain</div>; }\n", encoding="utf-8")
        wrapper = self.out / "panel.ir.json"
        code, payload = vellum("jsx", str(jsx), "--mode", "baked", "--emit", "ir-json", "--out", str(wrapper))
        self.assertEqual(code, 0, payload)
        code, payload = vellum("--source-type", "design-ir", str(wrapper), "--mode", "baked",
                               "--emit", "cpp", "--out", str(self.out / "ui.cpp"))
        self.assertEqual(code, 2, payload)
        self.assertEqual(payload["status"], "invalid_design_ir")
        self.assertIn("vellum.jsx-import.v1", payload["message"])
        self.assertFalse((self.out / "ui.cpp").exists())

    def test_non_json_design_ir_is_refused(self) -> None:
        bad = self.out / "bad.ir.json"
        bad.write_text("not json", encoding="utf-8")
        code, payload = vellum("--source-type", "design-ir", str(bad), "--mode", "baked", "--emit", "cpp",
                               "--out", str(self.out / "ui.cpp"))
        self.assertEqual(code, 2, payload)
        self.assertEqual(payload["status"], "invalid_design_ir")

    def test_local_fig_is_refused_before_the_emitter_runs(self) -> None:
        fig = self.out / "panel.fig"
        fig.write_bytes(b"synthetic")
        code, payload = vellum("--source-type", "figma", str(fig), "--mode", "baked", "--emit", "cpp",
                               "--out", str(self.out / "ui.cpp"))
        self.assertEqual(code, 2, payload)
        self.assertIn(".fig", payload["message"])
        self.assertFalse((self.out / "ui.cpp").exists())

    def test_dry_run_returns_content_and_writes_nothing(self) -> None:
        code, payload = vellum("--source-type", "design-ir", str(IR_FIXTURE), "--mode", "baked",
                               "--emit", "cpp", "--dry-run", "--out", str(self.out / "ui.cpp"))
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["status"], "dry_run")
        contents = {item["role"]: item["content"] for item in payload["data"]["files"]}
        self.assertIn('#include "ui.hpp"', contents["cpp.source"])
        self.assertIn("build_imported_ui", contents["cpp.header"])
        self.assertEqual(list(self.out.iterdir()), [])

    def test_dry_run_prints_files_without_json(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(CLI), "import", "--source-type", "design-ir", str(IR_FIXTURE),
             "--mode", "baked", "--emit", "swiftui", "--dry-run", "--out", str(self.out / "P.swift")],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("=== swift.view (", completed.stdout)
        self.assertIn("struct P", completed.stdout)
        self.assertEqual(list(self.out.iterdir()), [])

    def test_strict_fidelity_failure_is_a_backend_failure(self) -> None:
        code, payload = vellum("--source-type", "design-ir", str(LOSSY_FIXTURE), "--mode", "baked",
                               "--emit", "swiftui", "--strict-fidelity",
                               "--out", str(self.out / "Lossy.swift"))
        self.assertEqual(code, 5, payload)
        self.assertEqual(payload["status"], "fidelity_failed")
        self.assertTrue(payload["data"]["losses"])
        self.assertFalse((self.out / "Lossy.swift").exists())

    def test_without_strict_fidelity_losses_are_reported_and_written(self) -> None:
        code, payload = vellum("--source-type", "design-ir", str(LOSSY_FIXTURE), "--mode", "baked",
                               "--emit", "swiftui", "--out", str(self.out / "Lossy.swift"))
        self.assertEqual(code, 0, payload)
        self.assertTrue(payload["data"]["losses"])
        self.assertTrue((self.out / "Lossy.swift").is_file())

    def test_strict_fidelity_is_refused_for_cpp(self) -> None:
        code, payload = vellum("--source-type", "design-ir", str(IR_FIXTURE), "--mode", "baked",
                               "--emit", "cpp", "--strict-fidelity")
        self.assertEqual(code, 2, payload)
        self.assertIn("only --emit swiftui", payload["message"])

    def test_emit_flags_need_a_baked_emit(self) -> None:
        code, payload = vellum("--source-type", "design-ir", str(IR_FIXTURE), "--dry-run")
        self.assertEqual(code, 2, payload)
        self.assertIn("apply only to --emit cpp or swiftui", payload["message"])

    def test_missing_emitter_is_unavailable(self) -> None:
        code, payload = vellum("--source-type", "design-ir", str(IR_FIXTURE), "--mode", "baked",
                               "--emit", "cpp", "--out", str(self.out / "ui.cpp"),
                               env={"VELLUM_DESIGN_EMIT": str(self.out / "absent"),
                                    "VELLUM_SDK_ROOT": ""})
        self.assertEqual(code, 4, payload)
        self.assertEqual(payload["status"], "capability_unavailable")


if __name__ == "__main__":
    unittest.main()
