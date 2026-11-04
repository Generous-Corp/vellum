#!/usr/bin/env python3
"""check_release_policy.py: one key, one signing workflow, owner-owned policy files."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_release_policy as policy  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


class Policy(unittest.TestCase):
    SIGNING = ("jobs:\n  sign:\n    environment: release\n    steps:\n      - name: v\n        run: |\n"
               "          python3 -I scripts/verify_release_tag.py \\\n"
               "            --allowed-signers provenance/release-signers.allowed_signers \\\n"
               "            --expected-principal \"$PRINCIPAL\" --expected-fingerprint \"$FINGERPRINT\"\n")

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="release-policy-"))
        (self.tmp / ".github/workflows").mkdir(parents=True)
        (self.tmp / "provenance").mkdir()
        self.release = self.make_signer("release@ci", policy.ALLOWED_SIGNERS)
        self.authority = self.make_signer("owner@example", policy.SIGNER_FILES["authority-signers.allowed_signers"])
        self.fingerprint = self.release[1]
        shutil.copy(ROOT / policy.CODEOWNERS, self.tmp / policy.CODEOWNERS)
        self.workflow("release-tag.yml", self.SIGNING)
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
        # A dropped retired tag, a weakened immutable-release verifier, or a
        # version-at-land workflow that does more with its App token must not
        # land without the owner's review.
        for path in ("/scripts/verify_immutable_release_preflight.py",
                     "/provenance/retired-release-tags.json",
                     "/.github/workflows/version-at-land.yml",
                     "/scripts/version_at_land.py",
                     "/provenance/authority-signers.allowed_signers",
                     "/tools/provenance/verify_renderer_dependency.py"):
            with self.subTest(path=path):
                shutil.copy(ROOT / policy.CODEOWNERS, self.tmp / policy.CODEOWNERS)
                owners = self.tmp / policy.CODEOWNERS
                owners.write_text("\n".join(l for l in owners.read_text().splitlines()
                                            if not l.startswith(path + " ")))
                self.assertIn(f"{policy.CODEOWNERS} does not give {path} to {policy.OWNER}",
                              policy.problems(self.tmp))

    def test_release_decision_inputs_missing_from_policy_files_fail(self) -> None:
        for path in ("/scripts/verify_immutable_release_preflight.py",
                     "/provenance/retired-release-tags.json",
                     "/.github/workflows/version-at-land.yml",
                     "/scripts/version_at_land.py",
                     "/provenance/authority-signers.allowed_signers",
                     "/tools/provenance/verify_renderer_dependency.py"):
            with self.subTest(path=path):
                self.assertIn(path, policy.POLICY_FILES)
                kept = tuple(p for p in policy.POLICY_FILES if p != path)
                original, policy.POLICY_FILES = policy.POLICY_FILES, kept
                try:
                    self.assertIn(f"{policy.CODEOWNERS} owns {path}, which POLICY_FILES does not name",
                                  policy.problems(self.tmp))
                finally:
                    policy.POLICY_FILES = original

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

    def found(self, signing: str) -> list[str]:
        return policy.isolation_problems({policy.SIGNING_WORKFLOW: signing}, self.tmp)

    def test_python_without_isolation_fails(self) -> None:
        self.assertEqual(self.found("    steps:\n      - name: a\n        run: python3 -I scripts/x.py\n"), [])
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
        self.assertEqual(self.found("    steps:\n" + clean), [])
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
        self.assertEqual(self.found("    steps:\n" + output), [])

    def test_version_at_land_runs_no_python_where_it_holds_a_token(self) -> None:
        # Must-pass: the App token is minted and used in steps that run no
        # Python. Must-fail: repository Python in a token-holding step, or
        # Python without -I anywhere in the workflow.
        def found(text: str) -> list[str]:
            return policy.isolation_problems({policy.VERSION_WORKFLOW: text}, self.tmp)

        mint = ("      - name: mint\n        uses: actions/create-github-app-token@x\n        with:\n"
                "          private-key: ${{ secrets.SHIPYARD_APP_PRIVATE_KEY }}\n")
        push = ("      - name: push\n        env:\n          GH_TOKEN: ${{ steps.bump-token.outputs.token }}\n"
                "        run: git push origin HEAD\n")
        plan = "      - name: plan\n        run: python3 -I scripts/version_at_land.py plan\n"
        def workflow(plan_steps: str, push_steps: str) -> str:
            return "jobs:\n  plan:\n    steps:\n" + plan_steps + "  push:\n    steps:\n" + push_steps

        self.assertEqual(found(workflow(plan, mint + push)), [])
        held = push.replace("git push origin HEAD", "python3 -I scripts/version_at_land.py apply")
        self.assertTrue(any("holds a secret and runs Python" in p for p in found(workflow(plan, mint + held))))
        keyed = mint.replace("        with:", "        run: python3 -I x.py\n        with:")
        self.assertTrue(any("holds a secret and runs Python" in p for p in found(workflow(plan, keyed + push))))
        loose = plan.replace("python3 -I", "python3")
        self.assertTrue(any("without -I" in p for p in found(workflow(loose, mint + push))))
        self.assertIn("version-at-land.yml: no jobs could be read", found("    steps:\n" + plan))

    def test_a_token_job_runs_no_repository_python_in_any_step(self) -> None:
        # Must-pass: Python in a job that holds no token. Must-fail: the same
        # Python in an earlier step of the job that later mints the token,
        # which could plant a hook or a GITHUB_PATH entry the push then runs.
        def found(text: str) -> list[str]:
            return policy.isolation_problems({policy.VERSION_WORKFLOW: text}, self.tmp)

        plan = "  plan:\n    steps:\n      - name: plan\n        run: python3 -I scripts/version_at_land.py plan\n"
        push = ("  push:\n    steps:\n      - name: mint\n        uses: actions/create-github-app-token@x\n"
                "        with:\n          private-key: ${{ secrets.SHIPYARD_APP_PRIVATE_KEY }}\n")
        self.assertEqual(found("jobs:\n" + plan + push), [])
        merged = push.replace("    steps:\n", "    steps:\n      - name: plan\n"
                              "        run: python3 -I scripts/version_at_land.py apply\n")
        self.assertTrue(any("job push holds a secret and runs Python" in p for p in found("jobs:\n" + plan + merged)))

    def test_the_committed_version_at_land_holds_the_rule(self) -> None:
        text = (ROOT / policy.WORKFLOWS / policy.VERSION_WORKFLOW).read_text()
        self.assertEqual(policy.isolation_problems({policy.VERSION_WORKFLOW: text}, self.tmp), [])
        self.assertNotIn(policy.RELEASE_ENVIRONMENT, policy.environment_names(text))

    def test_admin_only_reads_need_the_app_token(self) -> None:
        for endpoint in ("rules/branches/main", "branches/main/protection/required_status_checks",
                         "immutable-releases"):
            with self.subTest(endpoint=endpoint):
                actions = ("      - name: read\n        env:\n          GH_TOKEN: ${{ github.token }}\n"
                           f"        run: gh api \"repos/$R/{endpoint}\" > out.json\n")
                app = ("      - name: read\n        env:\n          GH_TOKEN: ${{ steps.app-token.outputs.token }}\n"
                       f"        run: gh api \"repos/$R/{endpoint}\" > out.json\n")
                self.assertTrue(any("Administration-only" in p for p in self.found("    steps:\n" + actions)))
                self.assertEqual(self.found("    steps:\n" + app), [])
                self.assertEqual(self.found("    steps:\n" + app.replace("app-token", "push-token")), [])
                self.assertTrue(any("Administration-only" in p for p in self.found(
                    "    steps:\n" + app.replace("app-token", "other-token"))))

    def test_the_renderer_verifier_runs_isolated_in_every_workflow(self) -> None:
        step = "    steps:\n      - name: prove\n        run: python3 {flags}tools/provenance/verify_renderer_dependency.py\n"
        for name in ("sdk-release.yml", "provenance.yml", "gpu-macos.yml"):
            with self.subTest(workflow=name):
                found = policy.isolation_problems({name: step.format(flags="")}, self.tmp)
                self.assertIn(f"{name}: - name: prove runs the renderer verifier without -I", found)
                self.assertEqual(policy.isolation_problems({name: step.format(flags="-I ")}, self.tmp), [])
        # Its tests may run unisolated; they never decide eligibility.
        tests = "    steps:\n      - name: t\n        run: python3 tools/provenance/test_verify_renderer_dependency.py\n"
        self.assertEqual(policy.isolation_problems({"provenance.yml": tests}, self.tmp), [])

    def test_a_package_or_compiled_module_shadowing_the_standard_library_fails(self) -> None:
        (self.tmp / "tools" / "provenance").mkdir(parents=True)
        package = self.tmp / "tools" / "provenance" / "subprocess"
        package.mkdir()
        self.assertEqual(self.found(""), [])  # a plain directory imports nothing ahead of the stdlib
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
                     "/tools/provenance/verify_renderer_dependency.py"):
            with self.subTest(path=path):
                self.assertIn(path, policy.POLICY_FILES)


if __name__ == "__main__":
    unittest.main()
