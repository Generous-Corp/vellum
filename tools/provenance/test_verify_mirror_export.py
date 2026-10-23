#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from typing import Callable

try:
    from .git_test_environment import disable_auto_maintenance
except ImportError:  # Direct script execution.
    from git_test_environment import disable_auto_maintenance

disable_auto_maintenance()


MODULE_PATH = Path(__file__).with_name("verify_mirror_export.py")
SPEC = importlib.util.spec_from_file_location("verify_mirror_export", MODULE_PATH)
assert SPEC and SPEC.loader
verify_mirror_export = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify_mirror_export)
REPO = MODULE_PATH.parents[2]

PULP_FILES = {
    "core/canvas/CMakeLists.txt": b"add_library(pulp-canvas src/canvas.cpp)\n",
    "core/canvas/src/canvas.cpp": b"namespace pulp::canvas { int draw(); }\n",
    "core/canvas/include/pulp/canvas/canvas.hpp": b"#pragma once\n",
}
TOOL = b"#!/usr/bin/env python3\n"
SKIA_LINK = {"dependency": "skia", "source": "https://skia.googlesource.com/skia.git",
             "pin": "5" * 40, "asset_sha256": "6" * 64}
PULP_DEPENDENCY_MANIFEST = {"dependencies": [{"name": "Skia", "repository": SKIA_LINK["source"], "determinism": {
    "skia_commit": SKIA_LINK["pin"], "release_assets": {"darwin-arm64": {"sha256": SKIA_LINK["asset_sha256"]}}}}]}


def run(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=root, text=True, stderr=subprocess.DEVNULL,
    ).strip()


def write(root: Path, relative: str, content: bytes | str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if isinstance(content, bytes) else content.encode())
    return path


def dump(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


class Fixture:
    """A Pulp repository and a Vellum repository with one landed export."""

    def __init__(
        self,
        base: Path,
        *,
        mirror_overrides: dict[str, bytes] | None = None,
        base_files: dict[str, bytes] | None = None,
        mirror_prelude: list[dict[str, bytes]] | None = None,
        after_join: "Callable[[Fixture], None] | None" = None,
    ) -> None:
        self.pulp = base / "pulp"
        self.vellum = base / "vellum"
        self.pulp.mkdir()
        run(self.pulp, "init", "-q", "-b", "main")
        for path, content in PULP_FILES.items():
            write(self.pulp, path, content)
        write(self.pulp, "tools/scripts/vellum_closure.py", TOOL)
        write(self.pulp, "core/audio/engine.cpp", b"int audio;\n")
        write(self.pulp, "tools/deps/manifest.json", dump(PULP_DEPENDENCY_MANIFEST))
        run(self.pulp, "add", "-A")
        run(self.pulp, "commit", "-q", "-m", "pulp")
        self.pulp_sha = run(self.pulp, "rev-parse", "HEAD")
        run(self.pulp, "update-ref", "refs/remotes/origin/main", self.pulp_sha)

        self.vellum.mkdir()
        run(self.vellum, "init", "-q", "-b", "main")
        for name in ("link-policy.json", "blob-origins.json"):
            shutil.copy(REPO / "provenance/mirror" / name, write(self.vellum, f"provenance/mirror/{name}", ""))
        write(self.vellum, "provenance/mirror/receipts/index.json",
              dump({"schema": "vellum.mirror-receipt-index.v1", "receipts": []}))
        write(self.vellum, "README.md", "vellum\n")
        for path, content in (base_files or {}).items():
            write(self.vellum, path, content)
        run(self.vellum, "add", "-A")
        run(self.vellum, "commit", "-q", "-m", "base")
        self.base_sha = run(self.vellum, "rev-parse", "HEAD")

        # Projected mirror commit: what filter-repo produces for pulp_sha.
        run(self.vellum, "checkout", "-q", "--orphan", "mirror")
        run(self.vellum, "rm", "-rq", "--cached", ".")
        for child in list(self.vellum.iterdir()):
            if child.name != ".git":
                shutil.rmtree(child) if child.is_dir() else child.unlink()
        # Earlier side-history commits; paths are written as given, and the
        # mirror commit below replaces the whole tree with the export.
        for index, files in enumerate(mirror_prelude or []):
            for path, content in files.items():
                write(self.vellum, path, content)
            run(self.vellum, "add", "-A")
            run(self.vellum, "commit", "-q", "-m", f"mirror prelude {index}")
        for child in list(self.vellum.iterdir()):
            if child.name != ".git":
                shutil.rmtree(child) if child.is_dir() else child.unlink()
        mirrored = dict(PULP_FILES, **(mirror_overrides or {}))
        for path, content in mirrored.items():
            write(self.vellum, f"mirror/{path}", content)
        run(self.vellum, "add", "-A")
        run(self.vellum, "commit", "-q", "-m", "mirror")
        self.mirror_sha = run(self.vellum, "rev-parse", "HEAD")
        run(self.vellum, "checkout", "-q", "-f", "main")
        run(self.vellum, "merge", "-q", "--no-edit", "--allow-unrelated-histories", self.mirror_sha)
        if after_join is not None:
            after_join(self)

        self.export_id = f"20261010-{self.pulp_sha[:12]}"
        self.receipt_path = f"provenance/mirror/receipts/{self.export_id}.json"
        closure = {
            "schema": "pulp.vellum.closure.v1",
            "source_root": None,
            "build_dir": None,
            "configuration": "Release",
            "targets": [{
                "name": "pulp-canvas", "type": "STATIC_LIBRARY",
                "sources": ["core/canvas/src/canvas.cpp"], "include_dirs": [],
                "compile_definitions": ["PULP_HAS_SKIA=1", "PULP_HAS_TEXT_SHAPING=1"], "link_targets": [],
                "external_link_inputs": [SKIA_LINK],
            }],
            "missing_targets": [],
            "files": sorted(PULP_FILES),
            "external_inputs": [],
            "public_include_dirs": [],
            "compile_definitions": ["PULP_HAS_SKIA=1", "PULP_HAS_TEXT_SHAPING=1"],
            "transitive_link_targets": [],
            "resources": [],
            "generated_inputs": [],
            "configure": {
                "options": {
                    "PULP_ENABLE_GPU": "ON", "PULP_HAS_SKIA": "ON",
                    "PULP_TEXT_SHAPING": "ON", "CMAKE_BUILD_TYPE": "Release",
                    "CMAKE_SYSTEM_NAME": "Darwin", "CMAKE_SYSTEM_PROCESSOR": "arm64",
                },
                "options_sha256": hashlib.sha256(json.dumps({
                    "PULP_ENABLE_GPU": "ON", "PULP_HAS_SKIA": "ON",
                    "PULP_TEXT_SHAPING": "ON", "CMAKE_BUILD_TYPE": "Release",
                    "CMAKE_SYSTEM_NAME": "Darwin", "CMAKE_SYSTEM_PROCESSOR": "arm64",
                }, sort_keys=True).encode()).hexdigest(),
                "identity": {"gpu": True, "skia": True, "text_shaping": True,
                             "build_type": "Release", "system": "Darwin", "processor": "arm64"},
            },
        }
        self.closure_path = f"provenance/mirror/closures/{self.export_id}/canvas.json"
        write(self.vellum, self.closure_path, dump(closure))
        commit_map = f"provenance/mirror/commit-maps/{self.export_id}.txt"
        write(self.vellum, commit_map, f"old new\n{self.pulp_sha} {self.mirror_sha}\n")
        files = [
            {
                "mirror_path": f"mirror/{path}",
                "pulp_path": path,
                "mode": "100644",
                "blob": run(self.vellum, "rev-parse", f"{self.mirror_sha}:mirror/{path}"),
            }
            for path in sorted(mirrored)
        ]
        self.receipt = {
            "schema": "pulp.vellum.export-receipt.v2",
            "export_id": self.export_id,
            "pulp_repository": "Generous-Corp/pulp",
            "pulp_sha": self.pulp_sha,
            "mirror_ref": "refs/heads/mirror/pulp-framework",
            "mirror_sha": self.mirror_sha,
            "mirror_tree": run(self.vellum, "rev-parse", f"{self.mirror_sha}:mirror"),
            "rename": {"from_prefix": "", "to_prefix": "mirror/"},
            "commit_map": {"path": commit_map, "sha256": self.sha(commit_map)},
            "units": [{
                "name": "canvas", "closure_manifest": self.closure_path,
                "closure_sha256": self.sha(self.closure_path), "oracle_class": "identity",
                "configure": {"identity": self.receipt_configure_identity(), "options_sha256": self.receipt_configure_sha(), "label": ""},
            }],
            "files": files,
            "generated_inputs": [],
            "tool": {
                "path": "tools/scripts/vellum_closure.py",
                "pulp_blob": run(self.pulp, "rev-parse", f"{self.pulp_sha}:tools/scripts/vellum_closure.py"),
            },
            "created_at": "2026-10-10T18:00:00Z",
        }
        self.origins = {
            "schema": "vellum.mirror-blob-origins.v1",
            "pulp_repository": "Generous-Corp/pulp",
            "records": [
                {
                    "mirror_path": row["mirror_path"], "git_blob_sha": row["blob"],
                    "pulp_commit": self.pulp_sha, "pulp_path": row["pulp_path"],
                    "export_receipt": self.receipt_path,
                }
                for row in files
            ],
        }
        self.land()

    def receipt_configure_identity(self) -> dict[str, object]:
        return {"gpu": True, "skia": True, "text_shaping": True,
                "build_type": "Release", "system": "Darwin", "processor": "arm64"}

    def receipt_configure_sha(self) -> str:
        options = {"PULP_ENABLE_GPU": "ON", "PULP_HAS_SKIA": "ON",
                   "PULP_TEXT_SHAPING": "ON", "CMAKE_BUILD_TYPE": "Release",
                   "CMAKE_SYSTEM_NAME": "Darwin", "CMAKE_SYSTEM_PROCESSOR": "arm64"}
        return hashlib.sha256(json.dumps(options, sort_keys=True).encode()).hexdigest()

    def sha(self, relative: str) -> str:
        return hashlib.sha256((self.vellum / relative).read_bytes()).hexdigest()

    def land(self) -> None:
        write(self.vellum, self.receipt_path, dump(self.receipt))
        write(self.vellum, "provenance/mirror/blob-origins.json", dump(self.origins))
        write(self.vellum, "provenance/mirror/receipts/index.json",
              dump({"schema": "vellum.mirror-receipt-index.v1", "receipts": [self.receipt_path]}))
        run(self.vellum, "add", "-A")
        run(self.vellum, "commit", "-q", "--allow-empty", "-m", "land")

    def reland(self, **changes: object) -> None:
        self.receipt.update(changes)
        self.land()

    def errors(self, *, pulp: bool = False) -> list[str]:
        report = verify_mirror_export.verify(self.vellum, self.pulp if pulp else None)
        return report["errors"]


class VerifyMirrorExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def assertError(self, errors: list[str], fragment: str) -> None:
        self.assertTrue(any(fragment in e for e in errors), f"{fragment!r} not in {errors}")

    def test_repository_receipts_all_verify(self) -> None:
        receipts = verify_mirror_export.landed_receipts(REPO)
        has_mirror = verify_mirror_export.git_ok(REPO, "cat-file", "-e", "HEAD:mirror")
        self.assertEqual(bool(receipts), has_mirror, "mirror/ and the receipt index disagree")
        report = verify_mirror_export.verify(REPO)
        self.assertEqual(report["status"], "pass", report["errors"])
        self.assertEqual(report["receipts"], receipts)

    def test_repository_receipts_all_verify_with_pulp_identity(self) -> None:
        receipts = verify_mirror_export.landed_receipts(REPO)
        pulp_root_env = os.environ.get("MIRROR_PULP_ROOT")
        if receipts and not pulp_root_env:
            # The required provenance lanes check out the newest receipt's Pulp
            # commit and set MIRROR_PULP_ROOT whenever a receipt is landed. In
            # CI a missing checkout is a broken lane, not a reason to skip.
            if os.environ.get("CI", "").lower() == "true":
                self.fail("CI is set but MIRROR_PULP_ROOT names no Pulp checkout; the Pulp identity proof did not run")
            self.skipTest("Pulp identity is proved where MIRROR_PULP_ROOT names a Pulp checkout")
        pulp_root = Path(pulp_root_env).resolve() if pulp_root_env else None
        report = verify_mirror_export.verify(REPO, pulp_root)
        self.assertEqual(report["status"], "pass", report["errors"])
        self.assertEqual(report["receipts"], receipts)
        # The equivalent of `verify_mirror_export.py --require-pulp-identity`,
        # applied to every landed receipt rather than to any one of them.
        self.assertEqual(report["pulp_identity_checked"], bool(receipts))
        for receipt_path in receipts:
            receipt = json.loads((REPO / receipt_path).read_text(encoding="utf-8"))
            self.assertEqual(
                verify_mirror_export.verify_receipt_against_pulp(pulp_root, receipt),
                [],
                receipt_path,
            )

    def test_valid_landing_passes_with_pulp_identity(self) -> None:
        fixture = Fixture(self.base)
        report = verify_mirror_export.verify(fixture.vellum, fixture.pulp)
        self.assertEqual(report["errors"], [])
        self.assertTrue(report["pulp_identity_checked"])

    def test_hand_edit_under_mirror_breaks_tree_equality(self) -> None:
        fixture = Fixture(self.base)
        write(fixture.vellum, "mirror/core/canvas/src/canvas.cpp", b"edited\n")
        run(fixture.vellum, "commit", "-qam", "edit")
        self.assertError(fixture.errors(), "HEAD:mirror is not the newest receipt's mirror tree")

    def test_mirror_without_receipt_fails(self) -> None:
        fixture = Fixture(self.base)
        write(fixture.vellum, "provenance/mirror/receipts/index.json",
              dump({"schema": "vellum.mirror-receipt-index.v1", "receipts": []}))
        (fixture.vellum / fixture.receipt_path).unlink()
        run(fixture.vellum, "commit", "-qam", "drop receipt")
        self.assertError(fixture.errors(), "mirror/ exists at HEAD without an export receipt")

    def test_receipt_files_must_equal_projected_tree(self) -> None:
        fixture = Fixture(self.base)
        fixture.reland(files=fixture.receipt["files"][:-1])
        self.assertError(fixture.errors(), "files differ from the projected tree of mirror_sha")

    def test_mirror_bytes_that_differ_from_pulp_fail_identity(self) -> None:
        fixture = Fixture(
            self.base, mirror_overrides={"core/canvas/src/canvas.cpp": b"not pulp\n"}
        )
        self.assertEqual(fixture.errors(), [])
        self.assertError(
            fixture.errors(pulp=True), "mirror/core/canvas/src/canvas.cpp is not Pulp"
        )

    def test_closure_files_must_equal_exported_set(self) -> None:
        fixture = Fixture(self.base)
        closure = json.loads((fixture.vellum / fixture.closure_path).read_text())
        closure["files"].append("core/canvas/src/extra.cpp")
        write(fixture.vellum, fixture.closure_path, dump(closure))
        fixture.reland(units=[dict(fixture.receipt["units"][0], closure_sha256=fixture.sha(fixture.closure_path))])
        self.assertError(fixture.errors(), "closure files differ from the exported file set")

    def test_link_closure_gate_rejects_product_and_unknown_edges(self) -> None:
        fixture = Fixture(self.base)
        closure = json.loads((fixture.vellum / fixture.closure_path).read_text())
        closure["targets"][0]["link_targets"] = ["pulp-format-core", "skia"]
        closure["transitive_link_targets"] = ["pulp::audio"]
        closure["missing_targets"] = ["pulp-render"]
        closure["compile_definitions"].append("PULP_HAS_VST3=1")
        write(fixture.vellum, fixture.closure_path, dump(closure))
        fixture.reland(units=[dict(fixture.receipt["units"][0], closure_sha256=fixture.sha(fixture.closure_path))])
        errors = fixture.errors()
        for fragment in (
            "forbidden link target pulp-format-core",
            "transitive unmirrored target pulp-audio",
            "link target skia is neither exported nor allowed",
            "missing targets ['pulp-render']",
            "forbidden compile definition PULP_HAS_VST3=1",
        ):
            self.assertError(errors, fragment)

    def test_link_policy_allows_reviewed_external_and_exported_targets(self) -> None:
        policy = json.loads((REPO / "provenance/mirror/link-policy.json").read_text())
        policy["allowed_external_targets"] = ["skia"]
        closures = {
            "canvas": {"targets": [{"name": "pulp-canvas", "link_targets": ["skia"]}],
                       "files": ["core/canvas/a.cpp"], "compile_definitions": []},
            "view": {"targets": [{"name": "pulp-view-core", "link_targets": ["pulp-canvas"]}],
                     "files": ["core/view/a.cpp", "core/audio/b.cpp"], "compile_definitions": []},
        }
        self.assertEqual(
            verify_mirror_export.link_closure_findings(closures, policy),
            ["view: closure file under a forbidden Pulp root: core/audio/b.cpp"],
        )

    def test_commit_map_and_origins_are_required(self) -> None:
        fixture = Fixture(self.base)
        commit_map = fixture.receipt["commit_map"]["path"]
        write(fixture.vellum, commit_map, "old new\n")
        fixture.origins["records"] = fixture.origins["records"][1:]
        fixture.reland(commit_map={"path": commit_map, "sha256": fixture.sha(commit_map)})
        errors = fixture.errors()
        self.assertError(errors, "commit map lacks the row")
        self.assertError(errors, "1 files lack a blob-origin record for this receipt")

    def test_receipt_shape_is_strict(self) -> None:
        fixture = Fixture(self.base)
        fixture.reland(pulp_repository="/Users/someone/pulp", rename={"from_prefix": "", "to_prefix": ""})
        errors = fixture.errors()
        self.assertError(errors, "pulp_repository must be 'Generous-Corp/pulp'")
        self.assertError(errors, "rename must be")

    def test_index_must_match_receipt_files_on_disk(self) -> None:
        fixture = Fixture(self.base)
        write(fixture.vellum, "provenance/mirror/receipts/20261011-000000000000.json", "{}\n")
        run(fixture.vellum, "add", "-A")
        run(fixture.vellum, "commit", "-qm", "stray")
        self.assertError(fixture.errors(), "receipt files on disk differ from the receipt index")

    def test_generated_input_dict_entry_is_valid(self) -> None:
        self.assertEqual(
            verify_mirror_export.validate_generated_input(
                {
                    "path": "@build/generated/value.hpp",
                    "sha256": "0" * 64,
                    "producer_paths": ["core/runtime/CMakeLists.txt"],
                },
                "fixture generated input",
            ),
            [],
        )

    def test_legacy_string_generated_input_is_rejected(self) -> None:
        errors = verify_mirror_export.validate_generated_input(
            "@build/generated/value.hpp", "fixture generated input"
        )
        self.assertError(errors, "legacy string entries are rejected")

    def test_generated_input_missing_path_is_rejected(self) -> None:
        errors = verify_mirror_export.validate_generated_input(
            {
                "sha256": "0" * 64,
                "producer_paths": ["core/runtime/CMakeLists.txt"],
            },
            "fixture generated input",
        )
        self.assertError(errors, "must contain path, sha256, producer_paths")

    def test_generated_input_absolute_paths_are_rejected(self) -> None:
        errors = verify_mirror_export.validate_generated_input(
            {
                "path": "/Users/attacker/build/value.hpp",
                "sha256": "0" * 64,
                "producer_paths": ["/Users/attacker/source/CMakeLists.txt"],
            },
            "fixture generated input",
        )
        self.assertError(errors, ".path must be a relative @build/ path")
        self.assertError(errors, "producer_paths[0] must be repository-relative")

    def test_generated_input_parent_escape_is_rejected(self) -> None:
        errors = verify_mirror_export.validate_generated_input(
            {
                "path": "@build/../../etc/passwd",
                "sha256": "0" * 64,
                "producer_paths": ["core/runtime/CMakeLists.txt"],
            },
            "fixture generated input",
        )
        self.assertError(errors, ".path must be a relative @build/ path")

    def test_external_input_cache_relative_identity_is_valid(self) -> None:
        pin = "a" * 40
        self.assertEqual(
            verify_mirror_export.validate_external_inputs(
                [{"path": "@fetchcontent/mbedtls/include/mbedtls.h", "source": "https://github.com/Mbed-TLS/mbedtls", "pin": pin, "sha256": "0" * 64}],
                "fixture external inputs",
            ),
            [],
        )

    def test_external_input_requires_repository_and_commit_pin(self) -> None:
        errors = verify_mirror_export.validate_external_inputs(
            [{"path": "@fetchcontent/mbedtls-release-3.2.12/include/a.h", "source": "build-tree", "pin": "release-3.2.12"}],
            "fixture external inputs",
        )
        self.assertError(errors, "source must be a known repository URL")
        self.assertError(errors, "pin must be a 40-hex commit SHA")

    def test_external_input_embedded_pin_must_match(self) -> None:
        errors = verify_mirror_export.validate_external_inputs(
            [{"path": "@fetchcontent/mbedtls-" + "a" * 40 + "/include/a.h", "source": "https://github.com/Mbed-TLS/mbedtls", "pin": "b" * 40}],
            "fixture external inputs",
        )
        self.assertError(errors, "embedded pin must equal pin")

    def test_external_input_positional_pin_rejects_tag_and_short_hash(self) -> None:
        for positional in ("release-3.2.12", "a" * 12):
            errors = verify_mirror_export.validate_external_inputs(
                [{"path": f"@external/sdl3/{positional}/x.h", "source": "https://github.com/libsdl-org/SDL", "pin": "b" * 40}],
                "fixture external inputs",
            )
            self.assertError(errors, "@external pin segment must equal pin")

    def test_external_input_positional_pin_must_equal_full_pin(self) -> None:
        pin = "a" * 40
        errors = verify_mirror_export.validate_external_inputs(
            [{"path": "@external/sdl3/" + "b" * 40 + "/x.h", "source": "https://github.com/libsdl-org/SDL", "pin": pin}],
            "fixture external inputs",
        )
        self.assertError(errors, "@external pin segment must equal pin")

    def test_external_input_patched_copy_requires_patch_identity(self) -> None:
        pin, key, copy_tree = "a" * 40, "b" * 64, "c" * 64
        base_modification = ""
        recipe = f"patch-set:{key}\nbase-modification:{base_modification}\ncopy-tree:{copy_tree}\n"
        formula = "sha256(utf8('patch-set:' + patch_set.key + '\\nbase-modification:' + base_modification + '\\ncopy-tree:' + copy_tree + '\\n'))"
        patch = hashlib.sha256(recipe.encode()).hexdigest()
        row = {
            "path": f"@external/choc/{pin}+patch-{patch[:16]}/choc.h",
            "source": "https://github.com/danielraffel/choc.git",
            "pin": pin,
            "patched": True,
            "patch": patch,
            "sha256": "d" * 64,
            "patch_set": {"pulp_path": "tools/cmake/PulpPatchChoc.cmake", "pulp_blob": "e" * 40, "key": key},
            "base_modification": base_modification,
            "copy_tree": copy_tree,
            "patch_recipe": {"id": "pulp.vellum.patch-id.v1", "formula": formula, "replay": {"method": verify_mirror_export.REPLAY_METHOD, "patch_set_blob": "e" * 40, "functions": ["pulp_patch_choc_v8"]}},
        }
        self.assertEqual(
            verify_mirror_export.validate_external_inputs([row], "fixture"), []
        )
        row["patch"] = "f" * 64
        row["path"] = f"@external/choc/{pin}+patch-{row['patch'][:16]}/choc.h"
        self.assertError(
            verify_mirror_export.validate_external_inputs([row], "fixture"),
            "does not recompute",
        )

    def test_external_input_patch_negative_cases(self) -> None:
        pin, key, copy_tree = "a" * 40, "b" * 64, "c" * 64
        recipe = f"patch-set:{key}\nbase-modification:\ncopy-tree:{copy_tree}\n"
        formula = "sha256(utf8('patch-set:' + patch_set.key + '\\nbase-modification:' + base_modification + '\\ncopy-tree:' + copy_tree + '\\n'))"
        patch = hashlib.sha256(recipe.encode()).hexdigest()
        base = {
            "path": f"@external/choc/{pin}+patch-{patch[:16]}/choc.h",
            "source": "https://github.com/danielraffel/choc.git", "pin": pin,
            "patched": True, "patch": patch, "sha256": "d" * 64,
            "patch_set": {"pulp_path": "tools/cmake/PulpPatchChoc.cmake", "pulp_blob": "e" * 40, "key": key},
            "base_modification": "", "copy_tree": copy_tree,
            "patch_recipe": {"id": "pulp.vellum.patch-id.v1", "formula": formula, "replay": {"method": verify_mirror_export.REPLAY_METHOD, "patch_set_blob": "e" * 40, "functions": ["pulp_patch_choc_v8"]}},
        }
        cases = [
            ({**base, "path": f"@external/choc/{pin}/choc.h"}, "path marker"),
            ({**base, "patch": "not-a-digest"}, "patch must be a SHA-256"),
            ({k: v for k, v in base.items() if k != "pin"}, "pin must be a 40-hex"),
            ({k: v for k, v in base.items() if k not in ("patched",)}, "patch fields require patched=true"),
            ({k: v for k, v in base.items() if k != "sha256"}, "sha256 must be a SHA-256"),
            ({k: v for k, v in base.items() if k != "patch_set"}, "patch_set must contain"),
            ({**base, "patch_recipe": {"id": "wrong", "formula": formula, "replay": {"method": verify_mirror_export.REPLAY_METHOD, "patch_set_blob": "e" * 40, "functions": ["pulp_patch_choc_v8"]}}}, "patch_recipe.id"),
            ({**base, "base_modification": "a" * 64}, "base_modification must be empty"),
            ({**base, "path": "@fetchcontent/choc/choc.h"}, "must be @external"),
        ]
        for row, fragment in cases:
            self.assertError(verify_mirror_export.validate_external_inputs([row], "fixture"), fragment)

    def test_external_input_patch_set_is_checked_against_pulp_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pulp = Path(temporary)
            (pulp / "tools/cmake").mkdir(parents=True)
            patch_bytes = b"function(pulp_patch_choc_v8 x)\nendfunction()\n"
            patch_path = pulp / "tools/cmake/PulpPatchChoc.cmake"
            patch_path.write_bytes(patch_bytes)
            run(pulp, "init", "-q", "-b", "main")
            run(pulp, "add", "-A")
            run(pulp, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "pulp")
            pulp_sha = run(pulp, "rev-parse", "HEAD")
            run(pulp, "update-ref", "refs/remotes/origin/main", pulp_sha)
            pulp_blob = run(pulp, "rev-parse", f"{pulp_sha}:tools/cmake/PulpPatchChoc.cmake")
            key = hashlib.sha256(patch_bytes).hexdigest()
            copy_tree = "c" * 64
            recipe = f"patch-set:{key}\nbase-modification:\ncopy-tree:{copy_tree}\n"
            patch = hashlib.sha256(recipe.encode()).hexdigest()
            formula = "sha256(utf8('patch-set:' + patch_set.key + '\\nbase-modification:' + base_modification + '\\ncopy-tree:' + copy_tree + '\\n'))"
            row = {
                "path": f"@external/choc/{'a' * 40}+patch-{patch[:16]}/choc.h",
                "source": "https://github.com/danielraffel/choc.git", "pin": "a" * 40,
                "sha256": "d" * 64, "patched": True, "patch": patch,
                "patch_set": {"pulp_path": "tools/cmake/PulpPatchChoc.cmake", "pulp_blob": pulp_blob, "key": key},
                "base_modification": "", "copy_tree": copy_tree,
                "patch_recipe": {"id": "pulp.vellum.patch-id.v1", "formula": formula, "replay": {"method": verify_mirror_export.REPLAY_METHOD, "patch_set_blob": pulp_blob, "functions": ["pulp_patch_choc_v8"]}},
            }
            self.assertEqual(verify_mirror_export.validate_external_inputs([row], "fixture", pulp_root=pulp, pulp_sha=pulp_sha), [])
            for mutation, fragment in (
                ({**row, "patch_set": {**row["patch_set"], "pulp_blob": "f" * 40}}, "pulp_blob"),
                ({**row, "patch_set": {**row["patch_set"], "key": "e" * 64}}, "key"),
                ({**row, "patch_set": {**row["patch_set"], "pulp_path": "missing.cmake"}}, "pulp_blob"),
                ({**row, "patch_recipe": {**row["patch_recipe"], "replay": {**row["patch_recipe"]["replay"], "functions": ["missing_patch"]}}}, "replay function missing_patch"),
            ):
                self.assertError(verify_mirror_export.validate_external_inputs([mutation], "fixture", pulp_root=pulp, pulp_sha=pulp_sha), fragment)

    def test_external_input_accepts_exact_googlesource_hosts(self) -> None:
        pin = "a" * 40
        for host in ("skia.googlesource.com/skia", "dawn.googlesource.com/dawn"):
            self.assertEqual(
                verify_mirror_export.validate_external_inputs(
                    [{"path": f"@external/source/{pin}/include/a.h", "source": f"https://{host}", "pin": pin, "sha256": "0" * 64}],
                    "fixture external inputs",
                ),
                [],
            )

    def test_external_input_rejects_other_googlesource_hosts(self) -> None:
        errors = verify_mirror_export.validate_external_inputs(
            [{"path": "@external/source/" + "a" * 40 + "/include/a.h", "source": "https://other.googlesource.com/source", "pin": "a" * 40}],
            "fixture external inputs",
        )
        self.assertError(errors, "source must be a known repository URL")

    def test_external_input_parent_escape_is_rejected(self) -> None:
        errors = verify_mirror_export.validate_external_inputs(
            [{"path": "@build/../..", "source": "https://github.com/Mbed-TLS/mbedtls", "pin": "a" * 40}],
            "fixture external inputs",
        )
        self.assertError(errors, "cache-relative identity")

    def test_embedded_host_paths_are_rejected(self) -> None:
        for value in ("-I/Users/attacker/include", "prefix//Users/attacker/cache", "FOO=/Volumes/attacker/cache", "-I/home/attacker/include", "x=/private/attacker", "FOO=C:/attacker/cache", r"prefix=\\server\share\x", "prefix file:///Users/attacker/x", "prefix file:///etc/x"):
            self.assertTrue(verify_mirror_export.host_path_violations(value, "fixture"), value)
        self.assertEqual(
            verify_mirror_export.host_path_violations(
                "@external/skia/" + "a" * 40 + "/build/include/include/private/SkAPI.h", "fixture"
            ),
            [],
        )
        for value in (
            "@external/skia/" + "a" * 40 + "//Users/daniel/x/include/private/SkAPI.h",
            "X=@external/skia/" + "a" * 40 + "/a /private/var/folders/x/include/private/SkAPI.h",
        ):
            self.assertTrue(verify_mirror_export.host_path_violations(value, "fixture"), value)

    def test_malformed_receipts_yield_findings_not_tracebacks(self) -> None:
        receipts = verify_mirror_export.landed_receipts(REPO)
        if not receipts:
            self.skipTest("no landed receipt to malform")
        receipt_path = receipts[-1]
        good = json.loads((REPO / receipt_path).read_text(encoding="utf-8"))
        policy, _ = verify_mirror_export.load_policy(REPO)
        origins = json.loads((REPO / verify_mirror_export.ORIGINS_PATH).read_text(encoding="utf-8"))["records"]
        head = verify_mirror_export.git(REPO, "rev-parse", "HEAD")
        wrong_types = (None, 7, "x", [], {}, [None], [7], [{}])

        def malformed():
            for key in sorted(good):
                for value in wrong_types:
                    yield f"{key}={value!r}", {**good, key: value}
            for key in ("units", "files", "generated_inputs"):
                for index in range(min(1, len(good[key]))):
                    for field in sorted(good[key][index]):
                        for value in wrong_types:
                            rows = json.loads(json.dumps(good[key]))
                            rows[index][field] = value
                            yield f"{key}[{index}].{field}={value!r}", {**good, key: rows}

        for name, receipt in malformed():
            # A different string can be a legal value (a unit is free to be
            # renamed); every other wrong type must be named as an error.
            must_fail = not name.endswith("='x'")
            with self.subTest(malformed=name):
                try:
                    errors = verify_mirror_export.verify_receipt(
                        REPO, receipt_path, receipt, policy, origins, newest=True, head=head)
                except Exception as exc:  # noqa: BLE001 - any traceback is the finding
                    self.fail(f"{type(exc).__name__}: {exc}")
                self.assertTrue(all(isinstance(e, str) for e in errors), errors)
                if must_fail:
                    self.assertTrue(errors, "accepted")

    def test_host_paths_are_refused_whatever_their_root(self) -> None:
        # The check is an allowlist: a path-bearing token must be cache- or
        # repository-relative, so a host root it has never heard of is refused.
        for value in (
            "/opt/homebrew/include", "-I/opt/homebrew/include", "-isystem/usr/local/include",
            "/tmp/vellum-build/x.hpp", "FOO=/var/folders/kb/T/x", "/__w/vellum/vellum/x",
            "-F/Library/Frameworks", "-Wl,-rpath,/usr/local/lib", "--sysroot=/opt/sdk",
            "@build/../../../../etc/x", "-I@build/../../../../etc/x", "core/../../etc/x",
            "~/cache", "@/x", "C:/x", "file:///etc/x",
        ):
            with self.subTest(value=value):
                self.assertError(verify_mirror_export.host_path_violations(value, "fixture"),
                                 "contains an absolute host path")
        for value in (
            "@external/skia/" + "a" * 40 + "/build/include/include/private/SkAPI.h",
            "@build/core/runtime/generated/Release/pulp/runtime/build_info.hpp",
            "-Icore/canvas/include", "-isystem@external/mbed-tls/" + "a" * 40 + "/include",
            "PULP_HAS_SKIA=1", "https://github.com/google/skia", "mirror/", "<catch2/catch_test_macros.hpp>",
        ):
            with self.subTest(value=value):
                self.assertEqual(verify_mirror_export.host_path_violations(value, "fixture"), [])

    def test_embedded_host_paths_in_dict_keys_are_rejected(self) -> None:
        self.assertTrue(verify_mirror_export.host_path_violations({"/Users/attacker/key": "ok"}, "fixture"))

    def test_non_dict_targets_fail_through_verify(self) -> None:
        fixture = Fixture(self.base)
        closure = json.loads((fixture.vellum / fixture.closure_path).read_text())
        closure["targets"] = [1, "x", None]
        write(fixture.vellum, fixture.closure_path, dump(closure))
        fixture.receipt["units"][0]["closure_sha256"] = fixture.sha(fixture.closure_path)
        fixture.reland()
        self.assertError(fixture.errors(), "targets[0] must be an object")

    def test_missing_pulp_origin_main_ref_fails_export_identity(self) -> None:
        fixture = Fixture(self.base)
        run(fixture.pulp, "update-ref", "-d", "refs/remotes/origin/main")
        self.assertError(fixture.errors(pulp=True), "has no refs/remotes/origin/main")

    def test_export_commit_reachable_from_head_but_not_origin_main_fails(self) -> None:
        fixture = Fixture(self.base)
        run(fixture.pulp, "checkout", "-q", "-b", "side")
        write(fixture.pulp, "side.cpp", "int side;\n")
        run(fixture.pulp, "add", "side.cpp")
        run(fixture.pulp, "commit", "-q", "-m", "side")
        side = run(fixture.pulp, "rev-parse", "HEAD")
        receipt = dict(fixture.receipt, pulp_sha=side)
        errors = verify_mirror_export.verify_receipt_against_pulp(fixture.pulp, receipt)
        self.assertError(errors, "not reachable from Pulp main")

    def test_external_input_host_path_is_rejected(self) -> None:
        errors = verify_mirror_export.validate_external_inputs(
            [{"path": "/Users/attacker/Library/Caches/Pulp/fetchcontent-src/foo/a.h"}],
            "fixture external inputs",
        )
        self.assertError(errors, "cache-relative identity")

    def test_closure_public_include_host_path_is_rejected(self) -> None:
        errors = verify_mirror_export.validate_closure_manifest(
            {
                "external_inputs": [],
                "files": [],
                "resources": [],
                "public_include_dirs": ["/Users/attacker/Library/Caches/Pulp/skia/include"],
                "targets": [],
            },
            "fixture closure",
        )
        self.assertError(errors, "absolute host path")

    def test_end_to_end_closure_host_path_guard_is_required(self) -> None:
        fixture = Fixture(self.base)
        closure = json.loads((fixture.vellum / fixture.closure_path).read_text())
        closure["public_include_dirs"] = ["/Users/attacker/include"]
        write(fixture.vellum, fixture.closure_path, dump(closure))
        fixture.receipt["units"][0]["closure_sha256"] = fixture.sha(fixture.closure_path)
        fixture.reland()
        self.assertError(fixture.errors(), "absolute host path")
        with mock.patch.object(verify_mirror_export, "validate_closure_manifest", return_value=[]):
            self.assertEqual(fixture.errors(), [])

    def test_end_to_end_receipt_host_path_guard_is_required(self) -> None:
        fixture = Fixture(self.base)
        fixture.receipt["units"][0]["name"] = "FOO=/Users/attacker"
        fixture.reland()
        self.assertError(fixture.errors(), "absolute host path")

    def test_configure_digest_and_identity_are_checked(self) -> None:
        fixture = Fixture(self.base)
        closure = json.loads((fixture.vellum / fixture.closure_path).read_text())
        closure["configure"]["options_sha256"] = "0" * 64
        closure["configure"]["identity"]["system"] = ""
        errors = verify_mirror_export.validate_closure_manifest(closure, "fixture closure", is_canvas=True)
        self.assertError(errors, "options_sha256 does not match")
        self.assertError(errors, "identity.system")

    def test_cpu_only_canvas_is_refused_for_g2e(self) -> None:
        fixture = Fixture(self.base)
        closure = json.loads((fixture.vellum / fixture.closure_path).read_text())
        closure["configure"]["label"] = "cpu-only"
        errors = verify_mirror_export.validate_closure_manifest(closure, "fixture closure", is_canvas=True)
        self.assertError(errors, "cpu-only is refused for G2e")

    def test_producer_dependency_is_validated(self) -> None:
        valid = {
            "path": "@build/_deps/sdl3-build/include/revision.h",
            "sha256": "0" * 64,
            "producer_paths": [],
            "producer_dependency": {
                "name": "sdl3", "source": "https://github.com/libsdl-org/SDL.git", "pin": "a" * 40,
            },
        }
        self.assertEqual(verify_mirror_export.validate_generated_input(valid, "fixture"), [])
        invalid = dict(valid, producer_dependency={"name": "sdl3", "source": "build-tree", "pin": "release"})
        errors = verify_mirror_export.validate_generated_input(invalid, "fixture")
        self.assertError(errors, "source must be a known repository URL")
        self.assertError(errors, "pin must be a 40-hex commit SHA")

    def test_build_timestamp_normalization_is_the_only_accepted_one(self) -> None:
        row = {
            "path": "@build/core/runtime/generated/Release/pulp/runtime/build_info.hpp",
            "sha256": "0" * 64,
            "producer_paths": ["core/runtime/CMakeLists.txt"],
            "normalization": {"build_timestamp": "<build-timestamp>"},
        }
        self.assertEqual(verify_mirror_export.validate_generated_input(row, "fixture"), [])
        errors = verify_mirror_export.validate_generated_input(dict(row, normalization={"build_timestamp": "*"}), "fixture")
        self.assertError(errors, "normalization must be")
        errors = verify_mirror_export.validate_generated_input(dict(row, extra=1), "fixture")
        self.assertError(errors, "must contain path, sha256, producer_paths")
        errors = verify_mirror_export.validate_generated_input(
            dict(row, path="@build/core/canvas/pulp-bundled-fonts_data_inter.cpp"), "fixture")
        self.assertError(errors, "only accepted on the generated pulp/runtime/build_info.hpp")

    def test_skia_link_input_is_validated(self) -> None:
        link = {"dependency": "skia", "source": "https://skia.googlesource.com/skia.git",
                "pin": "a" * 40, "asset_sha256": "b" * 64}
        target = {"name": "pulp-canvas", "sources": [], "include_dirs": [],
                  "compile_definitions": ["PULP_HAS_SKIA=1"], "external_link_inputs": [link]}
        closure = {"targets": [target]}
        self.assertFalse([e for e in verify_mirror_export.validate_closure_manifest(closure, "c")
                          if "external_link_inputs" in e or "Skia link" in e])
        broken = {"targets": [dict(target, external_link_inputs=[dict(link, pin="release")])]}
        self.assertError(verify_mirror_export.validate_closure_manifest(broken, "c"), "40-hex pin")
        missing = {"targets": [dict(target, external_link_inputs=[])]}
        self.assertError(verify_mirror_export.validate_closure_manifest(missing, "c"), "records no Skia link input")
        absent = {"targets": [{key: value for key, value in target.items() if key != "external_link_inputs"}]}
        self.assertError(verify_mirror_export.validate_closure_manifest(absent, "c"), "records no Skia link input")
        cpu = {"targets": [dict(target, compile_definitions=[])]}
        self.assertError(verify_mirror_export.validate_closure_manifest(cpu, "c"), "does not compile against Skia")
        twice = {"targets": [dict(target, external_link_inputs=[link, link])]}
        self.assertError(verify_mirror_export.validate_closure_manifest(twice, "c"), "more than one Skia link input")
        dawn = {"targets": [dict(target, external_link_inputs=[link, dict(link, dependency="dawn")])]}
        self.assertError(verify_mirror_export.validate_closure_manifest(dawn, "c"), "must name the Skia repository")

    def test_skia_link_input_is_checked_against_pulps_manifest(self) -> None:
        asset = "b" * 64
        link = {"dependency": "skia", "source": "https://skia.googlesource.com/skia.git",
                "pin": "a" * 40, "asset_sha256": asset}
        target = {"name": "pulp-canvas", "sources": [], "include_dirs": [],
                  "compile_definitions": ["PULP_HAS_SKIA=1"], "external_link_inputs": [link]}
        manifest = {"dependencies": [{"name": "Skia", "repository": link["source"], "determinism": {
            "skia_commit": link["pin"], "release_assets": {"darwin-arm64": {"sha256": asset}}}}]}
        with tempfile.TemporaryDirectory() as directory:
            pulp = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=pulp, check=True)
            (pulp / "tools/deps").mkdir(parents=True)
            (pulp / "tools/deps/manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=pulp, check=True)
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "t"], cwd=pulp, check=True)
            sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=pulp, text=True).strip()

            def link_errors(candidate: dict) -> list[str]:
                closure = {"targets": [dict(target, external_link_inputs=[candidate])]}
                return [e for e in verify_mirror_export.validate_closure_manifest(closure, "c", pulp_root=pulp, pulp_sha=sha)
                        if "Skia" in e]

            self.assertEqual(link_errors(link), [])
            self.assertTrue(link_errors(dict(link, pin="c" * 40)))
            self.assertTrue(link_errors(dict(link, asset_sha256="d" * 64)))
            self.assertTrue(link_errors(dict(link, source="https://github.com/google/skia")))
            closure = {"targets": [target]}
            self.assertError(verify_mirror_export.validate_closure_manifest(
                closure, "c", pulp_root=pulp, pulp_sha="e" * 40), "records no Skia pin")

    def test_unmirrored_edges_require_exact_current_platform_exception(self) -> None:
        policy = json.loads((REPO / "provenance/mirror/link-policy.json").read_text())
        closure = {"configure": {"identity": {"system": "Darwin"}},
                   "targets": [{"name": "pulp-canvas", "link_targets": ["pulp-events"]}],
                   "files": [], "compile_definitions": []}
        self.assertError(
            verify_mirror_export.link_closure_findings({"canvas": closure}, policy),
            "lacks a current approved exception",
        )
        policy["approved_unmirrored_edges"] = [{
            "source": "pulp-canvas", "target": "pulp-events", "platforms": ["Darwin"],
            "owner": "d3", "approver": "team-lead", "expiry": "2099-01-01",
        }]
        self.assertEqual(verify_mirror_export.link_closure_findings({"canvas": closure}, policy), [])

    def test_unmirrored_edge_wrong_source_expiry_and_platform_fail(self) -> None:
        policy = json.loads((REPO / "provenance/mirror/link-policy.json").read_text())
        closure = {"configure": {"identity": {"system": "Darwin"}},
                   "targets": [{"name": "pulp-canvas", "link_targets": ["pulp-events"]}],
                   "files": [], "compile_definitions": []}
        for source, platforms in (("pulp-state", ["Darwin"]),
                                  ("pulp-canvas", ["Linux"])):
            policy["approved_unmirrored_edges"] = [{"source": source, "target": "pulp-events", "platforms": platforms,
                                                     "owner": "d3", "approver": "team-lead 2026-10-13", "id": "wrong",
                                                     "exit_criterion": "exit"}]
            self.assertError(verify_mirror_export.link_closure_findings({"canvas": closure}, policy),
                             "lacks a current approved exception")

    def test_unmirrored_transitive_target_cannot_be_carried_by_an_edge(self) -> None:
        policy = json.loads((REPO / "provenance/mirror/link-policy.json").read_text())
        closure = {"configure": {"identity": {"system": "Darwin"}},
                   "targets": [{"name": "pulp-view-native", "link_targets": ["pulp-view-audio"]}],
                   "transitive_link_targets": ["pulp-newthing"], "files": [], "compile_definitions": []}
        errors = verify_mirror_export.link_closure_findings({"view": closure}, policy)
        self.assertError(errors, "transitive unmirrored target pulp-newthing")

    def test_unmirrored_exception_staleness_is_fail_closed(self) -> None:
        policy = json.loads((REPO / "provenance/mirror/link-policy.json").read_text())
        closure = {"configure": {"identity": {"system": "Darwin"}},
                   "targets": [{"name": "pulp-view-native", "link_targets": ["pulp-timebase"]}],
                   "files": [], "compile_definitions": []}
        errors = verify_mirror_export.link_closure_findings({"view": closure}, policy, enforce_stale=True)
        self.assertError(errors, "stale approved unmirrored exception P4-edge-view-native-sample-bank-manifest")

    def test_pulp_target_keyed_allowlist_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "provenance/mirror").mkdir(parents=True)
            policy = json.loads((REPO / "provenance/mirror/link-policy.json").read_text())
            policy["allowed_external_targets"] = ["pulp-events"]
            write(root, "provenance/mirror/link-policy.json", dump(policy))
            loaded, errors = verify_mirror_export.load_policy(root)
            self.assertIsNone(loaded)
            self.assertError(errors, "cannot contain Pulp targets")

    def test_configure_required_and_identity_shape_checks(self) -> None:
        fixture = Fixture(self.base)
        closure = json.loads((fixture.vellum / fixture.closure_path).read_text())
        for mutation, fragment in (
            (lambda c: c.pop("configure"), ".configure must be recorded"),
            (lambda c: c["configure"]["identity"].pop("processor"), "identity must contain"),
            (lambda c: c["configure"]["identity"].update(gpu="yes"), "identity.gpu must be boolean"),
            (lambda c: c["configure"]["identity"].update(gpu=False), "identity.gpu disagrees"),
            (lambda c: c["configure"]["identity"].update(build_type="Debug"), "identity.build_type disagrees with configuration"),
            (lambda c: c["configure"].update(label="wrong"), "label must be cpu-only"),
        ):
            mutated = json.loads(json.dumps(closure))
            mutation(mutated)
            self.assertError(verify_mirror_export.validate_closure_manifest(mutated, "fixture closure", is_canvas=True), fragment)

    def test_canvas_requires_all_gpu_skia_text_shaping_evidence(self) -> None:
        fixture = Fixture(self.base)
        closure = json.loads((fixture.vellum / fixture.closure_path).read_text())
        closure["configure"]["identity"]["gpu"] = False
        closure["configure"]["options"]["PULP_ENABLE_GPU"] = "OFF"
        closure["configure"]["options_sha256"] = hashlib.sha256(json.dumps(closure["configure"]["options"], sort_keys=True).encode()).hexdigest()
        closure["targets"][0]["compile_definitions"].remove("PULP_HAS_TEXT_SHAPING=1")
        errors = verify_mirror_export.validate_closure_manifest(closure, "fixture closure", is_canvas=True)
        self.assertError(errors, "requires GPU, Skia, and text shaping")
        self.assertError(errors, "PULP_HAS_TEXT_SHAPING=1")

    def test_receipt_unit_configure_must_match_closure(self) -> None:
        fixture = Fixture(self.base)
        unit = dict(fixture.receipt["units"][0], configure=dict(fixture.receipt["units"][0]["configure"], label="cpu-only"))
        fixture.reland(units=[unit])
        self.assertError(fixture.errors(), "configure differs from its closure configure")

    def test_receipt_rejects_removed_unmirrored_list(self) -> None:
        fixture = Fixture(self.base)
        fixture.reland(unmirrored_link_targets=["pulp-events"])
        self.assertError(fixture.errors(), "receipt keys must be exactly")

    def test_producer_dependency_shape_is_required(self) -> None:
        row = {
            "path": "@build/_deps/sdl3-build/include/revision.h", "sha256": "0" * 64,
            "producer_paths": [], "producer_dependency": {"name": "sdl3", "pin": "a" * 40},
        }
        self.assertError(verify_mirror_export.validate_generated_input(row, "fixture"), "producer_dependency must contain")

    def test_round3_configure_and_receipt_guards_have_negative_controls(self) -> None:
        fixture = Fixture(self.base)
        closure = json.loads((fixture.vellum / fixture.closure_path).read_text())
        cases = (
            (lambda c: c["configure"].update(extra=True), "unexpected keys"),
            (lambda c: c["configure"].update(options=[]), "options must be an object"),
            (lambda c: c["configure"].update(options_sha256="bad"), "options_sha256 must be a SHA-256"),
            (lambda c: c["configure"]["options"].pop("PULP_ENABLE_GPU"), "identity.gpu disagrees"),
            (lambda c: c["configure"]["options"].pop("CMAKE_BUILD_TYPE"), "must include non-empty CMAKE_BUILD_TYPE"),
            (lambda c: c["configure"]["identity"].update(build_type="Debug"), "build_type disagrees"),
            (lambda c: c["configure"]["identity"].update(system="Linux"), "system disagrees"),
            (lambda c: c["configure"]["identity"].update(processor="x86_64"), "processor disagrees"),
        )
        for mutation, fragment in cases:
            mutated = json.loads(json.dumps(closure))
            mutation(mutated)
            self.assertError(verify_mirror_export.validate_closure_manifest(mutated, "fixture", is_canvas=True), fragment)
        malformed_unit = dict(fixture.receipt["units"][0], configure={"identity": {}})
        fixture.reland(units=[malformed_unit])
        self.assertError(fixture.errors(), "configure must contain identity, options_sha256, and label")

    def test_round3_policy_schema_and_closure_guards_have_negative_controls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "provenance/mirror").mkdir(parents=True)
            policy = json.loads((REPO / "provenance/mirror/link-policy.json").read_text())
            for mutation, fragment in (
                (lambda p: p["approved_unmirrored_edges"][0].pop("id"), "must contain id"),
                (lambda p: p["approved_unmirrored_edges"][0].update(owner=""), "string fields must be non-empty"),
                (lambda p: p["approved_unmirrored_edges"][0].update(platforms="Darwin"), "platforms must be a non-empty list"),
                (lambda p: p["approved_unmirrored_edges"][0].update(approver="someone"), "approver must be team-lead"),
            ):
                mutated = json.loads(json.dumps(policy))
                mutation(mutated)
                write(root, "provenance/mirror/link-policy.json", dump(mutated))
                loaded, errors = verify_mirror_export.load_policy(root)
                self.assertIsNone(loaded)
                self.assertError(errors, fragment)
        closure = {"configure": {"identity": {"system": "Darwin"}}, "targets": [], "unmirrored": []}
        self.assertError(verify_mirror_export.validate_closure_manifest(closure, "fixture"), "unmirrored is not an accepted closure field")

    def test_round3_replay_and_producer_guards_have_negative_controls(self) -> None:
        base = {"path": "@external/choc/" + "a" * 40 + "/choc.h", "source": "https://github.com/danielraffel/choc.git", "pin": "a" * 40,
                "sha256": "d" * 64, "patched": True, "patch": "b" * 64,
                "patch_set": {"pulp_path": "tools/cmake/PulpPatchChoc.cmake", "pulp_blob": "e" * 40, "key": "c" * 64},
                "base_modification": "", "copy_tree": "f" * 64,
                "patch_recipe": {"id": "pulp.vellum.patch-id.v1", "formula": "wrong",
                                 "replay": {"method": verify_mirror_export.REPLAY_METHOD, "patch_set_blob": "e" * 40, "functions": ["pulp_patch_choc_v8"]}}}
        for mutation, fragment in (
            (lambda r: r.pop("replay"), "must contain id, formula, and replay"),
            (lambda r: r["replay"].update(method="wrong"), "method is not"),
            (lambda r: r["replay"].update(functions=["bad"]), "functions must be"),
            (lambda r: r["replay"].update(patch_set_blob="0" * 40), "patch_set_blob must equal"),
            (lambda r: r.pop("formula"), "must contain id, formula, and replay"),
        ):
            row = json.loads(json.dumps(base))
            mutation(row["patch_recipe"])
            self.assertError(verify_mirror_export.validate_external_inputs([row], "fixture"), fragment)
        dependency = {"path": "@build/_deps/x-build/x.h", "sha256": "0" * 64, "producer_paths": [],
                      "producer_dependency": {"name": "", "source": "https://github.com/x/y", "pin": "a" * 40}}
        self.assertError(verify_mirror_export.validate_generated_input(dependency, "fixture"), "name must be non-empty")

    def test_pulp_canvas_must_stay_in_canvas_unit(self) -> None:
        fixture = Fixture(self.base)
        closure = json.loads((fixture.vellum / fixture.closure_path).read_text())
        write(fixture.vellum, fixture.closure_path, dump(closure))
        fixture.receipt["units"][0]["name"] = "foundation"
        fixture.receipt["units"][0]["closure_sha256"] = fixture.sha(fixture.closure_path)
        fixture.reland()
        self.assertError(fixture.errors(), "pulp-canvas may only appear in the canvas D3 unit")

    def test_guard_build_type_matches_cmake_option(self) -> None:
        fixture = Fixture(self.base)
        c = json.loads((fixture.vellum / fixture.closure_path).read_text())
        c["configure"]["options"]["CMAKE_BUILD_TYPE"] = "Debug"
        self.assertError(verify_mirror_export.validate_closure_manifest(c, "fixture"), "build_type disagrees with CMAKE_BUILD_TYPE")

    def test_guard_canvas_text_shaping(self) -> None:
        fixture = Fixture(self.base)
        c = json.loads((fixture.vellum / fixture.closure_path).read_text())
        c["configure"]["identity"]["text_shaping"] = False
        self.assertError(verify_mirror_export.validate_closure_manifest(c, "fixture", is_canvas=True), "requires GPU, Skia, and text shaping")

    def test_guard_full_verify_wires_canvas_and_stale(self) -> None:
        fixture = Fixture(self.base)
        c = json.loads((fixture.vellum / fixture.closure_path).read_text())
        c["configure"]["label"] = "cpu-only"
        write(fixture.vellum, fixture.closure_path, dump(c))
        fixture.receipt["units"][0]["closure_sha256"] = fixture.sha(fixture.closure_path)
        fixture.reland()
        self.assertError(fixture.errors(), "canvas closure labelled cpu-only")

    def test_guard_full_verify_enforces_stale_exceptions(self) -> None:
        fixture = Fixture(self.base)
        self.assertEqual(fixture.errors(), [])
        # pulp-canvas is exported by the landed receipt but links nothing, so an
        # exception whose source is pulp-canvas can never be used: it is stale.
        policy_path = fixture.vellum / "provenance/mirror/link-policy.json"
        policy = json.loads(policy_path.read_text())
        policy["approved_unmirrored_edges"].append({
            "id": "stale-canvas-events", "source": "pulp-canvas", "target": "pulp-events",
            "platforms": ["Darwin"], "owner": "d3", "approver": "team-lead 2026-10-13",
            "exit_criterion": "Removed when canvas stops reaching events",
        })
        write(fixture.vellum, "provenance/mirror/link-policy.json", dump(policy))
        fixture.reland()
        self.assertError(fixture.errors(), "stale approved unmirrored exception stale-canvas-events")

    def test_guard_malformed_receipt_unit_identity(self) -> None:
        fixture = Fixture(self.base)
        # A well-shaped configure whose identity lacks keys reaches the
        # identity branch rather than the outer configure-shape check.
        configure = {"identity": {"gpu": True}, "options_sha256": fixture.receipt_configure_sha(), "label": ""}
        unit = dict(fixture.receipt["units"][0], configure=configure)
        fixture.reland(units=[unit])
        errors = fixture.errors()
        self.assertError(errors, "units[0] configure identity is malformed")
        self.assertFalse(any("configure must contain identity" in e for e in errors), errors)

    def test_guard_transitive_forbidden_branch(self) -> None:
        policy = json.loads((REPO / "provenance/mirror/link-policy.json").read_text())
        # A non-Pulp name that matches the forbidden pattern, reachable only
        # transitively, so the forbidden branch rather than the unmirrored one runs.
        c = {"configure":{"identity":{"system":"Darwin"}},"targets":[],"transitive_link_targets":["juce-audio"],"files":[],"compile_definitions":[]}
        self.assertError(verify_mirror_export.link_closure_findings({"x": c}, policy), "x: forbidden link target juce-audio")

    def test_guard_transitive_non_pulp_branch(self) -> None:
        policy = json.loads((REPO / "provenance/mirror/link-policy.json").read_text())
        c = {"configure":{"identity":{"system":"Darwin"}},"targets":[],"transitive_link_targets":["mystery"],"files":[],"compile_definitions":[]}
        self.assertError(verify_mirror_export.link_closure_findings({"x": c}, policy), "link target mystery is neither exported nor allowed")

    def test_guard_host_path_boundary_characters(self) -> None:
        for value in ('x;"/Users/a', "x'/private/var/a"):
            self.assertError(verify_mirror_export.host_path_violations(value, "fixture"), "contains an absolute host path")

    def test_guard_include_flag_boundary_characters(self) -> None:
        for value in ("x;-I/Users/a", "x\"-isystem/Volumes/a", "x'-I/home/a"):
            self.assertError(verify_mirror_export.host_path_violations(value, "fixture"), "contains an absolute host path")

    def test_guard_skia_exemption_strips_only_segment(self) -> None:
        segment = "@external/skia/" + "a" * 40 + "/build/include/include/private/x"
        self.assertEqual(verify_mirror_export.host_path_violations(segment, "fixture"), [])
        value = segment + ";/private/var/folders/x"
        self.assertError(verify_mirror_export.host_path_violations(value, "fixture"), "contains an absolute host path")

    def test_guard_replay_shape(self) -> None:
        row = self._patched_row()
        row["patch_recipe"]["replay"] = []
        self.assertError(verify_mirror_export.validate_external_inputs([row], "fixture"), "replay must contain")

    def test_guard_replay_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pulp = Path(temporary)
            patch_bytes = b"function(pulp_patch_choc_v8 x)\nendfunction()\nfunction(pulp_patch_choc_v9 x)\nendfunction()\n"
            write(pulp, "tools/cmake/PulpPatchChoc.cmake", patch_bytes)
            run(pulp, "init", "-q", "-b", "main")
            run(pulp, "add", "-A")
            run(pulp, "commit", "-q", "-m", "pulp")
            pulp_sha = run(pulp, "rev-parse", "HEAD")
            pulp_blob = run(pulp, "rev-parse", f"{pulp_sha}:tools/cmake/PulpPatchChoc.cmake")
            key = hashlib.sha256(patch_bytes).hexdigest()
            row = self._patched_row()
            copy_tree = row["copy_tree"]
            patch = hashlib.sha256(f"patch-set:{key}\nbase-modification:\ncopy-tree:{copy_tree}\n".encode()).hexdigest()
            row.update(patch=patch, path=f"@external/choc/{'a' * 40}+patch-{patch[:16]}/choc.h")
            row["patch_set"].update(pulp_blob=pulp_blob, key=key)
            row["patch_recipe"]["replay"].update(patch_set_blob=pulp_blob, functions=["pulp_patch_choc_v8", "pulp_patch_choc_v9"])
            self.assertEqual(verify_mirror_export.patch_provenance_violations(row, "fixture", pulp_root=pulp, pulp_sha=pulp_sha), [])
            row["patch_recipe"]["replay"]["functions"] = ["pulp_patch_choc_v9", "pulp_patch_choc_v8"]
            errors = verify_mirror_export.patch_provenance_violations(row, "fixture", pulp_root=pulp, pulp_sha=pulp_sha)
            self.assertEqual(errors, ["fixture.patch_recipe.replay order does not match the patch-set blob"])

    def test_guard_recipe_formula(self) -> None:
        row = self._patched_row()
        row["patch_recipe"]["formula"] = "wrong"
        self.assertError(verify_mirror_export.validate_external_inputs([row], "fixture"), "recipe.formula")

    def _patched_row(self) -> dict[str, Any]:
        key = "c" * 64
        copy_tree = "d" * 64
        formula = f"sha256(utf8('patch-set:' + patch_set.key + '\\nbase-modification:' + base_modification + '\\ncopy-tree:' + copy_tree + '\\n'))"
        return {"path": "@external/choc/" + "a" * 40 + "+patch-" + "b" * 16 + "/choc.h", "source": "https://github.com/danielraffel/choc.git", "pin": "a" * 40, "sha256": "e" * 64, "patched": True, "patch": "f" * 64, "patch_set": {"pulp_path": "tools/cmake/PulpPatchChoc.cmake", "pulp_blob": "a" * 40, "key": key}, "base_modification": "", "copy_tree": copy_tree, "patch_recipe": {"id": "pulp.vellum.patch-id.v1", "formula": formula, "replay": {"method": verify_mirror_export.REPLAY_METHOD, "patch_set_blob": "a" * 40, "functions": ["pulp_patch_choc_v8"]}}}


if __name__ == "__main__":
    unittest.main()
