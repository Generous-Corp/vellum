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
- a step of release-tag.yml reads an Administration-only endpoint with `gh
  api` while its own `env.GH_TOKEN` is anything but the token an earlier step
  of its job minted with the pinned create-github-app-token action, or calls
  `gh api` with an endpoint this check cannot read. Steps are read from the
  workflow's structure, named or not; comments and shell line continuations
  do not change what is read;
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
from dataclasses import dataclass
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

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
# config naming the reviewer signers and sessions it counts, the counted-verdict
# parser it shares with the release readiness open-PR audit and that audit's
# collector (which decides which open pull requests can land before a tag), and the framework
# transfer's judges and the records they hold the transfer to (owned before
# the records exist, so the change that adds them is a release-path change).
# The list of Vellum-native paths the observatory classifies framework-only
# on its own is a policy file too: widening it decides observations. So is the
# code that applies it: the observatory and receiver modules and the other
# verifiers the observatory-verify job runs from the pull request's own checkout,
# and the two framework rule files (the active-source boundary and
# scripts/install_core.py) that the extraction judge passes on a single review.
# The parity completion record and scripts/run_parity_proofs.py, which runs its
# proofs for the receipt a release requires, are policy files too.
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
    "/provenance/authority/expansions/full-design-import-render-v1/parity-completion-1.json",
    "/provenance/framework-name-debt.json",
    "/provenance/framework/external-updates.json",
    "/provenance/framework/transfer.json",
    "/provenance/pulp-observatory/vellum-native-paths.json",
    "/provenance/mirror/link-policy.json",
    "/provenance/release-signers.allowed_signers",
    "/provenance/retired-release-tags.json",
    "/scripts/check_release_policy.py",
    "/scripts/counted_verdicts.py",
    "/scripts/install_core.py",
    "/scripts/pretag_checks.py",
    "/scripts/release_cadence.py",
    "/scripts/release_dry_run.py",
    "/scripts/release_path_review.py",
    "/scripts/release_request.py",
    "/scripts/retired_release_tags.py",
    "/scripts/run_ci_lane.py",
    "/scripts/run_parity_proofs.py",
    "/scripts/verify_immutable_release_preflight.py",
    "/scripts/verify_release_attestation.py",
    "/scripts/verify_release_tag.py",
    "/scripts/version_at_land.py",
    "/tools/provenance/collect_release_evidence.py",
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
VERSION_WORKFLOW = "version-at-land.yml"
RELEASE_WORKFLOWS = (SIGNING_WORKFLOW, "release-policy.yml", VERSION_WORKFLOW)
# Workflows whose secret-holding steps must run no Python at all.
SECRET_ISOLATED = (SIGNING_WORKFLOW, VERSION_WORKFLOW)
# Endpoints only an Administration-read token may call; the Actions token gets 403.
# Endpoints only the GitHub App token (Administration: read) can read.
ADMIN_ENDPOINT = re.compile(r"branches/main/protection|rules/branches/|/rulesets|immutable-releases")
# A `gh api` call: gh by its bare name, by any path (`/usr/bin/gh`, `./gh`), or
# quoted (`"gh"`, `'gh'`). Only a name ending in another word character, `.` or
# `-` (`my-gh`) is another tool. The rest of the line is captured in a
# lookahead, so finditer still finds every later call on the same line
# (`gh api a ; gh api b`, `gh api a | gh api b`).
# Quotes and backslashes may sit anywhere in either word without changing what
# the shell runs: `g\h api`, `g'h' api` and `"g"h 'a'pi` are all
# gh api, so the pattern allows them between every letter.
GH_API = re.compile(
    r"(?<![A-Za-z0-9_.-])[\"'\\]*g[\"'\\]*h[\"'\\]*[ \t]+[\"'\\]*a[\"'\\]*p[\"'\\]*i[\"'\\]*"
    r"(?![A-Za-z0-9_-])(?=(?P<rest>[^\n]*))")
# `gh api` options that take a value, which is therefore not the endpoint.
GH_API_VALUE_OPTIONS = frozenset({
    "-X", "--method", "-H", "--header", "-f", "--raw-field", "-F", "--field", "--input", "-q", "--jq",
    "-t", "--template", "--hostname", "--cache", "-p", "--preview",
})
# A shell redirection word, quotes already removed: an optional fd or `&`, the
# operator, and its operand when attached (`>out.json`, `2>/dev/null`, `&>log`).
# A detached operand is the next word. Neither is the endpoint.
REDIRECTION = re.compile(r"(?:[0-9]+|&)?(?:>>|>\||>&|>|<<<|<<-|<<|<&|<>|<)(?P<operand>.*)", re.DOTALL)
STEP_TOKEN = re.compile(r"\$\{\{\s*steps\.(?P<id>[A-Za-z0-9_-]+)\.outputs\.token\s*\}\}")
APP_TOKEN_ACTION = re.compile(r"actions/create-github-app-token@[0-9a-f]{40}")
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


def load_workflows(root: Path) -> dict[str, str]:
    return {path.name: path.read_text(encoding="utf-8")
            for pattern in WORKFLOW_PATTERNS for path in sorted((root / WORKFLOWS).glob(pattern))}


@dataclass
class Step:
    """A step from a parsed job, with decoded values for secret detection."""
    job: str
    index: int
    text: str
    name: str | None = None
    id: str | None = None
    uses: str | None = None
    run: str | None = None
    env: dict[str, str] | None = None
    shell: str = "bash"

    @property
    def title(self) -> str:
        return f"- name: {self.name}" if self.name else f"step {self.index} of job {self.job} (unnamed)"


@dataclass
class Workflow:
    jobs: dict[str, list[Step]]
    secrets: dict[str, bool]
    environments: list[str]
    reusable: list[str]


ROOT_KEYS = frozenset({"name", "run-name", "on", "permissions", "env", "defaults", "concurrency", "jobs"})
JOB_KEYS = frozenset({"name", "needs", "if", "runs-on", "permissions", "environment", "concurrency",
                      "outputs", "env", "defaults", "steps", "strategy", "timeout-minutes",
                      "continue-on-error", "container", "services", "uses", "with", "secrets"})
STEP_KEYS = frozenset({"name", "id", "if", "uses", "run", "shell", "working-directory", "with", "env",
                       "continue-on-error", "timeout-minutes"})


def parse_workflow(text: str) -> Workflow:
    """Read a conservative Actions subset using YAML tokens and composed nodes.

    BaseLoader keeps Actions' `on` and environment scalars as strings. No YAML
    constructors run. Reject indirection before composition, and duplicates
    before building dictionaries, so neither can replace a checked value.
    """
    if yaml is None:
        raise PolicyError("PyYAML is required; install PyYAML==6.0.3 in the Python environment (including -I)")

    def refuse(reason: str) -> None:
        raise PolicyError(f"unsupported construct: {reason}")

    def scalar(node, label: str) -> str:
        if not isinstance(node, yaml.ScalarNode):
            refuse(f"{label} must be a scalar")
        return node.value

    def mapping(node, label: str, allowed=None, block: bool = True) -> dict:
        if not isinstance(node, yaml.MappingNode) or (block and node.flow_style):
            refuse(f"{label} must be a block mapping")
        result = {}
        for key, value in node.value:
            key_name = scalar(key, f"{label} key")
            if key.style is not None or text[key.end_mark.index:key.end_mark.index + 1] != ":":
                refuse(f"{label} keys must be plain and immediately followed by a colon")
            if allowed is not None and key_name not in allowed:
                refuse(f"{label} has unknown key {key_name!r}")
            result[key_name] = value
        return result

    def strings(node, label: str, multiline: bool = False) -> dict[str, str]:
        result = {}
        for key, value in mapping(node, label).items():
            result[key] = scalar(value, f"{label}.{key}")
            if not multiline and (value.style in ("|", ">") or "\n" in value.value):
                refuse(f"{label}.{key} must be a single-line scalar")
        return result

    def default_shell(fields: dict, inherited: str, label: str) -> str:
        if "defaults" not in fields:
            return inherited
        defaults = mapping(fields["defaults"], f"{label}.defaults", {"run"})
        run = mapping(defaults["run"], f"{label}.defaults.run", {"shell", "working-directory"}) if "run" in defaults else {}
        shell = scalar(run["shell"], f"{label}.defaults.run.shell") if "shell" in run else inherited
        if shell != "bash":
            refuse(f"{label}.defaults.run.shell must be bash")
        return shell

    def contents(node) -> str:
        if isinstance(node, yaml.ScalarNode):
            return node.value
        children = (child for pair in node.value for child in pair) if isinstance(node, yaml.MappingNode) else node.value
        return "\n".join(contents(child) for child in children)

    def unique(node) -> None:
        if isinstance(node, yaml.MappingNode):
            seen = set()
            for key, value in node.value:
                name = scalar(key, "mapping key")
                if name in seen:
                    refuse(f"duplicate {name!r} key")
                seen.add(name)
                unique(value)
        elif isinstance(node, yaml.SequenceNode):
            for value in node.value:
                unique(value)

    try:
        for token in yaml.scan(text, Loader=yaml.BaseLoader):
            if isinstance(token, (yaml.tokens.AnchorToken, yaml.tokens.AliasToken, yaml.tokens.TagToken)):
                refuse("YAML anchor, alias or explicit tag")
            if isinstance(token, yaml.tokens.KeyToken) and text[token.start_mark.index:token.end_mark.index] == "?":
                refuse("explicit YAML key")
        node = yaml.compose(text, Loader=yaml.BaseLoader)
        unique(node)
        root = mapping(node, "workflow", ROOT_KEYS)
        if "jobs" not in root:
            refuse("workflow must have a jobs mapping")
        shell = default_shell(root, "bash", "workflow")
        workflow_env = strings(root["env"], "workflow.env") if "env" in root else {}
        parsed = Workflow({}, {}, [], [])
        for job, body in mapping(root["jobs"], "jobs").items():
            fields = mapping(body, f"job {job}", JOB_KEYS)
            job_shell = default_shell(fields, shell, f"job {job}")
            job_env = strings(fields["env"], f"job {job}.env") if "env" in fields else {}
            parsed.secrets[job] = bool(SECRET.search(contents(body))
                                       or SECRET.search(str(workflow_env)))
            if "environment" in fields:
                environment = fields["environment"]
                if isinstance(environment, yaml.MappingNode):
                    values = mapping(environment, "environment", {"name", "url"}, block=False)
                    if "name" not in values:
                        refuse("environment mapping must have a name")
                    environment = values["name"]
                value = scalar(environment, "environment")
                if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
                    refuse("environment must have a literal name")
                parsed.environments.append(value)
            if "uses" in fields:
                parsed.reusable.append(scalar(fields["uses"], "job uses"))
                if "steps" in fields:
                    refuse("a reusable job cannot have steps")
            parsed.jobs[job] = []
            if "steps" not in fields:
                continue
            sequence = fields["steps"]
            if not isinstance(sequence, yaml.SequenceNode) or sequence.flow_style:
                refuse("steps must be a block sequence")
            ids = set()
            for index, entry in enumerate(sequence.value, 1):
                values = mapping(entry, f"job {job} step {index}", STEP_KEYS)
                step = Step(job, index, contents(entry), shell=job_shell)
                for key in ("name", "id", "uses", "run", "shell"):
                    if key in values:
                        setattr(step, key, scalar(values[key], key))
                if step.id:
                    if step.id in ids:
                        refuse(f"duplicate step id {step.id!r}")
                    ids.add(step.id)
                if step.shell != "bash":
                    refuse("step shell must be bash")
                if (step.run is None) == (step.uses is None):
                    refuse("each step must have exactly one run or uses key")
                if "run" in values:
                    run = values["run"]
                    if run.style not in (None, "|"):
                        refuse("quoted or folded run scalar; use a plain scalar or run: |")
                    if run.style is None and (run.start_mark.line != run.end_mark.line
                                             or run.start_mark.line != next(k.start_mark.line for k, v in entry.value if v is run)):
                        refuse("multiline plain run scalar; use run: |")
                step.env = strings(values["env"], "step.env") if "env" in values else {}
                if "with" in values:
                    strings(values["with"], "step.with", multiline=True)
                # Inherited secrets reach a run even when its own env is empty.
                if SECRET.search(str(workflow_env)) or SECRET.search(str(job_env)):
                    step.text += "\n" + str(workflow_env) + str(job_env)
                parsed.jobs[job].append(step)
        return parsed
    except (yaml.YAMLError, RecursionError) as error:
        raise PolicyError(f"unsupported construct: YAML parse failed: {error}") from error


def environment_problems(workflows: dict[str, str]) -> list[str]:
    found = []
    for name, text in sorted(workflows.items()):
        try:
            environments = parse_workflow(text).environments
        except PolicyError as error:
            found.append(f"{name}: {error}")
            continue
        if name != SIGNING_WORKFLOW and RELEASE_ENVIRONMENT in environments:
            found.append(f"{name} names the `{RELEASE_ENVIRONMENT}` environment; only {SIGNING_WORKFLOW} may")
    return found


def environment_names(workflow: str) -> list[str]:
    try:
        return parse_workflow(workflow).environments
    except PolicyError:
        return []  # The diagnostic is returned by environment_problems.


def step_layout_problems(name: str, workflow: str) -> list[str]:
    try:
        parse_workflow(workflow)
        return []
    except PolicyError as error:
        return [f"{name}: {error}"]


def workflow_steps(workflow: str) -> list[Step]:
    return [step for steps in parse_workflow(workflow).jobs.values() for step in steps]


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
    """A job may call only this repository's own reusable workflows."""
    found = []
    for name, text in sorted(workflows.items()):
        try:
            parsed = parse_workflow(text)
        except PolicyError as error:
            found.append(f"{name}: {error}")
            continue
        for value in parsed.reusable:
            if not value.startswith("./.github/workflows/") or "\n" in value:
                found.append(f"{name} calls a reusable workflow from another repository: {value!r}")
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


def shell_commands(run: str) -> list[str]:
    """A run block's command lines: continuations joined, comment lines dropped."""
    # Shell comments end at their physical newline: a full-line comment ending
    # in `\\` cannot swallow the command on the following line. The shell removes a backslash-newline pair outright, so a continuation
    # inside a word (`immutable-\\` / `releases`) joins the word with no space;
    # a line continued from the one before is never a full-line comment.
    commands: list[str] = []
    pending: str | None = None
    for line in run.splitlines():
        if pending is None and line.lstrip().startswith("#"):
            continue
        trailing = len(line) - len(line.rstrip("\\"))
        continued = trailing % 2 == 1
        pending = (pending or "") + (line[:-1] if continued else line)
        if not continued:
            commands.append(pending)
            pending = None
    if pending is not None:
        commands.append(pending)
    return [line for line in commands if line.strip() and not line.lstrip().startswith("#")]


# Characters that end an unquoted shell word and start an operator or
# redirection: `"$E";` is the word `$E` then `;`, and `x>out` is `x`, `>`, `out`.
OPERATOR_CHARACTERS = frozenset(";&|()<>")


def shell_words(text: str):
    """The shell words of `text`, one at a time, quotes removed. An unquoted
    operator or redirection is a word of its own (`;`, `&&`, `2>&`, `>`), as the
    shell splits it, and `$(...)` stays inside the word it starts. The caller
    stops at the word it wants, so a quote left open further along the line
    (a `$(...)` closing, a multi-line argument) does not matter."""
    word, quote, started, operator, depth, escaped = "", "", False, False, 0, False
    for character in text:
        if not quote and escaped:
            word, started, escaped = word + character, True, False
            continue
        if not quote and character == "\\":
            escaped = True
            continue
        if quote:
            if character == quote:
                quote = ""
            else:
                word += character
            continue
        if depth:
            # Inside an unquoted `$(...)`: operators belong to the substitution.
            depth += {"(": 1, ")": -1}.get(character, 0)
            if character in " \t" and depth:
                yield word
                word = ""
            else:
                word += character
            continue
        if character == "(" and not operator and word.endswith("$"):
            word, depth = word + character, 1
            continue
        if character in OPERATOR_CHARACTERS:
            # A file descriptor (`2>`) or `&` (`&>`) before a redirection is
            # part of it, not a word of its own.
            if started and not operator and not (character in "<>" and word.isdigit()):
                yield word
                word = ""
            word, started, operator = word + character, True, True
            continue
        if operator:
            yield word
            word, started, operator = "", False, False
        if character in "'\"":
            quote, started = character, True
        elif character in " \t":
            if started:
                yield word
            word, started = "", False
        elif character == "#" and not started:
            return
        else:
            word, started = word + character, True
    if started:
        yield word


def gh_api_targets(run: str) -> list[str | None]:
    """The endpoint of each `gh api` call in a run block; None for a call whose
    endpoint cannot be read (no operand, or one built by a substitution)."""
    targets: list[str | None] = []
    for line in shell_commands(run):
        for call in GH_API.finditer(line):
            target = None
            skip = False
            for word in shell_words(call["rest"]):
                if skip:
                    skip = False
                elif (redirection := REDIRECTION.fullmatch(word)) is not None:
                    # Before the operator check: `>` and `2>` are redirections
                    # whose operand is the next word, not the end of the call.
                    skip = not redirection["operand"]
                elif (word in SHELL_OPERATORS or word in {"then", "do", "fi", "done"}
                      or set(word) <= OPERATOR_CHARACTERS):
                    break
                elif word in GH_API_VALUE_OPTIONS:
                    skip = True
                elif not word.startswith("-"):
                    target = word
                    break
            if target is not None and (re.fullmatch(r"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?", target)
                                       or "$(" in target or "`" in target):
                target = None
            targets.append(target)
    return targets


# Only GitHub context expressions are immutable before shell execution.
# Shell variables can be reassigned by run scripts, regardless of step.env.
# The repository is safe only in the owner/repo slot; the SHA is hexadecimal.
SAFE_REPOSITORY_SLOT = re.compile(
    r"^(/?repos/)\$\{\{\s*github\.repository\s*\}\}(?=/|$)")
SAFE_SHA = re.compile(r"\$\{\{\s*github\.sha\s*\}\}")


def dynamic_endpoint(target: str) -> bool:
    """Whether an endpoint contains an expansion whose value is unproved."""
    path = SAFE_REPOSITORY_SLOT.sub(r"\1owner/repo", target)
    path = SAFE_SHA.sub("0" * 40, path)
    return "$" in path or "`" in path


def app_token_problems(name: str, workflow: str) -> list[str]:
    """A step that reads an Administration-only endpoint must hold, as its own
    GH_TOKEN, the token an earlier step of its job minted with the pinned
    create-github-app-token action."""
    try:
        parsed = parse_workflow(workflow)
    except PolicyError as error:
        return [f"{name}: {error}"]
    found = []
    for job_steps in parsed.jobs.values():
        minted: set[str] = set()
        for step in job_steps:
            if step.id and step.uses and APP_TOKEN_ACTION.fullmatch(step.uses):
                minted.add(step.id)
            if step.run is None:
                continue
            if any("$'" in line for line in shell_commands(step.run)):
                found.append(f"{name}: {step.title} uses unsupported ANSI-C shell quoting")
                continue
            targets = gh_api_targets(step.run)
            if None in targets:
                found.append(f"{name}: {step.title} calls gh api with an endpoint this check cannot read")
            # Fail closed: an Administration-only path anywhere after a `gh api`
            # on its line counts, so a word the endpoint parser misreads (a
            # redirection, an option it does not know) cannot hide the read.
            named = any(ADMIN_ENDPOINT.search(call["rest"])
                        for line in shell_commands(step.run) for call in GH_API.finditer(line))
            # Fail closed: an endpoint built from an unresolved expansion
            # (`repos/$R/$ENDPOINT`) may resolve to an Administration-only
            # path, so it needs the App token as well.
            dynamic = any(target and dynamic_endpoint(target) for target in targets)
            if not named and not dynamic and not any(target and ADMIN_ENDPOINT.search(target) for target in targets):
                continue
            token = STEP_TOKEN.fullmatch((step.env or {}).get("GH_TOKEN", ""))
            if token is None or token["id"] not in minted:
                found.append(f"{name}: {step.title} reads an Administration-only endpoint without the App token"
                             if not dynamic or named or any(target and ADMIN_ENDPOINT.search(target)
                                                            for target in targets)
                             else f"{name}: {step.title} calls gh api on an endpoint built from an unresolved "
                                  f"expansion without the App token")
    return found


def token_mutation(run: str) -> bool:
    """Credentials must come unchanged from the reviewed step environment."""
    for line in shell_commands(run):
        words = list(shell_words(line))
        if any(re.search(r"(?<![A-Za-z0-9_])(?:GH_TOKEN|GITHUB_TOKEN)(?:\[[^]]*\])?\+?=", word)
               for word in words):
            return True
        credential = any(re.fullmatch(r"(?:GH_TOKEN|GITHUB_TOKEN)(?:\[[^]]*\])?", word)
                         for word in words)
        writers = {"export", "declare", "typeset", "local", "readonly", "read", "unset", "mapfile", "readarray"}
        if credential and (writers.intersection(words) or ("printf" in words and "-v" in words)):
            return True
    return False


def isolation_problems(workflows: dict[str, str], root: Path) -> list[str]:
    found = []
    for name, text in sorted(workflows.items()):
        try:
            parsed = parse_workflow(text)
        except PolicyError as error:
            found.append(f"{name}: {error}")
            continue
        for job, job_steps in parsed.jobs.items():
            sensitive = parsed.secrets[job] or any(gh_api_targets(step.run or "") for step in job_steps)
            if name == VERSION_WORKFLOW and parsed.secrets[job] and any(
                    PYTHON_CALL.search(step.run or "") for step in job_steps):
                found.append(f"{name}: job {job} holds a secret and runs Python in one of its steps")
            for step in job_steps:
                run = step.run or ""
                if sensitive and token_mutation(run):
                    found.append(f"{name}: {step.title} assigns or exports GH_TOKEN/GITHUB_TOKEN inside run")
                if name in RELEASE_WORKFLOWS:
                    for call in PYTHON_CALL.finditer(run):
                        if "I" not in call["flags"].replace("-", ""):
                            found.append(f"{name}: {step.title} runs `{call.group(0).strip()}` without -I")
                    if name in SECRET_ISOLATED and SECRET.search(step.text) and PYTHON_CALL.search(run):
                        found.append(f"{name}: {step.title} holds a secret and runs Python")
                for call in RENDERER_VERIFIER_CALL.finditer(run):
                    if "I" not in call["flags"].replace("-", ""):
                        found.append(f"{name}: {step.title} runs the renderer verifier without -I")
        if name == SIGNING_WORKFLOW:
            found.extend(app_token_problems(name, text))
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
