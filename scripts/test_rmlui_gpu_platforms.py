#!/usr/bin/env python3
"""Check that the RmlUi GPU proof is wired for both software GPU lanes."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    lock = json.loads((ROOT / "gpu-runtime/renderer-lock.json").read_text(encoding="utf-8"))
    platforms = lock["platforms"]
    for platform in ("linux-x64", "windows-x64"):
        assert platform in platforms, f"renderer lock has no {platform} archive"
        assert platforms[platform]["asset"].endswith("-gpu-release.zip")

    cmake = (ROOT / "cmake/VellumSkiaDawn.cmake").read_text(encoding="utf-8")
    assert "linux-x64" in cmake and "windows-x64" in cmake
    assert "VELLUM_SKIA_PLATFORM" in cmake

    workflow = (ROOT / ".github/workflows/rmlui.yml").read_text(encoding="utf-8")
    for platform, backend in (("Linux", "vulkan"), ("Windows", "d3d12")):
        assert f"name: {platform}" in workflow
        assert "-DVELLUM_RMLUI_VELLUM_RENDERER=ON" in workflow
        assert "-DVELLUM_ENABLE_GRAPHICS=ON" in workflow
        assert "-DVELLUM_ENABLE_GPU=ON" in workflow
        assert f"backend: {backend}" in workflow
    assert "-R '^vellum\\.rmlui\\.vellum-renderer$' --no-tests=error" in workflow
    assert "vellum\\\\.rmlui" not in workflow

    gpu_job = workflow[workflow.index("\n  rmlui-gpu:"):]
    # The GPU renderer's CPU fallback needs the native RmlUi packaging.
    configure = gpu_job[gpu_job.index("Configure the Vellum RmlUi GPU renderer"):
                        gpu_job.index("Build the Vellum RmlUi GPU test")]
    assert "-DVELLUM_RMLUI_NATIVE=ON" in configure
    assert "-DVELLUM_RMLUI_CPU=ON" in configure
    # Git Bash on the Windows runner ships sha256sum, not shasum; the digest
    # check must fail closed.
    assert "sha256sum -c -" in gpu_job
    assert "shasum" not in gpu_job


if __name__ == "__main__":
    main()
