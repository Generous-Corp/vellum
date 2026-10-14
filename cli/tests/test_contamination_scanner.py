from __future__ import annotations

from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest

from cli.tests.test_installer import (
    ARTIFACT_VERIFIER,
    REPO,
    _build_verified_fixture,
    _repack_fixture,
)


INSTALLED_VALIDATOR = REPO / "scripts" / "validate_installed_sdk.py"


@unittest.skipUnless(
    shutil.which("tar")
    and (shutil.which("shasum") or shutil.which("sha256sum")),
    "archive tools unavailable",
)
class ContaminationScannerTests(unittest.TestCase):
    def test_archive_scans_text_and_paths_but_not_binary_content(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, sums, _ = _build_verified_fixture(root)
            payload = root / "test-payload"
            native_binary = payload / "sdk/lib/libvellum-native.dylib"
            native_binary.parent.mkdir(parents=True, exist_ok=True)
            native_binary.write_bytes(
                b"\xcf\xfa\xed\xfeAudioUnit\x00VST3\x00"
            )
            _repack_fixture(payload, archive, sums)

            binary_verified = subprocess.run(
                [
                    sys.executable,
                    str(ARTIFACT_VERIFIER),
                    "--archive",
                    str(archive),
                    "--checksums",
                    str(sums),
                    "--json",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                binary_verified.returncode, 0, binary_verified.stderr
            )

            forbidden_binary = payload / "sdk/lib/pulp-audio.dylib"
            forbidden_binary.write_bytes(b"\xcf\xfa\xed\xfe")
            _repack_fixture(payload, archive, sums)
            path_rejected = subprocess.run(
                [
                    sys.executable,
                    str(ARTIFACT_VERIFIER),
                    "--archive",
                    str(archive),
                    "--checksums",
                    str(sums),
                    "--json",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(path_rejected.returncode, 0)
            self.assertIn(
                "artifact contamination: pulp-named-payload "
                "in sdk/lib/pulp-audio.dylib",
                path_rejected.stderr,
            )
            forbidden_binary.unlink()

            forbidden_header = payload / "sdk/include/forbidden.hpp"
            forbidden_header.write_text(
                "struct LegacyHost { AudioUnit unit; VST3 plugin; };\n",
                encoding="utf-8",
            )
            _repack_fixture(payload, archive, sums)
            text_rejected = subprocess.run(
                [
                    sys.executable,
                    str(ARTIFACT_VERIFIER),
                    "--archive",
                    str(archive),
                    "--checksums",
                    str(sums),
                    "--json",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(text_rejected.returncode, 0)
            self.assertIn(
                "artifact contamination: audio-plugin-sdk "
                "in sdk/include/forbidden.hpp",
                text_rejected.stderr,
            )

    def test_installed_mirror_payload_keeps_the_mirror_scope(self) -> None:
        scripts_path = str(REPO / "scripts")
        sys.path.insert(0, scripts_path)
        try:
            validator = runpy.run_path(str(INSTALLED_VALIDATOR))
        finally:
            sys.path.remove(scripts_path)
        scan = validator["installed_contamination_findings"]
        with tempfile.TemporaryDirectory() as temporary:
            prefix = Path(temporary)
            install = prefix / "lib/vellum-installs/0.1.8-darwin-arm64-abc/sdk/mirror"
            header = install / "include/pulp/canvas/canvas.hpp"
            header.parent.mkdir(parents=True)
            header.write_text("namespace pulp::canvas { struct Canvas; }\n", encoding="utf-8")
            (prefix / "lib/vellum").symlink_to("vellum-installs/0.1.8-darwin-arm64-abc")
            # Mirrored source keeps its origin names inside the mirror scope.
            self.assertEqual(scan(prefix), [])
            # The mirror edge rules still apply there.
            product = install / "include/pulp/canvas/product.hpp"
            product.write_text("#include <pulp/audio/buffer.hpp>\n", encoding="utf-8")
            self.assertEqual(
                {finding["rule"] for finding in scan(prefix)},
                {"mirror-product-include"},
            )
            product.unlink()
            # A Pulp-named file outside the mirror scope is still refused.
            stray = prefix / "lib/vellum-installs/0.1.8-darwin-arm64-abc/sdk/include/pulp/x.hpp"
            stray.parent.mkdir(parents=True)
            stray.write_text("int x;\n", encoding="utf-8")
            self.assertIn(
                "pulp-named-payload", {finding["rule"] for finding in scan(prefix)}
            )

    def test_installed_tree_scans_text_and_paths_but_not_binary_content(
        self,
    ) -> None:
        scripts_path = str(REPO / "scripts")
        sys.path.insert(0, scripts_path)
        try:
            validator = runpy.run_path(str(INSTALLED_VALIDATOR))
        finally:
            sys.path.remove(scripts_path)
        scan = validator["installed_contamination_findings"]

        with tempfile.TemporaryDirectory() as temporary:
            prefix = Path(temporary)
            native_binary = (
                prefix / "lib/vellum/sdk/lib/libvellum-native.dylib"
            )
            native_binary.parent.mkdir(parents=True)
            native_binary.write_bytes(
                b"\xcf\xfa\xed\xfeAudioUnit\x00VST3\x00"
            )
            self.assertEqual(scan(prefix), [])

            forbidden_binary = (
                prefix / "lib/vellum/sdk/lib/pulp-audio.dylib"
            )
            forbidden_binary.write_bytes(b"\xcf\xfa\xed\xfe")
            self.assertEqual(
                {
                    (finding["rule"], finding["path"])
                    for finding in scan(prefix)
                },
                {
                    (
                        "pulp-named-payload",
                        "lib/vellum/sdk/lib/pulp-audio.dylib",
                    )
                },
            )
            forbidden_binary.unlink()

            forbidden_header = (
                prefix / "lib/vellum/sdk/include/forbidden.hpp"
            )
            forbidden_header.parent.mkdir(parents=True)
            forbidden_header.write_text(
                "struct LegacyHost { AudioUnit unit; VST3 plugin; };\n",
                encoding="utf-8",
            )
            self.assertEqual(
                {
                    (finding["rule"], finding["path"])
                    for finding in scan(prefix)
                },
                {
                    (
                        "audio-plugin-sdk",
                        "lib/vellum/sdk/include/forbidden.hpp",
                    )
                },
            )


if __name__ == "__main__":
    unittest.main()
