#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    from .git_test_environment import disable_auto_maintenance
except ImportError:  # Direct script execution.
    from git_test_environment import disable_auto_maintenance

disable_auto_maintenance()


MODULE_PATH = Path(__file__).with_name("verify_extraction.py")
SPEC = importlib.util.spec_from_file_location("verify_extraction", MODULE_PATH)
assert SPEC and SPEC.loader
verify_extraction = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify_extraction)
ARTIFACT_MODULE_PATH = MODULE_PATH.parents[2] / "scripts/verify_sdk_artifact.py"
ARTIFACT_SPEC = importlib.util.spec_from_file_location(
    "verify_sdk_artifact", ARTIFACT_MODULE_PATH
)
assert ARTIFACT_SPEC and ARTIFACT_SPEC.loader
verify_sdk_artifact = importlib.util.module_from_spec(ARTIFACT_SPEC)
ARTIFACT_SPEC.loader.exec_module(verify_sdk_artifact)


class VerifyExtractionTests(unittest.TestCase):
    def test_authority_phase_is_cross_bound_to_active_lock(self) -> None:
        start = "a" * 40
        record = "b" * 40
        activation = "c" * 40
        extraction = {
            "status": "active",
            "authority": {
                "state": "active",
                "ownership_start_commit": start,
                "authority_record_commit": record,
                "authority_record_path": "provenance/authority/records/native.json",
                "authority_ref": "refs/tags/authority/native",
                "pulp_activation_commit": activation,
                "pulp_authority_event_path": ".github/vellum-change-events/active.json",
                "pulp_authority_event_id": "active",
                "accepted_by": "@owner",
                "accepted_at": "2026-07-24T02:00:00Z",
            },
        }
        lock = {
            "state": "active",
            "vellum_authority_start_commit": start,
            "vellum_authority_record_commit": record,
            "pulp_activation_commit": activation,
        }
        self.assertEqual(
            verify_extraction.verify_authority_phase(extraction, lock), (True, [])
        )
        extraction["authority"]["pulp_activation_commit"] = "d" * 40
        transferred, errors = verify_extraction.verify_authority_phase(
            extraction, lock
        )
        self.assertFalse(transferred)
        self.assertIn(
            "active extraction authority has invalid pulp_activation_commit", errors
        )

    def test_git_blob_identity_matches_git_format(self) -> None:
        self.assertEqual(
            verify_extraction.git_blob_sha(b"test content\n"),
            "d670460b4b4aece5915caf5c68d12f560a9fe3e4",
        )

    def test_active_scan_finds_pulp_namespace_and_plugin_sdk(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "foundation").mkdir()
            (root / "foundation/bad.cpp").write_text(
                "auto* x = pulp::audio::engine();\nAudioUnit thing;\n", encoding="utf-8"
            )
            findings = verify_extraction.scan_active_surface(root, ("foundation",))
            self.assertEqual({finding["rule"] for finding in findings}, {
                "pulp-public-namespace", "audio-plugin-sdk"
            })

    def test_default_inventory_scans_authoring_and_ui_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "authoring").mkdir()
            (root / "authoring/host.mm").write_text(
                "auto legacy = pulp::view::host();\n", encoding="utf-8"
            )
            (root / "packages/vellum-ui/src").mkdir(parents=True)
            (root / "packages/vellum-ui/src/runtime.js").write_text(
                "const forbidden = '@pulp/audio';\n", encoding="utf-8"
            )

            findings = verify_extraction.scan_active_surface(root)

            self.assertEqual(
                {finding["path"] for finding in findings},
                {
                    "authoring/host.mm",
                    "packages/vellum-ui/src/runtime.js",
                },
            )

    def test_active_scan_ignores_historical_evidence_outside_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "provenance").mkdir()
            (root / "provenance/history.txt").write_text(
                "pulp::audio::engine();\n", encoding="utf-8"
            )
            self.assertEqual(
                verify_extraction.scan_active_surface(root, ("foundation",)), []
            )

    def test_artifact_scan_finds_retired_path_namespace_and_plugin_sdk(self) -> None:
        findings = verify_sdk_artifact.payload_contamination_findings(
            "sdk/include/pulp/audio.hpp", b"namespace pulp { AudioUnit x; }\n"
        )
        self.assertEqual(
            {finding["rule"] for finding in findings},
            {"pulp-named-payload", "pulp-public-namespace", "audio-plugin-sdk"},
        )

    def write_mirror(self, root: Path, relative: str, content: bytes) -> dict[str, str]:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return {
            "mirror_path": relative,
            "git_blob_sha": verify_extraction.git_blob_sha(content),
            "pulp_commit": "a" * 40,
            "pulp_path": relative.removeprefix("mirror/"),
            "export_receipt": "provenance/mirror/receipts/first.json",
        }

    def test_mirror_accepts_recorded_extracted_blob(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = b"#include <pulp/canvas/canvas.hpp>\nnamespace pulp::view { int PULP_X; }\n"
            record = self.write_mirror(root, "mirror/core/view/src/view.cpp", source)

            self.assertEqual(verify_extraction.mirror_origin_findings(root, [record]), [])
            self.assertEqual(verify_extraction.mirror_edge_findings(root), [])
            self.assertEqual(verify_extraction.scan_active_surface(root), [])

    def test_mirror_rejects_unrecorded_and_edited_pulp_blobs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recorded = self.write_mirror(root, "mirror/core/view/src/a.cpp", b"int a;\n")
            self.write_mirror(root, "mirror/core/view/src/b.cpp", b"int b;\n")
            (root / recorded["mirror_path"]).write_bytes(b"int a = 1;\n")

            findings = verify_extraction.mirror_origin_findings(root, [recorded])

            self.assertEqual(
                {(finding["path"], finding["rule"]) for finding in findings},
                {
                    ("mirror/core/view/src/a.cpp", "mirror-blob-differs-from-origin-record"),
                    ("mirror/core/view/src/b.cpp", "mirror-file-without-origin-record"),
                },
            )

    def test_mirror_keeps_superseded_append_only_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = self.write_mirror(root, "mirror/core/view/src/a.cpp", b"int a;\n")
            second = self.write_mirror(root, "mirror/core/view/src/a.cpp", b"int a = 2;\n")
            removed = dict(first, mirror_path="mirror/core/view/src/gone.cpp")

            self.assertEqual(
                verify_extraction.mirror_origin_findings(root, [first, second, removed]),
                [],
            )

    def test_native_root_rejects_copy_of_recorded_pulp_blob(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            content = b"namespace detail { int copied; }\n"
            record = self.write_mirror(root, "mirror/core/view/src/a.cpp", content)
            (root / "graphics").mkdir()
            (root / "graphics/a.cpp").write_bytes(content)

            findings = verify_extraction.copied_seed_findings(
                root, ("graphics",), {record["git_blob_sha"]}
            )

            self.assertEqual([finding["path"] for finding in findings], ["graphics/a.cpp"])

    def test_mirror_rejects_audio_plugin_host_and_product_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_mirror(
                root,
                "mirror/core/view/src/meter.cpp",
                b"#include <pulp/audio/buffer.hpp>\n"
                b"#include <pluginterfaces/base/funknown.h>\n"
                b"auto x = pulp::signal::gain(1.0f);\n",
            )
            self.write_mirror(
                root,
                "mirror/core/view/CMakeLists.txt",
                b"target_link_libraries(pulp-view-core PUBLIC pulp-format-core)\n"
                b"target_link_libraries(pulp-view-core PRIVATE \"-framework CoreAudio\")\n",
            )
            self.write_mirror(
                root,
                "mirror/core/view/src/host.mm",
                b"#import <AudioToolbox/AudioToolbox.h>\n",
            )

            findings = verify_extraction.mirror_edge_findings(root)

            self.assertEqual(
                {(finding["path"], finding["rule"]) for finding in findings},
                {
                    ("mirror/core/view/src/meter.cpp", "mirror-product-include"),
                    ("mirror/core/view/src/meter.cpp", "mirror-product-symbol"),
                    ("mirror/core/view/CMakeLists.txt", "mirror-product-symbol"),
                    ("mirror/core/view/CMakeLists.txt", "mirror-audio-framework-link"),
                    ("mirror/core/view/src/host.mm", "mirror-product-include"),
                },
            )
            self.assertEqual(
                sum(finding["path"].endswith("meter.cpp") for finding in findings), 3
            )

    def test_pulp_namespace_is_scoped_to_the_mirror_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            content = b"namespace pulp::view { int x; }\n"
            self.write_mirror(root, "mirror/core/view/src/a.cpp", content)
            (root / "graphics").mkdir()
            (root / "graphics/a.cpp").write_bytes(content)

            findings = verify_extraction.scan_active_surface(root)

            self.assertEqual(
                {(finding["path"], finding["rule"]) for finding in findings},
                {("graphics/a.cpp", "pulp-public-namespace")},
            )

    def test_origin_records_are_strictly_validated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "blob-origins.json"
            good = {
                "mirror_path": "mirror/core/a.cpp",
                "git_blob_sha": "b" * 40,
                "pulp_commit": "c" * 40,
                "pulp_path": "core/a.cpp",
                "export_receipt": "provenance/mirror/receipts/first.json",
            }
            path.write_text(
                json.dumps(
                    {
                        "schema": "vellum.mirror-blob-origins.v1",
                        "pulp_repository": "Generous-Corp/pulp",
                        "records": [
                            good,
                            dict(good),
                            dict(good, mirror_path="graphics/a.cpp"),
                            dict(good, pulp_path="../secret"),
                            dict(good, pulp_commit="main"),
                            dict(good, extra="x"),
                        ],
                    }
                ),
                encoding="utf-8",
            )

            records, errors = verify_extraction.load_mirror_origin_records(path)

            self.assertEqual(len(records), 5)
            joined = "\n".join(errors)
            for expected in (
                "record 1 duplicates an earlier record",
                "record 2 mirror_path is outside mirror/",
                "record 3 pulp_path is not a repository-relative path",
                "record 4 pulp_commit is not a full commit SHA",
                "record 5 has unexpected keys",
            ):
                self.assertIn(expected, joined)
            missing, missing_errors = verify_extraction.load_mirror_origin_records(
                Path(temporary) / "absent.json"
            )
            self.assertEqual(missing, [])
            self.assertTrue(missing_errors)

    def test_origin_records_are_proved_against_pulp_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pulp = Path(temporary)

            def run(*args: str) -> str:
                return subprocess.check_output(
                    ["git", *args], cwd=pulp, text=True
                ).strip()

            run("init", "-q")
            (pulp / "core").mkdir()
            (pulp / "core/a.cpp").write_bytes(b"int a;\n")
            run("add", "core/a.cpp")
            run(
                "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                "commit", "-q", "-m", "origin",
            )
            commit = run("rev-parse", "HEAD")
            run("update-ref", "refs/remotes/origin/main", commit)
            record = {
                "mirror_path": "mirror/core/a.cpp",
                "git_blob_sha": verify_extraction.git_blob_sha(b"int a;\n"),
                "pulp_commit": commit,
                "pulp_path": "core/a.cpp",
                "export_receipt": "provenance/mirror/receipts/first.json",
            }

            self.assertEqual(verify_extraction.verify_origins_against_pulp(pulp, [record]), [])
            forged = dict(record, git_blob_sha=verify_extraction.git_blob_sha(b"int b;\n"))
            wrong_path = dict(record, pulp_path="core/missing.cpp")
            self.assertEqual(
                len(verify_extraction.verify_origins_against_pulp(pulp, [forged, wrong_path])),
                2,
            )

    def test_origin_proof_requires_origin_main_ref(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pulp = Path(temporary)
            subprocess.check_call(["git", "init", "-q", "-b", "main"], cwd=pulp)
            (pulp / "a.cpp").write_text("int a;\n")
            subprocess.check_call(["git", "add", "a.cpp"], cwd=pulp)
            subprocess.check_call(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "main"], cwd=pulp)
            commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=pulp, text=True).strip()
            record = {"mirror_path": "mirror/a.cpp", "git_blob_sha": verify_extraction.git_blob_sha(b"int a;\n"), "pulp_commit": commit, "pulp_path": "a.cpp", "export_receipt": "provenance/mirror/receipts/x.json"}
            errors = verify_extraction.verify_origins_against_pulp(pulp, [record])
            self.assertIn("has no refs/remotes/origin/main", errors[0])

    def test_origin_proof_rejects_head_commit_off_origin_main(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pulp = Path(temporary)
            run = lambda *args: subprocess.check_output(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args], cwd=pulp, text=True).strip()
            run("init", "-q", "-b", "main")
            (pulp / "a.cpp").write_text("int a;\n")
            run("add", "a.cpp"); run("commit", "-q", "-m", "main")
            main = run("rev-parse", "HEAD"); run("update-ref", "refs/remotes/origin/main", main)
            run("checkout", "-q", "-b", "side")
            (pulp / "a.cpp").write_text("int side;\n")
            run("add", "a.cpp"); run("commit", "-q", "-m", "side")
            side = run("rev-parse", "HEAD")
            record = {"mirror_path": "mirror/a.cpp", "git_blob_sha": verify_extraction.git_blob_sha(b"int side;\n"), "pulp_commit": side, "pulp_path": "a.cpp", "export_receipt": "provenance/mirror/receipts/x.json"}
            errors = verify_extraction.verify_origins_against_pulp(pulp, [record])
            self.assertIn("not on the checked-out Pulp main", errors[0])

    def test_required_provenance_workflow_does_not_defer_mirror_identity(self) -> None:
        workflow = MODULE_PATH.parents[2] / ".github/workflows/provenance.yml"
        self.assertNotIn("--defer-mirror-identity", workflow.read_text(encoding="utf-8"))

    def test_origin_records_must_cite_pulp_main(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pulp = Path(temporary)

            def run(*args: str) -> str:
                return subprocess.check_output(
                    ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                    cwd=pulp, text=True,
                ).strip()

            run("init", "-q", "-b", "main")
            (pulp / "a.cpp").write_bytes(b"int a;\n")
            run("add", "a.cpp")
            run("commit", "-q", "-m", "main")
            run("checkout", "-q", "-b", "side")
            (pulp / "core").mkdir()
            (pulp / "core/a.cpp").write_bytes(b"int side;\n")
            run("add", "core/a.cpp")
            run("commit", "-q", "-m", "side")
            side = run("rev-parse", "HEAD")
            run("checkout", "-q", "main")
            run("update-ref", "refs/remotes/origin/main", run("rev-parse", "main"))
            record = {
                "mirror_path": "mirror/core/a.cpp",
                "git_blob_sha": verify_extraction.git_blob_sha(b"int side;\n"),
                "pulp_commit": side,
                "pulp_path": "core/a.cpp",
                "export_receipt": "provenance/mirror/receipts/x.json",
            }
            errors = verify_extraction.verify_origins_against_pulp(pulp, [record])
            self.assertEqual(len(errors), 1)
            self.assertIn("not on the checked-out Pulp main", errors[0])

    def test_mirror_rejects_pulp_kept_view_audio_and_content_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_mirror(
                root,
                "mirror/core/view/src/panel.cpp",
                b"#include <pulp/view/view.hpp>\n"
                b"#include <pulp/view/eq_curve_view.hpp>\n"
                b"#include \"pulp/view/widgets/graph_editor_view.hpp\"\n"
                b"#include <pulp/view/waveform_overview_view.hpp>\n"
                b"#include <pulp/view/audio/meter.hpp>\n"
                b"#include <pulp/state/content_registry.hpp>\n"
                b"#include <pulp/state/store.hpp>\n",
            )
            self.write_mirror(root, "mirror/core/view/src/eq_curve_view.cpp", b"int eq;\n")
            self.write_mirror(
                root, "mirror/core/state/src/content_registry.cpp", b"int registry;\n"
            )
            self.write_mirror(root, "mirror/core/view/src/view.cpp", b"int view;\n")

            findings = verify_extraction.mirror_edge_findings(root)

            kept_includes = [
                finding["line"] for finding in findings
                if finding["rule"] == "mirror-pulp-kept-include"
            ]
            self.assertEqual(sorted(kept_includes), [2, 3, 4, 5, 6])
            self.assertEqual(
                sorted(
                    finding["path"] for finding in findings
                    if finding["rule"] == "mirror-pulp-kept-file"
                ),
                [
                    "mirror/core/state/src/content_registry.cpp",
                    "mirror/core/view/src/eq_curve_view.cpp",
                ],
            )

    def clone_repository(self, temporary: str) -> Path:
        clone = Path(temporary) / "vellum"
        subprocess.check_call(
            ["git", "clone", "-q", "--no-hardlinks", str(MODULE_PATH.parents[2]), str(clone)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for relative in (
            "tools/provenance/verify_extraction.py",
            "tools/provenance/verify_mirror_export.py",
            "provenance/active-source-boundary.json",
            "provenance/mirror/blob-origins.json",
            "provenance/mirror/link-policy.json",
            "provenance/mirror/receipts/index.json",
        ):
            source = MODULE_PATH.parents[2] / relative
            (clone / relative).parent.mkdir(parents=True, exist_ok=True)
            (clone / relative).write_bytes(source.read_bytes())
        return clone

    def test_fabricated_origin_record_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clone = self.clone_repository(temporary)
            content = b"namespace pulp::view { int forged; }\n"
            record = self.write_mirror(clone, "mirror/core/view/src/forged.cpp", content)
            record["pulp_commit"] = "f" * 40
            (clone / "provenance/mirror/blob-origins.json").write_text(
                json.dumps({
                    "schema": "vellum.mirror-blob-origins.v1",
                    "pulp_repository": "Generous-Corp/pulp",
                    "records": [record],
                }),
                encoding="utf-8",
            )
            subprocess.check_call(["git", "add", "-A"], cwd=clone)
            subprocess.check_call(
                ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                 "commit", "-q", "-m", "forge"],
                cwd=clone,
            )

            unproved = verify_extraction.verify(clone)
            deferred = verify_extraction.verify(clone, defer_mirror_identity=True)
            pulp = Path(temporary) / "pulp"
            pulp.mkdir()
            subprocess.check_call(["git", "init", "-q"], cwd=pulp)
            (pulp / "x").write_text("x\n")
            subprocess.check_call(["git", "add", "x"], cwd=pulp)
            subprocess.check_call(
                ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                 "commit", "-q", "-m", "pulp"],
                cwd=pulp,
            )
            main = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=pulp, text=True).strip()
            subprocess.check_call(["git", "update-ref", "refs/remotes/origin/main", main], cwd=pulp)
            proved = verify_extraction.verify(clone, pulp)

            self.assertEqual(unproved["status"], "fail")
            self.assertIn(
                "mirror root has 1 files but no Pulp checkout proved them; pass --pulp-root",
                unproved["errors"],
            )
            for report in (unproved, deferred, proved):
                self.assertEqual(report["status"], "fail")
                self.assertIn(
                    "blob-origin records name 1 receipts absent from the receipt index",
                    report["errors"],
                )
                self.assertIn(
                    "mirror export: mirror/ exists at HEAD without an export receipt",
                    report["errors"],
                )
            self.assertNotIn(
                "mirror root has 1 files but no Pulp checkout proved them; pass --pulp-root",
                deferred["errors"],
            )
            self.assertTrue(any("not on the checked-out Pulp main" in e for e in proved["errors"]))

    def test_declared_rules_must_match_enforced_rules(self) -> None:
        root = MODULE_PATH.parents[2]
        boundary = verify_extraction.configured_boundary(root)
        stale = dict(
            boundary,
            rules={
                "retired_paths_at_active_tip": 0,
                "active_files_matching_historical_seed_blobs": 0,
                "forbidden_namespace_or_product_references": 0,
                "open_extraction_debts": 0,
            },
        )
        with mock.patch.object(verify_extraction, "configured_boundary", return_value=stale):
            report = verify_extraction.verify(root)
        self.assertEqual(report["status"], "fail")
        self.assertIn("declared boundary rules differ from the enforced rules", report["errors"])

    def test_mirror_edge_rules_come_from_the_shared_table(self) -> None:
        table = json.loads(verify_extraction.MIRROR_EDGE_TABLE.read_text(encoding="utf-8"))
        self.assertEqual(table["schema"], "pulp.vellum.mirror-edge-patterns.v1")
        self.assertEqual(
            {name: rule.pattern for name, rule in verify_extraction.FORBIDDEN_MIRROR_EDGE_PATTERNS.items()},
            table["content_patterns"],
        )
        self.assertEqual(verify_extraction.MIRROR_EDGE_SUFFIXES, set(table["suffixes"]))
        self.assertTrue({"", ".md", ".ps1", ".sh", ".ts", ".tsx", ".yaml", ".yml"} <= verify_extraction.MIRROR_EDGE_SUFFIXES)

    def test_vendored_table_must_match_pulp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pulp = Path(directory)
            target = pulp / verify_extraction.PULP_MIRROR_EDGE_TABLE
            self.assertTrue(verify_extraction.mirror_edge_table_drift(pulp))
            target.parent.mkdir(parents=True)
            target.write_bytes(verify_extraction.MIRROR_EDGE_TABLE.read_bytes())
            self.assertEqual(verify_extraction.mirror_edge_table_drift(pulp), [])
            target.write_bytes(verify_extraction.MIRROR_EDGE_TABLE.read_bytes().replace(b"gpu_audio", b"gpuaudio", 1))
            self.assertTrue(verify_extraction.mirror_edge_table_drift(pulp))

    def test_unclassified_and_non_utf8_mirror_files_are_not_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_mirror(root, "mirror/core/view/src/x.weird", b"int x;\n")
            self.write_mirror(root, "mirror/core/view/src/y.cpp", b"\xff\xfe // pulp::audio\n")
            self.write_mirror(root, "mirror/external/fonts/Inter.ttf", b"\x00\x01pulp::audio")
            findings = verify_extraction.mirror_edge_findings(root)
        self.assertEqual(
            sorted((finding["rule"], finding["path"]) for finding in findings),
            [("mirror-product-symbol", "mirror/core/view/src/y.cpp"),
             ("mirror-unclassified-suffix", "mirror/core/view/src/x.weird")],
        )

    def test_vendored_table_is_the_pinned_blob(self) -> None:
        self.assertEqual(verify_extraction.mirror_edge_table_pin_errors(), [])
        pin = json.loads(verify_extraction.MIRROR_EDGE_TABLE_PIN.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            edited = Path(directory) / "table.json"
            edited.write_bytes(verify_extraction.MIRROR_EDGE_TABLE.read_bytes().replace(b"gpu_audio", b"gpuaudio", 1))
            self.assertTrue(verify_extraction.mirror_edge_table_pin_errors(table=edited))
            forged = Path(directory) / "pin.json"
            forged.write_text(json.dumps(dict(pin, pulp_path="tools/other.json")), encoding="utf-8")
            self.assertTrue(verify_extraction.mirror_edge_table_pin_errors(pin_path=forged))

    def test_pinned_blob_is_proved_against_a_pulp_checkout(self) -> None:
        def commit(pulp: Path, data: bytes) -> str:
            target = pulp / verify_extraction.PULP_MIRROR_EDGE_TABLE
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            subprocess.run(["git", "add", "-A"], cwd=pulp, check=True)
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "t"], cwd=pulp, check=True)
            return verify_extraction.git(pulp, "rev-parse", "HEAD")

        table = verify_extraction.MIRROR_EDGE_TABLE.read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            pulp, pin_path = Path(directory) / "pulp", Path(directory) / "pin.json"
            pulp.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=pulp, check=True)
            good = commit(pulp, table)
            pin = dict(json.loads(verify_extraction.MIRROR_EDGE_TABLE_PIN.read_text(encoding="utf-8")), pulp_commit=good)
            pin_path.write_text(json.dumps(pin), encoding="utf-8")
            self.assertEqual(verify_extraction.mirror_edge_table_pin_errors(pulp, pin_path=pin_path), [])

            # Vendored bytes and pin agree, but Pulp holds a different blob at the pinned commit.
            edited = table.replace(b"gpu_audio", b"gpuaudio", 1)
            vendored = Path(directory) / "vendored.json"
            vendored.write_bytes(edited)
            pin_path.write_text(json.dumps(dict(pin, git_blob=verify_extraction.git_blob_sha(edited))), encoding="utf-8")
            self.assertEqual(
                verify_extraction.mirror_edge_table_pin_errors(pulp, table=vendored, pin_path=pin_path),
                [f"Pulp's {verify_extraction.PULP_MIRROR_EDGE_TABLE} at {good} is blob "
                 f"{verify_extraction.git_blob_sha(table)}, not the pinned {verify_extraction.git_blob_sha(edited)}"],
            )

            # The checkout is not at the pinned commit.
            pin_path.write_text(json.dumps(pin), encoding="utf-8")
            moved = commit(pulp, table + b"\n")
            errors = verify_extraction.mirror_edge_table_pin_errors(pulp, pin_path=pin_path)
            self.assertIn(f"Pulp checkout {pulp} is at {moved}, not the pinned {good}", errors)

    def test_repository_passes_full_retirement_verification(self) -> None:
        root = MODULE_PATH.parents[2]
        report = verify_extraction.verify(root)
        self.assertEqual(report["status"], "pass", report["errors"])
        self.assertFalse(report["raw_seed_present_at_active_tip"])
        self.assertEqual(report["checks"]["retired_path_findings"], [])
        self.assertEqual(report["checks"]["active_historical_blob_matches"], [])
        self.assertEqual(report["checks"]["mirror_origin_findings"], [])
        self.assertEqual(report["checks"]["mirror_edge_findings"], [])


if __name__ == "__main__":
    unittest.main()
