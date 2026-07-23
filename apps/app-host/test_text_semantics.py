#!/usr/bin/env python3
"""Run the browser parity fixture through the real native app host."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main() -> int:
    if len(sys.argv) != 4:
        raise SystemExit("usage: test_text_semantics.py HOST SOURCE_ROOT NODE")
    host = Path(sys.argv[1]).resolve()
    source = Path(sys.argv[2]).resolve()
    node = Path(sys.argv[3]).resolve()
    sys.path.insert(0, str(source / "cli"))
    from vellum_native_backend import scenario_arguments  # noqa: PLC0415

    scenario = "web/tests/fixtures/text-semantics-scenario.json"
    arguments, name = scenario_arguments({"root": source}, scenario)
    if name != "browser text selection, IME, and accessibility":
        raise SystemExit(f"unexpected parity scenario: {name}")
    with tempfile.TemporaryDirectory(prefix="vellum-native-text-") as temporary:
        bundle = Path(temporary) / "app.js"
        environment = dict(os.environ)
        environment["VELLUM_BUILD_FORMAT"] = "iife"
        subprocess.run([
            str(node),
            str(source / "packages/vellum-ui/scripts/build-project.mjs"),
            str(source / "web/tests/fixtures/text-semantics-app.tsx"),
            str(bundle),
        ], env=environment, check=True)
        completed = subprocess.run(
            [str(host), "--bundle", str(bundle), "--self-test", *arguments],
            text=True, capture_output=True, check=False,
        )
        if completed.returncode or "text_inputs=1" not in completed.stdout:
            raise SystemExit(
                "native text/IME/accessibility parity proof failed:\n"
                f"{completed.stdout}\n{completed.stderr}"
            )
        print(completed.stdout.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
