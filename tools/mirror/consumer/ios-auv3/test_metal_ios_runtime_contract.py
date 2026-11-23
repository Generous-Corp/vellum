#!/usr/bin/env python3
"""Check the headless iOS Metal runtime contract without launching a simulator."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
CMAKE = ROOT / "tools/mirror/consumer/ios-auv3/CMakeLists.txt"
HARNESS = ROOT / "rmlui/tests/metal/cpu_vs_metal_test.mm"


def main() -> None:
    cmake = CMAKE.read_text()
    harness = HARNESS.read_text()
    runtime_block = cmake[cmake.index("if(VELLUM_RMLUI_IOS_METAL_RUNTIME_TEST)"):]
    assert '"${_vellum_rmlui_rmlui_src}"' in runtime_block
    assert '"${vellum_root}/rmlui" "${vellum_root}/rmlui/tests/fixtures"' not in runtime_block
    # The runtime test must go through the simulator wrapper, never a bare iOS binary.
    assert "run_ios_sim.sh" in runtime_block
    assert '"$<TARGET_FILE:vellum-rmlui-metal-ios-runtime' not in runtime_block
    assert runtime_block.count('MATCHES "simulator"') >= 2
    assert (CMAKE.parent / "run_ios_sim.sh").is_file()
    wrapper = (CMAKE.parent / "run_ios_sim.sh").read_text()
    assert "simctl launch" in wrapper and "exit 77" in wrapper
    # The negative control must prove the exit code AND the pixel message, not WILL_FAIL.
    assert "WILL_FAIL" not in cmake
    control = (CMAKE.parent / "run_ios_sim_negative_control.sh").read_text()
    assert "negative control: expected pixel 255, got" in control
    assert "run_ios_sim_negative_control.sh" in cmake
    assert "${VELLUM_RMLUI_IOS_METAL_RUNTIME_TEST})" in cmake
    for marker in (
        "progress: Metal device",
        "progress: Native context constructed",
        "progress: document loaded",
        "progress: render",
        "progress: readback",
        "progress: comparison",
        "watchdog: stuck in",
    ):
        assert marker in harness, marker


if __name__ == "__main__":
    main()
