from __future__ import annotations

from pathlib import Path
import base64
import json
import os
import runpy
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "cli/vellum_web_backend.py"
sys.path.insert(0, str(REPO / "cli"))
try:
    BACKEND_MODULE = runpy.run_path(str(BACKEND))
finally:
    sys.path.pop(0)

validate_scenario_evidence = BACKEND_MODULE["validate_scenario_evidence"]
run_chrome_scenario = BACKEND_MODULE["run_chrome_scenario"]
stop_browser = BACKEND_MODULE["stop_browser"]
validate_scenario_document = BACKEND_MODULE["validate_scenario_document"]
validate_component_source = BACKEND_MODULE["validate_component_source"]
build_component_modules = BACKEND_MODULE["build_component_modules"]
contains_sdk_install_path = BACKEND_MODULE["contains_sdk_install_path"]
lower_dom_snapshot = BACKEND_MODULE["lower_dom_snapshot"]
chrome_path = BACKEND_MODULE["chrome_path"]
BackendFailure = BACKEND_MODULE["BackendFailure"]


class ChromeDiscoveryTests(unittest.TestCase):
    def test_pinned_browser_path_is_used_ahead_of_an_installed_browser(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            browser = Path(directory) / "Google Chrome for Testing"
            browser.write_text("#!/bin/sh\n", encoding="utf-8")
            browser.chmod(0o755)
            with mock.patch.dict("os.environ", {"VELLUM_CHROME_PATH": str(browser)}), \
                    mock.patch.object(shutil, "which", return_value="/usr/bin/google-chrome"):
                self.assertEqual(chrome_path(), str(browser))

    def test_unusable_pinned_browser_path_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "absent-chrome"
            unreadable = Path(directory) / "not-executable"
            unreadable.write_text("", encoding="utf-8")
            unreadable.chmod(0o644)
            for candidate in (missing, unreadable):
                with self.subTest(candidate=candidate.name):
                    with mock.patch.dict(
                        "os.environ", {"VELLUM_CHROME_PATH": str(candidate)}
                    ), mock.patch.object(
                        shutil, "which", return_value="/usr/bin/google-chrome"
                    ):
                        with self.assertRaises(BackendFailure) as raised:
                            chrome_path()
                    self.assertEqual(raised.exception.status, "prerequisite_missing")
                    self.assertIn("VELLUM_CHROME_PATH", str(raised.exception))

    def test_strict_pinned_browser_requires_matching_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            browser = root / "Google Chrome for Testing"
            browser.write_text("#!/bin/sh\nprintf 'Google Chrome 151.0.7922.47\\n'\n", encoding="utf-8")
            browser.chmod(0o755)
            record = root / "browser-provenance.json"
            completed = subprocess.run([
                sys.executable, str(REPO / "scripts/create_browser_provenance.py"),
                "--browser", str(browser), "--requested-version", "151.0.7922.47",
                "--source-action", "browser-actions/setup-chrome@2e1d749697dd1612b833dba4a722266286fbefcd",
                "--output", str(record),
            ], text=True, capture_output=True, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            with mock.patch.dict("os.environ", {
                "VELLUM_CHROME_PATH": str(browser),
                "VELLUM_CHROME_PROVENANCE": str(record),
                "VELLUM_REQUIRE_CHROME_PROVENANCE": "1",
            }, clear=False):
                self.assertEqual(chrome_path(), str(browser))
                browser.write_text("#!/bin/sh\nprintf 'Google Chrome 151.0.7922.48\\n'\n", encoding="utf-8")
                browser.chmod(0o755)
                with self.assertRaisesRegex(BackendFailure, "provenance"):
                    chrome_path()

    def test_discovery_falls_back_when_no_browser_is_pinned(self) -> None:
        environment = {key: value for key, value in os.environ.items()
                       if key != "VELLUM_CHROME_PATH"}
        with mock.patch.dict("os.environ", environment, clear=True), \
                mock.patch.object(shutil, "which", return_value="/usr/bin/google-chrome"):
            self.assertEqual(chrome_path(), "/usr/bin/google-chrome")


class BrowserCaptureLoweringTests(unittest.TestCase):
    def test_dom_snapshot_treats_absent_node_value_as_empty(self) -> None:
        snapshot = {
            "strings": ["#document", "HTML"],
            "documents": [{
                "nodes": {
                    "nodeType": [9, 1], "nodeName": [0, 1], "nodeValue": [-1, -1],
                    "parentIndex": [-1, 0], "attributes": [[], []],
                },
            }],
        }
        root, _assets, _evidence = lower_dom_snapshot(
            snapshot, settled_snapshot=snapshot, screenshot={},
        )
        self.assertEqual(root["name"], "#document")

    def test_dom_snapshot_lowers_nodes_and_localizes_data_url_assets(self) -> None:
        payload = b"capture-asset"
        encoded = base64.b64encode(payload).decode("ascii")
        snapshot = {
            "strings": [
                "#document", "HTML", "IMG", "hello", "src", "data-vellum-id",
                "logo", "data:image/png;base64," + encoded, "block", "visible",
                "rgb(0, 0, 0)", "16px", "url(data:image/png;base64," + encoded + ")",
                "none", "data:field",
            ],
            "documents": [{
                "nodes": {
                    "nodeType": [9, 1, 1, 3],
                    "nodeName": [0, 1, 2, 3],
                    "nodeValue": [None, None, None, 3],
                    "parentIndex": [-1, 0, 1, 2],
                    "attributes": [[], [], [4, 7, 5, 6], []],
                },
                "layout": {"nodeIndex": [2], "styles": [[8, 9, 10, 11, 12, 13, 14]]},
            }],
        }
        root, assets, evidence = lower_dom_snapshot(
            snapshot,
            settled_snapshot=snapshot,
            screenshot={"mimeType": "image/png", "data": "iVBORw0KGgo=", "byteLength": 8},
            interaction_evidence=[{"action": "snapshot", "name": "saved"}],
        )
        self.assertEqual(root["sourceId"], "dom-0")
        image = root["children"][0]["children"][0]
        self.assertEqual(image["semanticId"], "logo")
        self.assertEqual(len(assets), 1)
        self.assertEqual(image["properties"]["assetRefs"], [assets[0]["uri"]])
        self.assertIn(assets[0]["uri"], image["properties"]["computedStyles"]["background-image"])
        self.assertEqual(evidence["interactionEvidence"], [{"action": "snapshot", "name": "saved"}])
        self.assertEqual(evidence["localizedAssets"][0]["data"], encoded)

    def test_dom_snapshot_rejects_oversized_asset_budget(self) -> None:
        snapshot = {
            "strings": ["#document", "data:text/plain," + ("x" * (8 * 1024 * 1024 + 1))],
            "documents": [{
                "nodes": {
                    "nodeType": [9], "nodeName": [0], "nodeValue": [None],
                    "parentIndex": [-1], "attributes": [[]],
                },
            }],
        }
        with self.assertRaises(BackendFailure):
            lower_dom_snapshot(snapshot, settled_snapshot=snapshot, screenshot={})


class WebScenarioEvidenceTests(unittest.TestCase):
    def test_sdk_path_detector_catches_absolute_and_relative_install_names(self) -> None:
        prefix = Path("/private/tmp/vellum-sdk")
        self.assertTrue(contains_sdk_install_path(
            b"source: /private/tmp/vellum-sdk/lib/ui.js", prefix,
        ))
        self.assertTrue(contains_sdk_install_path(
            b"source: ../vellum-installs/version/ui/src/runtime.js", prefix,
        ))
        self.assertTrue(contains_sdk_install_path(
            b'{"sources":["vellum://external/..%2Fvellum-installs%2F0.1.0%2Fir.js"]}',
            prefix,
        ))
        self.assertTrue(contains_sdk_install_path(
            b"source: ..%2fvellum-installs%2f0.1.0%2fir.js", prefix,
        ))
        self.assertFalse(contains_sdk_install_path(
            b"source: vellum://sdk/ui/src/runtime.js", prefix,
        ))

    @staticmethod
    def evidence(*, presses: list[dict[str, object]]) -> dict[str, object]:
        return {
            "schema": "vellum.web-proof.v1",
            "backend": "wasm-shared-cpp-core+canvas2d-shell",
            "authoringRuntime": "browser JavaScript",
            "initial": {"digest": 1, "commandCount": 4},
            "final": {"digest": 1, "commandCount": 4},
            "captures": [{"name": "imported-design"}],
            "presses": presses,
            "inputs": [],
            "keys": [],
            "components": [],
            "canvasDataBytes": 4096,
        }

    def test_static_boot_and_render_scenario_is_valid(self) -> None:
        validate_scenario_evidence(self.evidence(presses=[]))

    def test_every_semantic_press_must_change_rendered_state(self) -> None:
        with self.assertRaises(BackendFailure) as caught:
            validate_scenario_evidence(self.evidence(
                presses=[{"target": "save", "changed": False}],
            ))
        self.assertEqual(caught.exception.status, "test_failed")

    def test_changed_semantic_press_is_valid(self) -> None:
        validate_scenario_evidence(self.evidence(
            presses=[{"target": "save", "changed": True}],
        ))

    def test_missing_press_evidence_is_rejected(self) -> None:
        evidence = self.evidence(presses=[])
        del evidence["presses"]
        with self.assertRaises(BackendFailure):
            validate_scenario_evidence(evidence)

    def test_missing_render_evidence_is_rejected(self) -> None:
        evidence = self.evidence(presses=[])
        del evidence["canvasDataBytes"]
        with self.assertRaises(BackendFailure):
            validate_scenario_evidence(evidence)

    def test_component_inventory_is_exact_and_loaded(self) -> None:
        evidence = self.evidence(presses=[])
        evidence["components"] = [{
            "id": "gamut-field", "loaded": True, "renders": 1, "commands": 8,
        }]
        validate_scenario_evidence(evidence, expected_wasm_ids=["gamut-field"])
        evidence["components"][0]["loaded"] = False
        with self.assertRaisesRegex(BackendFailure, "component proof"):
            validate_scenario_evidence(evidence, expected_wasm_ids=["gamut-field"])

    def test_scenario_input_key_and_viewport_contract_matches_native_lane(self) -> None:
        validate_scenario_document({
            "schema": "vellum.scenario.v1",
            "name": "editor",
            "viewport": {"width": 720, "height": 540},
            "steps": [
                {"action": "input", "target": "title", "text": "Roadmap 🧭"},
                {"action": "key", "target": "title", "key": "Backspace"},
                {"action": "key", "target": "title", "key": "Enter"},
                {"action": "capture", "name": "saved"},
            ],
        })
        invalid = [
            {"action": "input", "target": "title", "text": "x", "mode": "append"},
            {"action": "input", "target": "title", "text": "x" * (64 * 1024 + 1)},
            {"action": "key", "target": "title", "key": "Meta+S"},
            {"action": "key", "target": "title", "key": ["Enter"]},
        ]
        for step in invalid:
            with self.subTest(step=step["action"]), self.assertRaises(BackendFailure):
                validate_scenario_document({
                    "schema": "vellum.scenario.v1", "name": "invalid",
                    "viewport": {"width": 720, "height": 540}, "steps": [step],
                })

    def test_unchanged_phase3_scenario_is_accepted_by_installed_web_backend(self) -> None:
        scenario = json.loads((
            REPO / "fixtures/authoring-phase3/scenarios/phase3.json"
        ).read_text(encoding="utf-8"))
        validate_scenario_document(scenario)

    def test_v2_text_composition_and_accessibility_contract(self) -> None:
        validate_scenario_document({
            "schema": "vellum.scenario.v2",
            "name": "text semantics",
            "steps": [
                {"action": "focus", "target": "title-input"},
                {"action": "input", "target": "title-input", "value": "GPU Notes"},
                {"action": "key", "target": "title-input", "value": "ArrowLeft"},
                {"action": "compose", "target": "title-input", "value": "日本語"},
                {
                    "action": "assert-accessibility",
                    "target": "title-input",
                    "expect": {
                        "label": "Board title",
                        "role": "text-field",
                        "value": "GPU Notes日本語",
                    },
                },
            ],
        })
        for step in [
            {"action": "compose", "target": "title-input", "value": "\0"},
            {"action": "assert-accessibility", "target": "title-input",
             "expect": {"role": 42}},
        ]:
            with self.subTest(step=step["action"]), self.assertRaises(BackendFailure):
                validate_scenario_document({
                    "schema": "vellum.scenario.v2", "name": "invalid", "steps": [step],
                })
        for action in ("pointer", "assert-state"):
            with self.subTest(action=action), self.assertRaisesRegex(
                BackendFailure, "Unsupported scenario action"
            ):
                validate_scenario_document({
                    "schema": "vellum.scenario.v2",
                    "name": "unsupported",
                    "steps": [{"action": action, "target": "title-input"}],
                })

    def test_component_source_rejects_private_framework_headers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "component.cpp"
            source.write_text(
                "#include <vellum/components/abi.h>\n", encoding="utf-8",
            )
            validate_component_source(source)
            source.write_text(
                "#include <vellum/components/abi.h>\n"
                "#include <vellum/graphics/scene.hpp>\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(BackendFailure, "public vellum/components/abi.h"):
                validate_component_source(source)

    def test_component_build_uses_installed_adapter_and_emits_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            sdk = root / "sdk"
            output = root / "output"
            project.mkdir(); output.mkdir()
            source = project / "gamut.cpp"
            source.write_text("#include <vellum/components/abi.h>\n", encoding="utf-8")
            abi = sdk / "sdk/include/vellum/components/abi.h"
            adapter = sdk / "web/browser_component_adapter.cpp"
            abi.parent.mkdir(parents=True); adapter.parent.mkdir(parents=True)
            abi.write_text("/* abi */\n", encoding="utf-8")
            adapter.write_text("/* adapter */\n", encoding="utf-8")
            (sdk / "metadata.json").write_text(json.dumps({
                "schema": "vellum.sdk-artifact.v1",
                "capabilities": {"custom_components": True},
            }), encoding="utf-8")
            commands: list[list[str]] = []

            def fake_run(arguments: list[str], **_kwargs: object) -> object:
                commands.append(arguments)
                javascript = Path(arguments[-1])
                javascript.write_text("export default async()=>({});\n", encoding="utf-8")
                javascript.with_suffix(".wasm").write_bytes(b"\\0asm")
                return object()

            context = {
                "root": project,
                "components": [{
                    "id": "gamut-field", "web": "wasm",
                    "wasm_source": "gamut.cpp", "native_source": "gamut.cpp",
                }, {
                    "id": "fallback-only", "web": "fallback",
                    "wasm_source": None, "native_source": "gamut.cpp",
                }],
            }
            with mock.patch.dict(build_component_modules.__globals__, {
                "discover_emxx": lambda: Path("/toolchain/em++"),
                "emxx_command": lambda _path: ["/toolchain/em++"],
                "run_checked": fake_run,
            }):
                inventory = build_component_modules(context, sdk, output)
            self.assertEqual(
                inventory,
                [
                    {"id": "fallback-only", "web": "fallback"},
                    {
                        "id": "gamut-field", "web": "wasm",
                        "module": "vellum_component_gamut-field.js",
                        "wasm": "vellum_component_gamut-field.wasm",
                    },
                ],
            )
            self.assertIn(str(adapter), commands[0])
            self.assertIn(str(source.resolve()), commands[0])
            self.assertTrue((output / "vellum_component_gamut-field.wasm").is_file())

    def test_wasm_declaration_requires_installed_browser_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            sdk = root / "sdk"
            output = root / "output"
            project.mkdir(); output.mkdir()
            source = project / "component.cpp"
            source.write_text("#include <vellum/components/abi.h>\n", encoding="utf-8")
            abi = sdk / "sdk/include/vellum/components/abi.h"
            abi.parent.mkdir(parents=True)
            abi.write_text("/* abi */\n", encoding="utf-8")
            (sdk / "metadata.json").write_text(json.dumps({
                "schema": "vellum.sdk-artifact.v1",
                "capabilities": {"custom_components": True},
            }), encoding="utf-8")
            with self.assertRaisesRegex(BackendFailure, "browser custom-component support"):
                build_component_modules({
                    "root": project,
                    "components": [{
                        "id": "missing-adapter", "web": "wasm",
                        "wasm_source": "component.cpp", "native_source": "component.cpp",
                    }],
                }, sdk, output)

    @unittest.skipUnless(
        (Path.home() / "emsdk/upstream/emscripten/em++").is_file()
        and shutil.which("node"),
        "Emscripten and Node are required for the linked browser ABI proof",
    )
    def test_real_linked_browser_adapter_executes_and_rejects_wrong_id(self) -> None:
        source_text = r'''
#include <vellum/components/abi.h>
static int render(const vellum_component_render_context_v1* context) {
    vellum_component_paint_command_v1 command{};
    command.struct_size = sizeof(command);
    command.kind = VELLUM_COMPONENT_PAINT_RECTANGLE_V1;
    command.id_suffix = "proof";
    command.bounds = {1.0F, 2.0F, 30.0F, 40.0F};
    command.fill = {0.1F, 0.2F, 0.3F, 1.0F};
    command.corner_radius = 4.0F;
    return context && context->emit &&
        context->emit(context->emit_user_data, &command) == 1;
}
static const vellum_component_descriptor_v1 descriptor{
    sizeof(vellum_component_descriptor_v1), VELLUM_COMPONENT_ABI_VERSION,
    "browser-proof", render,
};
extern "C" VELLUM_COMPONENT_EXPORT const vellum_component_descriptor_v1*
vellum_component_entry_v1(void) { return &descriptor; }
'''
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            sdk = root / "sdk"
            output = root / "output"
            project.mkdir(); output.mkdir()
            source = project / "proof.cpp"
            source.write_text(source_text, encoding="utf-8")
            abi = sdk / "sdk/include/vellum/components/abi.h"
            adapter = sdk / "web/browser_component_adapter.cpp"
            abi.parent.mkdir(parents=True); adapter.parent.mkdir(parents=True)
            shutil.copy2(REPO / "components/include/vellum/components/abi.h", abi)
            shutil.copy2(REPO / "components/wasm/browser_component_adapter.cpp", adapter)
            (sdk / "metadata.json").write_text(json.dumps({
                "schema": "vellum.sdk-artifact.v1",
                "capabilities": {"custom_components": True},
            }), encoding="utf-8")
            context = {
                "root": project,
                "components": [{
                    "id": "browser-proof", "web": "wasm",
                    "wasm_source": "proof.cpp", "native_source": "proof.cpp",
                }],
            }
            with mock.patch.dict(build_component_modules.__globals__, {
                "discover_emxx": lambda: Path.home() / "emsdk/upstream/emscripten/em++",
            }):
                build_component_modules(context, sdk, output)
            script = output / "execute.mjs"
            script.write_text(r'''
import create from './vellum_component_browser-proof.js';
const module = await create();
const start = module.cwrap('vellum_component_web_start', 'number', ['string']);
const render = module.cwrap(
    'vellum_component_web_render', 'number',
    ['string', 'string', 'number', 'number'],
);
const count = module.cwrap('vellum_component_web_command_count', 'number', []);
const error = module.cwrap('vellum_component_web_error', 'string', []);
if (!start('browser-proof') || !render('node', '{}', 100, 80) || count() !== 1) {
    throw new Error(error());
}
if (start('wrong-id') !== 0 || !error().includes('descriptor')) {
    throw new Error('wrong component identifier was accepted');
}
console.log('linked-browser-component-pass');
''', encoding="utf-8")
            completed = subprocess.run(
                [str(shutil.which("node")), str(script)],
                cwd=output, text=True, capture_output=True, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertEqual(completed.stdout.strip(), "linked-browser-component-pass")

    def test_chrome_stops_before_its_temporary_profile_is_removed(self) -> None:
        events: list[str] = []

        class Profile:
            def __enter__(self) -> str:
                events.append("profile-enter")
                return "/temporary/chrome-profile"

            def __exit__(self, *args: object) -> None:
                events.append("profile-exit")

        class Process:
            running = True

            def poll(self) -> int | None:
                return None if self.running else 0

            def terminate(self) -> None:
                events.append("terminate")
                self.running = False

            def wait(self, timeout: int) -> int:
                events.append(f"wait-{timeout}")
                return 0

            def kill(self) -> None:
                events.append("kill")
                self.running = False

        class Received:
            def wait(self, timeout: int) -> bool:
                events.append(f"evidence-{timeout}")
                return True

        process = Process()
        run_chrome_scenario(
            "http://127.0.0.1/example",
            Received(),
            chrome="/fake/chrome",
            profile_factory=lambda **_kwargs: Profile(),
            process_factory=lambda *_args, **_kwargs: process,
        )
        self.assertEqual(
            events,
            [
                "profile-enter",
                "evidence-20",
                "terminate",
                "wait-5",
                "profile-exit",
            ],
        )

    def test_chrome_timeout_still_stops_before_profile_cleanup(self) -> None:
        events: list[str] = []

        class Profile:
            def __enter__(self) -> str:
                events.append("profile-enter")
                return "/temporary/chrome-profile"

            def __exit__(self, *args: object) -> None:
                events.append("profile-exit")

        class Process:
            running = True

            def poll(self) -> int | None:
                return None if self.running else 0

            def terminate(self) -> None:
                events.append("terminate")
                self.running = False

            def wait(self, _timeout: int) -> int:
                events.append("wait")
                return 0

            def kill(self) -> None:
                events.append("kill")
                self.running = False

        class Received:
            def wait(self, _timeout: int) -> bool:
                events.append("timeout")
                return False

        process = Process()
        with self.assertRaisesRegex(BackendFailure, "timed out"):
            run_chrome_scenario(
                "http://127.0.0.1/example",
                Received(),
                chrome="/fake/chrome",
                profile_factory=lambda **_kwargs: Profile(),
                process_factory=lambda *_args, **_kwargs: process,
            )
        self.assertEqual(
            events,
            ["profile-enter", "timeout", "terminate", "wait", "profile-exit"],
        )

    @unittest.skipUnless(os.name == "posix", "process groups require POSIX")
    def test_exited_chrome_launcher_still_cleans_its_process_group(self) -> None:
        class Process:
            pid = 619

            def poll(self) -> int:
                return 0

        with mock.patch("os.killpg") as killpg:
            stop_browser(Process(), process_group=619)

        self.assertEqual(
            killpg.call_args_list,
            [mock.call(619, signal.SIGTERM), mock.call(619, signal.SIGKILL)],
        )


if __name__ == "__main__":
    unittest.main()
