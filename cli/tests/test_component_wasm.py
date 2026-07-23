from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]
CLI = REPO / "cli/vellum_cli.py"
PROOF = REPO / "scripts/verify_component_wasm.py"
EMXX = Path.home() / "emsdk/upstream/emscripten/em++"


SOURCE = r'''#include <vellum/components/abi.h>
#include <cstdio>

static int render(const vellum_component_render_context_v1* context) {
    if (context == nullptr || context->emit == nullptr) return 0;
    for (int index = 0; index < 12; ++index) {
        char suffix[16];
        std::snprintf(suffix, sizeof(suffix), "bar-%d", index);
        vellum_component_paint_command_v1 command{};
        command.struct_size = sizeof(command);
        command.kind = VELLUM_COMPONENT_PAINT_RECTANGLE_V1;
        command.id_suffix = suffix;
        command.bounds = {index * 46.0F, 20.0F + index * 3.0F, 34.0F, 180.0F - index * 8.0F};
        command.fill = {0.08F, 0.72F, 0.65F, 1.0F};
        command.corner_radius = 5.0F;
        if (context->emit(context->emit_user_data, &command) != 1) return 0;
    }
    return 1;
}

static const vellum_component_descriptor_v1 descriptor{
    sizeof(vellum_component_descriptor_v1), VELLUM_COMPONENT_ABI_VERSION,
    "level-meter", render,
};
extern "C" VELLUM_COMPONENT_EXPORT const vellum_component_descriptor_v1*
vellum_component_entry_v1(void) { return &descriptor; }
'''


@unittest.skipUnless(EMXX.is_file() and shutil.which("node"), "Emscripten/Node unavailable")
class ComponentWasmTests(unittest.TestCase):
    def test_declared_wasm_component_executes_the_paint_abi(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "app"
            created = subprocess.run([
                sys.executable, str(CLI), "create", "Wasm Component", "-d", str(project),
                "--no-verify", "--json",
            ], cwd=REPO, text=True, capture_output=True, check=False)
            self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
            for name in ("level-meter-native.cpp", "level-meter-wasm.cpp"):
                (project / "native" / name).write_text(SOURCE, encoding="utf-8")
            (project / "native/components.toml").write_text(
                '[manifest]\nschema = "vellum.components.v1"\ncomponents = ["level-meter"]\n\n'
                '[component.level-meter]\n'
                'native_source = "native/level-meter-native.cpp"\n'
                'web = "wasm"\n'
                'wasm_source = "native/level-meter-wasm.cpp"\n',
                encoding="utf-8",
            )
            completed = subprocess.run([
                sys.executable, str(PROOF), "--project", str(project),
                "--output", str(root / "wasm"), "--emxx", str(EMXX), "--json",
            ], cwd=REPO, text=True, capture_output=True, check=False,
                env=dict(os.environ))
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            evidence = json.loads(completed.stdout)
            self.assertTrue(evidence["ok"])
            self.assertEqual(evidence["components"][0]["id"], "level-meter")
            self.assertEqual(evidence["components"][0]["commands"], 12)
            self.assertTrue((root / "wasm/level-meter.wasm").is_file())


if __name__ == "__main__":
    unittest.main()
