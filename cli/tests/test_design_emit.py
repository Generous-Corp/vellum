"""`vellum import --mode baked --emit cpp|swiftui` through the built emitter.

Runs the checkout CLI against the native vellum-design-emit named by
$VELLUM_DESIGN_EMIT. Every input is a synthetic checked-in fixture.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
CLI = REPO / "cli" / "vellum_cli.py"
IR_FIXTURE = REPO / "fixtures/emitter-conformance/keyed-list-18.ir.json"
LOSSY_FIXTURE = REPO / "fixtures/emitter-conformance/form-controls-08.ir.json"
STATIC_FIXTURE = REPO / "fixtures/baked-emit/static-export.html"
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
        self.out = Path(self.temp.name)

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

    def test_jsx_points_at_the_design_ir_route(self) -> None:
        code, payload = vellum("jsx", str(IR_FIXTURE), "--mode", "baked", "--emit", "cpp")
        self.assertEqual(code, 2, payload)
        self.assertIn("--source-type design-ir", payload["message"])

    def test_local_fig_is_refused_before_the_emitter_runs(self) -> None:
        fig = self.out / "panel.fig"
        fig.write_bytes(b"synthetic")
        code, payload = vellum("--source-type", "figma", str(fig), "--mode", "baked", "--emit", "cpp",
                               "--out", str(self.out / "ui.cpp"))
        self.assertEqual(code, 2, payload)
        self.assertIn(".fig", payload["message"])
        self.assertFalse((self.out / "ui.cpp").exists())

    def test_claude_design_offline_emits_visible_text_only(self) -> None:
        code, payload = vellum("--source-type", "claude-design", str(STATIC_FIXTURE), "--offline",
                               "--mode", "baked", "--emit", "cpp", "--out", str(self.out / "ui.cpp"))
        self.assertEqual(code, 0, payload)
        source = (self.out / "ui.cpp").read_text(encoding="utf-8")
        for text in ("Gain Stage", "Drive", "Output Trim"):
            self.assertIn(text, source)
        self.assertNotIn("never rendered", source)

    def test_claude_design_offline_emits_swiftui(self) -> None:
        code, payload = vellum("--source-type", "claude-design", str(STATIC_FIXTURE), "--offline",
                               "--mode", "baked", "--emit", "swiftui", "--out", str(self.out / "Gain.swift"))
        self.assertEqual(code, 0, payload)
        self.assertIn("Gain Stage", (self.out / "Gain.swift").read_text(encoding="utf-8"))

    def test_claude_design_without_offline_points_at_the_browser_route(self) -> None:
        code, payload = vellum("--source-type", "claude-design", str(STATIC_FIXTURE), "--mode", "baked",
                               "--emit", "cpp", "--out", str(self.out / "ui.cpp"))
        self.assertEqual(code, 2, payload)
        self.assertIn("--offline", payload["message"])
        self.assertIn("design-ir", payload["message"])
        self.assertFalse((self.out / "ui.cpp").exists())

    def test_offline_is_refused_for_a_structured_source(self) -> None:
        code, payload = vellum("--source-type", "design-ir", str(IR_FIXTURE), "--offline", "--mode", "baked",
                               "--emit", "cpp", "--out", str(self.out / "ui.cpp"))
        self.assertEqual(code, 2, payload)
        self.assertIn("--offline applies only to --source-type claude-design", payload["message"])

    def test_offline_needs_a_baked_emit(self) -> None:
        code, payload = vellum("--source-type", "claude-design", str(STATIC_FIXTURE), "--offline")
        self.assertEqual(code, 2, payload)
        self.assertIn("--offline applies only to --emit cpp or swiftui", payload["message"])

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
