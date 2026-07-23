#!/usr/bin/env python3
"""Run the unchanged Phase 3 scenario through the real native app host."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


def main() -> int:
    if len(sys.argv) != 4:
        raise SystemExit(
            "usage: test_phase3_scenario.py HOST SOURCE_ROOT PHASE3_BUNDLE"
        )
    host = Path(sys.argv[1]).resolve()
    source = Path(sys.argv[2]).resolve()
    bundle = Path(sys.argv[3]).resolve()
    sys.path.insert(0, str(source / "cli"))
    from vellum_native_backend import scenario_arguments  # noqa: PLC0415

    fixture = source / "fixtures/authoring-phase3"
    capabilities = {
        "commands": "v1",
        "files": "denied",
        "clipboard": "text-v1",
        "open_url": "external-v1",
        "network": False,
        "persistence": "state-v1",
    }
    arguments, name = scenario_arguments(
        {"root": fixture, "capabilities": capabilities},
        "scenarios/phase3.json",
    )
    if name != "unchanged authoring fixture on native and browser":
        raise SystemExit(f"unexpected Phase 3 scenario: {name}")
    completed = subprocess.run(
        [str(host), "--bundle", str(bundle), "--self-test", *arguments],
        text=True,
        capture_output=True,
        check=False,
    )
    if (
        completed.returncode
        or "renderer=Skia Graphite backend=Metal fallback=false" not in completed.stdout
        or "text_inputs=1" not in completed.stdout
    ):
        raise SystemExit(
            "native unchanged Phase 3 scenario failed:\n"
            f"{completed.stdout}\n{completed.stderr}"
        )
    capability_json = json.dumps(
        capabilities, sort_keys=True, separators=(",", ":")
    )
    success_response = json.dumps({
        "protocol": "vellum.services.v1",
        "kind": "response",
        "id": "request-negative-control",
        "ok": True,
        "value": None,
    }, sort_keys=True, separators=(",", ":"))
    negative_controls = {
        "unknown-command": ["--command", "missing.command"],
        "wrong-text": ["--assert-text", "item-list", "not present"],
        "unchanged-touch": [
            "--touch", "open", '{"pointerType":"touch"}',
        ],
        "unconsumed-service": [
            "--service-result", "open", success_response,
        ],
        "wrong-throw": [
            "--expected-throw", "mapped-error", "vellum://wrong.tsx",
        ],
    }
    for label, action in negative_controls.items():
        rejected = subprocess.run(
            [
                str(host), "--bundle", str(bundle), "--self-test",
                "--service-capabilities", capability_json, *action,
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if rejected.returncode == 0:
            raise SystemExit(
                f"native Phase 3 negative control unexpectedly passed: {label}"
            )
    print(json.dumps({
        "schema": "vellum.native-scenario-proof.v1",
        "scenario": name,
        "status": "passed",
    }, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
