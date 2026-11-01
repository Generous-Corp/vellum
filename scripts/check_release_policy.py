#!/usr/bin/env python3
"""Hold the release-signing policy together; the required `release-policy` check.

The release key lives only in the `release` environment, and the signing
policy is a handful of files the owner owns through CODEOWNERS. This check
fails when:

- a workflow other than release-tag.yml names the `release` environment, so
  nothing else can read the key;
- a call to verify_release_tag.py names a signer file other than the release
  or authority file, pins a principal or fingerprint other than that file's,
  pins through a variable outside release-tag.yml, or a workflow that must
  verify release tags has fewer calls than it should, so the verifiers and
  the signer agree on one key per tag namespace;
- a job calls a reusable workflow from another repository, which could name
  the `release` environment where this check cannot read it;
- CODEOWNERS does not give every policy file to the owner, or owns a file
  the policy list does not name, so the two lists stay one inventory;
- a policy script imports a repository module that is not itself a policy
  file, so the release decision never runs code the owner does not own;
- a release workflow runs Python without `-I`, a step that holds a secret runs
  Python at all, or a repository Python file is named like a standard-library
  module, so checked-out code cannot run with the signing key or App token.

`signer` prints the principal and fingerprint the policy names, for the
release job to verify the tag it just signed.
"""

from __future__ import annotations

import argparse
import re
import shlex
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
# The signing policy, the code that enforces it, and the committed records the
# release decision trusts (the immutable-release preflight and the retired tags).
POLICY_FILES = (
    "/.github/CODEOWNERS",
    "/.github/workflows/release-tag.yml",
    "/.github/workflows/release-policy.yml",
    "/provenance/authority-signers.allowed_signers",
    "/provenance/immutable-release-preflight.json",
    "/provenance/release-request.json",
    "/provenance/release-signers.allowed_signers",
    "/provenance/renderer-signers.allowed_signers",
    "/provenance/retired-release-tags.json",
    "/scripts/check_release_policy.py",
    "/scripts/release_request.py",
    "/scripts/verify_immutable_release_preflight.py",
    "/scripts/verify_release_tag.py",
    "/tools/provenance/verify_authority_expansion.py",
)
# GitHub Actions accepts quoted YAML keys and optional whitespace before the
# colon. Keep both spellings in the structural scanner so a release
# environment cannot hide behind valid YAML formatting.
ENVIRONMENT_KEY = re.compile(
    r"^(?P<indent>[ \t]*)(?:environment|['\"]environment['\"])[ \t]*:[ \t]*(?P<value>.*)$",
    re.MULTILINE,
)
ENVIRONMENT_NAME = re.compile(r"\bname:\s*['\"]?(?P<name>[A-Za-z0-9_-]+)")
RELEASE_WORKFLOWS = (SIGNING_WORKFLOW, "release-policy.yml")
# Endpoints only an Administration-read token may call; the Actions token gets 403.
ADMIN_READ = re.compile(r"gh api [^\n]*(?:branches/main/protection|rules/branches/|/rulesets|immutable-releases)")
APP_TOKEN = re.compile(r"GH_TOKEN:\s*\$\{\{\s*steps\.app-token\.outputs\.token\s*\}\}")
PYTHON_CALL = re.compile(r"\bpython3?\b(?P<flags>(?:\s+-[A-Za-z]+)*)")
SECRET = re.compile(r"\$\{\{\s*(?:secrets\.|steps\.app-token\.outputs\.token)")
# Directories a release workflow runs Python from: the script's own directory
# and the working directory would be first on sys.path without -I.
PYTHON_DIRS = (".", "scripts", "tools/provenance")
LOCAL_IMPORT = re.compile(r"^[ \t]*(?:from|import)[ \t]+(?P<module>[A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)
# One signer file per tag namespace; verify_release_tag.py enforces which.
SIGNER_FILES = {
    "release-signers.allowed_signers": ALLOWED_SIGNERS,
    "authority-signers.allowed_signers": Path("provenance/authority-signers.allowed_signers"),
}
VERIFIER = "verify_release_tag.py"
TRUST_FLAGS = ("allowed-signers", "expected-principal", "expected-fingerprint")
# Every flag verify_release_tag.py takes, spelled in full (it refuses abbreviations).
VERIFIER_FLAGS = frozenset({"repo", "repository", "tag-name", "tag-object-sha", "source-commit", *TRUST_FLAGS,
                            "output"})
SHELL_OPERATORS = {";", "&&", "||", "|", "&", ")", "(", ">", ">>", "<", "2>", "<<"}
# Text before a call on its line that keeps it from running as a command.
NOT_RUN = re.compile(r"#|\becho\b|\bprintf\b|\btrue\b|(?:^|\s):\s|\bif\s+false\b")
TRUST_FLAG_NAME = re.compile(r"--(?:allowed-signers|expected-principal|expected-fingerprint)\b")
# The workflows that verify signed tags, and how many calls each must keep.
REQUIRED_VERIFIER_CALLS = {
    "authority-release.yml": 1,
    "gpu-macos.yml": 2,
    "provenance.yml": 2,
    "release-tag.yml": 1,
    "sdk-release.yml": 2,
}
# Any `uses:` key, block or flow, quoted or not, in any case, and its value.
USES = re.compile(r"""(?i)(?:^|[\s{,\-])['"]?uses['"]?[ \t]*:[ \t]*(?P<value>[^,}\n]*)""")


class PolicyError(RuntimeError):
    pass


def signer(root: Path, relative: Path = ALLOWED_SIGNERS) -> tuple[str, str]:
    """The single (principal, fingerprint) an allowed-signers file names."""
    path = root / relative
    rows = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]
    if len(rows) != 1:
        raise PolicyError(f"{relative} must name exactly one release key, not {len(rows)}")
    principal = rows[0].split()[0]
    if "," in principal:
        raise PolicyError(f"{relative} must name exactly one principal")
    listing = subprocess.run(["ssh-keygen", "-lf", str(path)], capture_output=True, text=True, check=False)
    fields = listing.stdout.split()
    if listing.returncode != 0 or len(fields) < 2:
        raise PolicyError(f"ssh-keygen cannot read {relative}: {listing.stderr.strip()}")
    return principal, fields[1]


# GitHub Actions runs both extensions.
WORKFLOW_PATTERNS = ("*.yml", "*.yaml")
SIMPLE_NAME = re.compile(r"['\"]?([A-Za-z0-9_-]+)['\"]?")
BLOCK_SCALAR = re.compile(r":\s*[|>][-+0-9]*\s*(?:#.*)?$")
# YAML anchors, aliases and merge keys can carry an `environment` into a job
# where a line-based reader never sees it, so their use outside block text is
# refused rather than read.
YAML_INDIRECTION = re.compile(r"(?:^|[\s\[{,])(?:&[A-Za-z0-9_-]+|\*[A-Za-z0-9_-]+)(?=[\s,\]}]|$)|^\s*<<\s*:")


# Key spellings the scanner cannot decode to a plain name: explicit `?` keys,
# tagged keys, and double-quoted keys holding escapes ("\x65nvironment").
UNDECODABLE_KEY = re.compile(r"^[ \t]*(?:-[ \t]+)?(?:\?(?:[ \t]|$)|!)|\"[^\"\n]*\\[^\"\n]*\"[ \t]*:")
# Any environment-shaped key, in any case, quoted or not, block or flow.
ANY_ENVIRONMENT_KEY = re.compile(r"(?i)(?:^|[\s{,\-])['\"]?environment['\"]?[ \t]*:")


def load_workflows(root: Path) -> dict[str, str]:
    return {path.name: path.read_text(encoding="utf-8")
            for pattern in WORKFLOW_PATTERNS for path in sorted((root / WORKFLOWS).glob(pattern))}


def structural_lines(workflow: str) -> list[str]:
    """The workflow's lines outside block scalars (`run: |` text and the like)."""
    kept: list[str] = []
    block_indent: int | None = None
    for line in workflow.splitlines():
        indent = len(line) - len(line.lstrip(" "))
        if block_indent is not None:
            if not line.strip() or indent > block_indent:
                continue
            block_indent = None
        kept.append(line)
        if BLOCK_SCALAR.search(line.split(" #", 1)[0].rstrip()):
            block_indent = indent
    return kept


def unreadable_environments(workflow: str) -> list[str]:
    """Environment settings this reader cannot resolve exactly; each is a violation."""
    found = []
    lines = structural_lines(workflow)
    for number, line in enumerate(lines):
        if YAML_INDIRECTION.search(line.split(" #", 1)[0]):
            found.append(f"uses a YAML anchor, alias or merge key: `{line.strip()}`")
        code = line.split(" #", 1)[0]
        if UNDECODABLE_KEY.search(code):
            found.append(f"has a YAML key it cannot decode: `{line.strip()}`")
        match = ENVIRONMENT_KEY.match(line)
        if not match:
            if ANY_ENVIRONMENT_KEY.search(code):
                found.append(f"has an environment key it cannot read: `{line.strip()}`")
            continue
        value = match.group("value").split(" #", 1)[0].strip()
        if value.startswith("{"):
            if not ENVIRONMENT_NAME.search(value) or "${{" in value:
                found.append(f"has an environment it cannot read: `{line.strip()}`")
        elif value:
            if not SIMPLE_NAME.fullmatch(value):
                found.append(f"has an environment it cannot read: `{line.strip()}`")
        else:
            indent = len(match.group("indent"))
            name = None
            for following in lines[number + 1:]:
                if not following.strip() or following.lstrip().startswith("#"):
                    continue
                if len(following) - len(following.lstrip(" \t")) <= indent:
                    break
                named = re.match(r"[ \t]+name:\s*(.*)$", following)
                if named:
                    name = named.group(1).split(" #", 1)[0].strip()
                    break
            if name is None or not SIMPLE_NAME.fullmatch(name):
                found.append(f"has an environment mapping without a plain name: `{line.strip()}`")
    return found


def environment_problems(workflows: dict[str, str]) -> list[str]:
    found = [f"{name} names the `{RELEASE_ENVIRONMENT}` environment; only {SIGNING_WORKFLOW} may"
             for name, text in sorted(workflows.items())
             if name != SIGNING_WORKFLOW and RELEASE_ENVIRONMENT in environment_names(text)]
    found += [f"{name} {problem}" for name, text in sorted(workflows.items())
              for problem in unreadable_environments(text)]
    return found


def environment_names(workflow: str) -> list[str]:
    """Return scalar and mapping-form environment names from a workflow.

    GitHub Actions accepts both ``environment: release`` and a mapping such as
    ``environment:\n  name: release`` (with optional keys before ``name``).
    Keep this small parser fail-closed: an indented ``name`` under an
    environment mapping is treated as its name, while a dedented key ends the
    mapping.
    """
    lines = workflow.splitlines()
    names: list[str] = []
    for match in ENVIRONMENT_KEY.finditer(workflow):
        value = match.group("value").split("#", 1)[0].strip()
        if value:
            if value.startswith("{"):
                inline = ENVIRONMENT_NAME.search(value)
                if inline:
                    names.append(inline.group("name"))
            else:
                scalar = re.fullmatch(r"['\"]?([A-Za-z0-9_-]+)['\"]?", value)
                if scalar:
                    names.append(scalar.group(1))
            continue

        line_number = workflow.count("\n", 0, match.start())
        environment_indent = len(match.group("indent").expandtabs(8))
        for line in lines[line_number + 1:]:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            indent = len(line) - len(line.lstrip(" \t"))
            if indent <= environment_indent:
                break
            mapping_name = re.match(r"[ \t]+name:\s*(.*)$", line)
            if mapping_name:
                candidate = mapping_name.group(1).split("#", 1)[0].strip()
                scalar = re.fullmatch(r"['\"]?([A-Za-z0-9_-]+)['\"]?", candidate)
                if scalar:
                    names.append(scalar.group(1))
                break
    return names


def verifier_calls(workflow: str) -> tuple[list[tuple[dict[str, list[str]], list[str]]], int]:
    """Each verify_release_tag.py call's trust flags and shape problems, and the trust flags seen anywhere.

    A call is read token by token: every argument must be one of the verifier's
    own flags spelled in full, each followed by its value, so an abbreviated,
    repeated or extra argument cannot replace a pinned one.
    """
    joined = re.sub(r"\\\n\s*", " ", workflow)
    calls = []
    for line in joined.splitlines():
        if VERIFIER not in line:
            continue
        before, _, after = line.partition(VERIFIER)
        flags: dict[str, list[str]] = {}
        problems = []
        if NOT_RUN.search(before):
            problems.append("is not run as a command")
        lexer = shlex.shlex(after, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        try:
            tokens = list(lexer)
        except ValueError as error:
            tokens = []
            problems.append(f"cannot be read: {error}")
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token in SHELL_OPERATORS:
                if token == "||":
                    problems.append("can be ignored with ||")
                break
            if not token.startswith("--"):
                problems.append(f"passes an argument that is not a flag value: {token}")
                index += 1
                continue
            name, equals, value = token[2:].partition("=")
            if name not in VERIFIER_FLAGS:
                problems.append(f"passes --{name}, which is not a verifier flag")
            if not equals:
                index += 1
                value = tokens[index] if index < len(tokens) else ""
                if value in SHELL_OPERATORS or value.startswith("--"):
                    problems.append(f"passes --{name} without a value")
                    continue
            if name in TRUST_FLAGS:
                flags.setdefault(name, []).append(value)
            index += 1
        calls.append((flags, problems))
    return calls, len(TRUST_FLAG_NAME.findall(joined))


def pin_problems(workflows: dict[str, str], root: Path) -> list[str]:
    found = []
    signers = {name: signer(root, path) for name, path in SIGNER_FILES.items()}
    for name, text in sorted(workflows.items()):
        calls, seen = verifier_calls(text)
        if seen != sum(len(values) for flags, _ in calls for values in flags.values()):
            found.append(f"{name}: a signer flag appears outside a {VERIFIER} call")
        if len(calls) < REQUIRED_VERIFIER_CALLS.get(name, 0):
            found.append(f"{name}: has {len(calls)} {VERIFIER} calls; it must keep "
                         f"{REQUIRED_VERIFIER_CALLS[name]}")
        for flags, shape in calls:
            found += [f"{name}: a {VERIFIER} call {problem}" for problem in shape]
            values = {key: flags.get(key, []) for key in TRUST_FLAGS}
            if any(len(found_values) != 1 for found_values in values.values()):
                found.append(f"{name}: a {VERIFIER} call must pass each signer flag exactly once")
                continue
            path = values["allowed-signers"][0]
            principal, fingerprint = values["expected-principal"][0], values["expected-fingerprint"][0]
            file_name = path.rsplit("/", 1)[-1]
            if file_name not in SIGNER_FILES or "${{" in path or "$(" in path or "`" in path:
                found.append(f"{name}: {VERIFIER} reads {path}, not a policy signer file")
                continue
            if name == SIGNING_WORKFLOW:
                if (file_name, principal, fingerprint) != (ALLOWED_SIGNERS.name, "$PRINCIPAL", "$FINGERPRINT"):
                    found.append(f"{name}: must verify against {ALLOWED_SIGNERS} with the signer step's pins")
                continue
            expected = signers[file_name]
            if (principal, fingerprint) != expected:
                found.append(f"{name}: pins {principal} {fingerprint}, but {SIGNER_FILES[file_name]} "
                             f"names {expected[0]} {expected[1]}")
    return found


def reusable_problems(workflows: dict[str, str]) -> list[str]:
    """A job may call only this repository's own workflows.

    A called workflow can name the `release` environment where this check
    cannot read it. A `uses:` value this reader cannot see whole (a block
    scalar, a value continued on the next line, an escaped string) is refused.
    """
    found = []
    for name, text in sorted(workflows.items()):
        for line in structural_lines(text):
            for match in USES.finditer(line.split(" #", 1)[0]):
                value = match.group("value").strip()
                plain = value.strip("'\"")
                if not plain or plain[0] in "|>" or "\\" in value:
                    found.append(f"{name} has a `uses:` value it cannot read: `{line.strip()}`")
                elif ".github/workflows/" in plain and not plain.startswith("./"):
                    found.append(f"{name} calls a reusable workflow from another repository: `{line.strip()}`")
    return found


def codeowner_problems(codeowners: str | None) -> list[str]:
    if codeowners is None:
        return [f"{CODEOWNERS} is missing"]
    owned = {}
    for line in codeowners.splitlines():
        fields = line.split("#", 1)[0].split()
        if fields:
            owned[fields[0]] = fields[1:]
    found = [f"{CODEOWNERS} does not give {path} to {OWNER}" for path in POLICY_FILES
             if owned.get(path) != [OWNER]]
    return found + [f"{CODEOWNERS} owns {path}, which POLICY_FILES does not name" for path in owned
                    if path not in POLICY_FILES]


def import_problems(root: Path) -> list[str]:
    """Repository modules a policy script imports must themselves be policy files."""
    found = []
    for path in POLICY_FILES:
        source = root / path.lstrip("/")
        if not path.endswith(".py") or not source.is_file():
            continue
        for module in sorted(set(LOCAL_IMPORT.findall(source.read_text(encoding="utf-8")))):
            for directory in PYTHON_DIRS:
                target = f"/{directory}/{module}.py".replace("/./", "/")
                if (root / target.lstrip("/")).is_file() and target not in POLICY_FILES:
                    found.append(f"{path.lstrip('/')} imports {target.lstrip('/')}, which is not a policy file")
    return found


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
            if name == SIGNING_WORKFLOW and ADMIN_READ.search(step) and not APP_TOKEN.search(step):
                found.append(f"{name}: {title} reads an Administration-only endpoint without the App token")
    stdlib = set(sys.stdlib_module_names)
    for directory in PYTHON_DIRS:
        for path in sorted((root / directory).glob("*.py")):
            if path.stem in stdlib:
                found.append(f"{path.relative_to(root)} is named like the standard-library module `{path.stem}`")
    return found


def problems(root: Path) -> list[str]:
    workflows = load_workflows(root)
    if SIGNING_WORKFLOW not in workflows:
        return [f"{WORKFLOWS / SIGNING_WORKFLOW} is missing"]
    codeowners_path = root / CODEOWNERS
    codeowners = codeowners_path.read_text(encoding="utf-8") if codeowners_path.is_file() else None
    found = environment_problems(workflows) + codeowner_problems(codeowners) + import_problems(root)
    found += isolation_problems(workflows, root)
    if RELEASE_ENVIRONMENT not in environment_names(workflows[SIGNING_WORKFLOW]):
        found.append(f"{SIGNING_WORKFLOW} does not run in the `{RELEASE_ENVIRONMENT}` environment")
    return found + pin_problems(workflows, root) + reusable_problems(workflows)


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
