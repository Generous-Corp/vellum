#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

EXPECTED_RUNNERS = {
    "authority-activation.yml": ("__hosted__", "ubuntu-latest"),
    "authority-release.yml": ("__hosted__", "ubuntu-latest"),
    "gpu-macos.yml": (
        "VELLUM_LOCAL_MACOS_RUNS_ON_JSON",
        '["macos-15"]',
    ),
    "merge-on-green.yml": (
        "VELLUM_LINUX_RUNS_ON_JSON",
        '["ubuntu-latest"]',
    ),
    "product-quality.yml": (
        "VELLUM_LINUX_RUNS_ON_JSON",
        '["ubuntu-latest"]',
    ),
    "provenance.yml": (
        "VELLUM_LINUX_RUNS_ON_JSON",
        '["ubuntu-latest"]',
    ),
    "pulp-observatory-receiver.yml": (
        "VELLUM_LINUX_RUNS_ON_JSON",
        '["ubuntu-latest"]',
    ),
    "pulp-observatory-watchdog.yml": ("__hosted__", "ubuntu-latest"),
    "review-verdict.yml": ("__hosted__", "ubuntu-24.04"),
    "review-verdict-trigger.yml": ("__hosted__", "ubuntu-24.04"),
    "readme-quick-start.yml": (
        "VELLUM_MACOS_RUNS_ON_JSON",
        '["macos-15"]',
    ),
    "sdk-release.yml": ("__mixed__", ""),
}

HOSTED_LABELS = (
    "ubuntu-latest",
    "ubuntu-24.04",
    "windows-latest",
    "macos-latest",
    "macos-13",
    "macos-14",
    "macos-15",
    "macos-26",
)

NODE24_ACTION_PINS = {
    "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",  # v7.0.1
    "actions/create-github-app-token": "1b10c78c7865c340bc4f6099eb2f838309f1e8c3",
    "actions/download-artifact": "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",  # v8.0.1
    "actions/setup-node": "820762786026740c76f36085b0efc47a31fe5020",  # v7.0.0
    "actions/setup-python": "ece7cb06caefa5fff74198d8649806c4678c61a1",  # v6.3.0
    "actions/upload-artifact": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",  # v7.0.1
    "browser-actions/setup-chrome": "2e1d749697dd1612b833dba4a722266286fbefcd",  # v2.1.2
}
ACTION_USE = re.compile(r"^\s*-?\s*uses:\s+([^./\s][^@\s]+)@([0-9a-f]{40})\s*$")

CHROME_ACTION = "browser-actions/setup-chrome"

# Jobs that drive a browser fixture. Job environments do not inherit, so each
# one must provision the pinned browser itself rather than rely on a browser
# happening to exist on the runner.
BROWSER_JOBS = {
    "gpu-macos.yml": ("gpu-macos-arm64", "sterile-consumer"),
    "sdk-release.yml": ("validate-sdk-artifacts",),
}

JOB_HEADER = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$")
GPU_GUARD = (
    "    if: github.event_name != 'pull_request' || "
    "github.event.pull_request.head.repo.full_name == github.repository || "
    "vars.VELLUM_LOCAL_MACOS_RUNS_ON_JSON == ''"
)

PULP_REPOSITORY = "Generous-Corp/pulp"
STEP_OUTPUT_REF = re.compile(
    r"^\$\{\{\s*steps\.([A-Za-z0-9_-]+)\.outputs\.([A-Za-z0-9_-]+)\s*\}\}$"
)
# Text a pinning step must contain to prove its output is derived from
# evidence committed in the Vellum tree, never from a live Pulp branch name.
COMMITTED_PIN_SOURCES = (
    "last_scanned_commit",  # provenance/pulp-observatory/cursor.json
    "pulp_candidate_commit",  # authority pending record
)
# A pinning step may still name a live branch for events that are not
# pull_request or merge_group; those runs pin themselves with a before/after
# live-SHA equality test instead. The literal must sit in such a branch.
LIVE_BRANCH_GUARD = 'if event in ("pull_request", "merge_group"):'
VERIFIED_POST_CHECKOUT_PINS = {
    ("provenance.yml", ".exact-boundary-pulp"),
    ("provenance.yml", ".mirror-pulp"),
}
CHECKOUT_STEP = re.compile(
    r"^(?P<indent> +)- name: (?P<name>.+)\n"
    r"(?:(?P=indent)  .*\n)*?"
    r"(?P=indent)  uses: actions/checkout@[0-9a-f]{40}\n"
    r"(?P<with>(?:(?P=indent)  .*\n)+)",
    re.M,
)
STEP_START = re.compile(r"^(?P<indent> +)- name: .*$", re.M)


MIRROR_EDGE_PIN = "tools/provenance/mirror_edge_patterns.pin.json"
# Pulp named in any form a workflow can reach it by: https, ssh, a
# server_url or repository_owner expression, or a plain owner/repo slug, in
# any letter case. Other Generous-Corp repositories (pulp-planning) do not match.
PULP_REFERENCE = re.compile(
    r"(?:generous-corp|\$\{\{\s*github\.repository_owner\s*\}\})\s*/\s*pulp(?:\.git)?(?![\w-])",
    re.I,
)
PINNED_PULP_FETCH_NAME = "Fetch the pinned Pulp mirror-edge table commit"
PINNED_PULP_GIT_DIR = '"$RUNNER_TEMP/mirror-edge-pulp.git"'
# The only step allowed to fetch Pulp outside actions/checkout, byte for byte:
# it reads the commit from the committed pin and fetches exactly that
# commit's trees into a bare repository. Any added, removed or reordered line
# fails the comparison.
PINNED_PULP_FETCH = """      - name: Fetch the pinned Pulp mirror-edge table commit
        shell: bash
        run: |
          set -euo pipefail
          pin_commit="$(python3 - <<'PY'
          import json
          import re
          pin = json.load(open("tools/provenance/mirror_edge_patterns.pin.json"))
          commit = pin["pulp_commit"]
          if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
              raise SystemExit("mirror edge table pin is not a full commit SHA")
          print(commit)
          PY
          )"
          pulp_git="$RUNNER_TEMP/mirror-edge-pulp.git"
          git init -q --bare "$pulp_git"
          git -C "$pulp_git" remote add origin https://github.com/Generous-Corp/pulp.git
          git -C "$pulp_git" fetch -q --depth 1 --filter=blob:none --no-tags origin "$pin_commit"
          git -C "$pulp_git" update-ref --no-deref HEAD "$pin_commit"
          test "$(git -C "$pulp_git" rev-parse HEAD)" = "$pin_commit\""""
# The single use of the pinned repository outside the fetch step.
PINNED_PULP_GIT_DIR_USE = f"            --mirror-edge-pulp-root {PINNED_PULP_GIT_DIR} \\"
# The one step that names Pulp only to read GitHub metadata and the
# routing-contract execution artifact. Every gh invocation in it must be one
# of these, verbatim; anything else (contents/, git/blobs, raw downloads,
# another artifact) fails.
PULP_API_READER = ("provenance.yml", "Test authority protocol and negative controls")
PULP_API_READER_GH_CALLS = {
    'api "repos/$GITHUB_REPOSITORY/commits/$GITHUB_SHA/check-runs?per_page=100"',
    'api "repos/Generous-Corp/pulp/commits/$PULP_ACCEPTANCE_SHA/check-runs?per_page=100"',
    'api "repos/$repository/actions/runs/$run_id"',
    'api "repos/$repository/pulls?state=open&per_page=100"',
    'api "repos/$repository/pulls/$pull_number"',
    'api "repos/$repository/compare/$base_commit...$head_commit"',
    'api "repos/$repository/git/commits/$merge_base_commit"',
    'api "repos/$repository/git/commits/$head_commit"',
    'api "repos/$repository/git/trees/$merge_base_tree?recursive=1"',
    'api "repos/$repository/git/trees/$head_tree?recursive=1"',
    "api repos/Generous-Corp/pulp/git/ref/heads/main",
    'run download "$run_id" --name pulp-vellum-routing-contract-execution',
    'run download "$run_id" --name vellum-parity-proof-execution',
}
PULP_API_READER_GIT_CALLS = {"git -C .exact-boundary-pulp rev-parse HEAD"}
# The reader step itself, byte for byte (sha256 of its text without trailing
# blank and comment lines). Shell and Python can assemble a command or a
# repository name in ways no line check reads (`eval "g""h ..."`, a
# backslash continuation inside a word, a glued `os.system` argument, a
# value written to a file for a later step), so any change to the reader is
# a reviewed change to this digest. The checks below only explain a failure.
PULP_API_READER_SHA256 = "65cd477615301089437089e468cfcf61a4a41dbf7c5c3175550770903b9b20d1"
# Every reader line that names Pulp, stripped, verbatim. A new line naming
# Pulp (an anchor, an env export, a command through a variable) fails.
PULP_API_READER_PULP_LINES = {
    '"repos/Generous-Corp/pulp/commits/$PULP_ACCEPTANCE_SHA/check-runs?per_page=100" \\',
    'repository == "Generous-Corp/pulp"',
    'if [[ "$repository" == "Generous-Corp/pulp" ]]; then',
    "for repository in Generous-Corp/pulp Generous-Corp/vellum; do",
    'for repository in ("Generous-Corp/pulp", "Generous-Corp/vellum"):',
    'live_pulp_main="$(gh api repos/Generous-Corp/pulp/git/ref/heads/main --jq .object.sha)"',
    '"$(gh api repos/Generous-Corp/pulp/git/ref/heads/main --jq .object.sha)"',
}
# A command reached through a variable (`g=git; $g clone ...`).
COMMAND_INDIRECTION = re.compile(
    r"\b\w+=['\"]?(?:git|gh|curl|wget|python3?|bash|sh)\b"
    r"|(?:^|[;&|(`]|\$\()\s*\$\{?\w+\}?\s+(?:repo|clone|api|fetch|run|-c)\b"
)
STEP_STATE_EXPORT = re.compile(r"GITHUB_(?:ENV|OUTPUT|PATH)\b")
# An anchor or alias name is any run of non-space, non-flow characters.
YAML_ANCHOR_OR_ALIAS = re.compile(r"(?:^\s*-\s+|:\s+|[\[{,]\s*|^\s*)[&*][^\s\[\]{},]")
# Transports that bypass gh entirely.
PULP_API_READER_FORBIDDEN = re.compile(
    r"\b(?:curl|wget|urllib|requests|http\.client|socket|subprocess|nc)\b"
    r"|raw\.githubusercontent|codeload\.|\b(?:tarball|zipball)\b|contents/|git/blobs",
    re.I,
)
# Triggers whose runs act on unmerged or queued code: a pull request, a
# merge-queue entry, or a workflow_run fired by either.
QUEUE_GATED_EVENTS = {
    "pull_request", "pull_request_target", "pull_request_review", "merge_group",
    "workflow_run", "workflow_call",
}
BLOCK_SCALAR = re.compile(r"(?:^|:|-)\s*[|>][-+0-9]*\s*(?:#.*)?$")


def indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def yaml_line_kinds(text: str) -> list[str]:
    """Classify each line as 'blank', 'comment' (a comment a YAML parser
    drops), 'block' (block-scalar content, including lines that look like
    comments: they are script text), or 'node'. A block scalar's content is
    every following line indented beyond the key that opened it; a less
    indented line ends it, which is the YAML rule for a parent's indentation."""
    kinds: list[str] = []
    block_parent: int | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if block_parent is not None:
            if not stripped or indent_of(line) > block_parent:
                kinds.append("block")
                continue
            block_parent = None
        if not stripped:
            kinds.append("blank")
        elif stripped.startswith("#"):
            kinds.append("comment")
        else:
            kinds.append("node")
            if BLOCK_SCALAR.search(line):
                key = re.match(r"^(\s*)(?:-\s+)?", line)
                block_parent = len(key.group(0)) if key else indent_of(line)
    return kinds


def workflow_events(text: str) -> set[str]:
    """Event names under the top-level `on:` key, in scalar, flow-sequence,
    flow-mapping or block form."""
    lines = text.splitlines()
    kinds = yaml_line_kinds(text)
    for index, line in enumerate(lines):
        match = re.match(r"""^(?:on|"on"|'on'|true):(.*)$""", line)
        if kinds[index] != "node" or match is None:
            continue
        inline = re.sub(r"\s#.*$", "", match.group(1)).strip()
        if inline:
            if inline[0] in "[{":
                return {
                    item.split(":")[0].strip().strip("'\"")
                    for item in inline.strip("[]{}").split(",") if item.strip()
                }
            return {inline.strip("'\"")}
        events: set[str] = set()
        child: int | None = None
        for following, kind in zip(lines[index + 1:], kinds[index + 1:]):
            if kind in ("blank", "comment"):
                continue
            depth = indent_of(following)
            if depth == 0:
                break
            child = depth if child is None else child
            if depth == child:
                events.add(re.sub(r"^-\s*", "", following.strip()).split(":")[0].strip("'\""))
        return events
    return set()


def queue_gated(text: str) -> bool:
    return bool(workflow_events(text) & QUEUE_GATED_EVENTS)


def workflow_items(text: str) -> tuple[list[dict[str, object]], list[tuple[int, str]]]:
    """Split a workflow into steps and the lines outside every step.

    Each `- ` item of a `steps:` list is a step, whatever key it starts with.
    A step records its name (None when unnamed), whether `name:` is its first
    key, and its lines; YAML comments are kept out of `code`, script text is
    not. Lines outside steps (workflow and job headers, env, container,
    services, job-level uses) are returned with their line numbers."""
    lines = text.splitlines()
    kinds = yaml_line_kinds(text)
    steps: list[dict[str, object]] = []
    in_step = [False] * len(lines)
    index = 0
    while index < len(lines):
        if kinds[index] == "node" and re.match(r"^\s*steps:\s*$", lines[index]):
            owner = indent_of(lines[index])
            item_indent: int | None = None
            cursor = index + 1
            current: dict[str, object] | None = None
            while cursor < len(lines):
                line, kind = lines[cursor], kinds[cursor]
                if kind == "node":
                    depth = indent_of(line)
                    if item_indent is None:
                        if not line.strip().startswith("- ") or depth < owner:
                            break
                        item_indent = depth
                    if depth < item_indent or (depth == item_indent and not line.strip().startswith("- ")):
                        break
                    if depth == item_indent:
                        first = re.match(r"^\s*-\s+([A-Za-z0-9_-]+):\s*(.*)$", line)
                        current = {"start": cursor, "lines": [], "code": [], "kinds": [],
                                   "first_key": first.group(1) if first else "",
                                   "name": None, "key_indent": item_indent + 2}
                        steps.append(current)
                if current is not None:
                    current["lines"].append(line)
                    current["kinds"].append(kind)
                    if kind != "comment":
                        current["code"].append(line)
                    in_step[cursor] = True
                    named = re.match(r"^\s*(?:-\s+)?name:\s*(.+?)\s*$", line)
                    if kind == "node" and named and current["name"] is None and (
                        indent_of(line) == item_indent or indent_of(line) == current["key_indent"]
                    ):
                        current["name"] = named.group(1).strip("'\"")
                cursor += 1
            index = cursor
            continue
        index += 1
    outside = [(number, line) for number, (line, kind, inside) in enumerate(zip(lines, kinds, in_step), 1)
               if not inside and kind in ("node", "block")]
    return steps, outside


def steps_lines(steps: list[dict[str, object]]) -> set[int]:
    covered: set[int] = set()
    for step in steps:
        start = int(step["start"])
        covered.update(range(start, start + len(step["lines"])))
    return covered


def open_quoted_scalar(line: str) -> bool:
    """Whether a YAML node line opens a single- or double-quoted scalar
    without closing it, at any position a scalar can start (after `- `, a key,
    or a flow-collection separator). A continuation line of such a scalar may
    start with `#` and still be text, so the rule refuses the form instead of
    reading it. A plain scalar that happens to contain `: '` is refused too,
    which is fail-closed."""
    for start in re.finditer(r"(?:^\s*-\s+|^\s*|:\s+|[\[{,]\s*)(['\"])", line):
        quote, index = start.group(1), start.end()
        closed = False
        while index < len(line):
            char = line[index]
            if quote == '"' and char == "\\":
                index += 2
                continue
            if char == quote:
                if quote == "'" and line[index + 1:index + 2] == "'":
                    index += 2
                    continue
                closed = True
                break
            index += 1
        if not closed:
            return True
    return False


def step_text(step: dict[str, object]) -> str:
    """The step as written, without the trailing blank and comment lines that
    belong to whatever follows it."""
    lines, kinds = list(step["lines"]), list(step["kinds"])
    while lines and (kinds[-1] == "comment" or not lines[-1].strip()):
        lines.pop()
        kinds.pop()
    return "\n".join(lines)


def gh_calls(code: str) -> list[str]:
    """Each gh invocation as `<subcommand> <path or run id> [--name artifact]`,
    with options that only shape output dropped."""
    joined = re.sub(r"\\\n\s*", " ", code)
    calls = []
    for match in re.finditer(r"\bgh\s+(.*)", joined):
        tokens = re.findall(r'"[^"]*"|\S+', match.group(1))
        if tokens[:1] == ["api"]:
            paths = [t for t in tokens[1:] if not t.startswith("-") and re.match(r'"?repos/', t)]
            calls.append("api " + (paths[0] if paths else "<no path>"))
        elif tokens[:2] == ["run", "download"]:
            name = tokens[tokens.index("--name") + 1] if "--name" in tokens else "<no name>"
            calls.append(f"run download {tokens[2] if len(tokens) > 2 else ''} --name {name}")
        else:
            calls.append(" ".join(tokens[:2]))
    return calls


def pulp_reach_problems(workflow: str, text: str) -> tuple[list[str], int]:
    """Problems with how a queue-gated workflow reaches Pulp, and how many
    steps name Pulp. A step that names Pulp in any form must be a named
    actions/checkout of the canonical slug (held by the checkout pin rule),
    the canonical pinned fetch, or the reviewed API reader. Nothing outside a
    step may name Pulp, and the pinned bare repository is used nowhere but
    its one verifier argument."""
    problems: list[str] = []
    reaching = 0
    fetches = 0
    lines, kinds = text.splitlines(), yaml_line_kinds(text)
    for number, (line, kind) in enumerate(zip(lines, kinds), 1):
        if kind != "node":
            continue
        if YAML_ANCHOR_OR_ALIAS.search(line):
            problems.append(f"line {number} uses a YAML anchor or alias")
        if open_quoted_scalar(line):
            problems.append(f"line {number} opens a quoted scalar that spans lines")
    steps, outside = workflow_items(text)
    # Comment-looking lines are scanned too: proving a line is a YAML comment
    # rather than quoted-scalar text is not worth the risk of hiding code.
    covered = steps_lines(steps)
    outside = [(number, line) for number, line in enumerate(lines, 1)
               if line.strip() and number - 1 not in covered]
    for number, line in outside:
        if PULP_REFERENCE.search(line):
            problems.append(f"line {number} names Pulp outside a step")
        if re.search(r"mirror-edge-pulp", line, re.I):
            problems.append(f"line {number} uses the pinned Pulp repository outside a step")
    pulp_checkout_steps = 0
    for step in steps:
        code = "\n".join(line for line in step["lines"] if line.strip())
        name = step["name"]
        label = repr(name) if name else f"unnamed step at line {int(step['start']) + 1}"
        if name == PINNED_PULP_FETCH_NAME:
            fetches += 1
            reaching += 1
            if step_text(step) != PINNED_PULP_FETCH:
                problems.append(f"{label} is not the canonical pinned Pulp fetch")
            continue
        uses = [line for line in step["lines"] if re.search(r"mirror-edge-pulp", line, re.I)]
        if uses and uses != [PINNED_PULP_GIT_DIR_USE]:
            problems.append(f"{label} uses the pinned Pulp repository beyond reading it")
        is_checkout = re.search(r"^ +(?:-\s+)?uses: actions/checkout@", code, re.M)
        if is_checkout and re.search(r"^ +repository:\s*.*\$\{\{", code, re.M):
            problems.append(f"{label} checks out a repository named by an expression")
        if not PULP_REFERENCE.search(code):
            continue
        reaching += 1
        if is_checkout:
            repository = re.findall(r"^ +repository: (.+)$", code, re.M)
            mentions = [line for line in step["lines"] if PULP_REFERENCE.search(line)]
            if repository != [PULP_REPOSITORY] or mentions != [f"{' ' * (int(step['key_indent']) + 2)}repository: {PULP_REPOSITORY}"]:
                problems.append(f"{label} names Pulp in a non-canonical form")
            # Backup to the count equality below: CHECKOUT_STEP only matches
            # a step led by `- name:`, so a checkout refused here is also
            # missing from pulp_checkouts. This check names the cause.
            elif step["first_key"] != "name":
                problems.append(f"{label} checks out Pulp without a leading name, outside the checkout pin rule")
            else:
                pulp_checkout_steps += 1
            continue
        if (workflow, name) == PULP_API_READER:
            if hashlib.sha256(step_text(step).encode("utf-8")).hexdigest() != PULP_API_READER_SHA256:
                problems.append(f"{label} is not the reviewed API reader")
            unreviewed = [call for call in gh_calls(code) if call not in PULP_API_READER_GH_CALLS]
            git_calls = re.findall(r"\bgit\s+[^\n|;)&]*", code)
            if unreviewed or any(call.strip() not in PULP_API_READER_GIT_CALLS for call in git_calls) \
                    or PULP_API_READER_FORBIDDEN.search(code):
                problems.append(f"{label} transfers Pulp content beyond its reviewed reads: {unreviewed}")
            naming = {line.strip() for line in step["lines"] if PULP_REFERENCE.search(line)}
            if naming - PULP_API_READER_PULP_LINES:
                problems.append(f"{label} names Pulp on an unreviewed line: {sorted(naming - PULP_API_READER_PULP_LINES)}")
            if STEP_STATE_EXPORT.search(code):
                problems.append(f"{label} writes step state another step could read")
            if COMMAND_INDIRECTION.search(code):
                problems.append(f"{label} runs a command through a variable")
            continue
        problems.append(f"{label} reaches Pulp outside actions/checkout or the pinned fetch")
    if fetches > 1:
        problems.append("more than one pinned Pulp fetch step")
    if pulp_checkout_steps != len(pulp_checkouts(text)):
        problems.append("a Pulp checkout is not visible to the checkout pin rule")
    return problems, reaching


def pulp_checkouts(text: str) -> list[dict[str, str]]:
    """Return every actions/checkout step that reads Generous-Corp/pulp."""
    found: list[dict[str, str]] = []
    for match in CHECKOUT_STEP.finditer(text):
        block = match.group(0)
        fields = dict(
            re.findall(r"^ +([a-z-]+): (.+)$", match.group("with"), re.M)
        )
        if not PULP_REFERENCE.fullmatch(fields.get("repository", "").strip()):
            continue
        condition = re.search(r"^ +if: (.+)$", block, re.M)
        found.append(
            {
                "name": match.group("name"),
                "path": fields.get("path", ""),
                "ref": fields.get("ref", ""),
                "if": condition.group(1) if condition else "",
            }
        )
    return found


def step_body(text: str, step_id: str) -> str | None:
    """Return the full text of the step whose `id:` is step_id."""
    marker = re.search(rf"^ +id: {re.escape(step_id)}\s*$", text, re.M)
    if marker is None:
        return None
    starts = [m for m in STEP_START.finditer(text)]
    begin = max(m.start() for m in starts if m.start() < marker.start())
    later = [m.start() for m in starts if m.start() > marker.start()]
    return text[begin : later[0] if later else len(text)]


def named_step_body(text: str, name: str) -> str | None:
    """Return the complete text of a named workflow step."""
    marker = re.search(rf"^ +- name: {re.escape(name)}\s*$", text, re.M)
    if marker is None:
        return None
    starts = [m for m in STEP_START.finditer(text)]
    later = [m.start() for m in starts if m.start() > marker.start()]
    return text[marker.start() : later[0] if later else len(text)]


def named_step_bodies(text: str, name: str) -> list[str]:
    """Return every workflow step body with the given name."""
    markers = list(re.finditer(rf"^ +- name: {re.escape(name)}\s*$", text, re.M))
    starts = [m.start() for m in STEP_START.finditer(text)]
    bodies: list[str] = []
    for marker in markers:
        later = [start for start in starts if start > marker.start()]
        bodies.append(text[marker.start() : later[0] if later else len(text)])
    return bodies


def assert_exact_boundary_pulp_pin(text: str) -> None:
    """Require the exact-boundary checkout's post-checkout pin and proof."""
    checkout = next(
        (row for row in pulp_checkouts(text) if row["path"] == ".exact-boundary-pulp"),
        None,
    )
    if checkout is None:
        raise AssertionError("provenance.yml: exact-boundary Pulp checkout is missing")
    if checkout["ref"] != "main":
        raise AssertionError(
            "exact-boundary checkout must start at literal main before pinning"
        )
    pin = named_step_body(text, "Pin pull-request and queue Pulp reads to committed evidence")
    if pin is None:
        raise AssertionError("exact-boundary checkout has no post-checkout pin step")
    condition = re.search(r"^ +if: (.+)$", pin, re.M)
    expression = condition.group(1) if condition else ""
    for event in ("pull_request", "merge_group"):
        if f"github.event_name == '{event}'" not in expression:
            raise AssertionError(
                f"exact-boundary pin must run for {event} (condition={expression!r})"
            )
    if "last_scanned_commit" not in pin:
        raise AssertionError("exact-boundary pin must derive its SHA from committed cursor evidence")
    if "git -C .exact-boundary-pulp checkout --detach \"$pulp_ref\"" not in pin:
        raise AssertionError("exact-boundary pin must detach the checkout to the committed SHA")
    if 'test "$(git -C .exact-boundary-pulp rev-parse HEAD)" = "$pulp_ref"' not in pin:
        raise AssertionError("exact-boundary pin must verify detached HEAD equals the committed SHA")


def assert_mirror_pulp_pin(text: str) -> None:
    """Require every mirror checkout to pin to the newest landed receipt SHA."""
    checkouts = [row for row in pulp_checkouts(text) if row["path"] == ".mirror-pulp"]
    if not checkouts:
        raise AssertionError("provenance.yml: mirror Pulp checkout is missing")
    if any(row["ref"] != "main" for row in checkouts):
        raise AssertionError("mirror Pulp checkout must start at literal main before pinning")
    name = "Pin mirror Pulp reads to committed Decision 0010 evidence"
    pins = named_step_bodies(text, name)
    if len(pins) < len(checkouts):
        raise AssertionError("every mirror Pulp checkout needs a post-checkout pin step")
    for pin in pins:
        condition = re.search(r"^ +if: (.+)$", pin, re.M)
        expression = condition.group(1) if condition else ""
        if "steps.mirror-receipts.outputs.present == 'true'" not in expression:
            raise AssertionError("mirror Pulp pin must run only when a landed receipt is present")
        if "refs[-1]" not in pin or "receipt.get(\"pulp_sha\")" not in pin:
            raise AssertionError("mirror Pulp pin must derive the newest receipt pulp_sha")
        if "re.fullmatch(r\"[0-9a-f]{40}\", ref)" not in pin:
            raise AssertionError("mirror Pulp pin must require a full 40-hex SHA")
        if 'git -C .mirror-pulp checkout --detach "$pulp_ref"' not in pin:
            raise AssertionError("mirror Pulp pin must detach the checkout to the receipt SHA")
        if 'test "$(git -C .mirror-pulp rev-parse HEAD)" = "$pulp_ref"' not in pin:
            raise AssertionError("mirror Pulp pin must verify detached HEAD equals the receipt SHA")


def workflow_jobs(text: str) -> dict[str, str]:
    """Split the `jobs:` mapping into per-job bodies.

    Raises on any two-space entry it cannot recognize as a job header. Silently
    folding an unrecognized header into the previous job would attribute that
    job's steps to its neighbour, which would let a provisioning assertion pass
    for a job that does not provision anything.
    """
    lines = text.splitlines()
    jobs: dict[str, list[str]] = {}
    current: str | None = None
    inside = False
    for number, line in enumerate(lines, start=1):
        if line.rstrip() == "jobs:":
            inside = True
            continue
        if not inside:
            continue
        if line.strip() and not line.startswith(" "):
            break
        header = JOB_HEADER.match(line)
        if header:
            current = header.group(1)
            jobs[current] = []
            continue
        # A two-space entry that is not a recognized header would corrupt the
        # split. Job bodies are indented four or more spaces.
        if re.match(r"^  [^\s]", line):
            raise AssertionError(
                f"line {number}: unrecognized two-space entry in jobs mapping: "
                f"{line.strip()!r}"
            )
        if current is not None:
            jobs[current].append(line)
    if not jobs:
        raise AssertionError("no jobs parsed; the jobs mapping shape changed")
    return {name: "\n".join(body) for name, body in jobs.items()}


def assert_privileged_release_coordinate_isolation(text: str) -> None:
    """Keep workflow_run coordinates out of the privileged finalizer steps."""
    finalizer = workflow_jobs(text).get("finalize-sdk-release")
    if finalizer is None:
        raise AssertionError("sdk-release.yml: finalize-sdk-release is missing")
    _, marker, steps = finalizer.partition("    steps:\n")
    if not marker:
        raise AssertionError("sdk-release.yml: finalizer steps are missing")
    for coordinate in (
        "github.event.workflow_run.id",
        "github.event.workflow_run.head_branch",
        "github.event.workflow_run.head_sha",
    ):
        if coordinate in steps:
            raise AssertionError(
                "privileged finalizer steps consume untrusted workflow_run "
                f"coordinate: {coordinate}"
            )
    required_order = (
        "Check out trusted finalizer controls",
        "Download validated release coordinates",
        "Load validated release coordinates",
        "Mint one-repository finalizer token",
    )
    positions = [steps.find(name) for name in required_order]
    if any(position < 0 for position in positions) or positions != sorted(positions):
        raise AssertionError(
            "validated release coordinates must be loaded before minting the "
            "privileged finalizer token"
        )


def assert_gpu_local_runner_policy(text: str) -> None:
    jobs = workflow_jobs(text)
    for job_name in ("gpu-macos-arm64", "sterile-consumer"):
        body = jobs.get(job_name)
        if body is None:
            raise AssertionError(f"gpu-macos.yml: {job_name} is missing")
        guards = [line for line in body.splitlines() if line.startswith("    if:")]
        if guards != [GPU_GUARD]:
            raise AssertionError(
                f"gpu-macos.yml: {job_name} must have exactly the reviewed guard"
            )


class RunnerPolicyTests(unittest.TestCase):
    def test_only_trusted_default_branch_code_can_publish_sdk_releases(self) -> None:
        gpu = (WORKFLOWS / "gpu-macos.yml").read_text(encoding="utf-8")
        self.assertIn("permissions:\n  actions: read\n  attestations: read\n  contents: read", gpu)
        for forbidden in (
            "VELLUM_RULESET_AUDITOR_TOKEN",
            "gh release create",
            "gh release upload",
            "--method DELETE",
            "--method PATCH",
        ):
            self.assertNotIn(forbidden, gpu)

        finalizer = (WORKFLOWS / "sdk-release.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_run:", finalizer)
        self.assertNotIn("workflow_dispatch:", finalizer)
        self.assertEqual(
            finalizer.count("startsWith(github.event.workflow_run.head_branch, 'v')"),
            2,
        )
        self.assertIn("permissions:\n  actions: read\n  contents: read", finalizer)
        self.assertIn("ref: main", finalizer)
        self.assertIn("persist-credentials: false", finalizer)
        self.assertIn("permission-administration: write", finalizer)
        self.assertIn("permission-contents: write", finalizer)
        self.assertIn("needs: validate-sdk-artifacts", finalizer)
        self.assertIn("trusted-sdk-verification.json", finalizer)
        self.assertIn("trusted-sdk-installed-validation.json", finalizer)
        self.assertIn('git show "$SOURCE_COMMIT:scripts/install.sh"', finalizer)
        self.assertIn('git show "$SOURCE_COMMIT:scripts/install_core.py"', finalizer)
        self.assertIn('> "$RUNNER_TEMP/trusted/SHA256SUMS"', finalizer)
        self.assertIn("immutable-release-attestation.json", finalizer)
        self.assertIn('{"sha1": os.environ["TAG_OBJECT_SHA"]}', finalizer)
        self.assertIn("release-final-prepublish.json", finalizer)
        self.assertIn("release-assets-final-prepublish-verification.json", finalizer)
        self.assertIn("scripts/verify_sdk_artifact.py", finalizer)
        self.assertIn("scripts/build_sterile_acceptance_bundle.py", finalizer)
        self.assertGreaterEqual(finalizer.count("scripts/verify_release_tag.py"), 2)
        eligibility = finalizer.index(
            "- name: Require renderer release eligibility at the release source"
        )
        publish = finalizer.index("- name: Publish only through live no-bypass controls")
        self.assertLess(eligibility, publish)
        # The whole eligibility step, up to the publish step that follows it.
        eligibility_step = finalizer[eligibility:publish]
        self.assertIn("--require-release-eligible", eligibility_step)
        self.assertIn(
            'git show "$SOURCE_COMMIT:provenance/third-party-lock.json"',
            eligibility_step,
        )
        for proof in ("archive", "legal-manifest", "toolchain-receipt",
                      "provenance-bundle", "release-json"):
            self.assertIn(f'("{proof}"', eligibility_step)
        # The pinned owner identity is never overridden in a release.
        self.assertNotIn("--owner-principal", eligibility_step)
        self.assertNotIn("--owner-fingerprint", eligibility_step)
        self.assertIn('test "$(gh api "repos/$GITHUB_REPOSITORY/immutable-releases" --jq \'.enabled\')" = true', finalizer)
        self.assertIn('for row in page["jobs"]', finalizer)
        self.assertIn('"path": ".github/workflows/gpu-macos.yml"', finalizer)
        self.assertIn('"conclusion": "success"', finalizer)
        self.assertIn("collect_release_tag_rulesets.py", finalizer)
        self.assertIn("verify_release_tag_ruleset.py", finalizer)
        self.assertNotIn("--clobber", finalizer)
        self.assertGreaterEqual(finalizer.count("verify_release_guard"), 5)
        assert_privileged_release_coordinate_isolation(finalizer)
        self.assertIn("vellum.trusted-release-source.v1", finalizer)
        self.assertIn("trusted-release-source.json", finalizer)
        self.assertIn(
            'test "$tag_object_sha" = "$VALIDATED_TAG_OBJECT_SHA"', finalizer
        )
        for mutation in (
            "gh release create",
            "--method DELETE",
            "gh release upload",
            "--method PATCH",
        ):
            position = finalizer.find(mutation)
            self.assertGreater(position, 0)
            self.assertGreater(
                finalizer.rfind("verify_release_guard", 0, position),
                0,
                f"{mutation} must follow a live release guard",
            )

    def test_privileged_release_coordinate_isolation_detects_direct_event_input(
        self,
    ) -> None:
        finalizer = (WORKFLOWS / "sdk-release.yml").read_text(encoding="utf-8")
        mutation = (
            "      - name: Mint one-repository finalizer token\n"
            "        env:\n"
            "          RELEASE_TAG: ${{ github.event.workflow_run.head_branch }}\n"
        )
        mutated = finalizer.replace(
            "      - name: Mint one-repository finalizer token\n", mutation, 1
        )
        self.assertNotEqual(mutated, finalizer)
        with self.assertRaisesRegex(
            AssertionError, "consume untrusted workflow_run coordinate"
        ):
            assert_privileged_release_coordinate_isolation(mutated)

    def test_browser_driving_jobs_provision_the_pinned_browser(self) -> None:
        for filename, job_names in BROWSER_JOBS.items():
            jobs = workflow_jobs((WORKFLOWS / filename).read_text(encoding="utf-8"))
            for job_name in job_names:
                with self.subTest(workflow=filename, job=job_name):
                    body = jobs.get(job_name)
                    self.assertIsNotNone(body, f"{filename}: {job_name} is missing")
                    self.assertIn(
                        f"{CHROME_ACTION}@{NODE24_ACTION_PINS[CHROME_ACTION]}",
                        body,
                        f"{filename}: {job_name} must install the pinned browser",
                    )
                    self.assertIn(
                        "VELLUM_CHROME_PATH=",
                        body,
                        f"{filename}: {job_name} must export the pinned browser path",
                    )

    def test_locked_gpu_archive_download_is_resumable_and_hash_gated(self) -> None:
        gpu = (WORKFLOWS / "gpu-macos.yml").read_text(encoding="utf-8")
        self.assertIn("--retry 5 --retry-delay 2 --retry-all-errors", gpu)
        self.assertIn("--continue-at -", gpu)
        self.assertIn(
            "0ebfe03a209ceefe47edfeae70c3cc6c499583b74f35a26140ea55bad7f1e5a9",
            gpu,
        )

    def test_workflows_use_their_reviewed_runner_class(self) -> None:
        workflow_paths = sorted(
            [*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")]
        )
        self.assertEqual(
            [path.name for path in workflow_paths],
            sorted(EXPECTED_RUNNERS),
        )

        for path in workflow_paths:
            variable, fallback = EXPECTED_RUNNERS[path.name]
            runs_on_lines = [
                line.strip()
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip().startswith("runs-on:")
            ]
            self.assertTrue(runs_on_lines, path.name)
            if variable == "__mixed__":
                self.assertEqual(
                    set(runs_on_lines),
                    {
                        "runs-on: ${{ fromJSON(vars.VELLUM_MACOS_RUNS_ON_JSON || "
                        "'[\"macos-15\"]') }}",
                        "runs-on: ${{ fromJSON(vars.VELLUM_AUTHORITY_RUNS_ON_JSON || "
                        "'[\"ubuntu-latest\"]') }}",
                    },
                )
                continue
            for line in runs_on_lines:
                with self.subTest(workflow=path.name, selector=line):
                    if variable == "__hosted__":
                        self.assertEqual(line, f"runs-on: {fallback}")
                        continue
                    self.assertIn(f"vars.{variable}", line)
                    self.assertIn(fallback, line)
                    # Public repository: Actions are free on GitHub-hosted
                    # runners, so every fallback must be hosted. A self-hosted
                    # fallback would route a public repo onto the private fleet
                    # that another repository's required gate depends on.
                    self.assertNotIn("self-hosted", line)
                    self.assertTrue(
                        any(label in fallback for label in HOSTED_LABELS),
                        f"{path.name} fallback is not a hosted label: {fallback}",
                    )

        gpu = (WORKFLOWS / "gpu-macos.yml").read_text(encoding="utf-8")
        for job in ("gpu-macos-arm64", "sterile-consumer"):
            body = workflow_jobs(gpu).get(job)
            self.assertIsNotNone(body, f"gpu-macos.yml: {job} is missing")
        assert_gpu_local_runner_policy(gpu)

    def test_gpu_local_runner_policy_rejects_guard_mutations(self) -> None:
        gpu = (WORKFLOWS / "gpu-macos.yml").read_text(encoding="utf-8")
        assert_gpu_local_runner_policy(gpu)
        mutations = {
            "truthy": gpu.replace(GPU_GUARD, GPU_GUARD + " || true"),
            "missing event clause": gpu.replace(
                "github.event_name != 'pull_request' || ", "", 2
            ),
            "commented guard": gpu.replace("    if: github.event_name", "    # if: github.event_name", 2),
        }
        for name, mutated in mutations.items():
            with self.subTest(mutation=name):
                with self.assertRaises(AssertionError):
                    assert_gpu_local_runner_policy(mutated)

    def test_external_actions_are_pinned_to_reviewed_node24_releases(self) -> None:
        for path in sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")]):
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                # `statuses: write` contains "uses:" too; only a step key counts.
                if not re.match(r"^\s*-?\s*uses:", line) or "uses: ./" in line:
                    continue
                match = ACTION_USE.match(line)
                self.assertIsNotNone(
                    match,
                    f"{path.name}:{line_number}: external action must use a full commit SHA",
                )
                action, pin = match.groups()
                self.assertIn(
                    action,
                    NODE24_ACTION_PINS,
                    f"{path.name}:{line_number}: review the action runtime before allowing it",
                )
                self.assertEqual(
                    pin,
                    NODE24_ACTION_PINS[action],
                    f"{path.name}:{line_number}: action pin is not the reviewed Node.js 24 release",
                )

    def test_required_check_workflows_also_run_on_merge_group(self) -> None:
        # A merge queue only admits an entry whose required checks reported on
        # the queue's synthetic merge commit. A required-context workflow with
        # no merge_group trigger never reports there, and the queue ejects the
        # entry after its check timeout instead of merging it.
        required = {
            "gpu-macos.yml",
            "product-quality.yml",
            "provenance.yml",
            "readme-quick-start.yml",
        }
        for path in sorted(WORKFLOWS.glob("*.yml")):
            text = path.read_text(encoding="utf-8")
            _, marker, triggers = text.partition("\non:\n")
            self.assertTrue(marker or text.startswith("on:\n"), path.name)
            triggers = triggers.split("\npermissions:", 1)[0].split("\nconcurrency:", 1)[0]
            declares = re.search(r"^  merge_group:\s*$", triggers, re.M) is not None
            with self.subTest(workflow=path.name):
                if path.name in required:
                    self.assertTrue(declares, f"{path.name} lacks a merge_group trigger")
                    self.assertIsNotNone(
                        re.search(r"^  pull_request:\s*$", triggers, re.M), path.name
                    )
                else:
                    self.assertFalse(declares, f"{path.name} must not run on merge_group")

    def test_pull_request_and_queue_runs_pin_every_pulp_checkout(self) -> None:
        # A required check that reads Pulp at a live branch can change verdict
        # between a pull request's check and its merge-queue check with no
        # change to the Vellum tree, and two queue entries built from the same
        # Vellum state can disagree. Every Pulp checkout a pull_request or
        # merge_group run can reach must therefore resolve its ref from
        # evidence committed in this repository.
        seen = 0
        non_exempt = 0
        verified_count = 0
        seen_verified: set[tuple[str, str]] = set()
        for path in sorted(WORKFLOWS.glob("*.yml")):
            text = path.read_text(encoding="utf-8")
            if not queue_gated(text):
                continue
            for checkout in pulp_checkouts(text):
                seen += 1
                with self.subTest(workflow=path.name, checkout=checkout["path"]):
                    key = (path.name, checkout["path"])
                    if key in VERIFIED_POST_CHECKOUT_PINS:
                        if checkout["path"] == ".exact-boundary-pulp":
                            assert_exact_boundary_pulp_pin(text)
                        else:
                            assert_mirror_pulp_pin(text)
                        verified_count += 1
                        seen_verified.add(key)
                        continue
                    non_exempt += 1
                    expression = STEP_OUTPUT_REF.match(checkout["ref"])
                    self.assertIsNotNone(
                        expression,
                        f"{checkout['name']!r} reads {PULP_REPOSITORY} at "
                        f"{checkout['ref']!r}; pin it to a step output derived "
                        "from committed evidence",
                    )
                    assert expression is not None
                    body = step_body(text, expression.group(1))
                    self.assertIsNotNone(body, f"missing step id {expression.group(1)}")
                    assert body is not None
                    self.assertTrue(
                        any(source in body for source in COMMITTED_PIN_SOURCES),
                        f"step {expression.group(1)} does not derive its output "
                        "from committed evidence",
                    )
                    if "main" in re.sub(r"#.*", "", body):
                        # The live branch name may only be the fallback of an
                        # event guard that pins pull_request and merge_group.
                        self.assertIn(LIVE_BRANCH_GUARD, body)
                        guarded, _, fallback = body.partition(LIVE_BRANCH_GUARD)
                        self.assertNotIn('"main"', re.sub(r"#.*", "", guarded))
                        self.assertIn('ref = "main"', fallback)
                    # The pin must exist whenever the checkout runs.
                    condition = re.search(r"^ +if: (.+)$", body, re.M)
                    self.assertEqual(
                        condition.group(1) if condition else "", checkout["if"]
                    )
        # Control: the rule must have examined the checkouts it protects.
        self.assertGreaterEqual(seen, 3)
        self.assertEqual(non_exempt, seen - verified_count)
        self.assertEqual(seen_verified, VERIFIED_POST_CHECKOUT_PINS)

    def test_every_pulp_reach_is_a_reviewed_form(self) -> None:
        # A Pulp read that bypasses actions/checkout is invisible to the
        # checkout rule above. Every step naming Pulp, in any form, is
        # therefore classified, and the only raw fetch is the canonical one.
        reaching = 0
        for path in sorted(WORKFLOWS.glob("*.yml")):
            text = path.read_text(encoding="utf-8")
            if not queue_gated(text):
                continue
            problems, count = pulp_reach_problems(path.name, text)
            reaching += count
            with self.subTest(workflow=path.name):
                self.assertEqual(problems, [])
        # Local composite actions run inside whichever job uses them.
        for path in sorted((ROOT / ".github" / "actions").glob("**/action.y*ml")):
            problems, count = pulp_reach_problems(path.name, path.read_text(encoding="utf-8"))
            reaching += count
            with self.subTest(action=str(path.relative_to(ROOT))):
                self.assertEqual(problems, [])
        # Control: provenance.yml alone has five Pulp checkouts, the pinned
        # fetch and the API-only reader.
        self.assertGreaterEqual(reaching, 7)
        workflow = (WORKFLOWS / "provenance.yml").read_text(encoding="utf-8")
        self.assertIn(PINNED_PULP_FETCH + "\n", workflow)
        self.assertEqual(workflow.count(PINNED_PULP_GIT_DIR_USE), 1)

    def test_pulp_reach_bypasses_fail_closed(self) -> None:
        workflow = (WORKFLOWS / "provenance.yml").read_text(encoding="utf-8")
        self.assertEqual(pulp_reach_problems("provenance.yml", workflow)[0], [])
        fetch = PINNED_PULP_FETCH
        tail = '          test "$(git -C "$pulp_git" rev-parse HEAD)" = "$pin_commit"'
        verify = "      - name: Verify extraction and active-source quarantine\n"

        def in_fetch(extra: str) -> str:
            return workflow.replace(fetch, fetch.replace(tail, tail + "\n" + extra), 1)

        pin_after_checkout = "      - name: Pin mirror Pulp reads to committed Decision 0010 evidence\n"
        reader_anchor = '            live_pulp_main="$(gh api repos/Generous-Corp/pulp/git/ref/heads/main --jq .object.sha)"\n'
        self.assertEqual(workflow.count(reader_anchor), 1)

        mirror_with = "        with:\n          repository: Generous-Corp/pulp\n          ref: main\n          path: .mirror-pulp\n"
        self.assertEqual(workflow.count(mirror_with), 2)
        reader_env = "          PULP_ACCEPTANCE_SHA: ${{ steps.expansion-evidence.outputs.pulp-sha }}\n"
        self.assertEqual(workflow.count(reader_env), 1)

        def in_reader(line: str) -> str:
            return workflow.replace(reader_anchor, reader_anchor + "            " + line + "\n", 1)

        def new_step(body: str) -> str:
            return workflow.replace(verify, f"      - name: Extra step\n        run: |\n{body}\n\n" + verify, 1)

        mutations = {
            "extra fetch of origin HEAD": in_fetch('          git -C "$pulp_git" fetch -q origin HEAD'),
            "second remote and branch": in_fetch(
                '          git -C "$pulp_git" remote add dev https://github.com/Generous-Corp/pulp.git\n'
                '          git -C "$pulp_git" fetch -q dev develop'),
            "worktree add": in_fetch('          git -C "$pulp_git" worktree add "$RUNNER_TEMP/pulp" "$pin_commit"'),
            "archive and run": in_fetch(
                '          git -C "$pulp_git" archive "$pin_commit" | tar x && bash setup.sh'),
            "live branch": workflow.replace(fetch, fetch.replace('origin "$pin_commit"', "origin main", 1), 1),
            "unpinned source": workflow.replace(fetch, fetch.replace(MIRROR_EDGE_PIN, "provenance/other.json", 1), 1),
            "full blobs": workflow.replace(fetch, fetch.replace(" --filter=blob:none", "", 1), 1),
            "working tree": workflow.replace(fetch, fetch.replace("git init -q --bare", "git init -q", 1), 1),
            "ssh url": new_step("          git clone git@github.com:Generous-Corp/pulp.git pulp"),
            "lowercase slug": new_step("          git clone https://github.com/generous-corp/pulp pulp"),
            "server_url": new_step("          git clone ${{ github.server_url }}/Generous-Corp/pulp pulp"),
            "repository_owner": new_step("          git clone https://github.com/${{ github.repository_owner }}/pulp.git"),
            "dot-git suffix": new_step("          git clone https://github.com/Generous-Corp/pulp.git"),
            "reuse the pinned repository": new_step(
                f'          git -C {PINNED_PULP_GIT_DIR} fetch -q origin develop'),
            "lowercase checkout": workflow.replace(
                "          repository: Generous-Corp/pulp\n", "          repository: generous-corp/pulp\n", 1),
            "api-only step clones": workflow.replace(
                "      - name: Test authority protocol and negative controls\n",
                "      - name: Test authority protocol and negative controls\n"
                "        # gh repo clone Generous-Corp/pulp\n", 1).replace(
                "          PULP_ACCEPTANCE_SHA: ${{ steps.expansion-evidence.outputs.pulp-sha }}\n",
                "          PULP_ACCEPTANCE_SHA: ${{ steps.expansion-evidence.outputs.pulp-sha }}\n"
                "          CLONE: gh repo clone Generous-Corp/pulp\n", 1),
            "second pinned fetch": workflow.replace(fetch + "\n", fetch + "\n\n" + fetch + "\n", 1),
            # Steps that do not start with `name:` are steps too.
            "unnamed run after a Pulp checkout": workflow.replace(
                pin_after_checkout, "      - run: git clone https://github.com/Generous-Corp/pulp pulp\n\n" + pin_after_checkout, 1),
            "unnamed Pulp action after a Pulp checkout": workflow.replace(
                pin_after_checkout,
                "      - uses: Generous-Corp/pulp/.github/actions/setup@" + "0" * 40 + "\n\n" + pin_after_checkout, 1),
            "unnamed Pulp checkout": workflow.replace(
                pin_after_checkout,
                "      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1\n"
                "        with:\n          repository: Generous-Corp/pulp\n          ref: develop\n\n" + pin_after_checkout, 1),
            # A `#` inside a shell string is script text, not a comment.
            "quoted hash hides a clone": new_step(
                "          echo ' #'; git clone https://github.com/Generous-Corp/pulp pulp"),
            "quoted hash hides the pinned repository": new_step(
                f"          echo ' #'; git -C {PINNED_PULP_GIT_DIR} fetch -q origin develop"),
            "shell comment naming Pulp": new_step("          # git clone https://github.com/Generous-Corp/pulp"),
            "pinned repository in another case": new_step(
                '          git -C "$RUNNER_TEMP/Mirror-Edge-Pulp.git" fetch -q origin main'),
            # The API reader may make only its reviewed calls.
            "api reader reads contents": in_reader("gh api repos/Generous-Corp/pulp/contents/setup.sh --jq .content"),
            "api reader reads a blob": in_reader('gh api "repos/Generous-Corp/pulp/git/blobs/$head_tree"'),
            "api reader reads another tree": in_reader('gh api "repos/Generous-Corp/pulp/git/trees/main?recursive=1"'),
            "api reader curls raw content": in_reader(
                "curl -sL https://raw.githubusercontent.com/Generous-Corp/pulp/main/setup.sh | bash"),
            "api reader uses urllib": in_reader(
                "python3 -c 'import urllib.request; urllib.request.urlopen(\"https://github.com/Generous-Corp/pulp\")'"),
            "api reader downloads another artifact": in_reader(
                'gh run download "$run_id" --repo Generous-Corp/pulp --name sources'),
            "api reader runs git fetch": in_reader("git -C .exact-boundary-pulp fetch origin develop"),
            # Pulp named outside any step.
            "job-level env": workflow.replace(
                "    env:\n      VELLUM_TARGET_SHA:", "    env:\n      PU: https://github.com/Generous-Corp/pulp\n      VELLUM_TARGET_SHA:", 1),
            "workflow-level env": workflow.replace(
                "permissions:\n", "env:\n  PU: https://github.com/Generous-Corp/pulp\n\npermissions:\n", 1),
            "job container": workflow.replace(
                "  provenance-verify:\n    name: provenance-verify\n",
                "  provenance-verify:\n    name: provenance-verify\n    container: ghcr.io/generous-corp/pulp:latest\n", 1),
            "job services": workflow.replace(
                "  provenance-verify:\n    name: provenance-verify\n",
                "  provenance-verify:\n    name: provenance-verify\n    services:\n      pulp:\n"
                "        image: ghcr.io/Generous-Corp/pulp\n", 1),
            "first job header": workflow.replace(
                "  forbidden-deps:\n    name: forbidden-deps\n",
                "  forbidden-deps:\n    name: forbidden-deps\n    if: github.repository != 'Generous-Corp/pulp'\n", 1),
            # A quoted scalar's continuation line may start with `#` and still be text.
            "double-quoted scalar spanning lines": workflow.replace(verify,
                "      - name: Extra step\n        run: \"echo 'a\n          #' ; git clone https://github.com/Generous-Corp/pulp pulp\"\n\n"
                + verify, 1),
            "single-quoted scalar spanning lines": workflow.replace(verify,
                "      - name: Extra step\n        run: 'echo a\n          #'' ; git clone https://github.com/Generous-Corp/pulp pulp'\n\n"
                + verify, 1),
            # The API reader must not hand Pulp to another step.
            "reader anchor aliased into a checkout": workflow.replace(
                reader_env, reader_env + "          PULP_URL: &pu Generous-Corp/pulp\n", 1).replace(verify,
                "      - name: Extra checkout\n        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1\n"
                "        with:\n          repository: *pu\n          ref: main\n\n" + verify, 1),
            "reader anchor aliased into env": workflow.replace(
                reader_env, reader_env + "          PULP_URL: &pu https://github.com/Generous-Corp/pulp\n", 1).replace(verify,
                "      - name: Extra step\n        env:\n          U: *pu\n        run: git clone \"$U\"\n\n" + verify, 1),
            "reader exports Pulp through GITHUB_ENV": in_reader(
                'echo "PU=https://github.com/Generous-Corp/pulp" >> "$GITHUB_ENV"'),
            "reader exports through GITHUB_OUTPUT without naming Pulp": in_reader(
                'echo "repo=$repository" >> "$GITHUB_OUTPUT"'),
            "reader runs git through a variable": in_reader("g=git; $g clone https://github.com/Generous-Corp/pulp"),
            "reader runs gh through a variable": in_reader("G=gh; $G repo clone Generous-Corp/pulp"),
            "reader names Pulp on a new line": in_reader('echo "Generous-Corp/pulp"'),
            # Routes only the reader's byte-for-byte pin catches.
            "reader evals a split command name": in_reader('eval "g""h repo clone $repository"'),
            "reader continues a word across lines": in_reader(
                'gi\\\n            t -C .exact-boundary-pulp fetch origin develop'),
            "reader glues a command in os.system": in_reader(
                "python3 -c 'import os; os.system(\"g\" + \"it -C .exact-boundary-pulp fetch origin develop\")'"),
            "reader writes the repository to a file": in_reader('echo "$repository" > "$RUNNER_TEMP/repository.txt"'),
            # Anchors: an anchored mapping that names no Pulp slug on its own
            # line, aliased into a checkout that names none either.
            "anchored checkout inputs aliased into another checkout": workflow.replace(
                mirror_with, mirror_with.replace("with:", "with: &w", 1), 1).replace(verify,
                "      - name: Extra checkout\n        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1\n"
                "        with: *w\n\n" + verify, 1),
            "anchor name with punctuation": workflow.replace(
                mirror_with, mirror_with.replace("with:", "with: &.w", 1), 1).replace(verify,
                "      - name: Extra checkout\n        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1\n"
                "        with: *.w\n\n" + verify, 1),
            "reader runs git through a variable without naming Pulp": in_reader('g=git; $g -C .exact-boundary-pulp fetch origin develop'),
            # Checkout shapes. The blank-line case is caught only by the
            # checkout count; the name-not-first case is caught by the
            # name-first check and, as its backup, by the count.
            "blank line hides an unpinned ref from the pin rule": workflow.replace(verify,
                "      - name: Extra checkout\n        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1\n"
                "        with:\n          path: .extra\n\n          repository: Generous-Corp/pulp\n          ref: main\n\n" + verify, 1),
            "checkout that does not lead with name": workflow.replace(verify,
                "      - if: always()\n        name: Extra checkout\n"
                "        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1\n"
                "        with:\n          repository: Generous-Corp/pulp\n          ref: main\n\n" + verify, 1),
            "checkout repository from an expression": workflow.replace(verify,
                "      - name: Extra checkout\n        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1\n"
                "        with:\n          repository: ${{ format('{0}/pulp', github.repository_owner) }}\n          ref: main\n\n"
                + verify, 1),
            "reusable Pulp workflow job": workflow.rstrip("\n")
                + "\n\n  pulp-reuse:\n    uses: Generous-Corp/pulp/.github/workflows/x.yml@" + "0" * 40 + "\n",
        }
        for name, mutated in mutations.items():
            with self.subTest(mutation=name):
                self.assertNotEqual(mutated, workflow)
                self.assertTrue(pulp_reach_problems("provenance.yml", mutated)[0])

    def test_queue_gated_scope_reads_every_on_form(self) -> None:
        self.assertTrue(queue_gated("on: [pull_request, merge_group]\n"))
        self.assertTrue(queue_gated("on: pull_request\n"))
        self.assertTrue(queue_gated("'on': {merge_group: {branches: [main]}}\n"))
        self.assertTrue(queue_gated("on:\n  workflow_run:\n    workflows: [x]\n"))
        self.assertTrue(queue_gated("on:\n  # queue\n  - push\n  - merge_group\n"))
        self.assertTrue(queue_gated("on:\n  pull_request_target:\n"))
        self.assertFalse(queue_gated("on:\n  push:\n    branches: [main]\n  schedule:\n    - cron: '0 0 * * *'\n"))
        self.assertFalse(queue_gated("on: workflow_dispatch\n"))
        # A workflow rewritten in flow style stays in scope, and its bypasses still fail.
        workflow = (WORKFLOWS / "provenance.yml").read_text(encoding="utf-8")
        block = workflow[workflow.index("on:\n"):workflow.index("permissions:\n")]
        flow = workflow.replace(block, "on: [workflow_dispatch, pull_request, merge_group, push]\n\n", 1)
        self.assertTrue(queue_gated(flow))
        self.assertEqual(pulp_reach_problems("provenance.yml", flow)[0], [])

    def test_composite_action_steps_are_read(self) -> None:
        action = (
            "name: x\nruns:\n  using: composite\n  steps:\n"
            "    - run: git clone https://github.com/Generous-Corp/pulp pulp\n      shell: bash\n"
        )
        self.assertEqual(pulp_reach_problems("action.yml", action)[0],
                         ["unnamed step at line 5 reaches Pulp outside actions/checkout or the pinned fetch"])
        self.assertTrue(queue_gated("on:\n  workflow_call:\n"))
        self.assertTrue(queue_gated("on: pull_request_review\n"))

    def test_quoted_scalar_detection(self) -> None:
        for line in ('        run: "echo a', "        run: 'echo a", "      - 'a", '        x: ["a', "        run: 'it''s"):
            with self.subTest(line=line):
                self.assertTrue(open_quoted_scalar(line))
        for line in ('        run: "echo a"', "        run: 'it''s'", '        run: echo "a"', "        run: echo don't",
                     '        x: ["a", "b"]', '        run: "a \\" b"'):
            with self.subTest(line=line):
                self.assertFalse(open_quoted_scalar(line))

    def test_every_step_item_is_a_step(self) -> None:
        text = (
            "jobs:\n  a:\n    steps:\n"
            "      - uses: actions/checkout@" + "0" * 40 + "\n"
            "      - run: |\n          echo ' #'\n          # script text\n\n"
            "      # a YAML comment\n"
            "      - name: Named\n        run: echo hi\n"
            "  b:\n    uses: org/repo/.github/workflows/x.yml@main\n"
        )
        steps, outside = workflow_items(text)
        self.assertEqual([(step["first_key"], step["name"]) for step in steps],
                         [("uses", None), ("run", None), ("name", "Named")])
        self.assertIn("          # script text", steps[1]["code"])
        self.assertNotIn("      # a YAML comment", steps[1]["code"])
        self.assertIn((13, "    uses: org/repo/.github/workflows/x.yml@main"), outside)

    def test_pulp_reference_recognises_every_form(self) -> None:
        for text in ("https://github.com/Generous-Corp/pulp", "git@github.com:Generous-Corp/pulp.git",
                     "${{ github.server_url }}/Generous-Corp/pulp", "generous-corp/PULP",
                     "${{ github.repository_owner }}/pulp", "repos/Generous-Corp/pulp/check-runs"):
            with self.subTest(text=text):
                self.assertIsNotNone(PULP_REFERENCE.search(text))
        for text in ("Generous-Corp/pulp-planning", "Generous-Corp/vellum", "pulp_sha"):
            with self.subTest(text=text):
                self.assertIsNone(PULP_REFERENCE.search(text))

    def test_exact_boundary_pin_mutations_fail_closed(self) -> None:
        workflow = (WORKFLOWS / "provenance.yml").read_text(encoding="utf-8")
        pin = named_step_body(
            workflow, "Pin pull-request and queue Pulp reads to committed evidence"
        )
        self.assertIsNotNone(pin)
        assert pin is not None
        mutations = {
            "delete pin step": workflow.replace(pin, "", 1),
            "drop merge_group guard": workflow.replace(
                pin,
                pin.replace(" || github.event_name == 'merge_group'", "", 1),
                1,
            ),
            "drop detach": workflow.replace(
                '          git -C .exact-boundary-pulp checkout --detach "$pulp_ref"\n',
                "",
                1,
            ),
            "drop equality check": workflow.replace(
                '          test "$(git -C .exact-boundary-pulp rev-parse HEAD)" = "$pulp_ref"\n',
                "",
                1,
            ),
        }
        for name, mutated in mutations.items():
            with self.subTest(mutation=name):
                with self.assertRaises(AssertionError):
                    assert_exact_boundary_pulp_pin(mutated)


if __name__ == "__main__":
    unittest.main(verbosity=2)
