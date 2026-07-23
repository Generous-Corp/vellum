from __future__ import annotations

import copy
import json
from pathlib import Path
import runpy
import shutil
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]
VERIFIER = REPO / "tools/agent_instructions/verify.py"
MODULE = runpy.run_path(str(VERIFIER))
verify = MODULE["verify"]
VerificationError = MODULE["VerificationError"]
MANIFEST = Path(".agents/skills/vellum-app-authoring/manifest.v1.json")
SKILL = Path(".agents/skills/vellum-app-authoring/SKILL.md")


class AgentInstructionVerificationTests(unittest.TestCase):
    def fixture(self, root: Path) -> None:
        for relative in (
            "cli/vellum_cli.py",
            "cli/vellum_manifest.py",
            "scripts/install.sh",
            "product/source-support.yaml",
            str(MANIFEST),
            str(SKILL),
        ):
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / relative, destination)

    def test_current_contract_matches_real_cli_and_source_policy(self) -> None:
        evidence = verify(REPO)
        self.assertTrue(evidence["ok"])
        self.assertEqual(evidence["instruction_schema"], "vellum.agent-instructions.v1")
        self.assertEqual(evidence["supported_source_types"], ["design-ir", "figma"])

    def test_rejects_unknown_cli_command(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            manifest = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
            manifest["lifecycle"][-1]["command"] = "deploy"
            (root / MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(VerificationError, "unknown CLI command"):
                verify(root)

    def test_rejects_unknown_flag_named_only_in_skill(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            skill = (root / SKILL).read_text(encoding="utf-8")
            skill = skill.replace(
                "vellum --json build --target macos --project \"$app\"",
                "vellum --json build --target macos --project \"$app\" --pretend-ready",
            )
            (root / SKILL).write_text(skill, encoding="utf-8")
            with self.assertRaisesRegex(VerificationError, "unknown build flags"):
                verify(root)

    def test_rejects_unknown_flag_in_inline_cli_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            skill = (root / SKILL).read_text(encoding="utf-8")
            skill = skill.replace(
                "`vellum --json doctor --fix --require-target macos`",
                "`vellum --json doctor --fix --require-target macos --install-everything`",
            )
            (root / SKILL).write_text(skill, encoding="utf-8")
            with self.assertRaisesRegex(VerificationError, "unknown doctor flags"):
                verify(root)

    def test_rejects_unsupported_source_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            manifest = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
            manifest["supportedSourceTypes"].append("html")
            (root / MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(VerificationError, "do not exactly match"):
                verify(root)

    def test_rejects_unsupported_source_named_in_skill_prose(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            with (root / SKILL).open("a", encoding="utf-8") as handle:
                handle.write("\nImport html applications directly.\n")
            with self.assertRaisesRegex(VerificationError, "unsupported source routes"):
                verify(root)

    def test_rejects_repository_relative_installer_in_downstream_skill(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            with (root / SKILL).open("a", encoding="utf-8") as handle:
                handle.write("\n./scripts/install.sh --version 0.1.0\n")
            with self.assertRaisesRegex(VerificationError, "repository-relative installer"):
                verify(root)


if __name__ == "__main__":
    unittest.main()
