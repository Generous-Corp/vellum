#!/usr/bin/env python3
"""Negative controls and reproducibility checks for the sterile job bundle."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bundle = load("build_sterile_acceptance_bundle", SCRIPT_DIR / "build_sterile_acceptance_bundle.py")
validator = load("validate_installed_sdk", SCRIPT_DIR / "validate_installed_sdk.py")


class SterileAcceptanceBundleTest(unittest.TestCase):
    def test_bundled_python_support_carries_its_sibling_imports(self) -> None:
        import ast
        root = SCRIPT_DIR.parent
        bundled = set(bundle.SUPPORT_FILES)
        missing = []
        for relative in sorted(bundled):
            if not relative.endswith(".py"):
                continue
            path = root / relative
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module.split(".")[0]]
                for name in names:
                    sibling = path.parent / f"{name}.py"
                    if sibling.is_file():
                        relative_sibling = sibling.relative_to(root).as_posix()
                        if relative_sibling not in bundled:
                            missing.append(f"{relative} imports {relative_sibling}")
        self.assertEqual(missing, [])

    def test_bundle_is_reproducible_and_source_free(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.tar.gz"
            second = root / "second.tar.gz"
            one = bundle.build(first)
            two = bundle.build(second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(one["sha256"], two["sha256"])
            with tarfile.open(first) as archive:
                names = set(archive.getnames())
            self.assertIn(
                "vellum-sterile-acceptance/validate_installed_sdk.py", names
            )
            self.assertIn(
                "vellum-sterile-acceptance/verify_release_tag_ruleset.py", names
            )
            self.assertIn(
                "vellum-sterile-acceptance/collect_release_tag_rulesets.py", names
            )
            self.assertIn(
                "vellum-sterile-acceptance/sterile-support/"
                "fixtures/authoring-phase3/scenarios/phase3.json",
                names,
            )
            self.assertIn(
                "vellum-sterile-acceptance/sterile-support/"
                "web/tests/run_text_semantics_browser.py",
                names,
            )
            self.assertNotIn(
                "vellum-sterile-acceptance/scripts/build_sdk_artifact.py", names
            )
            self.assertFalse(any("/.git/" in name for name in names))

    def test_checkout_negative_control_is_observed_red(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checkout = root / "vellum"
            (checkout / ".git").mkdir(parents=True)
            (checkout / "provenance").mkdir()
            (checkout / "provenance/pulp-extraction.json").write_text("{}\n")
            findings = validator.checkout_contamination_findings([root])
            self.assertEqual(findings, [str(checkout.resolve())])

    def test_empty_workspace_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual(
                validator.checkout_contamination_findings([Path(temporary)]),
                [],
            )


def release_claims(gpu: bool = True, native: bool = True) -> dict[str, object]:
    return {
        "gpu_renderer": gpu,
        "targets": {"macos": {"commands": {
            name: native for name in ("build", "run", "test", "capture", "package")
        }}},
    }


class RequireGpuClaimsTest(unittest.TestCase):
    """--require-gpu-claims asserts the claims instead of reading them."""

    def validate_with_claims(self, claims: object, require: bool) -> list[list[str]]:
        calls: list[list[str]] = []
        verification = json.dumps({"claims": claims})

        def fake_run(command, *args, **kwargs):
            calls.append([str(part) for part in command])
            if any(str(part).endswith("verify_sdk_artifact.py") for part in command):
                return subprocess.CompletedProcess(command, 0, verification, "")
            raise RuntimeError("installation reached")

        with mock.patch.object(validator, "run", fake_run):
            with tempfile.TemporaryDirectory() as temporary:
                validator.validate(
                    Path(temporary) / "a.tar.gz", Path(temporary) / "SHA256SUMS",
                    None, (), None, require_gpu_claims=require,
                )
        return calls

    def test_complete_claims_have_nothing_missing(self) -> None:
        self.assertEqual(validator.missing_release_claims(release_claims()), [])

    def test_missing_gpu_and_native_claims_are_each_named(self) -> None:
        self.assertEqual(
            validator.missing_release_claims(release_claims(gpu=False)),
            ["gpu_renderer"],
        )
        missing = validator.missing_release_claims(release_claims(native=False))
        self.assertEqual(missing, [
            f"targets.macos.commands.{name}"
            for name in ("build", "run", "test", "capture", "package")
        ])
        claims = release_claims()
        claims["gpu_renderer"] = 1  # truthy is not a claim
        self.assertEqual(validator.missing_release_claims(claims), ["gpu_renderer"])
        self.assertEqual(validator.missing_release_claims(None), ["claims"])
        self.assertEqual(
            len(validator.missing_release_claims({"gpu_renderer": True})), 5
        )

    def test_required_claims_fail_before_any_check_is_skipped(self) -> None:
        for claims in (release_claims(gpu=False), release_claims(native=False)):
            with self.assertRaisesRegex(validator.ValidationError, "does not claim"):
                self.validate_with_claims(claims, require=True)

    def test_unrequired_claims_keep_the_previous_behaviour(self) -> None:
        # Without the flag a no-GPU artifact proceeds to installation, as it
        # did before; the fake runner stops it there.
        with self.assertRaisesRegex(RuntimeError, "installation reached"):
            self.validate_with_claims(release_claims(gpu=False), require=False)

    def test_complete_claims_proceed_to_installation(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "installation reached"):
            self.validate_with_claims(release_claims(), require=True)

    def test_command_line_flag_reaches_validate(self) -> None:
        seen: dict[str, object] = {}

        def fake_validate(*args, **kwargs):
            seen.update(kwargs)
            raise validator.ValidationError("stop")

        with mock.patch.object(validator, "validate", fake_validate), \
                mock.patch("sys.stderr"):
            validator.main(["--archive", "a", "--checksums", "b", "--require-gpu-claims"])
            self.assertIs(seen["require_gpu_claims"], True)
            seen.clear()
            validator.main(["--archive", "a", "--checksums", "b"])
            self.assertIs(seen["require_gpu_claims"], False)


if __name__ == "__main__":
    unittest.main()
