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
        # A forged preflight, a dropped retired tag, or a weakened preflight
        # verifier must not land without the owner's review.
        for path in ("/scripts/verify_immutable_release_preflight.py",
                     "/provenance/immutable-release-preflight.json",
                     "/provenance/retired-release-tags.json",
                     "/provenance/release-request.json",
                     "/provenance/authority-signers.allowed_signers",
                     "/provenance/renderer-signers.allowed_signers"):
            with self.subTest(path=path):
                shutil.copy(ROOT / policy.CODEOWNERS, self.tmp / policy.CODEOWNERS)
                owners = self.tmp / policy.CODEOWNERS
                owners.write_text("\n".join(l for l in owners.read_text().splitlines()
                                            if not l.startswith(path + " ")))
                self.assertIn(f"{policy.CODEOWNERS} does not give {path} to {policy.OWNER}",
                              policy.problems(self.tmp))

    def test_release_decision_inputs_missing_from_policy_files_fail(self) -> None:
        for path in ("/scripts/verify_immutable_release_preflight.py",
                     "/provenance/immutable-release-preflight.json",
                     "/provenance/retired-release-tags.json",
                     "/provenance/release-request.json",
                     "/provenance/authority-signers.allowed_signers",
                     "/provenance/renderer-signers.allowed_signers"):
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

    def test_admin_only_reads_need_the_app_token(self) -> None:
        for endpoint in ("rules/branches/main", "branches/main/protection/required_status_checks"):
            with self.subTest(endpoint=endpoint):
                actions = ("      - name: read\n        env:\n          GH_TOKEN: ${{ github.token }}\n"
                           f"        run: gh api \"repos/$R/{endpoint}\" > out.json\n")
                app = ("      - name: read\n        env:\n          GH_TOKEN: ${{ steps.app-token.outputs.token }}\n"
                       f"        run: gh api \"repos/$R/{endpoint}\" > out.json\n")
                self.assertTrue(any("Administration-only" in p for p in self.found("    steps:\n" + actions)))
                self.assertEqual(self.found("    steps:\n" + app), [])

    def test_a_file_shadowing_the_standard_library_fails(self) -> None:
        (self.tmp / "scripts" / "json.py").write_text("")
        self.assertTrue(any("json" in p for p in self.found("")))
        (self.tmp / "scripts" / "json.py").unlink()
        (self.tmp / "re.py").write_text("")
        self.assertTrue(any("`re`" in p for p in self.found("")))


class Repository(unittest.TestCase):
    def test_the_repository_holds_its_policy(self) -> None:
        self.assertEqual(policy.problems(ROOT), [])

    def test_the_release_decision_inputs_are_policy_files(self) -> None:
        # Dropping one from both lists at once needs an owner-reviewed change
        # to check_release_policy.py; this names the omission in that review.
        for path in ("/scripts/verify_immutable_release_preflight.py",
                     "/provenance/immutable-release-preflight.json",
                     "/provenance/retired-release-tags.json",
                     "/provenance/release-request.json",
                     "/provenance/authority-signers.allowed_signers",
                     "/provenance/renderer-signers.allowed_signers"):
            with self.subTest(path=path):
                self.assertIn(path, policy.POLICY_FILES)


if __name__ == "__main__":
    unittest.main()
