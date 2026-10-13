#!/usr/bin/env python3
from __future__ import annotations

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


def pulp_checkouts(text: str) -> list[dict[str, str]]:
    """Return every actions/checkout step that reads Generous-Corp/pulp."""
    found: list[dict[str, str]] = []
    for match in CHECKOUT_STEP.finditer(text):
        block = match.group(0)
        fields = dict(
            re.findall(r"^ +([a-z-]+): (.+)$", match.group("with"), re.M)
        )
        if fields.get("repository") != PULP_REPOSITORY:
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
        self.assertLess(
            eligibility,
            finalizer.index("- name: Publish only through live no-bypass controls"),
        )
        self.assertIn(
            "--require-release-eligible", finalizer[eligibility:eligibility + 1200]
        )
        self.assertIn(
            'git show "$SOURCE_COMMIT:provenance/third-party-lock.json"',
            finalizer[eligibility:eligibility + 1200],
        )
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
            if not re.search(r"^  (pull_request|merge_group):", text, re.M):
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
