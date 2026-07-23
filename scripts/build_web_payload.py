#!/usr/bin/env python3
"""Build the exact, checkout-independent Vellum browser runtime payload."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


SCHEMA = "vellum.web-payload.v1"
REPO = Path(__file__).resolve().parents[1]


class WebPayloadError(RuntimeError):
    pass


def run(arguments: list[str], *, cwd: Path | None = None) -> str:
    completed = subprocess.run(arguments, cwd=cwd, text=True, capture_output=True, check=False)
    if completed.returncode:
        raise WebPayloadError(
            f"command failed ({completed.returncode}): {' '.join(arguments)}\n"
            f"{completed.stdout}{completed.stderr}"
        )
    return completed.stdout.strip()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build(repo: Path, output: Path, source_commit: str | None) -> dict[str, object]:
    repo = repo.resolve()
    commit = run(["git", "rev-parse", "HEAD"], cwd=repo)
    if source_commit is not None and source_commit != commit:
        raise WebPayloadError("--source-commit must equal the checked-out Vellum HEAD")
    emcmake = shutil.which("emcmake")
    emcc = shutil.which("emcc")
    if not emcmake or not emcc:
        raise WebPayloadError("an activated Emscripten SDK providing emcmake and emcc is required")
    version = run([emcc, "--version"]).splitlines()[0]
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".vellum-web-payload-", dir=output.parent
    ) as temporary_text:
        temporary = Path(temporary_text)
        build_dir = temporary / "build"
        run([
            emcmake, "cmake", "-S", str(repo), "-B", str(build_dir), "-G", "Ninja",
            "-DCMAKE_BUILD_TYPE=Release", "-DVELLUM_ENABLE_WEB=ON",
            "-DVELLUM_ENABLE_GPU=OFF", "-DVELLUM_ENABLE_AUTHORING=OFF",
            "-DVELLUM_BUILD_SMOKE_NATIVE=OFF", "-DVELLUM_BUILD_TESTS=OFF",
        ])
        run(["cmake", "--build", str(build_dir), "--target", "vellum-web-core", "--parallel"])
        staging = temporary / "payload"
        staging.mkdir()
        built = build_dir / "web-dist"
        for name in ("vellum_web_core.js", "vellum_web_core.wasm"):
            source = built / name
            if not source.is_file():
                raise WebPayloadError(f"Emscripten build omitted {name}")
            shutil.copy2(source, staging / name)
        for name in ("index.html", "style.css", "vellum_host.js"):
            shutil.copy2(repo / "web/consumer" / name, staging / name)
        shutil.copy2(repo / "web/tests/check_wasm_no_engine.py", staging / "check_wasm_no_engine.py")
        run([sys.executable, str(staging / "check_wasm_no_engine.py"), str(staging / "vellum_web_core.wasm")])
        run([sys.executable, str(staging / "check_wasm_no_engine.py"), "--negative-control"])
        files = {
            path.name: {"sha256": sha256(path), "size": path.stat().st_size}
            for path in sorted(staging.iterdir()) if path.is_file()
        }
        manifest: dict[str, object] = {
            "schema": SCHEMA,
            "source_commit": commit,
            "compiler": version,
            "backend": "wasm-shared-cpp-core+canvas2d-shell",
            "files": files,
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        if output.exists():
            shutil.rmtree(output)
        os.replace(staging, output)
    return {**manifest, "output": str(output)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-commit")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        result = build(args.repo, args.output.resolve(), args.source_commit)
    except (OSError, WebPayloadError) as error:
        print(f"vellum-web-payload: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")) if args.json
          else f"Built {result['output']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
