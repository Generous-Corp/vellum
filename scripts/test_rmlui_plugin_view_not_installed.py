"""PluginView needs the mirror view unit, so its header must not ship in the SDK.

The SDK payload scan (scripts/install_core.py) rejects `vellum/view/...` includes
outside the mirror scope; merge-group run 45043988226 failed on
sdk/include/vellum/rmlui/plugin_view.hpp for that reason.
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import install_core  # noqa: E402

HEADER = ROOT / "rmlui/plugin_view/include/vellum/rmlui/plugin_view.hpp"
CMAKE = ROOT / "rmlui/CMakeLists.txt"


class PluginViewNotInstalled(unittest.TestCase):
    def test_header_is_contaminating_if_installed(self):
        pattern = install_core.FORBIDDEN_PAYLOAD_CONTENT_PATTERNS["renamed-mirror-namespace"]
        self.assertTrue(pattern.search(HEADER.read_bytes()))

    def test_cmake_does_not_install_header(self):
        text = CMAKE.read_text()
        self.assertIsNone(re.search(r"install\([^)]*plugin_view\.hpp", text, re.S))


if __name__ == "__main__":
    unittest.main()
