#!/usr/bin/env python3
"""Ask whether the next release would be refused, before anyone tags it.

    release_canary.sh [MAJOR.MINOR.PATCH]

A real vX.Y.Z tag meets rules a dry run never sees, because the dry run tags
itself as an old waived version. This stages the version bump a release would
land, as one throwaway local commit that is never pushed, and runs
pretag_checks.py against it: the tag pipeline's own content checks, with the
environment a push of the tag gives them. The version defaults to the next
patch release. Exit 0 when every check passes; exit 2 when the live audit could not hold still
(Pulp moved on every attempt: run it again); exit 1, naming the failing
check and printing its output, when the tag would be refused. The checkout is
restored with git afterwards. The one check that reads a commit's GitHub check
runs reads the checkout's own HEAD's, since the throwaway commit has none
(override with $RELEASE_CANARY_CHECK_RUNS_COMMIT when HEAD is itself unpushed).

The checks read the Pulp commits the observatory names, so the checkout also
needs `.exact-boundary-pulp` (Pulp main) and `.observatory-pulp` (the cursor's
commit). Missing ones are cloned from $VELLUM_CANARY_PULP_SOURCE (default
https://github.com/Generous-Corp/pulp.git), and those it cloned are removed
again with `git clean`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import version_at_land  # noqa: E402

PULP_DEFAULT = "https://github.com/Generous-Corp/pulp.git"
CURSOR = "provenance/pulp-observatory/cursor.json"
FAILURE = re.compile(r"^(FAIL  .*|pretag checks refused: .*|.* would fail .*)$", re.MULTILINE)


# provenance.yml's live open-pull-request audit asks to be re-run when Pulp main or its
# open pull requests move while it reads them; that says nothing about the tree.
MOVED = re.compile(r"Re-run this job")


class CanaryError(RuntimeError):
    pass


def next_patch(cmakelists: str) -> str:
    """The next patch release after the project version `cmakelists` declares."""
    current = version_at_land.parse_version(version_at_land.project_version(cmakelists))
    return version_at_land.render(version_at_land.bumped(current, "patch"))


def failing_check(output: str) -> str:
    """The line that names what refused the tag, from pretag_checks.py's output."""
    found = FAILURE.findall(output)
    return found[0] if found else "pretag_checks.py failed without naming a check"


def inconclusive(output: str) -> bool:
    """True when the failure is Pulp moving under the live audit, not a refusal of the tree."""
    return MOVED.search(output) is not None


def git(root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        raise CanaryError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def ensure_pulp(root: Path, name: str, ref: str, source: str) -> bool:
    """Make `root/name` a Pulp checkout at `ref` (a commit, or `main` for the live main); True when created."""
    path = root / name
    live = ref == "main"
    if not live and path.is_dir() and git(path, "rev-parse", "HEAD", check=False) == ref:
        return False
    created = not path.exists()
    if created:
        subprocess.run(["git", "clone", "--quiet", "--no-checkout", source, str(path)], check=True)
    if live or not git(path, "cat-file", "-t", ref, check=False):
        git(path, "fetch", "--quiet", "--no-tags", "origin", ref)
        ref = "FETCH_HEAD"
    git(path, "checkout", "--quiet", "--detach", ref)
    return created


def run(root: Path, version: str) -> int:
    if git(root, "status", "--porcelain", "--untracked-files=no"):
        raise CanaryError("the checkout has uncommitted changes; run the canary on a clean checkout")
    original = git(root, "rev-parse", "HEAD")
    source = os.environ.get("VELLUM_CANARY_PULP_SOURCE", PULP_DEFAULT)
    created: list[str] = []
    try:
        cursor = json.loads((root / CURSOR).read_text(encoding="utf-8"))["pulp"]["last_scanned_commit"]
        if ensure_pulp(root, ".exact-boundary-pulp", "main", source):
            created.append(".exact-boundary-pulp")
        if ensure_pulp(root, ".observatory-pulp", cursor, source):
            created.append(".observatory-pulp")
        version_at_land.apply(root, version)
        git(root, "add", "-u")
        git(root, "-c", "user.name=release-canary", "-c", "user.email=release-canary@invalid",
            "-c", "commit.gpgsign=false", "commit", "--quiet", "--no-verify",
            "-m", f"chore: throwaway bump to {version} for the release canary")
        commit = git(root, "rev-parse", "HEAD")
        with tempfile.TemporaryFile("w+") as log:
            result = subprocess.run([sys.executable, "-I", str(root / "scripts/pretag_checks.py"),
                                     "--tag", f"v{version}", "--commit", commit, "--root", str(root),
                                     "--check-runs-commit",
                                     os.environ.get("RELEASE_CANARY_CHECK_RUNS_COMMIT", original)],
                                    stdout=log, stderr=subprocess.STDOUT, check=False, cwd=root)
            log.seek(0)
            output = log.read()
        if result.returncode == 0:
            print(f"release canary: v{version} would pass every pre-tag content check")
            return 0
        if inconclusive(output):
            print(f"release canary: inconclusive; Pulp main or its open pull requests moved during the "
                  f"live audit on all attempts\n\n{output}", file=sys.stderr)
            return 2
        print(f"release canary: v{version} would be refused\nfailing check: {failing_check(output)}\n\n{output}")
        return 1
    finally:
        git(root, "tag", "-d", f"v{version}", check=False)
        git(root, "reset", "--hard", "--quiet", original, check=False)
        for name in created:
            git(root, "clean", "-ffdxq", "--", name, check=False)


def main(argv: list[str]) -> int:
    if len(argv) > 1 or argv[:1] in (["-h"], ["--help"]):
        print(__doc__)
        return 2
    try:
        version = argv[0] if argv else next_patch((ROOT / "CMakeLists.txt").read_text(encoding="utf-8"))
        version_at_land.parse_version(version)
        return run(ROOT, version)
    except (CanaryError, version_at_land.VersionError, subprocess.CalledProcessError, OSError, KeyError) as error:
        print(f"release canary could not run: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
