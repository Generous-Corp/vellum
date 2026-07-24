from __future__ import annotations

import copy
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "scripts" / "install.sh"
INSTALLER_CORE = REPO / "scripts" / "install_core.py"
ARTIFACT_VERIFIER = REPO / "scripts" / "verify_sdk_artifact.py"
INSTALLER_SOURCE_SUMS = REPO / "scripts" / "INSTALLER_SHA256SUMS"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact_inventory(payload: Path) -> list[dict[str, object]]:
    return [
        {
            "path": path.relative_to(payload).as_posix(),
            "sha256": _sha256(path),
            "size": path.stat().st_size,
            "executable": bool(path.stat().st_mode & stat.S_IXUSR),
        }
        for path in sorted(payload.rglob("*"))
        if path.is_file() and path.name != "metadata.json"
    ]


def _normalize_fixture_member(info: tarfile.TarInfo) -> tarfile.TarInfo:
    """Give installer fixtures the same canonical mode contract as real SDKs."""
    info.mode = 0o755 if info.isdir() or info.mode & stat.S_IXUSR else 0o644
    return info


def _write_fixture_archive(payload: Path, archive: Path) -> None:
    with tarfile.open(archive, "w:gz") as handle:
        for path in sorted(payload.iterdir()):
            handle.add(
                path,
                arcname=path.name,
                filter=_normalize_fixture_member,
            )


def _build_verified_fixture(
    root: Path,
    *,
    fixture_name: str = "test",
    source_commit: str = "a" * 40,
    framework_version: str = "0.1.6",
    target: str = "test",
) -> tuple[Path, Path, str]:
    payload = root / f"{fixture_name}-payload"
    payload.mkdir()
    shutil.copy2(REPO / "cli/vellum_cli.py", payload / "vellum_cli.py")
    shutil.copy2(REPO / "cli/vellum_dev.py", payload / "vellum_dev.py")
    shutil.copy2(REPO / "cli/vellum_backend.py", payload / "vellum_backend.py")
    shutil.copy2(REPO / "cli/vellum_manifest.py", payload / "vellum_manifest.py")
    shutil.copy2(REPO / "cli/vellum_png.py", payload / "vellum_png.py")
    shutil.copytree(REPO / ".agents", payload / ".agents")
    shutil.copytree(REPO / "templates", payload / "templates")
    shutil.copytree(REPO / "packages/vellum-design-ir", payload / "design-ir")
    (payload / "sdk/include").mkdir(parents=True)
    (payload / "sdk/include/placeholder.hpp").write_text("// fixture\n", encoding="utf-8")
    (payload / "metadata.json").write_text(
        json.dumps({
            "schema": "vellum.sdk-artifact.v1",
            "framework_version": framework_version,
            "cli_version": "0.1.6",
            "cli_api": 1,
            "source_commit": source_commit,
            "source_tree_clean": True,
            "target": target,
            "capabilities": {
                "cmake_sdk": False,
                "authoring_cli": True,
                "gpu_renderer": False,
                "node_runtime": False,
                "custom_components": False,
                "commands": {
                    "import": True,
                    "reimport": True,
                    "build": False,
                    "run": False,
                    "test": False,
                    "capture": False,
                    "package": False,
                },
                "authoring": {
                    "text_input_v1": {
                        "retained_tree": False,
                        "native_pointer_focus": False,
                        "native_direct_text": False,
                        "ime_composition": False,
                        "caret_and_selection": False,
                        "clipboard_editing": False,
                        "accessibility_text": False,
                        "mobile": False,
                    },
                    "scenario_v1": {
                        "input": False,
                        "key": False,
                        "maximum_steps": 1000,
                        "maximum_input_utf8_bytes": 64 * 1024,
                        "keys": [],
                    },
                    "persistence": {
                        "state_v1": False,
                        "macos_application_support": False,
                        "atomic_snapshot_write": False,
                        "migration_api": False,
                        "key_value_store": False,
                        "sync": False,
                    },
                },
                "targets": {
                    "macos": {
                        "commands": {
                            "build": False,
                            "run": False,
                            "test": False,
                            "capture": False,
                            "package": False,
                        },
                    },
                    "web": {
                        "commands": {
                            "build": False,
                            "run": False,
                            "test": False,
                            "capture": False,
                            "package": False,
                        },
                    },
                },
            },
            "files": _artifact_inventory(payload),
        }),
        encoding="utf-8",
    )
    archive = root / f"vellum-sdk-{fixture_name}.tar.gz"
    _write_fixture_archive(payload, archive)
    digest = _sha256(archive)
    sums = root / f"{fixture_name}-SHA256SUMS"
    sums.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    return archive, sums, digest


def _repack_fixture(
    payload: Path, archive: Path, sums: Path
) -> tuple[Path, Path, str]:
    metadata_path = payload / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["files"] = _artifact_inventory(payload)
    metadata_path.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_fixture_archive(payload, archive)
    digest = _sha256(archive)
    sums.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    return archive, sums, digest


def _run_installer(
    prefix: Path,
    *arguments: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(INSTALLER), *arguments, "--install-dir", str(prefix)],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )


@unittest.skipUnless(shutil.which("tar") and (shutil.which("shasum") or shutil.which("sha256sum")), "archive tools unavailable")
class InstallerTests(unittest.TestCase):
    def test_installer_source_checksum_manifest_is_current(self) -> None:
        rows = {}
        for line in INSTALLER_SOURCE_SUMS.read_text(encoding="utf-8").splitlines():
            digest, name = line.split()
            self.assertNotIn(name, rows)
            rows[name] = digest
        self.assertEqual(set(rows), {"install.sh", "install_core.py"})
        self.assertEqual(rows["install.sh"], _sha256(INSTALLER))
        self.assertEqual(rows["install_core.py"], _sha256(INSTALLER_CORE))

    def test_official_release_requires_macos_15_or_newer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            uname = fake_bin / "uname"
            uname.write_text(
                "#!/bin/sh\n"
                'case "$1" in\n'
                '  -s) printf "Darwin\\n" ;;\n'
                '  -m) printf "arm64\\n" ;;\n'
                "  *) exit 2 ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            uname.chmod(0o755)
            sw_vers = fake_bin / "sw_vers"
            gh = fake_bin / "gh"
            gh.write_text(
                "#!/bin/sh\n"
                'if [ "$1" = "--version" ]; then printf "gh version 2.96.0 (test)\\n"; exit 0; fi\n'
                'if [ "$1" = "release" ] && [ "$2" = "verify-asset" ] && [ "$3" = "--help" ]; then exit 0; fi\n'
                'printf "GH_RELEASE_ACQUISITION_REACHED\\n" >&2\n'
                "exit 19\n",
                encoding="utf-8",
            )
            gh.chmod(0o755)
            environment = {
                **os.environ,
                "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
            }

            sw_vers.write_text(
                "#!/bin/sh\nprintf '14.7.6\\n'\n",
                encoding="utf-8",
            )
            sw_vers.chmod(0o755)
            rejected = _run_installer(
                root / "rejected-prefix",
                "--version", "0.1.6",
                env=environment,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn(
                "require macOS 15.0 or newer; found 14.7.6",
                rejected.stderr,
            )
            self.assertNotIn("GH_RELEASE_ACQUISITION_REACHED", rejected.stderr)

            sw_vers.write_text(
                "#!/bin/sh\nprintf '15.0\\n'\n",
                encoding="utf-8",
            )
            accepted_boundary = _run_installer(
                root / "accepted-prefix",
                "--version", "0.1.6",
                env=environment,
            )
            self.assertNotEqual(accepted_boundary.returncode, 0)
            self.assertNotIn("require macOS 15.0 or newer", accepted_boundary.stderr)
            self.assertIn("GH_RELEASE_ACQUISITION_REACHED", accepted_boundary.stderr)

    def test_official_release_requires_gh_release_verification_capability(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            for name, body in {
                "uname": (
                    '#!/bin/sh\ncase "$1" in -s) printf "Darwin\\n" ;; '
                    '-m) printf "arm64\\n" ;; *) exit 2 ;; esac\n'
                ),
                "sw_vers": "#!/bin/sh\nprintf '15.0\\n'\n",
                "gh": (
                    '#!/bin/sh\nif [ "$1" = "--version" ]; then '
                    'printf "gh version 2.74.2 (test)\\n"; exit 0; fi\n'
                    'printf "UNSUPPORTED_GH_REACHED\\n" >&2\nexit 19\n'
                ),
            }.items():
                path = fake_bin / name
                path.write_text(body, encoding="utf-8")
                path.chmod(0o755)
            environment = {
                **os.environ,
                "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
            }
            rejected = _run_installer(
                root / "prefix",
                "--version", "0.1.6",
                env=environment,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn(
                "GitHub CLI 2.75.0+ with release verify-asset support is required",
                rejected.stderr,
            )
            self.assertNotIn("GH_RELEASE_ACQUISITION_REACHED", rejected.stderr)

    def test_local_install_runs_and_creates_project_outside_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prefix = root / "prefix"
            completed = subprocess.run(
                ["sh", str(INSTALLER), "--local", str(REPO), "--install-dir", str(prefix)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("LOCAL DEVELOPMENT INSTALL", completed.stdout)
            project = root / "sterile-consumer"
            create = subprocess.run(
                [str(prefix / "bin/vellum"), "create", "Sterile Consumer", "-d", str(project), "--json"],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(create.returncode, 0, create.stderr)
            self.assertTrue((project / "framework.lock").is_file())
            install_manifest = json.loads(
                (prefix / "lib/vellum/install-manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(install_manifest, {
                "schema": "vellum.sdk-install.v1",
                "verified": False,
                "artifact": None,
                "artifact_sha256": None,
                "framework_version": "0.1.6",
                "target": "local-development",
                "source_commit": None,
            })
            lock = json.loads((project / "framework.lock").read_text(encoding="utf-8"))
            self.assertEqual(lock["framework"]["artifact"], {
                "verified": False,
                "sha256": None,
                "target": "local-development",
                "sourceCommit": None,
            })
            source = root / "revision-a.source.json"
            updated_source = root / "revision-b.source.json"
            shutil.copy2(REPO / "fixtures/design-ir/revision-a.source.json", source)
            shutil.copy2(REPO / "fixtures/design-ir/revision-b.source.json", updated_source)
            imported = subprocess.run(
                [str(prefix / "bin/vellum"), "import", str(source), "--json"],
                cwd=project,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(imported.returncode, 0, imported.stdout)
            self.assertTrue((project / "design/ir/sources/main.designir.json").is_file())
            self.assertTrue((prefix / "lib/vellum/bin/vellum-backend").is_file())
            self.assertTrue((prefix / "lib/vellum/bin/vellum-import-backend").is_file())
            self.assertFalse((prefix / "lib/vellum/bin/vellum-native-backend").exists())
            handshake = subprocess.run(
                [
                    str(prefix / "lib/vellum/bin/vellum-backend"),
                    "import", "--project", str(project), "--json",
                    "--framework-version", "9.9.9", "--cli-api", "1",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(handshake.returncode, 3, handshake.stdout)
            self.assertEqual(json.loads(handshake.stdout)["status"], "backend_handshake_mismatch")
            reimported = subprocess.run(
                [str(prefix / "bin/vellum"), "reimport", "--source", str(updated_source), "--json"],
                cwd=project,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(reimported.returncode, 0, reimported.stdout)
            active = json.loads((project / "design/import.lock.json").read_text())
            self.assertEqual(active["sources"]["main"]["activeRevision"], "palette-board-b")
            unavailable = subprocess.run(
                [str(prefix / "bin/vellum"), "build", "--json"],
                cwd=project,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(unavailable.returncode, 4, unavailable.stdout)
            self.assertEqual(json.loads(unavailable.stdout)["status"], "capability_unavailable")

            zip_project = root / "zip-consumer"
            zip_created = subprocess.run(
                [
                    str(prefix / "bin/vellum"), "create", "ZIP Consumer",
                    "-d", str(zip_project), "--from", "figma",
                    str(REPO / "fixtures/design-ir/pulp-emitter-generic.pulp.zip"),
                    "--no-verify", "--json",
                ],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(zip_created.returncode, 0, zip_created.stdout + zip_created.stderr)
            zip_lock = json.loads((zip_project / "design/import.lock.json").read_text())
            zip_source = zip_lock["sources"]["main"]
            self.assertEqual(zip_source["sourceArtifactKind"], "pulp-zip")
            zip_snapshot = (
                zip_project / "sources/imported/main" /
                zip_source["activeRevision"] / "source.pulp.zip"
            )
            self.assertEqual(
                zip_snapshot.read_bytes(),
                (REPO / "fixtures/design-ir/pulp-emitter-generic.pulp.zip").read_bytes(),
            )

    def test_local_install_requires_node_20_or_newer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_node = fake_bin / "node"
            fake_node.write_text("#!/bin/sh\nprintf '%s\\n' 'v18.20.0'\n", encoding="utf-8")
            fake_node.chmod(0o755)
            completed = subprocess.run(
                ["sh", str(INSTALLER), "--local", str(REPO), "--install-dir", str(root / "prefix")],
                text=True,
                capture_output=True,
                check=False,
                env={**os.environ, "PATH": f"{fake_bin}:/usr/bin:/bin"},
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("Node.js 20+", completed.stderr)
            self.assertIn("v18.20.0", completed.stderr)

    def test_archive_hash_is_verified_before_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, sums, digest = _build_verified_fixture(root)

            prefix = root / "verified-prefix"
            valid = _run_installer(
                prefix,
                "--archive", str(archive),
                "--checksums", str(sums),
            )
            self.assertEqual(valid.returncode, 0, valid.stderr)
            self.assertIn("Verified SHA-256", valid.stdout)
            manifest = json.loads(
                (prefix / "lib/vellum/install-manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["artifact_sha256"], digest)
            self.assertEqual(manifest["artifact"], archive.name)
            self.assertEqual(manifest["target"], "test")
            self.assertEqual(manifest["source_commit"], "a" * 40)
            project = root / "verified-project"
            created = subprocess.run(
                [str(prefix / "bin/vellum"), "create", "Verified", "-d", str(project), "--json"],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            lock = json.loads((project / "framework.lock").read_text(encoding="utf-8"))
            self.assertEqual(lock["framework"]["artifact"]["sha256"], digest)
            self.assertTrue(lock["framework"]["artifact"]["verified"])

            tampered = root / "tampered-prefix"
            archive.write_bytes(archive.read_bytes() + b"tampered")
            invalid = _run_installer(
                tampered,
                "--archive", str(archive),
                "--checksums", str(sums),
            )
            self.assertNotEqual(invalid.returncode, 0)
            self.assertIn("Refusing to extract", invalid.stderr)
            self.assertFalse(tampered.exists())

            missing_sums = root / "MISSING-SHA256SUMS"
            missing_sums.write_text(f"{'0' * 64}  another-asset.tar.gz\n", encoding="utf-8")
            missing_prefix = root / "missing-prefix"
            missing = _run_installer(
                missing_prefix,
                "--archive", str(archive),
                "--checksums", str(missing_sums),
            )
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("exactly one checksum", missing.stderr)
            self.assertFalse(missing_prefix.exists())

    def test_fixture_archive_and_install_manifest_modes_ignore_ambient_umask(self) -> None:
        for ambient_umask in (0o002, 0o077):
            with self.subTest(umask=oct(ambient_umask)):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    previous_umask = os.umask(ambient_umask)
                    try:
                        archive, sums, _ = _build_verified_fixture(
                            root,
                            fixture_name=f"umask-{ambient_umask:o}",
                        )
                        _repack_fixture(
                            root / f"umask-{ambient_umask:o}-payload",
                            archive,
                            sums,
                        )
                        prefix = root / "prefix"
                        installed = _run_installer(
                            prefix,
                            "--archive", str(archive),
                            "--checksums", str(sums),
                        )
                    finally:
                        os.umask(previous_umask)

                    self.assertEqual(
                        installed.returncode,
                        0,
                        installed.stdout + installed.stderr,
                    )
                    with tarfile.open(archive, "r:gz") as handle:
                        for member in handle.getmembers():
                            expected = (
                                0o755
                                if member.isdir() or member.mode & stat.S_IXUSR
                                else 0o644
                            )
                            self.assertEqual(
                                stat.S_IMODE(member.mode),
                                expected,
                                member.name,
                            )
                    manifest = prefix / "lib/vellum/install-manifest.json"
                    self.assertEqual(
                        stat.S_IMODE(manifest.stat().st_mode),
                        0o644,
                    )

    def test_installer_enforces_the_canonical_artifact_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, _sums, _digest = _build_verified_fixture(root)
            forged = root / "vellum-sdk-forged-contract.tar.gz"
            with tarfile.open(archive, "r:gz") as source:
                with tarfile.open(forged, "w:gz") as output:
                    for member in source.getmembers():
                        stream = (
                            source.extractfile(member)
                            if member.isfile()
                            else None
                        )
                        if member.name == "metadata.json":
                            metadata = json.load(stream)
                            del metadata["source_tree_clean"]
                            content = (
                                json.dumps(
                                    metadata, indent=2, sort_keys=True
                                )
                                + "\n"
                            ).encode()
                            replacement = copy.copy(member)
                            replacement.size = len(content)
                            output.addfile(replacement, io.BytesIO(content))
                        else:
                            output.addfile(member, stream)
            sums = root / "FORGED-SHA256SUMS"
            sums.write_text(
                f"{_sha256(forged)}  {forged.name}\n",
                encoding="utf-8",
            )

            canonical = subprocess.run(
                [
                    sys.executable,
                    str(ARTIFACT_VERIFIER),
                    "--archive",
                    str(forged),
                    "--checksums",
                    str(sums),
                    "--json",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            installed = _run_installer(
                root / "prefix",
                "--archive",
                str(forged),
                "--checksums",
                str(sums),
            )
            self.assertNotEqual(canonical.returncode, 0)
            self.assertNotEqual(installed.returncode, 0)
            expected = "metadata has missing or unknown fields"
            self.assertIn(expected, canonical.stderr)
            self.assertIn(expected, installed.stderr)
            self.assertFalse((root / "prefix/lib/vellum").exists())

    def test_cli_version_must_equal_framework_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, sums, _digest = _build_verified_fixture(root)
            verified = subprocess.run(
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
            self.assertEqual(verified.returncode, 0, verified.stderr)
            self.assertEqual(
                json.loads(verified.stdout)["cli_version"], "0.1.6"
            )

            payload = root / "test-payload"
            metadata_path = payload / "metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["cli_version"] = "0.1.2"
            metadata_path.write_text(
                json.dumps(metadata, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            _repack_fixture(payload, archive, sums)
            canonical = subprocess.run(
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
            installed = _run_installer(
                root / "prefix",
                "--archive",
                str(archive),
                "--checksums",
                str(sums),
            )
            for result in (canonical, installed):
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(
                    "CLI version does not match framework version",
                    result.stderr,
                )

    def test_checksum_manifest_has_byte_and_line_limits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, _sums, digest = _build_verified_fixture(root)
            exact = f"{digest}  {archive.name}\n"

            oversized = root / "OVERSIZED-SHA256SUMS"
            oversized.write_text(
                exact + "#" * (1024 * 1024),
                encoding="utf-8",
            )
            oversized_result = _run_installer(
                root / "oversized-prefix",
                "--archive",
                str(archive),
                "--checksums",
                str(oversized),
            )
            self.assertNotEqual(oversized_result.returncode, 0)
            self.assertIn(
                "checksum manifest exceeds the installer size limit",
                oversized_result.stderr,
            )

            too_many_lines = root / "TOO-MANY-LINES-SHA256SUMS"
            too_many_lines.write_text(
                exact + ("ignored\n" * 10_001),
                encoding="utf-8",
            )
            lines_result = _run_installer(
                root / "lines-prefix",
                "--archive",
                str(archive),
                "--checksums",
                str(too_many_lines),
            )
            self.assertNotEqual(lines_result.returncode, 0)
            self.assertIn(
                "checksum manifest exceeds the installer line limit",
                lines_result.stderr,
            )

    def test_contract_retention_has_per_file_and_total_memory_limits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, sums, _digest = _build_verified_fixture(root)

            per_file_core = runpy.run_path(str(INSTALLER_CORE))
            per_file_core["verify_archive_contract"].__globals__[
                "MAX_RETAINED_FILE_BYTES"
            ] = 1
            with self.assertRaisesRegex(
                per_file_core["InstallError"], "per-file memory limit"
            ):
                per_file_core["verify_archive_contract"](archive, sums)

            total_core = runpy.run_path(str(INSTALLER_CORE))
            total_globals = total_core[
                "verify_archive_contract"
            ].__globals__
            total_globals["MAX_RETAINED_FILE_BYTES"] = 1024**3
            total_globals["MAX_RETAINED_TOTAL_BYTES"] = 1
            with self.assertRaisesRegex(
                total_core["InstallError"], "total memory limit"
            ):
                total_core["verify_archive_contract"](archive, sums)

    def test_managed_regular_file_hardlinks_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, sums, digest = _build_verified_fixture(root)
            cases = {
                "installed payload": lambda prefix: (
                    prefix / "lib/vellum"
                ).resolve()
                / "vellum_cli.py",
                "verified cache": lambda prefix: (
                    prefix
                    / "lib/vellum-cache"
                    / digest
                    / archive.name
                ),
                "launcher": lambda prefix: prefix / "bin/vellum",
                "state": lambda prefix: (
                    prefix / "lib/vellum-installer-state.json"
                ),
                "lock": lambda prefix: prefix / ".vellum-installer.lock",
            }
            for name, managed_path in cases.items():
                with self.subTest(name=name):
                    prefix = root / f"prefix-{name.replace(' ', '-')}"
                    installed = _run_installer(
                        prefix,
                        "--archive",
                        str(archive),
                        "--checksums",
                        str(sums),
                    )
                    self.assertEqual(
                        installed.returncode, 0, installed.stderr
                    )
                    outside = root / f"{name.replace(' ', '-')}.link"
                    os.link(managed_path(prefix), outside)
                    verified = _run_installer(
                        prefix, "--verify-installed"
                    )
                    self.assertNotEqual(verified.returncode, 0)
                    self.assertTrue(
                        "unexpected hard links" in verified.stderr
                        or "launcher is missing or unmanaged"
                        in verified.stderr,
                        verified.stderr,
                    )

    def test_exact_reinstall_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, sums, digest = _build_verified_fixture(root)
            prefix = root / "prefix"
            first = _run_installer(
                prefix,
                "--archive", str(archive),
                "--checksums", str(sums),
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            active_before = (prefix / "lib/vellum").resolve()
            activation_paths = (
                prefix / "bin/vellum",
                prefix / "lib/vellum",
                prefix / "lib/vellum-installer-state.json",
            )
            activation_before = {
                path: (
                    path.lstat().st_ino,
                    path.lstat().st_mtime_ns,
                    stat.S_IMODE(path.lstat().st_mode),
                    path.lstat().st_size,
                )
                for path in activation_paths
            }
            second = _run_installer(
                prefix,
                "--archive", str(archive),
                "--checksums", str(sums),
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("Vellum installer: already_installed", second.stdout)
            self.assertEqual((prefix / "lib/vellum").resolve(), active_before)
            self.assertEqual(
                [path.name for path in (prefix / "lib/vellum-installs").iterdir()],
                [f"0.1.6-test-{digest}"],
            )
            self.assertEqual(
                {
                    path: (
                        path.lstat().st_ino,
                        path.lstat().st_mtime_ns,
                        stat.S_IMODE(path.lstat().st_mode),
                        path.lstat().st_size,
                    )
                    for path in activation_paths
                },
                activation_before,
                "an exact verified reinstall must not replace activation files",
            )
            verified = _run_installer(prefix, "--verify-installed")
            self.assertEqual(verified.returncode, 0, verified.stderr)
            self.assertIn("Vellum installer: verified", verified.stdout)

    def test_failure_after_activation_rolls_back_to_old_active_sdk(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first_archive, first_sums, first_digest = _build_verified_fixture(
                root,
                fixture_name="first",
                source_commit="a" * 40,
            )
            second_archive, second_sums, second_digest = _build_verified_fixture(
                root,
                fixture_name="second",
                source_commit="b" * 40,
            )
            prefix = root / "prefix"
            first = _run_installer(
                prefix,
                "--archive", str(first_archive),
                "--checksums", str(first_sums),
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            active_before = (prefix / "lib/vellum").resolve()
            self.assertEqual(active_before.name, f"0.1.6-test-{first_digest}")

            failed = _run_installer(
                prefix,
                "--archive", str(second_archive),
                "--checksums", str(second_sums),
                env={**os.environ, "VELLUM_INSTALL_FAIL_AFTER_ACTIVATE": "1"},
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("injected failure after activation", failed.stderr)
            self.assertEqual((prefix / "lib/vellum").resolve(), active_before)
            self.assertNotEqual(first_digest, second_digest)
            self.assertTrue(
                (prefix / "lib/vellum-installs" / f"0.1.6-test-{second_digest}").is_dir()
            )
            version = subprocess.run(
                [str(prefix / "bin/vellum"), "--version"],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(version.returncode, 0, version.stderr)
            verified = _run_installer(prefix, "--verify-installed")
            self.assertEqual(verified.returncode, 0, verified.stderr)

    def test_failure_before_activation_resumes_without_losing_old_sdk(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first_archive, first_sums, first_digest = _build_verified_fixture(
                root,
                fixture_name="first",
                source_commit="a" * 40,
            )
            second_archive, second_sums, second_digest = _build_verified_fixture(
                root,
                fixture_name="second",
                source_commit="b" * 40,
            )
            prefix = root / "prefix"
            first = _run_installer(
                prefix,
                "--archive", str(first_archive),
                "--checksums", str(first_sums),
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            active_before = (prefix / "lib/vellum").resolve()
            self.assertEqual(active_before.name, f"0.1.6-test-{first_digest}")

            interrupted = _run_installer(
                prefix,
                "--archive", str(second_archive),
                "--checksums", str(second_sums),
                env={**os.environ, "VELLUM_INSTALL_FAIL_BEFORE_ACTIVATE": "1"},
            )
            self.assertNotEqual(interrupted.returncode, 0)
            self.assertIn(
                "injected failure before activation", interrupted.stderr
            )
            self.assertEqual((prefix / "lib/vellum").resolve(), active_before)
            staged_install = (
                prefix
                / "lib/vellum-installs"
                / f"0.1.6-test-{second_digest}"
            )
            self.assertTrue(staged_install.is_dir())
            self.assertFalse(
                any(
                    path.name.startswith(".staging-")
                    for path in staged_install.parent.iterdir()
                )
            )

            resumed = _run_installer(
                prefix,
                "--archive", str(second_archive),
                "--checksums", str(second_sums),
            )
            self.assertEqual(resumed.returncode, 0, resumed.stderr)
            self.assertIn(
                "Vellum installer: already_installed", resumed.stdout
            )
            self.assertEqual(
                (prefix / "lib/vellum").resolve(), staged_install.resolve()
            )
            verified = _run_installer(prefix, "--verify-installed")
            self.assertEqual(verified.returncode, 0, verified.stderr)

    def test_installed_file_tampering_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, sums, _ = _build_verified_fixture(root)
            prefix = root / "prefix"
            installed = _run_installer(
                prefix,
                "--archive", str(archive),
                "--checksums", str(sums),
            )
            self.assertEqual(installed.returncode, 0, installed.stderr)
            active = (prefix / "lib/vellum").resolve()
            with (active / "vellum_cli.py").open("a", encoding="utf-8") as handle:
                handle.write("\n# tampered\n")

            verified = _run_installer(prefix, "--verify-installed")
            self.assertNotEqual(verified.returncode, 0)
            self.assertIn("installed SDK integrity check failed: vellum_cli.py", verified.stderr)
            reinstalled = _run_installer(
                prefix,
                "--archive", str(archive),
                "--checksums", str(sums),
            )
            self.assertNotEqual(reinstalled.returncode, 0)
            self.assertIn("installed SDK integrity check failed: vellum_cli.py", reinstalled.stderr)

    def test_installed_directory_mode_tampering_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, sums, _ = _build_verified_fixture(root)
            prefix = root / "prefix"
            installed = _run_installer(
                prefix,
                "--archive", str(archive),
                "--checksums", str(sums),
            )
            self.assertEqual(installed.returncode, 0, installed.stderr)
            active = (prefix / "lib/vellum").resolve()
            templates = active / "templates"
            templates.chmod(0o777)
            directory_verified = _run_installer(prefix, "--verify-installed")
            self.assertNotEqual(directory_verified.returncode, 0)
            self.assertIn("mode_changed=['templates']", directory_verified.stderr)

            templates.chmod(0o755)
            active.chmod(0o777)
            root_verified = _run_installer(prefix, "--verify-installed")
            self.assertNotEqual(root_verified.returncode, 0)
            self.assertIn("ownership receipt is incompatible", root_verified.stderr)

    def test_metadata_identity_cannot_escape_install_storage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, sums, _ = _build_verified_fixture(
                root,
                target="../outside",
            )
            prefix = root / "prefix"
            completed = _run_installer(
                prefix,
                "--archive", str(archive),
                "--checksums", str(sums),
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn(
                "artifact compatibility/provenance fields are malformed",
                completed.stderr,
            )
            self.assertFalse((root / "outside").exists())

    def test_requested_release_identity_must_match_artifact_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, sums, _ = _build_verified_fixture(root)
            prefix = root / "prefix"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(INSTALLER_CORE),
                    "install",
                    "--archive", str(archive),
                    "--checksums", str(sums),
                    "--prefix", str(prefix),
                    "--expected-version", "0.1.6",
                    "--expected-target", "wrong-target",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("target does not match", completed.stderr)
            self.assertFalse((prefix / "lib/vellum").exists())

    def test_portable_archive_path_collisions_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "vellum-sdk-collision.tar.gz"
            metadata = json.dumps({
                "schema": "vellum.sdk-artifact.v1",
                "framework_version": "0.1.6",
                "cli_api": 1,
                "source_commit": "a" * 40,
                "target": "test",
                "files": [],
            }).encode()
            with tarfile.open(archive, "w:gz") as handle:
                for name, payload in (
                    ("metadata.json", metadata),
                    ("ui/Case.txt", b"first"),
                    ("ui/case.txt", b"second"),
                ):
                    member = tarfile.TarInfo(name)
                    member.size = len(payload)
                    member.mode = 0o644
                    handle.addfile(member, io.BytesIO(payload))
            sums = root / "SHA256SUMS"
            sums.write_text(
                f"{_sha256(archive)}  {archive.name}\n",
                encoding="utf-8",
            )
            completed = _run_installer(
                root / "prefix",
                "--archive", str(archive),
                "--checksums", str(sums),
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("portable-path collision", completed.stderr)

    def test_concurrent_exact_installs_serialize_on_one_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, sums, _ = _build_verified_fixture(root)
            prefix = root / "prefix"
            arguments = [
                "sh", str(INSTALLER),
                "--archive", str(archive),
                "--checksums", str(sums),
                "--install-dir", str(prefix),
            ]
            first = subprocess.Popen(
                arguments,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            second = subprocess.Popen(
                arguments,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            first_stdout, first_stderr = first.communicate()
            second_stdout, second_stderr = second.communicate()
            self.assertEqual(first.returncode, 0, first_stderr)
            self.assertEqual(second.returncode, 0, second_stderr)
            combined = first_stdout + second_stdout
            self.assertIn("Vellum installer: installed", combined)
            self.assertIn("Vellum installer: already_installed", combined)
            verified = _run_installer(prefix, "--verify-installed")
            self.assertEqual(verified.returncode, 0, verified.stderr)

    def test_unowned_symlink_and_tampered_state_are_rejected_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, sums, _ = _build_verified_fixture(root)
            prefix = root / "prefix"
            installed = _run_installer(
                prefix,
                "--archive", str(archive),
                "--checksums", str(sums),
            )
            self.assertEqual(installed.returncode, 0, installed.stderr)
            active = (prefix / "lib/vellum").resolve()
            (active / "unowned-directory").symlink_to(root)
            verified = _run_installer(prefix, "--verify-installed")
            self.assertNotEqual(verified.returncode, 0)
            self.assertIn("unsafe symlink", verified.stderr)
            (active / "unowned-directory").unlink()

            state_path = prefix / "lib/vellum-installer-state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["active_install"] = str(root / "outside")
            state_path.write_text(json.dumps(state), encoding="utf-8")
            uninstalled = _run_installer(prefix, "--uninstall")
            self.assertNotEqual(uninstalled.returncode, 0)
            self.assertIn("state does not match", uninstalled.stderr)
            self.assertTrue((prefix / "lib/vellum").is_symlink())
            self.assertTrue((prefix / "bin/vellum").is_file())

    def test_uninstall_rejects_forged_receipt_path_without_deleting_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, sums, _ = _build_verified_fixture(root)
            prefix = root / "prefix"
            installed = _run_installer(
                prefix,
                "--archive", str(archive),
                "--checksums", str(sums),
            )
            self.assertEqual(installed.returncode, 0, installed.stderr)
            sentinel = prefix / "sentinel.txt"
            sentinel.write_text("must survive\n", encoding="utf-8")
            active = (prefix / "lib/vellum").resolve()
            receipt_path = active / ".vellum-install-ownership.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["files"][0] = {
                "path": "../../../sentinel.txt",
                "sha256": _sha256(sentinel),
                "size": sentinel.stat().st_size,
                "mode": 0o644,
            }
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

            completed = _run_installer(prefix, "--uninstall")
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("ownership row is malformed", completed.stderr)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "must survive\n")
            self.assertTrue((prefix / "lib/vellum").is_symlink())
            self.assertTrue((prefix / "bin/vellum").is_file())

    def test_local_install_refuses_transactional_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, sums, _ = _build_verified_fixture(root)
            prefix = root / "prefix"
            installed = _run_installer(
                prefix,
                "--archive", str(archive),
                "--checksums", str(sums),
            )
            self.assertEqual(installed.returncode, 0, installed.stderr)
            active_before = (prefix / "lib/vellum").resolve()

            local = _run_installer(prefix, "--local", str(REPO))
            self.assertNotEqual(local.returncode, 0)
            self.assertIn("Refusing unmanaged or transactional", local.stderr)
            self.assertEqual((prefix / "lib/vellum").resolve(), active_before)
            verified = _run_installer(prefix, "--verify-installed")
            self.assertEqual(verified.returncode, 0, verified.stderr)

    def test_uninstall_is_idempotent_and_preserves_unrelated_prefix_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, sums, _ = _build_verified_fixture(root)
            prefix = root / "prefix"
            unrelated = prefix / "share/other-tool/keep.txt"
            unrelated.parent.mkdir(parents=True)
            unrelated.write_text("not owned by Vellum\n", encoding="utf-8")
            unrelated_bin = prefix / "bin/other-tool"
            unrelated_bin.parent.mkdir(parents=True)
            unrelated_bin.write_text("#!/bin/sh\n", encoding="utf-8")
            installed = _run_installer(
                prefix,
                "--archive", str(archive),
                "--checksums", str(sums),
            )
            self.assertEqual(installed.returncode, 0, installed.stderr)

            first = _run_installer(prefix, "--uninstall")
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertIn("Vellum installer: uninstalled", first.stdout)
            self.assertFalse((prefix / "lib/vellum").exists())
            self.assertFalse((prefix / "bin/vellum").exists())
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "not owned by Vellum\n")
            self.assertTrue(unrelated_bin.is_file())

            second = _run_installer(prefix, "--uninstall")
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("Vellum installer: already_absent", second.stdout)
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "not owned by Vellum\n")
            self.assertTrue(unrelated_bin.is_file())

    def test_installed_python_launchers_do_not_mutate_sdk_with_bytecode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, sums, _ = _build_verified_fixture(root)
            prefix = root / "prefix"
            installed = _run_installer(
                prefix,
                "--archive", str(archive),
                "--checksums", str(sums),
            )
            self.assertEqual(installed.returncode, 0, installed.stderr)
            sdk = (prefix / "lib/vellum").resolve()
            backend = subprocess.run(
                [str(sdk / "bin/vellum-backend"), "--help"],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertIn(backend.returncode, {0, 2}, backend.stderr)
            self.assertEqual(list(sdk.rglob("__pycache__")), [])
            verified = _run_installer(prefix, "--verify-installed")
            self.assertEqual(verified.returncode, 0, verified.stderr)
            uninstalled = _run_installer(prefix, "--uninstall")
            self.assertEqual(uninstalled.returncode, 0, uninstalled.stderr)

    def test_verified_archive_rejects_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "payload"
            payload.mkdir()
            (payload / "vellum_cli.py").write_text("print('unsafe')\n", encoding="utf-8")
            link = payload / "templates"
            link.symlink_to("/tmp")
            archive = root / "vellum-sdk-link.tar.gz"
            with tarfile.open(archive, "w:gz") as handle:
                handle.add(payload / "vellum_cli.py", arcname="vellum_cli.py")
                handle.add(link, arcname="templates", recursive=False)
            sums = root / "SHA256SUMS"
            sums.write_text(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n", encoding="utf-8")
            prefix = root / "prefix"
            completed = subprocess.run(
                ["sh", str(INSTALLER), "--archive", str(archive), "--checksums", str(sums), "--install-dir", str(prefix)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("unsafe archive member", completed.stderr)
            self.assertFalse((prefix / "lib/vellum").exists())
            self.assertFalse((prefix / "lib/vellum-installs").exists())


if __name__ == "__main__":
    unittest.main()
