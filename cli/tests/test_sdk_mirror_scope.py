"""SDK payload contamination rules are scoped for mirrored Pulp source."""

from __future__ import annotations

import importlib.util
import io
import re
import sys
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import install_core  # noqa: E402

EXTRACTION_SPEC = importlib.util.spec_from_file_location(
    "verify_extraction", REPO / "tools/provenance/verify_extraction.py"
)
assert EXTRACTION_SPEC and EXTRACTION_SPEC.loader
verify_extraction = importlib.util.module_from_spec(EXTRACTION_SPEC)
EXTRACTION_SPEC.loader.exec_module(verify_extraction)
content_scan_policy = verify_extraction.content_scan_policy

MIRRORED_HEADER = b"#pragma once\n#include <pulp/canvas/canvas.hpp>\nnamespace pulp::view { int PULP_X; }\n"


def rules(name: str, content: bytes) -> set[str]:
    return {
        finding["rule"]
        for finding in install_core.payload_contamination_findings(name, content)
    }


class SdkMirrorScopeTests(unittest.TestCase):
    def test_mirrored_framework_source_is_admitted_in_mirror_scope(self) -> None:
        for name in (
            "sdk/mirror/include/pulp/view/view.hpp",
            "mirror/include/pulp/view/view.hpp",
            "sdk/mirror/core/view/src/view.cpp",
        ):
            self.assertEqual(rules(name, MIRRORED_HEADER), set(), name)

    def test_pulp_names_stay_forbidden_outside_mirror_scope(self) -> None:
        for name in (
            "sdk/include/pulp/view/view.hpp",
            "sdk/mirrorx/include/view.hpp",
            "sdk/include/mirror/view.hpp",
        ):
            self.assertTrue(
                rules(name, MIRRORED_HEADER) & {"pulp-named-payload", "pulp-public-namespace"},
                name,
            )

    def test_mirror_scope_rejects_product_edges(self) -> None:
        cases = {
            b"#include <pulp/audio/buffer.hpp>\n": "mirror-product-include",
            b"#include <pluginterfaces/base/funknown.h>\n": "mirror-product-include",
            b"#import <AudioToolbox/AudioToolbox.h>\n": "mirror-product-include",
            b"auto g = pulp::signal::gain(1.0f);\n": "mirror-product-symbol",
            b"target_link_libraries(x PUBLIC pulp-format-core)\n": "mirror-product-symbol",
            b"target_link_libraries(x \"-framework CoreAudio\")\n": "mirror-audio-framework-link",
            b"#include <pulp/view/eq_curve_view.hpp>\n": "mirror-pulp-kept-include",
            b"#include \"pulp/view/widgets/graph_editor_view.hpp\"\n": "mirror-pulp-kept-include",
            b"#include <pulp/state/content_registry.hpp>\n": "mirror-pulp-kept-include",
        }
        for content, rule in cases.items():
            self.assertIn(rule, rules("sdk/mirror/core/view/src/a.cpp", content), content)

    def test_mirror_scope_rejects_pulp_kept_and_product_paths(self) -> None:
        for name in (
            "sdk/mirror/core/audio/src/engine.cpp",
            "sdk/mirror/include/pulp/signal/gain.hpp",
        ):
            self.assertIn("mirror-product-path", rules(name, b""), name)
        for name in (
            "sdk/mirror/include/pulp/view/eq_curve_view.hpp",
            "sdk/mirror/include/pulp/view/waveform_editor.hpp",
            "sdk/mirror/core/view/include/pulp/view/widgets/graph_editor_view.hpp",
            "mirror/core/state/src/content_registry.cpp",
            # The view-audio headers the Vellum #92 review planted.
            "sdk/mirror/include/pulp/view/audio_widgets.hpp",
            "sdk/mirror/include/pulp/view/register_audio_widgets.hpp",
            "sdk/mirror/include/pulp/view/visualizers.hpp",
            "sdk/mirror/include/pulp/view/visualization_bridge.hpp",
            "sdk/mirror/include/pulp/view/hosted_editor_attachment.hpp",
            "sdk/mirror/core/view/audio/include/pulp/view/visualizers.hpp",
            "sdk/mirror/core/view/src/inspector.cpp",
            "sdk/mirror/Include/Pulp/View/Visualizers.hpp",
            "sdk/mirror/Core/View/Src/Inspector.cpp",
        ):
            self.assertIn("mirror-pulp-kept-file", rules(name, b""), name)
        for name in (
            "sdk/mirror/include/pulp/view/view.hpp",
            "sdk/mirror/include/pulp/view/widgets.hpp",
            "sdk/mirror/include/pulp/view/host_surfaces.hpp",
            "sdk/mirror/core/view/src/inspector_facts.cpp",
        ):
            self.assertEqual(rules(name, b""), set(), name)

    def test_mirror_scope_rejects_pulp_kept_view_audio_edges(self) -> None:
        name = "sdk/mirror/core/view/src/a.cpp"
        for header in ("audio_widgets", "register_audio_widgets", "visualizers",
                       "visualization_bridge", "hosted_editor_attachment", "audio_inspector_panel"):
            for spelled in (f"#include <pulp/view/{header}.hpp>\n", f'#include "pulp/view/{header}.hpp"\n'):
                self.assertIn("mirror-pulp-kept-include", rules(name, spelled.encode()), spelled)
        self.assertIn("mirror-pulp-kept-symbol", rules(name, b"pulp::view::register_audio_widgets();\n"))
        for content in (b"#include <pulp/view/widgets.hpp>\n", b"register_audio_widgets_count = 1;\n",
                        b"#if defined(PULP_VIEW_HAS_AUDIO_WIDGETS)\n"):
            self.assertEqual(rules(name, content), set(), content)

    def test_only_the_recorded_seam_debt_line_is_exempt(self) -> None:
        debt = b"#pragma once\n#if defined(PULP_VIEW_HAS_AUDIO_WIDGETS)\n#include <pulp/view/audio_widgets.hpp>\n#endif\n"
        for name in ("sdk/mirror/core/view/include/pulp/view/widgets.hpp",
                     "mirror/core/view/include/pulp/view/widgets.hpp"):
            self.assertEqual(rules(name, debt), set(), name)
            stream = install_core.inspect_payload_stream(name, io.BytesIO(debt), retain=False)[3]
            self.assertEqual(stream, [], name)
        # The installed header is the same file at its install path.
        self.assertEqual(rules("sdk/mirror/include/pulp/view/widgets.hpp", debt), set())
        self.assertIn("mirror-pulp-kept-include", rules("sdk/mirror/include/pulp/view/label.hpp", debt))
        self.assertIn("mirror-pulp-kept-include", rules("sdk/mirror/include/pulp/widgets.hpp", debt))
        # Another file, another spelling, or another kept header on that file is refused.
        self.assertIn("mirror-pulp-kept-include", rules("sdk/mirror/core/view/src/a.cpp", debt))
        self.assertIn("mirror-pulp-kept-include", rules(
            "sdk/mirror/core/view/include/pulp/view/widgets.hpp", b'#include "pulp/view/audio_widgets.hpp"\n'))
        self.assertIn("mirror-pulp-kept-include", rules(
            "sdk/mirror/core/view/include/pulp/view/widgets.hpp",
            debt + b"#include <pulp/view/register_audio_widgets.hpp>\n"))

    def test_an_exempt_line_must_start_inside_the_scan_window(self) -> None:
        name = "sdk/mirror/core/view/include/pulp/view/widgets.hpp"
        line = b"#include <pulp/view/audio_widgets.hpp>\n"
        rules_ = install_core.MIRROR_PAYLOAD_CONTENT_PATTERNS
        self.assertEqual(install_core.content_rule_findings(name, rules_, line, True), [])
        # A later window that starts mid-line cannot prove the line is the recorded one.
        self.assertEqual([f["rule"] for f in install_core.content_rule_findings(name, rules_, line, False)],
                         ["mirror-pulp-kept-include"])
        self.assertEqual(install_core.content_rule_findings(name, rules_, b"// x\n" + line, False), [])
        # End to end: a longer line cut at a chunk boundary is still refused.
        prefix = b"x" * (1024 * 1024 - 6) + b"\n" + b"int a;"
        stream = install_core.inspect_payload_stream(name, io.BytesIO(prefix + line), retain=False)[3]
        self.assertIn("mirror-pulp-kept-include", {f["rule"] for f in stream})

    def test_installer_edge_rules_are_the_vendored_table(self) -> None:
        import json
        table = json.loads((REPO / "tools/provenance/mirror_edge_patterns.json").read_text(encoding="utf-8"))
        self.assertEqual(install_core.MIRROR_EDGE_CONTENT_PATTERNS, table["content_patterns"])
        self.assertEqual(install_core.MIRROR_EDGE_KEPT_FILE_PATTERN, table["path_patterns"]["mirror-pulp-kept-file"])
        self.assertEqual(
            install_core.MIRROR_EDGE_EXCEPTIONS,
            {(row["rule"], row["path"], row["line"]) for row in table["approved_exceptions"]},
        )
        self.assertEqual(set(table["path_patterns"]), {"mirror-pulp-kept-file"})

    def test_streaming_inspection_uses_scoped_rules(self) -> None:
        def stream_rules(name: str, content: bytes) -> set[str]:
            _size, _digest, _retained, findings = install_core.inspect_payload_stream(
                name, io.BytesIO(content), retain=False
            )
            return {finding["rule"] for finding in findings}

        self.assertEqual(stream_rules("sdk/mirror/include/pulp/view/view.hpp", MIRRORED_HEADER), set())
        self.assertIn(
            "pulp-public-namespace",
            stream_rules("sdk/include/vellum/view.hpp", MIRRORED_HEADER),
        )
        self.assertIn(
            "mirror-product-include",
            stream_rules("sdk/mirror/core/view/src/a.cpp", b"#include <pulp/midi/x.hpp>\n"),
        )

    def test_payload_edge_rules_match_the_repository_verifier(self) -> None:
        source = verify_extraction.FORBIDDEN_MIRROR_EDGE_PATTERNS
        payload = install_core.MIRROR_PAYLOAD_CONTENT_PATTERNS
        shared = set(source) & set(payload)
        self.assertTrue(
            {"mirror-product-include", "mirror-product-symbol", "mirror-audio-framework-link"}
            <= shared
        )
        for name in shared:
            self.assertEqual(payload[name].pattern.decode(), source[name].pattern, name)
            self.assertEqual(
                payload[name].flags & re.IGNORECASE, source[name].flags & re.IGNORECASE, name
            )


    def test_installer_scan_policy_is_the_shared_policy(self) -> None:
        self.assertEqual(install_core.CONTENT_SCAN_SUFFIXES, content_scan_policy.CONTENT_SCAN_SUFFIXES)
        self.assertEqual(install_core.CONTENT_SCAN_BASENAMES, content_scan_policy.CONTENT_SCAN_BASENAMES)
        payload = install_core.FORBIDDEN_PAYLOAD_CONTENT_PATTERNS
        shared = content_scan_policy.FORBIDDEN_NATIVE_CONTENT_PATTERNS
        self.assertEqual(set(payload), set(shared))
        for name, (pattern, flags) in shared.items():
            self.assertEqual(payload[name].pattern.decode("utf-8"), pattern, name)
            self.assertEqual(payload[name].flags, flags, name)
        # The repository verifier compiles the same rules from the same source.
        self.assertEqual(
            {name: rule.pattern for name, rule in verify_extraction.FORBIDDEN_ACTIVE_PATTERNS.items()},
            {name: pattern for name, (pattern, _flags) in shared.items()},
        )
        # Same decision for every path in this checkout, plus the build inputs
        # the native scan once skipped.
        names = [path.relative_to(REPO).as_posix() for path in REPO.rglob("*")
                 if path.is_file() and ".git" not in path.parts]
        names += ["apps/demo/CMakeLists.txt", "apps/demo/config.hpp.in", "templates/x.cpp.template"]
        for name in names:
            self.assertEqual(
                install_core.should_scan_payload_content(name),
                content_scan_policy.should_scan_content(name),
                name,
            )


if __name__ == "__main__":
    unittest.main()
