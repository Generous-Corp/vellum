#!/usr/bin/env python3
"""Exercise every public application template through one installed SDK."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any


SCHEMA = "vellum.templates-smoke.v1"
VARIANTS = ("blank", "imported-app", "cpp-component")


class Error(RuntimeError):
    pass


def run_json(command: list[str], *, cwd: Path, env: dict[str, str]) -> dict[str, Any]:
    completed = subprocess.run(
        command, cwd=cwd, env=env, text=True, capture_output=True, check=False
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise Error(
            f"{command[1:]} returned invalid JSON: {completed.stderr.strip()}"
        ) from error
    if completed.returncode != 0 or payload.get("ok") is not True:
        raise Error(
            f"{command[1:]} failed ({completed.returncode}): "
            f"{payload.get('status')}: {payload.get('message')}"
        )
    return payload


def import_fixture(path: Path) -> None:
    value = {
        "source": {
            "key": "main",
            "namespace": "main",
            "adapter": "figma-plugin",
            "adapterVersion": "1.0.0",
            "formatVersion": "figma-plugin-export-v1",
            "revision": "templates-smoke",
            "snapshotHash": "sha256:templates-smoke",
            "sourceUri": "figma://templates-smoke/root",
        },
        "root": {
            "kind": "view",
            "sourceId": "root",
            "name": "Imported smoke",
            "properties": {
                "layout": {"width": 420, "height": 240},
                "paint": {"backgroundColor": "#0f172a"},
            },
            "children": [{
                "kind": "text",
                "sourceId": "title",
                "name": "Title",
                "text": "Installed SDK import",
                "properties": {"text": {"fontSize": 24}},
                "children": [],
            }],
        },
        "tokens": {},
        "assets": [],
        "diagnostics": [],
    }
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def smoke(install_prefix: Path, output: Path | None = None) -> dict[str, Any]:
    cli = install_prefix / "bin/vellum"
    if not cli.is_file():
        raise Error(f"installed Vellum CLI is missing: {cli}")
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join(
        (str(install_prefix / "bin"), env.get("PATH", ""))
    )
    evidence: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="vellum-templates-smoke-") as temporary:
        root = Path(temporary)
        fixture = root / "imported.designir.json"
        import_fixture(fixture)
        for variant in VARIANTS:
            project = root / variant
            create = [
                str(cli), "--json", "create", f"{variant} smoke",
                "--directory", str(project), "--template", variant,
                "--no-verify",
            ]
            if variant == "imported-app":
                create.extend(("--from", "design-ir", str(fixture), "--as", "main"))
            results = {
                "create": run_json(create, cwd=root, env=env),
                "doctor": run_json(
                    [str(cli), "--json", "doctor", "--fix", "--project", str(project)],
                    cwd=root, env=env,
                ),
                "build": run_json(
                    [str(cli), "--json", "build", "--target", "macos",
                     "--project", str(project)],
                    cwd=root, env=env,
                ),
                "test": run_json(
                    [str(cli), "--json", "test", "--target", "macos",
                     "--scenario", "smoke", "--project", str(project)],
                    cwd=root, env=env,
                ),
            }
            evidence.append({
                "template": variant,
                "selectedTemplate": results["create"]["data"]["template"],
                "statuses": {
                    command: payload["status"] for command, payload in results.items()
                },
            })
    payload = {"schema": SCHEMA, "ok": True, "templates": evidence}
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--install-prefix", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        payload = smoke(args.install_prefix.resolve(), args.output)
    except (Error, OSError) as error:
        if args.json:
            print(json.dumps({
                "schema": SCHEMA, "ok": False, "status": "templates_smoke_failed",
                "message": str(error),
            }, sort_keys=True, separators=(",", ":")))
        else:
            print(f"templates-smoke: FAIL: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        print("templates-smoke: OK (" + ", ".join(VARIANTS) + ")")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
