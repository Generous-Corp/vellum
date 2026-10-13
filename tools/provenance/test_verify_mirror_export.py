#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path


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

    def __init__(self, base: Path, *, mirror_overrides: dict[str, bytes] | None = None) -> None:
        self.pulp = base / "pulp"
        self.vellum = base / "vellum"
        self.pulp.mkdir()
        run(self.pulp, "init", "-q", "-b", "main")
        for path, content in PULP_FILES.items():
            write(self.pulp, path, content)
        write(self.pulp, "tools/scripts/vellum_closure.py", TOOL)
        write(self.pulp, "core/audio/engine.cpp", b"int audio;\n")
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
        run(self.vellum, "add", "-A")
        run(self.vellum, "commit", "-q", "-m", "base")

        # Projected mirror commit: what filter-repo produces for pulp_sha.
        run(self.vellum, "checkout", "-q", "--orphan", "mirror")
        run(self.vellum, "rm", "-rq", "--cached", ".")
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
                "compile_definitions": ["PULP_CANVAS_SKIA=1"], "link_targets": [],
            }],
            "missing_targets": [],
            "files": sorted(PULP_FILES),
            "external_inputs": [],
            "public_include_dirs": [],
            "compile_definitions": ["PULP_CANVAS_SKIA=1"],
            "transitive_link_targets": [],
            "resources": [],
            "generated_inputs": [],
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

    def test_repository_has_no_receipts_and_passes(self) -> None:
        report = verify_mirror_export.verify(REPO)
        self.assertEqual(report["status"], "pass", report["errors"])
        self.assertEqual(report["receipts"], [])

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
            "forbidden link target pulp::audio",
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
        self.assertError(errors, "must contain exactly path, sha256, and producer_paths")

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
            "patch_recipe": {"id": "pulp.vellum.patch-id.v1", "formula": formula},
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
            "patch_recipe": {"id": "pulp.vellum.patch-id.v1", "formula": formula},
        }
        cases = [
            ({**base, "path": f"@external/choc/{pin}/choc.h"}, "path marker"),
            ({**base, "patch": "not-a-digest"}, "patch must be a SHA-256"),
            ({k: v for k, v in base.items() if k != "pin"}, "pin must be a 40-hex"),
            ({k: v for k, v in base.items() if k not in ("patched",)}, "patch fields require patched=true"),
            ({k: v for k, v in base.items() if k != "sha256"}, "sha256 must be a SHA-256"),
            ({k: v for k, v in base.items() if k != "patch_set"}, "patch_set must contain"),
            ({**base, "patch_recipe": {"id": "wrong", "formula": formula}}, "patch_recipe.id"),
            ({**base, "base_modification": "a" * 64}, "base_modification must be empty"),
            ({**base, "path": "@fetchcontent/choc/choc.h"}, "must be @external"),
        ]
        for row, fragment in cases:
            self.assertError(verify_mirror_export.validate_external_inputs([row], "fixture"), fragment)

    def test_external_input_patch_set_is_checked_against_pulp_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pulp = Path(temporary)
            (pulp / "tools/cmake").mkdir(parents=True)
            patch_bytes = b"set(PATCH 1)\n"
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
                "patch_recipe": {"id": "pulp.vellum.patch-id.v1", "formula": formula},
            }
            self.assertEqual(verify_mirror_export.validate_external_inputs([row], "fixture", pulp_root=pulp, pulp_sha=pulp_sha), [])
            for mutation, fragment in (
                ({**row, "patch_set": {**row["patch_set"], "pulp_blob": "f" * 40}}, "pulp_blob"),
                ({**row, "patch_set": {**row["patch_set"], "key": "e" * 64}}, "key"),
                ({**row, "patch_set": {**row["patch_set"], "pulp_path": "missing.cmake"}}, "pulp_blob"),
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
        with mock.patch.object(verify_mirror_export, "host_path_violations", return_value=[]):
            self.assertEqual(fixture.errors(), [])


if __name__ == "__main__":
    unittest.main()
