#!/usr/bin/env python3
"""Contract checks for the iOS RmlUi Metal startup path."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CPU_TEST = ROOT / "rmlui/tests/metal/cpu_vs_metal_test.mm"
METAL_IMPL = ROOT / "rmlui/src/metal_render_interface.mm"
RMLUI_CMAKE = ROOT / "rmlui/CMakeLists.txt"


def require(text: str, needle: str) -> None:
    if needle not in text:
        raise AssertionError(f"missing contract text: {needle!r}")


def main() -> None:
    cpu = CPU_TEST.read_text()
    metal = METAL_IMPL.read_text()
    cmake = RMLUI_CMAKE.read_text()

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
    require(cmake, "metallib")
    require(cmake, "xcrun")


if __name__ == "__main__":
    main()
