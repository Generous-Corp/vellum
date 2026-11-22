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
lower_computed_material = BACKEND_MODULE["_lower_computed_material"]
computed_gradient = BACKEND_MODULE["_computed_gradient"]
CAPTURE_STYLE_NAMES = BACKEND_MODULE["CAPTURE_STYLE_NAMES"]
chrome_path = BACKEND_MODULE["chrome_path"]
BackendFailure = BACKEND_MODULE["BackendFailure"]
write_capture_artifact = BACKEND_MODULE["write_capture_artifact"]
command_result = BACKEND_MODULE["command_result"]
backend_parser = BACKEND_MODULE["parser"]
run_chrome_interaction_capture = BACKEND_MODULE["run_chrome_interaction_capture"]
validate_interaction_plan = BACKEND_MODULE["validate_interaction_plan"]

sys.path.insert(0, str(REPO / "cli"))
sys.path.insert(0, str(REPO / "cli/tests"))
try:
    import vellum_png
    import live_browser
finally:
    sys.path.pop(0)
    sys.path.pop(0)


class LiveBrowserGateTests(unittest.TestCase):
    """Live-browser tests never reach for the installed Chrome by default."""

    def run_live_capture_test(self, environ: dict[str, str]):
        case = next(case for case in globals().values()
                    if isinstance(case, type) and issubclass(case, unittest.TestCase)
                    and hasattr(case, "test_a_live_browser_capture_is_a_png_of_the_page"))
        suite = unittest.TestSuite([case("test_a_live_browser_capture_is_a_png_of_the_page")])
        result = unittest.TestResult()
        system = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        with mock.patch.dict("os.environ", environ, clear=True), \
             mock.patch("subprocess.Popen") as popen, \
             mock.patch("subprocess.run") as run, \
             mock.patch("shutil.which", return_value="/usr/bin/google-chrome"):
            suite.run(result)
        launched = [call for call in popen.call_args_list + run.call_args_list
                    if system in repr(call) or "google-chrome" in repr(call)]
        return result, popen, run, launched

    def test_unset_pinned_browser_skips_and_never_launches_system_chrome(self) -> None:
        result, popen, run, launched = self.run_live_capture_test({})
        self.assertEqual((len(result.skipped), len(result.failures), len(result.errors)), (1, 0, 0))
        self.assertIn("VELLUM_CHROME_PATH", result.skipped[0][1])
        self.assertEqual(launched, [])
        popen.assert_not_called()
        run.assert_not_called()

    def test_computed_material_rejects_elliptical_border_radius(self) -> None:
        node = {"kind": "view", "properties": {}}
        with self.assertRaisesRegex(BackendFailure, "elliptical border radius top-left") as raised:
            lower_computed_material(
                node, {"border-top-left-radius": "12px 6px"}, (0, 0, 80, 40))
        self.assertEqual(raised.exception.status, "unsupported_material")

    def test_computed_material_rejects_percentage_radius_on_non_square_box(self) -> None:
        node = {"kind": "view", "properties": {}}
        with self.assertRaisesRegex(BackendFailure, "percentage border radius top-left") as raised:
            lower_computed_material(
                node, {"border-top-left-radius": "50%"}, (0, 0, 80, 40))
        self.assertEqual(raised.exception.status, "unsupported_material")

    def test_computed_material_rejects_background_gradient_overflow(self) -> None:
        node = {"kind": "view", "properties": {}}
        layers = ", ".join("linear-gradient(to right, rgb(0, 0, 0), rgb(255, 255, 255))"
                            for _ in range(9))
        with self.assertRaisesRegex(BackendFailure, "more than 8 background gradients") as raised:
            lower_computed_material(
                node, {"background-image": layers}, (0, 0, 80, 40))
        self.assertEqual(raised.exception.status, "unsupported_material")

    def test_computed_gradient_retains_circle_shape_and_position(self) -> None:
        gradient = computed_gradient(
            "radial-gradient(circle 40% at 25% 75%, rgb(0, 0, 0), rgb(255, 255, 255))")
        self.assertEqual(gradient["radiusX"], 0.4)
        self.assertEqual(gradient["radiusY"], 0.4)
        self.assertEqual(gradient["centerX"], 0.25)
        self.assertEqual(gradient["centerY"], 0.75)

    def test_computed_material_rejects_default_radial_gradient(self) -> None:
        node = {"kind": "view", "properties": {}}
        with self.assertRaisesRegex(BackendFailure, "default radial gradients") as raised:
            lower_computed_material(
                node, {"background-image": "radial-gradient(rgb(0, 0, 0), rgb(255, 255, 255))"},
                (0, 0, 80, 40))
        self.assertEqual(raised.exception.status, "unsupported_material")

    def test_computed_material_rejects_unsupported_gradient_direction(self) -> None:
        node = {"kind": "view", "properties": {}}
        with self.assertRaisesRegex(BackendFailure, "unsupported CSS gradient layer") as raised:
            lower_computed_material(
                node,
                {"background-image": "linear-gradient(to bottom right, rgb(0, 0, 0), rgb(255, 255, 255))"},
                (0, 0, 80, 40),
            )
        self.assertEqual(raised.exception.status, "unsupported_material")

    def test_a_set_but_unusable_pinned_browser_fails_instead_of_skipping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = str(Path(directory) / "no-such-chrome")
            plain = Path(directory) / "plain"
            plain.write_text("x", encoding="utf-8")
            plain.chmod(0o644)
            for pin in (missing, str(plain)):
                with self.subTest(pin=pin):
                    self.assertIsNone(live_browser.live_browser_skip_reason({"VELLUM_CHROME_PATH": pin}))
                    result, popen, run, launched = self.run_live_capture_test({"VELLUM_CHROME_PATH": pin})
                    self.assertEqual(len(result.skipped), 0)
                    self.assertEqual(len(result.failures) + len(result.errors), 1)
                    self.assertIn("VELLUM_CHROME_PATH does not name an executable browser",
                                  (result.failures + result.errors)[0][1])
                    self.assertEqual(launched, [])
                    popen.assert_not_called()
                    run.assert_not_called()

    def test_escape_hatch_is_the_only_way_to_allow_the_system_browser(self) -> None:
        self.assertIsNone(live_browser.live_browser_skip_reason({"VELLUM_TEST_ALLOW_SYSTEM_CHROME": "1"}))
        self.assertIsNotNone(live_browser.live_browser_skip_reason({"VELLUM_TEST_ALLOW_SYSTEM_CHROME": "0"}))
        self.assertIsNotNone(live_browser.live_browser_skip_reason({}))
        self.assertIsNotNone(live_browser.live_browser_skip_reason({"VELLUM_CHROME_PATH": ""}))

    def test_the_escape_hatch_does_not_reach_the_installed_browser_in_a_sandbox(self) -> None:
        sandboxed = {"VELLUM_TEST_ALLOW_SYSTEM_CHROME": "1", "CODEX_SANDBOX": "seatbelt"}
        self.assertIsNotNone(live_browser.live_browser_skip_reason(sandboxed))
        result, popen, run, launched = self.run_live_capture_test(sandboxed)
        self.assertEqual((len(result.skipped), len(result.failures), len(result.errors)), (1, 0, 0))
        self.assertEqual(launched, [])

    def test_a_pinned_executable_browser_may_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            browser = Path(directory) / "chrome"
            browser.write_text("#!/bin/sh\n", encoding="utf-8")
            browser.chmod(0o755)
            self.assertIsNone(live_browser.live_browser_skip_reason({"VELLUM_CHROME_PATH": str(browser)}))


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

    def test_discovery_refuses_the_installed_app_inside_a_sandbox(self) -> None:
        environment = {key: value for key, value in os.environ.items()
                       if key not in ("VELLUM_CHROME_PATH", "CODEX_SANDBOX")}
        application = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        real_is_file = Path.is_file
        def is_file(path: Path) -> bool:
            return str(path) == application or real_is_file(path)
        with mock.patch.object(shutil, "which", return_value=None), \
                mock.patch.object(Path, "is_file", is_file):
            with mock.patch.dict("os.environ", environment, clear=True):
                self.assertEqual(chrome_path(), application)
            with mock.patch.dict("os.environ", {**environment, "CODEX_SANDBOX": "seatbelt"},
                                 clear=True):
                with self.assertRaisesRegex(BackendFailure, "sandbox"):
                    chrome_path()


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

    def test_dom_snapshot_omits_non_rendered_inline_source(self) -> None:
        inline_source = "x" * (64 * 1024 + 1)
        snapshot = {
            "strings": ["#document", "SCRIPT", "STYLE", "P", "#text", inline_source, "Visible"],
            "documents": [{
                "nodes": {
                    "nodeType": [9, 1, 3, 1, 3, 1, 3],
                    "nodeName": [0, 1, 4, 2, 4, 3, 4],
                    "nodeValue": [-1, -1, 5, -1, 5, -1, 6],
                    "parentIndex": [-1, 0, 1, 0, 3, 0, 5],
                },
                "layout": {"nodeIndex": [0, 5, 6], "styles": [[], [], []]},
            }],
        }
        root, _assets, evidence = lower_dom_snapshot(
            snapshot, settled_snapshot=snapshot, screenshot={},
        )
        script, style, paragraph = root["children"]
        self.assertEqual(script["children"], [])
        self.assertEqual(style["children"], [])
        self.assertEqual(paragraph["children"][0]["text"], "Visible")
        self.assertEqual(evidence["settledSnapshot"]["strings"][5], inline_source)

        snapshot["documents"][0]["nodes"]["nodeValue"][6] = 5
        with self.assertRaisesRegex(BackendFailure, r"nodeValue\[6\].*invalid bounded string"):
            lower_dom_snapshot(snapshot, settled_snapshot=snapshot, screenshot={})

        layout = snapshot["documents"][0]["layout"]
        layout["nodeIndex"].append(2)
        layout["styles"].append([])
        with self.assertRaisesRegex(BackendFailure, r"nodeValue\[2\].*invalid bounded string"):
            lower_dom_snapshot(snapshot, settled_snapshot=snapshot, screenshot={})

    def test_dom_snapshot_treats_empty_attribute_value_as_empty(self) -> None:
        """Chrome sends alt="" as the -1 sentinel, not an index to an empty string."""
        snapshot = {
            "strings": ["#document", "IMG", "alt", "src", "mark.svg"],
            "documents": [{
                "nodes": {
                    "nodeType": [9, 1], "nodeName": [0, 1], "nodeValue": [-1, -1],
                    "parentIndex": [-1, 0], "attributes": [[], [2, -1, 3, 4]],
                },
            }],
        }
        root, _assets, _evidence = lower_dom_snapshot(
            snapshot, settled_snapshot=snapshot, screenshot={},
        )
        attributes = root["children"][0]["properties"]["attributes"]
        self.assertEqual(attributes["alt"], "")
        self.assertEqual(attributes["src"], "mark.svg")

    def test_dom_snapshot_rejects_absent_attribute_name(self) -> None:
        """An attribute with no name is malformed; the sentinel stays fatal there."""
        snapshot = {
            "strings": ["#document", "IMG", "alt"],
            "documents": [{
                "nodes": {
                    "nodeType": [9, 1], "nodeName": [0, 1], "nodeValue": [-1, -1],
                    "parentIndex": [-1, 0], "attributes": [[], [-1, 2]],
                },
            }],
        }
        with self.assertRaisesRegex(BackendFailure, "attribute name"):
            lower_dom_snapshot(snapshot, settled_snapshot=snapshot, screenshot={})

    def test_dom_snapshot_rejects_out_of_range_attribute_value(self) -> None:
        """Only -1 is a sentinel; a genuinely out-of-range index stays fatal."""
        snapshot = {
            "strings": ["#document", "IMG", "alt"],
            "documents": [{
                "nodes": {
                    "nodeType": [9, 1], "nodeName": [0, 1], "nodeValue": [-1, -1],
                    "parentIndex": [-1, 0], "attributes": [[], [2, 99]],
                },
            }],
        }
        with self.assertRaisesRegex(BackendFailure, "attribute value"):
            lower_dom_snapshot(snapshot, settled_snapshot=snapshot, screenshot={})

    def test_dom_snapshot_treats_unresolved_computed_style_as_empty(self) -> None:
        """A computed style Chrome could not resolve arrives as the same sentinel."""
        snapshot = {
            "strings": ["#document", "IMG", "block"],
            "documents": [{
                "nodes": {
                    "nodeType": [9, 1], "nodeName": [0, 1], "nodeValue": [-1, -1],
                    "parentIndex": [-1, 0], "attributes": [[], []],
                },
                "layout": {"nodeIndex": [1], "styles": [[2, -1]]},
            }],
        }
        root, _assets, _evidence = lower_dom_snapshot(
            snapshot, settled_snapshot=snapshot, screenshot={},
        )
        computed = root["children"][0]["properties"]["computedStyles"]
        self.assertEqual(computed[CAPTURE_STYLE_NAMES[0]], "block")
        self.assertEqual(computed[CAPTURE_STYLE_NAMES[1]], "")

    def test_empty_pseudo_element_row_cannot_replace_its_painted_box(self) -> None:
        gradient = "radial-gradient(80% 60% at 50% 20%, rgb(0, 0, 0), rgba(0, 0, 0, 0))"
        snapshot = {
            "strings": ["#document", "::before", gradient],
            "documents": [{
                "nodes": {"nodeType": [9, 1], "nodeName": [0, 1], "nodeValue": [-1, -1],
                          "parentIndex": [-1, 0], "attributes": [[], []]},
                "layout": {"nodeIndex": [0, 1, 1], "styles": [[], [-1, -1, -1, -1, 2], [-1, -1, -1, -1, 2]],
                           "bounds": [[0, 0, 800, 600], [5, 6, 80, 60], [0, 0, 0, 0]]},
            }],
        }
        layout = snapshot["documents"][0]["layout"]
        for include_empty_row in (True, False):
            with self.subTest(include_empty_row=include_empty_row):
                if not include_empty_row:
                    for values in layout.values():
                        values.pop()
                root, _assets, _evidence = lower_dom_snapshot(
                    snapshot, settled_snapshot=snapshot, screenshot={})
                properties = root["children"][0]["properties"]
                self.assertEqual(properties["layout"], {"x": 5, "y": 6, "width": 80, "height": 60})
                self.assertEqual(properties["paint"]["backgroundGradients"][0]["radiusX"], 0.8)

    def test_dom_snapshot_lowers_chrome_boxes_relative_to_parent(self) -> None:
        """Each laid-out node keeps Chrome's box as a parent-relative frame."""
        snapshot = {
            "strings": ["#document", "BODY", "#text", "Hi", "  ", "HEAD", "SLOT", "SPAN"],
            "documents": [{
                "nodes": {
                    # document > body > (text "Hi", whitespace, slot > span), document > head
                    "nodeType": [9, 1, 3, 3, 1, 1, 1],
                    "nodeName": [0, 1, 2, 2, 5, 6, 7],
                    "nodeValue": [None, None, 3, 4, None, None, None],
                    "parentIndex": [-1, 0, 1, 1, 0, 1, 5],
                    "attributes": [[], [], [], [], [], [], []],
                },
                "layout": {
                    "nodeIndex": [0, 1, 2, 3, 6],
                    "styles": [[], [], [], [], []],
                    "bounds": [[0, 0, 800, 600], [8, 48, 784, 120.5],
                               [8, 48, 20.25, 18], [0, 0, 0, 0], [30, 70, 10, 12]],
                },
            }],
        }
        root, _assets, _evidence = lower_dom_snapshot(
            snapshot, settled_snapshot=snapshot, screenshot={},
        )
        # The root keeps no size, so the scenario viewport decides it.
        self.assertNotIn("layout", root["properties"])
        body, head = root["children"]
        text, space, slot = body["children"]
        self.assertEqual(body["properties"]["layout"],
                         {"x": 8, "y": 48, "width": 784, "height": 120.5})
        self.assertEqual(text["properties"]["layout"],
                         {"x": 0, "y": 0, "width": 20.25, "height": 18})
        # Collapsed whitespace and display: none have no box and are hidden.
        self.assertEqual(space["properties"]["layout"], {"display": "none"})
        self.assertEqual(head["properties"]["layout"], {"display": "none"})
        # A box-less node with laid-out descendants passes its parent's box
        # through, so the span still lands at Chrome's absolute (30, 70).
        self.assertEqual(slot["properties"]["layout"],
                         {"x": 0, "y": 0, "width": 784, "height": 120.5})
        self.assertEqual(slot["children"][0]["properties"]["layout"],
                         {"x": 22, "y": 22, "width": 10, "height": 12})

    def test_dom_snapshot_rejects_malformed_layout_bounds(self) -> None:
        snapshot = {
            "strings": ["#document"],
            "documents": [{
                "nodes": {
                    "nodeType": [9], "nodeName": [0], "nodeValue": [None],
                    "parentIndex": [-1], "attributes": [[]],
                },
                "layout": {"nodeIndex": [0], "styles": [[]], "bounds": [[0, 0, -1, 10]]},
            }],
        }
        with self.assertRaisesRegex(BackendFailure, "layout bounds"):
            lower_dom_snapshot(snapshot, settled_snapshot=snapshot, screenshot={})

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


class WebCaptureArtifactTests(unittest.TestCase):
    """The web backend's capture command leaves a PNG file a caller can open."""

    PLAN = {
        "schema": "vellum.browser-interaction-plan.v1", "name": "capture-proof",
        "steps": [{"action": "snapshot", "name": "settled", "computedStyles": ["display"]}],
    }

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name).resolve()

    def envelope(self, png: bytes, **screenshot: object) -> dict:
        record = {"mimeType": "image/png", "data": base64.b64encode(png).decode("ascii"),
                  "byteLength": len(png), **screenshot}
        return {
            "schema": "vellum.browser-capture-envelope.v1", "captureId": "c" * 32,
            "source": {"browser": "Chrome/1.2.3", "plan": "capture-proof"},
            "viewport": {"width": 3, "height": 2},
            "root": {"properties": {"captureEvidence": {"screenshot": record}}},
        }

    def png(self) -> bytes:
        pixels = bytes([10, 100, 200, 255]) * 5 + bytes([250, 20, 30, 255])
        return vellum_png.encode_png(vellum_png.RgbaImage(3, 2, pixels))

    def capture(self, *extra: str, envelope: dict | None = None):
        """Run the capture command with the build and the browser stood in for."""
        plan = self.project / "plans/hero.json"
        plan.parent.mkdir(exist_ok=True)
        plan.write_text(json.dumps(self.PLAN), encoding="utf-8")
        seen: list = []

        def browser(build, plan, **_):
            seen.append((build, plan["name"]))
            return envelope if envelope is not None else self.envelope(self.png())

        args = backend_parser("capture").parse_args(
            ["--project", str(self.project), "--json", "--interaction-plan", "plans/hero.json", *extra])
        # runpy hands back a copy of the module's globals; the functions keep the original.
        with mock.patch.dict(command_result.__globals__, {
            "ensure_build": lambda context, sdk, no_build=False: {"root": self.project / "build"},
            "run_chrome_interaction_capture": browser,
        }):
            return command_result("capture", args, {"root": self.project, "slug": "app"}, self.project), seen

    def test_capture_writes_the_browser_screenshot_as_a_png_file(self) -> None:
        result, seen = self.capture()
        self.assertEqual(seen, [(self.project / "build", "capture-proof")])
        output = self.project / "artifacts/hero.png"
        self.assertEqual((result["ok"], result["status"]), (True, "captured"))
        data = result["data"]
        self.assertEqual(data["output"], str(output))
        # The artifact is the file: decoded on its own, it is the browser's image.
        image = vellum_png.read_png(output)
        self.assertEqual((image.width, image.height), (3, 2))
        self.assertEqual(image.pixels[:4], bytes([10, 100, 200, 255]))
        self.assertEqual(image.pixels[-4:], bytes([250, 20, 30, 255]))
        written = output.read_bytes()
        self.assertEqual(written, self.png())
        self.assertEqual((data["bytes"], data["width"], data["height"]), (len(written), 3, 2))
        self.assertEqual(data["sha256"], __import__("hashlib").sha256(written).hexdigest())
        self.assertEqual((data["target"], data["plan"], data["captureId"], data["browser"]),
                         ("web", "capture-proof", "c" * 32, "Chrome/1.2.3"))
        self.assertEqual([path.name for path in output.parent.iterdir()], ["hero.png"])

    def test_capture_is_a_command_of_the_web_backend(self) -> None:
        # Through the backend's own entry point, as the vellum CLI invokes it.
        plan = self.project / "hero.json"
        plan.write_text(json.dumps(self.PLAN), encoding="utf-8")
        emitted: list = []
        main = BACKEND_MODULE["main"]
        with mock.patch.dict(main.__globals__, {
            "project_context": lambda project: {"root": self.project, "slug": "app"},
            "sdk_root": lambda: self.project,
            "ensure_build": lambda context, sdk, no_build=False: {"root": self.project / "build"},
            "run_chrome_interaction_capture": lambda build, plan, **_: self.envelope(self.png()),
            "emit": emitted.append,
        }):
            status = main(["capture", "--project", str(self.project), "--json",
                           "--interaction-plan", "hero.json", "--output", "out/hero.png"])
        self.assertEqual(status, 0, emitted)
        self.assertEqual((emitted[0]["command"], emitted[0]["status"]), ("capture", "captured"))
        self.assertEqual(vellum_png.read_png(self.project / "out/hero.png").width, 3)
        self.assertIn("capture", BACKEND_MODULE["WEB_COMMANDS"])

    def test_capture_honors_the_requested_output(self) -> None:
        result, _ = self.capture("--output", "shots/home.png")
        self.assertEqual(result["data"]["output"], str(self.project / "shots/home.png"))
        self.assertTrue((self.project / "shots/home.png").is_file())
        self.assertFalse((self.project / "artifacts").exists())

    def test_capture_refuses_arguments_it_cannot_honor(self) -> None:
        for extra, fragment in (
            (("--output", "shots/home.jpg"), "must end in .png"),
            (("--output", "../outside.png"), "capture output"),
            (("--scenario", "smoke"), "macOS only"),
            (("--matrix", "m.json"), "macOS only"),
            (("--montage",), "macOS only"),
        ):
            with self.subTest(extra=extra), self.assertRaises(BackendFailure) as raised:
                self.capture(*extra)
            self.assertIn(fragment, str(raised.exception))
        args = backend_parser("capture").parse_args(["--project", str(self.project), "--json"])
        with self.assertRaisesRegex(BackendFailure, "requires --interaction-plan"):
            command_result("capture", args, {"root": self.project, "slug": "app"}, self.project)
        self.assertEqual(list(self.project.rglob("*.png")), [])

    def test_a_capture_without_a_usable_screenshot_fails_and_keeps_the_earlier_file(self) -> None:
        output = self.project / "artifacts/hero.png"
        output.parent.mkdir()
        output.write_bytes(b"earlier capture")
        good = self.png()
        cases = {
            "not a PNG": self.envelope(b"GIF89a" + bytes(40)),
            "wrong type": self.envelope(good, mimeType="image/jpeg"),
            "length differs": self.envelope(good, byteLength=len(good) + 1),
            "not base64": self.envelope(good, data="@@@"),
            "truncated": self.envelope(good[:20]),
            "no pixels": self.envelope(good[:16] + bytes(8) + good[24:]),
            "no screenshot": {"root": {"properties": {"captureEvidence": {}}}},
        }
        for label, envelope in cases.items():
            with self.subTest(label), self.assertRaises(BackendFailure) as raised:
                self.capture(envelope=envelope)
            self.assertEqual(raised.exception.status, "capture_failed")
            self.assertEqual(output.read_bytes(), b"earlier capture")
            self.assertEqual([path.name for path in output.parent.iterdir()], ["hero.png"])

    @live_browser.requires_pinned_chrome
    def test_a_live_browser_capture_is_a_png_of_the_page(self) -> None:
        site = self.project / "site"
        site.mkdir()
        (site / "index.html").write_text(
            "<!doctype html><html><body style='margin:0;background:rgb(10,100,200)'>"
            "<div style='width:40px;height:40px;background:rgb(250,20,30)'></div></body></html>\n",
            encoding="utf-8")
        envelope = run_chrome_interaction_capture(site, validate_interaction_plan(self.PLAN))
        artifact = write_capture_artifact(envelope, self.project / "artifacts/live.png")
        image = vellum_png.read_png(self.project / "artifacts/live.png")
        self.assertEqual((image.width, image.height), (artifact["width"], artifact["height"]))
        viewport = envelope["viewport"]
        self.assertEqual(image.width * viewport["height"], image.height * viewport["width"])
        scale = image.width // viewport["width"]
        self.assertGreaterEqual(scale, 1)

        def pixel(x: int, y: int) -> tuple[int, ...]:
            offset = (y * scale * image.width + x * scale) * 4
            return tuple(image.pixels[offset:offset + 4])

        # The file shows the page the browser drew: the square, then the background.
        self.assertEqual(pixel(20, 20), (250, 20, 30, 255))
        self.assertEqual(pixel(viewport["width"] // 2, viewport["height"] // 2), (10, 100, 200, 255))


class WebNoBuildFreshnessTests(unittest.TestCase):
    """`--no-build` drives only a build whose recorded source digest matches."""

    def built_project(self, root: Path) -> tuple[dict, Path]:
        project = root / "application"
        (project / "src").mkdir(parents=True)
        (project / "src/main.tsx").write_text("export {};\n", encoding="utf-8")
        (project / "app.toml").write_text("name = 'x'\n", encoding="utf-8")
        built = project / ".vellum/build/web"
        built.mkdir(parents=True)
        (built / "build-manifest.json").write_text("{}", encoding="utf-8")
        (built / "vellum_components.json").write_text(json.dumps({
            "schema": "vellum.web-components.v1", "components": [],
        }), encoding="utf-8")
        BACKEND_MODULE["write_source_digest"](built, BACKEND_MODULE["build_input_digest"](project))
        return {"root": project}, built

    def test_package_and_capture_outputs_do_not_move_the_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            context, built = self.built_project(Path(temporary))
            project = context["root"]
            before = BACKEND_MODULE["build_input_digest"](project)
            for name in ("dist-a", "dist-b", "dist", "artifacts"):
                (project / name).mkdir()
                (project / name / "out.bin").write_bytes(b"x")
            self.assertEqual(BACKEND_MODULE["build_input_digest"](project), before)
            self.assertEqual(
                BACKEND_MODULE["ensure_build"](context, Path("/unused"), no_build=True)["root"],
                built)

    def test_a_fresh_build_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            context, built = self.built_project(Path(temporary))
            self.assertEqual(
                BACKEND_MODULE["ensure_build"](context, Path("/unused"), no_build=True)["root"],
                built)

    def test_an_edited_source_without_a_rebuild_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            context, _ = self.built_project(Path(temporary))
            (context["root"] / "src/main.tsx").write_text("export const a = 1;\n", encoding="utf-8")
            with self.assertRaisesRegex(BackendFailure, "stale.*source digest") as caught:
                BACKEND_MODULE["ensure_build"](context, Path("/unused"), no_build=True)
            self.assertEqual(caught.exception.status, "build_stale")

    def test_an_edited_input_outside_the_watched_directories_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            context, _ = self.built_project(Path(temporary))
            helper = context["root"] / "helper.ts"
            helper.write_text("export const a = 1;\n", encoding="utf-8")
            BACKEND_MODULE["write_source_digest"](
                context["root"] / ".vellum/build/web",
                BACKEND_MODULE["build_input_digest"](context["root"]))
            helper.write_text("export const a = 2;\n", encoding="utf-8")
            with self.assertRaisesRegex(BackendFailure, "stale.*source digest") as caught:
                BACKEND_MODULE["ensure_build"](context, Path("/unused"), no_build=True)
            self.assertEqual(caught.exception.status, "build_stale")

    def test_a_source_edited_during_the_build_is_not_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            context, _ = self.built_project(root)
            project = context["root"]
            shutil.rmtree(project / ".vellum")
            sdk = root / "sdk"
            (sdk / "web").mkdir(parents=True)
            for name in ("vellum_web_core.js", "vellum_web_core.wasm", "vellum_host.js",
                         "text_semantics.js", "style.css", "check_wasm_no_engine.py"):
                (sdk / "web" / name).write_text("x", encoding="utf-8")
            (sdk / "web/index.html").write_text("{{APP_NAME}}", encoding="utf-8")
            context.update({
                "entry": project / "src/main.tsx", "slug": "x", "components": [],
                "manifest": {"app": {"name": "x"}},
            })

            def bundle_while_editing(arguments: list[str], **_kwargs: object) -> object:
                if len(arguments) == 4 and arguments[-1].endswith("app.js"):
                    Path(arguments[-1]).write_text("void 0;\n", encoding="utf-8")
                    Path(arguments[-1] + ".map").write_text("{}", encoding="utf-8")
                    (project / "src/main.tsx").write_text("export const b = 1;\n", encoding="utf-8")
                return object()

            with mock.patch.dict(BACKEND_MODULE["build_app"].__globals__, {
                "validate_web_payload": lambda _sdk: {},
                "sdk_node": lambda _sdk: Path("node"),
                "run_checked": bundle_while_editing,
            }):
                with self.assertRaises(BackendFailure) as caught:
                    BACKEND_MODULE["build_app"](context, sdk)
            self.assertEqual(caught.exception.status, "source_changed")
            self.assertFalse((project / ".vellum/build/web").exists())

    def test_a_build_with_no_recorded_digest_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            context, built = self.built_project(Path(temporary))
            (built / "source-digest.json").unlink()
            with self.assertRaisesRegex(BackendFailure, "records no source digest") as caught:
                BACKEND_MODULE["ensure_build"](context, Path("/unused"), no_build=True)
            self.assertEqual(caught.exception.status, "build_stale")
            (built / "source-digest.json").write_text("not json", encoding="utf-8")
            with self.assertRaises(BackendFailure):
                BACKEND_MODULE["ensure_build"](context, Path("/unused"), no_build=True)


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

    COMMAND_SCENARIO = {
        "schema": "vellum.scenario.v2", "name": "commands",
        "steps": [{"action": "wait-for-idle"}, {"action": "command", "target": "item.add"},
                  {"action": "command", "target": "item.rename"}],
    }

    def command_evidence(self, *commands: dict) -> dict:
        evidence = self.evidence(presses=[])
        evidence["commands"] = list(commands)
        return evidence

    @staticmethod
    def ran(target: str, **changes: object) -> dict:
        return {"target": target, "invoked": True, "invocations": 1, "changed": True,
                "before": 1, "after": 2, **changes}

    def test_command_steps_need_evidence_that_they_ran_and_had_an_effect(self) -> None:
        validate_scenario_evidence(
            self.command_evidence(self.ran("item.add"), self.ran("item.rename")),
            scenario=self.COMMAND_SCENARIO)
        refused = {
            "no command evidence": self.evidence(presses=[]),
            "one command unproven": self.command_evidence(self.ran("item.add")),
            "out of order": self.command_evidence(self.ran("item.rename"), self.ran("item.add")),
            "another command": self.command_evidence(self.ran("item.add"), self.ran("item.archive")),
            "ran without an effect": self.command_evidence(
                self.ran("item.add", changed=False), self.ran("item.rename")),
            "not invoked": self.command_evidence(
                self.ran("item.add", invoked=False), self.ran("item.rename")),
            "no handler run counted": self.command_evidence(
                self.ran("item.add", invocations=0), self.ran("item.rename")),
            "an extra unproven entry": self.command_evidence(
                self.ran("item.add"), self.ran("item.rename"), {"target": "item.add"}),
            "evidence is not a list": {**self.evidence(presses=[]), "commands": {"item.add": True}},
        }
        for label, evidence in refused.items():
            with self.subTest(label), self.assertRaises(BackendFailure) as raised:
                validate_scenario_evidence(evidence, scenario=self.COMMAND_SCENARIO)
            self.assertIn("did not prove its command steps", str(raised.exception))
        # A scenario with no command step needs none, and accepts none.
        plain = {"schema": "vellum.scenario.v2", "name": "plain", "steps": [{"action": "wait-for-idle"}]}
        validate_scenario_evidence(self.evidence(presses=[]), scenario=plain)
        with self.assertRaises(BackendFailure):
            validate_scenario_evidence(self.command_evidence(self.ran("item.add")), scenario=plain)

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
