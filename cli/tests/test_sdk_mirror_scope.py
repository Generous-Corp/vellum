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
            "sdk/mirror/include/pulp/view/eq_curve_view.hpp",
            "sdk/mirror/include/pulp/view/waveform_editor.hpp",
            "sdk/mirror/core/view/include/pulp/view/widgets/graph_editor_view.hpp",
            "sdk/mirror/core/audio/src/engine.cpp",
            "sdk/mirror/include/pulp/signal/gain.hpp",
            "mirror/core/state/src/content_registry.cpp",
        ):
            self.assertIn("mirror-product-path", rules(name, b""), name)
        self.assertEqual(rules("sdk/mirror/include/pulp/view/view.hpp", b""), set())

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


if __name__ == "__main__":
    unittest.main()
