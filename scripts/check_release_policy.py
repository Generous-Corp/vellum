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
- any job of a release workflow runs a step sequence that matches none of its variants in RUN_ALLOWLIST, key for
  key and in order, with the job's and workflow's settings (env, runs-on,
  permissions and every key but name and steps) as its first entry (an
  "unsupported run body"), has an expression in a run
  body, names GITHUB_PATH or GITHUB_ENV (which change what its later steps
  run), uses an action not pinned to a commit, or runs elsewhere (container,
  services, a default working-directory). The check reads no shell: an exact, owner-reviewed
  list replaces analysis of what a body might run. Because the base branch's
  copy judges with the base's list, changing such a step takes two pull
  requests: one adds the new sequence as a variant, the next changes the
  workflow and drops the old variant;
- an allowlisted step names an Administration-only endpoint while its own
  `env.GH_TOKEN` is anything but the token an earlier step of its job minted
  with the pinned create-github-app-token action, any step sets GITHUB_TOKEN
  or a GH_TOKEN that is neither, or a step holding the signing key has a
  GitHub credential in scope;
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
    "/scripts/release_policy_requirements.txt",
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
# Endpoints only the GitHub App token (Administration: read) can read; the
# Actions token gets 403.
# An allowlisted body names one as an API path (`repos/<owner>/<repo>/...`);
# a file named after one (`immutable-releases.json`) is not a read.
ADMIN_ENDPOINT = re.compile(r"repos/[^\s\"']*?/(?:branches/main/protection|rules/branches/|rulesets|immutable-releases)")
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
    # Every key but `name`, as written: `env` and `with` as string mappings,
    # `run` without trailing whitespace. Compared against RUN_ALLOWLIST.
    fields: dict | None = None
    # The workflow and job env the step inherits.
    inherited: dict[str, str] | None = None

    @property
    def title(self) -> str:
        return f"- name: {self.name}" if self.name else f"step {self.index} of job {self.job} (unnamed)"


@dataclass
class Workflow:
    jobs: dict[str, list[Step]]
    secrets: dict[str, bool]
    environments: list[str]
    reusable: list[str]
    # Each job's text (every key and value) with the workflow env, its keys, and the root's keys.
    job_text: dict[str, str] | None = None
    job_keys: dict[str, frozenset[str]] | None = None
    root_keys: frozenset[str] = frozenset()
    # "workflow" and the jobs whose defaults set a working directory for every run step.
    directory_defaults: frozenset[str] = frozenset()
    # Each job's settings that reach every one of its steps: the job's keys but
    # `name` and `steps`, and the workflow's keys but `name`, `run-name` and
    # `jobs`, as plain strings, lists and mappings. Compared against
    # RUN_ALLOWLIST as the first entry of each variant.
    job_context: dict[str, dict] | None = None


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

    directories: set[str] = set()

    def default_shell(fields: dict, inherited: str, label: str) -> str:
        if "defaults" not in fields:
            return inherited
        defaults = mapping(fields["defaults"], f"{label}.defaults", {"run"})
        run = mapping(defaults["run"], f"{label}.defaults.run", {"shell", "working-directory"}) if "run" in defaults else {}
        shell = scalar(run["shell"], f"{label}.defaults.run.shell") if "shell" in run else inherited
        if "working-directory" in run:
            directories.add(label)
        if shell != "bash":
            refuse(f"{label}.defaults.run.shell must be bash")
        return shell

    def plain(node):
        if isinstance(node, yaml.ScalarNode):
            return node.value
        if isinstance(node, yaml.SequenceNode):
            return [plain(child) for child in node.value]
        return {scalar(key, "mapping key"): plain(value) for key, value in node.value}

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
        parsed = Workflow({}, {}, [], [], {}, {}, frozenset(root), job_context={})
        workflow_context = {key: plain(value) for key, value in root.items()
                            if key not in ("name", "run-name", "jobs")}
        for job, body in mapping(root["jobs"], "jobs").items():
            fields = mapping(body, f"job {job}", JOB_KEYS)
            job_shell = default_shell(fields, shell, f"job {job}")
            job_env = strings(fields["env"], f"job {job}.env") if "env" in fields else {}
            parsed.secrets[job] = bool(SECRET.search(contents(body))
                                       or SECRET.search(str(workflow_env)))
            parsed.job_text[job] = contents(body) + "\n" + "\n".join(f"{k}: {v}" for k, v in workflow_env.items())
            parsed.job_keys[job] = frozenset(fields)
            parsed.job_context[job] = {
                "workflow": workflow_context,
                "job": {key: plain(value) for key, value in fields.items() if key not in ("name", "steps")}}
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
                # GitHub environment names are case-insensitive.
                parsed.environments.append(value.lower())
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
                step.inherited = {**workflow_env, **job_env}
                step.fields = {}
                for key, value in values.items():
                    if key in ("env", "with"):
                        step.fields[key] = strings(value, f"step.{key}", multiline=key == "with")
                    elif key == "run":
                        step.fields[key] = scalar(value, key).rstrip()
                    elif key != "name":
                        step.fields[key] = scalar(value, key)
                # Inherited secrets reach a run even when its own env is empty.
                if SECRET.search(str(workflow_env)) or SECRET.search(str(job_env)):
                    step.text += "\n" + str(workflow_env) + str(job_env)
                parsed.jobs[job].append(step)
                # actions/checkout persists the Actions token for Git unless told not to.
                if (step.uses or "").lower().startswith("actions/checkout@") and str(
                        (step.fields.get("with") or {}).get("persist-credentials", "true")).strip().lower() != "false":
                    parsed.job_text[job] += "\n${{ github.token }} # actions/checkout persists credentials"
        parsed.directory_defaults = frozenset(directories)
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


# Every job of a release workflow runs only steps written, key for key, in
# RUN_ALLOWLIST. The check does not read shell: whatever a run body does, it
# does it because the owner reviewed that exact text here. Outside release
# workflows the check does not apply. reaches_credential still decides which
# steps beside the signing key are refused. A job reaches a credential when its text names GH_TOKEN,
# GITHUB_TOKEN or the App-token action, or an expression in it names a secret
# other than the signing key, a token, a step's outputs by index, or the
# github context as a whole.
CREDENTIAL_NAME = re.compile(r"(?i)\b(?:GH|GITHUB)_TOKEN\b|create-github-app-token")
EXPRESSION = re.compile(r"\$\{\{(?P<body>.*?)\}\}", re.DOTALL)
SIGNING_KEY = re.compile(r"(?i)\bsecrets\s*\.\s*RELEASE_TAG_SIGNING_KEY\b")
CREDENTIAL_EXPRESSION = re.compile(
    r"(?i)secrets|token|tojson|\[|\*|\bgithub\b(?!\s*\.\s*[a-z_][a-z0-9_]*)")
# Actions a credential-holding job may use: pinned to a full commit, never a
# local path or a container image, which could run unreviewed code in the job.
PINNED_ACTION = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}")
# Job keys that would run the reviewed steps somewhere else.
UNSUPPORTED_CREDENTIAL_JOB_KEYS = frozenset({"container", "services"})
ACTIONS_TOKEN = "${{ github.token }}"
# A step that writes these changes what every later step of its job runs: a
# directory put on PATH can shadow git, gh or ssh-keygen while the key or a
# token is held. No reviewed job names them, in a run body or as an env key.
PATH_OR_ENV_WRITES = ("GITHUB_PATH", "GITHUB_ENV", "::add-path", "::set-env", "ACTIONS_ALLOW_UNSECURE_COMMANDS")
ACTIONS_TOKEN_REFERENCE = re.compile(r"(?i)\bgithub\s*(?:\.\s*token\b|\[)")


def reaches_credential(text: str) -> bool:
    if CREDENTIAL_NAME.search(text):
        return True
    return any(CREDENTIAL_EXPRESSION.search(SIGNING_KEY.sub("", expression["body"]))
               for expression in EXPRESSION.finditer(text))


def reviewed_job(text: str, workflow: str | None = None) -> bool:
    """A job RUN_ALLOWLIST must list: every job of a release workflow, and
    elsewhere one that reaches a GitHub credential or holds the signing key.

    Text cannot tell which jobs hold a credential: an action receives the
    Actions token through a default input (actions/github-script) without
    the workflow naming it, and Python beside a secret is found only by
    spelling (`p'y'thon3` runs python3). So no job of a release workflow is
    classified; each is reviewed text, including the ones that run this
    check with its third-party parser, apart from the key."""
    return (workflow in RELEASE_WORKFLOWS or reaches_credential(text)
            or SIGNING_KEY.search(text) is not None)


def allowlist_entries(name: str, workflow: str) -> dict[str, list[dict]]:
    """Each credential-holding job's context and steps, in order: the variant RUN_ALLOWLIST would list for it."""
    parsed = parse_workflow(workflow)
    return {job: [parsed.job_context[job], *(step.fields for step in steps)]
            for job, steps in parsed.jobs.items() if reviewed_job(parsed.job_text[job], name)}


def _body(text: str) -> str:
    """A run body written on the lines after its opening quotes."""
    return text[1:].rstrip()


# Each credential-holding job of a release workflow, as one or more step
# sequences it may run: first the settings every step inherits (the
# workflow's keys but name, run-name and jobs; the job's keys but name and
# steps), then every step in order, every key but `name` exactly as the
# workflow writes it (a run body without its trailing whitespace). The
# release-path review reads here the exact shell that will hold a token.
#
# release-policy.yml also runs the base branch's copy of this check, with the
# base's list, so a step changes in two pull requests: the first adds the new
# sequence as another variant of its job (no workflow changes, so the base's
# check passes), and the second changes the workflow and removes the old one.
RUN_ALLOWLIST: dict[tuple[str, str], list[list[dict]]] = {
    ('release-tag.yml', 'select-release-commit'): [[
        {
            'workflow': {
                'on': {
                    'push': {
                        'branches': [
                            'main',
                        ],
                        'paths': [
                            'provenance/pulp-observatory/cursor.json',
                        ],
                    },
                    'schedule': [
                        {
                            'cron': '17 9 * * 1',
                        },
                    ],
                    'workflow_dispatch': '',
                },
                'permissions': {
                    'contents': 'read',
                    'checks': 'read',
                    'statuses': 'read',
                },
                'concurrency': {
                    'group': "${{ github.event_name == 'push' && 'release-tag' || 'release-dry-run' }}",
                    'cancel-in-progress': 'false',
                },
            },
            'job': {
                'if': "github.event_name == 'push'",
                'runs-on': 'ubuntu-latest',
                'permissions': {
                    'actions': 'read',
                    'checks': 'read',
                    'contents': 'read',
                    'pull-requests': 'read',
                },
                'outputs': {
                    'tag': '${{ steps.release.outputs.tag }}',
                },
            },
        },
        {
            'uses': 'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1',
            'with': {
                'ref': '${{ github.sha }}',
                'fetch-depth': '0',
                'persist-credentials': 'false',
            },
        },
        {
            'id': 'select',
            'run': _body(r'''
set -euo pipefail
test "$GITHUB_REPOSITORY" = Generous-Corp/vellum
test "$GITHUB_REF" = refs/heads/main
if ! python3 -I scripts/release_request.py select --commit "$GITHUB_SHA" --remote origin \
  > "$RUNNER_TEMP/selection.txt" 2> "$RUNNER_TEMP/selection-reason.txt"; then
  echo "::error::$(cat "$RUNNER_TEMP/selection-reason.txt")"
  exit 1
fi
cat "$RUNNER_TEMP/selection.txt" >> "$GITHUB_OUTPUT"
reason="$(cat "$RUNNER_TEMP/selection-reason.txt")"
echo "::notice::$reason"
printf '%s\n' "$reason" >> "$GITHUB_STEP_SUMMARY"'''),
        },
        {
            'if': "steps.select.outputs.tag != ''",
            'uses': 'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1',
            'with': {
                'repository': 'Generous-Corp/pulp',
                'ref': 'main',
                'path': '.exact-boundary-pulp',
                'fetch-depth': '0',
                'persist-credentials': 'false',
            },
        },
        {
            'if': "steps.select.outputs.tag != ''",
            'uses': 'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1',
            'with': {
                'repository': 'Generous-Corp/pulp',
                'ref': '${{ steps.select.outputs.pulp-cursor }}',
                'path': '.observatory-pulp',
                'fetch-depth': '0',
                'persist-credentials': 'false',
            },
        },
        {
            'if': "steps.select.outputs.tag != ''",
            'env': {
                'GH_TOKEN': '${{ github.token }}',
                'TAG': '${{ steps.select.outputs.tag }}',
            },
            'run': _body(r'''
set -euo pipefail
python3 -I scripts/pretag_checks.py --tag "$TAG" --commit "$GITHUB_SHA"'''),
        },
        {
            'id': 'release',
            'if': "steps.select.outputs.tag != ''",
            'env': {
                'TAG': '${{ steps.select.outputs.tag }}',
            },
            'run': _body(r'''
set -euo pipefail
printf 'tag=%s\n' "$TAG" >> "$GITHUB_OUTPUT"'''),
        },
    ],
    [{'workflow': {'on': {'push': {'branches': ['main'], 'paths': ['provenance/pulp-observatory/cursor.json']},
                          'schedule': [{'cron': '17 9 * * 1'}],
                          'workflow_dispatch': ''},
                   'permissions': {'contents': 'read', 'checks': 'read', 'statuses': 'read'},
                   'concurrency': {'group': "${{ github.event_name == 'push' && 'release-tag' || 'release-dry-run' }}",
                                   'cancel-in-progress': 'false'}},
      'job': {'if': "github.event_name == 'push'",
              'runs-on': 'ubuntu-latest',
              'permissions': {'actions': 'read', 'checks': 'read', 'contents': 'read', 'pull-requests': 'read'},
              'outputs': {'tag': '${{ steps.release.outputs.tag }}'}}},
     {'uses': 'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1',
      'with': {'ref': '${{ github.sha }}', 'fetch-depth': '0', 'persist-credentials': 'false'}},
     {'id': 'select',
      'run': 'set -euo pipefail\n'
             'test "$GITHUB_REPOSITORY" = Generous-Corp/vellum\n'
             'test "$GITHUB_REF" = refs/heads/main\n'
             'if ! python3 -I scripts/release_request.py select --commit "$GITHUB_SHA" --remote origin \\\n'
             '  > "$RUNNER_TEMP/selection.txt" 2> "$RUNNER_TEMP/selection-reason.txt"; then\n'
             '  echo "::error::$(cat "$RUNNER_TEMP/selection-reason.txt")"\n'
             '  exit 1\n'
             'fi\n'
             'cat "$RUNNER_TEMP/selection.txt" >> "$GITHUB_OUTPUT"\n'
             'reason="$(cat "$RUNNER_TEMP/selection-reason.txt")"\n'
             'echo "::notice::$reason"\n'
             'printf \'%s\\n\' "$reason" >> "$GITHUB_STEP_SUMMARY"'},
     {'if': "steps.select.outputs.tag != ''",
      'uses': 'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1',
      'with': {'repository': 'Generous-Corp/pulp',
               'ref': 'main',
               'path': '.exact-boundary-pulp',
               'fetch-depth': '0',
               'persist-credentials': 'false'}},
     {'if': "steps.select.outputs.tag != ''",
      'uses': 'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1',
      'with': {'repository': 'Generous-Corp/pulp',
               'ref': '${{ steps.select.outputs.pulp-cursor }}',
               'path': '.observatory-pulp',
               'fetch-depth': '0',
               'persist-credentials': 'false'}},
     {'if': "steps.select.outputs.tag != ''",
      'env': {'GH_TOKEN': '${{ github.token }}', 'TAG': '${{ steps.select.outputs.tag }}'},
      'run': 'set -euo pipefail\n'
             'python3 -I scripts/pretag_checks.py --tag "$TAG" --commit "$GITHUB_SHA" \\\n'
             '  --snapshot-output "$RUNNER_TEMP/open-pr-snapshot.json" \\\n'
             '  --bump-evidence-output "$RUNNER_TEMP/release-bump-evidence.json"'},
     {'if': "steps.select.outputs.tag != ''",
      'uses': 'actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a',
      'with': {'name': 'release-open-pr-snapshot',
               'path': '${{ runner.temp }}/open-pr-snapshot.json\n'
                       '${{ runner.temp }}/release-bump-evidence.json\n',
               'if-no-files-found': 'error',
               'retention-days': '1'}},
     {'id': 'release',
      'if': "steps.select.outputs.tag != ''",
      'env': {'TAG': '${{ steps.select.outputs.tag }}'},
      'run': 'set -euo pipefail\nprintf \'tag=%s\\n\' "$TAG" >> "$GITHUB_OUTPUT"'}],
[{'workflow': {'on': {'push': {'branches': ['main'], 'paths': ['provenance/pulp-observatory/cursor.json']},
                       'schedule': [{'cron': '17 9 * * 1'}],
                       'workflow_dispatch': ''},
                'permissions': {'contents': 'read', 'checks': 'read', 'statuses': 'read'},
                'concurrency': {'group': "${{ github.event_name == 'push' && 'release-tag' || 'release-dry-run' }}",
                                'cancel-in-progress': 'false'}},
   'job': {'if': "github.event_name == 'push'",
           'runs-on': 'ubuntu-latest',
           'permissions': {'actions': 'read', 'checks': 'read', 'contents': 'read', 'pull-requests': 'read'},
           'outputs': {'tag': '${{ steps.release.outputs.tag }}',
                       'snapshot-sha256': '${{ steps.snapshot.outputs.sha256 }}'}}},
  {'uses': 'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1',
   'with': {'ref': '${{ github.sha }}', 'fetch-depth': '0', 'persist-credentials': 'false'}},
  {'id': 'select',
   'run': 'set -euo pipefail\n'
          'test "$GITHUB_REPOSITORY" = Generous-Corp/vellum\n'
          'test "$GITHUB_REF" = refs/heads/main\n'
          'if ! python3 -I scripts/release_request.py select --commit "$GITHUB_SHA" --remote origin \\\n'
          '  > "$RUNNER_TEMP/selection.txt" 2> "$RUNNER_TEMP/selection-reason.txt"; then\n'
          '  echo "::error::$(cat "$RUNNER_TEMP/selection-reason.txt")"\n'
          '  exit 1\n'
          'fi\n'
          'cat "$RUNNER_TEMP/selection.txt" >> "$GITHUB_OUTPUT"\n'
          'reason="$(cat "$RUNNER_TEMP/selection-reason.txt")"\n'
          'echo "::notice::$reason"\n'
          'printf \'%s\\n\' "$reason" >> "$GITHUB_STEP_SUMMARY"'},
  {'if': "steps.select.outputs.tag != ''",
   'uses': 'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1',
   'with': {'repository': 'Generous-Corp/pulp',
            'ref': 'main',
            'path': '.exact-boundary-pulp',
            'fetch-depth': '0',
            'persist-credentials': 'false'}},
  {'if': "steps.select.outputs.tag != ''",
   'uses': 'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1',
   'with': {'repository': 'Generous-Corp/pulp',
            'ref': '${{ steps.select.outputs.pulp-cursor }}',
            'path': '.observatory-pulp',
            'fetch-depth': '0',
            'persist-credentials': 'false'}},
  {'if': "steps.select.outputs.tag != ''",
   'env': {'GH_TOKEN': '${{ github.token }}', 'TAG': '${{ steps.select.outputs.tag }}'},
   'run': 'set -euo pipefail\n'
          'python3 -I scripts/pretag_checks.py --tag "$TAG" --commit "$GITHUB_SHA" \\\n'
          '  --snapshot-output "$RUNNER_TEMP/open-pr-snapshot.json" \\\n'
          '  --release-bump-output "$RUNNER_TEMP/release-bump-evidence.json" \\\n'
          '  --pretag-replay'},
  {'if': "steps.select.outputs.tag != ''",
   'run': 'set -euo pipefail\n'
          'mkdir -p "$RUNNER_TEMP/release-snapshot"\n'
          'cp "$RUNNER_TEMP/open-pr-snapshot.json" "$RUNNER_TEMP/release-snapshot/open-pr-snapshot.json"\n'
          'cp "$RUNNER_TEMP/release-bump-evidence.json" "$RUNNER_TEMP/release-snapshot/release-bump-evidence.json"'},
  {'id': 'snapshot',
   'if': "steps.select.outputs.tag != ''",
   'run': 'set -euo pipefail\n'
          'sha256sum "$RUNNER_TEMP/release-snapshot/open-pr-snapshot.json" | awk \'{print "sha256=" $1}\' >> '
          '"$GITHUB_OUTPUT"'},
  {'if': "steps.select.outputs.tag != ''",
   'uses': 'actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a',
   'with': {'name': 'release-open-pr-snapshot',
            'path': '${{ runner.temp }}/release-snapshot',
            'if-no-files-found': 'error',
            'retention-days': '1'}},
  {'id': 'release',
   'if': "steps.select.outputs.tag != ''",
   'env': {'TAG': '${{ steps.select.outputs.tag }}'},
   'run': 'set -euo pipefail\nprintf \'tag=%s\\n\' "$TAG" >> "$GITHUB_OUTPUT"'}],
    ],
    ('release-tag.yml', 'release-policy-check'): [[
        {
            'workflow': {
                'on': {
                    'push': {
                        'branches': [
                            'main',
                        ],
                        'paths': [
                            'provenance/pulp-observatory/cursor.json',
                        ],
                    },
                    'schedule': [
                        {
                            'cron': '17 9 * * 1',
                        },
                    ],
                    'workflow_dispatch': '',
                },
                'permissions': {
                    'contents': 'read',
                    'checks': 'read',
                    'statuses': 'read',
                },
                'concurrency': {
                    'group': "${{ github.event_name == 'push' && 'release-tag' || 'release-dry-run' }}",
                    'cancel-in-progress': 'false',
                },
            },
            'job': {
                'needs': 'select-release-commit',
                'if': "needs.select-release-commit.outputs.tag != ''",
                'runs-on': 'ubuntu-latest',
                'permissions': {
                    'contents': 'read',
                },
            },
        },
        {
            'uses': 'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1',
            'with': {
                'ref': '${{ github.sha }}',
                'persist-credentials': 'false',
            },
        },
        {
            'run': _body(r'''
set -euo pipefail
python3 -I -m venv "$RUNNER_TEMP/release-policy-venv"
"$RUNNER_TEMP"/release-policy-venv/bin/python3 -I -m pip install --disable-pip-version-check \
  --only-binary=:all: --require-hashes -r scripts/release_policy_requirements.txt'''),
        },
        {
            'run': _body(r'''
set -euo pipefail
"$RUNNER_TEMP"/release-policy-venv/bin/python3 -I scripts/check_release_policy.py check'''),
        },
    ]],
    ('release-tag.yml', 'sign-release-tag'): [[
        {
            'workflow': {
                'on': {
                    'push': {
                        'branches': [
                            'main',
                        ],
                        'paths': [
                            'provenance/pulp-observatory/cursor.json',
                        ],
                    },
                    'schedule': [
                        {
                            'cron': '17 9 * * 1',
                        },
                    ],
                    'workflow_dispatch': '',
                },
                'permissions': {
                    'contents': 'read',
                    'checks': 'read',
                    'statuses': 'read',
                },
                'concurrency': {
                    'group': "${{ github.event_name == 'push' && 'release-tag' || 'release-dry-run' }}",
                    'cancel-in-progress': 'false',
                },
            },
            'job': {
                'needs': [
                    'select-release-commit',
                    'release-policy-check',
                ],
                'if': "needs.select-release-commit.outputs.tag != ''",
                'runs-on': 'ubuntu-latest',
                'environment': 'release',
            },
        },
        {
            'uses': 'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1',
            'with': {
                'ref': '${{ github.sha }}',
                'fetch-depth': '0',
                'persist-credentials': 'false',
            },
        },
        {
            'id': 'app-token',
            'uses': 'actions/create-github-app-token@1b10c78c7865c340bc4f6099eb2f838309f1e8c3',
            'with': {
                'app-id': '${{ vars.RELEASE_APP_ID }}',
                'private-key': '${{ secrets.RELEASE_APP_PRIVATE_KEY }}',
            },
        },
        {
            'env': {
                'GH_TOKEN': '${{ steps.app-token.outputs.token }}',
            },
            'run': _body(r'''
set -euo pipefail
timeout 120 gh api "repos/$GITHUB_REPOSITORY/branches/main/protection/required_status_checks" \
  > "$RUNNER_TEMP/required-checks.json"
timeout 120 gh api "repos/$GITHUB_REPOSITORY/rules/branches/main" > "$RUNNER_TEMP/branch-rules.json"
timeout 120 gh api "repos/$GITHUB_REPOSITORY/immutable-releases" > "$RUNNER_TEMP/immutable-releases.json"
# gh api exits non-zero on any non-2xx answer; a 2xx body of the wrong
# shape (another token, another endpoint) fails here, not in the decision.
jq -e 'type == "object" and (.contexts | type == "array")' "$RUNNER_TEMP/required-checks.json" > /dev/null
jq -e 'type == "array"' "$RUNNER_TEMP/branch-rules.json" > /dev/null
jq -e 'type == "object" and (.enabled | type == "boolean")' "$RUNNER_TEMP/immutable-releases.json" > /dev/null
date -u +%Y-%m-%dT%H:%M:%SZ > "$RUNNER_TEMP/immutable-releases-read-at.txt"'''),
        },
        {
            'env': {
                'GH_TOKEN': '${{ github.token }}',
                'SELECTED_TAG': '${{ needs.select-release-commit.outputs.tag }}',
            },
            'run': _body(r'''
set -euo pipefail
test "$GITHUB_REPOSITORY" = Generous-Corp/vellum
test "$GITHUB_REF" = refs/heads/main
# Decide again in this job, and require the same answer: the commit
# must still be the version's reconcile and the tag must still be absent.
python3 -I scripts/release_request.py select --commit "$GITHUB_SHA" --remote origin \
  > "$RUNNER_TEMP/selection.txt"
test "$(sed -n 's/^tag=//p' "$RUNNER_TEMP/selection.txt")" = "$SELECTED_TAG"
# The push that landed the reconcile starts main's required checks again
# on this commit. While the newest run of any required check is still
# running (gpu-macos takes about 45 minutes), wait rather than read it
# as unmet. A transient API failure is retried, not fatal.
# Both reads share one function, kept in a file the later step sources.
# Each gh api call has its own timeout so one hung call cannot outlive
# the loop's deadline; a timeout takes the same retry and failure path.
cat > "$RUNNER_TEMP/fetch_results.sh" <<'FETCH'
fetch_results() {
  local attempt
  for attempt in 1 2 3; do
    if timeout 120 gh api --method GET --paginate --slurp \
      "repos/$GITHUB_REPOSITORY/commits/$GITHUB_SHA/check-runs?per_page=100" \
      -f filter=all > "$RUNNER_TEMP/check-runs.json"; then
      break
    fi
    [ "$attempt" = 3 ] && exit 1
    sleep $((attempt * 20))
  done
  # release-path-review and framework-trusted-verify are commit
  # statuses, not check runs; main's rules accept either.
  for attempt in 1 2 3; do
    if timeout 120 gh api --method GET --paginate --slurp \
      "repos/$GITHUB_REPOSITORY/commits/$GITHUB_SHA/statuses?per_page=100" \
      > "$RUNNER_TEMP/statuses.json"; then
      break
    fi
    [ "$attempt" = 3 ] && exit 1
    sleep $((attempt * 20))
  done
}
FETCH
. "$RUNNER_TEMP/fetch_results.sh"
deadline=$((SECONDS + 6000))
while :; do
  fetch_results
  running="$(python3 -I scripts/release_request.py pending --commit "$GITHUB_SHA" \
    --branch-rules "$RUNNER_TEMP/branch-rules.json" --check-runs "$RUNNER_TEMP/check-runs.json" \
    --statuses "$RUNNER_TEMP/statuses.json" \
    --protection "$RUNNER_TEMP/required-checks.json")"
  [ -z "$running" ] && break
  if [ "$SECONDS" -ge "$deadline" ]; then
    echo "::error::required checks still running after 100 minutes: $running"
    exit 1
  fi
  echo "waiting for: $(printf '%s' "$running" | paste -sd, -)"
  sleep 60
done'''),
        },
        {
            'id': 'push-token',
            'uses': 'actions/create-github-app-token@1b10c78c7865c340bc4f6099eb2f838309f1e8c3',
            'with': {
                'app-id': '${{ vars.RELEASE_APP_ID }}',
                'private-key': '${{ secrets.RELEASE_APP_PRIVATE_KEY }}',
            },
        },
        {
            'env': {
                'GH_TOKEN': '${{ steps.push-token.outputs.token }}',
            },
            'run': _body(r'''
set -euo pipefail
timeout 120 gh api "repos/$GITHUB_REPOSITORY/branches/main/protection/required_status_checks" \
  > "$RUNNER_TEMP/required-checks.json"
timeout 120 gh api "repos/$GITHUB_REPOSITORY/rules/branches/main" > "$RUNNER_TEMP/branch-rules.json"
timeout 120 gh api "repos/$GITHUB_REPOSITORY/immutable-releases" > "$RUNNER_TEMP/immutable-releases.json"
# gh api exits non-zero on any non-2xx answer; a 2xx body of the wrong
# shape (another token, another endpoint) fails here, not in the decision.
jq -e 'type == "object" and (.contexts | type == "array")' "$RUNNER_TEMP/required-checks.json" > /dev/null
jq -e 'type == "array"' "$RUNNER_TEMP/branch-rules.json" > /dev/null
jq -e 'type == "object" and (.enabled | type == "boolean")' "$RUNNER_TEMP/immutable-releases.json" > /dev/null
date -u +%Y-%m-%dT%H:%M:%SZ > "$RUNNER_TEMP/immutable-releases-read-at.txt"'''),
        },
        {
            'id': 'decision',
            'env': {
                'GH_TOKEN': '${{ github.token }}',
                'SELECTED_TAG': '${{ needs.select-release-commit.outputs.tag }}',
            },
            'run': _body(r'''
set -euo pipefail
# Read the check runs and statuses again: the wait loop's snapshot can be
# minutes old, and the verdict must come from results read just before it.
. "$RUNNER_TEMP/fetch_results.sh"
fetch_results
tag="$(python3 -I scripts/release_request.py check --commit "$GITHUB_SHA" --remote origin \
  --branch-rules "$RUNNER_TEMP/branch-rules.json" --check-runs "$RUNNER_TEMP/check-runs.json" \
  --statuses "$RUNNER_TEMP/statuses.json" \
  --protection "$RUNNER_TEMP/required-checks.json" \
  --immutable-releases "$RUNNER_TEMP/immutable-releases.json" \
  --immutable-read-at "$(cat "$RUNNER_TEMP/immutable-releases-read-at.txt")")"
test "$tag" = "$SELECTED_TAG"
python3 -I scripts/check_release_policy.py signer >> "$GITHUB_OUTPUT"
version="${tag#v}"
python3 -I scripts/release_request.py freeze --tag "$tag" > "$RUNNER_TEMP/freeze.json"
python3 -I scripts/release_request.py attestation --commit "$GITHUB_SHA" \
  --attested-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --tag "$tag" > "$RUNNER_TEMP/promotion-attestation.json"
printf 'tag=%s\nversion=%s\n' "$tag" "$version" >> "$GITHUB_OUTPUT"'''),
        },
        {
            'id': 'sign',
            'env': {
                'RELEASE_TAG_SIGNING_KEY': '${{ secrets.RELEASE_TAG_SIGNING_KEY }}',
                'TAG': '${{ steps.decision.outputs.tag }}',
                'PRINCIPAL': '${{ steps.decision.outputs.principal }}',
            },
            'run': _body(r'''
set -euo pipefail
test -n "$RELEASE_TAG_SIGNING_KEY"
key="$RUNNER_TEMP/release-signing-key"
trap 'rm -f "$key"' EXIT
(umask 077 && printf '%s\n' "$RELEASE_TAG_SIGNING_KEY" > "$key")
# The signed message is the promotion attestation: provenance.yml
# parses it from the tag and the release-readiness gate verifies it.
git -c gpg.format=ssh -c user.signingkey="$key" \
  -c user.name="Vellum release" -c user.email="$PRINCIPAL" \
  tag -s -F "$RUNNER_TEMP/promotion-attestation.json" "$TAG" "$GITHUB_SHA"
printf 'object=%s\n' "$(git rev-parse "refs/tags/$TAG")" >> "$GITHUB_OUTPUT"'''),
        },
        {
            'env': {
                'TAG': '${{ steps.decision.outputs.tag }}',
                'OBJECT': '${{ steps.sign.outputs.object }}',
                'PRINCIPAL': '${{ steps.decision.outputs.principal }}',
                'FINGERPRINT': '${{ steps.decision.outputs.fingerprint }}',
            },
            'run': _body(r'''
set -euo pipefail
python3 -I scripts/verify_release_tag.py --repo . --repository "$GITHUB_REPOSITORY" \
  --tag-name "$TAG" --tag-object-sha "$OBJECT" --source-commit "$GITHUB_SHA" \
  --allowed-signers provenance/release-signers.allowed_signers \
  --expected-principal "$PRINCIPAL" --expected-fingerprint "$FINGERPRINT" \
  --output "$RUNNER_TEMP/release-trust.json"'''),
        },
        {
            'env': {
                'GH_TOKEN': '${{ steps.push-token.outputs.token }}',
                'TAG': '${{ steps.decision.outputs.tag }}',
                'OBJECT': '${{ steps.sign.outputs.object }}',
            },
            'run': _body(r'''
set -euo pipefail
git -c "http.https://github.com/.extraheader=AUTHORIZATION: basic $(printf 'x-access-token:%s' "$GH_TOKEN" | base64 -w0)" \
  push "https://github.com/$GITHUB_REPOSITORY" "refs/tags/$TAG:refs/tags/$TAG"
test "$(gh api "repos/$GITHUB_REPOSITORY/git/ref/tags/$TAG" --jq .object.sha)" = "$OBJECT"
# The tag is already pushed, so a transient API failure here must not
# leave it unfrozen: retry, and if every attempt fails, say exactly how
# to apply the same ruleset by hand.
for attempt in 1 2 3 4 5; do
  if gh api --method POST "repos/$GITHUB_REPOSITORY/rulesets" --input "$RUNNER_TEMP/freeze.json" > /dev/null; then
    break
  fi
  if [ "$attempt" = 5 ]; then
    echo "::error::$TAG is pushed but not frozen. Apply this ruleset as the owner:"
    cat "$RUNNER_TEMP/freeze.json"
    exit 1
  fi
  sleep $((attempt * 20))
done
echo "signed, pushed and froze $TAG at $OBJECT" >> "$GITHUB_STEP_SUMMARY"'''),
        },
    ],
    [{'workflow': {'on': {'push': {'branches': ['main'], 'paths': ['provenance/pulp-observatory/cursor.json']},
                          'schedule': [{'cron': '17 9 * * 1'}],
                          'workflow_dispatch': ''},
                   'permissions': {'contents': 'read', 'checks': 'read', 'statuses': 'read'},
                   'concurrency': {'group': "${{ github.event_name == 'push' && 'release-tag' || 'release-dry-run' }}",
                                   'cancel-in-progress': 'false'}},
      'job': {'needs': ['select-release-commit', 'release-policy-check'],
              'if': "needs.select-release-commit.outputs.tag != ''",
              'runs-on': 'ubuntu-latest',
              'environment': 'release'}},
     {'uses': 'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1',
      'with': {'ref': '${{ github.sha }}', 'fetch-depth': '0', 'persist-credentials': 'false'}},
     {'uses': 'actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c',
      'with': {'name': 'release-open-pr-snapshot', 'path': '${{ runner.temp }}'}},
     {'id': 'app-token',
      'uses': 'actions/create-github-app-token@1b10c78c7865c340bc4f6099eb2f838309f1e8c3',
      'with': {'app-id': '${{ vars.RELEASE_APP_ID }}', 'private-key': '${{ secrets.RELEASE_APP_PRIVATE_KEY }}'}},
     {'env': {'GH_TOKEN': '${{ steps.app-token.outputs.token }}'},
      'run': 'set -euo pipefail\n'
             'timeout 120 gh api "repos/$GITHUB_REPOSITORY/branches/main/protection/required_status_checks" \\\n'
             '  > "$RUNNER_TEMP/required-checks.json"\n'
             'timeout 120 gh api "repos/$GITHUB_REPOSITORY/rules/branches/main" > "$RUNNER_TEMP/branch-rules.json"\n'
             'timeout 120 gh api "repos/$GITHUB_REPOSITORY/immutable-releases" > "$RUNNER_TEMP/immutable-releases.json"\n'
             '# gh api exits non-zero on any non-2xx answer; a 2xx body of the wrong\n'
             '# shape (another token, another endpoint) fails here, not in the decision.\n'
             'jq -e \'type == "object" and (.contexts | type == "array")\' "$RUNNER_TEMP/required-checks.json" > '
             '/dev/null\n'
             'jq -e \'type == "array"\' "$RUNNER_TEMP/branch-rules.json" > /dev/null\n'
             'jq -e \'type == "object" and (.enabled | type == "boolean")\' "$RUNNER_TEMP/immutable-releases.json" > '
             '/dev/null\n'
             'date -u +%Y-%m-%dT%H:%M:%SZ > "$RUNNER_TEMP/immutable-releases-read-at.txt"'},
     {'env': {'GH_TOKEN': '${{ github.token }}', 'SELECTED_TAG': '${{ needs.select-release-commit.outputs.tag }}'},
      'run': 'set -euo pipefail\n'
             'test "$GITHUB_REPOSITORY" = Generous-Corp/vellum\n'
             'test "$GITHUB_REF" = refs/heads/main\n'
             '# Decide again in this job, and require the same answer: the commit\n'
             "# must still be the version's reconcile and the tag must still be absent.\n"
             'python3 -I scripts/release_request.py select --commit "$GITHUB_SHA" --remote origin \\\n'
             '  > "$RUNNER_TEMP/selection.txt"\n'
             'test "$(sed -n \'s/^tag=//p\' "$RUNNER_TEMP/selection.txt")" = "$SELECTED_TAG"\n'
             "# The push that landed the reconcile starts main's required checks again\n"
             '# on this commit. While the newest run of any required check is still\n'
             '# running (gpu-macos takes about 45 minutes), wait rather than read it\n'
             '# as unmet. A transient API failure is retried, not fatal.\n'
             '# Both reads share one function, kept in a file the later step sources.\n'
             '# Each gh api call has its own timeout so one hung call cannot outlive\n'
             "# the loop's deadline; a timeout takes the same retry and failure path.\n"
             'cat > "$RUNNER_TEMP/fetch_results.sh" <<\'FETCH\'\n'
             'fetch_results() {\n'
             '  local attempt\n'
             '  for attempt in 1 2 3; do\n'
             '    if timeout 120 gh api --method GET --paginate --slurp \\\n'
             '      "repos/$GITHUB_REPOSITORY/commits/$GITHUB_SHA/check-runs?per_page=100" \\\n'
             '      -f filter=all > "$RUNNER_TEMP/check-runs.json"; then\n'
             '      break\n'
             '    fi\n'
             '    [ "$attempt" = 3 ] && exit 1\n'
             '    sleep $((attempt * 20))\n'
             '  done\n'
             '  # release-path-review and framework-trusted-verify are commit\n'
             "  # statuses, not check runs; main's rules accept either.\n"
             '  for attempt in 1 2 3; do\n'
             '    if timeout 120 gh api --method GET --paginate --slurp \\\n'
             '      "repos/$GITHUB_REPOSITORY/commits/$GITHUB_SHA/statuses?per_page=100" \\\n'
             '      > "$RUNNER_TEMP/statuses.json"; then\n'
             '      break\n'
             '    fi\n'
             '    [ "$attempt" = 3 ] && exit 1\n'
             '    sleep $((attempt * 20))\n'
             '  done\n'
             '}\n'
             'FETCH\n'
             '. "$RUNNER_TEMP/fetch_results.sh"\n'
             'deadline=$((SECONDS + 6000))\n'
             'while :; do\n'
             '  fetch_results\n'
             '  running="$(python3 -I scripts/release_request.py pending --commit "$GITHUB_SHA" \\\n'
             '    --branch-rules "$RUNNER_TEMP/branch-rules.json" --check-runs "$RUNNER_TEMP/check-runs.json" \\\n'
             '    --statuses "$RUNNER_TEMP/statuses.json" \\\n'
             '    --protection "$RUNNER_TEMP/required-checks.json")"\n'
             '  [ -z "$running" ] && break\n'
             '  if [ "$SECONDS" -ge "$deadline" ]; then\n'
             '    echo "::error::required checks still running after 100 minutes: $running"\n'
             '    exit 1\n'
             '  fi\n'
             '  echo "waiting for: $(printf \'%s\' "$running" | paste -sd, -)"\n'
             '  sleep 60\n'
             'done'},
     {'id': 'push-token',
      'uses': 'actions/create-github-app-token@1b10c78c7865c340bc4f6099eb2f838309f1e8c3',
      'with': {'app-id': '${{ vars.RELEASE_APP_ID }}', 'private-key': '${{ secrets.RELEASE_APP_PRIVATE_KEY }}'}},
     {'env': {'GH_TOKEN': '${{ steps.push-token.outputs.token }}'},
      'run': 'set -euo pipefail\n'
             'timeout 120 gh api "repos/$GITHUB_REPOSITORY/branches/main/protection/required_status_checks" \\\n'
             '  > "$RUNNER_TEMP/required-checks.json"\n'
             'timeout 120 gh api "repos/$GITHUB_REPOSITORY/rules/branches/main" > "$RUNNER_TEMP/branch-rules.json"\n'
             'timeout 120 gh api "repos/$GITHUB_REPOSITORY/immutable-releases" > "$RUNNER_TEMP/immutable-releases.json"\n'
             '# gh api exits non-zero on any non-2xx answer; a 2xx body of the wrong\n'
             '# shape (another token, another endpoint) fails here, not in the decision.\n'
             'jq -e \'type == "object" and (.contexts | type == "array")\' "$RUNNER_TEMP/required-checks.json" > '
             '/dev/null\n'
             'jq -e \'type == "array"\' "$RUNNER_TEMP/branch-rules.json" > /dev/null\n'
             'jq -e \'type == "object" and (.enabled | type == "boolean")\' "$RUNNER_TEMP/immutable-releases.json" > '
             '/dev/null\n'
             'date -u +%Y-%m-%dT%H:%M:%SZ > "$RUNNER_TEMP/immutable-releases-read-at.txt"'},
     {'id': 'decision',
      'env': {'GH_TOKEN': '${{ github.token }}', 'SELECTED_TAG': '${{ needs.select-release-commit.outputs.tag }}'},
      'run': 'set -euo pipefail\n'
             "# Read the check runs and statuses again: the wait loop's snapshot can be\n"
             '# minutes old, and the verdict must come from results read just before it.\n'
             '. "$RUNNER_TEMP/fetch_results.sh"\n'
             'fetch_results\n'
             'tag="$(python3 -I scripts/release_request.py check --commit "$GITHUB_SHA" --remote origin \\\n'
             '  --branch-rules "$RUNNER_TEMP/branch-rules.json" --check-runs "$RUNNER_TEMP/check-runs.json" \\\n'
             '  --statuses "$RUNNER_TEMP/statuses.json" \\\n'
             '  --protection "$RUNNER_TEMP/required-checks.json" \\\n'
             '  --immutable-releases "$RUNNER_TEMP/immutable-releases.json" \\\n'
             '  --immutable-read-at "$(cat "$RUNNER_TEMP/immutable-releases-read-at.txt")")"\n'
             'test "$tag" = "$SELECTED_TAG"\n'
             'python3 -I scripts/check_release_policy.py signer >> "$GITHUB_OUTPUT"\n'
             'version="${tag#v}"\n'
             'python3 -I scripts/release_request.py freeze --tag "$tag" > "$RUNNER_TEMP/freeze.json"\n'
             'python3 -I scripts/release_request.py attestation --commit "$GITHUB_SHA" \\\n'
             '  --attested-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --tag "$tag" \\\n'
             '  --open-pr-snapshot-json "$RUNNER_TEMP/open-pr-snapshot.json" \\\n'
             '  --release-bump-evidence-json "$RUNNER_TEMP/release-bump-evidence.json" \\\n'
             '  > "$RUNNER_TEMP/promotion-attestation.json"\n'
             'printf \'tag=%s\\nversion=%s\\n\' "$tag" "$version" >> "$GITHUB_OUTPUT"'},
     {'id': 'sign',
      'env': {'RELEASE_TAG_SIGNING_KEY': '${{ secrets.RELEASE_TAG_SIGNING_KEY }}',
              'TAG': '${{ steps.decision.outputs.tag }}',
              'PRINCIPAL': '${{ steps.decision.outputs.principal }}'},
      'run': 'set -euo pipefail\n'
             'test -n "$RELEASE_TAG_SIGNING_KEY"\n'
             'key="$RUNNER_TEMP/release-signing-key"\n'
             'trap \'rm -f "$key"\' EXIT\n'
             '(umask 077 && printf \'%s\\n\' "$RELEASE_TAG_SIGNING_KEY" > "$key")\n'
             '# The signed message is the promotion attestation: provenance.yml\n'
             '# parses it from the tag and the release-readiness gate verifies it.\n'
             'git -c gpg.format=ssh -c user.signingkey="$key" \\\n'
             '  -c user.name="Vellum release" -c user.email="$PRINCIPAL" \\\n'
             '  tag -s -F "$RUNNER_TEMP/promotion-attestation.json" "$TAG" "$GITHUB_SHA"\n'
             'printf \'object=%s\\n\' "$(git rev-parse "refs/tags/$TAG")" >> "$GITHUB_OUTPUT"'},
     {'env': {'TAG': '${{ steps.decision.outputs.tag }}',
              'OBJECT': '${{ steps.sign.outputs.object }}',
              'PRINCIPAL': '${{ steps.decision.outputs.principal }}',
              'FINGERPRINT': '${{ steps.decision.outputs.fingerprint }}'},
      'run': 'set -euo pipefail\n'
             'python3 -I scripts/verify_release_tag.py --repo . --repository "$GITHUB_REPOSITORY" \\\n'
             '  --tag-name "$TAG" --tag-object-sha "$OBJECT" --source-commit "$GITHUB_SHA" \\\n'
             '  --allowed-signers provenance/release-signers.allowed_signers \\\n'
             '  --expected-principal "$PRINCIPAL" --expected-fingerprint "$FINGERPRINT" \\\n'
             '  --output "$RUNNER_TEMP/release-trust.json"'},
     {'env': {'GH_TOKEN': '${{ steps.push-token.outputs.token }}',
              'TAG': '${{ steps.decision.outputs.tag }}',
              'OBJECT': '${{ steps.sign.outputs.object }}'},
      'run': 'set -euo pipefail\n'
             'git -c "http.https://github.com/.extraheader=AUTHORIZATION: basic $(printf \'x-access-token:%s\' "$GH_TOKEN" '
             '| base64 -w0)" \\\n'
             '  push "https://github.com/$GITHUB_REPOSITORY" "refs/tags/$TAG:refs/tags/$TAG"\n'
             'test "$(gh api "repos/$GITHUB_REPOSITORY/git/ref/tags/$TAG" --jq .object.sha)" = "$OBJECT"\n'
             '# The tag is already pushed, so a transient API failure here must not\n'
             '# leave it unfrozen: retry, and if every attempt fails, say exactly how\n'
             '# to apply the same ruleset by hand.\n'
             'for attempt in 1 2 3 4 5; do\n'
             '  if gh api --method POST "repos/$GITHUB_REPOSITORY/rulesets" --input "$RUNNER_TEMP/freeze.json" > '
             '/dev/null; then\n'
             '    break\n'
             '  fi\n'
             '  if [ "$attempt" = 5 ]; then\n'
             '    echo "::error::$TAG is pushed but not frozen. Apply this ruleset as the owner:"\n'
             '    cat "$RUNNER_TEMP/freeze.json"\n'
             '    exit 1\n'
             '  fi\n'
             '  sleep $((attempt * 20))\n'
             'done\n'
             'echo "signed, pushed and froze $TAG at $OBJECT" >> "$GITHUB_STEP_SUMMARY"'}],
[{'workflow': {'on': {'push': {'branches': ['main'], 'paths': ['provenance/pulp-observatory/cursor.json']},
                       'schedule': [{'cron': '17 9 * * 1'}],
                       'workflow_dispatch': ''},
                'permissions': {'contents': 'read', 'checks': 'read', 'statuses': 'read'},
                'concurrency': {'group': "${{ github.event_name == 'push' && 'release-tag' || 'release-dry-run' }}",
                                'cancel-in-progress': 'false'}},
   'job': {'needs': ['select-release-commit', 'release-policy-check'],
           'if': "needs.select-release-commit.outputs.tag != ''",
           'runs-on': 'ubuntu-latest',
           'environment': 'release'}},
  {'uses': 'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1',
   'with': {'ref': '${{ github.sha }}', 'fetch-depth': '0', 'persist-credentials': 'false'}},
  {'uses': 'actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c',
   'with': {'name': 'release-open-pr-snapshot', 'path': '${{ runner.temp }}/release-snapshot'}},
  {'id': 'app-token',
   'uses': 'actions/create-github-app-token@1b10c78c7865c340bc4f6099eb2f838309f1e8c3',
   'with': {'app-id': '${{ vars.RELEASE_APP_ID }}', 'private-key': '${{ secrets.RELEASE_APP_PRIVATE_KEY }}'}},
  {'env': {'GH_TOKEN': '${{ steps.app-token.outputs.token }}'},
   'run': 'set -euo pipefail\n'
          'timeout 120 gh api "repos/$GITHUB_REPOSITORY/branches/main/protection/required_status_checks" \\\n'
          '  > "$RUNNER_TEMP/required-checks.json"\n'
          'timeout 120 gh api "repos/$GITHUB_REPOSITORY/rules/branches/main" > "$RUNNER_TEMP/branch-rules.json"\n'
          'timeout 120 gh api "repos/$GITHUB_REPOSITORY/immutable-releases" > "$RUNNER_TEMP/immutable-releases.json"\n'
          '# gh api exits non-zero on any non-2xx answer; a 2xx body of the wrong\n'
          '# shape (another token, another endpoint) fails here, not in the decision.\n'
          'jq -e \'type == "object" and (.contexts | type == "array")\' "$RUNNER_TEMP/required-checks.json" > '
          '/dev/null\n'
          'jq -e \'type == "array"\' "$RUNNER_TEMP/branch-rules.json" > /dev/null\n'
          'jq -e \'type == "object" and (.enabled | type == "boolean")\' "$RUNNER_TEMP/immutable-releases.json" > '
          '/dev/null\n'
          'date -u +%Y-%m-%dT%H:%M:%SZ > "$RUNNER_TEMP/immutable-releases-read-at.txt"'},
  {'env': {'GH_TOKEN': '${{ github.token }}', 'SELECTED_TAG': '${{ needs.select-release-commit.outputs.tag }}'},
   'run': 'set -euo pipefail\n'
          'test "$GITHUB_REPOSITORY" = Generous-Corp/vellum\n'
          'test "$GITHUB_REF" = refs/heads/main\n'
          '# Decide again in this job, and require the same answer: the commit\n'
          "# must still be the version's reconcile and the tag must still be absent.\n"
          'python3 -I scripts/release_request.py select --commit "$GITHUB_SHA" --remote origin \\\n'
          '  > "$RUNNER_TEMP/selection.txt"\n'
          'test "$(sed -n \'s/^tag=//p\' "$RUNNER_TEMP/selection.txt")" = "$SELECTED_TAG"\n'
          "# The push that landed the reconcile starts main's required checks again\n"
          '# on this commit. While the newest run of any required check is still\n'
          '# running (gpu-macos takes about 45 minutes), wait rather than read it\n'
          '# as unmet. A transient API failure is retried, not fatal.\n'
          '# Both reads share one function, kept in a file the later step sources.\n'
          '# Each gh api call has its own timeout so one hung call cannot outlive\n'
          "# the loop's deadline; a timeout takes the same retry and failure path.\n"
          'cat > "$RUNNER_TEMP/fetch_results.sh" <<\'FETCH\'\n'
          'fetch_results() {\n'
          '  local attempt\n'
          '  for attempt in 1 2 3; do\n'
          '    if timeout 120 gh api --method GET --paginate --slurp \\\n'
          '      "repos/$GITHUB_REPOSITORY/commits/$GITHUB_SHA/check-runs?per_page=100" \\\n'
          '      -f filter=all > "$RUNNER_TEMP/check-runs.json"; then\n'
          '      break\n'
          '    fi\n'
          '    [ "$attempt" = 3 ] && exit 1\n'
          '    sleep $((attempt * 20))\n'
          '  done\n'
          '  # release-path-review and framework-trusted-verify are commit\n'
          "  # statuses, not check runs; main's rules accept either.\n"
          '  for attempt in 1 2 3; do\n'
          '    if timeout 120 gh api --method GET --paginate --slurp \\\n'
          '      "repos/$GITHUB_REPOSITORY/commits/$GITHUB_SHA/statuses?per_page=100" \\\n'
          '      > "$RUNNER_TEMP/statuses.json"; then\n'
          '      break\n'
          '    fi\n'
          '    [ "$attempt" = 3 ] && exit 1\n'
          '    sleep $((attempt * 20))\n'
          '  done\n'
          '}\n'
          'FETCH\n'
          '. "$RUNNER_TEMP/fetch_results.sh"\n'
          'deadline=$((SECONDS + 6000))\n'
          'while :; do\n'
          '  fetch_results\n'
          '  running="$(python3 -I scripts/release_request.py pending --commit "$GITHUB_SHA" \\\n'
          '    --branch-rules "$RUNNER_TEMP/branch-rules.json" --check-runs "$RUNNER_TEMP/check-runs.json" \\\n'
          '    --statuses "$RUNNER_TEMP/statuses.json" \\\n'
          '    --protection "$RUNNER_TEMP/required-checks.json")"\n'
          '  [ -z "$running" ] && break\n'
          '  if [ "$SECONDS" -ge "$deadline" ]; then\n'
          '    echo "::error::required checks still running after 100 minutes: $running"\n'
          '    exit 1\n'
          '  fi\n'
          '  echo "waiting for: $(printf \'%s\' "$running" | paste -sd, -)"\n'
          '  sleep 60\n'
          'done'},
  {'id': 'push-token',
   'uses': 'actions/create-github-app-token@1b10c78c7865c340bc4f6099eb2f838309f1e8c3',
   'with': {'app-id': '${{ vars.RELEASE_APP_ID }}', 'private-key': '${{ secrets.RELEASE_APP_PRIVATE_KEY }}'}},
  {'env': {'GH_TOKEN': '${{ steps.push-token.outputs.token }}'},
   'run': 'set -euo pipefail\n'
          'timeout 120 gh api "repos/$GITHUB_REPOSITORY/branches/main/protection/required_status_checks" \\\n'
          '  > "$RUNNER_TEMP/required-checks.json"\n'
          'timeout 120 gh api "repos/$GITHUB_REPOSITORY/rules/branches/main" > "$RUNNER_TEMP/branch-rules.json"\n'
          'timeout 120 gh api "repos/$GITHUB_REPOSITORY/immutable-releases" > "$RUNNER_TEMP/immutable-releases.json"\n'
          '# gh api exits non-zero on any non-2xx answer; a 2xx body of the wrong\n'
          '# shape (another token, another endpoint) fails here, not in the decision.\n'
          'jq -e \'type == "object" and (.contexts | type == "array")\' "$RUNNER_TEMP/required-checks.json" > '
          '/dev/null\n'
          'jq -e \'type == "array"\' "$RUNNER_TEMP/branch-rules.json" > /dev/null\n'
          'jq -e \'type == "object" and (.enabled | type == "boolean")\' "$RUNNER_TEMP/immutable-releases.json" > '
          '/dev/null\n'
          'date -u +%Y-%m-%dT%H:%M:%SZ > "$RUNNER_TEMP/immutable-releases-read-at.txt"'},
  {'id': 'decision',
   'env': {'GH_TOKEN': '${{ github.token }}', 'SELECTED_TAG': '${{ needs.select-release-commit.outputs.tag }}'},
   'run': 'set -euo pipefail\n'
          "# Read the check runs and statuses again: the wait loop's snapshot can be\n"
          '# minutes old, and the verdict must come from results read just before it.\n'
          '. "$RUNNER_TEMP/fetch_results.sh"\n'
          'fetch_results\n'
          'tag="$(python3 -I scripts/release_request.py check --commit "$GITHUB_SHA" --remote origin \\\n'
          '  --branch-rules "$RUNNER_TEMP/branch-rules.json" --check-runs "$RUNNER_TEMP/check-runs.json" \\\n'
          '  --statuses "$RUNNER_TEMP/statuses.json" \\\n'
          '  --protection "$RUNNER_TEMP/required-checks.json" \\\n'
          '  --immutable-releases "$RUNNER_TEMP/immutable-releases.json" \\\n'
          '  --immutable-read-at "$(cat "$RUNNER_TEMP/immutable-releases-read-at.txt")")"\n'
          'test "$tag" = "$SELECTED_TAG"\n'
          'python3 -I scripts/check_release_policy.py signer >> "$GITHUB_OUTPUT"\n'
          'version="${tag#v}"\n'
          'python3 -I scripts/release_request.py freeze --tag "$tag" > "$RUNNER_TEMP/freeze.json"\n'
          'python3 -I scripts/release_request.py attestation --commit "$GITHUB_SHA" \\\n'
          '  --attested-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --tag "$tag" \\\n'
          '  --open-pr-snapshot-json "$RUNNER_TEMP/release-snapshot/open-pr-snapshot.json" \\\n'
          '  --release-bump-evidence-json "$RUNNER_TEMP/release-snapshot/release-bump-evidence.json" \\\n'
          '  > "$RUNNER_TEMP/promotion-attestation.json"\n'
          'printf \'tag=%s\\nversion=%s\\n\' "$tag" "$version" >> "$GITHUB_OUTPUT"'},
  {'id': 'sign',
   'env': {'RELEASE_TAG_SIGNING_KEY': '${{ secrets.RELEASE_TAG_SIGNING_KEY }}',
           'TAG': '${{ steps.decision.outputs.tag }}',
           'SNAPSHOT_SHA256': '${{ needs.select-release-commit.outputs.snapshot-sha256 }}',
           'PRINCIPAL': '${{ steps.decision.outputs.principal }}'},
   'run': 'set -euo pipefail\n'
          'test -n "$RELEASE_TAG_SIGNING_KEY"\n'
          'test "$(sha256sum "$RUNNER_TEMP/release-snapshot/open-pr-snapshot.json" | awk \'{print $1}\')" = '
          '"$SNAPSHOT_SHA256"\n'
          'key="$RUNNER_TEMP/release-signing-key"\n'
          'trap \'rm -f "$key"\' EXIT\n'
          '(umask 077 && printf \'%s\\n\' "$RELEASE_TAG_SIGNING_KEY" > "$key")\n'
          '# The signed message is the promotion attestation: provenance.yml\n'
          '# parses it from the tag and the release-readiness gate verifies it.\n'
          'git -c gpg.format=ssh -c user.signingkey="$key" \\\n'
          '  -c user.name="Vellum release" -c user.email="$PRINCIPAL" \\\n'
          '  tag -s -F "$RUNNER_TEMP/promotion-attestation.json" "$TAG" "$GITHUB_SHA"\n'
          'printf \'object=%s\\n\' "$(git rev-parse "refs/tags/$TAG")" >> "$GITHUB_OUTPUT"'},
  {'env': {'TAG': '${{ steps.decision.outputs.tag }}',
           'OBJECT': '${{ steps.sign.outputs.object }}',
           'PRINCIPAL': '${{ steps.decision.outputs.principal }}',
           'FINGERPRINT': '${{ steps.decision.outputs.fingerprint }}'},
   'run': 'set -euo pipefail\n'
          'python3 -I scripts/verify_release_tag.py --repo . --repository "$GITHUB_REPOSITORY" \\\n'
          '  --tag-name "$TAG" --tag-object-sha "$OBJECT" --source-commit "$GITHUB_SHA" \\\n'
          '  --allowed-signers provenance/release-signers.allowed_signers \\\n'
          '  --expected-principal "$PRINCIPAL" --expected-fingerprint "$FINGERPRINT" \\\n'
          '  --output "$RUNNER_TEMP/release-trust.json"'},
  {'env': {'GH_TOKEN': '${{ steps.push-token.outputs.token }}',
           'TAG': '${{ steps.decision.outputs.tag }}',
           'OBJECT': '${{ steps.sign.outputs.object }}'},
   'run': 'set -euo pipefail\n'
          'git -c "http.https://github.com/.extraheader=AUTHORIZATION: basic $(printf \'x-access-token:%s\' '
          '"$GH_TOKEN" | base64 -w0)" \\\n'
          '  push "https://github.com/$GITHUB_REPOSITORY" "refs/tags/$TAG:refs/tags/$TAG"\n'
          'test "$(gh api "repos/$GITHUB_REPOSITORY/git/ref/tags/$TAG" --jq .object.sha)" = "$OBJECT"\n'
          '# The tag is already pushed, so a transient API failure here must not\n'
          '# leave it unfrozen: retry, and if every attempt fails, say exactly how\n'
          '# to apply the same ruleset by hand.\n'
          'for attempt in 1 2 3 4 5; do\n'
          '  if gh api --method POST "repos/$GITHUB_REPOSITORY/rulesets" --input "$RUNNER_TEMP/freeze.json" > '
          '/dev/null; then\n'
          '    break\n'
          '  fi\n'
          '  if [ "$attempt" = 5 ]; then\n'
          '    echo "::error::$TAG is pushed but not frozen. Apply this ruleset as the owner:"\n'
          '    cat "$RUNNER_TEMP/freeze.json"\n'
          '    exit 1\n'
          '  fi\n'
          '  sleep $((attempt * 20))\n'
          'done\n'
          'echo "signed, pushed and froze $TAG at $OBJECT" >> "$GITHUB_STEP_SUMMARY"'}],
    ],
    ('release-tag.yml', 'dry-run-gate'): [[
        {
            'workflow': {
                'on': {
                    'push': {
                        'branches': [
                            'main',
                        ],
                        'paths': [
                            'provenance/pulp-observatory/cursor.json',
                        ],
                    },
                    'schedule': [
                        {
                            'cron': '17 9 * * 1',
                        },
                    ],
                    'workflow_dispatch': '',
                },
                'permissions': {
                    'contents': 'read',
                    'checks': 'read',
                    'statuses': 'read',
                },
                'concurrency': {
                    'group': "${{ github.event_name == 'push' && 'release-tag' || 'release-dry-run' }}",
                    'cancel-in-progress': 'false',
                },
            },
            'job': {
                'runs-on': 'ubuntu-latest',
                'permissions': {
                    'actions': 'read',
                    'contents': 'read',
                },
                'outputs': {
                    'run': '${{ steps.gate.outputs.run }}',
                },
            },
        },
        {
            'uses': 'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1',
            'with': {
                'ref': '${{ github.sha }}',
                'fetch-depth': '0',
                'persist-credentials': 'false',
            },
        },
        {
            'env': {
                'GH_TOKEN': '${{ github.token }}',
            },
            'run': _body(r'''
set -euo pipefail
for status in queued in_progress waiting pending requested; do
  gh api --method GET --paginate --slurp "repos/$GITHUB_REPOSITORY/actions/runs" \
    -f status="$status" -F per_page=100 > "$RUNNER_TEMP/runs-$status.json"
done
jq -s 'add' "$RUNNER_TEMP"/runs-*.json > "$RUNNER_TEMP/runs.json"'''),
        },
        {
            'id': 'gate',
            'env': {
                'EVENT': '${{ github.event_name }}',
                'BEFORE': '${{ github.event.before }}',
            },
            'run': _body(r'''
set -euo pipefail
test "$GITHUB_REF" = refs/heads/main
python3 -I scripts/release_dry_run.py gate --event "$EVENT" --before "$BEFORE" --after "$GITHUB_SHA" \
  --runs "$RUNNER_TEMP/runs.json" > "$RUNNER_TEMP/gate.txt" 2> "$RUNNER_TEMP/gate-reason.txt"
run="$(sed -n 's/^run=//p' "$RUNNER_TEMP/gate.txt")"
[[ "$run" == true || "$run" == false ]]
printf 'run=%s\n' "$run" >> "$GITHUB_OUTPUT"
echo "dry run: $run ($(head -n 1 "$RUNNER_TEMP/gate-reason.txt"))" >> "$GITHUB_STEP_SUMMARY"'''),
        },
    ]],
    ('release-tag.yml', 'dry-run-policy-check'): [[
        {
            'workflow': {
                'on': {
                    'push': {
                        'branches': [
                            'main',
                        ],
                        'paths': [
                            'provenance/pulp-observatory/cursor.json',
                        ],
                    },
                    'schedule': [
                        {
                            'cron': '17 9 * * 1',
                        },
                    ],
                    'workflow_dispatch': '',
                },
                'permissions': {
                    'contents': 'read',
                    'checks': 'read',
                    'statuses': 'read',
                },
                'concurrency': {
                    'group': "${{ github.event_name == 'push' && 'release-tag' || 'release-dry-run' }}",
                    'cancel-in-progress': 'false',
                },
            },
            'job': {
                'needs': 'dry-run-gate',
                'if': "needs.dry-run-gate.outputs.run == 'true'",
                'runs-on': 'ubuntu-latest',
                'permissions': {
                    'contents': 'read',
                },
                'outputs': {
                    'commit': '${{ steps.check.outputs.commit }}',
                },
            },
        },
        {
            'uses': 'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1',
            'with': {
                'ref': '${{ github.sha }}',
                'fetch-depth': '0',
                'persist-credentials': 'false',
            },
        },
        {
            'run': _body(r'''
set -euo pipefail
python3 -I -m venv "$RUNNER_TEMP/release-policy-venv"
"$RUNNER_TEMP"/release-policy-venv/bin/python3 -I -m pip install --disable-pip-version-check \
  --only-binary=:all: --require-hashes -r scripts/release_policy_requirements.txt'''),
        },
        {
            'id': 'check',
            'run': _body(r'''
set -euo pipefail
test "$GITHUB_REF" = refs/heads/main
python3 -I scripts/release_dry_run.py plan --head "$GITHUB_SHA" --run-id "$GITHUB_RUN_ID" \
  --attempt "$GITHUB_RUN_ATTEMPT" > "$RUNNER_TEMP/dry-run.txt"
commit="$(sed -n 's/^commit=//p' "$RUNNER_TEMP/dry-run.txt")"
[[ "$commit" =~ ^[0-9a-f]{40}$ ]]
git checkout --quiet --detach "$commit"
"$RUNNER_TEMP"/release-policy-venv/bin/python3 -I scripts/check_release_policy.py check
printf 'commit=%s\n' "$commit" >> "$GITHUB_OUTPUT"'''),
        },
    ]],
    ('release-tag.yml', 'sign-dry-run-tag'): [[
        {
            'workflow': {
                'on': {
                    'push': {
                        'branches': [
                            'main',
                        ],
                        'paths': [
                            'provenance/pulp-observatory/cursor.json',
                        ],
                    },
                    'schedule': [
                        {
                            'cron': '17 9 * * 1',
                        },
                    ],
                    'workflow_dispatch': '',
                },
                'permissions': {
                    'contents': 'read',
                    'checks': 'read',
                    'statuses': 'read',
                },
                'concurrency': {
                    'group': "${{ github.event_name == 'push' && 'release-tag' || 'release-dry-run' }}",
                    'cancel-in-progress': 'false',
                },
            },
            'job': {
                'needs': [
                    'dry-run-gate',
                    'dry-run-policy-check',
                ],
                'if': "needs.dry-run-gate.outputs.run == 'true'",
                'runs-on': 'ubuntu-latest',
                'environment': 'release',
            },
        },
        {
            'uses': 'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1',
            'with': {
                'ref': '${{ github.sha }}',
                'fetch-depth': '0',
                'persist-credentials': 'false',
            },
        },
        {
            'id': 'dry-run',
            'env': {
                'POLICY_COMMIT': '${{ needs.dry-run-policy-check.outputs.commit }}',
            },
            'run': _body(r'''
set -euo pipefail
test "$GITHUB_REPOSITORY" = Generous-Corp/vellum
test "$GITHUB_REF" = refs/heads/main
python3 -I scripts/release_dry_run.py plan --head "$GITHUB_SHA" --run-id "$GITHUB_RUN_ID" \
  --attempt "$GITHUB_RUN_ATTEMPT" > "$RUNNER_TEMP/dry-run.txt"
commit="$(sed -n 's/^commit=//p' "$RUNNER_TEMP/dry-run.txt")"
tag="$(sed -n 's/^tag=//p' "$RUNNER_TEMP/dry-run.txt")"
identity="$(sed -n 's/^identity=//p' "$RUNNER_TEMP/dry-run.txt")"
[[ "$commit" =~ ^[0-9a-f]{40}$ ]]
[[ "$tag" =~ ^dry-run/(v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*))/[1-9][0-9]*-[1-9][0-9]*$ ]]
test "$identity" = "${BASH_REMATCH[1]}"
# The tagged commit's own release scripts decide, as on a release.
# release-policy-check judged this commit with the YAML parser; it must
# be the commit this job plans.
test "$commit" = "$POLICY_COMMIT"
git checkout --quiet --detach "$commit"
python3 -I scripts/check_release_policy.py signer >> "$GITHUB_OUTPUT"
# The signed message wraps v<version>'s attestation under its own
# schema with the dry-run tag's name, so it can never pass for a release.
python3 -I scripts/release_request.py attestation --commit "$commit" \
  --attested-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --tag "$identity" --dry-run-tag "$tag" \
  > "$RUNNER_TEMP/promotion-attestation.json"
printf 'commit=%s\ntag=%s\n' "$commit" "$tag" >> "$GITHUB_OUTPUT"
echo "dry run: $tag on $commit (release identity $identity)" >> "$GITHUB_STEP_SUMMARY"'''),
        },
        {
            'id': 'sign',
            'env': {
                'RELEASE_TAG_SIGNING_KEY': '${{ secrets.RELEASE_TAG_SIGNING_KEY }}',
                'TAG': '${{ steps.dry-run.outputs.tag }}',
                'COMMIT': '${{ steps.dry-run.outputs.commit }}',
                'PRINCIPAL': '${{ steps.dry-run.outputs.principal }}',
            },
            'run': _body(r'''
set -euo pipefail
test -n "$RELEASE_TAG_SIGNING_KEY"
case "$TAG" in dry-run/v*) ;; *) exit 1 ;; esac
key="$RUNNER_TEMP/release-signing-key"
trap 'rm -f "$key"' EXIT
(umask 077 && printf '%s\n' "$RELEASE_TAG_SIGNING_KEY" > "$key")
git -c gpg.format=ssh -c user.signingkey="$key" \
  -c user.name="Vellum release" -c user.email="$PRINCIPAL" \
  tag -s -F "$RUNNER_TEMP/promotion-attestation.json" "$TAG" "$COMMIT"
printf 'object=%s\n' "$(git rev-parse "refs/tags/$TAG")" >> "$GITHUB_OUTPUT"'''),
        },
        {
            'env': {
                'TAG': '${{ steps.dry-run.outputs.tag }}',
                'COMMIT': '${{ steps.dry-run.outputs.commit }}',
                'OBJECT': '${{ steps.sign.outputs.object }}',
                'PRINCIPAL': '${{ steps.dry-run.outputs.principal }}',
                'FINGERPRINT': '${{ steps.dry-run.outputs.fingerprint }}',
            },
            'run': _body(r'''
set -euo pipefail
python3 -I scripts/verify_release_tag.py --repo . --repository "$GITHUB_REPOSITORY" \
  --tag-name "$TAG" --tag-object-sha "$OBJECT" --source-commit "$COMMIT" \
  --allowed-signers provenance/release-signers.allowed_signers \
  --expected-principal "$PRINCIPAL" --expected-fingerprint "$FINGERPRINT" \
  --output "$RUNNER_TEMP/dry-run-trust.json"'''),
        },
        {
            'id': 'push-token',
            'uses': 'actions/create-github-app-token@1b10c78c7865c340bc4f6099eb2f838309f1e8c3',
            'with': {
                'app-id': '${{ vars.RELEASE_APP_ID }}',
                'private-key': '${{ secrets.RELEASE_APP_PRIVATE_KEY }}',
                'owner': 'Generous-Corp',
                'repositories': 'vellum',
                'permission-contents': 'write',
            },
        },
        {
            'env': {
                'GH_TOKEN': '${{ steps.push-token.outputs.token }}',
                'TAG': '${{ steps.dry-run.outputs.tag }}',
                'OBJECT': '${{ steps.sign.outputs.object }}',
            },
            'run': _body(r'''
set -euo pipefail
case "$TAG" in dry-run/v*) ;; *) echo "::error::$TAG is not a dry-run tag"; exit 1 ;; esac
git -c "http.https://github.com/.extraheader=AUTHORIZATION: basic $(printf 'x-access-token:%s' "$GH_TOKEN" | base64 -w0)" \
  push "https://github.com/$GITHUB_REPOSITORY" "refs/tags/$TAG:refs/tags/$TAG"
test "$(gh api "repos/$GITHUB_REPOSITORY/git/ref/tags/$TAG" --jq .object.sha)" = "$OBJECT"
echo "pushed the dry-run tag $TAG at $OBJECT; sdk-release deletes it and its draft" >> "$GITHUB_STEP_SUMMARY"'''),
        },
    ],
[{'workflow': {'on': {'push': {'branches': ['main'], 'paths': ['provenance/pulp-observatory/cursor.json']},
                       'schedule': [{'cron': '17 9 * * 1'}],
                       'workflow_dispatch': ''},
                'permissions': {'contents': 'read', 'checks': 'read', 'statuses': 'read'},
                'concurrency': {'group': "${{ github.event_name == 'push' && 'release-tag' || 'release-dry-run' }}",
                                'cancel-in-progress': 'false'}},
   'job': {'needs': ['dry-run-gate', 'dry-run-policy-check'],
           'if': "needs.dry-run-gate.outputs.run == 'true'",
           'runs-on': 'ubuntu-latest',
           'environment': 'release'}},
  {'uses': 'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1',
   'with': {'ref': '${{ github.sha }}', 'fetch-depth': '0', 'persist-credentials': 'false'}},
  {'id': 'dry-run',
   'env': {'POLICY_COMMIT': '${{ needs.dry-run-policy-check.outputs.commit }}'},
   'run': 'set -euo pipefail\n'
          'test "$GITHUB_REPOSITORY" = Generous-Corp/vellum\n'
          'test "$GITHUB_REF" = refs/heads/main\n'
          'python3 -I scripts/release_dry_run.py plan --head "$GITHUB_SHA" --run-id "$GITHUB_RUN_ID" \\\n'
          '  --attempt "$GITHUB_RUN_ATTEMPT" > "$RUNNER_TEMP/dry-run.txt"\n'
          'commit="$(sed -n \'s/^commit=//p\' "$RUNNER_TEMP/dry-run.txt")"\n'
          'tag="$(sed -n \'s/^tag=//p\' "$RUNNER_TEMP/dry-run.txt")"\n'
          'identity="$(sed -n \'s/^identity=//p\' "$RUNNER_TEMP/dry-run.txt")"\n'
          '[[ "$commit" =~ ^[0-9a-f]{40}$ ]]\n'
          '[[ "$tag" =~ ^dry-run/(v(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*))/[1-9][0-9]*-[1-9][0-9]*$ ]]\n'
          'test "$identity" = "${BASH_REMATCH[1]}"\n'
          "# The tagged commit's own release scripts decide, as on a release.\n"
          '# release-policy-check judged this commit with the YAML parser; it must\n'
          '# be the commit this job plans.\n'
          'test "$commit" = "$POLICY_COMMIT"\n'
          'git checkout --quiet --detach "$commit"\n'
          'python3 -I scripts/check_release_policy.py signer >> "$GITHUB_OUTPUT"\n'
          'python3 -I scripts/pretag_checks.py --tag "$identity" --commit "$commit" \\\n'
          '  --snapshot-output "$RUNNER_TEMP/release-snapshot/open-pr-snapshot.json" \\\n'
          '  --release-bump-output "$RUNNER_TEMP/release-snapshot/release-bump-evidence.json" \\\n'
          '  --pretag-replay\n'
          "# The signed message wraps v<version>'s attestation under its own\n"
          "# schema with the dry-run tag's name, so it can never pass for a release.\n"
          'python3 -I scripts/release_request.py attestation --commit "$commit" \\\n'
          '  --attested-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --tag "$identity" --dry-run-tag "$tag" \\\n'
          '  --open-pr-snapshot-json "$RUNNER_TEMP/release-snapshot/open-pr-snapshot.json" \\\n'
          '  --release-bump-evidence-json "$RUNNER_TEMP/release-snapshot/release-bump-evidence.json" \\\n'
          '  > "$RUNNER_TEMP/promotion-attestation.json"\n'
          'printf \'commit=%s\\ntag=%s\\n\' "$commit" "$tag" >> "$GITHUB_OUTPUT"\n'
          'echo "dry run: $tag on $commit (release identity $identity)" >> "$GITHUB_STEP_SUMMARY"'},
  {'id': 'sign',
   'env': {'RELEASE_TAG_SIGNING_KEY': '${{ secrets.RELEASE_TAG_SIGNING_KEY }}',
           'TAG': '${{ steps.dry-run.outputs.tag }}',
           'COMMIT': '${{ steps.dry-run.outputs.commit }}',
           'PRINCIPAL': '${{ steps.dry-run.outputs.principal }}'},
   'run': 'set -euo pipefail\n'
          'test -n "$RELEASE_TAG_SIGNING_KEY"\n'
          'case "$TAG" in dry-run/v*) ;; *) exit 1 ;; esac\n'
          'key="$RUNNER_TEMP/release-signing-key"\n'
          'trap \'rm -f "$key"\' EXIT\n'
          '(umask 077 && printf \'%s\\n\' "$RELEASE_TAG_SIGNING_KEY" > "$key")\n'
          'git -c gpg.format=ssh -c user.signingkey="$key" \\\n'
          '  -c user.name="Vellum release" -c user.email="$PRINCIPAL" \\\n'
          '  tag -s -F "$RUNNER_TEMP/promotion-attestation.json" "$TAG" "$COMMIT"\n'
          'printf \'object=%s\\n\' "$(git rev-parse "refs/tags/$TAG")" >> "$GITHUB_OUTPUT"'},
  {'env': {'TAG': '${{ steps.dry-run.outputs.tag }}',
           'COMMIT': '${{ steps.dry-run.outputs.commit }}',
           'OBJECT': '${{ steps.sign.outputs.object }}',
           'PRINCIPAL': '${{ steps.dry-run.outputs.principal }}',
           'FINGERPRINT': '${{ steps.dry-run.outputs.fingerprint }}'},
   'run': 'set -euo pipefail\n'
          'python3 -I scripts/verify_release_tag.py --repo . --repository "$GITHUB_REPOSITORY" \\\n'
          '  --tag-name "$TAG" --tag-object-sha "$OBJECT" --source-commit "$COMMIT" \\\n'
          '  --allowed-signers provenance/release-signers.allowed_signers \\\n'
          '  --expected-principal "$PRINCIPAL" --expected-fingerprint "$FINGERPRINT" \\\n'
          '  --output "$RUNNER_TEMP/dry-run-trust.json"'},
  {'id': 'push-token',
   'uses': 'actions/create-github-app-token@1b10c78c7865c340bc4f6099eb2f838309f1e8c3',
   'with': {'app-id': '${{ vars.RELEASE_APP_ID }}',
            'private-key': '${{ secrets.RELEASE_APP_PRIVATE_KEY }}',
            'owner': 'Generous-Corp',
            'repositories': 'vellum',
            'permission-contents': 'write'}},
  {'env': {'GH_TOKEN': '${{ steps.push-token.outputs.token }}',
           'TAG': '${{ steps.dry-run.outputs.tag }}',
           'OBJECT': '${{ steps.sign.outputs.object }}'},
   'run': 'set -euo pipefail\n'
          'case "$TAG" in dry-run/v*) ;; *) echo "::error::$TAG is not a dry-run tag"; exit 1 ;; esac\n'
          'git -c "http.https://github.com/.extraheader=AUTHORIZATION: basic $(printf \'x-access-token:%s\' '
          '"$GH_TOKEN" | base64 -w0)" \\\n'
          '  push "https://github.com/$GITHUB_REPOSITORY" "refs/tags/$TAG:refs/tags/$TAG"\n'
          'test "$(gh api "repos/$GITHUB_REPOSITORY/git/ref/tags/$TAG" --jq .object.sha)" = "$OBJECT"\n'
          'echo "pushed the dry-run tag $TAG at $OBJECT; sdk-release deletes it and its draft" >> '
          '"$GITHUB_STEP_SUMMARY"'}]],
    ('release-policy.yml', 'release-policy'): [[
        {
            'workflow': {
                'on': {
                    'pull_request': '',
                    'merge_group': {
                        'branches': [
                            'main',
                        ],
                    },
                    'push': {
                        'branches': [
                            'main',
                        ],
                    },
                },
                'permissions': {
                    'contents': 'read',
                },
            },
            'job': {
                'runs-on': 'ubuntu-latest',
            },
        },
        {
            'uses': 'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1',
            'with': {
                'fetch-depth': '0',
                'persist-credentials': 'false',
            },
        },
        {
            'run': _body(r'''
set -euo pipefail
python3 -I -m venv "$RUNNER_TEMP/release-policy-venv"
"$RUNNER_TEMP"/release-policy-venv/bin/python3 -I -m pip install --disable-pip-version-check \
  --only-binary=:all: --require-hashes -r scripts/release_policy_requirements.txt'''),
        },
        {
            'run': _body(r'''
set -euo pipefail
"$RUNNER_TEMP"/release-policy-venv/bin/python3 -I scripts/run_ci_lane.py --lane release-policy'''),
        },
        {
            'if': "github.event_name != 'push'",
            'env': {
                'BASE_SHA': '${{ github.event.pull_request.base.sha || github.event.merge_group.base_sha }}',
            },
            'run': _body(r'''
set -euo pipefail
[[ "$BASE_SHA" =~ ^[0-9a-f]{40}$ ]]
base="$RUNNER_TEMP/policy-base"
git worktree add --detach "$base" "$BASE_SHA"
if ! grep -q -- '--from-base' "$base/scripts/check_release_policy.py"; then
  echo "::notice::the base branch's check_release_policy.py predates --from-base; nothing to run"
  exit 0
fi
"$RUNNER_TEMP"/release-policy-venv/bin/python3 -I "$base/scripts/check_release_policy.py" --root "$GITHUB_WORKSPACE" --from-base check'''),
        },
        {
            'if': "github.event_name == 'pull_request'",
            'env': {
                'BASE_SHA': '${{ github.event.pull_request.base.sha }}',
                'HEAD_SHA': '${{ github.event.pull_request.head.sha }}',
            },
            'run': 'python3 -I scripts/version_at_land.py lint --revisions "$BASE_SHA..$HEAD_SHA" || true',
        },
    ]],
    ('version-at-land.yml', 'plan'): [[
        {
            'workflow': {
                'on': {
                    'push': {
                        'branches': [
                            'main',
                        ],
                    },
                    'schedule': [
                        {
                            'cron': '41 */6 * * *',
                        },
                    ],
                    'workflow_dispatch': '',
                },
                'permissions': {
                    'contents': 'read',
                    'pull-requests': 'read',
                },
                'concurrency': {
                    'group': 'version-at-land',
                    'cancel-in-progress': 'false',
                },
                'env': {
                    'BUMP_BRANCH': 'automation/version-bump',
                },
            },
            'job': {
                'runs-on': 'ubuntu-latest',
                'timeout-minutes': '15',
                'outputs': {
                    'action': '${{ steps.plan.outputs.action }}',
                    'version': '${{ steps.plan.outputs.version }}',
                    'main': '${{ steps.plan.outputs.main }}',
                    'number': '${{ steps.standing.outputs.number }}',
                    'current': '${{ steps.standing.outputs.current }}',
                },
            },
        },
        {
            'uses': 'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1',
            'with': {
                'ref': 'main',
                'fetch-depth': '0',
                'persist-credentials': 'false',
            },
        },
        {
            'id': 'plan',
            'run': _body(r'''
set -euo pipefail
test "$GITHUB_REPOSITORY" = Generous-Corp/vellum
main="$(git rev-parse HEAD)"
if ! python3 -I scripts/version_at_land.py plan --commit "$main" --remote origin \
  > "$RUNNER_TEMP/plan.txt" 2> "$RUNNER_TEMP/plan-reason.txt"; then
  echo "::error::$(head -n 1 "$RUNNER_TEMP/plan-reason.txt")"
  exit 1
fi
action="$(sed -n 's/^action=//p' "$RUNNER_TEMP/plan.txt")"
version="$(sed -n 's/^version=//p' "$RUNNER_TEMP/plan.txt")"
case "$action" in
  bump) [[ "$version" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] ;;
  none|pending) test -z "$version" ;;
  *) false ;;
esac
[[ "$main" =~ ^[0-9a-f]{40}$ ]]
printf 'action=%s\nversion=%s\nmain=%s\n' "$action" "$version" "$main" >> "$GITHUB_OUTPUT"
{ echo '### Version at land'; echo; cat "$RUNNER_TEMP/plan-reason.txt"; } >> "$GITHUB_STEP_SUMMARY"'''),
        },
        {
            'if': "steps.plan.outputs.action == 'bump'",
            'env': {
                'VERSION': '${{ steps.plan.outputs.version }}',
            },
            'run': _body(r'''
set -euo pipefail
git switch -C "$BUMP_BRANCH"
python3 -I scripts/version_at_land.py apply --to "$VERSION" > "$RUNNER_TEMP/surfaces.txt"
xargs git add -- < "$RUNNER_TEMP/surfaces.txt"
{
  echo "chore(release): bump version to $VERSION"
  echo
  echo "Landing this pull request releases v$VERSION: release-tag.yml tags the"
  echo "observatory reconcile that covers it."
  echo
  sed -n '1,$p' "$RUNNER_TEMP/plan-reason.txt"
  echo
  echo 'Version-Bump: skip reason="the version-at-land bump itself"'
} > "$RUNNER_TEMP/message.txt"
git -c user.name=vellum-version-at-land \
  -c user.email=vellum-version-at-land@users.noreply.github.com \
  commit --quiet --file "$RUNNER_TEMP/message.txt"
mkdir -p "$RUNNER_TEMP/bump"
git format-patch -1 --stdout HEAD > "$RUNNER_TEMP/bump/bump.patch"
{
  echo "Automated version bump from \`version-at-land.yml\`: **$VERSION**."
  echo
  echo "Landing it releases \`v$VERSION\`. release-tag.yml tags it only if this pull request is still exactly"
  echo "the bump version-at-land derives. Lands by merge commit; never squash or rebase."
  echo
  echo '```'
  cat "$RUNNER_TEMP/plan-reason.txt"
  echo '```'
} > "$RUNNER_TEMP/bump/body.md"'''),
        },
        {
            'id': 'standing',
            'if': "steps.plan.outputs.action == 'bump'",
            'env': {
                'GH_TOKEN': '${{ github.token }}',
            },
            'run': _body(r'''
set -euo pipefail
number="$(gh api --method GET "repos/$GITHUB_REPOSITORY/pulls" -f state=open \
  -f head="$GITHUB_REPOSITORY_OWNER:$BUMP_BRANCH" -f per_page=100 --jq '.[0].number // ""')"
[[ -z "$number" || "$number" =~ ^[0-9]+$ ]]
current=false
if [ -n "$number" ] && git fetch --quiet --no-tags origin "refs/heads/$BUMP_BRANCH"; then
  ours="$(git -c core.hooksPath=/dev/null patch-id --stable < "$RUNNER_TEMP/bump/bump.patch" | cut -d' ' -f1)"
  theirs="$(git -c core.hooksPath=/dev/null show FETCH_HEAD | git patch-id --stable | cut -d' ' -f1)"
  bot="$(printf '%s\n%s\n%s\n%s' vellum-version-at-land vellum-version-at-land@users.noreply.github.com \
    vellum-version-at-land vellum-version-at-land@users.noreply.github.com)"
  if [ "$(git rev-list --count origin/main..FETCH_HEAD)" = 1 ] && [ -n "$ours" ] && [ "$ours" = "$theirs" ] \
    && [ "$(git log -1 --format=%an%n%ae%n%cn%n%ce FETCH_HEAD)" = "$bot" ] \
    && git log -1 --format=%B FETCH_HEAD \
      | grep -qxF 'Version-Bump: skip reason="the version-at-land bump itself"'; then
    current=true
    echo "::notice::#$number already proposes this bump"
  fi
fi
printf 'number=%s\ncurrent=%s\n' "$number" "$current" >> "$GITHUB_OUTPUT"'''),
        },
        {
            'if': "steps.plan.outputs.action == 'bump' && steps.standing.outputs.current != 'true'",
            'uses': 'actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a',
            'with': {
                'name': 'version-bump',
                'path': '${{ runner.temp }}/bump/',
                'retention-days': '3',
                'if-no-files-found': 'error',
            },
        },
    ]],
    ('version-at-land.yml', 'push'): [[
        {
            'workflow': {
                'on': {
                    'push': {
                        'branches': [
                            'main',
                        ],
                    },
                    'schedule': [
                        {
                            'cron': '41 */6 * * *',
                        },
                    ],
                    'workflow_dispatch': '',
                },
                'permissions': {
                    'contents': 'read',
                    'pull-requests': 'read',
                },
                'concurrency': {
                    'group': 'version-at-land',
                    'cancel-in-progress': 'false',
                },
                'env': {
                    'BUMP_BRANCH': 'automation/version-bump',
                },
            },
            'job': {
                'needs': 'plan',
                'if': "needs.plan.outputs.action == 'bump' && needs.plan.outputs.current != 'true'",
                'runs-on': 'ubuntu-latest',
                'timeout-minutes': '10',
            },
        },
        {
            'uses': 'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1',
            'with': {
                'ref': '${{ needs.plan.outputs.main }}',
                'persist-credentials': 'false',
            },
        },
        {
            'uses': 'actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c',
            'with': {
                'name': 'version-bump',
                'path': '${{ runner.temp }}/bump',
            },
        },
        {
            'env': {
                'MAIN': '${{ needs.plan.outputs.main }}',
            },
            'run': _body(r'''
set -euo pipefail
[[ "$MAIN" =~ ^[0-9a-f]{40}$ ]]
test "$(git rev-parse HEAD)" = "$MAIN"
allowed=(
  CMakeLists.txt
  README.md
  cli/tests/test_agent_instructions_artifact.py
  cli/tests/test_cli.py
  cli/tests/test_contamination_scanner.py
  cli/tests/test_dev.py
  cli/tests/test_installer.py
  cli/vellum_cli.py
  docs/cli/contract.md
  docs/cli/install-artifact.md
  scripts/build_sdk_artifact.py
  scripts/install.ps1
  scripts/install.sh
  scripts/verify_sdk_artifact.py
  scripts/INSTALLER_SHA256SUMS
)
patch="$RUNNER_TEMP/bump/bump.patch"
while IFS= read -r path; do
  ok=false
  for entry in "${allowed[@]}"; do [ "$path" = "$entry" ] && ok=true; done
  if [ "$ok" != true ]; then
    echo "::error::the planned bump touches $path, which is not a version surface"
    exit 1
  fi
done < <(git -c core.hooksPath=/dev/null apply --numstat "$patch" | cut -f3)
git -c core.hooksPath=/dev/null -c user.name=vellum-version-at-land \
  -c user.email=vellum-version-at-land@users.noreply.github.com am --quiet "$patch"
test "$(git rev-parse HEAD^)" = "$MAIN"'''),
        },
        {
            'id': 'bump-token',
            'uses': 'actions/create-github-app-token@1b10c78c7865c340bc4f6099eb2f838309f1e8c3',
            'with': {
                'app-id': '${{ secrets.SHIPYARD_APP_ID }}',
                'private-key': '${{ secrets.SHIPYARD_APP_PRIVATE_KEY }}',
                'owner': 'Generous-Corp',
                'repositories': 'vellum',
                'permission-contents': 'write',
                'permission-pull-requests': 'write',
            },
        },
        {
            'env': {
                'GH_TOKEN': '${{ steps.bump-token.outputs.token }}',
                'MAIN': '${{ needs.plan.outputs.main }}',
                'VERSION': '${{ needs.plan.outputs.version }}',
                'NUMBER': '${{ needs.plan.outputs.number }}',
            },
            'run': _body(r'''
set -euo pipefail
[[ "$VERSION" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]
[[ -z "$NUMBER" || "$NUMBER" =~ ^[0-9]+$ ]]
clean() { /usr/bin/env -i PATH=/usr/bin:/bin HOME="$RUNNER_TEMP" GH_TOKEN="$GH_TOKEN" GIT_CONFIG_NOSYSTEM=1 "$@"; }
auth="http.https://github.com/.extraheader=AUTHORIZATION: basic $(printf 'x-access-token:%s' "$GH_TOKEN" | base64 -w0)"
remote="https://github.com/$GITHUB_REPOSITORY"
live="$(clean git -c core.hooksPath=/dev/null -c "$auth" ls-remote "$remote" refs/heads/main | cut -f1)"
if [ "$live" != "$MAIN" ]; then
  echo "::notice::main moved from $MAIN to $live while the bump was planned; the next run proposes it"
  exit 0
fi
lease="$(clean git -c core.hooksPath=/dev/null -c "$auth" ls-remote "$remote" "refs/heads/$BUMP_BRANCH" | cut -f1)"
clean git -c core.hooksPath=/dev/null -c "$auth" push \
  --force-with-lease="refs/heads/$BUMP_BRANCH:$lease" "$remote" "HEAD:refs/heads/$BUMP_BRANCH"
title="chore(release): bump version to $VERSION"
if [ -n "$NUMBER" ]; then
  clean /usr/bin/gh api --method PATCH "repos/$GITHUB_REPOSITORY/pulls/$NUMBER" -f title="$title" \
    -F body=@"$RUNNER_TEMP/bump/body.md" > /dev/null
  echo "updated #$NUMBER to propose $VERSION" >> "$GITHUB_STEP_SUMMARY"
else
  clean /usr/bin/gh pr create --repo "$GITHUB_REPOSITORY" --base main --head "$BUMP_BRANCH" \
    --title "$title" --body-file "$RUNNER_TEMP/bump/body.md" >> "$GITHUB_STEP_SUMMARY"
fi'''),
        },
    ]],
}


def run_body_problems(name: str, workflow: str, allowlist: dict | None = None) -> list[str]:
    """Credential-holding jobs of release workflows run only allowlisted steps,
    and each such step holds the token its body needs."""
    allowlist = RUN_ALLOWLIST if allowlist is None else allowlist
    try:
        parsed = parse_workflow(workflow)
    except PolicyError as error:
        return [f"{name}: {error}"]
    found = []
    for job, job_steps in parsed.jobs.items():
        keyed = [step for step in job_steps if SIGNING_KEY.search(step.text)]
        for step in keyed:
            scope = {**(step.inherited or {}), **(step.env or {})}
            if any(CREDENTIAL_NAME.fullmatch(key) for key in scope) or reaches_credential(
                    SIGNING_KEY.sub("", step.text)):
                found.append(f"{name}: {step.title} holds the signing key with a GitHub credential in scope")
        if not reviewed_job(parsed.job_text[job], name):
            continue
        if parsed.directory_defaults & {"workflow", f"job {job}"}:
            found.append(f"{name}: job {job} is reviewed, so neither it nor the workflow may set a default "
                         f"working-directory")
        for key in sorted(parsed.job_keys[job] & UNSUPPORTED_CREDENTIAL_JOB_KEYS):
            found.append(f"{name}: job {job} holds a credential and sets {key}")
        variants = allowlist.get((name, job), [])
        # The job's and workflow's settings reach every step (env such as
        # BASH_ENV, runs-on, permissions, environment), so they are reviewed
        # text too: the first entry of each variant.
        fields = [parsed.job_context[job], *(step.fields for step in job_steps)]
        if fields not in variants:
            # Name the first entry that differs from the closest variant.
            closest = max(variants, default=[], key=lambda variant: next(
                (i for i, (a, b) in enumerate(zip(fields, variant)) if a != b), min(len(fields), len(variant))))
            index = next((i for i, (a, b) in enumerate(zip(fields, closest)) if a != b), min(len(fields), len(closest)))
            if index == 0:
                found.append(f"{name}: the job and workflow settings of job {job} match no variant RUN_ALLOWLIST "
                             f"lists for it (unsupported run body): env, runs-on, permissions and every other "
                             f"key but name and steps are reviewed")
            else:
                title = job_steps[index - 1].title if index - 1 < len(job_steps) else f"the end of job {job}"
                found.append(f"{name}: {title} of job {job} matches no step sequence RUN_ALLOWLIST lists for it "
                             f"(unsupported run body): it differs at step {index} of {len(job_steps)}")
        minted: set[str] = set()
        for step in job_steps:
            if step.uses is not None and not PINNED_ACTION.fullmatch(step.uses):
                found.append(f"{name}: {step.title} uses {step.uses!r}, not an action pinned to a commit")
            if step.run is not None and "${{" in step.run:
                found.append(f"{name}: {step.title} has an expression in its run body; pass it through env")
            for word in PATH_OR_ENV_WRITES:
                if word in (step.run or "") or word in "\n".join([*(step.env or {}), *(step.inherited or {})]):
                    found.append(f"{name}: {step.title} names {word}; a reviewed job never changes the PATH or "
                                 f"environment of its later steps")
            scope = {**(step.inherited or {}), **(step.env or {})}
            if "GITHUB_TOKEN" in scope:
                found.append(f"{name}: {step.title} sets GITHUB_TOKEN; pass a token as GH_TOKEN")
            token = scope.get("GH_TOKEN")
            app = STEP_TOKEN.fullmatch(token or "")
            proven = app is not None and app["id"] in minted and "GH_TOKEN" in (step.env or {})
            if step.run is not None and ADMIN_ENDPOINT.search(step.run):
                if not proven or any(ACTIONS_TOKEN_REFERENCE.search(value) for value in scope.values()):
                    found.append(f"{name}: {step.title} reads an Administration-only endpoint without the App token")
            elif token is not None and not proven and token != ACTIONS_TOKEN:
                found.append(f"{name}: {step.title} sets GH_TOKEN to neither the Actions token "
                             f"nor a token minted earlier in its job")
            if step.id and step.uses and APP_TOKEN_ACTION.fullmatch(step.uses):
                minted.add(step.id)
    return found


def isolation_problems(workflows: dict[str, str], root: Path) -> list[str]:
    found = []
    for name, text in sorted(workflows.items()):
        try:
            parsed = parse_workflow(text)
        except PolicyError as error:
            found.append(f"{name}: {error}")
            continue
        for job, job_steps in parsed.jobs.items():
            if name == VERSION_WORKFLOW and parsed.secrets[job] and any(
                    PYTHON_CALL.search(step.run or "") for step in job_steps):
                found.append(f"{name}: job {job} holds a secret and runs Python in one of its steps")
            for step in job_steps:
                run = step.run or ""
                if name in RELEASE_WORKFLOWS:
                    for call in PYTHON_CALL.finditer(run):
                        if "I" not in call["flags"].replace("-", ""):
                            found.append(f"{name}: {step.title} runs `{call.group(0).strip()}` without -I")
                    if name in SECRET_ISOLATED and SECRET.search(step.text) and PYTHON_CALL.search(run):
                        found.append(f"{name}: {step.title} holds a secret and runs Python")
                for call in RENDERER_VERIFIER_CALL.finditer(run):
                    if "I" not in call["flags"].replace("-", ""):
                        found.append(f"{name}: {step.title} runs the renderer verifier without -I")
        if name in RELEASE_WORKFLOWS:
            found.extend(run_body_problems(name, text))
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
