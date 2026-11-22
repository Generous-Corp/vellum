"""Headless contract checks for the Win32 GPU editor host wiring."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "framework/core/view/platform/win/gpu_plugin_editor_host_win.cpp"
MIRROR_CMAKE = ROOT / "tools/mirror/VellumMirror.cmake"


class WinPluginEditorHostContractTests(unittest.TestCase):
    def test_host_owns_the_required_win32_and_gpu_lifecycle(self) -> None:
        source = SOURCE.read_text()
        for required in (
            "CreateWindowExW",
            "SetParent",
            "GetDpiForWindow",
            "GpuSurface::create_dawn",
            "native_surface_handle = hwnd_",
            "set_design_viewport",
            "root_presentation_allowed",
            "present_first_frame",
            "background_rgb_",
        ):
            self.assertIn(required, source)

    def test_mirror_selects_the_host_only_on_windows(self) -> None:
        cmake = MIRROR_CMAKE.read_text()
        self.assertIn("if(TARGET pulp-view-core AND WIN32)", cmake)
        self.assertIn("platform/win/gpu_plugin_editor_host_win.cpp", cmake)
        self.assertNotIn("plugin_view_host_win.cpp", cmake)


if __name__ == "__main__":
    unittest.main()
