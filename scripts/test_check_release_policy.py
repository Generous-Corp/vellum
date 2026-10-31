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
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="release-policy-"))
        (self.tmp / ".github/workflows").mkdir(parents=True)
        (self.tmp / "provenance").mkdir()
        key = self.tmp / "release"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
        pub = key.with_suffix(".pub").read_text().split()
        (self.tmp / policy.ALLOWED_SIGNERS).write_text(f"release@ci {pub[0]} {pub[1]}\n")
        self.fingerprint = subprocess.run(["ssh-keygen", "-lf", f"{key}.pub"], capture_output=True, text=True,
                                          check=True).stdout.split()[1]
        shutil.copy(ROOT / policy.CODEOWNERS, self.tmp / policy.CODEOWNERS)
        self.workflow("release-tag.yml", "jobs:\n  sign:\n    environment: release\n")
        self.workflow("verify.yml", "run: |\n  verify \\\n    --expected-principal release@ci \\\n"
                                    f"    --expected-fingerprint \\\n      {self.fingerprint} \\\n")

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

    def test_a_stale_fingerprint_pin_fails(self) -> None:
        self.workflow("verify.yml", "verify --expected-fingerprint SHA256:old-key\n")
        self.assertTrue(any("SHA256:old-key" in p for p in policy.problems(self.tmp)))

    def test_a_stale_principal_pin_fails(self) -> None:
        self.workflow("verify.yml", "verify --expected-principal someone@else\n")
        self.assertTrue(any("someone@else" in p for p in policy.problems(self.tmp)))

    def test_a_second_key_fails(self) -> None:
        signers = self.tmp / policy.ALLOWED_SIGNERS
        signers.write_text(signers.read_text() * 2)
        with self.assertRaisesRegex(policy.PolicyError, "exactly one release key"):
            policy.problems(self.tmp)

    def test_a_policy_file_missing_from_codeowners_fails(self) -> None:
        owners = self.tmp / policy.CODEOWNERS
        owners.write_text("\n".join(l for l in owners.read_text().splitlines() if "verify_release_tag" not in l))
        self.assertTrue(any("verify_release_tag.py" in p for p in policy.problems(self.tmp)))

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

    def test_a_file_shadowing_the_standard_library_fails(self) -> None:
        (self.tmp / "scripts" / "json.py").write_text("")
        self.assertTrue(any("json" in p for p in self.found("")))
        (self.tmp / "scripts" / "json.py").unlink()
        (self.tmp / "re.py").write_text("")
        self.assertTrue(any("`re`" in p for p in self.found("")))


class Repository(unittest.TestCase):
    def test_the_repository_holds_its_policy(self) -> None:
        self.assertEqual(policy.problems(ROOT), [])


if __name__ == "__main__":
    unittest.main()
