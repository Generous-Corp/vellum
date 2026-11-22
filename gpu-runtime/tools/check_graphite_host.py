#!/usr/bin/env python3
"""Run a real Graphite pixel probe in modern and SDK-10.12 disposable Intel hosts.

Requires physical Intel macOS and a hardware Metal adapter. The probe itself
rejects missing, null and CPU adapters and checks completed GPU pixel output.
Only a copy of the supplied test executable receives old-SDK metadata. No DAW,
application, installed plug-in, capture permission or system binary is changed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import tempfile
from pathlib import Path


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        digest = hashlib.sha256()
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
        return digest.hexdigest()


def qualify(probe: Path, output: Path) -> dict:
    if platform.system() != "Darwin" or platform.machine() != "x86_64":
        raise ValueError("requires physical Intel macOS; Rosetta is not hardware qualification")
    # platform.machine alone can also report x86_64 under Rosetta.
    translated = subprocess.run(["sysctl", "-in", "sysctl.proc_translated"],
                                capture_output=True, text=True, check=False)
    if translated.returncode == 0 and translated.stdout.strip() == "1":
        raise ValueError("Rosetta execution is not physical Intel qualification")
    output.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="host-", dir=output))
    old = work / "graphite-old-sdk-host"
    subprocess.run(["xcrun", "vtool", "-set-build-version", "macos", "10.9", "10.12",
                    "-replace", "-output", str(old), str(probe)], check=True, capture_output=True)
    subprocess.run(["codesign", "--force", "--sign", "-", str(old)],
                   check=True, capture_output=True)
    report = {"scope": "hardware Graphite shader/draw/readback; not installed product acceptance",
              "architecture": platform.machine(), "macos": platform.mac_ver()[0], "cases": []}
    for name, executable in (("modern_sdk", probe), ("sdk_10_12", old)):
        result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=45)
        report["cases"].append({"host": name, "executable_sha256": sha256(executable),
                                "exit_code": result.returncode, "stdout": result.stdout,
                                "stderr": result.stderr})
        # Persist failures too. An exception/timeout or missing case never produces PASS.
        (work / "result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    report["pass"] = all(case["exit_code"] == 0 and
                         "PASS: hardware Graphite draw and pixel readback" in case["stdout"]
                         for case in report["cases"])
    (work / "result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = qualify(args.probe.resolve(strict=True), args.output.resolve())
    print(json.dumps(result, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
