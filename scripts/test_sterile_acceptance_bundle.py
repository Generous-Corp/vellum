#!/usr/bin/env python3
"""Negative controls and reproducibility checks for the sterile job bundle."""

from __future__ import annotations

import importlib.util
import gzip
import io
import json
from pathlib import Path
import subprocess
import sys
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

    def test_two_builds_of_one_tree_are_the_same_bundle(self) -> None:
        # The GPU proof and the finalizer each run build(); same tree, same bytes.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle.build(root / "gpu.tar.gz")
            bundle.build(root / "trusted.tar.gz")
            self.assertEqual((root / "gpu.tar.gz").read_bytes(), (root / "trusted.tar.gz").read_bytes())
            self.assertEqual(bundle.compare(root / "gpu.tar.gz", root / "trusted.tar.gz"), [])
            # The header is fixed: no name, mtime 0, XFL 2 (level 9), OS unknown.
            self.assertEqual((root / "gpu.tar.gz").read_bytes()[:10],
                             b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\xff")

    def test_compare_ignores_gzip_bytes_but_not_content(self) -> None:
        # Finalizer run 43302131142: the two runners' bundles differed at the
        # gzip header's XFL byte. Content identity must survive that, and a
        # single changed file must still fail.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            built = root / "built.tar.gz"
            bundle.build(built)
            tar = bundle.tar_stream(built)
            other_level = root / "other-level.tar.gz"
            other_level.write_bytes(gzip.compress(tar, compresslevel=6, mtime=0))
            self.assertNotEqual(other_level.read_bytes(), built.read_bytes())
            self.assertEqual(bundle.compare(built, other_level), [])
            with tarfile.open(built) as archive:
                members = [(m, archive.extractfile(m).read() if m.isfile() else None)
                           for m in archive.getmembers()]
            changed = io.BytesIO()
            with tarfile.open(fileobj=changed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                edited = False
                for member, data in members:
                    if data is not None and not edited and member.name.endswith(".py"):
                        data = data + b"\n# changed\n"
                        member.size = len(data)
                        edited = True
                    archive.addfile(member, io.BytesIO(data) if data is not None else None)
            self.assertTrue(edited)
            tampered = root / "tampered.tar.gz"
            bundle.write_gzip(changed.getvalue(), tampered)
            problems = bundle.compare(built, tampered)
            self.assertEqual(len(problems), 1)
            self.assertIn("hold different tar streams", problems[0])

    def test_the_finalizer_compares_tar_streams(self) -> None:
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/sdk-release.yml").read_text()
        self.assertIn("python3 -I scripts/build_sterile_acceptance_bundle.py --compare", workflow)
        self.assertNotIn('cmp "$RUNNER_TEMP/trusted-sterile-acceptance.tar.gz"', workflow)

    def test_compare_command_accepts_different_gzip_headers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            built = root / "built.tar.gz"
            alternate = root / "alternate.tar.gz"
            bundle.build(built)
            alternate.write_bytes(gzip.compress(bundle.tar_stream(built), compresslevel=6, mtime=0))
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "build_sterile_acceptance_bundle.py"),
                    "--compare",
                    str(built),
                    str(alternate),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

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


class RmlUiConsumerControlTest(unittest.TestCase):
    """The consumer's package lookup is red when the archive drops VellumRmlUi."""

    def fake_sdk(self, root: Path) -> Path:
        sdk = root / "sdk"
        package = sdk / "lib/cmake/VellumRmlUi"
        package.mkdir(parents=True)
        (package / "VellumRmlUiConfig.cmake").write_text("set(VellumRmlUi_FOUND TRUE)\n")
        (package / "VellumRmlUiConfigVersion.cmake").write_text(
            "set(PACKAGE_VERSION_COMPATIBLE TRUE)\n"
        )
        (sdk / "lib/cmake/Vellum").mkdir()
        (sdk / "lib/cmake/Vellum/VellumConfig.cmake").write_text("\n")
        (sdk / "lib/libvellum.a").write_text("\n")
        (sdk / "include").mkdir()
        return sdk

    def configure(self, prefix: Path, root: Path, name: str) -> subprocess.CompletedProcess:
        source = root / f"{name}-src"
        source.mkdir()
        (source / "CMakeLists.txt").write_text(
            "cmake_minimum_required(VERSION 3.24)\nproject(P LANGUAGES NONE)\n"
            "find_package(VellumRmlUi CONFIG REQUIRED)\n"
        )
        return subprocess.run([
            "cmake", "-S", str(source), "-B", str(root / f"{name}-build"),
            f"-DCMAKE_PREFIX_PATH={prefix}",
            "-DCMAKE_FIND_USE_PACKAGE_REGISTRY=FALSE",
            "-DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=FALSE",
        ], text=True, capture_output=True, check=False)

    def test_view_keeps_everything_but_the_package(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sdk = self.fake_sdk(root)
            view = validator.prefix_without_rmlui_package(sdk, root / "view")
            self.assertFalse((view / "lib/cmake/VellumRmlUi").exists())
            self.assertTrue((view / "lib/cmake/Vellum/VellumConfig.cmake").is_file())
            self.assertTrue((view / "lib/libvellum.a").is_file())
            self.assertTrue((view / "include").is_dir())

    def test_package_lookup_is_green_with_and_red_without_the_package(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sdk = self.fake_sdk(root)
            present = self.configure(sdk, root, "present")
            self.assertEqual(present.returncode, 0, present.stderr)
            view = validator.prefix_without_rmlui_package(sdk, root / "view")
            absent = self.configure(view, root, "absent")
            self.assertNotEqual(absent.returncode, 0)
            self.assertIn("VellumRmlUi", absent.stderr)

    def test_bundle_carries_the_rmlui_consumer(self) -> None:
        for relative in ("tools/rmlui/sterile-consumer/CMakeLists.txt",
                         "tools/rmlui/sterile-consumer/main.cpp"):
            self.assertIn(relative, bundle.SUPPORT_FILES)
            self.assertTrue((SCRIPT_DIR.parent / relative).is_file())


if __name__ == "__main__":
    unittest.main()
