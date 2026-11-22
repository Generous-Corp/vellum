"""Build the React package and run its renderer and materialization tests."""

from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
REACT = "packages/pulp-react"
RUNTIME = "tools/import-design/jsx-runtime"


def run(*args: str) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


def main() -> None:
    for package in (REACT, RUNTIME):
        run("npm", "ci", "--prefix", package)
    run("npm", "run", "build", "--prefix", REACT)
    run("npm", "test", "--prefix", REACT)
    tests = [
        str(path.relative_to(ROOT))
        for directory in (RUNTIME, "tools/import-design/browser_capture")
        for path in sorted((ROOT / directory).glob("*.test.mjs"))
    ]
    run("node", "--test", *tests)
    run("node", f"{RUNTIME}/materialized_runtime_conformance.mjs", "--json")


if __name__ == "__main__":
    main()
