#!/usr/bin/env python3
"""Compile and execute declared app-owned Wasm components through ABI v1."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Iterable


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "cli"))
from vellum_manifest import ManifestError, load_app_manifest, load_components_manifest  # noqa: E402


class WasmProofError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(arguments: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    if Path("/opt/homebrew/bin/python3").is_file():
        environment["PATH"] = f"/opt/homebrew/bin:{environment.get('PATH', '')}"
    completed = subprocess.run(
        arguments, cwd=cwd, text=True, capture_output=True, check=False,
        env=environment,
    )
    if completed.returncode:
        raise WasmProofError(
            f"command failed ({completed.returncode}): {' '.join(arguments)}\n"
            f"{completed.stdout}{completed.stderr}"
        )
    return completed


def discover_emxx(value: Path | None) -> Path:
    candidates = [
        value,
        Path(os.environ["EMSDK"]) / "upstream/emscripten/em++" if os.environ.get("EMSDK") else None,
        Path.home() / "emsdk/upstream/emscripten/em++",
        Path(shutil.which("em++")) if shutil.which("em++") else None,
    ]
    for candidate in candidates:
        if candidate and candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    raise WasmProofError("em++ was not found; activate emsdk or pass --emxx")


def emxx_command(emxx: Path) -> list[str]:
    driver = emxx.with_name("em++.py")
    if not driver.is_file():
        return [str(emxx)]
    python_candidates = [
        Path(sys.executable), Path("/opt/homebrew/bin/python3"),
        Path("/usr/local/bin/python3"),
    ]
    for candidate in python_candidates:
        if not candidate.is_file():
            continue
        version = subprocess.run(
            [str(candidate), "-c", "import sys; print(int(sys.version_info >= (3, 10)))"],
            text=True, capture_output=True, check=False,
        )
        if version.returncode == 0 and version.stdout.strip() == "1":
            return [str(candidate), str(driver)]
    raise WasmProofError("Emscripten requires Python 3.10 or newer")


def verify(project: Path, output: Path, emxx_value: Path | None) -> dict[str, object]:
    project = project.resolve()
    try:
        app = load_app_manifest(project)
        declarations = load_components_manifest(project, app["native"]["components_manifest"])
    except ManifestError as error:
        raise WasmProofError(str(error)) from error
    wasm_components = [item for item in declarations if item["web"] == "wasm"]
    if not wasm_components:
        raise WasmProofError("project declares no web = \"wasm\" component")
    emxx = discover_emxx(emxx_value)
    node = shutil.which("node")
    if node is None:
        raise WasmProofError("Node.js is required to execute the Wasm proof")
    output.mkdir(parents=True, exist_ok=True)
    evidence: list[dict[str, object]] = []
    for item in wasm_components:
        source = project / str(item["wasm_source"])
        javascript = output / f"{item['id']}.js"
        run([
            *emxx_command(emxx), "-std=c++20", "-O2", "-sWASM=1", "-sENVIRONMENT=node",
            "-sFILESYSTEM=0", "-sASSERTIONS=1", "-sEXIT_RUNTIME=1",
            "-I", str(REPO / "components/include"),
            str(REPO / "components/wasm/component_runner.cpp"), str(source),
            "-o", str(javascript),
        ], cwd=REPO)
        wasm = javascript.with_suffix(".wasm")
        executed = run([node, str(javascript)], cwd=output)
        match = re.fullmatch(
            rf"vellum-component-wasm: id={re.escape(str(item['id']))} commands=([1-9][0-9]*) digest=([0-9]+)\n?",
            executed.stdout,
        )
        if not wasm.is_file() or match is None:
            raise WasmProofError(f"Wasm component produced invalid evidence: {executed.stdout!r}")
        evidence.append({
            "id": item["id"], "commands": int(match.group(1)),
            "digest": int(match.group(2)), "javascript_sha256": sha256(javascript),
            "wasm_sha256": sha256(wasm), "web": "wasm",
        })
    return {
        "schema": "vellum.component-wasm-proof.v1", "ok": True,
        "abi_version": 1, "emxx": str(emxx), "components": evidence,
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--emxx", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.output:
            evidence = verify(args.project, args.output.resolve(), args.emxx)
        else:
            with tempfile.TemporaryDirectory(prefix="vellum-component-wasm-") as temporary:
                evidence = verify(args.project, Path(temporary), args.emxx)
    except (OSError, WasmProofError) as error:
        print(f"vellum-component-wasm: {error}", file=sys.stderr)
        return 1
    print(json.dumps(evidence, sort_keys=True, separators=(",", ":")) if args.json
          else f"Verified {len(evidence['components'])} Wasm component(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
