#!/usr/bin/env python3
"""check_release_policy.py: one key, one signing workflow, owner-owned policy files."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_release_policy as policy  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
# The general reason a credential-holding job's step is refused.
UNSUPPORTED = "unsupported run body"


def allowlist_for(workflows: dict[str, str]) -> dict:
    """RUN_ALLOWLIST as it would read if every credential-holding job of these
    workflows were reviewed exactly as written. A fixture the parser refuses
    gets no entries."""
    allowlist = {}
    for name, text in workflows.items():
        try:
            entries = policy.allowlist_entries(name, text)
        except policy.PolicyError:
            continue
        allowlist.update({(name, job): [steps] for job, steps in entries.items()})
    return allowlist


def isolation(workflows: dict[str, str], root: Path, allow: bool = False) -> list[str]:
    """isolation_problems, with the fixtures' own steps allowlisted when `allow`."""
    with mock.patch.object(policy, "RUN_ALLOWLIST", allowlist_for(workflows) if allow else {}):
        return policy.isolation_problems(workflows, root)


class Policy(unittest.TestCase):
    CALL = ("          python3 -I scripts/verify_release_tag.py \\\n"
            "            --allowed-signers provenance/release-signers.allowed_signers \\\n"
            "            --expected-principal \"$PRINCIPAL\" --expected-fingerprint \"$FINGERPRINT\"\n")
    # The release job and the dry-run job each verify the tag they signed.
    SIGNING = ("jobs:\n  sign:\n    environment: release\n    steps:\n      - name: v\n        run: |\n" + CALL
               + "      - name: d\n        run: |\n" + CALL)

    BASE_JUDGE = ("jobs:\n  release-policy:\n    steps:\n      - name: base\n        run: |\n"
                  f"          python3 -I {policy.BASE_JUDGE}\n")

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="release-policy-"))
        (self.tmp / ".github/workflows").mkdir(parents=True)
        (self.tmp / "provenance").mkdir()
        self.release = self.make_signer("release@ci", policy.ALLOWED_SIGNERS)
        self.authority = self.make_signer("owner@example", policy.SIGNER_FILES["authority-signers.allowed_signers"])
        self.fingerprint = self.release[1]
        shutil.copy(ROOT / policy.CODEOWNERS, self.tmp / policy.CODEOWNERS)
        self.workflow("release-tag.yml", self.SIGNING)
        self.workflow("release-policy.yml", self.BASE_JUDGE)
        self.workflow("verify.yml", self.call("release-signers.allowed_signers", *self.release))

    def make_signer(self, principal: str, relative: Path) -> tuple[str, str]:
        key = self.tmp / relative.name.split(".")[0]
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
        pub = key.with_suffix(".pub").read_text().split()
        (self.tmp / relative).write_text(f"{principal} {pub[0]} {pub[1]}\n")
        fingerprint = subprocess.run(["ssh-keygen", "-lf", f"{key}.pub"], capture_output=True, text=True,
                                     check=True).stdout.split()[1]
        return principal, fingerprint

    @staticmethod
    def call(signers: str, principal: str, fingerprint: str, separator: str = " ") -> str:
        return ("run: |\n  python3 scripts/verify_release_tag.py \\\n"
                f"    --allowed-signers{separator}\"$GITHUB_WORKSPACE/provenance/{signers}\" \\\n"
                f"    --expected-principal{separator}{principal} \\\n"
                f"    --expected-fingerprint \\\n      {fingerprint} \\\n    --output x\n")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp)

    def workflow(self, name: str, text: str) -> None:
        if text.startswith("run:"):
            text = "jobs:\n  verify:\n    steps:\n      - " + text.replace("\n", "\n        ").rstrip() + "\n"
        (self.tmp / ".github/workflows" / name).write_text(text)

    def test_positive_control(self) -> None:
        self.assertEqual(policy.problems(self.tmp), [])

    def test_another_workflow_naming_the_environment_fails(self) -> None:
        self.workflow("exfiltrate.yml", "jobs:\n  x:\n    environment: release\n")
        self.assertTrue(any("exfiltrate.yml" in p for p in policy.problems(self.tmp)))
        self.workflow("exfiltrate.yml", "jobs:\n  x:\n    environment: { name: release }\n")
        self.assertTrue(any("exfiltrate.yml" in p for p in policy.problems(self.tmp)))

    def test_multiline_environment_mapping_fails(self) -> None:
        self.workflow("exfiltrate.yml", "jobs:\n  x:\n    environment:\n      name: release\n")
        self.assertTrue(any("exfiltrate.yml" in p for p in policy.problems(self.tmp)))
        self.workflow("exfiltrate.yml", "jobs:\n  x:\n    environment:\n      url: https://example.test\n      name: release\n")
        self.assertTrue(any("exfiltrate.yml" in p for p in policy.problems(self.tmp)))

    def test_environment_key_whitespace_and_quotes_are_scanned(self) -> None:
        for key in ("environment : release", '"environment": release'):
            with self.subTest(key=key):
                self.workflow("exfiltrate.yml", f"jobs:\n  x:\n    {key}\n")
                self.assertTrue(any("exfiltrate.yml" in p for p in policy.problems(self.tmp)),
                                policy.problems(self.tmp))

    def test_environment_key_spellings_it_cannot_decode_fail_closed(self) -> None:
        for key in ('"\\x65nvironment": release', "? environment\n    : release", "!!str environment: release",
                    "Environment: release", "- environment: release", "x: {environment: release}",
                    "x: {'environment' : release}"):
            with self.subTest(key=key):
                self.workflow("exfiltrate.yml", f"jobs:\n  x:\n    {key}\n")
                self.assertTrue(any("exfiltrate.yml" in p for p in policy.problems(self.tmp)),
                                policy.problems(self.tmp))

    def test_a_yaml_extension_workflow_is_scanned(self) -> None:
        # GitHub Actions runs .yaml as well as .yml workflows.
        for text in ("jobs:\n  x:\n    environment: release\n",
                     "jobs:\n  x:\n    environment:\n      url: https://x\n      name: release\n"):
            with self.subTest(text=text):
                self.workflow("exfiltrate.yaml", text)
                self.assertTrue(any("exfiltrate.yaml" in p for p in policy.problems(self.tmp)))
        (self.tmp / ".github/workflows/exfiltrate.yaml").unlink()
        self.assertEqual(policy.problems(self.tmp), [])

    def test_an_environment_it_cannot_read_fails_closed(self) -> None:
        for text in ("jobs:\n  x:\n    environment: ${{ inputs.env }}\n",
                     "jobs:\n  x:\n    environment: *rel\n",
                     "jobs:\n  x:\n    environment: !!str release\n",
                     "jobs:\n  x:\n    environment: { name: ${{ inputs.env }} }\n",
                     "jobs:\n  x:\n    environment:\n      name: ${{ inputs.env }}\n",
                     "jobs:\n  x:\n    environment:\n      url: https://x\n"):
            with self.subTest(text=text):
                self.workflow("sneaky.yml", text)
                self.assertTrue(any("sneaky.yml" in p for p in policy.problems(self.tmp)), policy.problems(self.tmp))

    def test_yaml_anchors_aliases_and_merges_fail_closed(self) -> None:
        for text in ("defaults: &job\n  environment: release\njobs:\n  x: *job\n",
                     "jobs:\n  x:\n    <<: *job\n",
                     "jobs:\n  x: &copy\n    runs-on: ubuntu-latest\n"):
            with self.subTest(text=text):
                self.workflow("anchors.yml", text)
                self.assertTrue(any("anchor" in p for p in policy.problems(self.tmp)), policy.problems(self.tmp))

    def test_shell_text_in_run_blocks_is_not_yaml(self) -> None:
        self.workflow("shell.yml", "jobs:\n  x:\n    steps:\n      - name: s\n        run: |\n"
                                    "          rm -f *.json && cp -r src/* out/\n          echo &bg\n"
                                    "          python3 -c 'print(*args)'\n")
        self.assertEqual(policy.problems(self.tmp), [])

    def test_a_stale_fingerprint_pin_fails(self) -> None:
        self.workflow("verify.yml", self.call("release-signers.allowed_signers", "release@ci", "SHA256:old-key"))
        self.assertTrue(any("SHA256:old-key" in p for p in policy.problems(self.tmp)))

    def test_a_stale_principal_pin_fails(self) -> None:
        self.workflow("verify.yml", self.call("release-signers.allowed_signers", "someone@else", self.fingerprint))
        self.assertTrue(any("someone@else" in p for p in policy.problems(self.tmp)))

    def test_each_signer_file_takes_its_own_pins(self) -> None:
        self.workflow("authority.yml", self.call("authority-signers.allowed_signers", *self.authority))
        self.assertEqual(policy.problems(self.tmp), [])
        self.workflow("authority.yml", self.call("authority-signers.allowed_signers", *self.release))
        self.assertTrue(any("authority.yml: pins release@ci" in p for p in policy.problems(self.tmp)))

    def test_pins_cannot_hide_behind_equals_variables_or_other_files(self) -> None:
        cases = {
            "equals": self.call("release-signers.allowed_signers", "someone@else", self.fingerprint, "="),
            "variable": self.call("release-signers.allowed_signers", "$PRINCIPAL", self.fingerprint),
            "other file": self.call("my-signers.allowed_signers", *self.release),
            "loose flag": "run: verify --expected-principal release@ci\n",
            "missing flag": "run: python3 scripts/verify_release_tag.py --allowed-signers x/release-signers.allowed_signers\n",
        }
        for label, text in cases.items():
            with self.subTest(label):
                self.workflow("verify.yml", text)
                self.assertTrue(any(p.startswith("verify.yml") for p in policy.problems(self.tmp)),
                                policy.problems(self.tmp))

    def test_a_call_cannot_override_or_skip_its_pins(self) -> None:
        call = self.call("release-signers.allowed_signers", *self.release).rstrip("\n")
        cases = {
            "abbreviated duplicate": call.replace(" --output x", " --expected-princ someone@else --output x"),
            "repeated flag": call.replace(" --output x", " --expected-principal release@ci --output x"),
            "extra argument": call.replace(" --output x", " --output x $EXTRA_ARGS"),
            "ignored failure": call + " || true",
            "commented out": call.replace("python3 scripts", "true # python3 scripts"),
            "echoed": call.replace("python3 scripts", "echo python3 scripts"),
        }
        for label, text in cases.items():
            with self.subTest(label):
                self.workflow("verify.yml", text + "\n")
                self.assertTrue(any(p.startswith("verify.yml") for p in policy.problems(self.tmp)),
                                policy.problems(self.tmp))

    def test_the_signing_workflow_verifies_with_its_signer_step_pins(self) -> None:
        self.assertEqual(policy.problems(self.tmp), [])
        self.workflow("release-tag.yml", self.SIGNING.replace("release-signers", "authority-signers"))
        self.assertTrue(any("release-tag.yml: must verify" in p for p in policy.problems(self.tmp)))

    def test_a_tag_verifying_workflow_keeps_its_calls(self) -> None:
        self.workflow("gpu-macos.yml", self.call("release-signers.allowed_signers", *self.release))
        self.assertTrue(any("gpu-macos.yml: has 1" in p for p in policy.problems(self.tmp)))

    def test_a_reusable_workflow_from_another_repository_fails(self) -> None:
        self.workflow("local.yml", "jobs:\n  x:\n    uses: ./.github/workflows/verify.yml\n")
        self.assertEqual(policy.problems(self.tmp), [])
        for text in ("jobs:\n  x:\n    uses: evil/repo/.github/workflows/x.yml@0123\n",
                     "jobs:\n  x:\n    uses: 'evil/repo/.github/workflows/x.yml@main'\n",
                     "jobs:\n  x:\n    uses: >-\n      evil/repo/.github/workflows/x.yml@main\n",
                     "jobs:\n  x:\n    uses:\n      evil/repo/.github/workflows/x.yml@main\n",
                     "jobs:\n  b: {uses: evil/repo/.github/workflows/y.yml@main}\n",
                     'jobs:\n  x:\n    uses: "evil/repo/\\x2egithub/workflows/x.yml@main"\n',
                     "jobs:\n  x:\n    USES: evil/repo/.github/workflows/x.yml@main\n"):
            with self.subTest(text):
                self.workflow("remote.yml", text)
                self.assertTrue(any(p.startswith("remote.yml") for p in policy.problems(self.tmp)),
                                policy.problems(self.tmp))

    def test_a_second_key_fails(self) -> None:
        signers = self.tmp / policy.ALLOWED_SIGNERS
        signers.write_text(signers.read_text() * 2)
        with self.assertRaisesRegex(policy.PolicyError, "exactly one release key"):
            policy.problems(self.tmp)

    def test_a_policy_file_missing_from_codeowners_fails(self) -> None:
        owners = self.tmp / policy.CODEOWNERS
        owners.write_text("\n".join(l for l in owners.read_text().splitlines() if "verify_release_tag" not in l))
        self.assertTrue(any("verify_release_tag.py" in p for p in policy.problems(self.tmp)))

    def test_release_decision_inputs_missing_from_codeowners_fail(self) -> None:
        # A dropped retired tag, a weakened immutable-release verifier, a
        # version-at-land workflow that does more with its App token, or a
        # parity completion record or proof runner that decides a release must
        # not land without the owner's review.
        for path in ("/scripts/verify_immutable_release_preflight.py",
                     "/scripts/verify_release_attestation.py",
                     "/provenance/retired-release-tags.json",
                     "/.github/workflows/version-at-land.yml",
                     "/scripts/version_at_land.py",
                     "/provenance/authority-signers.allowed_signers",
                     "/provenance/mirror/link-policy.json",
                     "/tools/provenance/verify_renderer_dependency.py",
                     "/provenance/active-source-boundary.json",
                     "/scripts/install_core.py",
                     "/tools/provenance/live_delivery.py",
                     "/tools/provenance/observatory.py",
                     "/tools/provenance/observatory_common.py",
                     "/tools/provenance/observatory_events.py",
                     "/tools/provenance/observatory_git.py",
                     "/tools/provenance/observatory_native.py",
                     "/tools/provenance/observatory_report.py",
                     "/tools/provenance/receiver.py",
                     "/tools/provenance/verify_pulp_observation_scope.py",
                     "/provenance/authority/expansions/full-design-import-render-v1/parity-completion-1.json",
                     "/scripts/run_parity_proofs.py"):
            with self.subTest(path=path):
                shutil.copy(ROOT / policy.CODEOWNERS, self.tmp / policy.CODEOWNERS)
                owners = self.tmp / policy.CODEOWNERS
                owners.write_text("\n".join(l for l in owners.read_text().splitlines()
                                            if not l.startswith(path + " ")))
                self.assertIn(f"{policy.CODEOWNERS} does not give {path} to {policy.OWNER}",
                              policy.problems(self.tmp))

    def test_release_decision_inputs_missing_from_policy_files_fail(self) -> None:
        for path in ("/scripts/verify_immutable_release_preflight.py",
                     "/scripts/verify_release_attestation.py",
                     "/provenance/retired-release-tags.json",
                     "/.github/workflows/version-at-land.yml",
                     "/scripts/version_at_land.py",
                     "/provenance/authority-signers.allowed_signers",
                     "/provenance/mirror/link-policy.json",
                     "/tools/provenance/verify_renderer_dependency.py",
                     "/provenance/active-source-boundary.json",
                     "/scripts/install_core.py",
                     "/tools/provenance/live_delivery.py",
                     "/tools/provenance/observatory.py",
                     "/tools/provenance/observatory_common.py",
                     "/tools/provenance/observatory_events.py",
                     "/tools/provenance/observatory_git.py",
                     "/tools/provenance/observatory_native.py",
                     "/tools/provenance/observatory_report.py",
                     "/tools/provenance/receiver.py",
                     "/tools/provenance/verify_pulp_observation_scope.py"):
            with self.subTest(path=path):
                self.assertIn(path, policy.POLICY_FILES)
                kept = tuple(p for p in policy.POLICY_FILES if p != path)
                original, policy.POLICY_FILES = policy.POLICY_FILES, kept
                try:
                    self.assertIn(f"{policy.CODEOWNERS} owns {path}, which POLICY_FILES does not name",
                                  policy.problems(self.tmp))
                finally:
                    policy.POLICY_FILES = original

    def test_every_observatory_module_is_a_policy_file(self) -> None:
        # observatory-verify runs the pull request's own copy of these modules, so
        # a module that joins the package unowned would let one review edit the verifier.
        modules = sorted(f"/tools/provenance/{p.name}" for p in (ROOT / "tools/provenance").glob("observatory*.py")
                         if not p.name.startswith("test_"))
        self.assertTrue(modules)
        for path in modules:
            with self.subTest(path=path):
                self.assertIn(path, policy.POLICY_FILES)

    def test_a_policy_script_importing_an_unowned_module_fails(self) -> None:
        (self.tmp / "scripts").mkdir()
        (self.tmp / "scripts/release_request.py").write_text(
            "def f():\n    import verify_immutable_release_preflight as vip\n")
        (self.tmp / "scripts/verify_immutable_release_preflight.py").write_text("")
        self.assertEqual(policy.problems(self.tmp), [])
        kept = tuple(p for p in policy.POLICY_FILES if p != "/scripts/verify_immutable_release_preflight.py")
        original, policy.POLICY_FILES = policy.POLICY_FILES, kept
        try:
            owners = self.tmp / policy.CODEOWNERS
            owners.write_text("\n".join(l for l in owners.read_text().splitlines()
                                        if "verify_immutable_release_preflight" not in l))
            self.assertIn("scripts/release_request.py imports scripts/verify_immutable_release_preflight.py, "
                          "which is not a policy file", policy.problems(self.tmp))
        finally:
            policy.POLICY_FILES = original

    def test_a_policy_script_loading_an_unowned_module_by_path_fails(self) -> None:
        # verify_extraction.py runs content_scan_policy.py through importlib, not
        # an import statement; a module it loads by path decides its verdict too.
        judge = self.tmp / "tools/provenance/verify_extraction.py"
        judge.parent.mkdir(parents=True)
        (self.tmp / "tools/mirror").mkdir()
        (self.tmp / "tools/mirror/rename_mirror_namespace.py").write_text("")
        (self.tmp / "tools/provenance/content_scan_policy.py").write_text("")
        (self.tmp / "tools/provenance/helper.py").write_text("")
        (self.tmp / "scripts").mkdir()
        (self.tmp / "scripts/install_core.py").write_text("")
        judge.write_text(
            "import importlib.util\nfrom pathlib import Path\n"
            "SCANNED = (\"scripts/install_core.py\",)  # a path it reads as data, not code\n"
            "POLICY = Path(__file__).with_name(\"content_scan_policy.py\")\n"
            "RENAME = Path(__file__).resolve().parents[1] / \"mirror/rename_mirror_namespace.py\"\n"
            "spec = importlib.util.spec_from_file_location(\"p\", POLICY)\n")
        self.assertEqual(policy.problems(self.tmp), [])
        judge.write_text(judge.read_text() + "OTHER = Path(__file__).parent / \"helper.py\"\n")
        self.assertIn("tools/provenance/verify_extraction.py loads tools/provenance/helper.py, "
                      "which is not a policy file", policy.problems(self.tmp))
        judge.write_text(judge.read_text().replace("helper.py", "content_scan_policy.py"))
        kept = tuple(p for p in policy.POLICY_FILES if p != "/tools/provenance/content_scan_policy.py")
        original, policy.POLICY_FILES = policy.POLICY_FILES, kept
        try:
            self.owners(drop="/tools/provenance/content_scan_policy.py")
            self.assertEqual(policy.problems(self.tmp),
                             ["tools/provenance/verify_extraction.py loads tools/provenance/content_scan_policy.py, "
                              "which is not a policy file"])
        finally:
            policy.POLICY_FILES = original

    def test_the_reviewed_prefixes_match_the_release_path_review(self) -> None:
        import release_path_review
        self.assertEqual(policy.REVIEWED_PREFIXES,
                         tuple("/" + prefix for prefix in release_path_review.PROTECTED_PREFIXES))

    def lanes(self, scripts: list[str]) -> None:
        (self.tmp / "scripts").mkdir(exist_ok=True)
        entries = [{"script": s} for s in scripts]
        (self.tmp / policy.LANE_MANIFEST).write_text(json.dumps({"lanes": {policy.POLICY_LANE: entries}}))

    def test_the_policy_lane_must_keep_the_lane_coverage_test_and_this_check(self) -> None:
        self.lanes(list(policy.REQUIRED_LANE_SCRIPTS))
        self.assertEqual(policy.problems(self.tmp), [])  # control: a complete lane passes
        for dropped in policy.REQUIRED_LANE_SCRIPTS:
            with self.subTest(dropped=dropped):
                self.lanes([s for s in policy.REQUIRED_LANE_SCRIPTS if s != dropped])
                self.assertIn(f"{policy.LANE_MANIFEST} lane `{policy.POLICY_LANE}` does not run {dropped}",
                              policy.problems(self.tmp))

    def test_an_unreadable_policy_lane_fails(self) -> None:
        (self.tmp / "scripts").mkdir()
        (self.tmp / policy.LANE_MANIFEST).write_text('{"lanes": {}}')
        self.assertTrue(any("no readable" in p for p in policy.problems(self.tmp)))

    def test_the_lane_manifest_is_not_a_policy_file(self) -> None:
        self.assertNotIn("/scripts/ci_lanes.json", policy.POLICY_FILES)
        self.assertNotIn("ci_lanes.json", (ROOT / policy.CODEOWNERS).read_text())
        # Still named by the real manifest the check reads.
        self.assertEqual(policy.lane_problems(ROOT), [])

    def test_the_policy_workflow_must_run_the_base_copy(self) -> None:
        self.assertEqual(policy.problems(self.tmp), [])  # control: the fixture runs it
        for text in ("jobs:\n  release-policy:\n    steps:\n      - run: python3 -I scripts/check_release_policy.py check\n",
                     self.BASE_JUDGE.replace(" --from-base", "")):
            with self.subTest(text=text):
                self.workflow("release-policy.yml", text)
                self.assertIn("release-policy.yml does not run the base branch's check_release_policy.py on the tree",
                              policy.problems(self.tmp))
        (self.tmp / ".github/workflows/release-policy.yml").unlink()
        self.assertTrue(any("release-policy.yml is missing" in p for p in policy.problems(self.tmp)))

    def owners(self, extra: str = "", drop: str | None = None) -> None:
        lines = [line for line in (ROOT / policy.CODEOWNERS).read_text().splitlines()
                 if drop is None or not line.startswith(drop + " ")]
        (self.tmp / policy.CODEOWNERS).write_text("\n".join(lines) + "\n" + extra)

    def test_from_the_base_a_tree_may_own_more_files(self) -> None:
        self.owners(extra="/scripts/new_release_step.py @danielraffel\n")
        strict = policy.problems(self.tmp)
        self.assertIn(f"{policy.CODEOWNERS} owns /scripts/new_release_step.py, which POLICY_FILES does not name",
                      strict)  # control: the tree's own copy wants the two lists equal
        self.assertEqual(policy.problems(self.tmp, ROOT), [])

    def test_from_the_base_a_tree_cannot_disown_a_policy_file_it_keeps(self) -> None:
        kept = "/scripts/verify_release_tag.py"
        self.owners(drop=kept)
        (self.tmp / "scripts").mkdir(exist_ok=True)
        (self.tmp / kept.lstrip("/")).write_text("# still here\n")
        self.assertIn(f"{policy.CODEOWNERS} does not give the base branch's policy file {kept} to {policy.OWNER}",
                      policy.problems(self.tmp, ROOT))
        (self.tmp / kept.lstrip("/")).unlink()  # a file the tree deletes is retired, not weakened
        self.assertEqual(policy.problems(self.tmp, ROOT), [])

    def test_from_the_base_a_policy_script_may_import_a_module_the_tree_adds(self) -> None:
        self.owners(extra="/scripts/release_helper.py @danielraffel\n")
        (self.tmp / "scripts").mkdir(exist_ok=True)
        (self.tmp / "scripts/release_helper.py").write_text("VALUE = 1\n")
        (self.tmp / "scripts/verify_release_tag.py").write_text("import release_helper\n")
        self.assertEqual(policy.problems(self.tmp, ROOT), [])
        self.owners()  # control: a module nobody owns is still refused
        self.assertIn("scripts/verify_release_tag.py imports scripts/release_helper.py, which is not a policy file",
                      policy.problems(self.tmp, ROOT))

    def base_lane(self, entries: list[dict]) -> Path:
        base = Path(tempfile.mkdtemp(prefix="release-policy-base-"))
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        (base / "scripts").mkdir()
        (base / policy.LANE_MANIFEST).write_text(json.dumps({"lanes": {policy.POLICY_LANE: entries}}))
        return base

    def test_from_the_base_the_lane_keeps_every_base_entry_unchanged(self) -> None:
        required = [{"script": s} for s in policy.REQUIRED_LANE_SCRIPTS]
        kept = {"script": "scripts/test_release_cadence.py", "isolated": True}
        base = self.base_lane(required + [kept])
        (self.tmp / "scripts").mkdir(exist_ok=True)
        (self.tmp / kept["script"]).write_text("# test\n")
        manifest = self.tmp / policy.LANE_MANIFEST
        for lane, ok in ((required + [kept], True), (required + [kept, {"script": "scripts/test_new.py"}], True),
                         (required + [{"script": kept["script"], "isolated": True, "args": []}], False),
                         (required + [{**kept, "args": ["--help"]}], False),
                         (required, False), (required + [{"script": kept["script"]}], False),
                         (required + [{**kept, "isolated": False}], False)):
            with self.subTest(lane=lane):
                manifest.write_text(json.dumps({"lanes": {policy.POLICY_LANE: lane}}))
                self.assertEqual(policy.lane_problems(self.tmp), [])  # the tree's own copy has no baseline
                found = policy.lane_problems(self.tmp, base)
                self.assertEqual(found == [], ok, found)
        (self.tmp / kept["script"]).unlink()  # a deleted test is retired, not dropped
        manifest.write_text(json.dumps({"lanes": {policy.POLICY_LANE: required}}))
        self.assertEqual(policy.lane_problems(self.tmp, base), [])

    def test_from_the_base_a_lane_entry_may_become_isolated(self) -> None:
        required = [{"script": s} for s in policy.REQUIRED_LANE_SCRIPTS]
        base = self.base_lane(required + [{"script": "scripts/test_check_release_policy.py"}])
        (self.tmp / "scripts").mkdir(exist_ok=True)
        (self.tmp / "scripts/test_check_release_policy.py").write_text("# test\n")
        self.lanes(list(policy.REQUIRED_LANE_SCRIPTS))
        self.assertTrue(policy.lane_problems(self.tmp, base))  # control: dropped
        manifest = self.tmp / policy.LANE_MANIFEST
        manifest.write_text(json.dumps({"lanes": {policy.POLICY_LANE: required + [
            {"script": "scripts/test_check_release_policy.py", "isolated": True}]}}))
        self.assertEqual(policy.lane_problems(self.tmp, base), [])

    def test_from_the_base_an_unreadable_base_lane_fails(self) -> None:
        self.lanes(list(policy.REQUIRED_LANE_SCRIPTS))
        base = Path(tempfile.mkdtemp(prefix="release-policy-base-"))
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        self.assertTrue(any("base branch's" in p for p in policy.lane_problems(self.tmp, base)))

    def test_from_base_refuses_to_judge_its_own_checkout(self) -> None:
        done = subprocess.run([sys.executable, "-I", str(ROOT / "scripts/check_release_policy.py"),
                               "--root", str(ROOT), "--from-base", "check"], capture_output=True, text=True)
        self.assertEqual(done.returncode, 1)
        self.assertIn("--from-base judges another checkout", done.stderr)

    def test_the_signing_workflow_must_use_the_environment(self) -> None:
        self.workflow("release-tag.yml", "jobs:\n  sign:\n    runs-on: x\n")
        self.assertTrue(any("does not run in" in p for p in policy.problems(self.tmp)))


class Isolation(unittest.TestCase):
    """Checked-out code never runs where the signing key or App token is in reach."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="release-isolation-"))
        (self.tmp / "scripts").mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp)

    def found(self, signing: str, allow: bool = False) -> list[str]:
        if not signing:
            signing = "jobs:\n  check:\n    steps:\n      - run: true\n"
        elif signing.lstrip().startswith("steps:"):
            signing = "jobs:\n  sign:\n" + signing
        return isolation({policy.SIGNING_WORKFLOW: signing}, self.tmp, allow)

    def allowed(self, signing: str) -> list[str]:
        """The problems left once the fixture's own steps are allowlisted."""
        return self.found(signing, allow=True)

    def test_python_without_isolation_fails(self) -> None:
        self.assertEqual(self.allowed("    steps:\n      - name: a\n        run: python3 -I scripts/x.py\n"), [])
        self.assertTrue(self.found("    steps:\n      - name: a\n        run: python3 scripts/x.py\n"))
        self.assertTrue(self.found("    steps:\n      - name: a\n        run: python3 -c 'print(1)'\n"))

    def test_a_step_holding_a_secret_runs_no_python(self) -> None:
        key = ("      - name: sign\n        env:\n          K: ${{ secrets.RELEASE_TAG_SIGNING_KEY }}\n"
               "        run: python3 -I scripts/verify_release_tag.py\n")
        token = ("      - name: push\n        env:\n          GH_TOKEN: ${{ steps.app-token.outputs.token }}\n"
                 "        run: python3 -I scripts/release_request.py freeze\n")
        clean = "      - name: sign\n        env:\n          K: ${{ secrets.RELEASE_TAG_SIGNING_KEY }}\n        run: git tag -s v1\n"
        self.assertTrue(any("holds a secret" in p for p in self.found("    steps:\n" + key)))
        self.assertTrue(any("holds a secret" in p for p in self.found("    steps:\n" + token)))
        self.assertEqual(self.allowed("    steps:\n" + clean), [])
        # Any minted token counts, whatever its step id: the push mints its own.
        for step_id in ("push-token", "other-token"):
            with self.subTest(step_id=step_id):
                minted = token.replace("steps.app-token", f"steps.{step_id}")
                self.assertTrue(any("holds a secret" in p for p in self.found("    steps:\n" + minted)))
        # Other spellings of the same expressions are secrets too.
        for spelling in ("${{ secrets['RELEASE_TAG_SIGNING_KEY'] }}", "${{ toJSON(secrets) }}",
                         "${{ steps['push-token'].outputs.token }}", "${{ steps.x.outputs['token'] }}"):
            with self.subTest(spelling=spelling):
                held = token.replace("${{ steps.app-token.outputs.token }}", spelling)
                self.assertTrue(any("holds a secret" in p for p in self.found("    steps:\n" + held)))
        # A step output that is not a token is not a secret.
        output = token.replace("GH_TOKEN: ${{ steps.app-token.outputs.token }}", "T: ${{ steps.request.outputs.tag }}")
        self.assertEqual(self.allowed("    steps:\n" + output), [])

    def test_version_at_land_runs_no_python_where_it_holds_a_token(self) -> None:
        # Must-pass: the App token is minted and used in steps that run no
        # Python. Must-fail: repository Python in a token-holding step, or
        # Python without -I anywhere in the workflow.
        def found(text: str, allow: bool = False) -> list[str]:
            return isolation({policy.VERSION_WORKFLOW: text}, self.tmp, allow)

        mint = ("      - name: mint\n        id: bump-token\n"
                "        uses: actions/create-github-app-token@1b10c78c7865c340bc4f6099eb2f838309f1e8c3\n        with:\n"
                "          private-key: ${{ secrets.SHIPYARD_APP_PRIVATE_KEY }}\n")
        push = ("      - name: push\n        env:\n          GH_TOKEN: ${{ steps.bump-token.outputs.token }}\n"
                "        run: git push origin HEAD\n")
        plan = "      - name: plan\n        run: python3 -I scripts/version_at_land.py plan\n"
        def workflow(plan_steps: str, push_steps: str) -> str:
            return "jobs:\n  plan:\n    steps:\n" + plan_steps + "  push:\n    steps:\n" + push_steps

        self.assertEqual(found(workflow(plan, mint + push), allow=True), [])
        held = push.replace("git push origin HEAD", "python3 -I scripts/version_at_land.py apply")
        self.assertTrue(any("holds a secret and runs Python" in p for p in found(workflow(plan, mint + held))))
        keyed = mint.replace("        with:", "        run: python3 -I x.py\n        with:")
        self.assertTrue(any("unsupported construct" in p for p in found(workflow(plan, keyed + push))))
        loose = plan.replace("python3 -I", "python3")
        self.assertTrue(any("without -I" in p for p in found(workflow(loose, mint + push))))
        self.assertTrue(any("unsupported construct" in p for p in found("    steps:\n" + plan)))

    def test_a_token_job_runs_no_repository_python_in_any_step(self) -> None:
        # Must-pass: Python in a job that holds no token. Must-fail: the same
        # Python in an earlier step of the job that later mints the token,
        # which could plant a hook or a GITHUB_PATH entry the push then runs.
        def found(text: str, allow: bool = False) -> list[str]:
            return isolation({policy.VERSION_WORKFLOW: text}, self.tmp, allow)

        plan = "  plan:\n    steps:\n      - name: plan\n        run: python3 -I scripts/version_at_land.py plan\n"
        push = ("  push:\n    steps:\n      - name: mint\n"
                "        uses: actions/create-github-app-token@1b10c78c7865c340bc4f6099eb2f838309f1e8c3\n"
                "        with:\n          private-key: ${{ secrets.SHIPYARD_APP_PRIVATE_KEY }}\n")
        self.assertEqual(found("jobs:\n" + plan + push, allow=True), [])
        merged = push.replace("    steps:\n", "    steps:\n      - name: plan\n"
                              "        run: python3 -I scripts/version_at_land.py apply\n")
        self.assertTrue(any("job push holds a secret and runs Python" in p for p in found("jobs:\n" + plan + merged)))

    def test_the_committed_version_at_land_holds_the_rule(self) -> None:
        text = (ROOT / policy.WORKFLOWS / policy.VERSION_WORKFLOW).read_text()
        self.assertEqual(policy.isolation_problems({policy.VERSION_WORKFLOW: text}, self.tmp), [])
        self.assertNotIn(policy.RELEASE_ENVIRONMENT, policy.environment_names(text))

    MINT = ("      - name: mint\n        id: {id}\n"
            "        uses: actions/create-github-app-token@1b10c78c7865c340bc4f6099eb2f838309f1e8c3\n")

    def read(self, token: str = "${{ steps.app-token.outputs.token }}", run: str | None = None,
             mint: str | None = None, name: str | None = "read") -> str:
        """A job that mints an App token, then a step that reads with `token`."""
        head = f"      - name: {name}\n        env:\n" if name else "      - env:\n"
        run = run if run is not None else 'run: gh api "repos/$R/immutable-releases" > out.json\n'
        return ("    steps:\n" + (self.MINT.format(id="app-token") if mint is None else mint)
                + head + f"          GH_TOKEN: {token}\n        " + run)

    def refused(self, workflow: str, reason: str = "Administration-only") -> None:
        """Refused for the general reason: a structure the parser refuses, or a
        credential-holding step that is not in the run allowlist. `reason`
        names the rule for jobs that hold no credential."""
        problems = self.found(workflow)
        self.assertTrue(any(reason in problem or "unsupported construct" in problem or UNSUPPORTED in problem
                            for problem in problems), problems)

    def test_admin_only_reads_need_the_app_token(self) -> None:
        for endpoint in ("rules/branches/main", "branches/main/protection/required_status_checks",
                         "immutable-releases"):
            with self.subTest(endpoint=endpoint):
                run = f'run: gh api "repos/$R/{endpoint}" > out.json\n'
                self.assertEqual(self.allowed(self.read(run=run)), [])
                self.refused(self.read(token="${{ github.token }}", run=run))
                self.refused(self.read(token="${{ steps.other-token.outputs.token }}", run=run))
                # Allowlisting the body does not stand in for the token.
                for token in ("${{ github.token }}", "${{ steps.other-token.outputs.token }}"):
                    problems = self.allowed(self.read(token=token, run=run))
                    self.assertTrue(any("Administration-only" in p for p in problems), problems)
        push = self.read(token="${{ steps.push-token.outputs.token }}", mint=self.MINT.format(id="push-token"))
        self.assertEqual(self.allowed(push), [])
        # A read of another endpoint needs no App token.
        self.assertEqual(self.allowed(self.read(token="${{ github.token }}",
                                                run='run: gh api "repos/$GITHUB_REPOSITORY/actions/runs" > out.json\n')), [])

    def test_a_comment_naming_the_app_token_does_not_satisfy_the_rule(self) -> None:
        run = ("run: |\n          # GH_TOKEN: ${{ steps.app-token.outputs.token }}\n"
               '          gh api "repos/$R/immutable-releases" > out.json\n')
        self.refused(self.read(token="${{ github.token }}", run=run))
        # Nor does the App token under another name, with GH_TOKEN the Actions token.
        other = self.read(token="${{ github.token }}").replace(
            "          GH_TOKEN:", "          OTHER: ${{ steps.app-token.outputs.token }}\n          GH_TOKEN:")
        self.refused(other)
        # The allowlist is judged as text: a body that names an
        # Administration-only endpoint, even in a comment, needs the App token.
        quiet = 'run: |\n          # gh api "repos/$R/immutable-releases"\n          true\n'
        self.assertTrue(any("Administration-only" in p for p in self.allowed(self.read(token="${{ github.token }}", run=quiet))))
        self.assertEqual(self.allowed(self.read(run=quiet)), [])
        # A commented App token does not satisfy the rule once allowlisted either.
        problems = self.allowed(self.read(token="${{ github.token }}", run=run))
        self.assertTrue(any("Administration-only" in p for p in problems), problems)

    def test_folded_run_scalars_fail_closed(self) -> None:
        # YAML folds these lines into an Administration-only read. The
        # line-based shell reader must not accept them as separate commands.
        for indicator in (">", ">-", ">+", ">2", ">2-", ">-2", ">- # folded"):
            with self.subTest(indicator=indicator):
                run = f'run: {indicator}\n          gh\n          api "repos/$R/immutable-releases"\n'
                self.refused(self.read(token="${{ github.token }}", run=run), "folded run scalar")
        # Literal blocks preserve the newline, and a supported App-token
        # read remains accepted.
        literal = 'run: |-\n          gh api "repos/$R/immutable-releases"\n'
        self.assertEqual(self.allowed(self.read(run=literal)), [])
        self.refused(self.read(token="${{ github.token }}", run=literal))

    def test_folded_run_rejection_does_not_depend_on_visible_commands(self) -> None:
        # Folding can join comments or arguments as well as command names;
        # refuse the scalar itself for every workflow scanned by the policy.
        for command in ('gh\n          api "repos/$R/rulesets"', "echo ok"):
            with self.subTest(command=command):
                workflow = f"jobs:\n  build:\n    steps:\n      - run: >-\n          {command}\n"
                problems = policy.isolation_problems({"other.yml": workflow}, self.tmp)
                self.assertTrue(any("folded run scalar" in problem for problem in problems), problems)

    def test_yaml_aliases_in_a_step_fail_closed(self) -> None:
        # An alias expands to an Administration-only read (or a GH_TOKEN) that
        # neither the raw step text nor the parsed run value shows.
        anchored = ("jobs:\n  sign:\n    env:\n      READ: &read gh api \"repos/$R/immutable-releases\"\n"
                    "      STEP: &step {run: 'gh api repos/x/rulesets'}\n")
        cases = {
            "run alias": self.read(token="${{ github.token }}", run="run: *read\n"),
            "token alias": self.read(token="*token"),
            "merge key": self.read(token="${{ github.token }}", run="<<: *step\n"),
            "anchored run": self.read(token="${{ github.token }}", run="run: &r echo ok\n"),
        }
        for label, steps in cases.items():
            with self.subTest(label):
                problems = policy.isolation_problems({policy.SIGNING_WORKFLOW: anchored + steps}, self.tmp)
                self.assertTrue(any("YAML anchor, alias" in p for p in problems), problems)
        whole = anchored + "    steps:\n      - *step\n"
        problems = policy.isolation_problems({"other.yml": whole}, self.tmp)
        self.assertTrue(any("YAML anchor, alias" in p for p in problems), problems)
        # Shell globs inside a literal run block are not YAML indirection.
        glob = self.read(run='run: |\n          ls *.json *out\n          gh api "repos/$R/immutable-releases"\n')
        self.assertEqual(self.allowed(glob), [])

    def test_a_steps_line_outside_the_jobs_steps_key_mints_nothing(self) -> None:
        # A `steps:` list written inside a job-level key (a block scalar under
        # env:, or a nested mapping) is text the runner never runs. Its fake
        # `id: app-token` mint must not satisfy the App-token rule.
        fake = "    steps:\n" + self.MINT.format(id="app-token")
        hidden = "".join("        " + line[4:] + "\n" if line.startswith("    ") else line + "\n"
                         for line in fake.splitlines())
        cases = {
            "block scalar": "    env:\n      NOTE: |\n" + "".join("    " + line + "\n" for line in hidden.splitlines()),
            "nested key": "    env:\n      X:\n" + "".join("  " + line + "\n" for line in hidden.splitlines()),
        }
        for label, before in cases.items():
            with self.subTest(label):
                workflow = "jobs:\n  sign:\n" + before + self.read(mint="")
                self.refused(workflow)
        # A second steps key at the job's column is refused too.
        twice = "jobs:\n  sign:\n" + fake + self.read(mint="")
        self.refused(twice, "more than one steps key")
        # The honest layout still passes.
        self.assertEqual(self.allowed("jobs:\n  sign:\n" + self.read()), [])

    def test_a_steps_key_inside_a_job_block_scalar_is_rejected(self) -> None:
        # A fake minting list in a job-level block scalar is text, not a
        # runnable steps list. The layout audit must still report the hidden
        # key instead of silently dropping it.
        hidden = ("jobs:\n  sign:\n    env:\n      NOTE: |\n"
                  "        steps:\n"
                  "          - id: app-token\n"
                  "            uses: actions/create-github-app-token@"
                  "1b10c78c7865c340bc4f6099eb2f838309f1e8c3\n")
        workflow = hidden + self.read(mint="")
        problems = policy.step_layout_problems(policy.SIGNING_WORKFLOW, workflow)
        self.assertTrue(any("unsupported construct" in problem for problem in problems),
                        problems)

    def test_a_column_zero_comment_does_not_hide_the_steps(self) -> None:
        # `# note: |` is a YAML comment, not a block-scalar opener. Read as
        # one, it hid the job's steps key, so an Actions-token Administration
        # read and an unisolated Python step passed unseen.
        for comment in ("# note: |", "# note: >-", "  # note: |"):
            with self.subTest(comment=comment):
                head = "jobs:\n  sign:\n" + comment + "\n"
                self.refused(head + self.read(token="${{ github.token }}"))
                self.assertTrue(any("without -I" in problem for problem in self.found(
                    head + "    steps:\n      - name: py\n        run: python3 scripts/x.py\n")))
                self.assertEqual(self.allowed(head + self.read()), [])
                self.assertEqual(len(policy.workflow_steps(head + self.read())), 2)

    def test_a_block_scalar_on_a_step_dash_line_hides_no_later_key(self) -> None:
        # `- name: >-` opens a scalar owned by `name` (column 8), not by the
        # dash (column 6). Read from the dash, it hid the step's later keys,
        # so `run: *read` escaped the alias check and an Actions-token
        # Administration read passed.
        anchored = ("jobs:\n  sign:\n    env:\n      READ: &read gh api \"repos/$R/immutable-releases\"\n")
        for label, name in {"folded name": ">-\n          read", "literal name": "|\n          read",
                            "no space before indicator": "a:>"}.items():
            with self.subTest(label):
                steps = self.read(token="${{ github.token }}", run="run: *read\n", name=name)
                problems = policy.isolation_problems({policy.SIGNING_WORKFLOW: anchored + steps}, self.tmp)
                self.assertTrue(any("YAML anchor, alias" in p for p in problems),
                                problems)
                with self.assertRaisesRegex(policy.PolicyError, "YAML anchor, alias"):
                    policy.parse_workflow(anchored + steps)
        # The anchor itself under a `- n: |` matrix item is not hidden either.
        matrix = ("jobs:\n  sign:\n    strategy:\n      matrix:\n        include:\n          - n: |\n"
                  "              x\n            r: &read gh api \"repos/$R/immutable-releases\"\n")
        self.assertTrue(any("YAML anchor" in p for p in policy.step_layout_problems("matrix.yml", matrix)))
        # The scalar's own body stays hidden, and an honest step still passes.
        body = "jobs:\n  sign:\n" + self.read(name=">-\n          read text")
        self.assertEqual(policy.workflow_steps(body)[1].name, "read text")
        self.assertEqual(self.allowed(body), [])

    def test_the_token_must_come_from_the_pinned_app_token_action(self) -> None:
        cases = {
            "a run step": "      - name: mint\n        id: app-token\n        run: echo token=x >> \"$GITHUB_OUTPUT\"\n",
            "another action": "      - name: mint\n        id: app-token\n        uses: someone/make-token@"
                              + "a" * 40 + "\n",
            "an unpinned action": "      - name: mint\n        id: app-token\n"
                                  "        uses: actions/create-github-app-token@v2\n",
            "no minting step": "",
        }
        for label, mint in cases.items():
            with self.subTest(label):
                self.refused(self.read(mint=mint))
        # A token minted in another job, or after the read, is not this step's.
        later = self.read(mint="") + self.MINT.format(id="app-token")
        self.refused(later)
        elsewhere = ("jobs:\n  mint:\n" + "    steps:\n" + self.MINT.format(id="app-token")
                     + "  read:\n" + self.read(mint=""))
        self.refused(elsewhere)
        # Allowlisting the steps does not stand in for the mint: each of these
        # still reads without a token minted earlier in its own job.
        for label, workflow in {**{label: self.read(mint=mint) for label, mint in cases.items()},
                                "later": later, "elsewhere": elsewhere}.items():
            with self.subTest(allowlisted=label):
                problems = self.allowed(workflow)
                self.assertTrue(any("Administration-only" in p for p in problems), problems)
        self.assertEqual(self.allowed(self.read()), [])

    def test_a_gh_api_call_split_across_lines_is_read(self) -> None:
        for label, run in {
            "endpoint on the next line": 'run: |\n          gh api \\\n            "repos/$R/immutable-releases" > out.json\n',
            "options first": 'run: |\n          timeout 120 gh api --method GET --paginate \\\n'
                             '            "repos/$R/rulesets" > out.json\n',
            "inside a substitution": 'run: |\n          test "$(gh api "repos/$R/rules/branches/main" --jq length)" = 1\n',
            "after a field": 'run: |\n          gh api -f a=b -H "Accept: x" repos/$R/branches/main/protection\n',
        }.items():
            with self.subTest(label):
                self.refused(self.read(token="${{ github.token }}", run=run))
                self.assertEqual(self.allowed(self.read(run=run)), [])

    def test_a_path_qualified_or_quoted_gh_is_read(self) -> None:
        for label, run in {
            "an absolute path": 'run: /usr/bin/gh api "repos/$R/immutable-releases" > out.json\n',
            "a relative path": 'run: ./gh api "repos/$R/rulesets" > out.json\n',
            "a nested relative path": 'run: bin/gh api "repos/$R/rules/branches/main"\n',
            "a quoted path": 'run: |\n          "/opt/homebrew/bin/gh" api "repos/$R/immutable-releases"\n',
            "a quoted name": "run: |\n          'gh' api repos/$R/branches/main/protection\n",
            "an escaped name": 'run: \\gh api "repos/$R/immutable-releases"\n',
        }.items():
            with self.subTest(label):
                self.refused(self.read(token="${{ github.token }}", run=run))
                self.assertEqual(self.allowed(self.read(run=run)), [])
        # Whatever tool a body runs, unreviewed it is refused, and reviewed it
        # still needs the App token to name an Administration-only endpoint.
        other = self.read(token="${{ github.token }}", run='run: my-gh api "repos/$R/immutable-releases"\n')
        self.refused(other)
        self.assertTrue(any("Administration-only" in p for p in self.allowed(other)))

    def test_every_gh_api_call_on_one_line_is_read(self) -> None:
        # A later call on the same shell line must not hide behind an earlier,
        # harmless one: each command start is scanned on its own.
        first = 'gh api "repos/$R/actions/runs"'
        for label, run in {
            "after a semicolon": f'run: {first} ; gh api "repos/$R/immutable-releases"\n',
            "after a semicolon with no space": f'run: {first};gh api "repos/$R/rulesets"\n',
            "after &&": f'run: {first} && gh api repos/$R/branches/main/protection\n',
            "after ||": f'run: {first} || /usr/bin/gh api "repos/$R/rules/branches/main"\n',
            "in a pipeline": f"run: |\n          {first} | gh api --input - \"repos/$R/rulesets\"\n",
            "after &": f'run: {first} & gh api "repos/$R/immutable-releases"\n',
        }.items():
            with self.subTest(label):
                self.refused(self.read(token="${{ github.token }}", run=run))
                self.assertEqual(self.allowed(self.read(run=run)), [])

    def test_a_redirection_before_the_endpoint_is_not_the_endpoint(self) -> None:
        # A redirection word, attached or detached, precedes the endpoint; the
        # Administration-only read behind it still needs the App token.
        for label, run in {
            "attached >": 'run: gh api >out.json "repos/$R/immutable-releases"\n',
            "quoted attached >": 'run: gh api >"out.json" repos/$R/rulesets\n',
            "detached >": 'run: gh api > out.json "repos/$R/immutable-releases"\n',
            "2>": 'run: gh api 2>/dev/null "repos/$R/branches/main/protection"\n',
            "detached 2>": 'run: gh api 2> err.log "repos/$R/rules/branches/main"\n',
            "&>": 'run: gh api &>log "repos/$R/immutable-releases"\n',
            ">>": 'run: gh api >>out.json "repos/$R/immutable-releases"\n',
            "<": 'run: gh api <in.json "repos/$R/rulesets"\n',
            "2>&1": 'run: gh api 2>&1 "repos/$R/immutable-releases"\n',
        }.items():
            with self.subTest(label):
                self.refused(self.read(token="${{ github.token }}", run=run))
                self.assertEqual(self.allowed(self.read(run=run)), [])
        # An option this check does not know cannot hide the read either.
        unknown = 'run: gh api --some-new-flag "repos/$R/immutable-releases"\n'
        self.refused(self.read(token="${{ github.token }}", run=unknown))
        # A read of another endpoint, redirected, still needs no App token.
        self.assertEqual(self.allowed(self.read(token="${{ github.token }}",
                                              run='run: gh api >out.json "repos/$GITHUB_REPOSITORY/actions/runs"\n')), [])

    def test_an_escaped_or_quoted_gh_spelling_is_still_gh_api(self) -> None:
        # The shell runs each of these as `gh api`; the Administration-only
        # read still needs the App token.
        for label, run in {
            "backslash in gh": 'run: g\\h api "repos/$R/immutable-releases"\n',
            "single-quoted h": "run: g'h' api \"repos/$R/immutable-releases\"\n",
            "double-quoted g": 'run: |\n          "g"h api "repos/$R/rulesets"\n',
            "leading backslash": 'run: \\gh api "repos/$R/branches/main/protection"\n',
            "quoted api": "run: gh 'a'pi \"repos/$R/rules/branches/main\"\n",
            "backslash in api": 'run: gh a\\pi "repos/$R/immutable-releases"\n',
            "after another call": 'run: gh api "repos/$R/actions/runs" ; g\\h api "repos/$R/immutable-releases"\n',
        }.items():
            with self.subTest(label):
                self.refused(self.read(token="${{ github.token }}", run=run))
                self.assertEqual(self.allowed(self.read(run=run)), [])
        # A variable endpoint behind an escaped gh still cannot be read.
        self.refused(self.read(run='run: g\\h api "$endpoint"\n'), "an endpoint this check cannot read")

    def test_a_gh_api_call_whose_endpoint_cannot_be_read_fails_closed(self) -> None:
        for label, run in {
            "a variable": 'run: gh api "$endpoint" > out.json\n',
            "a braced variable": "run: gh api ${ENDPOINT}\n",
            "a substitution": 'run: gh api "$(cat endpoint.txt)"\n',
            "no operand": "run: gh api --paginate\n",
        }.items():
            with self.subTest(label):
                self.refused(self.read(run=run), "an endpoint this check cannot read")

    def test_a_shell_operator_attached_to_the_endpoint_ends_the_word(self) -> None:
        # The shell splits a word at an unquoted operator, so `"$ENDPOINT";`
        # runs gh api with a variable endpoint. That endpoint cannot be read.
        for label, run in {
            "variable then ;": 'run: gh api "$ENDPOINT"; echo done\n',
            "bare variable then ;": "run: gh api $ENDPOINT;\n",
            "braced variable then &&": 'run: gh api "${ENDPOINT}"&& echo done\n',
            "variable then |": 'run: gh api "$ENDPOINT"|jq .\n',
            "variable then >": 'run: gh api "$ENDPOINT">out.json\n',
            "variable then 2>": 'run: gh api "$ENDPOINT"2>/dev/null\n',
            "variable then )": 'run: ( gh api "$ENDPOINT")\n',
            "variable then &": 'run: gh api "$ENDPOINT"&\n',
            "substitution then ;": 'run: gh api "$(cat endpoint)";\n',
            "unquoted substitution then ;": "run: gh api $(cat endpoint);\n",
            "backticks then ;": "run: gh api `cat endpoint`;\n",
        }.items():
            with self.subTest(label):
                self.refused(self.read(token="${{ github.token }}", run=run),
                             "an endpoint this check cannot read")
        # An Administration read behind an attached operator still needs the App token.
        self.refused(self.read(token="${{ github.token }}", run='run: gh api "repos/$R/immutable-releases";\n'))

    def test_an_unnamed_step_is_scanned(self) -> None:
        self.refused(self.read(token="${{ github.token }}", name=None))
        self.assertEqual(self.allowed(self.read(name=None)), [])
        bare = "    steps:\n" + self.MINT.format(id="app-token") + '      - run: gh api "repos/$R/immutable-releases"\n'
        self.refused(bare)
        # The Python isolation rule reads unnamed steps through the same reader.
        self.assertTrue(any("without -I" in problem for problem in self.found(
            "    steps:\n      - run: python3 scripts/x.py\n")))

    def test_a_step_env_this_check_cannot_read_fails_closed(self) -> None:
        flow = ("    steps:\n" + self.MINT.format(id="app-token")
                + "      - name: read\n        env: {GH_TOKEN: \"${{ steps.app-token.outputs.token }}\"}\n"
                '        run: gh api "repos/$R/immutable-releases"\n')
        self.refused(flow, "cannot be read")

    def test_a_layout_the_reader_cannot_follow_fails_closed(self) -> None:
        gh = '"repos/$R/rulesets"'
        header = "    steps:\n" + self.MINT.format(id="app-token")
        env = "        env:\n          GH_TOKEN: ${{ github.token }}\n"
        cases = {
            "a quoted run key": f'      - name: read\n        "run": gh api {gh}\n{env}',
            "a space before the colon": f"      - name: read\n        run : gh api {gh}\n{env}",
            "keys not two columns past the dash": f"      -   run: gh api {gh}\n          env:\n            GH_TOKEN: ${{{{ github.token }}}}\n",
            "a plain multi-line value": f"      - name: read\n        run:\n          gh api {gh}\n{env}",
            "a quoted multi-line value": f'      - name: read\n        run: "echo hi;\n          gh api {gh}"\n{env}',
        }
        for label, step in cases.items():
            with self.subTest(label):
                self.refused(header + step)

    def test_jobs_stop_before_later_top_level_mappings(self) -> None:
        # A top-level env mapping may contain a key that looks like a job id;
        # it must not replace the real job in the jobs mapping.
        workflow = ("jobs:\n  sign:\n" + self.read(token="${{ github.token }}")
                    + "env:\n  sign:\n    harmless\n")
        self.refused(workflow)

    def test_shell_comments_are_removed_before_continuations_are_joined(self) -> None:
        run = ('run: |\n          # this comment continues \\\n'
               '          gh api "repos/$R/immutable-releases"\n')
        self.refused(self.read(token="${{ github.token }}", run=run))

    def test_plain_run_continuations_fail_closed(self) -> None:
        run = ('run: gh\n          api "repos/$R/immutable-releases"\n'
               '        env:\n          GH_TOKEN: ${{ github.token }}\n')
        self.refused(self.read(token="${{ github.token }}", run=run),
                     "layout this check cannot read")

    def test_unquoted_shell_escapes_are_decoded_for_endpoints(self) -> None:
        run = 'run: gh api repos/$R/immutable\\-releases\n'
        self.refused(self.read(token="${{ github.token }}", run=run))

    def test_steps_layout_scan_ignores_heredoc_payload(self) -> None:
        workflow = ("jobs:\n  sign:\n    steps:\n      - name: emit\n        run: |\n"
                    "          cat <<'YAML'\n          steps: []\n          YAML\n")
        self.assertEqual(self.allowed(workflow), [])

    def test_a_heredoc_steps_key_with_yaml_content_is_shell_payload(self) -> None:
        # A step's run script that writes YAML holding `steps:` (followed by
        # content) is shell payload, not a hidden steps list.
        for lead in ("      - name: emit\n        run: |\n", "      - run: |\n"):
            with self.subTest(lead=lead):
                workflow = ("jobs:\n  sign:\n    steps:\n" + lead
                            + "          cat <<'YAML'\n          steps:\n            - run: true\n          YAML\n")
                self.assertEqual(self.allowed(workflow), [])
                self.assertEqual(policy.step_layout_problems(policy.SIGNING_WORKFLOW, workflow), [])
        # The same text in a job-level block scalar, or in a step key other
        # than run, is still reported.
        for hidden in ("    env:\n      NOTE: |\n        steps:\n          - run: true\n",
                       "    steps:\n      - name: emit\n        with:\n          text: |\n"
                       "            steps:\n              - run: true\n"):
            with self.subTest(hidden=hidden):
                problems = policy.step_layout_problems(policy.SIGNING_WORKFLOW, "jobs:\n  sign:\n" + hidden)
                self.assertTrue(any("unsupported construct" in problem for problem in problems),
                                problems)

    def test_a_continuation_inside_a_word_is_removed_not_spaced(self) -> None:
        # The shell removes backslash-newline, so these run the
        # Administration-only read immutable-releases.
        for label, script in {
            "endpoint": 'gh api "repos/$R/immutable-\\\n          releases" > out.json\n'.replace(
                "\n          releases", "\nreleases"),
            "endpoint, unquoted": 'gh api repos/$R/immutable-\\\nreleases\n',
            "executable": 'g\\\nh api "repos/$R/immutable-releases"\n',
            "api word": 'gh a\\\npi "repos/$R/immutable-releases"\n',
        }.items():
            with self.subTest(label=label):
                run = "run: |\n" + "".join("          " + line + "\n" for line in script.splitlines())
                self.refused(self.read(token="${{ github.token }}", run=run))
                self.assertEqual(self.allowed(self.read(run=run)), [])

    def test_mutable_shell_endpoint_values_need_the_app_token(self) -> None:
        for target in ('repos/$GITHUB_REPOSITORY/$GITHUB_SHA',
                       'repos/${GITHUB_REPOSITORY}/${GITHUB_SHA}',
                       'repos/${{ github.repository }}/$GITHUB_SHA',
                       'repos/$GITHUB_REPOSITORY/actions/runs'):
            with self.subTest(target=target):
                run = ('run: |\n          GITHUB_SHA=immutable-releases\n'
                       '          GITHUB_REPOSITORY=owner/repo\n'
                       f'          gh api "{target}"\n')
                self.refused(self.read(token="${{ github.token }}", run=run), "unresolved expansion")
                self.assertEqual(self.allowed(self.read(run=run)), [] if "${{" not in run else [
                    "release-tag.yml: - name: read has an expression in its run body; pass it through env"])
        # A context expression is pasted into the script before the shell runs,
        # so no run body of a credential-holding job may hold one, reviewed or not.
        run = ('run: |\n          GITHUB_SHA=immutable-releases\n'
               '          GITHUB_REPOSITORY=owner/repo\n'
               '          gh api "repos/${{ github.repository }}/commits/${{ github.sha }}/check-runs"\n')
        problems = self.allowed(self.read(token="${{ github.token }}", run=run))
        self.assertTrue(any("expression in its run body" in p for p in problems), problems)

    def test_ansi_c_shell_quoting_is_refused_before_call_detection(self) -> None:
        for command in ("g$'h' api", "$'gh' api", "gh a$'pi'", r"$'\x67h' api",
                        "g$\\\n'h' api"):
            with self.subTest(command=command):
                script = f"{command} repos/owner/repo/immutable-releases"
                run = "run: |\n" + "".join("          " + line + "\n" for line in script.splitlines())
                self.refused(self.read(token="${{ github.token }}", run=run), "ANSI-C")
                # This syntax is unsupported even with a valid App token.
                self.refused(self.read(run=run), "ANSI-C")
        self.assertEqual(self.allowed(self.read(run='run: gh api repos/owner/repo/immutable-releases\n')), [])

    def test_an_endpoint_built_from_an_expansion_needs_the_app_token(self) -> None:
        for target in ('"repos/$R/$ENDPOINT"', '"repos/$GITHUB_REPOSITORY/$ENDPOINT"',
                       '"repos/$GITHUB_REPOSITORY/${ENDPOINT}"', '"repos/$GITHUB_REPOSITORY/x$ENDPOINT"',
                       '"repos/$R/actions/runs"', '"repos/$GITHUB_REPOSITORY/actions/runs/$ID"',
                       '"repos/${{ github.repository }}/${{ inputs.endpoint }}"'):
            with self.subTest(target=target):
                run = f"run: gh api {target} > out.json\n"
                self.refused(self.read(token="${{ github.token }}", run=run), "unresolved expansion")
                self.assertEqual(self.allowed(self.read(run=run)), [] if "${{" not in run else [
                    "release-tag.yml: - name: read has an expression in its run body; pass it through env"])
        # No context substitution is a safe value in a run body: inputs come
        # through env, as the runner's own GITHUB_REPOSITORY and GITHUB_SHA do.
        for target in ('"repos/${{ github.repository }}/pulls"',
                       '"repos/${{ github.repository }}/commits/${{ github.sha }}/check-runs?per_page=100"'):
            with self.subTest(target=target):
                run = f"run: gh api {target} > out.json\n"
                problems = self.allowed(self.read(token="${{ github.token }}", run=run))
                self.assertTrue(any("expression in its run body" in p for p in problems), problems)
                plain = run.replace("${{ github.repository }}", "$GITHUB_REPOSITORY").replace("${{ github.sha }}", "$GITHUB_SHA")
                self.assertEqual(self.allowed(self.read(token="${{ github.token }}", run=plain)), [])
        # A step that sets GITHUB_REPOSITORY or GITHUB_SHA itself makes them arbitrary values.
        overridden = self.read(token="${{ github.token }}\n          GITHUB_SHA: immutable-releases",
                               run='run: gh api "repos/$GITHUB_REPOSITORY/$GITHUB_SHA"\n')
        self.refused(overridden, "unresolved expansion")

    def test_a_steps_key_the_reader_cannot_follow_fails_closed(self) -> None:
        body = "      - name: a\n        run: python3 scripts/x.py\n"
        self.assertTrue(any("without -I" in problem for problem in self.found("    steps:\n" + body)))
        for key in ('"steps":', "'steps':", "steps :", "steps: &s", "steps: [ ]"):
            with self.subTest(key=key):
                self.refused(f"jobs:\n  sign:\n    {key}\n" + body, "a steps key this check cannot read")
        # The same layouts in any workflow, since the renderer-verifier rule reads every one.
        problems = policy.isolation_problems({"other.yml": '  build:\n    "steps":\n' + body}, self.tmp)
        self.assertTrue(any("unsupported construct" in problem for problem in problems), problems)

    def test_job_headers_are_read_or_fail_closed(self) -> None:
        read = self.read(token="${{ github.token }}", run='run: gh api "repos/$R/rulesets"\n')
        clean = "  clean:\n    steps:\n      - name: noop\n        run: true\n"
        for header in ("  read:  # comment", '  "read":'):
            with self.subTest(header=header):
                self.refused("jobs:\n" + header + "\n" + read + clean)
        unreadable = "jobs:\n  read :\n" + read + clean
        problems = self.found(unreadable)
        self.assertTrue(any("unsupported construct" in problem for problem in problems), problems)

    def test_a_jobs_key_the_reader_cannot_follow_fails_closed(self) -> None:
        # jobs() reads only a block `jobs:` line; a flow mapping, anchor or
        # differently spelled key would otherwise be read as no jobs at all.
        flow = ('jobs: {sign: {steps: [{name: read, run: gh api "repos/$R/rulesets",'
                ' env: {GH_TOKEN: "${{ github.token }}"}}]}}\n')
        for workflow in (flow, "on: push\n" + flow, 'jobs: &all\n  sign:\n    steps: []\n',
                         '"jobs":\n  sign:\n    steps: []\n', "jobs :\n  sign:\n    steps: []\n",
                         "'jobs': {}\n"):
            with self.subTest(workflow=workflow):
                self.refused(workflow, "a jobs key this check cannot read")
                problems = policy.isolation_problems({"other.yml": workflow}, self.tmp)
                self.assertTrue(any("unsupported construct" in problem for problem in problems), problems)
        self.assertEqual(self.allowed("jobs:  # all jobs\n  clean:\n    steps:\n      - name: a\n        run: true\n"), [])

    def test_a_line_under_steps_that_no_step_holds_fails_closed(self) -> None:
        gh = '"repos/$R/rulesets"'
        bare_first = ("    steps:\n      -\n        name: read\n"
                      f"        run: gh api {gh}\n        env:\n          GH_TOKEN: ${{{{ github.token }}}}\n")
        self.refused(bare_first)
        bare_later = "    steps:\n      - name: a\n        run: true\n      -\n        run: python3 scripts/x.py\n"
        self.refused(bare_later, "without -I")
        self.assertEqual(self.allowed("    steps:\n      # a comment\n\n      - name: a\n        run: true\n"), [])

    def test_jobs_at_another_indentation_fail_closed(self) -> None:
        # jobs() reads only job IDs two columns in; four-space jobs would be
        # read as no jobs, and the whole-file fallback would let a token minted
        # in one job satisfy a read in another.
        mint = "  mint:\n    steps:\n" + self.MINT.format(id="app-token")
        read = "  read:\n" + self.read(mint="")
        workflow = "jobs:\n" + "".join("  " + line if line.strip() else line
                                       for line in (mint + read).splitlines(keepends=True))
        self.refused(workflow)
        self.assertEqual(self.allowed("jobs:\n  # note\n\n  clean:\n    steps:\n      - name: a\n        run: true\n"), [])

    def test_a_bare_dash_at_the_steps_indentation_fails_closed(self) -> None:
        # An indentationless sequence: a bare `-` item at the same column as
        # `steps:` must not end the step list and hide the steps after it.
        workflow = ("jobs:\n  sign:\n    steps:\n    - name: a\n      run: true\n    -\n      name: b\n"
                    "      run: true\n    - name: c\n      run: python3 scripts/x.py\n")
        problems = self.found(workflow)
        self.assertTrue(any("without -I" in problem for problem in problems), problems)
        indentless = "jobs:\n  sign:\n    steps:\n    - name: a\n      run: true\n    - name: c\n      run: true\n"
        self.assertEqual(self.allowed(indentless), [])

    def test_the_step_reader_agrees_with_a_yaml_parser(self) -> None:
        try:
            import yaml  # noqa: PLC0415
        except ImportError:
            self.fail("PyYAML is required by the policy checker")
        total = 0
        for path in sorted((ROOT / policy.WORKFLOWS).glob("*.y*ml")):
            text = path.read_text(encoding="utf-8")
            expected = [step for body in yaml.load(text, Loader=yaml.BaseLoader)["jobs"].values()
                        for step in body.get("steps") or []]
            read = policy.workflow_steps(text)
            self.assertEqual(len(read), len(expected), path.name)
            for step, want in zip(read, expected):
                total += 1
                with self.subTest(workflow=path.name, step=want.get("name")):
                    self.assertEqual((step.name, step.id, step.uses), (want.get("name"), want.get("id"), want.get("uses")))
                    self.assertEqual((step.run or "").rstrip("\n"), (want.get("run") or "").rstrip("\n"))
                    self.assertEqual(step.env or {}, {key: str(value) for key, value in (want.get("env") or {}).items()})
        self.assertGreater(total, 100)

    def test_the_signing_workflow_reads_admin_endpoints_only_with_its_app_tokens(self) -> None:
        text = (ROOT / policy.WORKFLOWS / policy.SIGNING_WORKFLOW).read_text(encoding="utf-8")
        self.assertEqual(policy.run_body_problems(policy.SIGNING_WORKFLOW, text), [])
        reads = [step for step in policy.workflow_steps(text) if step.run and policy.ADMIN_ENDPOINT.search(step.run)]
        # The rule has something to judge: the two reads and the freeze exist and were found.
        self.assertGreaterEqual(len(reads), 3)
        # With the Actions token in place of an App token, each is refused, even
        # with the swapped steps allowlisted.
        swapped = re.sub(r"\$\{\{ steps\.(?:app-token|push-token)\.outputs\.token \}\}", "${{ github.token }}", text)
        problems = policy.run_body_problems(policy.SIGNING_WORKFLOW, swapped, allowlist_for({policy.SIGNING_WORKFLOW: swapped}))
        self.assertEqual(len([problem for problem in problems if "Administration-only" in problem]), len(reads))
        # And unallowlisted, each job holding a swapped step is refused for the general reason.
        problems = policy.run_body_problems(policy.SIGNING_WORKFLOW, swapped)
        for job in ("sign-release-tag", "sign-dry-run-tag"):
            self.assertTrue(any(UNSUPPORTED in p and f"of job {job} " in p for p in problems), problems)

    def test_the_renderer_verifier_runs_isolated_in_every_workflow(self) -> None:
        step = "    steps:\n      - name: prove\n        run: python3 {flags}tools/provenance/verify_renderer_dependency.py\n"
        for name in ("sdk-release.yml", "provenance.yml", "gpu-macos.yml"):
            with self.subTest(workflow=name):
                found = policy.isolation_problems({name: "jobs:\n  check:\n" + step.format(flags="")}, self.tmp)
                self.assertIn(f"{name}: - name: prove runs the renderer verifier without -I", found)
                self.assertEqual(policy.isolation_problems({name: "jobs:\n  check:\n" + step.format(flags="-I ")}, self.tmp), [])
        # Its tests may run unisolated; they never decide eligibility.
        tests = "    steps:\n      - name: t\n        run: python3 tools/provenance/test_verify_renderer_dependency.py\n"
        self.assertEqual(policy.isolation_problems({"provenance.yml": "jobs:\n  check:\n" + tests}, self.tmp), [])

    def test_parser_effective_shell_is_bash_at_every_scope(self) -> None:
        base = ("jobs:\n  sign:\n    steps:\n      - env:\n"
                "          K: ${{ secrets.RELEASE_TAG_SIGNING_KEY }}\n        run: import os\n")
        for shell in ("python", "python -I {0}", "sh", "pwsh", "${{ inputs.shell }}"):
            for location in ("workflow", "job", "step"):
                with self.subTest(shell=shell, location=location):
                    if location == "workflow":
                        text = f"defaults:\n  run:\n    shell: {shell}\n" + base
                    elif location == "job":
                        text = base.replace("    steps:", f"    defaults:\n      run:\n        shell: {shell}\n    steps:")
                    else:
                        text = base.replace("        run:", f"        shell: {shell}\n        run:")
                    self.assertTrue(any("shell must be bash" in p for p in self.found(text)), self.found(text))
                    # The same structure with bash is supported at all scopes.
                    self.assertEqual(self.allowed(text.replace(f"shell: {shell}", "shell: bash").replace("import os", "true")), [])

    def test_parser_quoted_run_keys_fail_before_command_detection(self) -> None:
        for run in ('"run": >-\n          gh\n          api repos/owner/repo/immutable-releases\n',
                    '"run": g$\'h\' api repos/owner/repo/immutable-releases\n',
                    '"run": echo ok\n'):
            with self.subTest(run=run):
                problems = self.found(self.read(run=run))
                self.assertTrue(any("unsupported construct" in p for p in problems), problems)
        self.assertEqual(self.allowed(self.read(run="run: gh api repos/owner/repo/immutable-releases\n")), [])

    def test_parser_rejects_complete_flow_documents(self) -> None:
        text = ("{on: push, jobs: {build: {runs-on: ubuntu-latest, steps: "
                "[{run: python3 tools/provenance/verify_renderer_dependency.py}]}}}\n")
        problems = policy.isolation_problems({"other.yml": text}, self.tmp)
        self.assertTrue(any("unsupported construct" in p for p in problems), problems)
        block = ("on: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n"
                 "      - run: python3 -I tools/provenance/verify_renderer_dependency.py\n")
        self.assertEqual(policy.isolation_problems({"other.yml": block}, self.tmp), [])

    def test_parser_requires_an_unchanged_run_credential(self) -> None:
        command = "gh api repos/owner/repo/immutable-releases"
        for token in ("GH_TOKEN", "GITHUB_TOKEN"):
            for script in (f"{token}=${{{{ github.token }}}} {command}",
                           f"export {token}=${{{{ github.token }}}}\n{command}",
                           f"export {token}\n{command}",
                           f'G"{token[1:]}"=bad {command}',
                           f'{token}[0]=bad; {command}',
                           f'declare -x {token}; {command}',
                           f'typeset -x {token}; {command}',
                           f'read -r {token}; {command}',
                           f'printf -v {token} bad; {command}',
                           f'unset {token}; {command}'):
                with self.subTest(script=script):
                    run = "run: |\n" + "".join("          " + line + "\n" for line in script.splitlines())
                    problems = self.found(self.read(run=run))
                    self.assertTrue(any(UNSUPPORTED in p for p in problems), problems)
        self.assertEqual(self.allowed(self.read(run=f"run: {command}\n")), [])

    def test_token_assignment_is_rejected_in_every_sensitive_job_step(self) -> None:
        # Release workflows only: the run allowlist governs their credential-holding jobs.
        for name in ("release-tag.yml", "version-at-land.yml"):
            for sensitive in ("env:\n      KEY: ${{ secrets.KEY }}\n",
                              "steps:\n      - run: gh api repos/owner/repo/actions/runs\n"):
                for assignment in ('GH_TOKEN=bad', 'export GITHUB_TOKEN',
                                   'env -i GH_TOKEN="$GH_TOKEN" /usr/bin/gh api repos/o/r/actions/runs'):
                    if sensitive.startswith("env:"):
                        body = sensitive + "    steps:\n"
                    else:
                        body = sensitive
                    workflow = "jobs:\n  check:\n    " + body + "      - run: " + assignment + "\n"
                    with self.subTest(name=name, sensitive=sensitive, assignment=assignment):
                        problems = isolation({name: workflow}, self.tmp)
                        self.assertTrue(any(UNSUPPORTED in p for p in problems), problems)
                        control = workflow.replace(assignment, "true")
                        self.assertEqual(isolation({name: control}, self.tmp, allow=True), [])

    def test_parser_missing_dependency_and_malformed_yaml_fail_closed(self) -> None:
        valid = "jobs:\n  check:\n    steps:\n      - run: true\n"
        with mock.patch.object(policy, "yaml", None):
            self.assertTrue(any("PyYAML is required" in p for p in self.found(valid)))
        for text in ("jobs: [", valid + "jobs: {}\n", valid.replace("run: true", "run: true\n        run: false"),
                     valid.replace("run: true", "run: !!python/object:builtins.str true")):
            with self.subTest(text=text):
                self.assertTrue(any("unsupported construct" in p for p in self.found(text)), self.found(text))
        process = subprocess.run([sys.executable, "-I", "-S", str(ROOT / "scripts/check_release_policy.py"),
                                  "check"], text=True, capture_output=True, check=False)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("PyYAML is required", process.stderr)
        self.assertEqual(self.allowed(valid), [])

    def test_a_package_or_compiled_module_shadowing_the_standard_library_fails(self) -> None:
        (self.tmp / "tools" / "provenance").mkdir(parents=True)
        package = self.tmp / "tools" / "provenance" / "subprocess"
        package.mkdir()
        self.assertEqual(self.allowed(""), [])  # a plain directory imports nothing ahead of the stdlib
        (package / "__init__.py").write_text("")
        self.assertTrue(any("subprocess" in p for p in self.found("")))
        shutil.rmtree(package)
        for name in ("json.cpython-312.pyc", "zipfile.so", "shutil.pyd"):
            with self.subTest(name=name):
                (self.tmp / "scripts" / name).write_bytes(b"")
                self.assertTrue(any(name.split(".")[0] in p for p in self.found("")))
                (self.tmp / "scripts" / name).unlink()

    def test_a_file_shadowing_the_standard_library_fails(self) -> None:
        (self.tmp / "scripts" / "json.py").write_text("")
        self.assertTrue(any("json" in p for p in self.found("")))
        (self.tmp / "scripts" / "json.py").unlink()
        (self.tmp / "re.py").write_text("")
        self.assertTrue(any("`re`" in p for p in self.found("")))


class RunAllowlist(unittest.TestCase):
    """Credential-holding jobs of the real release workflows run only reviewed steps."""

    def setUp(self) -> None:
        self.workflows = {name: (ROOT / policy.WORKFLOWS / name).read_text(encoding="utf-8")
                          for name in policy.RELEASE_WORKFLOWS}

    def judged(self, name: str, old: str, new: str) -> list[str]:
        text = self.workflows[name]
        self.assertEqual(text.count(old), 1, old)
        return policy.run_body_problems(name, text.replace(old, new))

    def assert_unsupported(self, problems: list[str]) -> None:
        self.assertTrue(any(UNSUPPORTED in problem for problem in problems), problems)

    def test_the_real_release_workflows_are_allowlisted(self) -> None:
        for name, text in self.workflows.items():
            with self.subTest(workflow=name):
                self.assertEqual(policy.run_body_problems(name, text), [])
        # The rule has something to judge: every job that holds a credential.
        covered = sorted(key for name, text in self.workflows.items()
                         for key in allowlist_for({name: text}))
        self.assertEqual(covered, sorted([
            ("release-tag.yml", "select-release-commit"), ("release-tag.yml", "sign-release-tag"),
            ("release-tag.yml", "dry-run-gate"), ("release-tag.yml", "sign-dry-run-tag"),
            ("version-at-land.yml", "plan"), ("version-at-land.yml", "push")]))
        self.assertEqual(sorted(policy.RUN_ALLOWLIST), covered)
        self.assertNotIn("${{", "".join(step.get("run", "") for variants in policy.RUN_ALLOWLIST.values()
                                        for steps in variants for step in steps))

    def test_a_job_runs_one_of_its_listed_variants(self) -> None:
        # A step changes in two pull requests: the first lists the new sequence
        # as a second variant, the second changes the workflow.
        name = policy.SIGNING_WORKFLOW
        text = self.workflows[name]
        old = "          echo \"signed, pushed and froze $TAG at $OBJECT\" >> \"$GITHUB_STEP_SUMMARY\""
        changed = text.replace(old, old.replace("signed, pushed", "signed, then pushed"))
        self.assert_unsupported(policy.run_body_problems(name, changed))
        new_variant = allowlist_for({name: changed})[(name, "sign-release-tag")][0]
        allowlist = {**policy.RUN_ALLOWLIST,
                     (name, "sign-release-tag"): [*policy.RUN_ALLOWLIST[(name, "sign-release-tag")], new_variant]}
        # Pull request one: both sequences listed, the workflow unchanged; pull request two: the new one runs.
        self.assertEqual(policy.run_body_problems(name, text, allowlist), [])
        self.assertEqual(policy.run_body_problems(name, changed, allowlist), [])
        # A sequence that matches no variant still fails, and names the step that differs.
        neither = text.replace(old, old.replace("signed, pushed", "pushed"))
        problems = policy.run_body_problems(name, neither, allowlist)
        self.assertTrue(any(UNSUPPORTED in p and "Push the tag and freeze it" in p for p in problems), problems)
        # A job with no listed variant fails too.
        missing = {key: value for key, value in policy.RUN_ALLOWLIST.items() if key != (name, "sign-release-tag")}
        self.assert_unsupported(policy.run_body_problems(name, text, missing))

    def test_locale_quoted_gh_is_not_in_the_allowlist(self) -> None:
        # The HOLD at 160897c17: `g$"h" api` and `gh a$"pi"` run gh api, and
        # an Actions-token step could read an Administration-only endpoint.
        wait = '          . "$RUNNER_TEMP/fetch_results.sh"\n          deadline='
        for command in ('g$"h" api', 'gh a$"pi"', "g$'h' api", '"g"h api'):
            with self.subTest(command=command):
                self.assert_unsupported(self.judged(policy.SIGNING_WORKFLOW, wait, (
                    f'          {command} "repos/$GITHUB_REPOSITORY/immutable-releases" > x.json\n' + wait)))
        # The positive control: the same step as reviewed.
        self.assertEqual(self.judged(policy.SIGNING_WORKFLOW, wait, wait), [])

    def test_removing_the_app_token_with_env_is_not_in_the_allowlist(self) -> None:
        # The HOLD at 160897c17: `env --unset=GH_TOKEN gh api` falls back to
        # another credential while the step's env still shows the App token.
        read = ('          timeout 120 gh api "repos/$GITHUB_REPOSITORY/rules/branches/main"'
                ' > "$RUNNER_TEMP/branch-rules.json"\n          timeout 120 gh api "repos/$GITHUB_REPOSITORY/immutable-releases"')
        text = self.workflows[policy.SIGNING_WORKFLOW]
        self.assertEqual(text.count(read), 2)
        for wrapper in ("env --unset=GH_TOKEN", "env -u GH_TOKEN", "env -i", "GH_TOKEN= "):
            with self.subTest(wrapper=wrapper):
                changed = text.replace(read, read.replace("timeout 120 gh api", f"timeout 120 {wrapper} gh api"), 1)
                self.assert_unsupported(policy.run_body_problems(policy.SIGNING_WORKFLOW, changed))

    def test_inherited_bash_functions_cannot_reach_the_bump_push(self) -> None:
        # The HOLD at 160897c17: BASH_FUNC_read%% inherited into the push step
        # empties clean()'s inventory. No step of the job can set it: a new env
        # entry, or an earlier step writing GITHUB_ENV, is not reviewed.
        push = "          MAIN: ${{ needs.plan.outputs.main }}\n          VERSION:"
        self.assert_unsupported(self.judged(policy.VERSION_WORKFLOW, push, (
            "          MAIN: ${{ needs.plan.outputs.main }}\n"
            "          BASH_FUNC_read%%: '() { record=; return 0; }'\n          VERSION:")))
        apply = '          test "$(git rev-parse HEAD^)" = "$MAIN"\n'
        self.assert_unsupported(self.judged(policy.VERSION_WORKFLOW, apply, apply + (
            "          printf 'BASH_FUNC_read%%%%=() { record=; return 0; }\\n' >> \"$GITHUB_ENV\"\n")))
        for key in ("BASH_ENV", "ENV"):
            with self.subTest(key=key):
                self.assert_unsupported(self.judged(policy.VERSION_WORKFLOW, push, (
                    f"          MAIN: ${{{{ needs.plan.outputs.main }}}}\n          {key}: /tmp/x\n          VERSION:")))

    def test_a_reviewed_step_cannot_change_any_key(self) -> None:
        # Every key but `name` is reviewed: the token, the checkout, a working
        # directory or `continue-on-error` change what the token-holding step does.
        sign = self.workflows[policy.SIGNING_WORKFLOW]
        for old, new in {
            "GH_TOKEN: ${{ steps.app-token.outputs.token }}": "GH_TOKEN: ${{ steps.push-token.outputs.token }}",
            "      - name: Read main's required checks again\n":
                "      - name: Read main's required checks again\n        continue-on-error: true\n",
            "      - name: Decide whether the version may be tagged\n":
                "      - name: Decide whether the version may be tagged\n        working-directory: .observatory-pulp\n",
        }.items():
            with self.subTest(new=new):
                self.assert_unsupported(policy.run_body_problems(policy.SIGNING_WORKFLOW, sign.replace(old, new, 1)))
        # Reviewed steps run in the reviewed order: verifying after the push is refused.
        verify = sign[sign.index("      - name: Verify the tag against the policy\n"):sign.index("      - name: Push the tag and freeze it\n")]
        push = sign[sign.index("      - name: Push the tag and freeze it\n"):sign.index("  # A release dry run.")]
        self.assert_unsupported(policy.run_body_problems(policy.SIGNING_WORKFLOW, sign.replace(verify + push, push + verify)))
        # A step's name is not reviewed: renaming one changes nothing that runs.
        renamed = sign.replace("- name: Sign the tag\n", "- name: Sign the release tag\n")
        self.assertEqual(policy.run_body_problems(policy.SIGNING_WORKFLOW, renamed), [])

    def test_a_reviewed_job_runs_its_steps_in_bash(self) -> None:
        # A default or step shell other than bash turns a reviewed body into
        # another language beside the token or the key, so it is refused even
        # when the changed steps are allowlisted. Only a default working
        # directory is refused among the other defaults; shell: bash stays.
        sign = self.workflows[policy.SIGNING_WORKFLOW]
        job = "    environment: release\n    steps:\n      - name: Check out the reconciled"
        step = "      - name: Sign the tag\n        id: sign\n"
        for shell in ("python", "python -I {0}", "sh", "pwsh", "bash {0}", "${{ inputs.shell }}"):
            for label, old, new in (
                    ("workflow", "permissions:\n  contents: read\n  checks: read",
                     f"defaults:\n  run:\n    shell: {shell}\npermissions:\n  contents: read\n  checks: read"),
                    ("job", job, job.replace("    steps:", f"    defaults:\n      run:\n        shell: {shell}\n    steps:")),
                    ("step", step, step + f"        shell: {shell}\n")):
                with self.subTest(shell=shell, location=label):
                    text = sign.replace(old, new, 1)
                    self.assertNotEqual(text, sign)
                    problems = policy.run_body_problems(policy.SIGNING_WORKFLOW, text,
                                                        allowlist_for({policy.SIGNING_WORKFLOW: text}))
                    self.assertTrue(any("shell must be bash" in p for p in problems), problems)
        for label, old, new in (("workflow", "permissions:\n  contents: read\n  checks: read",
                                 "defaults:\n  run:\n    shell: bash\npermissions:\n  contents: read\n  checks: read"),
                                ("job", job, job.replace("    steps:", "    defaults:\n      run:\n        shell: bash\n    steps:"))):
            with self.subTest(bash=label):
                text = sign.replace(old, new, 1)
                self.assertEqual(policy.run_body_problems(policy.SIGNING_WORKFLOW, text,
                                                          allowlist_for({policy.SIGNING_WORKFLOW: text})), [])

    def test_a_credential_job_runs_only_pinned_actions_in_place(self) -> None:
        for old, new in {
            "uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1":
                "uses: actions/checkout@v4",
            "    environment: release\n    steps:": "    environment: release\n    container: ubuntu:latest\n    steps:",
            "    environment: release\n    steps:\n      - name: Check out the reconciled":
                "    environment: release\n    defaults:\n      run:\n        working-directory: .observatory-pulp\n"
                "    steps:\n      - name: Check out the reconciled",
            "permissions:\n  contents: read\n  checks: read":
                "defaults:\n  run:\n    working-directory: .exact-boundary-pulp\npermissions:\n  contents: read\n  checks: read",
        }.items():
            with self.subTest(new=new):
                text = self.workflows[policy.SIGNING_WORKFLOW].replace(old, new, 1)
                problems = policy.run_body_problems(policy.SIGNING_WORKFLOW, text, allowlist_for(
                    {policy.SIGNING_WORKFLOW: text}))
                self.assertTrue(problems, "allowlisted, still refused")
        local = self.workflows[policy.SIGNING_WORKFLOW].replace(
            "uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1", "uses: ./.github/actions/local", 1)
        problems = policy.run_body_problems(policy.SIGNING_WORKFLOW, local, allowlist_for({policy.SIGNING_WORKFLOW: local}))
        self.assertTrue(any("not an action pinned" in p for p in problems), problems)

    def test_gh_token_is_the_actions_token_or_a_minted_one(self) -> None:
        # Outside the Administration reads, GH_TOKEN is still one of the two
        # credentials the review expects, even in an allowlisted step.
        old = "          GH_TOKEN: ${{ github.token }}\n          TAG: ${{ steps.select.outputs.tag }}"
        for value in ("${{ secrets.SHIPYARD_APP_PRIVATE_KEY }}", "${{ steps.select.outputs.tag }}", "ghp_literal",
                      "${{ steps.later.outputs.token }}"):
            with self.subTest(value=value):
                text = self.workflows[policy.SIGNING_WORKFLOW].replace(old, old.replace("${{ github.token }}", value), 1)
                self.assertNotEqual(text, self.workflows[policy.SIGNING_WORKFLOW])
                problems = policy.run_body_problems(policy.SIGNING_WORKFLOW, text,
                                                    allowlist_for({policy.SIGNING_WORKFLOW: text}))
                self.assertTrue(any("neither the Actions token" in p for p in problems), problems)

    def test_python_spelled_any_way_beside_the_signing_key_is_not_reviewed(self) -> None:
        # The HOLD at 8488ac5be: `p'y'thon3` runs python3, but no spelling-based
        # Python rule sees it. A job holding only the signing key is reviewed
        # text too, so the spelling does not matter.
        key = ("jobs:\n  sign:\n    steps:\n      - name: sign\n        env:\n"
               "          K: ${{ secrets.RELEASE_TAG_SIGNING_KEY }}\n        run: {run}\n")
        for run in ("p'y'thon3 scripts/x.py", "py\\thon3 scripts/x.py", 'env python3 scripts/x.py',
                    "python$'3' scripts/x.py", 'p$"y"thon3 -I scripts/x.py'):
            with self.subTest(run=run):
                text = key.replace("{run}", run)
                self.assertTrue(any(UNSUPPORTED in p for p in policy.run_body_problems(policy.SIGNING_WORKFLOW, text)),
                                policy.run_body_problems(policy.SIGNING_WORKFLOW, text))
        # Controls: the reviewed signing step passes; with no secret the job is not listed at all.
        clean = key.replace("{run}", "git tag -s v1")
        self.assertEqual(policy.run_body_problems(policy.SIGNING_WORKFLOW, clean,
                                                  allowlist_for({policy.SIGNING_WORKFLOW: clean})), [])
        plain = key.replace("          K: ${{ secrets.RELEASE_TAG_SIGNING_KEY }}\n", "          K: x\n").replace(
            "{run}", "p'y'thon3 scripts/x.py")
        self.assertEqual(policy.run_body_problems(policy.SIGNING_WORKFLOW, plain), [])

    def test_the_signing_key_never_shares_a_step_with_a_github_credential(self) -> None:
        key = "          RELEASE_TAG_SIGNING_KEY: ${{ secrets.RELEASE_TAG_SIGNING_KEY }}\n          TAG: ${{ steps.decision"
        for credential in ("GH_TOKEN: ${{ github.token }}", "GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}",
                           "OTHER: ${{ steps.push-token.outputs.token }}"):
            with self.subTest(credential=credential):
                text = self.workflows[policy.SIGNING_WORKFLOW]
                text = text.replace(key, key.replace("          TAG:", f"          {credential}\n          TAG:"), 1)
                problems = policy.run_body_problems(policy.SIGNING_WORKFLOW, text, allowlist_for(
                    {policy.SIGNING_WORKFLOW: text}))
                self.assertTrue(any("signing key with a GitHub credential" in p for p in problems), problems)

    def test_a_job_reaches_a_credential_however_it_names_one(self) -> None:
        for text in ("${{ github.token }}", "${{ GitHub.Token }}", "${{ github['token'] }}", "${{ toJSON(github) }}",
                     "${{ secrets.GITHUB_TOKEN }}", "${{ secrets[format('{0}', 'X')] }}", "${{ steps.x.outputs.token }}",
                     "${{ steps.x.outputs[format('{0}', 'token')] }}", "${{ join(steps.*.outputs) }}", "GH_TOKEN",
                     "github_token", "uses: actions/create-github-app-token@v2"):
            with self.subTest(text=text):
                self.assertTrue(policy.reaches_credential(text))
        for text in ("${{ secrets.RELEASE_TAG_SIGNING_KEY }}", "${{ github.sha }}", "${{ steps.x.outputs.tag }}",
                     "${{ needs.plan.outputs.main }}", "${{ runner.temp }}", "echo hi"):
            with self.subTest(text=text):
                self.assertFalse(policy.reaches_credential(text))


class AdminReadRuntime(unittest.TestCase):
    """The admin reads fail closed at run time on an answer of the wrong shape."""

    GOOD = {"required_status_checks": '{"contexts": [], "checks": []}', "rules/branches/main": "[]",
            "immutable-releases": '{"enabled": true, "enforced_by_owner": false}'}

    def run_read(self, answers: dict[str, str], status: int = 0) -> subprocess.CompletedProcess:
        if shutil.which("jq") is None or shutil.which("bash") is None:
            self.fail("bash and jq are required, as on the runner")
        text = (ROOT / policy.WORKFLOWS / policy.SIGNING_WORKFLOW).read_text(encoding="utf-8")
        body = next(step.run for step in policy.workflow_steps(text)
                    if step.name == "Read main's required checks and the immutable-release setting")
        tmp = Path(tempfile.mkdtemp(prefix="admin-read-"))
        self.addCleanup(shutil.rmtree, tmp)
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        (bin_dir / "timeout").write_text('#!/bin/sh\nshift\nexec "$@"\n')
        cases = "".join(f'  *{endpoint}) printf %s {shlex_quote(answer)} ;;\n' for endpoint, answer in answers.items())
        (bin_dir / "gh").write_text(f'#!/bin/sh\ncase "$2" in\n{cases}esac\nexit {status}\n')
        for tool in ("timeout", "gh"):
            (bin_dir / tool).chmod(0o755)
        env = {"PATH": f"{bin_dir}:{Path(shutil.which('jq')).parent}:/usr/bin:/bin",
               "RUNNER_TEMP": str(tmp), "GITHUB_REPOSITORY": "Generous-Corp/vellum"}
        return subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", body], env=env,
                              capture_output=True, text=True, check=False)

    def test_well_formed_answers_pass(self) -> None:
        self.assertEqual(self.run_read(self.GOOD).returncode, 0)

    def test_an_answer_of_the_wrong_shape_fails(self) -> None:
        for endpoint, answer in {"required_status_checks": '{"message": "Not Found"}',
                                 "rules/branches/main": '{"message": "Resource not accessible by integration"}',
                                 "immutable-releases": '{"message": "Must have admin rights"}'}.items():
            with self.subTest(endpoint=endpoint):
                self.assertNotEqual(self.run_read({**self.GOOD, endpoint: answer}).returncode, 0)

    def test_a_failed_request_fails(self) -> None:
        self.assertNotEqual(self.run_read(self.GOOD, status=1).returncode, 0)


def shlex_quote(text: str) -> str:
    return "'" + text.replace("'", "'\"'\"'") + "'"


class Repository(unittest.TestCase):
    def test_the_repository_holds_its_policy(self) -> None:
        self.assertEqual(policy.problems(ROOT), [])

    def test_the_committed_immutable_release_preflight_is_retired(self) -> None:
        # Immutable releases are read live by the release jobs; a committed
        # record would be a second, stale answer, and owning it would be moot.
        self.assertFalse((ROOT / "provenance/immutable-release-preflight.json").exists())
        self.assertNotIn("/provenance/immutable-release-preflight.json", policy.POLICY_FILES)
        self.assertNotIn("immutable-release-preflight.json", (ROOT / policy.CODEOWNERS).read_text())

    def test_the_release_request_file_is_retired(self) -> None:
        # The project VERSION is the release; a second file naming it would be
        # a second answer, and owning it would be moot.
        self.assertFalse((ROOT / "provenance/release-request.json").exists())
        self.assertNotIn("/provenance/release-request.json", policy.POLICY_FILES)
        self.assertNotIn("release-request.json", (ROOT / policy.CODEOWNERS).read_text())

    def test_the_release_decision_inputs_are_policy_files(self) -> None:
        # Dropping one from both lists at once needs an owner-reviewed change
        # to check_release_policy.py; this names the omission in that review.
        for path in ("/scripts/verify_immutable_release_preflight.py",
                     "/provenance/retired-release-tags.json",
                     "/.github/workflows/version-at-land.yml",
                     "/scripts/version_at_land.py",
                     "/provenance/authority-signers.allowed_signers",
                     "/provenance/mirror/link-policy.json",
                     "/tools/provenance/verify_renderer_dependency.py",
                     "/provenance/active-source-boundary.json",
                     "/scripts/install_core.py",
                     "/tools/provenance/live_delivery.py",
                     "/tools/provenance/observatory.py",
                     "/tools/provenance/observatory_common.py",
                     "/tools/provenance/observatory_events.py",
                     "/tools/provenance/observatory_git.py",
                     "/tools/provenance/observatory_native.py",
                     "/tools/provenance/observatory_report.py",
                     "/tools/provenance/receiver.py",
                     "/tools/provenance/verify_pulp_observation_scope.py"):
            with self.subTest(path=path):
                self.assertIn(path, policy.POLICY_FILES)


if __name__ == "__main__":
    unittest.main()
