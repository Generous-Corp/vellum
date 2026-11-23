#!/usr/bin/env python3
"""Contract checks for the iOS RmlUi Metal startup path."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CPU_TEST = ROOT / "rmlui/tests/metal/cpu_vs_metal_test.mm"
METAL_IMPL = ROOT / "rmlui/src/metal_render_interface.mm"
RMLUI_CMAKE = ROOT / "rmlui/CMakeLists.txt"
IOS_CMAKE = ROOT / "tools/mirror/consumer/ios-auv3/CMakeLists.txt"


def require(text: str, needle: str) -> None:
    if needle not in text:
        raise AssertionError(f"missing contract text: {needle!r}")


def main() -> None:
    cpu = CPU_TEST.read_text()
    metal = METAL_IMPL.read_text()
    cmake = RMLUI_CMAKE.read_text()
    ios_cmake = IOS_CMAKE.read_text()

    metal_pos = cpu.index("MetalRenderInterface metal(device);")
    cpu_pos = cpu.index("CpuRenderInterface cpu;", metal_pos)
    native_pos = cpu.index("NativeContext keepalive(assets, cpu);", cpu_pos)
    require(cpu[metal_pos:], "progress: Metal renderer constructed")
    require(cpu[cpu_pos:], "progress: CPU renderer constructed")
    require(cpu[native_pos:], "progress: Native context constructed")
    if cpu.find("progress: Metal renderer constructed", metal_pos) > cpu.find(
        "CpuRenderInterface cpu;", metal_pos
    ):
        raise AssertionError("Metal progress must follow the Metal construction")
    if cpu.find("progress: CPU renderer constructed", cpu_pos) > cpu.find(
        "NativeContext keepalive(assets, cpu);", cpu_pos
    ):
        raise AssertionError("CPU progress must follow the CPU construction")
    require(metal, "newLibraryWithURL")
    # The installed SDK ships no metallib: iOS must fall back to source compile.
    ios_branch = metal[metal.index("#if TARGET_OS_IPHONE") : metal.index("#else", metal.index("#if TARGET_OS_IPHONE"))]
    require(ios_branch, "newLibraryWithSource")
    require(ios_branch, "fileExistsAtPath")
    require(cmake, "metallib")
    require(cmake, "xcrun")
    require(cmake, 'install(FILES "${_vellum_rmlui_metal_library}"')
    require(cpu, "progress: fonts loaded")
    require(cpu, "NSBundle mainBundle")
    require(cpu, "resourcePath")
    require(cpu, "std::filesystem::is_directory")
    require(ios_cmake, "MACOSX_BUNDLE")
    require(ios_cmake, "TARGET_BUNDLE_DIR")
    require(ios_cmake, "LatoLatin-Regular.ttf")
    require(ios_cmake, "fixtures")
    if ios_cmake.count("TARGET_BUNDLE_DIR") < 2:
        raise AssertionError("both iOS runtime apps must receive bundle resources")


if __name__ == "__main__":
    main()
