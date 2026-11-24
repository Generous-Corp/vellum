"""Evaluates VellumMirror.cmake itself for each declared platform.

The probe in platform_targets/ includes the real module under a forced-compiler
toolchain for the target system, with a stand-in planner and V8 resolver, and
reads back the target properties the module generated. Nothing is compiled.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
PROBE = HERE / "platform_targets"
CC = shutil.which("clang") or shutil.which("cc")
CXX = shutil.which("clang++") or shutil.which("c++")


def link_tree(source: Path, destination: Path, replace: dict[str, Path]) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for entry in source.iterdir():
        if entry.name in ("__pycache__", ".DS_Store"):
            continue
        if entry.name in replace:
            (destination / entry.name).symlink_to(replace[entry.name])
        else:
            (destination / entry.name).symlink_to(entry)


def make_root(tmp: Path) -> Path:
    """A project root whose mirror module is the real one but whose planner and
    V8 resolver are the probe's stand-ins."""
    root = tmp / "root"
    root.mkdir()
    for name in ("provenance", "gpu-runtime"):
        (root / name).symlink_to(REPO / name)
    link_tree(HERE, root / "tools/mirror", {"mirror_package.py": PROBE / "stub_mirror_package.py"})
    framework = root / "framework"
    link_tree(REPO / "framework", framework, {})
    # The framework's tools directory is rebuilt so one resolver can be replaced.
    (framework / "tools").unlink()
    link_tree(REPO / "framework/tools", framework / "tools", {})
    (framework / "tools/cmake").unlink()
    link_tree(REPO / "framework/tools/cmake", framework / "tools/cmake",
              {"VellumV8Provider.cmake": PROBE / "StubV8Provider.cmake"})
    git = ["git", "-C", str(root), "-c", "user.name=probe", "-c", "user.email=probe@example.com"]
    subprocess.run(git + ["init", "-q"], check=True)
    subprocess.run(git + ["commit", "-q", "--allow-empty", "-m", "probe"], check=True)
    return root


def toolchain(system: str, processor: str, path: Path) -> None:
    path.write_text(
        f'set(CMAKE_SYSTEM_NAME {system})\n'
        f'set(CMAKE_SYSTEM_PROCESSOR {processor})\n'
        f'set(CMAKE_C_COMPILER "{CC}")\nset(CMAKE_CXX_COMPILER "{CXX}")\n'
        'set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY)\n', encoding="utf-8")


def probe(tmp: Path, system: str, processor: str, v8: bool) -> dict[tuple[str, str], str]:
    root = make_root(tmp)
    report = tmp / "report.txt"
    command = ["cmake", "-S", str(PROBE), "-B", str(tmp / "build"),
               f"-DPROBE_ROOT={root}", f"-DPROBE_REPORT={report}",
               f"-DPROBE_V8={'ON' if v8 else 'OFF'}", "-DCMAKE_BUILD_TYPE=Release"]
    if system != "Darwin":
        toolchain(system, processor, tmp / "toolchain.cmake")
        command.append(f"-DCMAKE_TOOLCHAIN_FILE={tmp / 'toolchain.cmake'}")
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise AssertionError(f"{system} probe configure failed:\n{result.stdout}\n{result.stderr}")
    properties = {}
    for line in report.read_text(encoding="utf-8").splitlines():
        target, prop, value = line.split("|", 2)
        properties[(target, prop)] = value
    return properties


def v8_provider_path(props) -> str:
    """The provider path the module compiles into pulp-view-script."""
    match = re.search(r'PULP_V8_PROVIDER_PATH=\\*"([^"\\]*)',
                      props[("pulp-view-script", "COMPILE_DEFINITIONS")])
    assert match, "pulp-view-script carries no PULP_V8_PROVIDER_PATH"
    return match.group(1)


@unittest.skipIf(os.name == "nt", "the probe root is assembled from symlinks")
@unittest.skipUnless(CC and CXX, "the probe needs a C and C++ compiler to identify")
class MirrorPlatformTargetTests(unittest.TestCase):
    def run_platform(self, system: str, processor: str, v8: bool = True):
        with tempfile.TemporaryDirectory() as tmp:
            return probe(Path(tmp), system, processor, v8)

    def test_linux_generates_origin_rpath_x11_host_and_so_provider(self) -> None:
        props = self.run_platform("Linux", "x86_64")
        for unit in ("view-core", "view-script", "canvas"):
            rpath = props[(f"vellum-mirror-{unit}", "INSTALL_RPATH")]
            self.assertIn("$ORIGIN", rpath)
            self.assertNotIn("@loader_path", rpath)
            self.assertEqual(props[(f"vellum-mirror-{unit}", "OSX_ARCHITECTURES")], "")
            self.assertNotIn("exported_symbols_list",
                             props[(f"vellum-mirror-{unit}", "LINK_OPTIONS")])
        self.assertEqual(v8_provider_path(props), "libv8.so")
        sources = props[("pulp-view-core", "SOURCES")]
        self.assertIn("gpu_plugin_editor_host_x11.cpp", sources)
        self.assertIn("plugin_view_host.cpp", sources)
        self.assertNotIn("gpu_plugin_editor_host_win.cpp", sources)
        self.assertIn("X11::X11", props[("pulp-view-core", "LINK_LIBRARIES")])
        self.assertIn("Vellum::SkiaDawnProvider",
                      props[("vellum-mirror-canvas", "INTERFACE_LINK_LIBRARIES")])
        self.assertIn("vellum-v8", props[("vellum-skia-dawn-provider", "LINK_LIBRARIES")])

    def test_windows_generates_win32_host_and_dll_provider(self) -> None:
        props = self.run_platform("Windows", "AMD64")
        for unit in ("view-core", "view-script", "canvas"):
            self.assertEqual(props[(f"vellum-mirror-{unit}", "INSTALL_RPATH")], "")
            self.assertEqual(props[(f"vellum-mirror-{unit}", "OSX_ARCHITECTURES")], "")
        self.assertEqual(v8_provider_path(props), "v8.dll")
        sources = props[("pulp-view-core", "SOURCES")]
        self.assertIn("gpu_plugin_editor_host_win.cpp", sources)
        self.assertNotIn("gpu_plugin_editor_host_x11.cpp", sources)
        self.assertIn("Vellum::SkiaDawnProvider",
                      props[("vellum-mirror-canvas", "INTERFACE_LINK_LIBRARIES")])

    def test_darwin_keeps_loader_path_arm64_and_dylib_provider(self) -> None:
        if os.uname().sysname != "Darwin":
            self.skipTest("the Darwin control needs a macOS host")
        props = self.run_platform("Darwin", "arm64")
        self.assertIn("@loader_path", props[("vellum-mirror-view-core", "INSTALL_RPATH")])
        self.assertEqual(props[("vellum-mirror-view-core", "OSX_ARCHITECTURES")], "arm64")
        self.assertEqual(v8_provider_path(props), "@rpath/libv8.dylib")
        self.assertIn("exported_symbols_list",
                      props[("vellum-mirror-canvas", "LINK_OPTIONS")])

    def test_an_undeclared_system_is_refused_by_the_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(AssertionError) as raised:
                probe(Path(tmp), "FreeBSD", "amd64", False)
        self.assertIn("requires a verified platform closure", " ".join(str(raised.exception).split()))


if __name__ == "__main__":
    unittest.main()
