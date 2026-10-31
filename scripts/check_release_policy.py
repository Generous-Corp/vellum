#!/usr/bin/env python3
"""Hold the release-signing policy together; the required `release-policy` check.

The release key lives only in the `release` environment, and the signing
policy is a handful of files the owner owns through CODEOWNERS. This check
fails when:

- a workflow other than release-tag.yml names the `release` environment, so
  nothing else can read the key;
- an --expected-principal or --expected-fingerprint pin in any workflow
  differs from provenance/release-signers.allowed_signers, so the verifiers
  and the signer agree on one key;
- CODEOWNERS does not give every policy file to the owner;
- a release workflow runs Python without `-I`, a step that holds a secret runs
  Python at all, or a repository Python file is named like a standard-library
  module, so checked-out code cannot run with the signing key or App token.

`signer` prints the principal and fingerprint the policy names, for the
release job to verify the tag it just signed.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = Path(".github/workflows")
ALLOWED_SIGNERS = Path("provenance/release-signers.allowed_signers")
CODEOWNERS = Path(".github/CODEOWNERS")
RELEASE_ENVIRONMENT = "release"
SIGNING_WORKFLOW = "release-tag.yml"
OWNER = "@danielraffel"
# The signing policy and the code that enforces it.
POLICY_FILES = (
    "/.github/CODEOWNERS",
    "/.github/workflows/release-tag.yml",
    "/.github/workflows/release-policy.yml",
    "/provenance/release-signers.allowed_signers",
    "/scripts/check_release_policy.py",
    "/scripts/release_request.py",
    "/scripts/verify_release_tag.py",
)
ENVIRONMENT = re.compile(r"^\s*environment:\s*(?:\{[^}]*\bname:\s*)?['\"]?([A-Za-z0-9_-]+)", re.MULTILINE)
RELEASE_WORKFLOWS = (SIGNING_WORKFLOW, "release-policy.yml")
PYTHON_CALL = re.compile(r"\bpython3?\b(?P<flags>(?:\s+-[A-Za-z]+)*)")
SECRET = re.compile(r"\$\{\{\s*(?:secrets\.|steps\.app-token\.outputs\.token)")
# Directories a release workflow runs Python from: the script's own directory
# and the working directory would be first on sys.path without -I.
PYTHON_DIRS = (".", "scripts")
PIN = re.compile(r"--expected-(principal|fingerprint)(?:\s*\\?\s*\n\s*|\s+)(\S+)")


class PolicyError(RuntimeError):
    pass


def signer(root: Path) -> tuple[str, str]:
    """The single (principal, fingerprint) the allowed-signers file names."""
    path = root / ALLOWED_SIGNERS
    rows = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]
    if len(rows) != 1:
        raise PolicyError(f"{ALLOWED_SIGNERS} must name exactly one release key, not {len(rows)}")
    principal = rows[0].split()[0]
    if "," in principal:
        raise PolicyError(f"{ALLOWED_SIGNERS} must name exactly one principal")
    listing = subprocess.run(["ssh-keygen", "-lf", str(path)], capture_output=True, text=True, check=False)
    fields = listing.stdout.split()
    if listing.returncode != 0 or len(fields) < 2:
        raise PolicyError(f"ssh-keygen cannot read {ALLOWED_SIGNERS}: {listing.stderr.strip()}")
    return principal, fields[1]


def environment_problems(workflows: dict[str, str]) -> list[str]:
    return [f"{name} names the `{RELEASE_ENVIRONMENT}` environment; only {SIGNING_WORKFLOW} may"
            for name, text in sorted(workflows.items())
            if name != SIGNING_WORKFLOW and RELEASE_ENVIRONMENT in ENVIRONMENT.findall(text)]


def pin_problems(workflows: dict[str, str], principal: str, fingerprint: str) -> list[str]:
    expected = {"principal": principal, "fingerprint": fingerprint}
    return [f"{name}: --expected-{kind} {value} differs from {ALLOWED_SIGNERS} ({expected[kind]})"
            for name, text in sorted(workflows.items())
            for kind, value in PIN.findall(text)
            if value.strip("\"'") != expected[kind] and not value.strip("\"'").startswith("$")]


def codeowner_problems(codeowners: str | None) -> list[str]:
    if codeowners is None:
        return [f"{CODEOWNERS} is missing"]
    owned = {}
    for line in codeowners.splitlines():
        fields = line.split("#", 1)[0].split()
        if fields:
            owned[fields[0]] = fields[1:]
    return [f"{CODEOWNERS} does not give {path} to {OWNER}" for path in POLICY_FILES
            if owned.get(path) != [OWNER]]


def steps(workflow: str) -> list[str]:
    """The workflow's steps, as text blocks starting at `- name:`."""
    return [block for block in re.split(r"\n(?=\s+- name:)", workflow)[1:] if block.strip()]


def isolation_problems(workflows: dict[str, str], root: Path) -> list[str]:
    found = []
    for name in RELEASE_WORKFLOWS:
        for step in steps(workflows.get(name, "")):
            title = step.strip().splitlines()[0]
            for call in PYTHON_CALL.finditer(step):
                if "I" not in call["flags"].replace("-", ""):
                    found.append(f"{name}: {title} runs `{call.group(0).strip()}` without -I")
            if name == SIGNING_WORKFLOW and SECRET.search(step) and PYTHON_CALL.search(step):
                found.append(f"{name}: {title} holds a secret and runs Python")
    stdlib = set(sys.stdlib_module_names)
    for directory in PYTHON_DIRS:
        for path in sorted((root / directory).glob("*.py")):
            if path.stem in stdlib:
                found.append(f"{path.relative_to(root)} is named like the standard-library module `{path.stem}`")
    return found


def problems(root: Path) -> list[str]:
    workflows = {path.name: path.read_text(encoding="utf-8") for path in sorted((root / WORKFLOWS).glob("*.yml"))}
    if SIGNING_WORKFLOW not in workflows:
        return [f"{WORKFLOWS / SIGNING_WORKFLOW} is missing"]
    codeowners_path = root / CODEOWNERS
    codeowners = codeowners_path.read_text(encoding="utf-8") if codeowners_path.is_file() else None
    found = environment_problems(workflows) + codeowner_problems(codeowners) + isolation_problems(workflows, root)
    if RELEASE_ENVIRONMENT not in ENVIRONMENT.findall(workflows[SIGNING_WORKFLOW]):
        found.append(f"{SIGNING_WORKFLOW} does not run in the `{RELEASE_ENVIRONMENT}` environment")
    principal, fingerprint = signer(root)
    return found + pin_problems(workflows, principal, fingerprint)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("check", "signer"))
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        if args.command == "signer":
            principal, fingerprint = signer(args.root)
            print(f"principal={principal}\nfingerprint={fingerprint}")
            return 0
        found = problems(args.root)
    except (OSError, PolicyError) as error:
        print(f"release policy: {error}", file=sys.stderr)
        return 1
    if found:
        print("release policy violated:", file=sys.stderr)
        for line in found:
            print(f"  {line}", file=sys.stderr)
        return 1
    print("release policy holds")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
