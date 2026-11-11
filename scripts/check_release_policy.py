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
- a release workflow runs Python without `-I`, a step of release-tag.yml or
  version-at-land.yml that holds a secret runs Python at all, a job of
  version-at-land.yml that holds a secret runs Python in any step, or a repository
  Python file is named like a standard-library module, so checked-out code
  cannot run with the signing key or an App token;
- the release-policy lane of scripts/ci_lanes.json stops running
  scripts/test_ci_lanes.py or this check, so a release test dropped from every
  lane still fails CI without the manifest being owner-owned;
- release-policy.yml stops running the base branch's copy of this script on
  a pull request's tree (`--from-base`).

With `--from-base`, this copy is the base branch's and `--root` is a pull
request's tree. Every rule above then runs as the base wrote it, so a pull
request cannot weaken the check that judges it. The tree may grow: CODEOWNERS
may own files the base's POLICY_FILES does not name yet (the tree's own copy
holds the two lists equal), a policy script may import one of them, and the
release-policy lane may add entries or keys. It may not lose what the base
has: every base policy file the tree still has stays owned, and every base
lane entry whose script the tree still has keeps its keys, values and
arguments. A deliberate loosening (disowning a file that stays, changing a lane entry's
arguments, moving a test out of the lane) therefore also changes the
release-policy.yml step that runs this, which is a release-path change and
needs both reviews; see Decision 0014.

The base-branch run is an accident guard, not a security boundary
(pulp-planning architecture/2026-11-05-release-path-gate.md, option A): a pull
request still edits the workflow that runs it, and two reviews of that edit
(the `release-path-review` status) are what catch it.

`signer` prints the principal and fingerprint the policy names, for the
release job to verify the tag it just signed.
"""

from __future__ import annotations

import argparse
import json
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
# The signing policy, the code that enforces it, the committed record the
# release decision trusts (the retired tags) and its one loader, the lane
# runner that decides which release-path tests CI runs (the lane manifest is not
# owned; lane_problems holds the release-policy lane's contents instead), the
# workflow that holds an App token to propose each version (version-at-land.yml),
# the release-path review: its rule, its two workflows and the Shipyard
# config naming the reviewer signers and sessions it counts, and the framework
# transfer's judges and the records they hold the transfer to (owned before
# the records exist, so the change that adds them is a release-path change).
# The list of Vellum-native paths the observatory classifies framework-only
# on its own is a policy file too: widening it decides observations. So is the
# code that applies it: the observatory and receiver modules and the other
# verifiers the observatory-verify job runs from the pull request's own checkout,
# and the two framework rule files (the active-source boundary and
# scripts/install_core.py) that the extraction judge passes on a single review.
POLICY_FILES = (
    "/.github/CODEOWNERS",
    "/.github/workflows/release-path-review-trigger.yml",
    "/.github/workflows/release-path-review.yml",
    "/.github/workflows/release-policy.yml",
    "/.github/workflows/release-tag.yml",
    "/.github/workflows/version-at-land.yml",
    "/.shipyard/config.toml",
    "/provenance/active-source-boundary.json",
    "/provenance/authority-signers.allowed_signers",
    "/provenance/framework-name-debt.json",
    "/provenance/framework/external-updates.json",
    "/provenance/framework/transfer.json",
    "/provenance/pulp-observatory/vellum-native-paths.json",
    "/provenance/mirror/link-policy.json",
    "/provenance/release-signers.allowed_signers",
    "/provenance/retired-release-tags.json",
    "/scripts/check_release_policy.py",
    "/scripts/install_core.py",
    "/scripts/pretag_checks.py",
    "/scripts/release_cadence.py",
    "/scripts/release_dry_run.py",
    "/scripts/release_path_review.py",
    "/scripts/release_request.py",
    "/scripts/retired_release_tags.py",
    "/scripts/run_ci_lane.py",
    "/scripts/verify_immutable_release_preflight.py",
    "/scripts/verify_release_attestation.py",
    "/scripts/verify_release_tag.py",
    "/scripts/version_at_land.py",
    "/tools/provenance/content_scan_policy.py",
    "/tools/provenance/framework_trusted_verify.py",
    "/tools/provenance/live_delivery.py",
    "/tools/provenance/observatory.py",
    "/tools/provenance/observatory_common.py",
    "/tools/provenance/observatory_events.py",
    "/tools/provenance/observatory_git.py",
    "/tools/provenance/observatory_native.py",
    "/tools/provenance/observatory_report.py",
    "/tools/provenance/receiver.py",
    "/tools/provenance/mirror_edge_patterns.json",
    "/tools/provenance/mirror_edge_patterns.pin.json",
    "/tools/provenance/verify_authority_expansion.py",
    "/tools/provenance/verify_extraction.py",
    "/tools/provenance/verify_mirror_export.py",
    "/tools/provenance/verify_renderer_dependency.py",
    "/tools/provenance/verify_pulp_observation_scope.py",
)
# GitHub Actions accepts quoted YAML keys and optional whitespace before the
# colon. Keep both spellings in the structural scanner so a release
# environment cannot hide behind valid YAML formatting.
ENVIRONMENT_KEY = re.compile(
    r"^(?P<indent>[ \t]*)(?:environment|['\"]environment['\"])[ \t]*:[ \t]*(?P<value>.*)$",
    re.MULTILINE,
)
ENVIRONMENT_NAME = re.compile(r"\bname:\s*['\"]?(?P<name>[A-Za-z0-9_-]+)")
VERSION_WORKFLOW = "version-at-land.yml"
RELEASE_WORKFLOWS = (SIGNING_WORKFLOW, "release-policy.yml", VERSION_WORKFLOW)
# Workflows whose secret-holding steps must run no Python at all.
SECRET_ISOLATED = (SIGNING_WORKFLOW, VERSION_WORKFLOW)
# Endpoints only an Administration-read token may call; the Actions token gets 403.
ADMIN_READ = re.compile(r"gh api [^\n]*(?:branches/main/protection|rules/branches/|/rulesets|immutable-releases)")
APP_TOKEN = re.compile(r"GH_TOKEN:\s*\$\{\{\s*steps\.(?:app-token|push-token)\.outputs\.token\s*\}\}")
PYTHON_CALL = re.compile(r"\bpython3?\b(?P<flags>(?:\s+-[A-Za-z]+)*)")
RENDERER_VERIFIER_CALL = re.compile(
    r"\bpython3?\b(?P<flags>(?:\s+-[A-Za-z]+)*)\s+(?:\S*/)?verify_renderer_dependency\.py")
IMPORTABLE_SUFFIXES = (".py", ".pyc", ".so", ".pyd")
# Any step output named `token` is a minted credential, whatever the step id.
SECRET = re.compile(r"\$\{\{[^}]*(?:\bsecrets\b|\boutputs\s*(?:\.\s*token\b|\[\s*['\"]token['\"]\s*\]))")
# Directories a release workflow runs Python from: the script's own directory
# and the working directory would be first on sys.path without -I.
PYTHON_DIRS = (".", "scripts", "tools/provenance")
LOCAL_IMPORT = re.compile(r"^[ \t]*(?:from|import)[ \t]+(?P<module>[A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)
# A policy script that loads a module by path (importlib's spec_from_file_location,
# runpy) runs it as surely as an import does. Every `.py` path such a script
# builds (`Path.with_name("x.py")` or `<path> / "x.py"`) is resolved against the
# script's directory and each directory above it.
DYNAMIC_LOAD = re.compile(r"\b(?:spec_from_file_location|run_path|SourceFileLoader)\b")
PY_PATH_LITERAL = re.compile(r"(?:\bwith_name\(\s*|/\s*)[\"']((?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_-]+\.py)[\"']")
# Prefixes scripts/release_path_review.py protects as wholes (PROTECTED_PREFIXES
# there): a module under one already needs both reviews, so a policy script may load it.
REVIEWED_PREFIXES = ("/.github/workflows/", "/.github/actions/", "/tools/mirror/")
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
    "release-tag.yml": 2,
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


def codeowner_problems(codeowners: str | None, root: Path | None = None) -> list[str]:
    """CODEOWNERS and POLICY_FILES are one list. With `root` (this copy is the
    base's, judging a pull request's tree), CODEOWNERS may own more, but every
    base policy file the tree still has stays owned."""
    if codeowners is None:
        return [f"{CODEOWNERS} is missing"]
    owned = {}
    for line in codeowners.splitlines():
        fields = line.split("#", 1)[0].split()
        if fields:
            owned[fields[0]] = fields[1:]
    if root is not None:
        return [f"{CODEOWNERS} does not give the base branch's policy file {path} to {OWNER}"
                for path in POLICY_FILES
                if owned.get(path) != [OWNER] and (root / path.lstrip("/")).exists()]
    found = [f"{CODEOWNERS} does not give {path} to {OWNER}" for path in POLICY_FILES
             if owned.get(path) != [OWNER]]
    return found + [f"{CODEOWNERS} owns {path}, which POLICY_FILES does not name" for path in owned
                    if path not in POLICY_FILES]


def owned_paths(codeowners: str | None) -> frozenset[str]:
    """The anchored paths CODEOWNERS gives to the owner."""
    owned = set()
    for line in (codeowners or "").splitlines():
        fields = line.split("#", 1)[0].split()
        if fields and fields[1:] == [OWNER]:
            owned.add(fields[0])
    return frozenset(owned)


def import_problems(root: Path, also_policy: frozenset[str] = frozenset()) -> list[str]:
    """Repository modules a policy script imports must themselves be policy files.

    `also_policy` names more files that count: from the base, the files the
    tree's CODEOWNERS owns, so a pull request may add a policy module and
    import it in the same change."""
    found = []
    for path in POLICY_FILES:
        source = root / path.lstrip("/")
        if not path.endswith(".py") or not source.is_file():
            continue
        for module in sorted(set(LOCAL_IMPORT.findall(source.read_text(encoding="utf-8")))):
            for directory in PYTHON_DIRS:
                target = f"/{directory}/{module}.py".replace("/./", "/")
                if (root / target.lstrip("/")).is_file() and target not in POLICY_FILES \
                        and target not in also_policy:
                    found.append(f"{path.lstrip('/')} imports {target.lstrip('/')}, which is not a policy file")
        found += dynamic_load_problems(root, path, source, also_policy)
    return found


def dynamic_load_problems(root: Path, path: str, source: Path, also_policy: frozenset[str]) -> list[str]:
    """Modules policy script `path` loads by file path must be policy files too,
    or sit under a prefix the release-path review protects."""
    text = source.read_text(encoding="utf-8")
    if not DYNAMIC_LOAD.search(text):
        return []
    found = []
    resolved_root = root.resolve()
    for literal in sorted(set(PY_PATH_LITERAL.findall(text))):
        directory = source.parent.resolve()
        while True:
            candidate = directory / literal
            if candidate.is_file():
                target = "/" + candidate.resolve().relative_to(resolved_root).as_posix()
                if target not in POLICY_FILES and target not in also_policy \
                        and not target.startswith(REVIEWED_PREFIXES):
                    found.append(f"{path.lstrip('/')} loads {target.lstrip('/')}, which is not a policy file")
            if directory == resolved_root or resolved_root not in directory.parents:
                break
            directory = directory.parent
    return found


def steps(workflow: str) -> list[str]:
    """The workflow's steps, as text blocks starting at `- name:`."""
    return [block for block in re.split(r"\n(?=\s+- name:)", workflow)[1:] if block.strip()]


JOB_HEADER = re.compile(r"^  (?P<name>[A-Za-z0-9_-]+):[ \t]*$", re.MULTILINE)


def jobs(workflow: str) -> dict[str, str]:
    """The workflow's jobs, as text blocks keyed by job id."""
    found = re.search(r"(?:^|\n)jobs:[ \t]*\n", workflow)
    body = workflow[found.end():] if found else ""
    marks = list(JOB_HEADER.finditer(body))
    return {mark["name"]: body[mark.start():(marks[index + 1].start() if index + 1 < len(marks) else None)]
            for index, mark in enumerate(marks)}


def isolation_problems(workflows: dict[str, str], root: Path) -> list[str]:
    found = []
    # A job that holds a token runs no repository Python in any step: an
    # earlier step could otherwise plant code (a hook, GITHUB_PATH, GITHUB_ENV)
    # that a later token-holding step runs.
    if VERSION_WORKFLOW in workflows and not jobs(workflows[VERSION_WORKFLOW]):
        found.append(f"{VERSION_WORKFLOW}: no jobs could be read")
    for job, text in jobs(workflows.get(VERSION_WORKFLOW, "")).items():
        if SECRET.search(text) and PYTHON_CALL.search(text):
            found.append(f"{VERSION_WORKFLOW}: job {job} holds a secret and runs Python in one of its steps")
    for name in RELEASE_WORKFLOWS:
        for step in steps(workflows.get(name, "")):
            title = step.strip().splitlines()[0]
            for call in PYTHON_CALL.finditer(step):
                if "I" not in call["flags"].replace("-", ""):
                    found.append(f"{name}: {title} runs `{call.group(0).strip()}` without -I")
            if name in SECRET_ISOLATED and SECRET.search(step) and PYTHON_CALL.search(step):
                found.append(f"{name}: {title} holds a secret and runs Python")
            if name == SIGNING_WORKFLOW and ADMIN_READ.search(step) and not APP_TOKEN.search(step):
                found.append(f"{name}: {title} reads an Administration-only endpoint without the App token")
    # The renderer verifier decides release eligibility wherever a workflow
    # runs it, so every call must be isolated too.
    for name, text in sorted(workflows.items()):
        for step in steps(text):
            title = step.strip().splitlines()[0]
            for call in RENDERER_VERIFIER_CALL.finditer(step):
                if "I" not in call["flags"].replace("-", ""):
                    found.append(f"{name}: {title} runs the renderer verifier without -I")
    stdlib = set(sys.stdlib_module_names)
    for directory in PYTHON_DIRS:
        for path in sorted((root / directory).iterdir()) if (root / directory).is_dir() else []:
            # A module, a package, or a compiled or extension module all import
            # under the name before the dot.
            module = path.name.split(".", 1)[0]
            importable = (path.is_dir() and (path / "__init__.py").exists()) or (
                path.is_file() and path.suffix in IMPORTABLE_SUFFIXES)
            if importable and module in stdlib:
                found.append(f"{path.relative_to(root)} is named like the standard-library module `{module}`")
    return found


LANE_MANIFEST = Path("scripts/ci_lanes.json")
POLICY_LANE = "release-policy"
# Entries the release-policy lane must keep: the test that every test_*.py sits
# in a lane, and this check.
REQUIRED_LANE_SCRIPTS = ("scripts/test_ci_lanes.py", "scripts/check_release_policy.py")


def policy_lane(root: Path) -> list | None:
    """The release-policy lane's entries, or None when the manifest is unreadable."""
    try:
        lane = json.loads((root / LANE_MANIFEST).read_text(encoding="utf-8"))["lanes"][POLICY_LANE]
    except (ValueError, KeyError, TypeError, OSError):
        return None
    return lane if isinstance(lane, list) else None


def lane_problems(root: Path, baseline: Path | None = None) -> list[str]:
    """The release-policy lane must keep running the lane-coverage test and this check.

    With `baseline` (the base branch's checkout, when its copy judges a pull
    request's tree), the lane must also keep every entry of the base's lane
    whose script the tree still has, with every key, value and argument it had."""
    if not (root / LANE_MANIFEST).is_file():
        return []  # run_ci_lane.py cannot run the required lane without it
    lane = policy_lane(root)
    if lane is None:
        return [f"{LANE_MANIFEST} has no readable `{POLICY_LANE}` lane"]
    scripts = [entry.get("script") for entry in lane if isinstance(entry, dict)]
    found = [f"{LANE_MANIFEST} lane `{POLICY_LANE}` does not run {script}"
             for script in REQUIRED_LANE_SCRIPTS if script not in scripts]
    if baseline is not None:
        base_lane = policy_lane(baseline)
        if base_lane is None:
            return found + [f"the base branch's {LANE_MANIFEST} has no readable `{POLICY_LANE}` lane"]
        for entry in base_lane:
            script = entry.get("script") if isinstance(entry, dict) else None
            retired = isinstance(script, str) and not (root / script).exists()
            # An entry may gain keys (`isolated`), never lose or change one; its
            # arguments stay exactly as they were, since an added argument can
            # change what a test runs.
            kept = any(isinstance(entry, dict) and isinstance(mine, dict)
                       and all(mine.get(key) == value for key, value in entry.items())
                       and mine.get("args") == entry.get("args") for mine in lane)
            if not kept and not retired:
                found.append(f"{LANE_MANIFEST} lane `{POLICY_LANE}` drops or changes the base branch's entry "
                             f"{json.dumps(entry, sort_keys=True)}")
    return found


POLICY_WORKFLOW = "release-policy.yml"
# How release-policy.yml runs the base branch's copy of this check on a pull
# request's tree (`$base` is a checkout of the base commit).
BASE_JUDGE = '"$base/scripts/check_release_policy.py" --root "$GITHUB_WORKSPACE" --from-base check'


def base_judge_problems(workflows: dict[str, str]) -> list[str]:
    text = workflows.get(POLICY_WORKFLOW)
    if text is None:
        return [f"{WORKFLOWS / POLICY_WORKFLOW} is missing"]
    if BASE_JUDGE not in text:
        return [f"{POLICY_WORKFLOW} does not run the base branch's check_release_policy.py on the tree"]
    return []


def problems(root: Path, baseline: Path | None = None) -> list[str]:
    """Every policy violation in `root`. `baseline` is this copy's own
    checkout when it is the base branch's, judging a pull request's tree."""
    workflows = load_workflows(root)
    if SIGNING_WORKFLOW not in workflows:
        return [f"{WORKFLOWS / SIGNING_WORKFLOW} is missing"]
    codeowners_path = root / CODEOWNERS
    codeowners = codeowners_path.read_text(encoding="utf-8") if codeowners_path.is_file() else None
    found = (environment_problems(workflows) + codeowner_problems(codeowners, root if baseline else None)
             + import_problems(root, owned_paths(codeowners) if baseline else frozenset())
             + lane_problems(root, baseline))
    found += base_judge_problems(workflows)
    found += isolation_problems(workflows, root)
    if RELEASE_ENVIRONMENT not in environment_names(workflows[SIGNING_WORKFLOW]):
        found.append(f"{SIGNING_WORKFLOW} does not run in the `{RELEASE_ENVIRONMENT}` environment")
    return found + pin_problems(workflows, root) + reusable_problems(workflows)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("check", "signer"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--from-base", action="store_true",
                        help="this copy is the base branch's; judge the tree at --root against it")
    args = parser.parse_args(argv)
    try:
        if args.command == "signer":
            principal, fingerprint = signer(args.root)
            print(f"principal={principal}\nfingerprint={fingerprint}")
            return 0
        if args.from_base and args.root.resolve() == ROOT.resolve():
            raise PolicyError("--from-base judges another checkout; --root names this one")
        found = problems(args.root, ROOT if args.from_base else None)
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
