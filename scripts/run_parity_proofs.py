#!/usr/bin/env python3
"""Execute every declared parity proof and emit a run-bound receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

CPP_PROOF_TESTS = {
    "graphics/tests/design_ir_renderer_test.cpp": (
        "vellum.gpu.design-ir-renderer", r"^vellum\.gpu\.design-ir-renderer$"
    ),
    "graphics/tests/gpu_style_test.cpp": (
        "vellum.gpu.style-fixtures",
        r"^vellum\.gpu\.(packaged_text_fallback_weights|repeating_linear_gradient_non_square|outset_shadow_spread_diagonal|attributed_text_runs_materialization)$",
    ),
    "graphics/tests/skia_raster_surface_test.cpp": (
        "vellum.gpu.skia-raster-surface", r"^vellum\.gpu\.skia-raster-surface$"
    ),
    "graphics/tests/text_shaping_concurrency_test.cpp": (
        "vellum.gpu.text-shaping-concurrency",
        r"^vellum\.gpu\.text-shaping-concurrency$",
    ),
}
ARGUMENT_DRIVEN_PROOF_TESTS = {
    "apps/app-host/test_phase3_scenario.py": (
        "vellum.app-host-phase3-scenario",
        r"^vellum\.app-host-phase3-scenario$",
    ),
    "apps/app-host/test_text_semantics.py": (
        "vellum.app-host-text-ime-accessibility",
        r"^vellum\.app-host-text-ime-accessibility$",
    ),
    "web/tests/run_text_semantics_browser.py": (
        "vellum.web.text-semantics-proofs",
        r"^vellum\.web\.(text-ime-accessibility|phase3-exact-browser)$",
    ),
}
FIXTURE_PROOF_CONSUMERS = {
    "fixtures/design-ir/pulp-emitter-generic.pulp.zip": "cli/tests/test_pulp_zip.py",
}


def command_for(root: Path, proof: dict[str, str]) -> list[str]:
    path = proof["path"]
    runner = proof["runner"]
    test_id = proof["test_id"]
    if runner == "python-file" and path.endswith(".py") and test_id == path:
        return [sys.executable, path]
    if runner == "node-test-file" and Path(path).suffix in {".js", ".mjs"} and test_id == path:
        return ["node", "--test", path]
    ctest_binding = CPP_PROOF_TESTS.get(path) or ARGUMENT_DRIVEN_PROOF_TESTS.get(path)
    if runner == "ctest-case" and ctest_binding and test_id == ctest_binding[0]:
        return [
            "ctest", "--test-dir", "build-gpu", "--output-on-failure",
            "--no-tests=error", "-R", ctest_binding[1],
        ]
    consumer = FIXTURE_PROOF_CONSUMERS.get(path)
    if runner == "fixture-consumer" and consumer and test_id == consumer:
        return [sys.executable, consumer]
    raise ValueError(f"unsupported proof runner binding: {path}")


def run(root: Path, completion: dict, *, run_id: int, head_sha: str) -> dict:
    proofs: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for cell in completion.get("cells", []):
        for proof in cell.get("proof_executions", []):
            key = (proof.get("path"), proof.get("test_id"))
            if key in seen:
                continue
            seen.add(key)
            path = root / proof["path"]
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != proof["sha256"] or proof.get("check") != "gpu-macos-arm64":
                raise ValueError(f"proof binding drift: {proof['path']}")
            command = command_for(root, proof)
            completed = subprocess.run(command, cwd=root, check=False)
            if completed.returncode != 0:
                raise RuntimeError(f"parity proof failed: {proof['path']}")
            proofs.append({
                "path": proof["path"], "sha256": digest,
                "test_id": proof["test_id"], "runner": proof["runner"],
                "status": "pass",
            })
    return {
        "schema_version": 1,
        "kind": "vellum-parity-proof-execution",
        "repository": "Generous-Corp/vellum",
        "head_sha": head_sha,
        "run_id": run_id,
        "check_name": "gpu-macos-arm64",
        "workflow_path": ".github/workflows/gpu-macos.yml",
        "status": "pass",
        "proofs": proofs,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--completion", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(
        args.root.resolve(), json.loads(args.completion.read_text(encoding="utf-8")),
        run_id=int(os.environ["GITHUB_RUN_ID"]), head_sha=os.environ["GITHUB_SHA"],
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
